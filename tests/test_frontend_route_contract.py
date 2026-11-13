from __future__ import annotations

import re
import tempfile
import unittest
import os
from unittest.mock import patch
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.settings_store import SettingsStore


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
NAV_TEMPLATE = TEMPLATES / "documents" / "nav.html"
URL_FOR = re.compile(r"url_for\(\s*['\"]([^'\"]+)['\"]")
STATIC_FILE = re.compile(
    r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]"
)
TEMPLATE_REF = re.compile(
    r"{%\s*(?:extends|include|import|from)\s+['\"]([^'\"]+)['\"]"
)
SERVICE_WORKER_STATIC = re.compile(r"['\"](/static/[^'\"]+)['\"]")
LAYOUT_EXTENDS = re.compile(r"{%\s*extends\s+['\"]layout\.html['\"]\s*%}")
BODY_BLOCK = re.compile(r"{%\s*block\s+body\b")
CONTENT_BLOCK = re.compile(r"{%\s*block\s+content\b")
DYNAMIC_LAYOUT_ASSETS = {
    "manifest.webmanifest",
    "manifest-clock.webmanifest",
    "manifest-files.webmanifest",
    "manifest-slideshow.webmanifest",
}
STATE_CHANGING_GET_ENDPOINTS = {"auth.logout"}
NON_PAGE_GET_ENDPOINTS = {
    # JavaScript JSON lookup. A parameterless request is a valid negative lookup (404).
    "library.resolve_location",
    # File downloads are covered by their HTTP contracts, not the page-shell smoke test.
    "library.export_books_csv",
    "library.export_books_ris",
    "library.export_books_bibtex",
    # Firefox bookmark export is a Netscape HTML file download, not a page shell.
    "browser_bookmarks.export_firefox",
    # Import preview is only available after this browser session stages an upload.
    "browser_bookmarks.import_preview",
}


def _literal_endpoints(path: Path) -> set[str]:
    return set(URL_FOR.findall(path.read_text(encoding="utf-8")))


def _all_template_endpoints() -> set[str]:
    endpoints: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        endpoints.update(_literal_endpoints(path))
    return endpoints


def _literal_static_files() -> set[str]:
    files: set[str] = set(DYNAMIC_LAYOUT_ASSETS)
    for path in TEMPLATES.rglob("*.html"):
        files.update(STATIC_FILE.findall(path.read_text(encoding="utf-8")))
    worker = STATIC / "service-worker.js"
    if worker.is_file():
        files.update(
            value.removeprefix("/static/")
            for value in SERVICE_WORKER_STATIC.findall(worker.read_text(encoding="utf-8"))
        )
    return files


def _literal_template_references() -> set[str]:
    references: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        references.update(TEMPLATE_REF.findall(path.read_text(encoding="utf-8")))
    return references


def _parameterless_get_rule(endpoint: str):
    rules = [
        rule for rule in app.url_map.iter_rules(endpoint)
        if "GET" in rule.methods and not rule.arguments
    ]
    return min(rules, key=lambda item: (len(item.rule), item.rule)) if rules else None


