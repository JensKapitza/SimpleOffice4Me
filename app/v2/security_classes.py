"""Security classes for V2 storage consumers.

These policies are deliberately implementation-neutral.  They prevent sensitive
payloads such as password-vault material from silently inheriting ordinary
document deduplication, indexing or federation defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StorageSecurityClass(str, Enum):
    DOCUMENT = "document"
    PRIVATE = "private"
    SECRET = "secret"


@dataclass(frozen=True)
class StorageSecurityPolicy:
    security_class: StorageSecurityClass
    content_deduplication: bool
    automatic_federation: bool
    content_indexing: bool
    normal_storage_read_grants_apply: bool
    separate_unlock_required: bool


_POLICIES = {
    StorageSecurityClass.DOCUMENT: StorageSecurityPolicy(
        security_class=StorageSecurityClass.DOCUMENT,
        content_deduplication=True,
        automatic_federation=False,
        content_indexing=True,
        normal_storage_read_grants_apply=True,
        separate_unlock_required=False,
    ),
    StorageSecurityClass.PRIVATE: StorageSecurityPolicy(
        security_class=StorageSecurityClass.PRIVATE,
        content_deduplication=False,
        automatic_federation=False,
        content_indexing=False,
        normal_storage_read_grants_apply=True,
        separate_unlock_required=False,
    ),
    StorageSecurityClass.SECRET: StorageSecurityPolicy(
        security_class=StorageSecurityClass.SECRET,
        content_deduplication=False,
        automatic_federation=False,
        content_indexing=False,
        normal_storage_read_grants_apply=False,
        separate_unlock_required=True,
    ),
}


def security_policy(value: StorageSecurityClass | str) -> StorageSecurityPolicy:
    try:
        security_class = value if isinstance(value, StorageSecurityClass) else StorageSecurityClass(str(value))
    except ValueError as exc:
        raise ValueError("unknown V2 storage security class") from exc
    return _POLICIES[security_class]


VAULT_SECURITY_CLASS = StorageSecurityClass.SECRET
