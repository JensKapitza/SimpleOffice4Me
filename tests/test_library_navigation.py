"""Navigation contracts for the physical-library bulk scanner."""
from pathlib import Path
import unittest


NAVIGATION = Path("templates/documents/nav.html")
LIBRARY_PAGE = Path("templates/library/index.html")
PRINTERSHARE_PAGE = Path("templates/printershare/index.html")


class LibraryNavigationTests(unittest.TestCase):
    def test_bulk_scan_is_directly_reachable_from_responsive_navigation(self):
        navigation = NAVIGATION.read_text(encoding="utf-8")
        library_page = LIBRARY_PAGE.read_text(encoding="utf-8")

        link = 'href="{{ url_for(\'library.index\') }}#bulk-scan">Massen-Scan</a>'
        self.assertEqual(1, navigation.count(link))
        self.assertIn('id="bulk-scan"', library_page)
        self.assertLess(navigation.index(">Bibliothek</a>"), navigation.index(link))

    def test_printershare_has_direct_barcode_scan_and_label_print_actions(self):
        printershare = PRINTERSHARE_PAGE.read_text(encoding="utf-8")
        library = LIBRARY_PAGE.read_text(encoding="utf-8")

        self.assertIn("url_for('library.index', _anchor='label-barcode-scan')", printershare)
        self.assertIn("url_for('library.index', _anchor='printer')", printershare)
        self.assertIn('id="label-barcode-scan"', library)
        self.assertIn('data-scan-target="label-barcode"', library)
        self.assertIn('name="copies" type="number" min="1" max="100"', library)
        self.assertIn("const isLabelScanner = scanTarget.id === 'label-barcode'", library)
        self.assertIn("['ean_13', 'ean_8', 'code_128', 'code_39']", library)
        self.assertIn("Code bitte manuell eingeben.", library)


if __name__ == "__main__":
    unittest.main()
