"""Small user-facing MCP smoke flow: navigate, issue credentials, revoke.

No API/schema checks, database access or layout implementation assertions.
Screenshots are taken only after reloading the one-time credential page.
"""
from __future__ import annotations

from playwright.sync_api import expect


def run_mcp_smoke(page, *, output_dir, run_id, checks):
    width = page.viewport_size["width"]
    prefix = f"mcp-{width}"

    def record(action):
        checks.append({"page": "/settings/mcp", "action": action, "viewport": width, "ok": True})

    def screenshot(label):
        page.screenshot(path=str(output_dir / f"{prefix}-{label}.png"), full_page=True)

    navigation = page.get_by_role("navigation", name="Hauptnavigation")
    toggle = navigation.get_by_role("button", name="Navigation öffnen")
    if toggle.is_visible() and not navigation.get_by_role("button", name="Mehr", exact=True).is_visible():
        toggle.click()
    navigation.get_by_role("button", name="Mehr", exact=True).click()
    navigation.get_by_role("link", name="MCP", exact=True).click()
    expect(page.get_by_role("heading", name="ChatGPT- und MCP-Zugang")).to_be_visible()
    record("MCP über Mehr öffnen")
    screenshot("settings")

    for writable, suffix in ((False, "Lesen"), (True, "Schreiben")):
        label = f"Browser MCP {suffix} {run_id}-{width}"
        form = page.locator("form").filter(has=page.get_by_role("button", name="Zugang einmalig erzeugen"))
        form.locator("input[name='name']").fill(label)
        if writable:
            form.get_by_role("checkbox", name="Schreibende Werkzeuge erlauben").check()
        form.get_by_role("button", name="Zugang einmalig erzeugen").click()
        row = page.get_by_role("row").filter(has=page.get_by_role("cell", name=label, exact=True))
        expect(row).to_be_visible()
        record(f"{suffix}-Zugang über Formular anlegen")
        # Reload through the browser before recording any page containing a secret.
        page.goto(page.url, wait_until="domcontentloaded")
        screenshot(suffix.lower())
        if not writable:
            row.get_by_role("button", name="Widerrufen", exact=True).click()
            expect(row.get_by_text("widerrufen", exact=True)).to_be_visible()
            record("Lesezugang über Widerrufen sperren")
            screenshot("revoked")

    expect(page.get_by_text("Verarbeitungsjournal", exact=False)).to_be_visible()
    record("Verarbeitungsjournal anzeigen")
