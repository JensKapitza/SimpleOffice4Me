"""Server-side feature/capability registry for the additive 3.0 rollout."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


_TRUE = {"1", "true", "yes", "on"}


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
        raise ValueError("capability key and environment variable are required")
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


def enabled(key: str, environ: Mapping[str, str] | None = None) -> bool:
    """Return False for unknown capabilities: optional features fail closed."""
    definition = _CAPABILITIES.get(str(key))
    if definition is None:
        return False
    source = os.environ if environ is None else environ
    return _bool(source.get(definition.env_var), definition.default)


def state(key: str, environ: Mapping[str, str] | None = None) -> dict[str, object]:
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
        "enabled": enabled(definition.key, environ),
        "description": definition.description,
    }


def snapshot(environ: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    """Return a safe admin/diagnostic view; no environment values are exposed."""
    return [state(key, environ) for key in sorted(_CAPABILITIES)]


def run_if_enabled(key: str, operation, *, fallback=None, environ: Mapping[str, str] | None = None):
    """Execute an optional capability or return a caller-provided fallback."""
    if not enabled(key, environ):
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
