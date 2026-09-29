"""Versioned SimpleOffice extension contract without dynamic code loading."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .document_store import atomic_json_write
from .file_lock import exclusive_file_lock
from .v3_capabilities import enabled as capability_enabled


API_VERSION = 1
MANIFEST_MAX_BYTES = 64 * 1024
REQUEST_MAX_BYTES = 256 * 1024
RESPONSE_MAX_BYTES = 1024 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.:-]{1,79}$")

POINT_CAPABILITY = {
    "search_provider": "search.read",
    "command": "command.invoke",
    "inbox_source": "inbox.write",
    "export_provider": "export.read",
    "activity_consumer": "activity.consume",
    "automation_action": "automation.execute",
    "health_check": "health.read",
}
KNOWN_CAPABILITIES = frozenset(POINT_CAPABILITY.values()) | frozenset({"ui.register"})
SENSITIVE_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "credential_value",
)


class ExtensionError(RuntimeError):
    pass


class ExtensionUnavailable(ExtensionError):
    pass


class ExtensionDenied(ExtensionError):
    pass


class ExtensionCallError(ExtensionError):
    pass


@dataclass(frozen=True)
class ExtensionManifest:
    extension_id: str
    version: str
    api_version: int
    name: str
    extension_points: tuple[str, ...]
    capabilities: tuple[str, ...]
    transport_type: str
    endpoint: str = ""
    auth_ref: str = ""
    timeout_seconds: float = 2.0
    ui: tuple[dict[str, str], ...] = ()
    commands: tuple[dict[str, str], ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, external: bool = True) -> "ExtensionManifest":
        if not isinstance(raw, Mapping):
            raise ValueError("extension manifest must be an object")
        _reject_sensitive_values(raw)
        extension_id = str(raw.get("id", "")).strip()
        version = str(raw.get("version", "")).strip()
        try:
            api_version = int(raw.get("api_version", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("extension api_version must be an integer") from exc
        if not _ID.fullmatch(extension_id):
            raise ValueError("extension id is invalid")
        if not _VERSION.fullmatch(version):
            raise ValueError("extension version is invalid")
        if api_version != API_VERSION:
            raise ValueError(
                f"unsupported extension API version {api_version}; server supports {API_VERSION}"
            )
        points = tuple(dict.fromkeys(str(value).strip() for value in raw.get("extension_points", [])))
        if not points or any(point not in POINT_CAPABILITY for point in points):
            raise ValueError("extension point is unknown or missing")
        capabilities = tuple(dict.fromkeys(str(value).strip() for value in raw.get("capabilities", [])))
        if any(not _CAPABILITY.fullmatch(value) for value in capabilities):
            raise ValueError("extension capability is invalid")
        required = {POINT_CAPABILITY[point] for point in points}
        if not required.issubset(set(capabilities)):
            raise ValueError("manifest does not request the capabilities required by its extension points")
        transport = raw.get("transport", {})
        if not isinstance(transport, Mapping):
            raise ValueError("extension transport must be an object")
        transport_type = str(transport.get("type", "")).strip().casefold()
        if external and transport_type != "http":
            raise ValueError("external manifests support only the isolated HTTP transport")
        if transport_type not in {"http", "builtin"}:
            raise ValueError("extension transport type is unsupported")
        endpoint = str(transport.get("endpoint", "")).strip()
        if transport_type == "http":
            _validate_endpoint_shape(endpoint)
        auth_ref = str(transport.get("auth_ref", "")).strip()
        if len(auth_ref) > 200:
            raise ValueError("extension auth_ref is too long")
        try:
            timeout_seconds = float(transport.get("timeout_seconds", 2.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("extension timeout must be numeric") from exc
        timeout_seconds = max(0.2, min(10.0, timeout_seconds))
        ui = _ui_items(raw.get("ui", []))
        commands = _command_items(raw.get("commands", []))
        if (ui or commands) and "ui.register" not in capabilities:
            raise ValueError("UI or command descriptors require ui.register capability")
        return cls(
            extension_id=extension_id,
            version=version,
            api_version=api_version,
            name=str(raw.get("name", extension_id)).strip()[:120] or extension_id,
            extension_points=points,
            capabilities=capabilities,
            transport_type=transport_type,
            endpoint=endpoint,
            auth_ref=auth_ref,
            timeout_seconds=timeout_seconds,
            ui=ui,
            commands=commands,
        )


def _reject_sensitive_values(value: Any, *, path: str = "manifest", depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("extension manifest is too deeply nested")
    if isinstance(value, Mapping):
        for key, item in value.items():
            folded = str(key).casefold().replace("-", "_")
            if any(part in folded for part in SENSITIVE_KEYS) and folded not in {"auth_ref"}:
                raise ValueError(f"{path}.{key} must not contain a secret value")
            _reject_sensitive_values(item, path=f"{path}.{key}", depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) > 200:
            raise ValueError("extension manifest array is too large")
        for index, item in enumerate(value):
            _reject_sensitive_values(item, path=f"{path}[{index}]", depth=depth + 1)
    elif isinstance(value, str) and len(value) > 16 * 1024:
        raise ValueError("extension manifest value is too large")


def _validate_endpoint_shape(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("extension endpoint must not contain credentials or fragments")
    host = (parsed.hostname or "").casefold()
    if not host or parsed.path == "":
        raise ValueError("extension endpoint is incomplete")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ValueError("extension endpoint must use HTTPS except for loopback development")


def _ui_items(value: Any) -> tuple[dict[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("extension UI descriptors must be a bounded list")
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("extension UI descriptor must be an object")
        label = str(item.get("label", "")).strip()[:80]
        url = str(item.get("url", "")).strip()[:500]
        if not label or not _safe_navigation_url(url):
            raise ValueError("extension UI descriptor is invalid")
        result.append({"label": label, "url": url})
    return tuple(result)


def _command_items(value: Any) -> tuple[dict[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("extension command descriptors must be a bounded list")
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("extension command descriptor must be an object")
        command_id = str(item.get("id", "")).strip()
        label = str(item.get("label", "")).strip()[:80]
        if not _ID.fullmatch(command_id) or not label:
            raise ValueError("extension command descriptor is invalid")
        result.append({"id": command_id, "label": label})
    return tuple(result)


def _safe_navigation_url(url: str) -> bool:
    if url.startswith("/") and not url.startswith("//"):
        return True
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname)


class ExtensionManifestStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / ".simpleoffice-meta" / "extensions"
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, manifest: Mapping[str, Any]) -> ExtensionManifest:
        parsed = ExtensionManifest.from_dict(manifest, external=True)
        encoded = json.dumps(
            dict(manifest),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        if len(encoded.encode("utf-8")) > MANIFEST_MAX_BYTES:
            raise ValueError("extension manifest is too large")
        target = self.directory / f"{parsed.extension_id}.json"
        atomic_json_write(target, dict(manifest))
        return parsed

    def remove(self, extension_id: str) -> None:
        if not _ID.fullmatch(str(extension_id)):
            raise ValueError("invalid extension id")
        (self.directory / f"{extension_id}.json").unlink(missing_ok=True)

    def load(self, extension_id: str) -> ExtensionManifest:
        if not _ID.fullmatch(str(extension_id)):
            raise ValueError("invalid extension id")
        path = self.directory / f"{extension_id}.json"
        try:
            if path.is_symlink() or path.stat().st_size > MANIFEST_MAX_BYTES:
                raise ValueError("extension manifest is unsafe or too large")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LookupError("extension manifest not found") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("extension manifest is unreadable") from exc
        return ExtensionManifest.from_dict(payload, external=True)

    def discover(self) -> tuple[list[ExtensionManifest], list[dict[str, str]]]:
        manifests = []
        errors = []
        for path in sorted(self.directory.glob("*.json")):
            extension_id = path.stem
            try:
                manifests.append(self.load(extension_id))
            except (LookupError, OSError, ValueError) as exc:
                errors.append({
                    "id": extension_id[:64],
                    "error": str(exc)[:300],
                })
        return manifests, errors


class ExtensionStateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / ".simpleoffice-meta" / "extension-state.json"
        self.lock = self.path.with_suffix(".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"extensions": {}}
        if not isinstance(payload, dict) or not isinstance(payload.get("extensions"), dict):
            return {"extensions": {}}
        return payload

    def state(self, extension_id: str) -> dict[str, Any]:
        row = self._read().get("extensions", {}).get(str(extension_id), {})
        if not isinstance(row, dict):
            row = {}
        return {
            "enabled": bool(row.get("enabled", False)),
            "approved_capabilities": sorted({
                str(value)
                for value in row.get("approved_capabilities", [])
                if str(value) in KNOWN_CAPABILITIES
            }),
        }

    def set(
        self,
        extension_id: str,
        *,
        enabled: bool | None = None,
        approved_capabilities: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if not _ID.fullmatch(str(extension_id)):
            raise ValueError("invalid extension id")
        if approved_capabilities is not None:
            values = {str(value) for value in approved_capabilities}
            unknown = values - KNOWN_CAPABILITIES
            if unknown:
                raise ValueError("unknown extension capability approval")
        with exclusive_file_lock(self.lock):
            payload = self._read()
            extensions = payload.setdefault("extensions", {})
            row = dict(extensions.get(extension_id, {}))
            if enabled is not None:
                row["enabled"] = bool(enabled)
            if approved_capabilities is not None:
                row["approved_capabilities"] = sorted(set(approved_capabilities))
            extensions[extension_id] = row
            atomic_json_write(self.path, payload)
        return self.state(extension_id)


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: int = 60):
        self.failure_threshold = max(1, min(20, int(failure_threshold)))
        self.cooldown_seconds = max(5, min(3600, int(cooldown_seconds)))
        self._state: dict[str, tuple[int, float]] = {}

    def allow(self, key: str) -> bool:
        failures, opened_until = self._state.get(key, (0, 0.0))
        if opened_until and opened_until <= time.monotonic():
            self._state[key] = (0, 0.0)
            return True
        return not opened_until and failures < self.failure_threshold

    def success(self, key: str) -> None:
        self._state.pop(key, None)

    def failure(self, key: str) -> None:
        failures, opened_until = self._state.get(key, (0, 0.0))
        if opened_until:
            return
        failures += 1
        if failures >= self.failure_threshold:
            self._state[key] = (
                failures,
                time.monotonic() + self.cooldown_seconds,
            )
        else:
            self._state[key] = (failures, 0.0)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


SecretResolver = Callable[[str], str]
BuiltinHandler = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class HttpExtensionTransport:
    def __init__(
        self,
        *,
        allow_hosts: set[str] | frozenset[str] | None = None,
        secret_resolver: SecretResolver | None = None,
        breaker: CircuitBreaker | None = None,
    ):
        configured = allow_hosts
        if configured is None:
            configured = {
                item.strip().casefold()
                for item in os.environ.get(
                    "SIMPLEOFFICE_EXTENSION_HTTP_ALLOWLIST",
                    "",
                ).split(",")
                if item.strip()
            }
        self.allow_hosts = frozenset(configured)
        self.secret_resolver = secret_resolver
        self.breaker = breaker or CircuitBreaker()
        self._opener = build_opener(_NoRedirect())

    def _authorize_endpoint(self, manifest: ExtensionManifest) -> None:
        parsed = urlparse(manifest.endpoint)
        host = (parsed.hostname or "").casefold()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return
        if host not in self.allow_hosts:
            raise ExtensionDenied("extension endpoint host is not locally allowlisted")

    def invoke(
        self,
        manifest: ExtensionManifest,
        point: str,
        operation: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._authorize_endpoint(manifest)
        breaker_key = manifest.extension_id
        if not self.breaker.allow(breaker_key):
            raise ExtensionUnavailable("extension circuit breaker is open")
        envelope = {
            "api_version": API_VERSION,
            "extension_id": manifest.extension_id,
            "extension_version": manifest.version,
            "point": point,
            "operation": str(operation)[:100],
            "payload": dict(payload),
        }
        raw = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > REQUEST_MAX_BYTES:
            raise ExtensionCallError("extension request is too large")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "SimpleOffice4Me-Extension/1",
        }
        if manifest.auth_ref:
            if self.secret_resolver is None:
                raise ExtensionUnavailable("extension authentication reference is unavailable")
            secret = self.secret_resolver(manifest.auth_ref)
            if not secret:
                raise ExtensionUnavailable("extension authentication reference could not be resolved")
            headers["Authorization"] = "Bearer " + str(secret)
        request = Request(
            manifest.endpoint,
            data=raw,
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(
                request,
                timeout=manifest.timeout_seconds,
            ) as response:
                if not 200 <= int(response.status) < 300:
                    raise ExtensionCallError("extension returned a non-success status")
                body = response.read(RESPONSE_MAX_BYTES + 1)
                if len(body) > RESPONSE_MAX_BYTES:
                    raise ExtensionCallError("extension response is too large")
        except HTTPError as exc:
            self.breaker.failure(breaker_key)
            raise ExtensionCallError(
                f"extension returned HTTP {int(exc.code)}"
            ) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            self.breaker.failure(breaker_key)
            raise ExtensionCallError("extension transport failed") from exc
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.breaker.failure(breaker_key)
            raise ExtensionCallError("extension returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            self.breaker.failure(breaker_key)
            raise ExtensionCallError("extension response must be a JSON object")
        self.breaker.success(breaker_key)
        return decoded


@dataclass(frozen=True)
class ExtensionStatus:
    manifest: ExtensionManifest
    enabled: bool
    approved_capabilities: tuple[str, ...]
    ready_points: tuple[str, ...]


class ExtensionRegistry:
    def __init__(
        self,
        root: str | Path,
        *,
        transport: HttpExtensionTransport | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.manifests = ExtensionManifestStore(self.root)
        self.states = ExtensionStateStore(self.root)
        self.transport = transport or HttpExtensionTransport()
        self._builtins: dict[str, tuple[ExtensionManifest, dict[str, BuiltinHandler], frozenset[str], bool]] = {}

    def register_builtin(
        self,
        manifest: Mapping[str, Any],
        handlers: Mapping[str, BuiltinHandler],
        *,
        approved_capabilities: set[str] | frozenset[str],
        enabled: bool = True,
    ) -> ExtensionManifest:
        parsed = ExtensionManifest.from_dict(
            {
                **dict(manifest),
                "transport": {"type": "builtin"},
            },
            external=False,
        )
        unknown_handlers = set(handlers) - set(parsed.extension_points)
        if unknown_handlers:
            raise ValueError("builtin handler registered for an undeclared extension point")
        approvals = frozenset(str(value) for value in approved_capabilities)
        if not approvals.issubset(KNOWN_CAPABILITIES):
            raise ValueError("builtin approval contains an unknown capability")
        self._builtins[parsed.extension_id] = (
            parsed,
            dict(handlers),
            approvals,
            bool(enabled),
        )
        return parsed

    def status(self, extension_id: str) -> ExtensionStatus:
        if extension_id in self._builtins:
            manifest, _handlers, approvals, enabled = self._builtins[extension_id]
            return self._status(manifest, enabled, approvals)
        manifest = self.manifests.load(extension_id)
        state = self.states.state(extension_id)
        approvals = frozenset(state["approved_capabilities"])
        return self._status(manifest, state["enabled"], approvals)

    @staticmethod
    def _status(
        manifest: ExtensionManifest,
        enabled: bool,
        approvals: frozenset[str],
    ) -> ExtensionStatus:
        ready = tuple(
            point
            for point in manifest.extension_points
            if POINT_CAPABILITY[point] in approvals
            and POINT_CAPABILITY[point] in manifest.capabilities
        )
        return ExtensionStatus(
            manifest=manifest,
            enabled=bool(enabled),
            approved_capabilities=tuple(sorted(approvals)),
            ready_points=ready,
        )

    def list_status(self) -> tuple[list[ExtensionStatus], list[dict[str, str]]]:
        manifests, errors = self.manifests.discover()
        rows = []
        for manifest in manifests:
            state = self.states.state(manifest.extension_id)
            rows.append(
                self._status(
                    manifest,
                    state["enabled"],
                    frozenset(state["approved_capabilities"]),
                )
            )
        for extension_id, (manifest, _handlers, approvals, enabled) in self._builtins.items():
            if not any(row.manifest.extension_id == extension_id for row in rows):
                rows.append(self._status(manifest, enabled, approvals))
        rows.sort(key=lambda row: row.manifest.extension_id)
        return rows, errors

    def invoke(
        self,
        extension_id: str,
        point: str,
        operation: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not capability_enabled("v3.extensions"):
            raise ExtensionUnavailable("extension subsystem is disabled")
        if point not in POINT_CAPABILITY:
            raise ExtensionDenied("unknown extension point")
        status = self.status(extension_id)
        if not status.enabled:
            raise ExtensionUnavailable("extension is disabled")
        if point not in status.manifest.extension_points:
            raise ExtensionDenied("extension did not declare this extension point")
        required = POINT_CAPABILITY[point]
        if (
            required not in status.manifest.capabilities
            or required not in status.approved_capabilities
        ):
            raise ExtensionDenied("extension capability is not locally approved")
        safe_payload = dict(payload or {})
        _reject_sensitive_values(safe_payload, path="payload")
        encoded = json.dumps(
            safe_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > REQUEST_MAX_BYTES:
            raise ExtensionCallError("extension payload is too large")
        if extension_id in self._builtins:
            _manifest, handlers, _approvals, _enabled = self._builtins[extension_id]
            handler = handlers.get(point)
            if handler is None:
                raise ExtensionUnavailable("builtin extension point has no handler")
            try:
                result = handler(str(operation)[:100], safe_payload)
            except Exception as exc:
                raise ExtensionCallError("builtin extension failed") from exc
            if not isinstance(result, Mapping):
                raise ExtensionCallError("builtin extension returned an invalid result")
            return dict(result)
        return self.transport.invoke(
            status.manifest,
            point,
            operation,
            safe_payload,
        )

    def invoke_all(
        self,
        point: str,
        operation: str,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        rows, _errors = self.list_status()
        results = []
        failures = []
        for row in rows:
            if not row.enabled or point not in row.ready_points:
                continue
            try:
                result = self.invoke(
                    row.manifest.extension_id,
                    point,
                    operation,
                    payload,
                )
                results.append({
                    "extension_id": row.manifest.extension_id,
                    "result": result,
                })
            except ExtensionError:
                failures.append(row.manifest.extension_id)
        return results, sorted(failures)

    def ui_items(self) -> list[dict[str, str]]:
        if not capability_enabled("v3.extensions"):
            return []
        rows, _errors = self.list_status()
        result = []
        for row in rows:
            if (
                row.enabled
                and "ui.register" in row.approved_capabilities
                and "ui.register" in row.manifest.capabilities
            ):
                for item in row.manifest.ui:
                    result.append({
                        "extension_id": row.manifest.extension_id,
                        **item,
                    })
        return result[:100]


def external_search_hits(
    root: str | Path,
    query: str,
    *,
    actor: str,
    limit: int = 30,
) -> tuple[list[dict[str, str]], list[str]]:
    if not capability_enabled("v3.extensions"):
        return [], []
    registry = ExtensionRegistry(root)
    results, failures = registry.invoke_all(
        "search_provider",
        "search",
        {
            "query": " ".join(str(query).split())[:200],
            "actor": str(actor)[:200],
            "limit": max(1, min(100, int(limit))),
        },
    )
    hits = []
    for result in results:
        extension_id = result["extension_id"]
        values = result["result"].get("results", [])
        if not isinstance(values, list):
            continue
        for value in values[:limit]:
            if not isinstance(value, Mapping):
                continue
            title = str(value.get("title", "")).strip()[:200]
            url = str(value.get("url", "")).strip()[:500]
            if not title or not _safe_navigation_url(url):
                continue
            hits.append({
                "extension_id": extension_id,
                "title": title,
                "subtitle": str(value.get("subtitle", "")).strip()[:300],
                "kind": str(value.get("kind", "Extension")).strip()[:80] or "Extension",
                "url": url,
                "ref_id": str(value.get("ref_id", ""))[:200],
            })
            if len(hits) >= limit:
                return hits, failures
    return hits, failures
