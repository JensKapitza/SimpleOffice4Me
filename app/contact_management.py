"""Higher-level contact management operations built on ContactStore.

The service keeps bulk edits, duplicate merges and restores under the same
contacts write lock as normal ContactStore writes. Before destructive or
multi-field mutations it writes bounded snapshots so the previous state can
be restored without relying on browser state or CardDAV clients.
"""

from __future__ import annotations

import copy
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contact_store import ContactStore
from .document_store import atomic_json_write, utc_now
from .file_lock import exclusive_file_lock


@dataclass(frozen=True)
class DuplicateCandidate:
    left: dict[str, Any]
    right: dict[str, Any]
    score: int
    reasons: tuple[str, ...]
    confidence: str = "manual"
    trivial: bool = False


class ContactManagement:
    SNAPSHOT_LIMIT = 500
    BULK_LIMIT = 500

    def __init__(self, root: str | Path):
        self.store = ContactStore(root)
        self.store.initialize()
        self.snapshots_path = self.store.control / "contact-snapshots.json"

    @staticmethod
    def _collapse(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).strip()
        return re.sub(r"\s+", " ", text)

    @classmethod
    def _norm_text(cls, value: Any) -> str:
        return cls._collapse(value).casefold()

    @classmethod
    def _norm_loose_text(cls, value: Any) -> str:
        text = cls._norm_text(value)
        return re.sub(r"[^\w]+", "", text, flags=re.UNICODE)

    @classmethod
    def _norm_email(cls, value: Any) -> str:
        return cls._collapse(value).casefold()

    @staticmethod
    def _norm_phone(value: Any) -> str:
        raw = str(value or "").strip()
        if raw.startswith("+"):
            return "+" + "".join(character for character in raw[1:] if character.isdigit())
        return "".join(character for character in raw if character.isdigit())

    @classmethod
    def _norm_address(cls, value: Any) -> str:
        text = cls._norm_text(value)
        replacements = {
            "straße": "str", "strasse": "str", "str.": "str",
            "platz": "pl", "pl.": "pl",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        return re.sub(r"[^\w]+", "", text, flags=re.UNICODE)

    @classmethod
    def _normalized_display(cls, value: Any) -> str:
        return cls._collapse(value)

    def dashboard(self, actor: str) -> dict[str, Any]:
        contacts = self.store.contacts(actor)
        missing_email = sum(not self._norm_email(item.get("fields", {}).get("email")) for item in contacts)
        missing_phone = sum(not self._norm_phone(item.get("fields", {}).get("phone")) for item in contacts)
        missing_company = sum(not self._norm_text(item.get("fields", {}).get("company")) for item in contacts)
        tags = sorted({tag for item in contacts for tag in item.get("tags", []) if str(tag).strip()}, key=str.casefold)
        groups = sorted({group for item in contacts for group in item.get("groups", []) if str(group).strip()}, key=str.casefold)
        duplicates = self.duplicate_candidates(actor)
        return {
            "total": len(contacts),
            "missing_email": missing_email,
            "missing_phone": missing_phone,
            "missing_company": missing_company,
            "duplicate_pairs": len(duplicates),
            "duplicate_trivial": sum(item.trivial for item in duplicates),
            "duplicate_high_confidence": sum(item.confidence == "high" for item in duplicates),
            "tags": tags,
            "groups": groups,
        }

    def advanced_search(self, actor: str, query: str = "", tag: str = "", group: str = "", company: str = "", incomplete: str = "") -> list[dict[str, Any]]:
        items = self.store.search(query, actor) if query.strip() else self.store.contacts(actor)
        tag_n = self._norm_text(tag)
        group_n = self._norm_text(group)
        company_n = self._norm_text(company)
        result: list[dict[str, Any]] = []
        for item in items:
            fields = item.get("fields", {})
            if tag_n and tag_n not in {self._norm_text(value) for value in item.get("tags", [])}:
                continue
            if group_n and group_n not in {self._norm_text(value) for value in item.get("groups", [])}:
                continue
            if company_n and company_n not in self._norm_text(fields.get("company")):
                continue
            if incomplete == "email" and self._norm_email(fields.get("email")):
                continue
            if incomplete == "phone" and self._norm_phone(fields.get("phone")):
                continue
            if incomplete == "company" and self._norm_text(fields.get("company")):
                continue
            result.append(item)
        return result

    def duplicate_candidates(self, actor: str, minimum_score: int = 55) -> list[DuplicateCandidate]:
        contacts = self.store.contacts(actor)
        candidates: list[DuplicateCandidate] = []
        for index, left in enumerate(contacts):
            for right in contacts[index + 1:]:
                score, reasons, trivial = self._duplicate_score(left, right)
                if score < minimum_score:
                    continue
                confidence = "high" if score >= 90 else "likely" if score >= 70 else "manual"
                candidates.append(DuplicateCandidate(left, right, score, tuple(reasons), confidence, trivial))
        candidates.sort(key=lambda item: (-item.score, not item.trivial, self._norm_text(item.left.get("fields", {}).get("display_name"))))
        return candidates

    def _duplicate_score(self, left: dict[str, Any], right: dict[str, Any]) -> tuple[int, list[str], bool]:
        lf, rf = left.get("fields", {}), right.get("fields", {})
        score = 0
        reasons: list[str] = []
        strong_matches = 0
        conflicts = 0

        def equal_nonempty(a: str, b: str, points: int, reason: str, strong: bool = False) -> None:
            nonlocal score, strong_matches
            if a and a == b:
                score += points
                reasons.append(reason)
                if strong:
                    strong_matches += 1

        email_l, email_r = self._norm_email(lf.get("email")), self._norm_email(rf.get("email"))
        phone_l, phone_r = self._norm_phone(lf.get("phone")), self._norm_phone(rf.get("phone"))
        name_l, name_r = self._norm_text(lf.get("display_name")), self._norm_text(rf.get("display_name"))
        loose_name_l, loose_name_r = self._norm_loose_text(lf.get("display_name")), self._norm_loose_text(rf.get("display_name"))
        company_l, company_r = self._norm_text(lf.get("company")), self._norm_text(rf.get("company"))

        equal_nonempty(email_l, email_r, 75, "gleiche E-Mail", True)
        if phone_l and len(phone_l) >= 6 and phone_l == phone_r:
            score += 65; reasons.append("gleiche Telefonnummer"); strong_matches += 1
        equal_nonempty(name_l, name_r, 40, "gleicher Anzeigename")
        if loose_name_l and loose_name_l == loose_name_r and name_l != name_r:
            score += 30; reasons.append("Name nur durch Leerzeichen/Zeichen verschieden")
        equal_nonempty(company_l, company_r, 10, "gleiche Firma")

        left_name_parts = (self._norm_text(lf.get("first_name")), self._norm_text(lf.get("last_name")))
        right_name_parts = (self._norm_text(rf.get("first_name")), self._norm_text(rf.get("last_name")))
        if any(left_name_parts) and left_name_parts == right_name_parts and name_l != name_r:
            score += 30; reasons.append("gleicher Vor-/Nachname")

        left_addresses = {self._norm_address(row.get("value")) for row in left.get("addresses", []) if self._norm_address(row.get("value"))}
        right_addresses = {self._norm_address(row.get("value")) for row in right.get("addresses", []) if self._norm_address(row.get("value"))}
        if left_addresses and right_addresses and left_addresses.intersection(right_addresses):
            score += 45; reasons.append("gleiche Adresse normalisiert"); strong_matches += 1

        for key in ("email", "phone", "birthday"):
            lv, rv = self._norm_text(lf.get(key)), self._norm_text(rf.get(key))
            if lv and rv and lv != rv:
                conflicts += 1
        if conflicts:
            score -= min(30, conflicts * 10)
            reasons.append(f"{conflicts} abweichende Kernfelder")

        trivial = bool(score >= 70 and conflicts == 0 and (
            strong_matches >= 1 or (loose_name_l and loose_name_l == loose_name_r and left_addresses.intersection(right_addresses))
        ))
        return max(0, min(score, 100)), reasons, trivial

    def normalize_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(contact)
        fields = normalized.setdefault("fields", {})
        for key, value in list(fields.items()):
            if isinstance(value, str):
                fields[key] = self._normalized_display(value)
        if fields.get("email"):
            fields["email"] = self._norm_email(fields["email"])
        for address in normalized.get("addresses", []):
            if isinstance(address.get("label"), str):
                address["label"] = self._normalized_display(address["label"])
            if isinstance(address.get("value"), str):
                address["value"] = self._normalized_display(address["value"])
                address["normalized"] = self._norm_address(address["value"])
        normalized["tags"] = self._clean_labels(normalized.get("tags", []))
        normalized["groups"] = self._clean_labels(normalized.get("groups", []))
        return normalized

    def normalize_all(self, actor: str) -> int:
        """Apply only information-preserving cleanup such as trim and whitespace collapse."""
        principal = self.store._principal(actor)
        changed = 0
        with exclusive_file_lock(self.store.control / ".contacts-write.lock"):
            payload = self.store._read(self.store.contacts_path, {"contacts": []})
            for index, contact in enumerate(payload.get("contacts", [])):
                if not self.store._can_manage(contact, principal):
                    continue
                normalized = self.normalize_contact(contact)
                if normalized == contact:
                    continue
                self._snapshot_locked(contact, actor, "trivial_normalization")
                normalized["updated_at"], normalized["updated_by"] = utc_now(), actor
                payload["contacts"][index] = normalized
                changed += 1
            if changed:
                atomic_json_write(self.store.contacts_path, payload)
                self.store.history.record("contacts_trivial_normalized", actor, "contacts", "bulk", {"changed": changed})
        return changed

    def _read_snapshots(self) -> dict[str, Any]:
        return self.store._read(self.snapshots_path, {"snapshots": []})

    def _snapshot_locked(self, contact: dict[str, Any], actor: str, reason: str) -> dict[str, Any]:
        payload = self._read_snapshots()
        snapshot = {"snapshot_id": str(uuid.uuid4()), "contact_id": contact.get("contact_id", ""), "created_at": utc_now(), "created_by": actor, "reason": reason, "contact": copy.deepcopy(contact)}
        payload["snapshots"] = (payload.get("snapshots", []) + [snapshot])[-self.SNAPSHOT_LIMIT:]
        atomic_json_write(self.snapshots_path, payload)
        return snapshot

    def snapshots(self, contact_id: str, actor: str) -> list[dict[str, Any]]:
        self.store.get(contact_id, actor)
        rows = [row for row in self._read_snapshots().get("snapshots", []) if row.get("contact_id") == contact_id]
        return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)

    def update_metadata(self, contact_id: str, actor: str, tags: list[str], groups: list[str]) -> dict[str, Any]:
        principal = self.store._principal(actor)
        clean_tags = self._clean_labels(tags)
        clean_groups = self._clean_labels(groups)
        with exclusive_file_lock(self.store.control / ".contacts-write.lock"):
            payload = self.store._read(self.store.contacts_path, {"contacts": []})
            contact = next((item for item in payload.get("contacts", []) if item.get("contact_id") == contact_id), None)
            if contact is None or not self.store._can_manage(contact, principal):
                raise ValueError("contact is not shared with this user")
            if contact.get("tags", []) == clean_tags and contact.get("groups", []) == clean_groups:
                return contact
            self._snapshot_locked(contact, actor, "metadata_update")
            before = {"tags": list(contact.get("tags", [])), "groups": list(contact.get("groups", []))}
            contact["tags"] = clean_tags
            contact["groups"] = clean_groups
            contact["updated_at"] = utc_now()
            contact["updated_by"] = actor
            atomic_json_write(self.store.contacts_path, payload)
            self.store.history.record("contact_metadata_updated", actor, "contacts", contact_id, {"before": before, "after": {"tags": clean_tags, "groups": clean_groups}})
            return contact

    @staticmethod
    def _clean_labels(values: list[str]) -> list[str]:
        cleaned = {re.sub(r"\s+", " ", str(value).strip()) for value in values if str(value).strip()}
        return sorted(cleaned, key=str.casefold)[:100]

    def bulk_metadata(self, contact_ids: list[str], actor: str, add_tags: list[str], add_groups: list[str]) -> int:
        unique_ids = list(dict.fromkeys(contact_ids))[:self.BULK_LIMIT]
        if not unique_ids:
            raise ValueError("no contacts selected")
        principal = self.store._principal(actor)
        tags_to_add = set(self._clean_labels(add_tags))
        groups_to_add = set(self._clean_labels(add_groups))
        changed = 0
        with exclusive_file_lock(self.store.control / ".contacts-write.lock"):
            payload = self.store._read(self.store.contacts_path, {"contacts": []})
            by_id = {item.get("contact_id"): item for item in payload.get("contacts", [])}
            selected = [by_id.get(contact_id) for contact_id in unique_ids]
            if any(item is None or not self.store._can_manage(item, principal) for item in selected):
                raise ValueError("one or more contacts are not editable")
            for contact in selected:
                assert contact is not None
                new_tags = self._clean_labels([*contact.get("tags", []), *tags_to_add])
                new_groups = self._clean_labels([*contact.get("groups", []), *groups_to_add])
                if new_tags == contact.get("tags", []) and new_groups == contact.get("groups", []):
                    continue
                self._snapshot_locked(contact, actor, "bulk_metadata")
                contact["tags"], contact["groups"] = new_tags, new_groups
                contact["updated_at"], contact["updated_by"] = utc_now(), actor
                changed += 1
            atomic_json_write(self.store.contacts_path, payload)
            self.store.history.record("contacts_bulk_metadata_updated", actor, "contacts", "bulk", {"contact_ids": unique_ids, "changed": changed, "tags_added": sorted(tags_to_add), "groups_added": sorted(groups_to_add)})
        return changed

    def merge(self, target_id: str, source_id: str, actor: str) -> dict[str, Any]:
        if not target_id or not source_id or target_id == source_id:
            raise ValueError("two different contacts are required")
        principal = self.store._principal(actor)
        with exclusive_file_lock(self.store.control / ".contacts-write.lock"):
            payload = self.store._read(self.store.contacts_path, {"contacts": []})
            target = next((item for item in payload.get("contacts", []) if item.get("contact_id") == target_id), None)
            source = next((item for item in payload.get("contacts", []) if item.get("contact_id") == source_id), None)
            if target is None or source is None:
                raise ValueError("unknown contact")
            if not self.store._can_manage(target, principal) or not self.store._can_manage(source, principal):
                raise ValueError("both contacts must be editable")
            target_owner = target.get("owner") or self.store._principal(str(target.get("created_by", "")))
            source_owner = source.get("owner") or self.store._principal(str(source.get("created_by", "")))
            if target_owner != source_owner:
                raise ValueError("contacts with different owners cannot be merged")
            self._snapshot_locked(target, actor, "merge_target")
            self._snapshot_locked(source, actor, "merge_source")
            target_n, source_n = self.normalize_contact(target), self.normalize_contact(source)
            merged = copy.deepcopy(target_n)
            merged_fields = dict(source_n.get("fields", {}))
            merged_fields.update({key: value for key, value in target_n.get("fields", {}).items() if str(value).strip()})
            merged["fields"] = merged_fields
            address_keys: set[str] = set()
            addresses: list[dict[str, Any]] = []
            for address in [*target_n.get("addresses", []), *source_n.get("addresses", [])]:
                key = self._norm_address(address.get("value"))
                if key and key in address_keys:
                    continue
                if key:
                    address_keys.add(key)
                addresses.append(copy.deepcopy(address))
            merged["addresses"] = addresses
            merged["tags"] = self._clean_labels([*target_n.get("tags", []), *source_n.get("tags", [])])
            merged["groups"] = self._clean_labels([*target_n.get("groups", []), *source_n.get("groups", [])])
            merged["managers"] = sorted(set(target_n.get("managers", [])) | set(source_n.get("managers", [])), key=str.casefold)
            merged["readers"] = sorted((set(target_n.get("readers", [])) | set(source_n.get("readers", []))) - set(merged["managers"]), key=str.casefold)
            merged["changes"] = [*source_n.get("changes", []), *target_n.get("changes", [])][-200:]
            merged["updated_at"], merged["updated_by"] = utc_now(), actor
            merged.setdefault("merged_from", []).append({"contact_id": source_id, "at": merged["updated_at"], "actor": actor})
            payload["contacts"] = [item for item in payload.get("contacts", []) if item.get("contact_id") not in {target_id, source_id}] + [merged]
            atomic_json_write(self.store.contacts_path, payload)
            self.store.history.record("contacts_merged", actor, "contacts", target_id, {"target_id": target_id, "source_id": source_id, "result": merged})
            return merged

    def restore(self, snapshot_id: str, actor: str) -> dict[str, Any]:
        principal = self.store._principal(actor)
        snapshots = self._read_snapshots().get("snapshots", [])
        snapshot = next((item for item in snapshots if item.get("snapshot_id") == snapshot_id), None)
        if snapshot is None or not isinstance(snapshot.get("contact"), dict):
            raise ValueError("unknown contact snapshot")
        archived = copy.deepcopy(snapshot["contact"])
        contact_id = str(archived.get("contact_id", ""))
        with exclusive_file_lock(self.store.control / ".contacts-write.lock"):
            payload = self.store._read(self.store.contacts_path, {"contacts": []})
            current = next((item for item in payload.get("contacts", []) if item.get("contact_id") == contact_id), None)
            permission_source = current or archived
            if not self.store._can_manage(permission_source, principal):
                raise ValueError("contact is not shared with this user")
            if current:
                self._snapshot_locked(current, actor, "before_restore")
            archived["updated_at"], archived["updated_by"] = utc_now(), actor
            payload["contacts"] = [item for item in payload.get("contacts", []) if item.get("contact_id") != contact_id] + [archived]
            atomic_json_write(self.store.contacts_path, payload)
            self.store.history.record("contact_snapshot_restored", actor, "contacts", contact_id, {"snapshot_id": snapshot_id, "restored": archived})
            return archived

    def import_preview(self, text: str, actor: str) -> dict[str, Any]:
        if len(text.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("import preview is limited to 2 MiB")
        lines = [line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
        if not lines:
            raise ValueError("empty import")
        delimiter = ";" if lines[0].count(";") >= lines[0].count(",") else ","
        headers = [self._norm_text(value).replace(" ", "_") for value in lines[0].split(delimiter)]
        rows: list[dict[str, str]] = []
        for line in lines[1:101]:
            values = [value.strip() for value in line.split(delimiter)]
            rows.append({headers[index]: value for index, value in enumerate(values) if index < len(headers)})
        return {"delimiter": delimiter, "headers": headers, "rows": rows, "truncated": len(lines) > 101, "existing_contacts": len(self.store.contacts(actor))}
