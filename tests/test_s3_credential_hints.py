"""Tests for local S3 credential hints (read-only proposals)."""

import unittest

from app.s3_credential_hints import suggest_credential_review

TODAY = "2026-11-04"


def _cred(label, expires_at="", revoked=False, last_used_at=""):
    return {
        "label": label,
        "expires_at": expires_at,
        "revoked": revoked,
        "last_used_at": last_used_at,
    }


class SuggestCredentialReviewTests(unittest.TestCase):
    def test_expired_flagged(self):
        creds = [_cred("alt", "2026-09-01T00:00:00+00:00")]
        hints = suggest_credential_review(creds, True, TODAY)
        self.assertEqual(len(hints), 1)
        self.assertIn("abgelaufen", hints[0]["reason"])

    def test_expiring_soon_flagged(self):
        creds = [_cred("bald", "2026-11-10T00:00:00+00:00")]
        hints = suggest_credential_review(creds, True, TODAY)
        self.assertEqual(len(hints), 1)
        self.assertIn("2026-11-10", hints[0]["reason"])

    def test_revoked_excluded(self):
        creds = [_cred("weg", "2026-09-01T00:00:00+00:00", revoked=True)]
        self.assertEqual(suggest_credential_review(creds, True, TODAY), [])

    def test_stale_unused_flagged(self):
        creds = [_cred("still", "2027-06-01T00:00:00+00:00", last_used_at="2026-01-01T00:00:00+00:00")]
        hints = suggest_credential_review(creds, True, TODAY)
        self.assertEqual(len(hints), 1)
        self.assertIn("ungenutzt", hints[0]["reason"])

    def test_disabled_overlay_flagged(self):
        creds = [_cred("ok", "2027-06-01T00:00:00+00:00", last_used_at="2026-11-01T00:00:00+00:00")]
        hints = suggest_credential_review(creds, False, TODAY)
        self.assertEqual(len(hints), 1)
        self.assertIn("deaktiviert", hints[0]["reason"])

    def test_empty_no_hint(self):
        self.assertEqual(suggest_credential_review([], True, TODAY), [])
        self.assertEqual(suggest_credential_review(None, True, TODAY), [])


if __name__ == "__main__":
    unittest.main()
