"""Setup hints for federation peers (no writes)."""

from __future__ import annotations

import unittest

from app.federation_setup_hints import suggest_federation_setup


def _peer(peer_id: str, enabled: bool = True, has_token: bool = True,
          pairing: str = "claimed", label: str = ""):
    return {
        "peer_id": peer_id,
        "label": label or peer_id,
        "enabled": enabled,
        "has_token": has_token,
        "pairing": {"status": pairing},
    }


class FederationSetupHintsTest(unittest.TestCase):
    def test_reports_only_active_peer_without_token(self) -> None:
        rows = [
            _peer("a", has_token=True, pairing="expired"),
            _peer("b", has_token=False, pairing="expired", label="Office B"),
        ]
        self.assertEqual(suggest_federation_setup(rows), [{"peer_id": "b", "label": "Office B"}])

    def test_skips_disabled_and_token_configured_peers(self) -> None:
        rows = [
            _peer("a", enabled=False, has_token=False),
            _peer("b", pairing="claimed"),
            _peer("c", pairing="pending"),
            _peer("d", pairing="expired"),
            _peer("e", pairing="missing"),
            _peer("", has_token=False),
        ]
        self.assertEqual(suggest_federation_setup(rows), [])

    def test_sorts_hint_labels_and_uses_peer_id_as_fallback(self) -> None:
        rows = [
            _peer("first", has_token=False, label="Zulu"),
            _peer("second", has_token=False, label="Alpha"),
            _peer("middle", has_token=False, label=" "),
        ]
        self.assertEqual(
            suggest_federation_setup(rows),
            [
                {"peer_id": "second", "label": "Alpha"},
                {"peer_id": "middle", "label": "middle"},
                {"peer_id": "first", "label": "Zulu"},
            ],
        )

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_federation_setup([]), [])
        self.assertEqual(suggest_federation_setup([None, "x"]), [])

    def test_limits_items(self) -> None:
        rows = [_peer(str(i), has_token=False) for i in range(7)]
        self.assertEqual(len(suggest_federation_setup(rows)), 5)


if __name__ == "__main__":
    unittest.main()
