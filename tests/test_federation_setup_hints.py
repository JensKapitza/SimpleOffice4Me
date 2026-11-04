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
    def test_missing_token_first(self) -> None:
        rows = [
            _peer("a", has_token=True, pairing="expired"),
            _peer("b", has_token=False),
        ]
        proposals = suggest_federation_setup(rows)
        self.assertEqual([row["peer_id"] for row in proposals], ["b", "a"])
        self.assertIn("Zugangstoken", proposals[0]["reason"])

    def test_skips_disabled_and_complete(self) -> None:
        rows = [
            _peer("a", enabled=False, has_token=False),
            _peer("b", pairing="claimed"),
            _peer("c", pairing="pending"),
        ]
        self.assertEqual(suggest_federation_setup(rows), [])

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_federation_setup([]), [])
        self.assertEqual(suggest_federation_setup([None, "x"]), [])

    def test_limits_items(self) -> None:
        rows = [_peer(str(i), has_token=False) for i in range(7)]
        self.assertEqual(len(suggest_federation_setup(rows)), 5)


if __name__ == "__main__":
    unittest.main()
