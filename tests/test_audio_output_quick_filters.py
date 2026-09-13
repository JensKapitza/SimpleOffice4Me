import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "admin" / "audio_output.html"


class AudioOutputQuickFilterTests(unittest.TestCase):
    def test_output_filters_are_present(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="audio-output-filter"', template)
        self.assertIn('id="audio-output-status-filter"', template)
        self.assertIn('data-audio-output-row', template)
        self.assertIn('id="audio-output-filter-count"', template)
        self.assertIn('id="audio-output-filter-empty"', template)


if __name__ == "__main__":
    unittest.main()
