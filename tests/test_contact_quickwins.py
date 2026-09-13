from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_contact_merge_has_bulk_selection_and_reset_controls():
    template = read("templates/documents/contact_combine.html")
    script = read("static/js/contact-combine.js")
    assert 'id="combine-select-all"' in template
    assert 'id="combine-clear"' in template
    assert "Filter zurücksetzen" in template
    assert 'role="search"' in template
    assert 'enterkeyhint="search"' in template
    assert "setAll(true)" in script
    assert "setAll(false)" in script


def test_contact_merge_rows_are_keyboard_selectable_and_highlighted():
    template = read("templates/documents/contact_combine.html")
    script = read("static/js/contact-combine.js")
    assert 'class="combine-contact-row"' in template
    assert 'tabindex="0"' in template
    assert 'href="mailto:' in template
    assert 'href="tel:' in template
    assert 'event.key !== " " && event.key !== "Enter"' in script
    assert 'row.classList.toggle("table-primary"' in script
    assert 'row.setAttribute("aria-selected"' in script
