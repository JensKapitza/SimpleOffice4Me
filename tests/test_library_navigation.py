"""Navigation contracts for the physical-library bulk scanner."""
from pathlib import Path


NAVIGATION = Path("templates/documents/nav.html")
LIBRARY_PAGE = Path("templates/library/index.html")


def test_bulk_scan_is_directly_reachable_from_responsive_navigation():
    navigation = NAVIGATION.read_text(encoding="utf-8")
    library_page = LIBRARY_PAGE.read_text(encoding="utf-8")

    link = 'href="{{ url_for(\'library.index\') }}#bulk-scan">Massen-Scan</a>'
    assert navigation.count(link) == 1
    assert 'id="bulk-scan"' in library_page
    assert navigation.index(">Bibliothek</a>") < navigation.index(link)
