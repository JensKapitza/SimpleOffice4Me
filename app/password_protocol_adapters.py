"""Protocol adapter registry for existing password-manager clients.

Adapters are intentionally capability-gated. A protocol is never advertised as
fully compatible until its login, sync, CRUD and crypto semantics have passed
fixture/client tests. Protocol-native opaque ciphertext may be stored separately
from the SimpleOffice plaintext-capable vault so we do not silently weaken the
client's end-to-end encryption model.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class PasswordProtocolAdapter:
    protocol: str
    status: str
    client_side_encryption: bool
    configurable_server_url: bool
    discovery: bool
    authentication: bool
    sync: bool
    create_update_delete: bool
    totp: bool
    folders_or_collections: bool
    notes: str = ""

    @property
    def compatible(self) -> bool:
        return all((
            self.status == "compatible",
            self.discovery,
            self.authentication,
            self.sync,
            self.create_update_delete,
        ))

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["compatible"] = self.compatible
        return data


ADAPTERS: dict[str, PasswordProtocolAdapter] = {
    "bitwarden": PasswordProtocolAdapter(
        protocol="bitwarden",
        status="experimental",
        client_side_encryption=True,
        configurable_server_url=True,
        discovery=True,
        authentication=False,
        sync=False,
        create_update_delete=False,
        totp=False,
        folders_or_collections=False,
        notes="Discovery/KDF compatibility surface exists; full client crypto/login/sync is gated until tested.",
    ),
    "psono": PasswordProtocolAdapter(
        protocol="psono",
        status="experimental",
        client_side_encryption=True,
        configurable_server_url=True,
        discovery=True,
        authentication=False,
        sync=False,
        create_update_delete=False,
        totp=False,
        folders_or_collections=False,
        notes="Remote server discovery is supported; Psono session/secret crypto remains gated.",
    ),
    "passbolt": PasswordProtocolAdapter(
        protocol="passbolt",
        status="planned",
        client_side_encryption=True,
        configurable_server_url=True,
        discovery=False,
        authentication=False,
        sync=False,
        create_update_delete=False,
        totp=False,
        folders_or_collections=False,
        notes="OpenPGP-based protocol requires a dedicated adapter.",
    ),
    "nextcloud-passwords": PasswordProtocolAdapter(
        protocol="nextcloud-passwords",
        status="planned",
        client_side_encryption=True,
        configurable_server_url=True,
        discovery=False,
        authentication=False,
        sync=False,
        create_update_delete=False,
        totp=False,
        folders_or_collections=False,
        notes="Optional future adapter for already-installed Nextcloud Passwords clients.",
    ),
    "keepass-kdbx": PasswordProtocolAdapter(
        protocol="keepass-kdbx",
        status="planned-import-export",
        client_side_encryption=True,
        configurable_server_url=False,
        discovery=False,
        authentication=False,
        sync=False,
        create_update_delete=False,
        totp=True,
        folders_or_collections=True,
        notes="KDBX is treated as a portable encrypted file format, not a live server protocol.",
    ),
}


def adapter_capabilities(protocol: str | None = None) -> dict[str, Any]:
    if protocol is None:
        return {name: adapter.public() for name, adapter in ADAPTERS.items()}
    name = str(protocol).strip().casefold()
    if name not in ADAPTERS:
        raise ValueError("Unbekannter Passwort-Protokolladapter")
    return ADAPTERS[name].public()


def require_compatible(protocol: str) -> PasswordProtocolAdapter:
    name = str(protocol).strip().casefold()
    adapter = ADAPTERS.get(name)
    if adapter is None:
        raise ValueError("Unbekannter Passwort-Protokolladapter")
    if not adapter.compatible:
        raise RuntimeError(f"{name} ist noch nicht als vollständig kompatibel freigeschaltet")
    return adapter
