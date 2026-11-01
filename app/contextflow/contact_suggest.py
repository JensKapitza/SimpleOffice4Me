"""Meta-model contact field suggestions from user text and photos (Bueroengel).

Takes contact text pasted by the user plus up to three business-card photos,
runs local OCR per photo (stays on the machine), and asks a Meta model to
structure the combined sources into concrete contact fields — the on-machine
Meta Ollama model by default, or Muse Spark 1.3 Contributor via the Meta Model API when
explicitly selected. Every suggestion carries a verbatim evidence quote plus its source;
unsupported fields are dropped, never invented. Values that deviate from
their quote (e.g. typo fixes) are explicitly marked as corrected so the UI
can label them. Nothing is stored; applying a suggestion stays an explicit
per-field user click in the contact form, and saving still happens only via
the normal contact save path. The Meta cloud path runs on synthetic demo
roots only: Contributor content may train future Meta models, so real
contact data never reaches it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..object_vision import analyze_ocr
from .doc_tags import complete_text
from .vision import (
    InferenceUnavailable,
    MockBlockedError,
    VisionError,
    select_provider,
)

MAX_PHOTOS = 3
TEXT_LIMIT = 4000
SOURCE_CHARS = 4000
EXCERPT_CHARS = 1000
EVIDENCE_LIMIT = 160

FIELD_LIMITS = {
    "display_name": 120,
    "first_name": 60,
    "last_name": 60,
    "company": 300,
    "email": 254,
    "phone": 100,
    "website": 500,
    "street": 300,
    "postal": 20,
    "city": 100,
    "state": 100,
    "country": 2,
}
FIELD_LABELS = {
    "display_name": "Anzeigename",
    "first_name": "Vorname",
    "last_name": "Nachname",
    "company": "Firma",
    "email": "E-Mail",
    "phone": "Telefon",
    "website": "Webseite",
    "street": "Straße",
    "postal": "PLZ",
    "city": "Ort",
    "state": "Region",
    "country": "Land",
}
ADDRESS_FIELDS = ("street", "postal", "city", "state", "country")

_MIME_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _normalize_quote(text: str) -> str:
    """Normalize a quote for verbatim matching (case/space/quote blind)."""
    folded = " ".join(str(text or "").split()).casefold()
    return folded.strip(" \t\"'„“”‚‘`´")


def _looks_like_email(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 254 or text.count("@") != 1:
        return False
    local, domain = text.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    return all(
        label and len(label) <= 63 and not label.startswith("-") and not label.endswith("-")
        for label in domain.split(".")
    )


def _looks_like_phone(value: str) -> bool:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return 7 <= len(digits) <= 18


def build_contact_prompt(sources: list[dict[str, str]]) -> str:
    """Build the local structuring prompt from labeled user sources."""
    blocks = []
    for source in sources:
        blocks.append(f"[{source['id']}] {source['label']}:\n{source['text']}")
    excerpt = "\n\n".join(blocks)[: SOURCE_CHARS * MAX_PHOTOS + 1200]
    fields = ", ".join(sorted(FIELD_LIMITS))
    return (
        "Du bist der Büroengel. Ordne die folgenden Nutzerquellen (eingefügter "
        "Text und/oder lokale Foto-Texterkennung) Kontaktfeldern zu. Antworte "
        'NUR mit einem JSON-Objekt {"fields": [{"field": "...", "value": "...", '
        '"evidence": "...", "source": "..."}]}. Erlaubte Feldnamen: '
        f"{fields}. "
        "Jeder Eintrag braucht ein kurzes wörtliches Zitat (evidence, max. 160 "
        "Zeichen) aus genau der genannten Quelle, das den Wert belegt; ohne "
        "Beleg kein Feld. Höchstens ein Eintrag je Feld. Namen und Firmen "
        "wörtlich übernehmen; offensichtliche Tippfehler minimal korrigieren "
        "(value darf dann leicht vom Zitat abweichen). E-Mail und Telefon nur "
        "aus den Quellen, niemals raten oder ergänzen. Keine Anschriften aus "
        "Vorwissen: Straßen, PLZ, Orte und Länder nur aus den Quellen. "
        "Unsicheres weglassen — weniger Felder sind in Ordnung.\n\n"
        f"Quellen:\n{excerpt}"
    )


def validate_contact_suggestions(
    raw: object, sources: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], int]:
    """Validate model field output; return (suggestions, dropped_count).

    Malformed payloads raise; valid JSON with zero surviving suggestions
    returns ([], n) so callers answer 200 with an empty list instead of an
    error. Each entry needs a known field, a bounded value, and a verbatim
    evidence quote found in its cited source. Values that deviate from any
    source text are kept but marked ``corrected`` so the UI can label the
    typo fix explicitly. No value is ever invented or silently replaced:
    unsupported entries are dropped and counted.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), list):
        raise VisionError("Kontaktvorschläge müssen ein JSON-Objekt mit 'fields'-Liste sein")
    by_id = {source["id"]: source for source in sources}
    source_keys = {key: _normalize_quote(source["text"]) for key, source in by_id.items()}
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for entry in raw["fields"]:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        field = str(entry.get("field", "") or "").strip()
        value = str(entry.get("value", "") or "").strip()
        evidence = str(entry.get("evidence", "") or "").strip()
        source_id = str(entry.get("source", "") or "").strip()
        if field not in FIELD_LIMITS or field in seen:
            dropped += 1
            continue
        limit = FIELD_LIMITS[field]
        if not value or len(value) > limit:
            dropped += 1
            continue
        if not evidence or len(evidence) > EVIDENCE_LIMIT:
            dropped += 1
            continue
        source = by_id.get(source_id)
        if source is None:
            dropped += 1
            continue
        key = _normalize_quote(evidence)
        if not key or key not in source_keys[source_id]:
            dropped += 1
            continue
        if field == "email" and not _looks_like_email(value):
            dropped += 1
            continue
        if field == "phone" and not _looks_like_phone(value):
            dropped += 1
            continue
        if field == "country" and len(value) != 2:
            dropped += 1
            continue
        seen.add(field)
        value_key = _normalize_quote(value)
        corrected = bool(value_key) and all(
            value_key not in source_key for source_key in source_keys.values()
        )
        suggestions.append(
            {
                "field": field,
                "label": FIELD_LABELS[field],
                "value": value,
                "evidence": evidence,
                "source": source_id,
                "source_label": source["label"],
                "corrected": corrected,
            }
        )
    return suggestions, dropped


