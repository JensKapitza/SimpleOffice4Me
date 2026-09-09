"""Remote Federation provider backed by the Resource Commander HTTP API."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import BinaryIO, Iterable

from .resource_provider import ProviderCapabilities, ProviderError, ResourceEntry


class FederationResourceProvider:
    def __init__(self, peer_id: str, label: str, base_url: str, token: str):
        self.peer_id = peer_id
        self.provider_id = f"federation:{peer_id}"
        self.label = label or peer_id
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.capabilities = ProviderCapabilities(
            read=True, write=True, delete=True, move=True, copy=True, folders=True,
            search=True, metadata=True, streaming=True, smart_view=False,
            server_side_copy=True,
        )

    def _request(self, method: str, path: str, *, payload=None, body: bytes | None = None, content_type: str = "application/json"):
        url = self.base_url + path
        data = body
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return urllib.request.urlopen(request, timeout=30)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise ProviderError(f"Federation-Peer nicht erreichbar: {self.peer_id}") from exc

    def _json(self, method: str, path: str, *, payload=None):
        with self._request(method, path, payload=payload) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _entry(value: dict, provider_id: str) -> ResourceEntry:
        value = dict(value)
        value["provider"] = provider_id
        allowed = {"resource_id", "name", "kind", "path", "size", "modified", "mime_type", "provider", "metadata"}
        return ResourceEntry(**{k: v for k, v in value.items() if k in allowed})

    def list(self, path: str = "") -> Iterable[ResourceEntry]:
        query = urllib.parse.urlencode({"provider": "self", "path": path})
        data = self._json("GET", f"/resource-commander/api/list?{query}")
        remote_caps = data.get("capabilities") or {}
        if remote_caps:
            self.capabilities = ProviderCapabilities(**{k: bool(v) for k, v in remote_caps.items() if k in ProviderCapabilities.__dataclass_fields__})
        return [self._entry(item, self.provider_id) for item in data.get("entries", [])]

    def stat(self, resource_id: str) -> ResourceEntry:
        query = urllib.parse.urlencode({"provider": "self", "id": resource_id})
        return self._entry(self._json("GET", f"/resource-commander/api/stat?{query}")["entry"], self.provider_id)

    def open(self, resource_id: str) -> BinaryIO:
        query = urllib.parse.urlencode({"provider": "self", "id": resource_id})
        with self._request("GET", f"/resource-commander/api/download?{query}") as response:
            return io.BytesIO(response.read())

    def upload(self, path: str, source: BinaryIO, *, name: str, metadata=None) -> ResourceEntry:
        query = urllib.parse.urlencode({"provider": "self", "path": path, "name": name})
        body = source.read()
        with self._request("POST", f"/resource-commander/api/upload?{query}", body=body, content_type="application/octet-stream") as response:
            data = json.loads(response.read().decode("utf-8"))
        return self._entry(data["entry"], self.provider_id)

    def mkdir(self, path: str, name: str) -> ResourceEntry:
        data = self._json("POST", "/resource-commander/api/mkdir", payload={"provider": "self", "path": path, "name": name})
        return self._entry(data["entry"], self.provider_id)

    def delete(self, resource_id: str) -> None:
        self._json("POST", "/resource-commander/api/delete", payload={"provider": "self", "id": resource_id})

    def move(self, resource_id: str, target_path: str, *, name: str | None = None) -> ResourceEntry:
        data = self._json("POST", "/resource-commander/api/move", payload={"provider": "self", "id": resource_id, "path": target_path, "name": name or ""})
        return self._entry(data["entry"], self.provider_id)

    def search(self, query: str, path: str = "") -> Iterable[ResourceEntry]:
        params = urllib.parse.urlencode({"provider": "self", "q": query, "path": path})
        data = self._json("GET", f"/resource-commander/api/search?{params}")
        return [self._entry(item, self.provider_id) for item in data.get("entries", [])]
