from __future__ import annotations

import hashlib
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app as flask_app
from app.telephony_profiles import TelephonyProfileStore, sip_digest_ha1
from app.telephony_profiles import sip_digest_verifiers
from simpleoffice_sip_crypto import decrypt_sip_digest_verifiers, sip_installation_key_path
from simpleoffice_sip_runtime import SipRegistrarService, auto_sip_bind_host

TEST_KEY = "test-installation-key-that-is-long-enough"


def md5_hex(value: str) -> str:
    data = value.encode("utf-8")
    try:
        return hashlib.md5(data, usedforsecurity=False).hexdigest()
    except TypeError:  # pragma: no cover
        return hashlib.md5(data).hexdigest()  # nosec B324 - SIP Digest test vector


def packet(
    method: str,
    uri: str,
    *,
    call_id: str,
    cseq: int,
    authorization: str = "",
    contact: str = "",
    from_user: str = "101",
    to_uri: str = "",
) -> bytes:
    target = to_uri or uri
    rows = [
        f"{method} {uri} SIP/2.0",
        "Via: SIP/2.0/UDP 192.168.10.20:5062;branch=z9hG4bK-test",
        f"From: <sip:{from_user}@simpleoffice.local>;tag=caller",
        f"To: <{target}>",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} {method}",
        "Max-Forwards: 70",
    ]
    if contact:
        rows.append(f"Contact: {contact}")
        rows.append("Expires: 600")
    if authorization:
        rows.append("Authorization: " + authorization)
    rows.extend(("Content-Length: 0", "", ""))
    return "\r\n".join(rows).encode("ascii")


def challenge_nonce(response: bytes) -> str:
    match = re.search(rb'nonce="([^"]+)"', response)
    if not match:
        raise AssertionError("SIP challenge did not contain a nonce")
    return match.group(1).decode("ascii")


def auth(username: str, realm: str, password: str, nonce: str, method: str, uri: str, *, algorithm: str = "MD5", nc: int = 1) -> str:
    nc_text = f"{nc:08x}"
    cnonce = "simpleoffice-test"
    if algorithm == "SHA-256":
        digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    else:
        digest = md5_hex
    ha1 = sip_digest_ha1(username, realm, password, algorithm)
    ha2 = digest(f"{method}:{uri}")
    response = digest(f"{ha1}:{nonce}:{nc_text}:{cnonce}:auth:{ha2}")
    return (
        f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", response="{response}", algorithm={algorithm}, qop=auth, '
        f'nc={nc_text}, cnonce="{cnonce}"'
    )


