"""Thunderbird-compatible mail provider discovery with conservative network access."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass


MAX_CONFIG_BYTES = 512 * 1024
REQUEST_TIMEOUT = 5.0
THUNDERBIRD_ISPDB = "https://autoconfig.thunderbird.net/v1.1/{domain}"


@dataclass(frozen=True)
class DiscoverySource:
    name: str
    url: str
    provider_owned: bool = False


def _email_parts(email: str) -> tuple[str, str, str]:
    value = str(email or "").strip()
    if len(value) > 320 or value.count("@") != 1 or any(ch.isspace() for ch in value):
        raise ValueError("Bitte eine gültige E-Mail-Adresse eingeben.")
    local, domain = value.rsplit("@", 1)
    domain = domain.rstrip(".").lower()
    if not local or not domain or "." not in domain or len(domain) > 253:
        raise ValueError("Bitte eine vollständige E-Mail-Adresse mit gültiger Domain eingeben.")
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Die Domain der E-Mail-Adresse ist ungültig.") from exc
    labels = ascii_domain.split(".")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
        raise ValueError("Die Domain der E-Mail-Adresse ist ungültig.")
    return value, local, ascii_domain


def _host_is_public(host: str) -> bool:
    """Reject loopback/private/link-local destinations before provider-owned HTTPS requests."""
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    found = False
    for family, _socktype, _proto, _canonname, sockaddr in addresses:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        found = True
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global:
            return False
    return found


def _fetch(source: DiscoverySource) -> bytes:
    parsed = urllib.parse.urlparse(source.url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Autokonfiguration darf nur über HTTPS geladen werden.")
    if source.provider_owned and not _host_is_public(parsed.hostname):
        raise ValueError("Provider-Autokonfiguration verweist nicht auf eine öffentliche Adresse.")
    request = urllib.request.Request(
        source.url,
        headers={
            "User-Agent": "SimpleOffice4Me-MailAutoconfig/1.0",
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        final = urllib.parse.urlparse(response.geturl())
        if final.scheme != "https" or not final.hostname:
            raise ValueError("Unsichere Weiterleitung bei der Mail-Autokonfiguration.")
        if source.provider_owned and not _host_is_public(final.hostname):
            raise ValueError("Provider-Autokonfiguration wurde auf eine interne Adresse umgeleitet.")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > MAX_CONFIG_BYTES:
            raise ValueError("Mail-Autokonfiguration ist unerwartet groß.")
        data = response.read(MAX_CONFIG_BYTES + 1)
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError("Mail-Autokonfiguration ist unerwartet groß.")
    return data


def _text(element: ET.Element | None, child: str, default: str = "") -> str:
    if element is None:
        return default
    node = element.find(child)
    return (node.text or "").strip() if node is not None else default


def _replace_username(template: str, email: str, local: str, domain: str) -> str:
    value = template or "%EMAILADDRESS%"
    replacements = {
        "%EMAILADDRESS%": email,
        "%EMAILLOCALPART%": local,
        "%EMAILDOMAIN%": domain,
    }
    for token, replacement in replacements.items():
        value = value.replace(token, replacement)
    return value[:320]


def _security(socket_type: str, default_port: int) -> tuple[str, int] | None:
    kind = socket_type.strip().upper()
    if kind in {"SSL", "SSL/TLS"}:
        return "tls", default_port
    if kind == "STARTTLS":
        return "starttls", default_port
    return None


def parse_thunderbird_config(data: bytes, email: str, source: str) -> dict[str, object]:
    full_email, local, domain = _email_parts(email)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("Ungültige Thunderbird-Autokonfiguration.") from exc
    provider = root.find(".//emailProvider")
    if provider is None:
        raise ValueError("Keine E-Mail-Providerdaten in der Autokonfiguration gefunden.")

    display = _text(provider, "displayName", domain)
    incoming = None
    for candidate in provider.findall("incomingServer"):
        if str(candidate.attrib.get("type", "")).lower() == "imap":
            secured = _security(_text(candidate, "socketType"), int(_text(candidate, "port", "993") or 993))
            if secured and _text(candidate, "hostname"):
                incoming = (candidate, secured)
                break
    outgoing = None
    for candidate in provider.findall("outgoingServer"):
        if str(candidate.attrib.get("type", "")).lower() == "smtp":
            secured = _security(_text(candidate, "socketType"), int(_text(candidate, "port", "587") or 587))
            if secured and _text(candidate, "hostname"):
                outgoing = (candidate, secured)
                break
    if incoming is None or outgoing is None:
        raise ValueError("Autokonfiguration enthält keine unterstützte IMAP/SMTP-TLS-Kombination.")

    imap, (imap_security, imap_port) = incoming
    smtp, (smtp_security, smtp_port) = outgoing
    imap_auth = [_text(node, ".") for node in imap.findall("authentication")]
    smtp_auth = [_text(node, ".") for node in smtp.findall("authentication")]
    auth_values = " ".join(imap_auth + smtp_auth).lower()
    warnings = []
    if "oauth2" in auth_values:
        warnings.append("Der Anbieter kündigt OAuth2 an; falls Passwort-Anmeldung abgewiesen wird, ist OAuth/App-Passwort erforderlich.")

    result: dict[str, object] = {
        "source": source,
        "source_label": "Thunderbird ISPDB" if source == "thunderbird-ispdb" else "Provider-Autokonfiguration",
        "email": full_email,
        "label": display[:120],
        "host": _text(imap, "hostname")[:253],
        "port": imap_port,
        "security": imap_security,
        "auth_method": "auto",
        "username": _replace_username(_text(imap, "username"), full_email, local, domain),
        "folder": "INBOX",
        "smtp_host": _text(smtp, "hostname")[:253],
        "smtp_port": smtp_port,
        "smtp_security": smtp_security,
        "smtp_username": _replace_username(_text(smtp, "username"), full_email, local, domain),
        "smtp_from": full_email,
        "sieve_host": "",
        "sieve_port": 4190,
        "warnings": warnings,
        "confidence": "provider",
    }
    return result


def _heuristic(email: str, domain: str) -> dict[str, object]:
    return {
        "source": "heuristic",
        "source_label": "Domain-Heuristik",
        "email": email,
        "label": domain,
        "host": f"imap.{domain}",
        "port": 993,
        "security": "tls",
        "auth_method": "auto",
        "username": email,
        "folder": "INBOX",
        "smtp_host": f"smtp.{domain}",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_username": email,
        "smtp_from": email,
        "sieve_host": "",
        "sieve_port": 4190,
        "warnings": ["Keine bestätigten Providerdaten gefunden. Die vorgeschlagenen Servernamen müssen vor dem Speichern getestet werden."],
        "confidence": "guess",
    }


def discover_mail_settings(email: str) -> dict[str, object]:
    full_email, _local, domain = _email_parts(email)
    query_email = urllib.parse.quote(full_email, safe="@")
    sources = (
        DiscoverySource(
            "provider-autoconfig",
            f"https://autoconfig.{domain}/mail/config-v1.1.xml?emailaddress={query_email}",
            provider_owned=True,
        ),
        DiscoverySource(
            "provider-well-known",
            f"https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml?emailaddress={query_email}",
            provider_owned=True,
        ),
        DiscoverySource("thunderbird-ispdb", THUNDERBIRD_ISPDB.format(domain=urllib.parse.quote(domain, safe=""))),
    )
    for source in sources:
        try:
            data = _fetch(source)
            return parse_thunderbird_config(data, full_email, source.name)
        except (ValueError, OSError, urllib.error.URLError, TimeoutError, socket.timeout):
            continue
    return _heuristic(full_email, domain)
