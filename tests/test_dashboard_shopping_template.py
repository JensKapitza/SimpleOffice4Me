"""Regression test for the dashboard shopping card (issue #1083)."""
from pathlib import Path
import re

from jinja2 import Environment, select_autoescape


def test_dashboard_shopping_items_are_iterated_as_mapping_value():
    template = (Path(__file__).resolve().parents[1] / "templates/documents/dashboard.html").read_text(encoding="utf-8")
    shopping_list = re.search(
        r'(<ul class="small text-secondary mb-0 mt-2">\{% for item in briefing\.shopping\[\x27items\x27\] %\}.*?\{% endfor %\}</ul>)',
        template,
        flags=re.DOTALL,
    )
    assert shopping_list is not None, "The shopping list must use explicit mapping-key access"
    render = Environment(autoescape=select_autoescape(["html"])).from_string(shopping_list.group(1)).render
    html = render(briefing={"shopping": {"items": [{"name": "Milk", "quantity": "2"}, {"name": "Bread", "quantity": ""}]}})
    assert "<li>Milk · 2</li>" in html
    assert "<li>Bread</li>" in html
    assert "builtin_function_or_method" not in html
    assert "<li>" not in render(briefing={"shopping": {"items": []}})
