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
from dataclasses import dataclass, replace
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
    differences: tuple[dict[str, str], ...] = ()
    additions: int = 0
    conflicts: int = 0
    blocking_conflicts: int = 0
    bulk_eligible: bool = False


class ContactManagement:
    SNAPSHOT_LIMIT = 500
    BULK_LIMIT = 500
    BULK_MERGE_LIMIT = 100
    MANUAL_MERGE_LIMIT = 100
    COPY_MARKER_RE = re.compile(r"\b(?:copy|kopie)\b(?:\s+(?:von|of))?(?:\s+\d+)?", re.IGNORECASE)

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

    @classmethod
    def _clean_name(cls, value: Any) -> str:
        """Remove standalone importer markers such as Copy or Kopie."""
        text = cls._collapse(value)
        return cls._collapse(cls.COPY_MARKER_RE.sub("", text).strip(" -–—,()[]"))

    @staticmethod
    def _raw_property_value(line: str) -> str:
        return ContactStore._unescape_vcard_text(str(line).partition(":")[2]).strip()

    def _property_values(self, contact: dict[str, Any], field: str, property_name: str) -> list[str]:
        values: list[str] = []
        primary = self._collapse(contact.get("fields", {}).get(field))
        if primary:
            values.append(primary)
        for key, raw in contact.get("fields", {}).items():
            if not str(key).startswith("vcard_"):
                continue
            safe = ContactStore._safe_raw_vcard_line(str(raw))
            if safe and ContactStore._vcard_property_name(safe) == property_name:
                value = self._raw_property_value(safe)
                if value:
                    values.append(value)
        normalizer = self._norm_email if field == "email" else self._norm_phone if field == "phone" else self._norm_text
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = normalizer(value)
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(value)
        return result

    def _contact_signals(self, contact: dict[str, Any]) -> dict[str, set[str]]:
        fields = contact.get("fields", {})
        return {
            "email": {self._norm_email(value) for value in self._property_values(contact, "email", "EMAIL") if self._norm_email(value)},
            "phone": {self._norm_phone(value) for value in self._property_values(contact, "phone", "TEL") if len(self._norm_phone(value)) >= 6},
            "name": {value for value in (
                self._norm_text(self._clean_name(fields.get("display_name"))),
                "\x1f".join((self._norm_text(self._clean_name(fields.get("first_name"))), self._norm_text(self._clean_name(fields.get("last_name"))))),
            ) if value and value != "\x1f"},
            "company": {self._norm_text(fields.get("company"))} - {""},
            "address": {self._norm_address(row.get("value")) for row in contact.get("addresses", []) if self._norm_address(row.get("value"))},
        }

    def dashboard(self, actor: str, contacts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        contacts = contacts if contacts is not None else self.store.contacts(actor)
        missing_email = sum(not self._norm_email(item.get("fields", {}).get("email")) for item in contacts)
        missing_phone = sum(not self._norm_phone(item.get("fields", {}).get("phone")) for item in contacts)
        missing_company = sum(not self._norm_text(item.get("fields", {}).get("company")) for item in contacts)
        tags = sorted({tag for item in contacts for tag in item.get("tags", []) if str(tag).strip()}, key=str.casefold)
        groups = sorted({group for item in contacts for group in item.get("groups", []) if str(group).strip()}, key=str.casefold)
        return {
            "total": len(contacts),
            "missing_email": missing_email,
            "missing_phone": missing_phone,
            "missing_company": missing_company,
            "tags": tags,
            "groups": groups,
        }

    def advanced_search(self, actor: str, query: str = "", tag: str = "", group: str = "", company: str = "", incomplete: str = "", *, contacts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        items = contacts if contacts is not None else self.store.contacts(actor)
        needle = query.strip().casefold()
        tag_n = self._norm_text(tag)
        group_n = self._norm_text(group)
        company_n = self._norm_text(company)
        result: list[dict[str, Any]] = []
        for item in items:
            fields = item.get("fields", {})
            if needle:
                searchable = [item.get("contact_id", ""), *fields.values(), *item.get("tags", []), *item.get("groups", [])]
                searchable.extend(f"{row.get('label', '')} {row.get('value', '')}" for row in item.get("addresses", []))
                if needle not in " ".join(str(value) for value in searchable).casefold():
                    continue
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
        # Candidate blocking avoids the previous O(n²) comparison of every
        # visible contact against every other contact. Every signal capable of
        # reaching the score threshold gets its own bucket.
        buckets: dict[tuple[str, str], list[int]] = {}
        for index, contact in enumerate(contacts):
            fields = contact.get("fields", {})
            contact_signals = self._contact_signals(contact)
            signals = {
                (kind, signal)
                for kind, values in contact_signals.items()
                for signal in values
            }
            signals.add(("loose_name", self._norm_loose_text(self._clean_name(fields.get("display_name")))))
            for kind, signal in signals:
                if signal and signal != "\x1f" and (kind != "phone" or len(signal) >= 6):
                    buckets.setdefault((kind, signal), []).append(index)
        pair_indexes: set[tuple[int, int]] = set()
        for indexes in buckets.values():
            for offset, left_index in enumerate(indexes):
                pair_indexes.update((left_index, right_index) for right_index in indexes[offset + 1:])
        candidates: list[DuplicateCandidate] = []
        for left_index, right_index in pair_indexes:
            left, right = contacts[left_index], contacts[right_index]
            score, reasons, trivial = self._duplicate_score(left, right)
            if score < minimum_score:
                continue
            if self._contact_completeness(right) > self._contact_completeness(left):
                left, right = right, left
            differences = self.merge_preview(left, right)
            confidence = "high" if score >= 90 else "likely" if score >= 70 else "manual"
            candidates.append(DuplicateCandidate(
                left, right, score, tuple(reasons), confidence, trivial,
                tuple(differences),
                sum(row["kind"] == "addition" for row in differences),
                sum(row["kind"] == "conflict" for row in differences),
                sum(self._is_blocking_bulk_conflict(row) for row in differences),
            ))
        candidates.sort(key=lambda item: (-item.score, not item.trivial, self._norm_text(item.left.get("fields", {}).get("display_name"))))
        used_for_bulk: set[str] = set()
        result: list[DuplicateCandidate] = []
        for candidate in candidates:
            left_id = str(candidate.left.get("contact_id", ""))
            right_id = str(candidate.right.get("contact_id", ""))
            eligible = bool(
                candidate.trivial and not candidate.blocking_conflicts
                and left_id not in used_for_bulk and right_id not in used_for_bulk
                and len(used_for_bulk) // 2 < self.BULK_MERGE_LIMIT
            )
            if eligible:
                used_for_bulk.update((left_id, right_id))
            result.append(replace(candidate, bulk_eligible=eligible))
        return result

    def merge_search(self, actor: str, query: str = "", match: str = "any") -> list[dict[str, Any]]:
        """Return editable contacts plus shared-field indicators for manual merging."""
        match = match if match in {"any", "name", "email", "phone", "company", "address"} else "any"
        contacts = [
            contact for contact in self.advanced_search(actor, query=query)
            if self.store.can_manage_contact(contact, actor)
        ][:500]
        signals = [self._contact_signals(contact) for contact in contacts]
        counts: dict[tuple[str, str], int] = {}
        for contact_signals in signals:
            for kind, values in contact_signals.items():
                for value in values:
                    counts[(kind, value)] = counts.get((kind, value), 0) + 1
        rows: list[dict[str, Any]] = []
        labels = {"name": "Name", "email": "E-Mail", "phone": "Telefon", "company": "Firma", "address": "Adresse"}
        for contact, contact_signals in zip(contacts, signals):
            indicators = [
                labels[kind]
                for kind in ("email", "phone", "name", "company", "address")
                if any(counts.get((kind, value), 0) > 1 for value in contact_signals[kind])
            ]
            if match != "any" and labels[match] not in indicators:
                continue
            rows.append({"contact": contact, "match_indicators": indicators})
        rows.sort(key=lambda row: self._norm_text(row["contact"].get("fields", {}).get("display_name")))
        return rows

    @staticmethod
    def _contact_completeness(contact: dict[str, Any]) -> tuple[int, int, int]:
        """Prefer the richer record as the default merge target."""
        fields = contact.get("fields", {})
        filled = sum(bool(str(value).strip()) for value in fields.values())
        related = len(contact.get("addresses", [])) + len(contact.get("tags", [])) + len(contact.get("groups", []))
        history = len(contact.get("changes", []))
        return filled, related, history

    @staticmethod
    def _is_blocking_bulk_conflict(difference: dict[str, str]) -> bool:
        # A different display name is expected for many otherwise identical
        # imports and the selected target name remains visible and unchanged.
        return difference.get("kind") == "conflict" and difference.get("field") != "display_name"

    def merge_preview(self, target: dict[str, Any], source: dict[str, Any]) -> list[dict[str, str]]:
        """Describe exactly what a source would add to or conflict with in a target."""
        result: list[dict[str, str]] = []
        target_fields, source_fields = target.get("fields", {}), source.get("fields", {})
        for field in sorted(set(target_fields) | set(source_fields)):
            left, right = self._collapse(target_fields.get(field)), self._collapse(source_fields.get(field))
            if not right or self._norm_text(left) == self._norm_text(right):
                continue
            result.append({"field": field, "left": left, "right": right, "kind": "conflict" if left else "addition"})
        target_addresses = {self._norm_address(row.get("value")) for row in target.get("addresses", [])}
        for row in source.get("addresses", []):
            address = self._collapse(row.get("value"))
            if address and self._norm_address(address) not in target_addresses:
                result.append({"field": "address", "left": "", "right": address, "kind": "addition"})
        for field in ("tags", "groups"):
            existing = {self._norm_text(value) for value in target.get(field, [])}
            additions = [str(value) for value in source.get(field, []) if self._norm_text(value) not in existing]
            if additions:
                result.append({"field": field, "left": "", "right": ", ".join(additions), "kind": "addition"})
        return result

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

        emails_l = {self._norm_email(value) for value in self._property_values(left, "email", "EMAIL") if self._norm_email(value)}
        emails_r = {self._norm_email(value) for value in self._property_values(right, "email", "EMAIL") if self._norm_email(value)}
        phones_l = {self._norm_phone(value) for value in self._property_values(left, "phone", "TEL") if self._norm_phone(value)}
        phones_r = {self._norm_phone(value) for value in self._property_values(right, "phone", "TEL") if self._norm_phone(value)}
        name_l, name_r = self._norm_text(self._clean_name(lf.get("display_name"))), self._norm_text(self._clean_name(rf.get("display_name")))
        loose_name_l, loose_name_r = self._norm_loose_text(self._clean_name(lf.get("display_name"))), self._norm_loose_text(self._clean_name(rf.get("display_name")))
        company_l, company_r = self._norm_text(lf.get("company")), self._norm_text(rf.get("company"))

        if emails_l.intersection(emails_r):
            score += 75; reasons.append("gleiche E-Mail"); strong_matches += 1
        if any(len(value) >= 6 for value in phones_l.intersection(phones_r)):
            score += 65; reasons.append("gleiche Telefonnummer"); strong_matches += 1
        equal_nonempty(name_l, name_r, 40, "gleicher Anzeigename")
        if loose_name_l and loose_name_l == loose_name_r and name_l != name_r:
            score += 30; reasons.append("Name nur durch Leerzeichen/Zeichen verschieden")
        equal_nonempty(company_l, company_r, 10, "gleiche Firma")

        left_name_parts = (self._norm_text(self._clean_name(lf.get("first_name"))), self._norm_text(self._clean_name(lf.get("last_name"))))
        right_name_parts = (self._norm_text(self._clean_name(rf.get("first_name"))), self._norm_text(self._clean_name(rf.get("last_name"))))
        if any(left_name_parts) and left_name_parts == right_name_parts and name_l != name_r:
            score += 30; reasons.append("gleicher Vor-/Nachname")

        left_addresses = {self._norm_address(row.get("value")) for row in left.get("addresses", []) if self._norm_address(row.get("value"))}
        right_addresses = {self._norm_address(row.get("value")) for row in right.get("addresses", []) if self._norm_address(row.get("value"))}
        if left_addresses and right_addresses and left_addresses.intersection(right_addresses):
            score += 45; reasons.append("gleiche Adresse normalisiert"); strong_matches += 1

        if emails_l and emails_r and not emails_l.intersection(emails_r):
            conflicts += 1
        if phones_l and phones_r and not phones_l.intersection(phones_r):
            conflicts += 1
        birthday_l, birthday_r = self._norm_text(lf.get("birthday")), self._norm_text(rf.get("birthday"))
        if birthday_l and birthday_r and birthday_l != birthday_r:
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
        return self._snapshots_locked([(contact, reason)], actor)[0]

    def _snapshots_locked(self, contacts: list[tuple[dict[str, Any], str]], actor: str) -> list[dict[str, Any]]:
        """Persist several contact snapshots with one bounded file write."""
        payload = self._read_snapshots()
        snapshots = [
            {"snapshot_id": str(uuid.uuid4()), "contact_id": contact.get("contact_id", ""), "created_at": utc_now(), "created_by": actor, "reason": reason, "contact": copy.deepcopy(contact)}
            for contact, reason in contacts
        ]
        payload["snapshots"] = (payload.get("snapshots", []) + snapshots)[-self.SNAPSHOT_LIMIT:]
        atomic_json_write(self.snapshots_path, payload)
        return snapshots

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
            merged = self._merge_pair_locked(target, source, actor)
            payload["contacts"] = [item for item in payload.get("contacts", []) if item.get("contact_id") not in {target_id, source_id}] + [merged]
            atomic_json_write(self.store.contacts_path, payload)
            self.store.history.record("contacts_merged", actor, "contacts", target_id, {"target_id": target_id, "source_id": source_id, "result": merged})
            return merged

    def merge_many(self, contact_ids: list[str], actor: str, target_id: str = "") -> dict[str, Any]:
        """Atomically merge two or more explicitly selected contacts into one."""
        unique_ids = list(dict.fromkeys(str(value).strip() for value in contact_ids if str(value).strip()))
        if len(unique_ids) < 2:
            raise ValueError("at least two contacts are required")
        if len(unique_ids) > self.MANUAL_MERGE_LIMIT:
            raise ValueError(f"at most {self.MANUAL_MERGE_LIMIT} contacts can be merged at once")
        principal = self.store._principal(actor)
        with exclusive_file_lock(self.store.control / ".contacts-write.lock"):
            payload = self.store._read(self.store.contacts_path, {"contacts": []})
            by_id = {str(item.get("contact_id", "")): item for item in payload.get("contacts", [])}
            contacts = [by_id.get(contact_id) for contact_id in unique_ids]
            if any(contact is None for contact in contacts):
                raise ValueError("one or more selected contacts no longer exist")
            selected = [contact for contact in contacts if contact is not None]
            if any(not self.store._can_manage(contact, principal) for contact in selected):
                raise ValueError("all selected contacts must be editable")
            owners = {
                contact.get("owner") or self.store._principal(str(contact.get("created_by", "")))
                for contact in selected
            }
            if len(owners) != 1:
                raise ValueError("contacts with different owners cannot be merged")
            target = by_id.get(target_id) if target_id in unique_ids else max(selected, key=self._contact_completeness)
            sources = [contact for contact in selected if contact is not target]
            self._snapshots_locked(
                [(contact, "merge_target" if contact is target else "merge_source") for contact in selected],
                actor,
            )
            merged = copy.deepcopy(target)
            for source in sources:
                merged = self._merge_pair_locked(merged, source, actor, snapshot=False)
            payload["contacts"] = [
                item for item in payload.get("contacts", [])
                if str(item.get("contact_id", "")) not in set(unique_ids)
            ] + [merged]
            atomic_json_write(self.store.contacts_path, payload)
            self.store.history.record(
                "contacts_multi_merged", actor, "contacts", str(merged.get("contact_id", "")),
                {"contact_ids": unique_ids, "target_id": merged.get("contact_id", ""), "result": merged},
            )
            return merged

    def bulk_merge(self, pairs: list[tuple[str, str]], actor: str) -> list[dict[str, Any]]:
        """Merge independent, unambiguous duplicate pairs in one atomic write.

        The score and preview are recalculated from the locked current data.
        Bulk mode deliberately rejects conflicting or low-confidence pairs;
        those remain available through the explicit single-contact merge.
        """
        if not pairs:
            raise ValueError("no duplicate pairs selected")
        if len(pairs) > self.BULK_MERGE_LIMIT:
            raise ValueError(f"at most {self.BULK_MERGE_LIMIT} duplicate pairs can be merged at once")
        normalized_pairs = [(str(target).strip(), str(source).strip()) for target, source in pairs]
        used_ids: set[str] = set()
        for target_id, source_id in normalized_pairs:
            if not target_id or not source_id or target_id == source_id:
                raise ValueError("every selection requires two different contacts")
            if target_id in used_ids or source_id in used_ids:
                raise ValueError("a contact may only occur in one selected duplicate pair")
            used_ids.update((target_id, source_id))

        principal = self.store._principal(actor)
        merged_rows: list[dict[str, Any]] = []
        audit_rows: list[dict[str, Any]] = []
        with exclusive_file_lock(self.store.control / ".contacts-write.lock"):
            payload = self.store._read(self.store.contacts_path, {"contacts": []})
            by_id = {str(item.get("contact_id", "")): item for item in payload.get("contacts", [])}

            # Validate the complete batch before creating snapshots or changing
            # contacts. A rejected pair therefore cannot leave a partial merge.
            for target_id, source_id in normalized_pairs:
                target, source = by_id.get(target_id), by_id.get(source_id)
                if target is None or source is None:
                    raise ValueError("one or more selected contacts no longer exist")
                if not self.store._can_manage(target, principal) or not self.store._can_manage(source, principal):
                    raise ValueError("all selected contacts must be editable")
                target_owner = target.get("owner") or self.store._principal(str(target.get("created_by", "")))
                source_owner = source.get("owner") or self.store._principal(str(source.get("created_by", "")))
                if target_owner != source_owner:
                    raise ValueError("contacts with different owners cannot be merged")
                score, _, trivial = self._duplicate_score(target, source)
                preview = self.merge_preview(target, source)
                if score < 70 or not trivial or any(self._is_blocking_bulk_conflict(row) for row in preview):
                    raise ValueError("bulk merge only accepts high-confidence pairs without conflicting values")

            self._snapshots_locked(
                [
                    (by_id[contact_id], reason)
                    for target_id, source_id in normalized_pairs
                    for contact_id, reason in ((target_id, "merge_target"), (source_id, "merge_source"))
                ],
                actor,
            )
            for target_id, source_id in normalized_pairs:
                target, source = by_id[target_id], by_id[source_id]
                merged = self._merge_pair_locked(target, source, actor, snapshot=False)
                merged_rows.append(merged)
                audit_rows.append({"target_id": target_id, "source_id": source_id})

            payload["contacts"] = [item for item in payload.get("contacts", []) if str(item.get("contact_id", "")) not in used_ids] + merged_rows
            atomic_json_write(self.store.contacts_path, payload)
            self.store.history.record(
                "contacts_bulk_merged", actor, "contacts", "bulk",
                {"pairs": audit_rows, "count": len(merged_rows)},
            )
        return merged_rows

    def _merge_pair_locked(self, target: dict[str, Any], source: dict[str, Any], actor: str, *, snapshot: bool = True) -> dict[str, Any]:
        """Build one merged contact while the contacts write lock is held."""
        source_id = str(source.get("contact_id", ""))
        if snapshot:
            self._snapshots_locked([(target, "merge_target"), (source, "merge_source")], actor)
        target_n, source_n = self.normalize_contact(target), self.normalize_contact(source)
        merged = copy.deepcopy(target_n)
        merged_fields = self._merge_fields(target_n, source_n)
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
        merged.setdefault("merged_from", []).append({
            "contact_id": source_id,
            "at": merged["updated_at"],
            "actor": actor,
            "source": copy.deepcopy(source_n.get("source", {})),
        })
        return merged

    def _merge_fields(self, target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        """Merge scalar fields and preserve distinct multi-value vCard data."""
        target_fields = target.get("fields", {})
        source_fields = source.get("fields", {})
        merged = {
            key: value for key, value in target_fields.items()
            if not str(key).startswith("vcard_") and str(value).strip()
        }
        for key, value in source_fields.items():
            if not str(key).startswith("vcard_") and str(value).strip() and not str(merged.get(key, "")).strip():
                merged[key] = value

        raw_lines: list[str] = []
        raw_seen: set[str] = set()
        multi_seen: dict[str, set[str]] = {"EMAIL": set(), "TEL": set()}
        multi_specs = (("email", "EMAIL", self._norm_email), ("phone", "TEL", self._norm_phone))
        for contact in (target, source):
            fields = contact.get("fields", {})
            for field, property_name, normalizer in multi_specs:
                value = self._collapse(fields.get(field))
                normalized = normalizer(value)
                if not normalized or normalized in multi_seen[property_name]:
                    continue
                multi_seen[property_name].add(normalized)
                if normalizer(self._collapse(merged.get(field))) != normalized:
                    raw_lines.append(f"{property_name}:{value}")
            for key, raw in fields.items():
                if not str(key).startswith("vcard_"):
                    continue
                safe = ContactStore._safe_raw_vcard_line(str(raw))
                if not safe:
                    continue
                property_name = ContactStore._vcard_property_name(safe)
                if property_name in multi_seen:
                    normalizer = self._norm_email if property_name == "EMAIL" else self._norm_phone
                    normalized = normalizer(self._raw_property_value(safe))
                    if not normalized or normalized in multi_seen[property_name]:
                        continue
                    multi_seen[property_name].add(normalized)
                signature = self._norm_text(safe)
                if signature not in raw_seen:
                    raw_seen.add(signature)
                    raw_lines.append(safe)
        for index, raw in enumerate(raw_lines):
            merged[f"vcard_merged_{index:03d}_{ContactStore._vcard_property_name(raw).casefold()}"] = raw

        first_name = self._clean_name(merged.get("first_name"))
        last_name = self._clean_name(merged.get("last_name"))
        if first_name:
            merged["first_name"] = first_name
        else:
            merged.pop("first_name", None)
        if last_name:
            merged["last_name"] = last_name
        else:
            merged.pop("last_name", None)
        generated_name = " ".join(part for part in (first_name, last_name) if part).strip()
        clean_display = next((
            value for value in (
                self._clean_name(target_fields.get("display_name")),
                self._clean_name(source_fields.get("display_name")),
            ) if value
        ), "Kontakt")
        merged["display_name"] = generated_name or clean_display
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
