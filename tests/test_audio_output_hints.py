"""Tests for local audio output hints (read-only proposals)."""

import unittest

from app.audio_output_hints import suggest_output_review


def _output(name, node="local", online=True):
    return {"name": name, "node_id": node, "online": online}


def _service(ok=True, message=""):
    return {"health": {"ok": ok, "message": message}}


class SuggestOutputReviewTests(unittest.TestCase):
    def test_offline_output_flagged(self):
        hints = suggest_output_review(
            [_output("Küche", online=False)], _service(ok=True)
        )
        self.assertEqual(len(hints), 1)
        self.assertIn("Küche", hints[0]["reason"])

    def test_online_quiet(self):
        outputs = [_output("Küche", online=True)]
        self.assertEqual(suggest_output_review(outputs, _service(ok=True)), [])

    def test_worker_issue_flagged_with_outputs(self):
        outputs = [_output("Küche", online=True)]
        hints = suggest_output_review(outputs, _service(ok=False, message="kein Scan"))
        self.assertEqual(len(hints), 1)
        self.assertIn("kein Scan", hints[0]["reason"])

    def test_empty_setup_quiet(self):
        self.assertEqual(suggest_output_review([], _service(ok=False)), [])
        self.assertEqual(suggest_output_review(None, None), [])

    def test_sorted_and_limited(self):
        outputs = [_output(f"raum-{i:02d}", online=False) for i in range(7)]
        hints = suggest_output_review(outputs, _service(ok=True), max_items=3)
        self.assertEqual(len(hints), 3)
        self.assertIn("raum-00", hints[0]["reason"])

    def test_node_fallback_label(self):
        hints = suggest_output_review(
            [{"node_id": "remote-1", "online": False}], _service(ok=True)
        )
        self.assertIn("remote-1", hints[0]["reason"])


if __name__ == "__main__":
    unittest.main()
