"""Security policy for data-quality gamification.

Gamification is never an authorization mechanism.  Callers must prove normal
read/write access separately; this module only narrows that access further.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

SCOPES = frozenset({"local", "federation", "organization"})
PROVIDERS = frozenset({"images", "documents", "contacts"})

# These classes cannot enter the normal game, even when a broad collection is
# selected. A future dedicated business workflow must opt into them elsewhere.
HARD_EXCLUDED_CLASSES = frozenset({
    "invoice", "invoices", "billing", "accounting", "receipt", "receipts",
    "payment", "payments", "banking", "crm_internal", "credential",
    "credentials", "secret", "security", "private_note", "private_notes",
})

# Contact fields that may be offered by the first contact provider. Sensitive
# notes and arbitrary custom fields deliberately do not inherit this allowlist.
DEFAULT_CONTACT_FIELDS = frozenset({
    "display_name", "first_name", "last_name", "company", "organization",
    "street", "house_number", "postal_code", "city", "country",
    "phone", "mobile", "email",
})


@dataclass(frozen=True)
class GamePolicy:
    scope: str = "local"
    providers: frozenset[str] = field(default_factory=frozenset)
    collections: frozenset[str] = field(default_factory=frozenset)
    fields: frozenset[str] = field(default_factory=frozenset)
    participants: frozenset[str] = field(default_factory=frozenset)
    preview_allowed: bool = False
    original_allowed: bool = False
    submit_proposals: bool = False
    view_other_proposals: bool = False
    auto_accept_consensus: bool = False
    min_votes: int = 3
    consensus_ratio: float = 0.75

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise ValueError("invalid gamification scope")
        if not self.providers.issubset(PROVIDERS):
            raise ValueError("unknown gamification provider")
        if self.min_votes < 1:
            raise ValueError("min_votes must be positive")
        if not 0.5 <= self.consensus_ratio <= 1.0:
            raise ValueError("consensus_ratio must be between 0.5 and 1.0")
        # Remote originals require an explicit policy; the default is preview
        # only. Keeping the flag legal allows tightly controlled future modes.


def normalize_classification(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_hard_excluded(*classifications: str | None) -> bool:
    return any(normalize_classification(value) in HARD_EXCLUDED_CLASSES for value in classifications if value)


def can_expose(
    policy: GamePolicy,
    *,
    provider: str,
    actor: str,
    normal_read_allowed: bool,
    resource_class: str = "",
    collection: str = "",
    field_name: str = "",
    federation_allowed: bool = True,
    organization_member: bool = False,
) -> bool:
    """Return True only when every required permission layer allows exposure."""
    if not normal_read_allowed or provider not in policy.providers:
        return False
    if is_hard_excluded(resource_class):
        return False
    if policy.participants and actor not in policy.participants:
        return False
    if policy.collections and collection not in policy.collections:
        return False
    if policy.fields and field_name and field_name not in policy.fields:
        return False
    if provider == "contacts" and field_name and field_name not in DEFAULT_CONTACT_FIELDS:
        return False
    if policy.scope == "federation" and not federation_allowed:
        return False
    if policy.scope == "organization" and not organization_member:
        return False
    return True


def may_deliver_original(policy: GamePolicy, **access: object) -> bool:
    return bool(policy.original_allowed and can_expose(policy, **access))


def may_deliver_preview(policy: GamePolicy, **access: object) -> bool:
    return bool(policy.preview_allowed and can_expose(policy, **access))


def allowed_contact_fields(policy: GamePolicy, requested: Iterable[str]) -> tuple[str, ...]:
    """Intersect requested fields with both policy and the safe contact allowlist."""
    allowed = DEFAULT_CONTACT_FIELDS
    if policy.fields:
        allowed = allowed.intersection(policy.fields)
    return tuple(field for field in requested if field in allowed)
