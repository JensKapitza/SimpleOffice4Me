"""Thin administrator browser flow: open, save, check, download, reject restore."""
import re
from urllib.parse import urlsplit

from playwright.sync_api import expect


def run_litellm_smoke(page, *, output_dir, checks):
    width = page.viewport_size['width']
    address = urlsplit(page.url)
    base = address.scheme + '://' + address.netloc

    def record(action):
        checks.append({'page': '/admin/mini-services/litellm', 'action': action, 'viewport': width, 'ok': True})

    page.goto(base + '/admin/mini-services', wait_until='domcontentloaded')
    page.get_by_role('link', name='LiteLLM konfigurieren', exact=True).click()
    expect(page.get_by_role('heading', name='LiteLLM Gateway', exact=True)).to_be_visible()
    record('LiteLLM-Konfiguration über Mini Services öffnen')
    page.get_by_label(re.compile('^Betriebsart')).select_option('external')
    page.get_by_label(re.compile('^LiteLLM\\ aktivieren')).uncheck()
    page.get_by_label(re.compile('^Externe\\ Gateway\\-URL')).fill('https://gateway.example.org/v1')
    page.get_by_label(re.compile('^Standardmodell\\ /\\ Gateway\\-Alias')).fill('browser-test-model')
    page.get_by_role('button', name='Einstellungen speichern', exact=True).click()
    expect(page.get_by_text('LiteLLM-Einstellungen gespeichert.', exact=True)).to_be_visible()
    record('Deaktivierte externe Konfiguration speichern')
    page.get_by_role('button', name='Verbindung testen', exact=True).click()
    expect(page.get_by_text('LiteLLM deaktiviert.', exact=True).first).to_be_visible()
    record('Verbindungstest für deaktivierten Dienst aufrufen')
    with page.expect_download() as download:
        page.get_by_role('link', name='Verschlüsselte Konfiguration sichern', exact=True).click()
    assert download.value.suggested_filename == 'litellm-backup.json'
    record('Konfigurationssicherung herunterladen')
    page.get_by_label(re.compile('^Sicherung\\ \\(JSON\\)')).set_input_files(
        {'name': 'invalid-backup.json', 'mimeType': 'application/json', 'buffer': b'broken'})
    page.get_by_role('button', name='Deaktivierte Konfiguration wiederherstellen', exact=True).click()
    expect(page.get_by_text('Restore fehlgeschlagen.', exact=False)).to_be_visible()
    record('Fehlermeldung nach ungueltiger Wiederherstellung anzeigen')
    page.screenshot(path=str(output_dir / f'litellm-{width}-restore-error.png'), full_page=True)