class FrontendRouteContractTests(unittest.TestCase):
    def test_every_literal_template_url_for_endpoint_is_registered(self):
        """A stale url_for in any template must not take the whole UI down."""
        registered = {rule.endpoint for rule in app.url_map.iter_rules()}
        referenced = {
            endpoint for endpoint in _all_template_endpoints()
            if not endpoint.startswith(".")
        }
        missing = sorted(referenced - registered)
        self.assertEqual([], missing, f"Template url_for endpoints are not registered: {missing}")

    def test_main_navigation_endpoints_have_routes(self):
        """Every endpoint advertised by the global navigation must exist."""
        registered = {rule.endpoint for rule in app.url_map.iter_rules()}
        missing = sorted(_literal_endpoints(NAV_TEMPLATE) - registered)
        self.assertEqual([], missing, f"Main navigation endpoints are not registered: {missing}")

    def test_main_navigation_dropdowns_stay_within_ten_entries(self):
        """Large sections get their own dropdown instead of becoming crowded lists."""
        template = NAV_TEMPLATE.read_text(encoding="utf-8")
        dropdowns = re.findall(r'<ul class="dropdown-menu"[^>]*>(.*?)</ul>', template, re.S)
        counts = [len(re.findall(r"<li\b", menu)) for menu in dropdowns]
        self.assertTrue(counts, "No primary navigation dropdowns found")
        self.assertLessEqual(max(counts), 10, f"Dropdown item counts exceed 10: {counts}")
        top_level_count = len(re.findall(r'<div class="nav-item dropdown">', template)) + 1
        self.assertLessEqual(top_level_count, 10, f"Top-level navigation items exceed 10: {top_level_count}")

    def test_mini_services_has_one_global_entry_and_sftp_is_a_static_hub_link(self):
        """Mini Service configuration stays under one destination, independent of JS status loading."""
        template = NAV_TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(1, template.count("url_for('mini_services_admin.index')"))
        for endpoint in (
            "telephony_admin.index",
            "network_boot_admin.index",
            "audio_output_admin.index",
            "audio_streamer_admin.page",
            "connectivity_relay_admin.index",
            "firewall_admin.index",
            "litellm_admin.index",
            "mini_services_admin.sftp",
        ):
            self.assertNotIn(f"url_for('{endpoint}')", template)

        hub = (TEMPLATES / "admin" / "mini_services_hub.html").read_text(encoding="utf-8")
        self.assertIn("href=\"{{ url_for('mini_services_admin.sftp') }}\"", hub)

    def test_layout_children_do_not_define_unrendered_content_block(self):
        """layout.html exposes body, so a direct child using only content renders blank."""
        broken: list[str] = []
        for path in TEMPLATES.rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            if LAYOUT_EXTENDS.search(text) and CONTENT_BLOCK.search(text) and not BODY_BLOCK.search(text):
                broken.append(str(path.relative_to(TEMPLATES)))
        self.assertEqual([], sorted(broken), f"Direct layout children render a dead content block: {broken}")

    def test_literal_template_dependencies_exist(self):
        """Literal include/extends/import targets must always be shipped."""
        missing = sorted(
            reference for reference in _literal_template_references()
            if not (TEMPLATES / reference).is_file()
        )
        self.assertEqual([], missing, f"Referenced templates are missing: {missing}")

    def test_frontend_static_assets_exist_and_are_served(self):
        """Every literal UI asset and PWA shell asset must exist and return HTTP 200."""
        missing = sorted(
            filename for filename in _literal_static_files()
            if not (STATIC / filename).is_file()
        )
        self.assertEqual([], missing, f"Frontend static files are missing: {missing}")

        client = app.test_client()
        failures: list[str] = []
        for filename in sorted(_literal_static_files()):
            response = client.get(f"/static/{filename}")
            if response.status_code != 200 or not response.data:
                failures.append(f"/static/{filename}: HTTP {response.status_code}, {len(response.data)} bytes")
        self.assertEqual([], failures, "Static asset delivery failures:\n" + "\n".join(failures))

    def test_service_worker_root_endpoint_is_available(self):
        response = app.test_client().get("/service-worker.js")
        self.assertEqual(200, response.status_code)
        self.assertGreater(len(response.data), 100)
        self.assertIn("javascript", response.content_type.casefold())


