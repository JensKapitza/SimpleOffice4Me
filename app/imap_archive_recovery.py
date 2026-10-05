"""Durable, owner-scoped retries for the existing read-only IMAP archive."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path

from .attachment_security import AttachmentSecurity
from .document_store import DocumentStore, utc_now
from .file_lock import exclusive_file_lock
from .mail_client import MAX_MESSAGE_BYTES, _owner_key
from .v2.storage_runtime import create_or_verify_document


class CheckpointUnavailable(OSError):
    """Stop the run rather than continue without durable recovery evidence."""


class ArchiveRecovery:
    def __init__(self, client, actor, account):
        self.client, self.store = client, client.store
        self.actor, self.account = actor, account
        self.state = {}
        self.known = set()
        self.locations = None
        self.result = {"examined": 0, "archived": 0, "duplicates": 0,
                       "attachments": 0, "errors": [], "pending": 0}

    def save(self):
        self.state.update(sha512=sorted(self.known)[-100000:], updated_at=utc_now())
        try:
            self.store.update_archive_state(self.actor, self.account["id"], self.state)
        except OSError as exc:
            raise CheckpointUnavailable("Archivfortschritt konnte nicht sicher gespeichert werden.") from exc

    def prepare(self, connection):
        status, _ = connection.select(self.account["folder"], readonly=True)
        if status != "OK":
            raise RuntimeError("IMAP EXAMINE failed")
        raw = connection.untagged_responses.get("UIDVALIDITY", [b""])[0]
        validity = raw.decode("ascii") if isinstance(raw, bytes) else str(raw)
        if not validity.isdigit() or not 0 < int(validity) < 2**32:
            raise ValueError("IMAP server returned invalid UIDVALIDITY")
        binding = hashlib.sha256(json.dumps(
            {key: self.account.get(key) for key in
             ("host", "port", "security", "username", "folder")}, sort_keys=True,
        ).encode()).hexdigest()
        previous = self.store.archive_state(self.actor, self.account["id"])
        self.known = set(previous.get("sha512", []))
        same = (previous.get("recovery_version") == 2
                and previous.get("uidvalidity") == validity
                and previous.get("source_binding") == binding)
        superseded = dict(previous.get("superseded", {}))
        if not same and previous.get("pending"):
            namespace = f"{previous.get('source_binding', 'legacy')}:{previous.get('uidvalidity', '')}"
            superseded[namespace] = previous["pending"]
        self.state = {**previous, "recovery_version": 2, "uidvalidity": validity,
                      "source_binding": binding, "folder": self.account["folder"],
                      "last_uid": int(previous.get("last_uid", 0)) if same else 0,
                      "pending": dict(previous.get("pending", {})) if same else {},
                      "superseded": superseded}
        # Old checkpoints can contain holes. Reconcile once, retaining all EMLs.
        self.save()

    def archive_message(self, connection, uid, extract):
        pending = self.state["pending"]
        row = pending.setdefault(uid, {"stage": "fetch", "parts": {}})
        row["stage"] = "fetch"
        row.pop("error_type", None)
        row["extract_attachments"] = bool(extract or row.get("extract_attachments"))
        self.state["last_uid"] = max(self.state["last_uid"], int(uid))
        self.save()  # A crash at any subsequent boundary retains this UID.
        status, fetched = connection.uid("fetch", uid.encode("ascii"), "(UID RFC822.SIZE BODY.PEEK[])")
        if status != "OK":
            raise RuntimeError("IMAP UID FETCH failed")
        raw = self.client._literal(fetched)
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("message exceeds 100 MiB archive limit")
        self.result["examined"] += 1
        digest = hashlib.sha512(raw).hexdigest()
        if row.get("sha512") and row["sha512"] != digest:
            raise ValueError("message changed within the same IMAP UID namespace")
        if "path" not in row:
            # Reuse archives from earlier years, including migration reconciliation.
            from .mail_archive_storage import archive_locations
            if self.locations is None:
                self.locations = {}
                for path in archive_locations(self.store, self.actor, self.account["id"]):
                    relative = Path(path)
                    if len(relative.parts) == 5:
                        self.locations.setdefault(relative.stem, []).append(path)
            matches = self.locations.get(digest, [])
            if len(matches) > 1:
                raise ValueError("archive message identity is ambiguous")
            year = datetime.now(timezone.utc).strftime("%Y")
            row["path"] = matches[0] if matches else (
                f"email/{_owner_key(self.actor)}/{self.account['id']}/{year}/{digest}.eml"
            )
        row.update(stage="storage", sha512=digest)
        self.save()
        document, created = create_or_verify_document(
            self.store.root, self.actor, row["path"], raw, max_bytes=MAX_MESSAGE_BYTES,
        )
        if self.locations is not None and digest not in self.locations:
            self.locations[digest] = [row["path"]]
        self.result["archived" if created else "duplicates"] += 1
        row.update(stage="metadata", document_id=document["document_id"])
        self.save()
        documents = DocumentStore(self.store.root)
        documents.set_tags(document["document_id"], list(dict.fromkeys([
            *document.get("tags", []), "email", "source:imap", f"imap-account:{self.account['id']}",
        ])), self.actor)
        message = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        documents.set_attribute(document["document_id"], "email_origin", {
            "account_id": self.account["id"], "folder": self.account["folder"],
            "uidvalidity": self.state["uidvalidity"], "uid": uid, "sha512": digest,
            **{key: str(message.get(header, ""))[:500] for key, header in
               (("message_id", "Message-ID"), ("subject", "Subject"), ("from", "From"))},
        }, self.actor)
        if row["extract_attachments"]:
            row["stage"] = "attachments"
            self.save()
            security = AttachmentSecurity(self.store.root)
            manifest = security.preview_eml(document["document_id"], self.actor)
            for part in manifest["attachments"]:
                key = str(part["part"])
                if key in row["parts"]:
                    continue
                released = security.extract(manifest["manifest_id"], [part["part"]], self.actor, idempotent=True)[0]
                row["parts"][key] = {field: released[field] for field in
                                     ("document_id", "quarantine_id", "verdict") if field in released}
                self.result["attachments"] += int(bool(released.get("document_id")))
                self.save()
        self.known.add(digest)
        del pending[uid]
        self.save()

    def run(self, limit, extract):
        self.store._owned_row(self.actor, self.account["id"])
        lock_id = hashlib.sha256(f"{self.actor}:{self.account['id']}".encode()).hexdigest()
        with exclusive_file_lock(self.store.control / f"archive-{lock_id}.lock"):
            connection = self.client._connect(self.account)
            try:
                self.store.ensure_private_archive(self.actor, self.account["id"])
                self.prepare(connection)
                last = self.state["last_uid"]
                start = min(last + 1, 2**32 - 1)
                status, data = connection.uid("search", None, f"UID {start}:*" if last else "ALL")
                if status != "OK":
                    raise RuntimeError("IMAP UID SEARCH failed")
                candidates = set()
                for raw in data[0].split() if data and data[0] else []:
                    uid = raw.decode("ascii")
                    if not uid.isdigit() or not 0 < int(uid) < 2**32:
                        raise ValueError("IMAP server returned invalid UID")
                    if int(uid) > last:  # N:* may return the highest UID below N.
                        candidates.add(str(int(uid)))
                pending = sorted(self.state["pending"], key=int)
                batch = (pending + sorted(candidates - set(pending), key=int))[:limit]
                for uid in batch:
                    try:
                        self.archive_message(connection, uid, extract)
                    except CheckpointUnavailable:
                        raise
                    except Exception as exc:
                        row = self.state["pending"][uid]
                        row["error_type"] = type(exc).__name__
                        self.save()
                        self.result["errors"].append({"uid": uid, "stage": row["stage"],
                                                      "error": "Archivierung unvollständig; wird erneut versucht.",
                                                      "error_type": type(exc).__name__})
                        logging.getLogger(__name__).warning(
                            "IMAP archive incomplete: account=%s uid=%s stage=%s error_type=%s",
                            self.account["id"], uid, row["stage"], type(exc).__name__,
                        )
                self.result["pending"] = len(self.state["pending"])
                self.result["superseded_pending"] = sum(len(rows) for rows in self.state["superseded"].values())
                self.store.history.record("imap_archive_completed", self.actor, "mail-archive", self.account["id"],
                                          {key: value for key, value in self.result.items() if key != "errors"}
                                          | {"errors": len(self.result["errors"]), "last_uid": self.state["last_uid"]})
                return self.result
            finally:
                try:
                    connection.logout()
                except Exception:
                    pass
