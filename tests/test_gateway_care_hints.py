"""Tests for local gateway care hints (read-only proposals)."""

import unittest

from app.gateway_care_hints import suggest_gateway_review


def _service(state="running", enabled=True, key=True, autostart=True):
    return {
        "state": state,
        "health": {"ok": state == "running", "code": "ok" if state == "running" else "conn"},
        "settings": {
            "enabled": enabled,
            "api_key_configured": key,
            "provider_key_configured": False,
            "autostart": autostart,
        },
    }


class SuggestGatewayReviewTests(unittest.TestCase):
    def test_degraded_flagged(self):
        hints = suggest_gateway_review(_service(state="degraded"))
        self.assertEqual(len(hints), 1)
        self.assertIn("degraded", hints[0]["reason"])

    def test_healthy_quiet(self):
        self.assertEqual(suggest_gateway_review(_service()), [])

    def test_missing_key_flagged(self):
        hints = suggest_gateway_review(_service(key=False))
        self.assertEqual(len(hints), 1)
        self.assertIn("Gateway-Key", hints[0]["reason"])

    def test_autostart_off_flagged(self):
        hints = suggest_gateway_review(_service(autostart=False))
        self.assertEqual(len(hints), 1)
        self.assertIn("Autostart", hints[0]["reason"])

    def test_disabled_service_quiet(self):
        service = _service(state="disabled", enabled=False, key=False, autostart=False)
        self.assertEqual(suggest_gateway_review(service), [])

    def test_none_no_hint(self):
        self.assertEqual(suggest_gateway_review(None), [])
        self.assertEqual(suggest_gateway_review("x"), [])


if __name__ == "__main__":
    unittest.main()
