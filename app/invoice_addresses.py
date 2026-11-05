"""Free-text invoice address parsing with optional structured fields."""

import re

def parse_free_text_invoice_address(value: object, default_country: str = "DE") -> dict[str, str]:
    """Preserve the user's invoice address literally; structured fields are best-effort."""
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if len(raw) > 1000 or len(lines) < 2:
        raise ValueError("Freitextadresse benötigt mindestens zwei Zeilen (maximal 1000 Zeichen).")
    result = {"id": "", "label": raw, "street": "", "postal": "", "city": "",
              "country": str(default_country or "DE").upper()}
    has_country = len(lines) >= 3 and bool(re.fullmatch(r"[A-Za-z]{2}", lines[-1]))
    postal_city = lines[-2] if has_country else lines[-1]
    parts = postal_city.split(maxsplit=1)
    if has_country:
        result["country"] = lines[-1].upper()
    if len(parts) == 2 and parts[0].rstrip(",").isdigit() and lines[:-2 if has_country else -1]:
        result["street"] = " ".join(lines[:-2] if has_country else lines[:-1])
        result["postal"] = parts[0].rstrip(",")
        result["city"] = parts[1]
    return result