class SipRuntimeTests(unittest.TestCase):
    def test_worker_key_path_matches_flask_installation_key(self) -> None:
        with patch.dict("os.environ", {"SIMPLEOFFICE_SECRET_KEY_FILE": ""}):
            self.assertEqual(
                Path(flask_app.instance_path) / "session-secret",
                sip_installation_key_path(),
            )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "mini-services.json"
        self.telephony = TelephonyProfileStore(self.root / "telephony", TEST_KEY)
        self.realm = "simpleoffice.local"
        self.password101 = "secret-for-101"
        self.password102 = "secret-for-102"
        self.telephony.create_profile(
            "101", "Ziel", "enc:v1:test101", digest_ha1=sip_digest_verifiers("101", self.realm, self.password101)
        )
        self.telephony.create_profile(
            "102", "Anrufer", "enc:v1:test102", digest_ha1=sip_digest_verifiers("102", self.realm, self.password102)
        )
        patcher = patch("simpleoffice_sip_runtime.auto_sip_bind_host", return_value="192.168.10.10")
        self.addCleanup(patcher.stop)
        patcher.start()
        self.service = SipRegistrarService(self.config, master_key=TEST_KEY)

    def test_verifiers_are_encrypted_and_sha256_is_challenged_first(self) -> None:
        with sqlite3.connect(self.telephony.path) as db:
            stored = db.execute("SELECT digest_ha1 FROM telephony_profile WHERE extension='101'").fetchone()[0]
        self.assertTrue(stored.startswith("enc:sip-ha1:v1:"))
        self.assertEqual(
            decrypt_sip_digest_verifiers("101", stored, TEST_KEY),
            sip_digest_verifiers("101", self.realm, self.password101),
        )
        response = self.service.handle_datagram(
            packet("REGISTER", "sip:192.168.10.10", call_id="sha-offer", cseq=1,
                   to_uri="sip:101@simpleoffice.local"),
            ("192.168.10.21", 5062),
        ) or b""
        self.assertLess(response.index(b"algorithm=SHA-256"), response.index(b"algorithm=MD5"))

    def test_sha256_digest_authentication_succeeds(self) -> None:
        uri = "sip:192.168.10.10"
        challenge = self.service.handle_datagram(
            packet("REGISTER", uri, call_id="sha-register", cseq=1, to_uri="sip:101@simpleoffice.local"),
            ("192.168.10.21", 5062),
        ) or b""
        nonce = challenge_nonce(challenge)
        response = self.service.handle_datagram(
            packet("REGISTER", uri, call_id="sha-register", cseq=2, to_uri="sip:101@simpleoffice.local",
                   authorization=auth("101", self.realm, self.password101, nonce, "REGISTER", uri, algorithm="SHA-256"),
                   contact="<sip:101@192.168.10.21:5062>"),
            ("192.168.10.21", 5062),
        ) or b""
        self.assertIn(b"200 OK", response)

    def test_bind_host_uses_loopback_when_socket_creation_is_denied(self) -> None:
        with patch.dict("os.environ", {"SIMPLEOFFICE_SIP_BIND": ""}), patch(
            "simpleoffice_sip_runtime.socket.getaddrinfo", side_effect=OSError
        ), patch("simpleoffice_sip_runtime.socket.socket", side_effect=PermissionError):
            self.assertEqual("127.0.0.1", auto_sip_bind_host())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_standard_register_uses_to_address_and_stores_source_lan_endpoint(self) -> None:
        registrar_uri = "sip:192.168.10.10"
        address_of_record = "sip:101@simpleoffice.local"
        first = self.service.handle_datagram(
            packet(
                "REGISTER", registrar_uri, call_id="register-1", cseq=1,
                contact="<sip:101@203.0.113.44:9999>", to_uri=address_of_record,
            ),
            ("192.168.10.21", 5062),
        )
        self.assertIsNotNone(first)
        self.assertIn(b"401 Unauthorized", first)
        nonce = challenge_nonce(first or b"")
        authorized = auth("101", self.realm, self.password101, nonce, "REGISTER", registrar_uri)
        second = self.service.handle_datagram(
            packet(
                "REGISTER", registrar_uri, call_id="register-1", cseq=2,
                authorization=authorized, contact="<sip:101@203.0.113.44:9999>", to_uri=address_of_record,
            ),
            ("192.168.10.21", 5062),
        )
        self.assertIsNotNone(second)
        self.assertIn(b"200 OK", second)
        self.assertIn(b"sip:101@192.168.10.21:5062", second)
        self.assertNotIn(b"203.0.113.44", second)

    def _register_target(self) -> None:
        registrar_uri = "sip:192.168.10.10"
        address_of_record = "sip:101@simpleoffice.local"
        challenge = self.service.handle_datagram(
            packet(
                "REGISTER", registrar_uri, call_id="register-target", cseq=1,
                contact="<sip:101@192.168.10.21:5062>", to_uri=address_of_record,
            ),
            ("192.168.10.21", 5062),
        ) or b""
        nonce = challenge_nonce(challenge)
        registered = self.service.handle_datagram(
            packet(
                "REGISTER", registrar_uri, call_id="register-target", cseq=2,
                authorization=auth("101", self.realm, self.password101, nonce, "REGISTER", registrar_uri),
                contact="<sip:101@192.168.10.21:5062>", to_uri=address_of_record,
            ),
            ("192.168.10.21", 5062),
        ) or b""
        self.assertIn(b"200 OK", registered)

    def test_invite_redirects_authenticated_local_caller_to_registered_target(self) -> None:
        self._register_target()
        invite_uri = "sip:101@simpleoffice.local"
        invite_challenge = self.service.handle_datagram(
            packet("INVITE", invite_uri, call_id="call-1", cseq=1, from_user="102"),
            ("192.168.10.22", 5070),
        ) or b""
        self.assertIn(b"401 Unauthorized", invite_challenge)
        invite_nonce = challenge_nonce(invite_challenge)
        redirected = self.service.handle_datagram(
            packet(
                "INVITE", invite_uri, call_id="call-1", cseq=2, from_user="102",
                authorization=auth("102", self.realm, self.password102, invite_nonce, "INVITE", invite_uri),
            ),
            ("192.168.10.22", 5070),
        ) or b""
        self.assertIn(b"302 Moved Temporarily", redirected)
        self.assertIn(b"Contact: <sip:101@192.168.10.21:5062>", redirected)

    def test_invite_rejects_spoofed_from_identity(self) -> None:
        self._register_target()
        uri = "sip:101@simpleoffice.local"
        challenge = self.service.handle_datagram(
            packet("INVITE", uri, call_id="spoof", cseq=1, from_user="101"),
            ("192.168.10.22", 5070),
        ) or b""
        nonce = challenge_nonce(challenge)
        response = self.service.handle_datagram(
            packet(
                "INVITE", uri, call_id="spoof", cseq=2, from_user="101",
                authorization=auth("102", self.realm, self.password102, nonce, "INVITE", uri),
            ),
            ("192.168.10.22", 5070),
        ) or b""
        self.assertIn(b"403 Forbidden", response)

    def test_unknown_or_external_sources_are_not_forwarded(self) -> None:
        uri = "sip:999@simpleoffice.local"
        challenge = self.service.handle_datagram(
            packet("INVITE", uri, call_id="call-unknown", cseq=1, from_user="102"),
            ("192.168.10.22", 5070),
        ) or b""
        self.assertIn(b"401 Unauthorized", challenge)
        self.assertIsNone(
            self.service.handle_datagram(packet("OPTIONS", uri, call_id="outside", cseq=1), ("8.8.8.8", 5060))
        )

    def test_digest_nonce_cannot_be_replayed(self) -> None:
        registrar_uri = "sip:192.168.10.10"
        address_of_record = "sip:101@simpleoffice.local"
        challenge = self.service.handle_datagram(
            packet(
                "REGISTER", registrar_uri, call_id="replay", cseq=1,
                contact="<sip:101@192.168.10.21:5062>", to_uri=address_of_record,
            ),
            ("192.168.10.21", 5062),
        ) or b""
        nonce = challenge_nonce(challenge)
        authorization = auth("101", self.realm, self.password101, nonce, "REGISTER", registrar_uri, nc=1)
        ok = self.service.handle_datagram(
            packet(
                "REGISTER", registrar_uri, call_id="replay", cseq=2,
                authorization=authorization, contact="<sip:101@192.168.10.21:5062>", to_uri=address_of_record,
            ),
            ("192.168.10.21", 5062),
        ) or b""
        self.assertIn(b"200 OK", ok)
        replay = self.service.handle_datagram(
            packet(
                "REGISTER", registrar_uri, call_id="replay", cseq=3,
                authorization=authorization, contact="<sip:101@192.168.10.21:5062>", to_uri=address_of_record,
            ),
            ("192.168.10.21", 5062),
        ) or b""
        self.assertIn(b"401 Unauthorized", replay)


if __name__ == "__main__":
    unittest.main()