def _ocr_photo(data: bytes, mime: str, label: str) -> tuple[str, str]:
    """Run local OCR on validated photo bytes; return (text, engine)."""
    suffix = _MIME_SUFFIXES.get(str(mime or "").strip().lower(), ".jpg")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="simpleoffice-engel-kontakt-", suffix=suffix, delete=False
        ) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        result = analyze_ocr(temporary_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    text = str(result.get("text", "") or "").strip()
    if result.get("status") != "completed" or not text:
        detail = " ".join(str(result.get("error", "") or "").split())
        if detail:
            raise ValueError(f"{label}: keine lokale Texterkennung verfügbar ({detail})")
        raise ValueError(f"{label}: auf dem Foto wurde kein Text erkannt")
    return text[:SOURCE_CHARS], str(result.get("engine", "") or "lokal")


def _require_provider(
    settings: dict[str, Any], consent_meta: bool = False, root: object = None
) -> Any:
    """Select the real Meta provider; refuse mock placeholders.

    Local Ollama is the default. The Meta cloud backend additionally
    needs explicit per-request consent, the server-side consent flag,
    an API key, and a verified synthetic demo root; real data roots
    stay excluded (PermissionError, routed as 403). Only pasted text
    plus local OCR text reach the single text-only Meta call, photo
    bytes never leave the machine.
    """
    choice = str(settings.get("provider", "local") or "local").strip().lower()
    if choice == "local" and not str(settings.get("ollama_model", "") or "").strip():
        raise InferenceUnavailable(
            "Kein freigegebenes Meta-Modell konfiguriert. Bitte ein Ollama-Modell wählen."
        )
    provider = select_provider(settings, consent_meta is True, root)
    if provider.name == "mock":
        raise MockBlockedError(
            "Für Kontaktvorschläge ist kein Meta-Modell verfügbar "
            "(Platzhalter sind hier nicht erlaubt). Bitte Ollama mit "
            "einem freigegebenen Meta-Modell einrichten."
        )
    return provider


def suggest_contact_fields(
    *,
    text: str,
    photos: list[dict[str, Any]],
    settings: dict[str, Any],
    consent_meta: bool = False,
    root: object = None,
) -> dict[str, Any]:
    """Suggest contact fields from user text and photos; stores nothing.

    Photos are OCR-read locally (bytes stay on the machine and are never
    sent to any model); exactly one Meta text call structures the combined
    sources. Local Ollama answers by default; the Meta Model API path
    additionally needs explicit per-request consent, the server-side
    consent flag, an API key, and a verified synthetic demo root
    (real roots are refused), otherwise it is refused. Mock answers
    are refused with 503.
    """
    if len(photos) > MAX_PHOTOS:
        raise ValueError(f"Bitte höchstens {MAX_PHOTOS} Fotos auswählen.")
    warnings: list[str] = []
    clean_text = str(text or "").strip()
    if len(clean_text) > TEXT_LIMIT:
        clean_text = clean_text[:TEXT_LIMIT]
        warnings.append(f"Der eingefügte Text wurde auf {TEXT_LIMIT} Zeichen gekürzt.")
    sources: list[dict[str, str]] = []
    if clean_text:
        sources.append({"id": "text", "label": "Eingefügter Text", "text": clean_text})
    engines: dict[str, str] = {}
    for index, photo in enumerate(photos, start=1):
        label = f"Foto {index}"
        source_id = f"foto-{index}"
        try:
            ocr_text, engine = _ocr_photo(
                photo["data"], str(photo.get("mime", "") or ""), label
            )
        except (ValueError, OSError, RuntimeError) as exc:
            warnings.append(str(exc))
            continue
        engines[source_id] = engine
        sources.append({"id": source_id, "label": label, "text": ocr_text})
    if not sources:
        raise ValueError(
            "Bitte Kontakttext einfügen oder mindestens ein lesbares Foto auswählen."
        )
    provider = _require_provider(settings, consent_meta, root)
    prompt = build_contact_prompt(sources)
    if provider.name == "meta":
        raw = provider.complete_text(prompt)
    else:
        raw = complete_text(provider, prompt)
    suggestions, dropped = validate_contact_suggestions(raw, sources)
    if dropped:
        warnings.append(
            f"{dropped} unbelegte Angabe(n) wurden verworfen — nichts erfunden."
        )
    if not suggestions:
        warnings.append("Keine belegbaren Kontaktfelder gefunden.")
    return {
        "suggestions": suggestions,
        "sources": [
            {
                "id": source["id"],
                "label": source["label"],
                "chars": len(source["text"]),
                "excerpt": source["text"][:EXCERPT_CHARS],
                **({"ocr_engine": engines[source["id"]]} if source["id"] in engines else {}),
            }
            for source in sources
        ],
        "provider_label": provider.label,
        "model": getattr(provider, "resolved_model", None) or provider.model,
        "warnings": warnings,
    }
