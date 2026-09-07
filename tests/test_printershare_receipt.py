import hashlib
import hmac
import json
import unittest

from app.printershare import _verify_receipt


TOKEN = "remote-receipt-token"
REVISION = "a" * 64
PAYLOAD = b"exact print payload"
PAYLOAD_HASH = hashlib.sha256(PAYLOAD).hexdigest()


def signed(receipt):
    return hmac.new(
        TOKEN.encode("utf-8"),
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def receipt(**updates):
    value = {
        "schema": 1,
        "job_id": "job-1",
        "request_nonce": "nonce-1234567890abcdef",
        "printer_id": "printer-1",
        "status": "spooled",
        "retention": "no_store",
        "expires_at": 0,
        "payload_sha256": PAYLOAD_HASH,
        "payload_size": len(PAYLOAD),
        "policy_revision": REVISION,
        "completed_at": 1000,
        "application_archive": False,
        "os_spooler_may_cache": True,
    }
    value.update(updates)
    return value


def verify(value, *, ceiling="no_store", ttl=0):
    return _verify_receipt(
        value,
        signed(value),
        TOKEN,
        ceiling=ceiling,
        expected_policy_revision=REVISION,
        expected_printer_id="printer-1",
        expected_payload_sha256=PAYLOAD_HASH,
        expected_payload_size=len(PAYLOAD),
        expected_request_nonce="nonce-1234567890abcdef",
        ttl_ceiling_seconds=ttl,
    )


class PrinterShareReceiptTest(unittest.TestCase):
    def test_exact_no_store_receipt_is_accepted(self):
        self.assertIsNone(verify(receipt()))

    def test_receipt_for_other_payload_is_rejected_even_when_signed(self):
        with self.assertRaisesRegex(ValueError, "anderen Druckdatei"):
            verify(receipt(payload_sha256="b" * 64))

    def test_receipt_for_other_printer_is_rejected_even_when_signed(self):
        with self.assertRaisesRegex(ValueError, "anderen Drucker"):
            verify(receipt(printer_id="printer-2"))

    def test_stale_receipt_nonce_is_rejected_even_when_signed(self):
        with self.assertRaisesRegex(ValueError, "konkreten Druckauftrag"):
            verify(receipt(request_nonce="nonce-stale-123456789"))

    def test_ttl_receipt_may_not_exceed_sent_ceiling(self):
        value = receipt(
            retention="ttl",
            application_archive=True,
            completed_at=1000,
            expires_at=1121,
        )
        with self.assertRaisesRegex(ValueError, "längere TTL"):
            verify(value, ceiling="ttl", ttl=120)

    def test_ttl_receipt_within_sent_ceiling_is_accepted(self):
        value = receipt(
            retention="ttl",
            application_archive=True,
            completed_at=1000,
            expires_at=1120,
        )
        self.assertIsNone(verify(value, ceiling="ttl", ttl=120))


if __name__ == "__main__":
    unittest.main()
