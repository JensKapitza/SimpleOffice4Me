from __future__ import annotations

import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.telephony_profiles import TelephonyProfileStore, sip_digest_ha1
from simpleoffice_sip_runtime import SipRegistrarService


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


def auth(username: str, realm: str, password: str, nonce: str, method: str, uri: str, *, nc: int = 1) -> str:
    nc_text = f"{nc:08x}"
    cnonce = "simpleoffice-test"
    ha1 = sip_digest_ha1(username, realm, password)
    ha2 = md5_hex(f"{method}:{uri}")
    response = md5_hex(f"{ha1}:{nonce}:{nc_text}:{cnonce}:auth:{ha2}")
    return (
        f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", response="{response}", algorithm=MD5, qop=auth, '
        f'nc={nc_text}, cnonce="{cnonce}"'
    )


class SipRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "mini-services.json"
        self.telephony = TelephonyProfileStore(self.root / "telephony")
        self.realm = "simpleoffice.local"
        self.password101 = "secret-for-101"
        self.password102 = "secret-for-102"
        self.telephony.create_profile(
            "101", "Ziel", "enc:v1:test101", digest_ha1=sip_digest_ha1("101", self.realm, self.password101)
        )
        self.telephony.create_profile(
            "102", "Anrufer", "enc:v1:test102", digest_ha1=sip_digest_ha1("102", self.realm, self.password102)
        )
        patcher = patch("simpleoffice_sip_runtime.auto_sip_bind_host", return_value="192.168.10.10")
        self.addCleanup(patcher.stop)
        patcher.start()
        self.service = SipRegistrarService(self.config)

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
