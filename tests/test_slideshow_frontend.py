import unittest
from pathlib import Path


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
        self.assertIn('id="slide-info-toggle"', template)
        self.assertIn("data-slide-information", template)
        self.assertIn("picture.image_analysis.width", template)
        self.assertIn("simpleoffice-slideshow-information", script)
        self.assertIn("setInformationVisible", script)
        self.assertNotIn("carousel-caption", template)
        self.assertNotIn("!window.bootstrap || !bootstrap.Carousel", template)


if __name__ == "__main__":
    unittest.main()
