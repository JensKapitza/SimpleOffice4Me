"""Keep library JSON responses away from HTML parsing sinks."""

from pathlib import Path


TEMPLATE = Path("templates/library/index.html")
HELPER = Path("static/js/library_safe_dom.js")


def test_library_template_does_not_render_json_with_html_sinks():
    source = TEMPLATE.read_text(encoding="utf-8")
    dangerous = (
        "target.innerHTML = rows.map",
        "insertAdjacentHTML('afterbegin'",
        "box.innerHTML = data.printers.map",
        "escapeHtml(data.detail_url)",
        "escapeHtml(data.capture_url)",
    )
    for pattern in dangerous:
        assert pattern not in source


def test_library_safe_dom_restricts_dynamic_links_to_same_origin_http():
    source = HELPER.read_text(encoding="utf-8")
    assert "url.origin !== window.location.origin" in source
    assert "['http:', 'https:'].includes(url.protocol)" in source
    assert ".textContent" in source
    assert "insertAdjacentHTML" not in source
    assert ".innerHTML" not in source
