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
NAV_TEMPLATE = TEMPLATES / "documents" / "nav.html"
URL_FOR = re.compile(r"url_for\(\s*['\"]([^'\"]+)['\"]")


def _literal_endpoints(path: Path) -> set[str]:
    return set(URL_FOR.findall(path.read_text(encoding="utf-8")))


def _all_template_endpoints() -> set[str]:
    endpoints: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        endpoints.update(_literal_endpoints(path))
    return endpoints


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

            # Prefer the shortest canonical rule if aliases exist.
            rule = min(rules, key=lambda item: (len(item.rule), item.rule))
            response = self.client.get(rule.rule, follow_redirects=False)
            tested.append(f"{endpoint} -> {rule.rule} [{response.status_code}]")
            if response.status_code == 404 or response.status_code >= 500:
                excerpt = response.get_data(as_text=True)[:240].replace("\n", " ")
                failures.append(
                    f"{endpoint} -> {rule.rule}: HTTP {response.status_code}: {excerpt}"
                )

        self.assertGreater(len(tested), 10, f"Too few navigation pages tested: {tested}")
        self.assertEqual([], failures, "Navigation page smoke failures:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
