"""Server-side feature/capability registry for the additive 3.0 rollout.

Flags live in the user-home disk store (``v3`` section, default off); an
explicit ``environ`` mapping remains as a unit-test seam only and never reads
the process environment.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Mapping


_TRUE = {"1", "true", "yes", "on"}

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Capability:
    key: str
    env_var: str
    description: str
    default: bool = False


_CAPABILITIES: dict[str, Capability] = {}


def register(key: str, env_var: str, description: str, *, default: bool = False) -> Capability:
    """Register a capability. Registration is deterministic and idempotent."""
    key = str(key).strip()
    env_var = str(env_var).strip()
    if not key or not env_var:
        raise ValueError("capability key and setting name are required")
    candidate = Capability(key=key, env_var=env_var, description=str(description).strip(), default=bool(default))
    existing = _CAPABILITIES.get(key)
    if existing is not None and existing != candidate:
        raise ValueError(f"capability {key!r} is already registered differently")
    _CAPABILITIES[key] = candidate
    return candidate


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().casefold() in _TRUE


def _store_flags(root=None) -> dict[str, bool]:
    """Read persisted flags; fail closed to defaults without a resolvable store."""
    try:
        if root is None:
            from flask import current_app, has_app_context

            if not has_app_context():
                return {}
            root = current_app.config["DOCUMENT_ROOT"]
        from .settings_store import SettingsStore

        section = SettingsStore(root).settings().get("v3", {})
        return section if isinstance(section, dict) else {}
    except (OSError, ValueError, KeyError, RuntimeError):
        return {}


def enabled(key: str, environ: Mapping[str, str] | None = None, *, root=None) -> bool:
    """Return False for unknown capabilities: optional features fail closed."""
    definition = _CAPABILITIES.get(str(key))
    if definition is None:
        return False
    if environ is not None:
        return _bool(environ.get(definition.env_var), definition.default)
    stored = _store_flags(root).get(definition.key, definition.default)
    return bool(stored) if isinstance(stored, bool) else definition.default


def state(key: str, environ: Mapping[str, str] | None = None, *, root=None) -> dict[str, object]:
    definition = _CAPABILITIES.get(str(key))
    if definition is None:
        return {
            "key": str(key),
            "known": False,
            "enabled": False,
            "description": "Unknown optional capability",
        }
    return {
        "key": definition.key,
        "known": True,
        "enabled": enabled(definition.key, environ, root=root),
        "description": definition.description,
    }


def snapshot(environ: Mapping[str, str] | None = None, *, root=None) -> list[dict[str, object]]:
    """Return a safe admin/diagnostic view; no secret values are exposed."""
    return [state(key, environ, root=root) for key in sorted(_CAPABILITIES)]


def run_if_enabled(key: str, operation, *, fallback=None, environ: Mapping[str, str] | None = None, root=None):
    """Execute an optional capability or return a caller-provided fallback."""
    if not enabled(key, environ, root=root):
        return fallback() if callable(fallback) else fallback
    return operation()


# Every 3.0 feature is off by default. New modules may be deployed independently.
register("v3.sample", "SIMPLEOFFICE_V3_SAMPLE_ENABLED", "Evolution-contract smoke capability")
register("v3.relations", "SIMPLEOFFICE_V3_RELATIONS_ENABLED", "Entity references and relation service")
register("v3.activity", "SIMPLEOFFICE_V3_ACTIVITY_ENABLED", "Domain events and activity stream")
register("v3.jobs", "SIMPLEOFFICE_V3_JOBS_ENABLED", "Background job and worker layer")
register("v3.policy", "SIMPLEOFFICE_V3_POLICY_ENABLED", "Central authorization policy facade")
register("v3.search", "SIMPLEOFFICE_V3_SEARCH_ENABLED", "Global search and command palette")
register("v3.entity_context", "SIMPLEOFFICE_V3_ENTITY_CONTEXT_ENABLED", "Unified entity detail context")
register("v3.inbox", "SIMPLEOFFICE_V3_INBOX_ENABLED", "Universal document inbox pipeline")
register("v3.automation", "SIMPLEOFFICE_V3_AUTOMATION_ENABLED", "Declarative automation rules")
register("v3.workboard", "SIMPLEOFFICE_V3_WORKBOARD_ENABLED", "Combined task and calendar workboard")
register("v3.crm", "SIMPLEOFFICE_V3_CRM_ENABLED", "CRM relationship workspace")
register("v3.finance", "SIMPLEOFFICE_V3_FINANCE_ENABLED", "Business-document lifecycle")
register("v3.extensions", "SIMPLEOFFICE_V3_EXTENSIONS_ENABLED", "Versioned extension API")
register("v3.health", "SIMPLEOFFICE_V3_HEALTH_ENABLED", "System health registry and dashboard")
register("v3.android_offline", "SIMPLEOFFICE_V3_ANDROID_OFFLINE_ENABLED", "Controlled Android offline cache")
register("v3.federation", "SIMPLEOFFICE_V3_FEDERATION_ENABLED", "Versioned federation transfer contract")


CAPABILITY_KEYS = tuple(sorted(_CAPABILITIES))


def normalize_v3_settings(value: Any) -> dict[str, bool]:
    """Coerce a stored v3 section to known boolean flags (default off)."""
    stored = value if isinstance(value, dict) else {}
    result: dict[str, bool] = {}
    for key in CAPABILITY_KEYS:
        flag = stored.get(key, False)
        if not isinstance(flag, bool):
            _LOGGER.warning("Invalid stored v3 capability %s; leaving it disabled", key)
            flag = False
        result[key] = flag
    return result


def validate_v3_settings(value: Any) -> dict[str, bool]:
    """Validate v3 flags for saving; unknown keys are dropped, non-booleans rejected."""
    if not isinstance(value, dict):
        raise ValueError("v3 capabilities must be an object")
    result = normalize_v3_settings(value)
    for key in CAPABILITY_KEYS:
        if key in value and not isinstance(value[key], bool):
            raise ValueError(f"v3 capability {key} must be a boolean")
    return result
