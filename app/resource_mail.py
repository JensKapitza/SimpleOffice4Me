"""Mail-as-resource provider.

Accounts are virtual top-level folders.  Their archived EML files and extracted
attachments live in SimpleOffice's private managed mail tree and are exposed as
normal resources without leaking saved credentials.
"""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Iterable

from .mail_client import MailStore, _owner_key
from .resource_local import LocalResourceProvider
from .resource_provider import ProviderCapabilities, ProviderError, ResourceEntry


class MailResourceProvider:
    provider_id = "mail"
    label = "E-Mail"
    capabilities = ProviderCapabilities(
        read=True, write=False, delete=False, move=False, copy=True, folders=True,
        search=True, metadata=True, streaming=True, smart_view=False,
        server_side_copy=False,
    )

    def __init__(self, root: str | Path, master_key: bytes, actor: str):
        self.root = Path(root).resolve()
        self.store = MailStore(self.root, master_key)
        self.actor = actor

    def _account(self, account_id: str) -> dict:
        account = next((a for a in self.store.accounts(self.actor) if a.get("id") == account_id), None)
        if account is None:
            raise ProviderError("E-Mail-Konto nicht gefunden")
        return account

    def _archive_provider(self, account_id: str) -> LocalResourceProvider:
        self._account(account_id)
        folder = self.root / "email" / _owner_key(self.actor) / account_id
        folder.mkdir(parents=True, exist_ok=True)
        return LocalResourceProvider(folder)

    def list(self, path: str = "") -> Iterable[ResourceEntry]:
        clean = str(path or "").strip("/")
        if not clean:
            return [
                ResourceEntry(
                    resource_id=str(a["id"]), name=str(a.get("label") or a.get("username") or a["id"]),
                    kind="folder", path=str(a["id"]), mime_type="inode/directory",
                    provider=self.provider_id,
                    metadata={"username": a.get("username", ""), "folder": a.get("folder", "INBOX")},
                )
                for a in self.store.accounts(self.actor)
            ]
        account_id, _, subpath = clean.partition("/")
        provider = self._archive_provider(account_id)
        entries = provider.list(subpath)
        prefix = account_id + ("/" + subpath.strip("/") if subpath else "")
        return [
            ResourceEntry(
                **{**entry.to_dict(), "resource_id": f"{account_id}/{entry.resource_id}",
                   "path": f"{account_id}/{entry.path}", "provider": self.provider_id}
            ) for entry in entries
        ]

    def stat(self, resource_id: str) -> ResourceEntry:
        account_id, sep, subpath = str(resource_id).partition("/")
        if not sep:
            account = self._account(account_id)
            return ResourceEntry(resource_id=account_id, name=str(account.get("label") or account_id), kind="folder", path=account_id, mime_type="inode/directory", provider=self.provider_id)
        entry = self._archive_provider(account_id).stat(subpath)
        return ResourceEntry(**{**entry.to_dict(), "resource_id": resource_id, "path": resource_id, "provider": self.provider_id})

    def open(self, resource_id: str) -> BinaryIO:
        account_id, sep, subpath = str(resource_id).partition("/")
        if not sep:
            raise ProviderError("E-Mail-Konto ist keine Datei")
        return self._archive_provider(account_id).open(subpath)

    def search(self, query: str, path: str = "") -> Iterable[ResourceEntry]:
        clean = str(path or "").strip("/")
        accounts = [clean.partition("/")[0]] if clean else [str(a["id"]) for a in self.store.accounts(self.actor)]
        result = []
        for account_id in accounts:
            provider = self._archive_provider(account_id)
            subpath = clean.partition("/")[2] if clean.startswith(account_id + "/") else ""
            for entry in provider.search(query, subpath):
                result.append(ResourceEntry(**{**entry.to_dict(), "resource_id": f"{account_id}/{entry.resource_id}", "path": f"{account_id}/{entry.path}", "provider": self.provider_id}))
        return result[:500]

    def upload(self, *args, **kwargs):
        raise ProviderError("E-Mail ist im Commander nur als Quelle schreibgeschützt")
    mkdir = upload
    delete = upload
    move = upload
