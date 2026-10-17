"""Bank access capabilities for safe transaction retrieval.

Public web discovery is informational only. Authenticated online-banking pages
must not be scraped for PIN/TAN, cookies, or transactions.
"""
from __future__ import annotations

from typing import Any

BANK_ACCESS = {
    "santander": {
        "name": "Santander Deutschland",
        "methods": ("psd2", "statement_import"),
        "psd2_information_url": "https://unternehmen.openbank.de/unternehmen/kooperationen/partner/api-market/",
        "notes": "Kontoumsaetze per PSD2-AIS nur mit autorisiertem Zugang und Einwilligung; kein Login-Scraping.",
    },
}


def discover_bank_access(institution: str) -> dict[str, Any]:
    """Return only documented capabilities; never infer an authenticated login URL."""
    normalized = str(institution or "").strip().casefold()
    if "santander" in normalized:
        return dict(BANK_ACCESS["santander"])
    return {
        "name": str(institution or "").strip(),
        "methods": ("fints", "statement_import"),
        "psd2_information_url": "",
        "notes": "FinTS nur nutzen, falls die Bank es tatsaechlich unterstuetzt.",
    }
