import unittest

from app import app
from tools.generate_sbom import build_sbom


class SecurityBaselineTests(unittest.TestCase):
    def test_security_headers_are_present(self):
        response = app.test_client().get("/")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        self.assertIn("camera=()", response.headers["Permissions-Policy"])

    def test_sbom_contains_application_dependencies(self):
        sbom = build_sbom()
        self.assertEqual("CycloneDX", sbom["bomFormat"])
        self.assertTrue(any(item["name"].casefold() == "flask" for item in sbom["components"]))
