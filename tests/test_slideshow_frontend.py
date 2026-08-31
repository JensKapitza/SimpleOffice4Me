import unittest
from pathlib import Path

from app.settings_store import TRANSLATIONS


class SlideshowFrontendTest(unittest.TestCase):
    def test_initialization_waits_for_bootstrap_and_uses_selected_delay(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "slideshow.js").read_text(encoding="utf-8")
        template = (root / "templates" / "documents" / "images.html").read_text(encoding="utf-8")

        self.assertIn('document.addEventListener("DOMContentLoaded", initializeSlideshow', script)
        self.assertIn("window.bootstrap?.Carousel", script)
        self.assertIn("window.setTimeout(() => carousel.next(), delay())", script)
        self.assertIn('data-original-url="{{ url_for(', template)
        self.assertIn("js/slideshow.js", template)
        self.assertIn("css/slideshow.css", template)
        self.assertIn('class="slideshow-filmstrip"', template)
        self.assertIn('class="slideshow-details"', template)
        self.assertIn('class="slideshow-thumb', template)
        self.assertIn('thumbnails[safeIndex]?.scrollIntoView', script)
        self.assertIn('currentTitle.textContent', script)
        self.assertNotIn("carousel-caption", template)
        self.assertNotIn("!window.bootstrap || !bootstrap.Carousel", template)

    def test_slideshow_labels_exist_in_german_and_english(self):
        keys = {
            "slideshow.title", "slideshow.close", "slideshow.info_controls",
            "slideshow.autostart", "slideshow.duration", "slideshow.seconds",
            "slideshow.start", "slideshow.stop", "slideshow.open_original",
            "slideshow.previous", "slideshow.next", "slideshow.thumbnails",
        }
        for language in ("de", "en"):
            self.assertTrue(keys.issubset(TRANSLATIONS[language]))


if __name__ == "__main__":
    unittest.main()
