"""Loss-aware ManageSieve synchronization helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .document_store import atomic_json_write, utc_now
from .mail_client import MAX_SCRIPT_BYTES, MailStore, ManageSieveClient

_SCRIPT_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}")


class ManageSieveSyncClient(ManageSieveClient):
    """ManageSieve operations needed to inventory, download and activate scripts."""

    @staticmethod
    def _quote(name: str) -> str:
        if not _SCRIPT_NAME.fullmatch(name) or name in {".", ".."}:
            raise ValueError("invalid Sieve script name")
        return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def list_scripts(self) -> list[dict[str, Any]]:
        lines = self._command("LISTSCRIPTS")
        scripts: list[dict[str, Any]] = []
        for line in lines[:-1]:
            match = re.match(r'^"((?:\\.|[^"\\])*)"(?:\s+(ACTIVE))?$', line, re.IGNORECASE)
            if not match:
                continue
            name = re.sub(r'\\([\\"])', r'\1', match.group(1))
            if _SCRIPT_NAME.fullmatch(name):
                scripts.append({"name": name, "active": bool(match.group(2))})
        return scripts

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
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"version": 1, "accounts": {}}
    payload.setdefault("accounts", {})[f"{actor}:{account_id}"] = state
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, payload)


def sync_from_server(store: MailStore, actor: str, account: dict[str, Any]) -> dict[str, Any]:
    """Download every server script before editing without silently losing local data."""
    client = ManageSieveSyncClient(account["sieve_host"], account["sieve_port"])
    downloaded: list[dict[str, Any]] = []
    active = ""
    try:
        client.connect(account["username"], account["plain_password"])
        remote = client.list_scripts()
        local = {row["name"]: row for row in store.scripts_for(actor, account["id"])}
        for row in remote:
            name = row["name"]
            content = client.get_script(name)
            digest = hashlib.sha512(content.encode("utf-8")).hexdigest()
            changed = local.get(name, {}).get("sha512") != digest
            if changed:
                saved = store.save_script(actor, account["id"], name, content)
                store.history.record(
                    "sieve_script_downloaded", actor, "sieve", saved["sha512"],
                    {**saved, "source": "server", "active": row["active"]},
                )
            downloaded.append({"name": name, "active": row["active"], "sha512": digest, "changed": changed})
            if row["active"]:
                active = name
        state = {"scripts": downloaded, "active": active, "updated_at": utc_now()}
        _save_state(store, actor, account["id"], state)
        store.history.record(
            "sieve_server_inventory_downloaded", actor, "sieve", account["id"],
            {"account_id": account["id"], "scripts": len(downloaded), "active": active},
        )
        return state
    finally:
        client.close()


def activate_server_script(store: MailStore, actor: str, account: dict[str, Any], name: str) -> dict[str, Any]:
    """Back up the full server inventory, then activate an existing server script."""
    state = sync_from_server(store, actor, account)
    if name not in {row["name"] for row in state["scripts"]}:
        raise ValueError("Sieve-Skript existiert auf dem Server nicht")
    client = ManageSieveSyncClient(account["sieve_host"], account["sieve_port"])
    try:
        client.connect(account["username"], account["plain_password"])
        client.set_active(name)
    finally:
        client.close()
    for row in state["scripts"]:
        row["active"] = row["name"] == name
    state["active"] = name
    state["updated_at"] = utc_now()
    _save_state(store, actor, account["id"], state)
    store.history.record("sieve_script_activated", actor, "sieve", account["id"], {"account_id": account["id"], "name": name})
    return state