class FrontendNavigationSmokeTests(unittest.TestCase):
    def test_guided_boot_profile_editor_renders_values_and_safe_validation_error(self):
        from simpleoffice_network_boot import save_boot_settings
        path = Path(self.temp.name) / "mini-profile.json"
        with patch.dict(os.environ, {"SIMPLEOFFICE_MINI_SERVICES_CONFIG": str(path)}):
            save_boot_settings({"profiles": [{"id": "rescue", "label": "Rescue", "kernel": "vmlinuz"}]}, path)
            response = self.client.get("/admin/mini-services/network-boot?profile=rescue")
            self.assertEqual(200, response.status_code)
            body = response.get_data(as_text=True)
            self.assertIn('name="original_id" value="rescue"', body)
            self.assertIn('value="vmlinuz"', body)
            response = self.client.post("/admin/mini-services/network-boot/profiles", data={"id": "new", "mode": "kernel", "kernel": "../../outside", "label": '<script>alert(1)</script>'})
            self.assertEqual(400, response.status_code)
            body = response.get_data(as_text=True)
            self.assertIn("&lt;script&gt;", body)
            self.assertNotIn("<script>alert(1)</script>", body)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE",
                "DOCUMENT_ROOT",
                "TESTING",
                "PROPAGATE_EXCEPTIONS",
                "TEST_CSRF_PROTECTION",
                "CONTEXTFLOW_ENABLED",
            )
        }
        app.config.update(
            TESTING=True,
            PROPAGATE_EXCEPTIONS=False,
            TEST_CSRF_PROTECTION=False,
            CONTEXTFLOW_ENABLED=True,
            DATABASE=str(Path(self.temp.name) / "frontend-smoke.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        Path(app.config["DOCUMENT_ROOT"]).mkdir(parents=True, exist_ok=True)
        SettingsStore(Path(app.config["DOCUMENT_ROOT"])).save(
            {"v3": {"v3.android_offline": True}}, "v3-frontend-contract",
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("frontend-smoke-admin", generate_password_hash("frontend-smoke-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "frontend-smoke-admin", "password": "frontend-smoke-password"},
        )
        self.assertLess(response.status_code, 400)

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    @staticmethod
    def _page_failure(endpoint: str, rule: str, response) -> str | None:
        mimetype = (response.mimetype or "").casefold()
        is_text = mimetype.startswith("text/") or mimetype in {
            "application/javascript",
            "application/json",
            "application/xml",
            "application/xhtml+xml",
        }
        body: str | None = None
        if is_text:
            try:
                body = response.get_data(as_text=True)
            except UnicodeDecodeError as exc:
                return f"{endpoint} -> {rule}: invalid UTF-8 {mimetype or 'text'} response: {exc}"

        if response.status_code == 404 or response.status_code >= 500:
            if body is not None:
                excerpt = body[:240].replace("\n", " ")
            else:
                excerpt = f"{len(response.data)} bytes, {mimetype or 'unknown content type'}"
            return f"{endpoint} -> {rule}: HTTP {response.status_code}: {excerpt}"
        if response.status_code == 200 and mimetype == "text/html":
            assert body is not None
            folded = body.casefold()
            if "<html" not in folded or "<body" not in folded or len(body.strip()) < 300:
                return f"{endpoint} -> {rule}: empty/incomplete HTML shell ({len(body)} chars)"
        return None

    def test_root_resolves_to_nonempty_html_interface(self):
        response = self.client.get("/", follow_redirects=True)
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("<html", body.casefold())
        self.assertIn("<body", body.casefold())
        self.assertGreater(len(body.strip()), 500)

    def test_invalid_mini_network_form_renders_and_escapes_retained_input(self):
        raw = '<script>alert("test")</script>{'
        response = self.client.post("/admin/mini-services/save", data={
            "reservations": raw, "network": "invalid-network", "domain": "keep.home.arpa",
        })
        self.assertEqual(400, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("invalid-network", body)
        self.assertIn("keep.home.arpa", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn(raw, body)
        self.assertIn('role="alert"', body)
        self.assertIn('for="mini-reservations"', body)
        self.assertIn('id="mini-reservations"', body)

    def test_quick_share_temporary_history_pin_and_owner_access(self):
        self.client.get("/quick-share")
        with app.app_context():
            before = database.get_db().execute("SELECT COUNT(*) FROM quick_share_item").fetchone()[0]
        temporary = self.client.post("/quick-share", data={
            "kind": "text", "text": "Notiz fürs Handy", "save_mode": "temporary",
        })
        self.assertEqual(200, temporary.status_code)
        self.assertIn("Temporary only; this QR is not stored", temporary.get_data(as_text=True))
        self.assertIn("Nothing saved yet", temporary.get_data(as_text=True))
        self.assertIn("data:image/svg+xml;base64,", temporary.get_data(as_text=True))
        with app.app_context():
            after = database.get_db().execute("SELECT COUNT(*) FROM quick_share_item").fetchone()[0]
        self.assertEqual(before, after, "Temporary QR must not be persisted")

        saved = self.client.post("/quick-share", data={
            "kind": "link", "title": "Startseite", "url": "https://example.org/start?q=1",
            "save_mode": "history",
        }, follow_redirects=True)
        self.assertEqual(200, saved.status_code)
        saved_qr = re.search(r'src="/quick-share/([^"/]+)\.svg"', saved.get_data(as_text=True))
        self.assertIsNotNone(saved_qr)
        item_id = saved_qr.group(1)
        with app.app_context():
            row = database.get_db().execute(
                "SELECT title,payload,pinned,expires_at FROM quick_share_item WHERE item_id=?", (item_id,),
            ).fetchone()
            self.assertNotIn("Startseite", row["title"])
            self.assertNotIn("https://example.org/start", row["payload"])
            self.assertEqual(0, row["pinned"])
        qr = self.client.get(f"/quick-share/{item_id}.svg")
        self.assertEqual(200, qr.status_code)
        self.assertEqual("image/svg+xml", qr.mimetype)
        self.assertIn("<svg", qr.get_data(as_text=True))
        self.assertIn("no-store", qr.headers["Cache-Control"])

        pinned = self.client.post(f"/quick-share/{item_id}/pin", follow_redirects=True)
        self.assertEqual(200, pinned.status_code)
        self.assertIn("Pinned", pinned.get_data(as_text=True))
        with app.app_context():
            row = database.get_db().execute(
                "SELECT pinned,expires_at FROM quick_share_item WHERE item_id=?", (item_id,),
            ).fetchone()
            self.assertEqual((1, None), (row["pinned"], row["expires_at"]))

        with app.app_context():
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,is_disabled,created_at,updated_at) "
                "VALUES (?,?,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("quick-share-other", generate_password_hash("quick-share-other-password")),
            )
            db.commit()
        other = app.test_client()
        login = other.post("/auth/login", data={
            "username": "quick-share-other", "password": "quick-share-other-password",
        })
        self.assertLess(login.status_code, 400)
        self.assertEqual(404, other.get(f"/quick-share/{item_id}.svg").status_code)
        self.assertEqual(302, self.client.post(f"/quick-share/{item_id}/delete").status_code)
        self.assertEqual(404, self.client.get(f"/quick-share/{item_id}.svg").status_code)

    def test_quick_share_event_ics_and_unsafe_link_validation(self):
        unsafe = self.client.post("/quick-share", data={
            "kind": "link", "url": "javascript:alert(1)", "save_mode": "history",
        })
        self.assertEqual(400, unsafe.status_code)
        self.assertIn("https://", unsafe.get_data(as_text=True))

        created = self.client.post("/quick-share", data={
            "kind": "event", "title": "Review",
            "event_title": "Release, Review", "start": "2030-03-21T10:30",
            "end": "2030-03-21T11:30", "location": "Raum; 2",
            "description": "Punkt eins\nPunkt zwei", "save_mode": "history",
        }, follow_redirects=True)
        self.assertEqual(200, created.status_code)
        selected = re.search(r'src="/quick-share/([^"/]+)\.svg"', created.get_data(as_text=True))
        self.assertIsNotNone(selected)
        response = self.client.get(f"/quick-share/{selected.group(1)}.ics")
        self.assertEqual(200, response.status_code)
        self.assertIn("text/calendar", response.content_type)
        self.assertIn("SUMMARY:Release\\, Review", response.get_data(as_text=True))
        self.assertIn("LOCATION:Raum\\; 2", response.get_data(as_text=True))
        self.assertIn("DESCRIPTION:Punkt eins\\nPunkt zwei", response.get_data(as_text=True))
        self.assertEqual("no-store", response.headers["Cache-Control"].split(",")[-1].strip())

    def test_quick_share_create_is_csrf_protected(self):
        self.client.get("/quick-share")
        with self.client.session_transaction() as session:
            csrf = session.get("_csrf_token")
        app.config["TEST_CSRF_PROTECTION"] = True
        try:
            denied = self.client.post("/quick-share", data={
                "kind": "text", "text": "should not be stored", "save_mode": "history",
            })
            self.assertEqual(403, denied.status_code)
            accepted = self.client.post("/quick-share", data={
                "_csrf_token": csrf, "kind": "text", "text": "CSRF protected",
                "save_mode": "history",
            })
            self.assertEqual(302, accepted.status_code)
        finally:
            app.config["TEST_CSRF_PROTECTION"] = False

    def test_bookmark_firefox_import_preview_is_non_mutating_and_confirm_is_csrf_protected(self):
        from io import BytesIO

        self.client.post("/bookmarks/add", data={
            "title": "Existing", "url": "https://example.org/existing", "folder": "Work",
        })
        firefox_html = b'''<!DOCTYPE NETSCAPE-Bookmark-file-1>
        <DL><p><DT><H3>Work</H3><DL><p>
        <DT><A HREF="https://example.org/new?x=1&amp;y=2" TAGS="project">&lt;img src=x onerror=alert(1)&gt;</A>
        <DT><A HREF="https://example.org/existing">Existing duplicate</A>
        <DT><A HREF="javascript:alert(1)">Unsafe</A>
        <DT><A HREF="data:text/html,unsafe">Also unsafe</A>
        </DL><p></DL><p>'''
        imported = self.client.post("/bookmarks/import", data={
            "file": (BytesIO(firefox_html), "bookmarks.html"),
        })
        self.assertEqual(302, imported.status_code)
        preview = self.client.get("/bookmarks/import-preview")
        self.assertEqual(200, preview.status_code)
        body = preview.get_data(as_text=True)
        self.assertIn(">1</div><div>neu</div>", body)
        self.assertIn(">1</div><div>Duplikate", body)
        self.assertIn(">2</div><div>ungültige", body)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", body)
        self.assertNotIn("<img src=x onerror=alert(1)>", body)
        with app.app_context():
            db = database.get_db()
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM browser_bookmark").fetchone()[0])

        with self.client.session_transaction() as session:
            csrf = session.get("_csrf_token")
        app.config["TEST_CSRF_PROTECTION"] = True
        try:
            denied = self.client.post("/bookmarks/import-confirm")
            self.assertEqual(403, denied.status_code)
            with app.app_context():
                self.assertEqual(1, database.get_db().execute("SELECT COUNT(*) FROM browser_bookmark").fetchone()[0])
            accepted = self.client.post("/bookmarks/import-confirm", data={"_csrf_token": csrf})
            self.assertEqual(302, accepted.status_code)
        finally:
            app.config["TEST_CSRF_PROTECTION"] = False

        with app.app_context():
            rows = database.get_db().execute("SELECT title,url,folder FROM browser_bookmark ORDER BY title").fetchall()
            self.assertEqual(2, len(rows))
            added = next(row for row in rows if row["url"].endswith("new?x=1&y=2"))
            self.assertEqual("Work", added["folder"])
            self.assertIn("<img", added["title"])

        exported = self.client.get("/bookmarks/export")
        self.assertEqual(200, exported.status_code)
        self.assertIn(b"<H3>Work</H3>", exported.data)
        self.assertIn(b"&lt;img src=x onerror=alert(1)&gt;", exported.data)
        with app.app_context():
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,is_disabled,created_at,updated_at) "
                "VALUES (?,?,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("bookmark-roundtrip", generate_password_hash("bookmark-roundtrip-password")),
            )
            db.commit()
        roundtrip = app.test_client()
        roundtrip.post("/auth/login", data={
            "username": "bookmark-roundtrip", "password": "bookmark-roundtrip-password",
        })
        roundtrip.post("/bookmarks/import", data={
            "file": (BytesIO(exported.data), "roundtrip.html"),
        })
        roundtrip_preview = roundtrip.get("/bookmarks/import-preview")
        self.assertIn(">2</div><div>neu</div>", roundtrip_preview.get_data(as_text=True))
        self.assertEqual(302, roundtrip.post("/bookmarks/import-confirm").status_code)
        with app.app_context():
            db = database.get_db()
            roundtrip_user = db.execute("SELECT id FROM user WHERE username=?", ("bookmark-roundtrip",)).fetchone()
            imported_folders = db.execute(
                "SELECT DISTINCT folder FROM browser_bookmark WHERE user_id=?", (roundtrip_user["id"],),
            ).fetchall()
            self.assertEqual({"Work"}, {row["folder"] for row in imported_folders})

    def test_bookmarks_are_owner_scoped_and_reject_unsafe_urls(self):
        unsafe = self.client.post("/bookmarks/add", data={
            "title": "Unsafe", "url": "javascript:alert(1)",
        })
        self.assertEqual(302, unsafe.status_code)
        with app.app_context():
            db = database.get_db()
            count = db.execute("SELECT COUNT(*) FROM browser_bookmark").fetchone()[0]
            self.assertEqual(0, count)
            db.execute(
                "INSERT INTO browser_bookmark(id,user_id,title,url) VALUES(?,?,?,?)",
                ("owner-only-bookmark", 1, "Private bookmark", "https://example.org/private"),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,is_disabled,created_at,updated_at) "
                "VALUES (?,?,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("bookmark-other", generate_password_hash("bookmark-other-password")),
            )
            db.commit()
        other = app.test_client()
        login = other.post("/auth/login", data={
            "username": "bookmark-other", "password": "bookmark-other-password",
        })
        self.assertLess(login.status_code, 400)
        listing = other.get("/bookmarks/")
        self.assertEqual(200, listing.status_code)
        self.assertNotIn("Private bookmark", listing.get_data(as_text=True))
        self.assertEqual(404, other.post("/bookmarks/owner-only-bookmark/delete").status_code)

    def test_all_parameterless_template_linked_get_endpoints_render(self):
        """Smoke-test every directly callable GET page advertised anywhere in templates."""
        failures: list[str] = []
        tested: list[str] = []
        for endpoint in sorted(_all_template_endpoints()):
            if (
                endpoint.startswith(".")
                or endpoint == "static"
                or endpoint in STATE_CHANGING_GET_ENDPOINTS
                or endpoint in NON_PAGE_GET_ENDPOINTS
            ):
                continue
            rule = _parameterless_get_rule(endpoint)
            if rule is None:
                continue
            response = self.client.get(rule.rule, follow_redirects=True)
            tested.append(f"{endpoint} -> {rule.rule} [{response.status_code}]")
            failure = self._page_failure(endpoint, rule.rule, response)
            response.close()
            if failure:
                failures.append(failure)

        self.assertGreater(len(tested), 25, f"Too few template-linked GET endpoints tested: {tested}")
        self.assertEqual([], failures, "Template-linked page smoke failures:\n" + "\n".join(failures))

    def test_all_parameterless_get_pages_in_main_navigation_render(self):
        """Render every directly reachable GET page exposed by the global navbar."""
        nav_endpoints = _literal_endpoints(NAV_TEMPLATE)
        failures: list[str] = []
        tested: list[str] = []

        for endpoint in sorted(nav_endpoints):
            if endpoint in STATE_CHANGING_GET_ENDPOINTS:
                continue
            rule = _parameterless_get_rule(endpoint)
            if rule is None:
                continue
            response = self.client.get(rule.rule, follow_redirects=True)
            tested.append(f"{endpoint} -> {rule.rule} [{response.status_code}, {len(response.data)} bytes]")
            failure = self._page_failure(endpoint, rule.rule, response)
            if failure:
                failures.append(failure)
                continue
            if response.status_code == 200 and response.mimetype == "text/html":
                body = response.get_data(as_text=True)
                if 'id="main-navigation"' not in body:
                    failures.append(f"{endpoint} -> {rule.rule}: global navigation missing from rendered page")
            response.close()

        self.assertGreater(len(tested), 10, f"Too few navigation pages tested: {tested}")
        self.assertEqual([], failures, "Navigation page smoke failures:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
