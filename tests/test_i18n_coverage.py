"""Regression coverage for catalog references and UI created by JavaScript."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password
from app.settings_store import TRANSLATIONS


ROOT = Path(__file__).resolve().parents[1]
_TR_CALL = re.compile(r"\btr\(\s*['\"]([^'\"]+)['\"]")
_TRANSLATE_CALL = re.compile(
    r"\btranslate\(\s*[^,]+,\s*['\"]([^'\"]+)['\"]"
)


class TranslationReferenceCoverageTest(unittest.TestCase):
    def test_shared_helper_is_available_before_page_scripts(self) -> None:
        layout = (ROOT / "templates/layout.html").read_text(encoding="utf-8")
        helper_position = layout.index("window.SimpleOfficeI18n")
        page_script_position = layout.index("{% block script %}")
        self.assertLess(helper_position, page_script_position)

    def test_literal_translation_calls_have_catalog_entries(self) -> None:
        missing = []
        for folder in (ROOT / "app", ROOT / "templates"):
            for path in folder.rglob("*"):
                if path.suffix not in {".py", ".html"}:
                    continue
                source = path.read_text(encoding="utf-8")
                keys = set(_TR_CALL.findall(source) + _TRANSLATE_CALL.findall(source))
                for key in keys:
                    # A trailing dot marks a deliberately assembled namespace,
                    # for example tr("invoice.status." ~ status).
                    if key.endswith("."):
                        continue
                    if key not in TRANSLATIONS["de"] or key not in TRANSLATIONS["en"]:
                        missing.append(f"{path.relative_to(ROOT)}: {key}")
        self.assertEqual([], sorted(missing))


class TranslationTemplateRenderTest(unittest.TestCase):
    password = "test-only-i18n-coverage-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            ensure_auth_database()
            get_db().execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at) "
                "VALUES (?,?,?,1,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin"),
            )
            get_db().commit()
        self.client = app.test_client()
        login_body = self.client.get("/auth/login").data
        csrf = re.search(rb'name="_csrf_token" value="([^"]+)"', login_body)
        assert csrf is not None
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password,
                  "_csrf_token": csrf.group(1).decode()},
        )

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def _get(self, path: str, language: str) -> str:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        return response.data.decode("utf-8")

    def test_visual_template_overview_renders_in_both_languages(self) -> None:
        english = self._get("/documents/business/templates/editor", "en")
        self.assertIn("Template overview", english)
        self.assertIn("Create template", english)
        self.assertNotIn("Vorlagenübersicht", english)
        german = self._get("/documents/business/templates/editor", "de")
        self.assertIn("Vorlagenübersicht", german)
        self.assertIn("Neue Vorlage erstellen", german)

    def test_datalogger_settings_renders_in_both_languages(self) -> None:
        english = self._get("/datalogger/settings", "en")
        self.assertIn("Data logger worker", english)
        self.assertIn("Allowed sensor hosts", english)
        self.assertNotIn("Datenlogger-Worker", english)
        german = self._get("/datalogger/settings", "de")
        self.assertIn("Datenlogger-Worker", german)
        self.assertIn("Erlaubte Sensor-Hosts", german)


try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional browser dependency
    PlaywrightError = Exception
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class DynamicJavaScriptTranslationTest(unittest.TestCase):
    def test_project_cockpit_uses_selected_language_after_dom_load(self) -> None:
        app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        i18n_js = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")
        page_markup = """<!doctype html><html lang="__LANG__"><body>
          <main><h1>Demo project</h1><section id="aufgaben" class="card">
            <article id="task-1" data-task-status="open"><div class="card-header">
              <span class="badge">Open</span><strong>Project name</strong>
            </div><div class="card-body"><form action="/tasks/1"><input name="resources" value=""></form></div></article>
          </section></main>
          <script>__APP__</script><script>window.SimpleOfficeTranslations = {};</script>
          <script>__I18N__</script></body></html>"""
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
                try:
                    for language, expected_heading, expected_filter in (
                        ("de", "Projektcockpit", "Aufgaben, Beschreibung oder Ressource filtern"),
                        ("en", "Project overview", "Filter tasks, descriptions, or resources"),
                    ):
                        page = browser.new_page()
                        page.set_content(
                            page_markup.replace("__LANG__", language)
                            .replace("__APP__", app_js)
                            .replace("__I18N__", i18n_js)
                        )
                        self.assertEqual(expected_heading, page.locator("#project-cockpit .card-header strong").inner_text())
                        self.assertTrue(page.locator("#project-task-filter").get_attribute("placeholder").startswith(expected_filter))
                        page.close()
                finally:
                    browser.close()
        except PlaywrightError as exc:
            self.skipTest(f"Chromium is unavailable: {exc}")

    def test_calendar_quick_wins_are_created_in_selected_language(self) -> None:
        calendar_js = (ROOT / "static/js/calendar_quick_wins.js").read_text(encoding="utf-8")
        i18n_js = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")
        page_markup = """<!doctype html><html lang="__LANG__"><body>
          <main class="container-xl"><div class="d-flex"><h1>__HEADING__</h1></div>
            <section id="reminders"><div class="card-header"><span class="badge">1</span></div>
              <div class="list-group"><div class="list-group-item">Remember this</div></div>
            </section>
            <form action="/documents/calendar"><div class="col-md-6"><input name="title" value=""></div></form>
          </main><script>window.SimpleOfficeTranslations = {};</script>
          <script>Object.defineProperty(window, "localStorage", {value: {getItem: () => null, setItem: () => {}}, configurable: true});</script>
          <script>__CALENDAR__</script><script>__I18N__</script></body></html>"""
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
                try:
                    page = browser.new_page()
                    for language, heading, expected, validation in (
                        ("de", "Kalender", "Kalendercockpit", "Privat oder Geschäftlich muss ausgewählt werden."),
                        ("en", "Calendar", "Calendar overview", "Choose private or business."),
                    ):
                        content = (page_markup.replace("__LANG__", language)
                                   .replace("__HEADING__", heading)
                                   .replace("__CALENDAR__", calendar_js)
                                   .replace("__I18N__", i18n_js))
                        page.set_content(content)
                        self.assertEqual(expected, page.locator("#calendar-cockpit strong").inner_text())
                        page.locator("form[action='/documents/calendar']").evaluate(
                            "form => form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}))"
                        )
                        self.assertEqual(validation, page.locator("[data-appointment-context]").evaluate("select => select.validationMessage"))
                finally:
                    browser.close()
        except PlaywrightError as exc:
            self.skipTest(f"Chromium is unavailable: {exc}")

    def test_site_visit_device_status_uses_selected_language(self) -> None:
        visits_js = (ROOT / "static/js/site_visits.js").read_text(encoding="utf-8")
        i18n_js = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")
        page_markup = """<!doctype html><html lang="__LANG__"><body>
          <div data-site-visit><span data-cap="camera"></span><span data-cap="barcode"></span>
            <span data-cap="location"></span><span data-cap="orientation"></span></div>
          <script>window.SimpleOfficeTranslations = {};</script>
          <script>Object.defineProperty(navigator, "mediaDevices", {value: undefined, configurable: true});</script>
          <script>__I18N__</script><script>__VISITS__</script></body></html>"""
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
                try:
                    page = browser.new_page()
                    for language, expected in (("de", "Kamera: nicht verfügbar"), ("en", "Camera: unavailable")):
                        content = (page_markup.replace("__LANG__", language)
                                   .replace("__I18N__", i18n_js)
                                   .replace("__VISITS__", visits_js))
                        page.set_content(content)
                        self.assertEqual(expected, page.locator('[data-cap="camera"]').inner_text())
                finally:
                    browser.close()
        except PlaywrightError as exc:
            self.skipTest(f"Chromium is unavailable: {exc}")

    def test_android_offline_fallback_uses_selected_language(self) -> None:
        offline_js = (ROOT / "static/js/v3_android_offline.js").read_text(encoding="utf-8")
        page_markup = """<!doctype html><html lang="__LANG__"><head><script>
          (() => { const language = document.documentElement.lang === "de" ? "de" : "en";
            window.SimpleOfficeI18n = {text: (german, english) => language === "de" ? german : english}; })();
          </script></head><body>
          <div data-android-offline-app data-enabled="1" data-task-api-prefix="/tasks/" data-sync-api="/sync">
            <div data-offline-message></div><div data-offline-cached></div><div data-offline-queue></div>
            <span data-offline-native></span><span data-offline-items></span><span data-offline-outbox></span>
            <span data-offline-bytes></span><div data-offline-worksets></div>
          </div><script>__OFFLINE__</script></body></html>"""
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
                try:
                    page = browser.new_page()
                    for language, expected in (("de", "Offline-Arbeitsdaten sind nur in der Android-App verfügbar."),
                                               ("en", "Offline data is available only in the Android app.")):
                        page.set_content(page_markup.replace("__LANG__", language).replace("__OFFLINE__", offline_js))
                        self.assertEqual(expected, page.locator("[data-offline-cached]").inner_text())
                finally:
                    browser.close()
        except PlaywrightError as exc:
            self.skipTest(f"Chromium is unavailable: {exc}")

    def test_screen_support_status_uses_selected_language(self) -> None:
        support_js = (ROOT / "static/js/screen_support.js").read_text(encoding="utf-8")
        page_markup = """<!doctype html><html lang="__LANG__"><head><script>
          (() => { const language = document.documentElement.lang === "de" ? "de" : "en";
            window.SimpleOfficeI18n = {text: (german, english) => language === "de" ? german : english}; })();
          </script></head><body><div id="screen-support-panel">
          __ELEMENTS__</div><script>__SUPPORT__</script></body></html>"""
        identifiers = ("request grant deny revoke send permission-popup active-banner status popup popup-text fields history error notice-form popup-grant popup-deny banner-revoke ack notice").split()
        elements = "".join(f'<form id="screen-support-{name}"></form>' if name == "notice-form" else f'<div id="screen-support-{name}"></div>' for name in identifiers)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
                try:
                    page = browser.new_page()
                    for language, expected in (("de", "Keine aktive Support-Sitzung. Verlauf bleibt verfügbar."),
                                               ("en", "No active support session. History remains available.")):
                        page.set_content(page_markup.replace("__LANG__", language)
                                         .replace("__ELEMENTS__", elements)
                                         .replace("__SUPPORT__", support_js))
                        self.assertEqual(expected, page.locator("#screen-support-status").inner_text())
                finally:
                    browser.close()
        except PlaywrightError as exc:
            self.skipTest(f"Chromium is unavailable: {exc}")

    def test_company_autocomplete_hint_uses_selected_language(self) -> None:
        autocomplete_js = (ROOT / "static/js/company-contact-autocomplete.js").read_text(encoding="utf-8")
        page_markup = """<!doctype html><html lang="__LANG__"><head><script>
          (() => { const language = document.documentElement.lang === "de" ? "de" : "en";
            window.SimpleOfficeI18n = {text: (german, english) => language === "de" ? german : english}; })();
          </script></head><body><div data-company-autocomplete data-company-endpoint="/contacts/search">
          <input data-company-name><input data-company-id><div data-company-results hidden></div>
          <div data-company-status></div></div><script>__AUTOCOMPLETE__</script></body></html>"""
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
                try:
                    page = browser.new_page()
                    for language, expected in (("de", "Bitte mindestens zwei Zeichen eingeben."),
                                               ("en", "Enter at least two characters.")):
                        page.set_content(page_markup.replace("__LANG__", language)
                                         .replace("__AUTOCOMPLETE__", autocomplete_js))
                        page.locator("[data-company-name]").fill("a")
                        page.wait_for_timeout(250)
                        self.assertEqual(expected, page.locator("[data-company-status]").inner_text())
                finally:
                    browser.close()
        except PlaywrightError as exc:
            self.skipTest(f"Chromium is unavailable: {exc}")


if __name__ == "__main__":
    unittest.main()
