"""Remote Federation provider backed by the Resource Commander HTTP API."""
from __future__ import annotations

import http.client
import json
import ssl
import urllib.parse
from typing import BinaryIO, Iterable

from .resource_provider import ProviderCapabilities, ProviderError, ResourceEntry

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class FederationResourceProvider:
    def __init__(
        self,
        peer_id: str,
        label: str,
        base_url: str,
        token: str,
        allowed_capabilities: ProviderCapabilities | None = None,
    ):
        self.peer_id = peer_id
        self.provider_id = f"federation:{peer_id}"
        self.label = label or peer_id
        self.base = self._validated_base(base_url)
        self.token = token
        self._allowed_capabilities = allowed_capabilities or ProviderCapabilities(
            read=False, write=False, delete=False, move=False, copy=False, folders=False,
            search=False, metadata=False, streaming=False, smart_view=False,
            server_side_copy=False,
        )
        self.capabilities = self._allowed_capabilities

    def _require(self, capability: str) -> None:
        if not bool(getattr(self.capabilities, capability, False)):
            raise ProviderError(f"Federation-Peer erlaubt '{capability}' nicht")

    def _apply_remote_capabilities(self, remote_caps: dict) -> None:
        """Intersect remote claims with this peer's locally configured ceiling."""
        values = {}
        for key in ProviderCapabilities.__dataclass_fields__:
            allowed = bool(getattr(self._allowed_capabilities, key, False))
            advertised = bool(remote_caps.get(key, False))
            values[key] = allowed and advertised
        self.capabilities = ProviderCapabilities(**values)

    @staticmethod
    def _validated_base(base_url: str) -> urllib.parse.SplitResult:
        parsed = urllib.parse.urlsplit(str(base_url or "").strip().rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ProviderError("Federation-Peer benötigt eine HTTP- oder HTTPS-URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProviderError("Federation-Peer-URL darf keine Zugangsdaten, Query oder Fragment enthalten")
        if parsed.path not in {"", "/"} and any(part in {".", ".."} for part in parsed.path.split("/")):
            raise ProviderError("Ungültiger Federation-Basispfad")
        return parsed

    def _connection(self):
        port = self.base.port
        if self.base.scheme == "https":
            return http.client.HTTPSConnection(
                self.base.hostname,
                port or 443,
                timeout=30,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(self.base.hostname, port or 80, timeout=30)

    def _target(self, path: str) -> str:
        if not path.startswith("/resource-commander/api/"):
            raise ProviderError("Nicht erlaubter Federation-Resource-Pfad")
        base_path = self.base.path.rstrip("/")
        return f"{base_path}{path}" or "/"

    def _request(self, method: str, path: str, *, payload=None, body: bytes | None = None,
                 content_type: str = "application/json"):
        data = body
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json", "Connection": "close"}
        if data is not None:
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(len(data))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        connection = self._connection()
        try:
            connection.request(method, self._target(path), body=data, headers=headers)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            connection.close()
            raise ProviderError(f"Federation-Peer nicht erreichbar: {self.peer_id}") from exc
        response._simpleoffice_connection = connection
        if response.status < 200 or response.status >= 300:
            try:
                response.read(min(MAX_RESPONSE_BYTES, 4096))
            finally:
                response.close()
                connection.close()
            raise ProviderError(f"Federation-Peer antwortete mit HTTP {response.status}")
        return response

    @staticmethod
    def _read_bounded(response, limit: int = MAX_RESPONSE_BYTES) -> bytes:
        declared = response.getheader("Content-Length")
        if declared:
            try:
                if int(declared) > limit:
                    raise ProviderError("Federation-Antwort überschreitet das Größenlimit")
            except ValueError as exc:
                raise ProviderError("Ungültige Federation-Antwortgröße") from exc
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ProviderError("Federation-Antwort überschreitet das Größenlimit")
        return data

    def _json(self, method: str, path: str, *, payload=None):
        response = self._request(method, path, payload=payload)
        connection = getattr(response, "_simpleoffice_connection", None)
        try:
            raw = self._read_bounded(response)
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ProviderError("Ungültige Federation-JSON-Antwort")
            return value
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("Ungültige Federation-JSON-Antwort") from exc
        finally:
            response.close()
            if connection is not None:
                connection.close()

    @staticmethod
    def _entry(value: dict, provider_id: str) -> ResourceEntry:
        value = dict(value)
        value["provider"] = provider_id
        allowed = {"resource_id", "name", "kind", "path", "size", "modified", "mime_type", "provider", "metadata"}
        return ResourceEntry(**{key: item for key, item in value.items() if key in allowed})

    def list(self, path: str = "") -> Iterable[ResourceEntry]:
        self._require("read")
        query = urllib.parse.urlencode({"provider": "self", "path": path})
        data = self._json("GET", f"/resource-commander/api/list?{query}")
        remote_caps = data.get("capabilities") or {}
        if remote_caps:
            self._apply_remote_capabilities(remote_caps)
            self._require("read")
        return [self._entry(item, self.provider_id) for item in data.get("entries", [])]

    def stat(self, resource_id: str) -> ResourceEntry:
        self._require("metadata")
        self._require("read")
        query = urllib.parse.urlencode({"provider": "self", "id": resource_id})
        return self._entry(self._json("GET", f"/resource-commander/api/stat?{query}")["entry"], self.provider_id)

    def open(self, resource_id: str) -> BinaryIO:
        self._require("read")
        query = urllib.parse.urlencode({"provider": "self", "id": resource_id})
        return self._request("GET", f"/resource-commander/api/download?{query}")

    def read_range(self, resource_id: str, offset: int, length: int) -> bytes:
        self._require("read")
        self._require("streaming")
        requested = max(0, min(int(length), 1024 * 1024))
        query = urllib.parse.urlencode({
            "provider": "self", "id": resource_id,
            "offset": max(0, int(offset)), "length": requested,
        })
        response = self._request("GET", f"/resource-commander/api/range?{query}")
        connection = getattr(response, "_simpleoffice_connection", None)
        try:
            data = response.read(requested + 1)
            if len(data) > requested:
                raise ProviderError("Federation-Range-Antwort ist größer als angefordert")
            return data
        finally:
            response.close()
            if connection is not None:
                connection.close()

    def upload(self, path: str, source: BinaryIO, *, name: str, metadata=None) -> ResourceEntry:
        self._require("write")
        query = urllib.parse.urlencode({"provider": "self", "path": path, "name": name})
        body = source.read()
        response = self._request(
            "POST", f"/resource-commander/api/upload?{query}", body=body,
            content_type="application/octet-stream",
        )
        connection = getattr(response, "_simpleoffice_connection", None)
        try:
            data = json.loads(self._read_bounded(response).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("Ungültige Federation-Upload-Antwort") from exc
        finally:
            response.close()
            if connection is not None:
                connection.close()
        return self._entry(data["entry"], self.provider_id)

    def mkdir(self, path: str, name: str) -> ResourceEntry:
        self._require("write")
        self._require("folders")
        data = self._json("POST", "/resource-commander/api/mkdir", payload={"provider": "self", "path": path, "name": name})
        return self._entry(data["entry"], self.provider_id)

    def delete(self, resource_id: str) -> None:
        self._require("delete")
        self._json("POST", "/resource-commander/api/delete", payload={"provider": "self", "id": resource_id})

    def move(self, resource_id: str, target_path: str, *, name: str | None = None) -> ResourceEntry:
        self._require("move")
        data = self._json("POST", "/resource-commander/api/move", payload={
            "provider": "self", "id": resource_id, "path": target_path, "name": name or "",
        })
        return self._entry(data["entry"], self.provider_id)

    def search(self, query: str, path: str = "") -> Iterable[ResourceEntry]:
        self._require("read")
        self._require("search")
        params = urllib.parse.urlencode({"provider": "self", "q": query, "path": path})
        data = self._json("GET", f"/resource-commander/api/search?{params}")
        return [self._entry(item, self.provider_id) for item in data.get("entries", [])]
