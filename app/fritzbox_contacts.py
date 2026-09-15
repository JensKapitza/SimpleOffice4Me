"""FRITZ!Box TR-064 integration for contacts and connection status."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_xml_fromstring
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .contact_store import ContactStore
from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory

bp = Blueprint("fritzbox_contacts", __name__, url_prefix="/contacts/fritzbox")

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
DEFAULT_SERVICE = "urn:dslforum-org:service:X_AVM-DE_OnTel:1"
DEFAULT_CONTROL_URL = "/upnp/control/x_contact"
TR064_DESCRIPTION_PATHS = (
    ("/tr64desc.xml", ""),
    ("/tr064/tr64desc.xml", "/tr064"),
)
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_SYNC_CONTACTS = 1000
PHONE_FIELDS = (
    ("mobile", "mobile"),
    ("phone_private", "home"),
    ("phone_business", "work"),
    ("phone", "home"),
)


class FritzBoxError(RuntimeError):
    """Actionable FRITZ!Box communication error without credentials."""


class FritzBoxSecretBox:
    """Encrypt FRITZ!Box secrets with an installation-specific key domain."""

    def __init__(self, master_key: bytes):
        if len(master_key) < 16:
            raise ValueError("FRITZ!Box secret master key is too short")
        self.key = hashlib.sha256(b"simpleoffice-fritzbox-v1\0" + master_key).digest()

    def encrypt(self, value: str) -> str:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key).encrypt(nonce, value.encode("utf-8"), b"simpleoffice-fritzbox-v1")
        return "enc:v1:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value.startswith("enc:v1:"):
            raise ValueError("unsupported encrypted FRITZ!Box secret")
        raw = base64.urlsafe_b64decode(value.removeprefix("enc:v1:").encode("ascii"))
        if len(raw) < 29:
            raise ValueError("invalid encrypted FRITZ!Box secret")
        return AESGCM(self.key).decrypt(raw[:12], raw[12:], b"simpleoffice-fritzbox-v1").decode("utf-8")


def _is_local_host(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    if host in {"fritz.box", "localhost"} or host.endswith(".fritz.box"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback or address.is_link_local:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        ))
    return address in ipaddress.ip_network("fc00::/7")


def _safe_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("FRITZ!Box-Adresse muss HTTP/HTTPS ohne Zugangsdaten verwenden")
    if parsed.scheme == "http" and not _is_local_host(parsed.hostname):
        raise ValueError("Unverschlüsseltes TR-064 ist nur für fritz.box oder lokale/private IP-Adressen erlaubt")
    if parsed.query or parsed.fragment:
        raise ValueError("FRITZ!Box-Adresse darf keine Query oder Fragment enthalten")
    if parsed.path not in {"", "/"}:
        raise ValueError("FRITZ!Box-Adresse bitte nur als Basis-URL angeben")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("ungültiger FRITZ!Box-Port")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _as_bool(value: Any) -> bool | None:
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _contact_numbers(contact: dict[str, Any]) -> list[tuple[str, str]]:
    fields = contact.get("fields", {})
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for field, kind in PHONE_FIELDS:
        value = str(fields.get(field, "")).strip()
        if not value:
            continue
        normalized = re.sub(r"[^0-9+]", "", value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append((value[:80], kind))
        if len(result) >= 3:
            break
    return result


def contact_entry_xml(contact: dict[str, Any], unique_id: int | None = None) -> str:
    """Build the AVM phonebook contact XML passed to SetPhonebookEntryUID."""
    fields = contact.get("fields", {})
    name = str(fields.get("display_name") or " ".join(
        part for part in (fields.get("first_name", ""), fields.get("last_name", "")) if part
    )).strip()
    numbers = _contact_numbers(contact)
    if not name:
        raise ValueError("Kontakt hat keinen Anzeigenamen")
    if not numbers:
        raise ValueError("Kontakt hat keine Telefonnummer")

    root = ET.Element("contact")
    if unique_id is not None:
        ET.SubElement(root, "uniqueid").text = str(int(unique_id))
    ET.SubElement(root, "category").text = "0"
    person = ET.SubElement(root, "person")
    ET.SubElement(person, "realName").text = name[:200]
    telephony = ET.SubElement(root, "telephony", {"nid": "1"})
    for index, (number, kind) in enumerate(numbers):
        node = ET.SubElement(
            telephony, "number",
            {"type": kind, "prio": "1" if index == 0 else "0", "id": str(index)},
        )
        node.text = number
    email = str(fields.get("email", "")).strip()
    services = ET.SubElement(root, "services")
    if email and len(email) <= 320 and "@" in email:
        ET.SubElement(services, "email", {"classifier": "private", "id": "0"}).text = email
    ET.SubElement(root, "setup")
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


class FritzBoxClient:
    def __init__(self, base_url: str, username: str, password: str, verify_tls: bool = True, timeout: float = 8.0):
        self.base_url = _safe_base_url(base_url)
        self.username = username.strip()
        self.password = password
        self.verify_tls = bool(verify_tls)
        self.timeout = max(1.0, min(float(timeout), 30.0))
        if not self.username or not self.password:
            raise ValueError("FRITZ!Box-Benutzername und Passwort sind erforderlich")
        if not self.verify_tls:
            raise ValueError(
                "TLS-Zertifikatsprüfung wird nicht deaktiviert. Für eine lokale FRITZ!Box "
                "kann stattdessen der von AVM vorgesehene TR-064-Endpunkt http://fritz.box:49000 verwendet werden."
            )
        context = ssl.create_default_context()
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, self.base_url + "/", self.username, self.password)
        handlers: list[Any] = [
            urllib.request.HTTPDigestAuthHandler(manager),
            urllib.request.HTTPBasicAuthHandler(manager),
        ]
        if urllib.parse.urlsplit(self.base_url).scheme == "https":
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self.opener = urllib.request.build_opener(*handlers)
        self.service_type = DEFAULT_SERVICE
        self.control_url = DEFAULT_CONTROL_URL
        self.services: dict[str, str] = {}
        self._discover()

    @staticmethod
    def _http_status(exc: BaseException) -> int | None:
        cause = exc.__cause__
        return cause.code if isinstance(cause, urllib.error.HTTPError) else None

    @staticmethod
    def _apply_path_prefix(control_url: str, prefix: str) -> str:
        if not control_url.startswith("/"):
            return control_url
        if prefix and not control_url.startswith(prefix + "/"):
            return prefix + control_url
        return control_url

    @staticmethod
    def _control_candidates(control_url: str) -> list[str]:
        control = control_url if control_url.startswith("/") else DEFAULT_CONTROL_URL
        if control.startswith("/tr064/"):
            candidates = [control, control.removeprefix("/tr064")]
        else:
            candidates = [control, "/tr064" + control]
        return list(dict.fromkeys(candidates))

    def _read(self, request_or_url: urllib.request.Request | str) -> bytes:
        try:
            with self.opener.open(request_or_url, timeout=self.timeout) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise FritzBoxError("FRITZ!Box-Anmeldung abgewiesen; Benutzerrechte und Passwort prüfen") from exc
            raise FritzBoxError(f"FRITZ!Box antwortet mit HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise FritzBoxError(
                    "FRITZ!Box-Zertifikat ist nicht vertrauenswürdig. Die Prüfung bleibt aktiv. "
                    "Im lokalen Heimnetz kann TR-064 stattdessen über http://fritz.box:49000 verwendet werden."
                ) from exc
            raise FritzBoxError(f"FRITZ!Box nicht erreichbar: {exc}") from exc
        except (TimeoutError, ssl.SSLError, OSError) as exc:
            raise FritzBoxError(f"FRITZ!Box nicht erreichbar: {exc}") from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise FritzBoxError("FRITZ!Box-Antwort ist unerwartet groß")
        return data

    def _discover(self) -> None:
        last_error: FritzBoxError | None = None
        for description_path, path_prefix in TR064_DESCRIPTION_PATHS:
            try:
                data = self._read(self.base_url + description_path)
                root = safe_xml_fromstring(data)
            except FritzBoxError as exc:
                last_error = exc
                continue
            except (ET.ParseError, DefusedXmlException):
                continue

            discovered: dict[str, str] = {}
            for service in root.iter():
                if service.tag.rsplit("}", 1)[-1] != "service":
                    continue
                values = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in service}
                service_type = values.get("serviceType", "")
                control = values.get("controlURL", "")
                if service_type and control.startswith("/"):
                    discovered[service_type] = self._apply_path_prefix(control, path_prefix)
            if discovered:
                self.services = discovered
                phonebook = self._find_service("X_AVM-DE_OnTel", required=False)
                if phonebook is not None:
                    self.service_type, self.control_url = phonebook
                return

        if last_error is not None:
            raise last_error
        raise FritzBoxError("FRITZ!Box-TR-064-Beschreibung konnte nicht geladen werden")

    def _find_service(self, fragment: str, required: bool = True) -> tuple[str, str] | None:
        for service_type, control_url in self.services.items():
            if fragment in service_type:
                return service_type, control_url
        if required:
            raise FritzBoxError(f"FRITZ!Box stellt den TR-064-Service {fragment} nicht bereit")
        return None

    def _soap_service(
        self,
        service_type: str,
        control_url: str,
        action: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        arguments = arguments or {}
        params = "".join(f"<{key}>{escape(str(value))}</{key}>" for key, value in arguments.items())
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{SOAP_ENV}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{service_type}">{params}</u:{action}></s:Body></s:Envelope>'
        ).encode("utf-8")
        raw: bytes | None = None
        last_error: FritzBoxError | None = None
        for candidate in self._control_candidates(control_url):
            url = urllib.parse.urljoin(self.base_url + "/", candidate.lstrip("/"))
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{service_type}#{action}"',
            })
            try:
                raw = self._read(req)
                if service_type == self.service_type:
                    self.control_url = candidate
                break
            except FritzBoxError as exc:
                last_error = exc
                if self._http_status(exc) == 404:
                    continue
                raise
        if raw is None:
            raise FritzBoxError(
                "FRITZ!Box-TR-064-Endpunkt nicht gefunden (HTTP 404). "
                "Im Heimnetz Port 49000 (HTTP) oder 49443 (HTTPS) verwenden; bei Remote-HTTPS "
                "wird /tr064 automatisch versucht. Außerdem 'Zugriff für Anwendungen zulassen' aktivieren."
            ) from last_error
        try:
            root = safe_xml_fromstring(raw)
        except (ET.ParseError, DefusedXmlException) as exc:
            raise FritzBoxError("Ungültige SOAP-Antwort der FRITZ!Box") from exc
        fault = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Fault"), None)
        if fault is not None:
            text = " ".join((node.text or "").strip() for node in fault.iter() if (node.text or "").strip())
            raise FritzBoxError(("FRITZ!Box-Aktion abgewiesen: " + text)[:500])
        response = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == action + "Response"), None)
        if response is None:
            raise FritzBoxError("FRITZ!Box-Antwort enthält kein erwartetes Aktionsergebnis")
        return {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in response}

    def soap(self, action: str, arguments: dict[str, Any] | None = None) -> dict[str, str]:
        return self._soap_service(self.service_type, self.control_url, action, arguments)

    def service_action(self, fragment: str, action: str) -> dict[str, str]:
        service = self._find_service(fragment)
        assert service is not None
        return self._soap_service(service[0], service[1], action)

    def phonebooks(self) -> list[dict[str, Any]]:
        if self._find_service("X_AVM-DE_OnTel", required=False) is None:
            raise FritzBoxError("FRITZ!Box stellt keinen Telefonbuch-Service bereit")
        raw = self.soap("GetPhonebookList").get("NewPhonebookList", "")
        ids = [int(value) for value in re.findall(r"\d+", raw)][:100]
        result = []
        for phonebook_id in ids:
            info = self.soap("GetPhonebook", {"NewPhonebookID": phonebook_id})
            result.append({
                "id": phonebook_id,
                "name": info.get("NewPhonebookName") or f"Telefonbuch {phonebook_id}",
                "extra_id": info.get("NewPhonebookExtraID", ""),
            })
        return result

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "external_ipv4": "",
            "external_ipv6": "",
            "dyndns_enabled": None,
            "dyndns_domain": "",
            "dyndns_provider": "",
            "myfritz_enabled": None,
            "myfritz_domain": "",
            "myfritz_state": "",
            "errors": [],
        }

        if self._find_service("X_AVM-DE_AppSetup", required=False) is not None:
            try:
                info = self.service_action("X_AVM-DE_AppSetup", "GetAppRemoteInfo")
                result["external_ipv4"] = info.get("NewExternalIPAddress", info.get("ExternalIPAddress", ""))
                result["external_ipv6"] = info.get("NewExternalIPv6Address", info.get("ExternalIPv6Address", ""))
                result["dyndns_enabled"] = _as_bool(info.get("NewRemoteAccessDDNSEnabled", info.get("RemoteAccessDDNSEnabled", "")))
                result["dyndns_domain"] = info.get("NewRemoteAccessDDNSDomain", info.get("RemoteAccessDDNSDomain", ""))
                result["myfritz_enabled"] = _as_bool(info.get("NewMyFritzDynDNSEnabled", info.get("MyFritzDynDNSEnabled", "")))
                result["myfritz_domain"] = info.get("NewMyFritzDynDNSName", info.get("MyFritzDynDNSName", ""))
            except FritzBoxError as exc:
                result["errors"].append(str(exc))

        if self._find_service("X_AVM-DE_RemoteAccess", required=False) is not None:
            try:
                info = self.service_action("X_AVM-DE_RemoteAccess", "GetDDNSInfo")
                if result["dyndns_enabled"] is None:
                    result["dyndns_enabled"] = _as_bool(info.get("NewEnabled", ""))
                if not result["dyndns_domain"]:
                    result["dyndns_domain"] = info.get("NewDomain", "")
                result["dyndns_provider"] = info.get("NewProviderName", "")
            except FritzBoxError as exc:
                result["errors"].append(str(exc))

        if self._find_service("X_AVM-DE_MyFritz", required=False) is not None:
            try:
                info = self.service_action("X_AVM-DE_MyFritz", "GetInfo")
                if result["myfritz_enabled"] is None:
                    result["myfritz_enabled"] = _as_bool(info.get("NewEnabled", ""))
                if not result["myfritz_domain"]:
                    result["myfritz_domain"] = info.get("NewDynDNSName", "")
                result["myfritz_state"] = info.get("NewState", "")
            except FritzBoxError as exc:
                result["errors"].append(str(exc))

        if not result["external_ipv4"]:
            for service_name in ("WANIPConnection", "WANPPPConnection"):
                if self._find_service(service_name, required=False) is None:
                    continue
                try:
                    info = self.service_action(service_name, "GetExternalIPAddress")
                    result["external_ipv4"] = info.get("NewExternalIPAddress", "")
                    if result["external_ipv4"]:
                        break
                except FritzBoxError as exc:
                    result["errors"].append(str(exc))

        return result

    def set_contact(self, phonebook_id: int, contact: dict[str, Any], unique_id: int | None = None) -> int:
        result = self.soap("SetPhonebookEntryUID", {
            "NewPhonebookID": int(phonebook_id),
            "NewPhonebookEntryData": contact_entry_xml(contact, unique_id),
        })
        value = result.get("NewPhonebookEntryUniqueID", "")
        if not re.fullmatch(r"\d+", value):
            raise FritzBoxError("FRITZ!Box hat keine Kontakt-UID zurückgegeben")
        return int(value)


class FritzBoxStore:
    def __init__(self, root: str | Path, master_key: bytes):
        self.root = Path(root).resolve()
        self.control = self.root / CONTROL_DIR / "fritzbox"
        self.path = self.control / "contacts.json"
        self.secrets = FritzBoxSecretBox(master_key)
        self.history = RevisionHistory(self.root)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"version": 1, "connections": []}
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "connections": []}

    def _row(self, actor: str) -> dict[str, Any]:
        return next((dict(row) for row in self._read().get("connections", []) if row.get("owner") == actor), {})

    @staticmethod
    def _effective_url(row: dict[str, Any]) -> str:
        url = str(row.get("url") or "http://fritz.box:49000")
        if url == "https://fritz.box:49443" and row.get("verify_tls") is False:
            return "http://fritz.box:49000"
        return url

    def config(self, actor: str) -> dict[str, Any]:
        row = self._row(actor)
        return {
            "url": self._effective_url(row),
            "username": row.get("username", ""),
            "verify_tls": True,
            "phonebook_id": row.get("phonebook_id", ""),
            "password_saved": bool(row.get("password")),
            "updated_at": row.get("updated_at", ""),
        }

    def save_config(self, actor: str, data: dict[str, Any], password: str, remember: bool) -> dict[str, Any]:
        url = _safe_base_url(str(data.get("url", "")))
        username = str(data.get("username", "")).strip()[:128]
        if not username:
            raise ValueError("FRITZ!Box-Benutzername fehlt")
        phonebook_raw = str(data.get("phonebook_id", "")).strip()
        phonebook_id = int(phonebook_raw) if phonebook_raw else ""
        if phonebook_id != "" and not 0 <= int(phonebook_id) <= 9999:
            raise ValueError("ungültige Telefonbuch-ID")
        payload = self._read()
        previous = next((row for row in payload.get("connections", []) if row.get("owner") == actor), None)
        stored_password = (previous or {}).get("password", "")
        if password:
            stored_password = self.secrets.encrypt(password) if remember else ""
        row = {
            "id": (previous or {}).get("id", uuid.uuid4().hex),
            "owner": actor,
            "url": url,
            "username": username,
            "password": stored_password,
            "verify_tls": True,
            "phonebook_id": phonebook_id,
            "mappings": dict((previous or {}).get("mappings", {})),
            "updated_at": utc_now(),
        }
        payload["connections"] = [x for x in payload.get("connections", []) if x.get("owner") != actor] + [row]
        self.control.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            atomic_json_write(self.path, payload)
        self.history.record("fritzbox_contact_connection_updated", actor, "fritzbox", row["id"], self.config(actor))
        return self.config(actor)

    def credentials(self, actor: str, password: str = "") -> dict[str, Any]:
        row = self._row(actor)
        if not row:
            raise ValueError("FRITZ!Box-Verbindung ist noch nicht eingerichtet")
        plain = password or (self.secrets.decrypt(str(row["password"])) if row.get("password") else "")
        if not plain:
            raise ValueError("FRITZ!Box-Passwort ist erforderlich")
        return {**row, "url": self._effective_url(row), "verify_tls": True, "plain_password": plain}

    def mapping(self, actor: str, phonebook_id: int, contact_id: str) -> int | None:
        value = self._row(actor).get("mappings", {}).get(f"{phonebook_id}:{contact_id}")
        return int(value) if str(value).isdigit() else None

    def save_mapping(self, actor: str, phonebook_id: int, contact_id: str, unique_id: int) -> None:
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            payload = self._read()
            row = next((x for x in payload.get("connections", []) if x.get("owner") == actor), None)
            if row is None:
                raise ValueError("FRITZ!Box-Verbindung ist noch nicht eingerichtet")
            row.setdefault("mappings", {})[f"{phonebook_id}:{contact_id}"] = int(unique_id)
            row["updated_at"] = utc_now()
            atomic_json_write(self.path, payload)


def _master_key() -> bytes:
    value = current_app.config["SECRET_KEY"]
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def _store() -> FritzBoxStore:
    return FritzBoxStore(current_app.config["DOCUMENT_ROOT"], _master_key())


def _contacts() -> list[dict[str, Any]]:
    return ContactStore(current_app.config["DOCUMENT_ROOT"]).contacts(str(g.user["username"]))


def _client(credentials: dict[str, Any]) -> FritzBoxClient:
    return FritzBoxClient(
        credentials["url"], credentials["username"], credentials["plain_password"], verify_tls=True,
    )


@bp.get("")
@login_required
def index():
    contacts = _contacts()
    return render_template(
        "documents/fritzbox_contacts.html",
        config=_store().config(str(g.user["username"])),
        contacts=[{**item, "syncable": bool(_contact_numbers(item))} for item in contacts],
        syncable_count=sum(bool(_contact_numbers(item)) for item in contacts),
        phonebooks=[],
        fritz_status=None,
    )


@bp.post("/discover")
@login_required
def discover():
    actor = str(g.user["username"])
    password = request.form.get("password", "")
    remember = request.form.get("remember") == "1"
    try:
        store = _store()
        store.save_config(actor, {
            "url": request.form.get("url", ""), "username": request.form.get("username", ""),
            "verify_tls": True, "phonebook_id": request.form.get("phonebook_id", ""),
        }, password, remember)
        credentials = store.credentials(actor, password)
        client = _client(credentials)
        books = client.phonebooks()
        status = client.status()
        contacts = _contacts()
        flash(f"FRITZ!Box erreichbar: {len(books)} Telefonbuch/Telefonbücher gefunden.")
        return render_template(
            "documents/fritzbox_contacts.html", config=store.config(actor), phonebooks=books,
            contacts=[{**item, "syncable": bool(_contact_numbers(item))} for item in contacts],
            syncable_count=sum(bool(_contact_numbers(item)) for item in contacts),
            fritz_status=status,
        )
    except (ValueError, FritzBoxError) as exc:
        flash(str(exc))
        return redirect(url_for("fritzbox_contacts.index"))


@bp.post("/sync")
@login_required
def sync():
    actor = str(g.user["username"])
    password = request.form.get("password", "")
    selected = list(dict.fromkeys(request.form.getlist("contact_id")))[:MAX_SYNC_CONTACTS]
    try:
        store = _store()
        credentials = store.credentials(actor, password)
        phonebook_id = int(request.form.get("phonebook_id", credentials.get("phonebook_id", "")))
        if not 0 <= phonebook_id <= 9999:
            raise ValueError("ungültige Telefonbuch-ID")
        available = {item["contact_id"]: item for item in _contacts()}
        if not selected:
            raise ValueError("Keine Kontakte für die Übertragung ausgewählt")
        client = _client(credentials)
        created = updated = skipped = 0
        failures: list[str] = []
        for contact_id in selected:
            contact = available.get(contact_id)
            if contact is None:
                skipped += 1
                continue
            try:
                if not _contact_numbers(contact):
                    skipped += 1
                    continue
                existing_uid = store.mapping(actor, phonebook_id, contact_id)
                unique_id = client.set_contact(phonebook_id, contact, existing_uid)
                store.save_mapping(actor, phonebook_id, contact_id, unique_id)
                if existing_uid is None:
                    created += 1
                else:
                    updated += 1
            except (ValueError, FritzBoxError) as exc:
                failures.append(f"{contact.get('fields', {}).get('display_name', contact_id)}: {exc}")
        store.save_config(actor, {**store.config(actor), "phonebook_id": phonebook_id}, "", True)
        store.history.record(
            "fritzbox_contacts_synced", actor, "fritzbox", str(phonebook_id),
            {"phonebook_id": phonebook_id, "created": created, "updated": updated, "skipped": skipped, "failed": len(failures), "contact_ids": selected},
        )
        message = f"FRITZ!Box-Sync: {created} neu, {updated} aktualisiert, {skipped} übersprungen"
        if failures:
            message += f", {len(failures)} Fehler. " + " | ".join(failures[:3])
        flash(message)
    except (ValueError, FritzBoxError) as exc:
        flash(str(exc))
    return redirect(url_for("fritzbox_contacts.index"))
