"""Regression tests for the public shopping-share template item mapping."""
from pathlib import Path

from jinja2 import Environment


def test_public_shopping_share_renders_items_from_mapping():
    template_path = Path(__file__).resolve().parents[1] / "templates/shopping/public_share.html"
    markup = template_path.read_text(encoding="utf-8")
    assert "{% for item in shared['items'] %}" in markup
    start = markup.index("{% for item in shared['items'] %}")
    end = markup.index("{% endfor %}", start) + len("{% endfor %}")
    render = Environment(autoescape=True).from_string(markup[start:end]).render

    populated = render(shared={"items": [
        {"name": "Milk", "quantity": 2, "unit": "L", "price": "1,50", "estimated_line_total": "3,00"},
        {"name": "Bread", "quantity": "", "unit": "", "price": "", "estimated_line_total": ""},
    ]})
    assert "Milk" in populated
    assert "Bread" in populated
    assert "3,00" in populated
    assert "Die Liste ist leer." not in populated

    empty = render(shared={"items": []})
    assert "Die Liste ist leer." in empty
    assert "<article" not in empty
