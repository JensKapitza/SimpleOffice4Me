"""Per-user reading progress and annotations stored beside document access metadata."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .document_store import DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock

MAX_ANNOTATIONS = 500
MAX_ANNOTATION_TEXT = 10_000
MAX_LOCATOR_TEXT = 2_000


def _actor_key(actor: str) -> str:
    return hashlib.sha256(actor.encode("utf-8")).hexdigest()


def _version(document: dict[str, Any]) -> str:
    digest = str(document.get("sha256") or document.get("content_sha256") or "").strip().casefold()
    number = int(document.get("version_number", 1) or 1)
    return f"sha256:{digest}:v{number}"


def _format(document: dict[str, Any]) -> str:
    suffix = Path(str(document.get("last_path") or "")).suffix.casefold()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".epub":
        return "epub"
    raise ValueError("document is not a supported reader format")


def _locator(value: Any, kind: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("reader locator must be an object")
    if kind == "pdf":
        page = value.get("page")
        try:
            page = int(page)
        except (TypeError, ValueError) as exc:
            raise ValueError("PDF locator requires a page number") from exc
        if page < 1 or page > 100_000:
            raise ValueError("PDF page is outside the supported range")
        return {"page": page}
    chapter = str(value.get("chapter", "")).strip()
    anchor = str(value.get("anchor", "")).strip()
    cfi = str(value.get("cfi", "")).strip()
    try:
        chapter_index = int(value.get("chapter_index", 0) or 0)
        offset = int(value.get("offset", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("EPUB locator offset is invalid") from exc
    if not chapter or len(chapter) > MAX_LOCATOR_TEXT or len(anchor) > MAX_LOCATOR_TEXT or len(cfi) > MAX_LOCATOR_TEXT:
        raise ValueError("EPUB locator is invalid")
    if chapter_index < 0 or chapter_index > 100_000 or offset < 0 or offset > 10_000_000:
        raise ValueError("EPUB locator offset is invalid")
    return {
        "chapter": chapter,
        "chapter_index": chapter_index,
        "anchor": anchor,
        "cfi": cfi,
        "offset": offset,
    }


class ReadingStateStore:
    """Reuse DocumentStore's access sidecar, preserving original file authority."""

    def __init__(self, root: str | Path):
        self.documents = DocumentStore(root)

    def _path(self, document_id: str) -> Path:
        document = self.documents.get_document(document_id)
        return self.documents.document_access / f"{document['document_id']}.json"

    @staticmethod
    def _public_state(document: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
        current = _version(document)
        saved = str(value.get("document_version", ""))
        stale = bool(saved and saved != current)
        return {
            "format": _format(document),
            "document_version": current,
            "saved_version": saved,
            "stale": stale,
            # A position from another content version is evidence only. It must
            # never be restored automatically against the current bytes.
            "locator": {} if stale else dict(value.get("locator") or {}),
            "saved_locator": dict(value.get("locator") or {}),
            "percent": int(value.get("percent", 0) or 0),
            "updated_at": str(value.get("updated_at", "")),
        }

    def state(self, document_id: str, actor: str) -> dict[str, Any]:
        document = self.documents.get_document(document_id)
        path = self._path(document_id)
        data = self.documents._read_json(path, {})
        readers = data.get("reader_state", {})
        value = readers.get(_actor_key(actor), {}) if isinstance(readers, dict) else {}
        if not isinstance(value, dict):
            value = {}
        return self._public_state(document, value)

    def save_progress(
        self,
        document_id: str,
        actor: str,
        locator: Any,
        percent: int | float,
        expected_version: str,
    ) -> dict[str, Any]:
        document = self.documents.get_document(document_id)
        kind = _format(document)
        current = _version(document)
        if str(expected_version or "") != current:
            raise ValueError("document version changed")
        clean_locator = _locator(locator, kind)
        try:
            clean_percent = int(percent)
        except (TypeError, ValueError) as exc:
            raise ValueError("reading percent is invalid") from exc
        clean_percent = min(100, max(0, clean_percent))
        path = self._path(document_id)
        with exclusive_file_lock(path.with_suffix(".lock")):
            data = self.documents._read_json(path, {})
            readers = data.setdefault("reader_state", {})
            readers[_actor_key(actor)] = {
                "actor": actor,
                "document_version": current,
                "locator": clean_locator,
                "percent": clean_percent,
                "updated_at": utc_now(),
            }
            atomic_json_write(path, data)
        self.documents.history.record(
            "document_reading_progress", actor, "documents", document["document_id"],
            {"document_version": current, "format": kind, "percent": clean_percent},
        )
        return self.state(document_id, actor)

    def annotations(self, document_id: str, actor: str) -> list[dict[str, Any]]:
        document = self.documents.get_document(document_id)
        path = self._path(document_id)
        data = self.documents._read_json(path, {})
        all_annotations = data.get("reader_annotations", {})
        rows = all_annotations.get(_actor_key(actor), []) if isinstance(all_annotations, dict) else []
        if not isinstance(rows, list):
            return []
        current = _version(document)
        result = []
        for raw in rows[-MAX_ANNOTATIONS:]:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row["stale"] = str(row.get("document_version", "")) != current
            result.append(row)
        return result

    def add_annotation(
        self,
        document_id: str,
        actor: str,
        locator: Any,
        text: str,
        expected_version: str,
        *,
        kind: str = "note",
        quote: str = "",
    ) -> dict[str, Any]:
        document = self.documents.get_document(document_id)
        kind = _format(document)
        current = _version(document)
        if str(expected_version or "") != current:
            raise ValueError("document version changed")
        clean = str(text or "").strip()
        if not clean or len(clean) > MAX_ANNOTATION_TEXT:
            raise ValueError("annotation text is empty or too long")
        annotation_kind = str(kind or "note").strip().casefold()
        if annotation_kind not in {"note", "question", "summary"}:
            raise ValueError("annotation kind is invalid")
        clean_quote = str(quote or "").strip()
        if len(clean_quote) > 4_000:
            raise ValueError("annotation quote is too long")
        row = {
            "annotation_id": uuid.uuid4().hex,
            "document_version": current,
            "locator": _locator(locator, kind),
            "kind": annotation_kind,
            "quote": clean_quote,
            "text": clean,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        path = self._path(document_id)
        with exclusive_file_lock(path.with_suffix(".lock")):
            data = self.documents._read_json(path, {})
            all_annotations = data.setdefault("reader_annotations", {})
            rows = all_annotations.setdefault(_actor_key(actor), [])
            if not isinstance(rows, list):
                rows = []
                all_annotations[_actor_key(actor)] = rows
            if len(rows) >= MAX_ANNOTATIONS:
                raise ValueError("annotation limit reached")
            rows.append(row)
            atomic_json_write(path, data)
        self.documents.history.record(
            "document_reader_annotation_created", actor, "documents", document["document_id"],
            {"annotation_id": row["annotation_id"], "document_version": current, "format": kind},
        )
        return {**row, "stale": False}

    def search_text(self, document_id: str, actor: str) -> str:
        """Return bounded user-owned annotation text for the bookshelf filter."""
        values: list[str] = []
        for row in self.annotations(document_id, actor):
            values.extend((
                str(row.get("text", "")),
                str(row.get("quote", "")),
                str(row.get("kind", "")),
            ))
        return " ".join(values)[:200_000]

    def delete_annotation(self, document_id: str, actor: str, annotation_id: str) -> None:
        document = self.documents.get_document(document_id)
        target = str(annotation_id or "").strip()
        if not target:
            raise ValueError("annotation id is required")
        path = self._path(document_id)
        removed = False
        with exclusive_file_lock(path.with_suffix(".lock")):
            data = self.documents._read_json(path, {})
            all_annotations = data.get("reader_annotations", {})
            rows = all_annotations.get(_actor_key(actor), []) if isinstance(all_annotations, dict) else []
            if isinstance(rows, list):
                kept = [row for row in rows if not (isinstance(row, dict) and row.get("annotation_id") == target)]
                removed = len(kept) != len(rows)
                if removed:
                    all_annotations[_actor_key(actor)] = kept
                    atomic_json_write(path, data)
        if not removed:
            raise KeyError(target)
        self.documents.history.record(
            "document_reader_annotation_deleted", actor, "documents", document["document_id"],
            {"annotation_id": target},
        )


def reader_version(document: dict[str, Any]) -> str:
    return _version(document)
