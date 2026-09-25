"""Safe V1 document-metadata projection into the V2 metadata contract.

The adapter is additive: V1 metadata stays authoritative during migration and is
never rewritten. The V2 projection is versioned, private and tied to the exact
legacy metadata snapshot so migration verification can detect drift.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .contracts import LogicalObjectId
from .metadata import (
    FilenameAlias,
    MetadataEnvelope,
    MetadataSource,
    MetadataValue,
    NamespaceRules,
    Provenance,
    TrustLevel,
    VerificationStatus,
    original_value,
)


FORMAT = "simpleoffice-v2-metadata-envelope"
FORMAT_VERSION = 1
PRODUCER = "v1-migration-adapter"
_UNKNOWN_TIME = "1970-01-01T00:00:00Z"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _legacy_digest(metadata: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(metadata))).hexdigest()


def _time(metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return _UNKNOWN_TIME


def _portable_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("[external]"):
        return ""
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _basename(value: Any) -> str:
    path = _portable_path(value)
    return PurePosixPath(path).name if path else ""


def _initial_path(metadata: Mapping[str, Any], current_path: str) -> str:
    history = metadata.get("location_history")
    if isinstance(history, list):
        for event in history:
            if not isinstance(event, Mapping):
                continue
            candidate = _portable_path(event.get("from"))
            if candidate:
                return candidate
    return _portable_path(current_path)


def _provenance(
    source: MetadataSource,
    document_id: str,
    observed_at: str,
    *,
    suffix: str = "",
    actor: str = "",
) -> Provenance:
    source_ref = f"v1-document:{document_id}"
    if suffix:
        source_ref += f":{suffix}"
    return Provenance(
        source=source,
        source_ref=source_ref,
        observed_at=observed_at,
        actor=str(actor or ""),
    )


def _observed_value(
    name: str,
    value: Any,
    provenance: Provenance,
    *,
    verification: VerificationStatus = VerificationStatus.UNVERIFIED,
) -> MetadataValue:
    return MetadataValue(
        name=name,
        value=value,
        provenance=provenance,
        trust=TrustLevel.LOCAL,
        verification=verification,
    )


def legacy_metadata_envelope(
    metadata: Mapping[str, Any],
    *,
    current_path: str,
    current_size: int,
    current_sha256: str,
) -> MetadataEnvelope:
    """Map one verified V1 document metadata record into a V2 envelope."""

    document_id = str(metadata.get("document_id") or "").strip()
    if not document_id:
        raise ValueError("legacy metadata document_id is missing")
    if isinstance(current_size, bool) or not isinstance(current_size, int) or current_size < 0:
        raise ValueError("current document size is invalid")
    digest = str(current_sha256 or "").strip().casefold()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("current document sha256 is invalid")

    relative_path = _portable_path(current_path)
    if not relative_path:
        raise ValueError("current document path is unavailable")

    first_seen = _time(metadata, "first_seen_at", "last_seen_at")
    last_seen = _time(metadata, "last_seen_at", "first_seen_at")
    initial_path = _initial_path(metadata, relative_path)
    configured_original = str(metadata.get("original_name") or "").strip()
    original_name = _basename(configured_original) if configured_original else _basename(initial_path)
    if not original_name:
        original_name = _basename(relative_path)
    rules = NamespaceRules()

    original_provenance = _provenance(
        MetadataSource.ORIGINAL,
        document_id,
        first_seen,
        suffix="initial",
    )
    original_sha = str(metadata.get("original_sha256") or digest).strip().casefold()
    original_verification = (
        VerificationStatus.VERIFIED
        if original_sha == digest
        else VerificationStatus.UNVERIFIED
    )
    original = (
        original_value(
            "filename",
            original_name,
            original_provenance,
            verification=VerificationStatus.VERIFIED,
        ),
        original_value(
            "suffix",
            PurePosixPath(original_name).suffix,
            original_provenance,
            verification=VerificationStatus.VERIFIED,
        ),
        original_value(
            "sha256",
            original_sha,
            original_provenance,
            verification=original_verification,
        ),
        original_value(
            "first_seen_at",
            first_seen,
            original_provenance,
            verification=VerificationStatus.VERIFIED,
        ),
    )

    observed_provenance = _provenance(
        MetadataSource.OBSERVED,
        document_id,
        last_seen,
        suffix="migration",
    )
    observed_values: list[MetadataValue] = [
        _observed_value(
            "current_path",
            relative_path,
            observed_provenance,
            verification=VerificationStatus.VERIFIED,
        ),
        _observed_value(
            "current_filename",
            _basename(relative_path),
            observed_provenance,
            verification=VerificationStatus.VERIFIED,
        ),
        _observed_value(
            "current_size",
            current_size,
            observed_provenance,
            verification=VerificationStatus.VERIFIED,
        ),
        _observed_value(
            "current_sha256",
            digest,
            observed_provenance,
            verification=VerificationStatus.VERIFIED,
        ),
        _observed_value(
            "last_seen_at",
            last_seen,
            observed_provenance,
            verification=VerificationStatus.VERIFIED,
        ),
    ]
    for key in ("state", "system_state", "version_number", "tags"):
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            observed_values.append(_observed_value(key, value, observed_provenance))

    aliases: list[FilenameAlias] = []
    seen_names: set[str] = set()

    def add_alias(name: str, provenance: Provenance) -> None:
        if not name:
            return
        try:
            alias = FilenameAlias(name, provenance, rules)
        except ValueError:
            return
        if alias.name in seen_names:
            return
        seen_names.add(alias.name)
        aliases.append(alias)

    add_alias(original_name, original_provenance)
    history = metadata.get("location_history")
    if isinstance(history, list):
        for index, event in enumerate(history):
            if not isinstance(event, Mapping):
                continue
            event_time = str(event.get("at") or last_seen).strip() or last_seen
            event_actor = str(event.get("actor") or "")
            provenance = _provenance(
                MetadataSource.OBSERVED,
                document_id,
                event_time,
                suffix=f"location-{index}",
                actor=event_actor,
            )
            add_alias(_basename(event.get("from")), provenance)
            add_alias(_basename(event.get("to")), provenance)
    add_alias(_basename(relative_path), observed_provenance)

    federated: tuple[MetadataValue, ...] = ()
    attributes = metadata.get("attributes")
    if isinstance(attributes, Mapping):
        origin = attributes.get("federation_origin")
        if isinstance(origin, Mapping) and origin:
            peer_id = str(origin.get("peer_id") or "unknown").strip() or "unknown"
            federation_provenance = Provenance(
                source=MetadataSource.FEDERATED,
                source_ref=f"peer:{peer_id}",
                observed_at=last_seen,
            )
            federated = (
                MetadataValue(
                    "federation_origin",
                    dict(origin),
                    federation_provenance,
                    trust=TrustLevel.UNKNOWN,
                    verification=VerificationStatus.UNVERIFIED,
                ),
            )

    sidecar = {
        "legacy_metadata_sha256": _legacy_digest(metadata),
        "legacy_metadata_version": metadata.get("version", 1),
        "legacy_field_names": sorted(str(key) for key in metadata.keys()),
    }
    return MetadataEnvelope(
        object_id=LogicalObjectId(document_id),
        original=original,
        observed=tuple(observed_values),
        federated=federated,
        aliases=tuple(aliases),
        sidecar=sidecar,
    )


def _provenance_dict(value: Provenance) -> dict[str, Any]:
    return {
        "source": value.source.value,
        "source_ref": value.source_ref,
        "observed_at": value.observed_at,
        "actor": value.actor,
    }


def _metadata_value_dict(value: MetadataValue) -> dict[str, Any]:
    return {
        "name": value.name,
        "value": value.value,
        "provenance": _provenance_dict(value.provenance),
        "trust": value.trust.value,
        "verification": value.verification.value,
        "immutable": value.immutable,
    }


def metadata_envelope_dict(envelope: MetadataEnvelope) -> dict[str, Any]:
    def alias_dict(alias: FilenameAlias) -> dict[str, Any]:
        return {
            "name": alias.name,
            "provenance": _provenance_dict(alias.provenance),
            "namespace": {
                "case_sensitive": alias.namespace.case_sensitive,
                "unicode_normalization": alias.namespace.unicode_normalization.value,
                "max_name_bytes": alias.namespace.max_name_bytes,
            },
        }

    return {
        "object_id": envelope.object_id.value,
        "original": [_metadata_value_dict(item) for item in envelope.original],
        "observed": [_metadata_value_dict(item) for item in envelope.observed],
        "imported": [_metadata_value_dict(item) for item in envelope.imported],
        "federated": [_metadata_value_dict(item) for item in envelope.federated],
        "aliases": [alias_dict(item) for item in envelope.aliases],
        "sidecar": dict(envelope.sidecar),
    }


def build_legacy_metadata_projection(
    metadata: Mapping[str, Any],
    *,
    current_path: str,
    current_size: int,
    current_sha256: str,
) -> dict[str, Any]:
    envelope = legacy_metadata_envelope(
        metadata,
        current_path=current_path,
        current_size=current_size,
        current_sha256=current_sha256,
    )
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "producer": PRODUCER,
        "envelope": metadata_envelope_dict(envelope),
    }


class V2MetadataMigrationStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / ".simpleoffice-v2" / "metadata"

    @staticmethod
    def _name(object_id: str) -> str:
        return hashlib.sha256(object_id.encode("utf-8")).hexdigest() + ".json"

    def path_for(self, object_id: str) -> Path:
        value = str(object_id or "").strip()
        if not value:
            raise ValueError("metadata object id is required")
        return self.directory / self._name(value)

    def read(self, object_id: str) -> dict[str, Any]:
        target = self.path_for(object_id)
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("V2 metadata projection is unreadable") from exc
        if not isinstance(value, dict):
            raise ValueError("V2 metadata projection is not an object")
        return value

    def write(self, projection: Mapping[str, Any]) -> str:
        value = dict(projection)
        if (
            value.get("format") != FORMAT
            or int(value.get("format_version", 0)) != FORMAT_VERSION
            or value.get("producer") != PRODUCER
            or not isinstance(value.get("envelope"), Mapping)
        ):
            raise ValueError("unsupported V2 metadata projection")
        object_id = str(value["envelope"].get("object_id") or "").strip()
        target = self.path_for(object_id)
        existing: dict[str, Any] | None = None
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file():
                raise ValueError("V2 metadata projection path is unsafe")
            existing = self.read(object_id)
            if existing == value:
                return "unchanged"
            if existing.get("producer") != PRODUCER:
                raise ValueError("existing V2 metadata is owned by another producer")
            if existing.get("envelope", {}).get("original") != value["envelope"].get("original"):
                raise ValueError("existing V2 original metadata conflicts with V1")

        if self.directory.is_symlink() or self.directory.parent.is_symlink():
            raise ValueError("V2 metadata directory is unsafe")
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("V2 metadata directory is unsafe")
        if os.name == "posix":
            os.chmod(self.directory, 0o700)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        published = False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            published = True
            if os.name == "posix":
                os.chmod(target, 0o600)
        finally:
            if not published:
                temporary.unlink(missing_ok=True)
        return "updated" if existing is not None else "created"

    def verify(self, projection: Mapping[str, Any]) -> bool:
        envelope = projection.get("envelope")
        if not isinstance(envelope, Mapping):
            return False
        object_id = str(envelope.get("object_id") or "").strip()
        if not object_id:
            return False
        try:
            return self.read(object_id) == dict(projection)
        except (FileNotFoundError, ValueError):
            return False
