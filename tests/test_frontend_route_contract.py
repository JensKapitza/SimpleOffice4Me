from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database


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
            )
        }
        app.config.update(
            TESTING=True,
            PROPAGATE_EXCEPTIONS=False,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(Path(self.temp.name) / "frontend-smoke.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        Path(app.config["DOCUMENT_ROOT"]).mkdir(parents=True, exist_ok=True)
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

    def test_root_resolves_to_nonempty_html_interface(self):
        response = self.client.get("/", follow_redirects=True)
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("<html", body.casefold())
        self.assertIn("<body", body.casefold())
        self.assertGreater(len(body.strip()), 500)

    def test_all_parameterless_get_pages_in_main_navigation_render(self):
        """Render every directly reachable GET page exposed by the global navbar."""
        nav_endpoints = _literal_endpoints(NAV_TEMPLATE)
        failures: list[str] = []
        tested: list[str] = []

        for endpoint in sorted(nav_endpoints):
            rules = [
                rule for rule in app.url_map.iter_rules(endpoint)
                if "GET" in rule.methods and not rule.arguments
            ]
            if not rules:
                continue

            rule = min(rules, key=lambda item: (len(item.rule), item.rule))
            response = self.client.get(rule.rule, follow_redirects=True)
            body = response.get_data(as_text=True)
            tested.append(f"{endpoint} -> {rule.rule} [{response.status_code}, {len(body)} chars]")
            if response.status_code == 404 or response.status_code >= 500:
                excerpt = body[:240].replace("\n", " ")
                failures.append(
                    f"{endpoint} -> {rule.rule}: HTTP {response.status_code}: {excerpt}"
                )
                continue
            if response.status_code == 200 and response.content_type.startswith("text/html"):
                folded = body.casefold()
                if "<html" not in folded or "<body" not in folded or len(body.strip()) < 300:
                    failures.append(
                        f"{endpoint} -> {rule.rule}: empty/incomplete HTML shell ({len(body)} chars)"
                    )

        self.assertGreater(len(tested), 10, f"Too few navigation pages tested: {tested}")
        self.assertEqual([], failures, "Navigation page smoke failures:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
