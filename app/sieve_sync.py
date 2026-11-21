"""Loss-aware ManageSieve synchronization helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .document_store import atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .mail_client import MAX_SCRIPT_BYTES, MailStore, ManageSieveClient

_SCRIPT_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def sanitize_local_name(server_name: str, taken: set[str]) -> str:
    """Derive a deterministic filesystem-safe key for a server script name.

    Safe names stay identical (backward compatible). Anything else is
    folded to ``[A-Za-z0-9_.-]`` plus a short hash suffix so distinct
    server names can never collide on one local key.
    """
    raw = str(server_name or "")
    if _SCRIPT_NAME.fullmatch(raw) and raw not in {".", ".."}:
        return raw
    folded = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")[:64].strip("._")
    if not folded:
        folded = "script"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    candidate = f"{folded}-{digest}"
    counter = 2
    while candidate in taken:
        candidate = f"{folded}-{digest}-{counter}"
        counter += 1
    return candidate


class ManageSieveSyncClient(ManageSieveClient):
    """ManageSieve operations needed to inventory, download and activate scripts."""

    @staticmethod
    def _quote(name: str) -> str:
        """Quote a server script name for the wire (RFC 5804 quoted-string).

        Unlike local filenames, server names may contain spaces and Unicode.
        Only the characters forbidden inside a quoted-string are rejected.
        """
        text = str(name or "")
        if not text or len(text) > 255 or text in {".", ".."}:
            raise ValueError("invalid Sieve script name")
        if any(char in text for char in ('"', "\\")) or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValueError("invalid Sieve script name")
        return '"' + text + '"'

    def list_scripts_detailed(self) -> dict[str, Any]:
        """List server scripts, keeping every parseable name plus diagnostics."""
        lines = self._command("LISTSCRIPTS")
        scripts: list[dict[str, Any]] = []
        skipped: list[str] = []
        for line in lines[:-1]:
            match = re.match(r'^"((?:\\.|[^"\\])*)"(?:\s+(ACTIVE))?$', line, re.IGNORECASE)
            if not match:
                skipped.append(line[:120])
                continue
            name = re.sub(r'\\([\\"])', r'\1', match.group(1))
            if not name or len(name) > 255:
                skipped.append(line[:120])
                continue
            scripts.append({"name": name, "active": bool(match.group(2))})
        return {"scripts": scripts, "skipped": skipped}

    def list_scripts(self) -> list[dict[str, Any]]:
        return self.list_scripts_detailed()["scripts"]

    def get_script(self, name: str) -> str:
        if self.file is None:
            raise RuntimeError("ManageSieve is not connected")
        self.file.write(f"GETSCRIPT {self._quote(name)}\r\n".encode("ascii"))
        self.file.flush()
        first = self.file.readline()
        if not first:
            raise RuntimeError("ManageSieve connection closed")
        text = first.decode("utf-8", "replace").rstrip("\r\n")
        match = re.match(r"^\{(\d+)\+?\}$", text)
        if not match:
            if text.upper().startswith(("NO", "BYE")):
                raise RuntimeError(f"ManageSieve rejected GETSCRIPT: {text[:300]}")
            raise RuntimeError("ManageSieve returned an invalid script literal")
        size = int(match.group(1))
        if size > MAX_SCRIPT_BYTES:
            raise ValueError("Sieve script exceeds 1 MiB")
        payload = self.file.read(size)
        if len(payload) != size:
            raise RuntimeError("ManageSieve script literal was truncated")
        trailer = self.file.readline()
        if trailer not in {b"\r\n", b"\n"}:
            raise RuntimeError("ManageSieve returned an invalid literal terminator")
        self._response()
        if b"\0" in payload:
            raise ValueError("Sieve script contains NUL")
        return payload.decode("utf-8")

    def set_active(self, name: str) -> None:
        self._command(f"SETACTIVE {self._quote(name)}")


def _state_path(store: MailStore):
    return store.control / "sieve-server-state.json"


def server_state(store: MailStore, actor: str, account_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(_state_path(store).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"scripts": [], "active": "", "updated_at": ""}
    return payload.get("accounts", {}).get(f"{actor}:{account_id}", {"scripts": [], "active": "", "updated_at": ""})


def _save_state(store: MailStore, actor: str, account_id: str, state: dict[str, Any]) -> None:
    path = _state_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path.with_suffix(".lock")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {"version": 1, "accounts": {}}
        payload.setdefault("accounts", {})[f"{actor}:{account_id}"] = state
        atomic_json_write(path, payload)


def _preserve_local_revision(store: MailStore, actor: str, account_id: str, name: str, content: str) -> str:
    """Persist the exact local script body in the Git-backed audit trail before replacement."""
    encoded = content.encode("utf-8")
    digest = hashlib.sha512(encoded).hexdigest()
    key = hashlib.sha256(f"{actor}:{account_id}:{name}:local:{digest}".encode("utf-8")).hexdigest()
    return store.history.record(
        "sieve_script_local_preserved",
        actor,
        "sieve-revisions",
        key,
        {
            "account_id": account_id,
            "name": name,
            "sha512": digest,
            "size": len(encoded),
            "content": content,
            "source": "local",
            "preserved_at": utc_now(),
        },
    )


def sync_from_server(store: MailStore, actor: str, account: dict[str, Any]) -> dict[str, Any]:
    """Download every server script before editing without silently losing local data.

    Server names may contain spaces or Unicode; they are stored under a
    deterministic safe local key (identity for safe names) and the mapping
    is reported so the UI can show what was renamed or skipped.
    """
    client = ManageSieveSyncClient(account["sieve_host"], account["sieve_port"])
    downloaded: list[dict[str, Any]] = []
    renamed: list[dict[str, str]] = []
    active = ""
    try:
        client.connect(account["username"], account["plain_password"])
        inventory = client.list_scripts_detailed()
        remote = inventory["scripts"]
        skipped = list(inventory["skipped"])
        local = {row["name"]: row for row in store.scripts_for(actor, account["id"])}
        taken = set(local)
        for row in remote:
            server_name = row["name"]
            try:
                content = client.get_script(server_name)
            except ValueError as exc:
                skipped.append(f"{server_name}: {exc}")
                continue
            name = sanitize_local_name(server_name, taken)
            taken.add(name)
            if name != server_name:
                renamed.append({"server": server_name, "local": name})
            digest = hashlib.sha512(content.encode("utf-8")).hexdigest()
            local_row = local.get(name)
            changed = local_row is None or local_row.get("sha512") != digest
            if changed:
                if local_row is not None:
                    local_content = store.script(actor, account["id"], name)
                    _preserve_local_revision(store, actor, account["id"], name, local_content)
                saved = store.save_script(actor, account["id"], name, content)
                store.history.record(
                    "sieve_script_downloaded", actor, "sieve", saved["sha512"],
                    {**saved, "server_name": server_name, "source": "server", "active": row["active"]},
                )
            downloaded.append({"name": name, "server_name": server_name, "active": row["active"],
                               "sha512": digest, "changed": changed})
            if row["active"]:
                active = name
        state = {"scripts": downloaded, "active": active, "updated_at": utc_now(),
                 "renamed": renamed, "skipped": skipped}
        _save_state(store, actor, account["id"], state)
        store.history.record(
            "sieve_server_inventory_downloaded", actor, "sieve", account["id"],
            {"account_id": account["id"], "scripts": len(downloaded), "active": active,
             "renamed": renamed, "skipped": skipped},
        )
        return state
    finally:
        client.close()


def activate_server_script(store: MailStore, actor: str, account: dict[str, Any], name: str) -> dict[str, Any]:
    """Back up the full server inventory, then activate an existing server script."""
    state = sync_from_server(store, actor, account)
    row = next((entry for entry in state["scripts"]
                if name in {entry["name"], entry.get("server_name", "")}), None)
    if row is None:
        raise ValueError("Sieve-Skript existiert auf dem Server nicht")
    client = ManageSieveSyncClient(account["sieve_host"], account["sieve_port"])
    try:
        client.connect(account["username"], account["plain_password"])
        client.set_active(row.get("server_name", "") or row["name"])
    finally:
        client.close()
    for entry in state["scripts"]:
        entry["active"] = entry["name"] == row["name"]
    state["active"] = row["name"]
    state["updated_at"] = utc_now()
    _save_state(store, actor, account["id"], state)
    store.history.record("sieve_script_activated", actor, "sieve", account["id"],
                         {"account_id": account["id"], "name": row["name"],
                          "server_name": row.get("server_name", "") or row["name"]})
    return state
