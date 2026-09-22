"""Local contact capture helpers for business-card photos and QR payloads."""
from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote

from PIL import Image, UnidentifiedImageError

from .contact_store import ContactStore
from .object_vision import analyze_ocr


MAX_CONTACT_IMAGE_BYTES = 12 * 1024 * 1024
MAX_CONTACT_IMAGE_PIXELS = 50_000_000
MAX_QR_PAYLOAD_CHARS = 64_000
ALLOWED_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}

_EMAIL_LOCAL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-")
_EMAIL_DOMAIN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
_URL_RE = re.compile(r"\b((?:https?://|www\.)[^\s<>()]+)", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s()./-]{5,}\d)(?!\w)")
_COMPANY_HINTS = (
    "gmbh", " ag", "ug ", " ug", "kg", "ohg", "gbr", "e.k.", "llc", "ltd",
    "inc.", "corp", "company", "consulting", "solutions", "engineering",
    "kanzlei", "praxis", "studio", "agentur", "systems", "service",
)
_NAME_PREFIXES = {"dr.", "dr", "prof.", "prof", "dipl.-ing.", "dipl.-ing", "ing."}
_FIELD_NAMES = {
    "first_name", "last_name", "display_name", "email", "phone", "birthday",
    "company", "department", "title", "role", "website", "note",
}


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()


def _email_around(value: str, marker: int) -> str:
    """Extract one email-looking token around an @ without regex backtracking."""
    text = str(value or "")
    if marker < 1 or marker >= len(text) - 1 or text[marker] != "@":
        return ""
    left = marker - 1
    while left >= 0 and text[left] in _EMAIL_LOCAL_CHARS:
        left -= 1
    right = marker + 1
    while right < len(text) and text[right] in _EMAIL_DOMAIN_CHARS:
        right += 1
    candidate = text[left + 1:right].strip(".,;:")
    if len(candidate) > 320 or candidate.count("@") != 1:
        return ""
    local, domain = candidate.rsplit("@", 1)
    if not local or len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
        return ""
    if not domain or len(domain) > 253 or domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return ""
    labels = domain.split(".")
    if len(labels) < 2 or len(labels[-1]) < 2:
        return ""
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for ch in label)
        for label in labels
    ):
        return ""
    return candidate


def _find_email(value: str) -> str:
    text = str(value or "")
    for index, character in enumerate(text):
        if character == "@":
            candidate = _email_around(text, index)
            if candidate:
                return candidate
    return ""


