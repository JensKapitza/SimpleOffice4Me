"""Present stored federation evidence without inferring a stronger result."""

from __future__ import annotations

import re

_HTTP_STATUS = re.compile(r"[1-5][0-9]{2}")
STALE_AFTER_SECONDS = 300


def _pair(language: str, de: str, en: str) -> str:
    return en if language == "en" else de


_HTTP_HINTS = {
    200: (
        "Basisadresse, Peer-ID und Paarungsdaten abgleichen; die signierte Antwort wurde nicht bestätigt.",
        "Check the base address, peer ID and pairing details; the signed response was not confirmed.",
    ),
    401: (
        "Paarungsdaten dieser Richtung und die Weitergabe der Authentifizierung am Reverse-Proxy prüfen.",
        "Check pairing for this direction and HTTP authentication forwarding at the reverse proxy.",
    ),
    403: (
        "Auf der Gegenstelle Zugriffsregeln und Reverse-Proxy prüfen; die Antwort bestätigt keine Peer-Identität.",
        "Check access rules and the reverse proxy on the other instance; the response does not verify the peer.",
    ),
    404: (
        "Gespeicherte Basisadresse und Weiterleitung zum Federation-Endpunkt prüfen.",
        "Check the saved base address and forwarding to the federation endpoint.",
    ),
    429: (
        "Warte bis zur nächsten erlaubten Prüfung; kontrolliere bei Wiederholung die Limits der Gegenstelle oder des Proxys.",
        "Wait before retrying; if repeated, check limits on the other instance or its proxy.",
    ),
}
_GENERAL_HTTP_HINT = (
    "Basisadresse, Federation-Endpunkt und Reverse-Proxy auf der Gegenstelle prüfen.",
    "Check the base address, federation endpoint and reverse proxy on the other instance.",
)
_NO_HTTP_HINTS = {
    "configuration": (
        "Die lokale Peer-Adresse und Paarung prüfen.", "Check the local peer address and pairing.",
    ),
    "target_changed": (
        "Peer-Adresse wurde während der Prüfung geändert; erneut prüfen.",
        "Peer address changed during the check; run it again.",
    ),
    "policy": (
        "Die gespeicherte Zieladresse und die bestehende Netzwerk-Sicherheitsrichtlinie prüfen.",
        "Check the saved destination and the existing network security policy.",
    ),
    "rate_limited": (
        "Die lokale Prüfung wurde gedrosselt; später erneut versuchen.",
        "The local check was rate limited; retry later.",
    ),
    "dns": (
        "Namensauflösung auf diesem Server prüfen.", "Check name resolution on this server.",
    ),
    "dns_timeout": (
        "Namensauflösung und Wartezeit auf diesem Server prüfen.", "Check DNS resolution and timeouts on this server.",
    ),
    "tcp": (
        "Port, laufenden Dienst und Firewall zwischen den Instanzen prüfen.",
        "Check the port, running service and firewall between the instances.",
    ),
    "tcp_timeout": (
        "Port, laufenden Dienst und Firewall zwischen den Instanzen prüfen.",
        "Check the port, running service and firewall between the instances.",
    ),
    "tls": (
        "TLS am Ziel und den Reverse-Proxy prüfen.", "Check TLS on the destination and the reverse proxy.",
    ),
    "tls_certificate": (
        "TLS-Hostnamen und Zertifikatskette am Ziel prüfen.",
        "Check the TLS hostname and certificate chain on the destination.",
    ),
    "http_timeout": (
        "Antwortzeit am Ziel und im Reverse-Proxy prüfen.",
        "Check response times on the destination and reverse proxy.",
    ),
}
_GENERAL_HINT = (
    "Konfigurierte Zieladresse und lokalen Prüfpfad kontrollieren.",
    "Check the configured destination and the local diagnostic path.",
)


def _http_status(row: dict) -> int | None:
    detail = row.get("detail")
    stages = detail.get("stages") if isinstance(detail, dict) else None
    value = stages.get("http") if isinstance(stages, dict) else None
    if isinstance(value, str) and _HTTP_STATUS.fullmatch(value):
        return int(value)
    if type(value) is int and 100 <= value <= 599:
        return value
    return None


def diagnostic_readout(row: dict, *, direction: str, language: str, stale: bool = False) -> tuple[str, str]:
    """Return a localized administrator status and the next safe step, if known."""
    if stale:
        return _pair(language, "veraltet", "stale"), ""
    if row.get("status") == "success":
        return _pair(language, "erreichbar", "reachable"), ""
    if direction != "outbound":
        return _pair(language, "Eingehende Prüfung nicht bestätigt", "Incoming check not confirmed"), ""
    category = row.get("category")
    category = category if isinstance(category, str) else ""
    code = _http_status(row) if category != "target_changed" else None
    if code is not None:
        return (
            _pair(language, f"HTTP-Antwort erhalten ({code}); Peer nicht verifiziert",
                  f"HTTP response received ({code}); peer not verified"),
            _pair(language, *_HTTP_HINTS.get(code, _GENERAL_HTTP_HINT)),
        )
    label = (
        _pair(language, "Keine HTTP-Antwort nachgewiesen", "No HTTP response confirmed")
        if category not in {"configuration", "policy", "rate_limited", "target_changed"}
        else _pair(language, "Prüfung nicht bestätigt", "Check not confirmed")
    )
    return label, _pair(language, *_NO_HTTP_HINTS.get(category, _GENERAL_HINT))