def _is_email(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and _find_email(text) == text


def _clean_phone(value: str) -> str:
    value = _clean_text(value).strip(".,;:")
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 18:
        return ""
    if len(digits) == 5 and not value.startswith("+"):
        return ""
    return value


def _looks_like_company(line: str) -> bool:
    lower = f" {line.casefold()} "
    return any(hint in lower for hint in _COMPANY_HINTS)


def _looks_like_name(line: str) -> bool:
    if not line or "@" in line or _URL_RE.search(line):
        return False
    if _looks_like_company(line):
        return False
    if sum(character.isdigit() for character in line) > 1:
        return False
    words = [part for part in re.split(r"\s+", line) if part]
    if not 2 <= len(words) <= 6:
        return False
    letters = sum(character.isalpha() for character in line)
    return letters >= max(4, len(line) // 2)


def _split_name(display_name: str) -> tuple[str, str]:
    parts = [part for part in display_name.split() if part]
    while parts and parts[0].casefold() in _NAME_PREFIXES:
        parts.pop(0)
    if len(parts) < 2:
        return ("", parts[0] if parts else "")
    return (" ".join(parts[:-1]), parts[-1])


def extract_contact_fields(text: str) -> dict[str, str]:
    """Extract conservative contact suggestions from OCR or plain contact text."""
    raw_lines = [line.strip(" \t|") for line in str(text or "").replace("\r", "\n").split("\n")]
    lines = [line for line in raw_lines if line]
    fields: dict[str, str] = {}

    email = _find_email(text or "")
    if email:
        fields["email"] = email

    for candidate in _PHONE_RE.findall(text or ""):
        phone = _clean_phone(candidate)
        if phone:
            fields["phone"] = phone
            break

    url_match = _URL_RE.search(text or "")
    if url_match:
        fields["website"] = url_match.group(1).rstrip(".,;:)")

    company_line = next((line for line in lines[:10] if _looks_like_company(line)), "")
    if company_line:
        fields["company"] = _clean_text(company_line)

    contact_lines = {
        value.casefold()
        for value in (fields.get("email", ""), fields.get("phone", ""), fields.get("website", ""), fields.get("company", ""))
        if value
    }
    name_line = next(
        (
            line for line in lines[:10]
            if line.casefold() not in contact_lines
            and not _find_email(line)
            and not _URL_RE.search(line)
            and not _clean_phone(line)
            and _looks_like_name(line)
        ),
        "",
    )
    if name_line:
        display_name = _clean_text(name_line)
        first_name, last_name = _split_name(display_name)
        fields["display_name"] = display_name
        if first_name:
            fields["first_name"] = first_name
        if last_name:
            fields["last_name"] = last_name
    elif company_line:
        fields["display_name"] = fields["company"]

    return fields


def _split_escaped(value: str, separator: str = ";") -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == separator:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return parts


def _mecard_fields(payload: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    body = payload.strip()[7:]
    for item in _split_escaped(body):
        key, separator, value = item.partition(":")
        if not separator:
            continue
        key = key.strip().upper()
        value = _clean_text(value)
        if not value:
            continue
        if key == "N":
            name_parts = [part.strip() for part in value.split(",")]
            if len(name_parts) >= 2:
                fields["last_name"] = name_parts[0]
                fields["first_name"] = name_parts[1]
                fields["display_name"] = " ".join(part for part in (name_parts[1], name_parts[0]) if part)
            else:
                fields["display_name"] = value
                first_name, last_name = _split_name(value)
                if first_name:
                    fields["first_name"] = first_name
                if last_name:
                    fields["last_name"] = last_name
        elif key == "FN":
            fields["display_name"] = value
        elif key == "TEL" and "phone" not in fields:
            phone = _clean_phone(value)
            if phone:
                fields["phone"] = phone
        elif key == "EMAIL" and "email" not in fields:
            fields["email"] = value
        elif key == "ORG":
            fields["company"] = value
        elif key == "URL":
            fields["website"] = value
        elif key == "BDAY":
            fields["birthday"] = value
    if not fields.get("display_name") and fields.get("company"):
        fields["display_name"] = fields["company"]
    return fields


def parse_qr_payload(payload: str) -> dict[str, str]:
    """Parse common QR contact encodings without writing any contact data."""
    value = str(payload or "").strip()
    if not value:
        raise ValueError("QR-Code enthält keine Daten")
    if len(value) > MAX_QR_PAYLOAD_CHARS:
        raise ValueError("QR-Code enthält zu viele Daten")

    upper = value.upper()
    if upper.startswith("BEGIN:VCARD"):
        parsed, _contact_id, _metadata = ContactStore._vcard_values(value)
        fields = {key: _clean_text(raw) for key, raw in parsed.items() if key in _FIELD_NAMES and _clean_text(raw)}
    elif upper.startswith("MECARD:"):
        fields = _mecard_fields(value)
    elif value.casefold().startswith("mailto:"):
        address = unquote(value[7:].split("?", 1)[0]).strip()
        fields = {"email": address} if _is_email(address) else {}
    elif value.casefold().startswith("tel:"):
        phone = _clean_phone(unquote(value[4:]))
        fields = {"phone": phone} if phone else {}
    else:
        fields = extract_contact_fields(value)

    if not fields:
        raise ValueError("Im QR-Code wurden keine Kontaktdaten erkannt")
    return fields


def analyze_contact_image(data: bytes, filename: str = "") -> dict[str, object]:
    """Validate an image, run local OCR and return a non-persistent contact preview."""
    if not data:
        raise ValueError("Bitte ein Foto auswählen")
    if len(data) > MAX_CONTACT_IMAGE_BYTES:
        raise ValueError("Das Kontaktfoto ist größer als 12 MiB")

    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_CONTACT_IMAGE_PIXELS:
                raise ValueError("Das Kontaktfoto hat eine unzulässige Bildgröße")
            image.verify()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Die Datei ist kein unterstütztes Bild") from exc

    suffix = ALLOWED_IMAGE_FORMATS.get(image_format)
    if suffix is None:
        raise ValueError("Unterstützt werden JPEG, PNG und WebP")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="simpleoffice-contact-", suffix=suffix, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        result = analyze_ocr(temporary_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    text = str(result.get("text", "") or "").strip()
    if result.get("status") != "completed" or not text:
        detail = _clean_text(str(result.get("error", "") or ""))
        if detail:
            raise ValueError(f"Keine lokale Texterkennung verfügbar: {detail}")
        raise ValueError("Auf dem Foto wurde kein Text erkannt")

    fields = extract_contact_fields(text)
    warning = "" if fields else "Text erkannt, aber keine eindeutigen Kontaktfelder gefunden."
    return {
        "fields": fields,
        "text": text[:4000],
        "warning": warning,
        "ocr": {
            "engine": str(result.get("engine", "")),
            "confidence": result.get("confidence"),
            "characters": int(result.get("characters", len(text)) or len(text)),
            "filename": Path(filename or "kontaktfoto").name[:180],
        },
    }