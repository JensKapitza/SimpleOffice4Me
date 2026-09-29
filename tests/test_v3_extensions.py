from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.v3_extensions import (
    API_VERSION,
    CircuitBreaker,
    ExtensionCallError,
    ExtensionDenied,
    ExtensionManifest,
    ExtensionManifestStore,
    ExtensionRegistry,
    ExtensionStateStore,
    ExtensionUnavailable,
    HttpExtensionTransport,
    external_search_hits,
)


def manifest(
    extension_id: str = "example.search",
    *,
    api_version: int = API_VERSION,
    endpoint: str = "http://127.0.0.1:8765/simpleoffice-extension",
):
    return {
        "id": extension_id,
        "name": "Example Search",
        "version": "1.0.0",
        "api_version": api_version,
        "extension_points": ["search_provider", "health_check"],
        "capabilities": ["search.read", "health.read"],
        "transport": {
            "type": "http",
            "endpoint": endpoint,
            "timeout_seconds": 1,
        },
    }


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.fail = set()

    def invoke(self, extension, point, operation, payload):
        self.calls.append(
            (extension.extension_id, point, operation, dict(payload))
        )
        if extension.extension_id in self.fail:
            raise ExtensionCallError("down")
        if point == "search_provider":
            return {
                "results": [
                    {
                        "title": "Result",
                        "subtitle": "External",
                        "kind": "Extension",
                        "url": "/documents/",
                        "ref_id": "r1",
                    }
                ]
            }
        return {"status": "ok"}


class V3ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifests = ExtensionManifestStore(self.root)
        self.states = ExtensionStateStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_too_new_api_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported extension API version"):
            ExtensionManifest.from_dict(
                manifest(api_version=API_VERSION + 1),
                external=True,
            )

    def test_manifest_rejects_inline_secrets_and_dynamic_code_transport(self):
        raw = manifest()
        raw["api_token"] = "do-not-store"
        with self.assertRaisesRegex(ValueError, "must not contain a secret"):
            ExtensionManifest.from_dict(raw, external=True)

        raw = manifest()
        raw["transport"] = {"type": "python", "module": "unsafe.module"}
        with self.assertRaisesRegex(ValueError, "only the isolated HTTP transport"):
            ExtensionManifest.from_dict(raw, external=True)

    def test_external_http_requires_https_except_loopback(self):
        raw = manifest(endpoint="http://example.test/plugin")
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            ExtensionManifest.from_dict(raw, external=True)

    def test_registered_manifest_is_disabled_and_unapproved_by_default(self):
        parsed = self.manifests.save(manifest())
        state = self.states.state(parsed.extension_id)
        self.assertFalse(state["enabled"])
        self.assertEqual([], state["approved_capabilities"])

    def test_unapproved_capability_is_denied(self):
        parsed = self.manifests.save(manifest())
        self.states.set(parsed.extension_id, enabled=True)
        registry = ExtensionRegistry(
            self.root,
            transport=FakeTransport(),
        )
        with patch.dict(
            "os.environ",
            {"SIMPLEOFFICE_V3_EXTENSIONS_ENABLED": "1"},
        ):
            with self.assertRaises(ExtensionDenied):
                registry.invoke(
                    parsed.extension_id,
                    "search_provider",
                    "search",
                    {"query": "x"},
                )

    def test_approved_extension_can_be_enabled_and_disabled_without_core_patch(self):
        parsed = self.manifests.save(manifest())
        transport = FakeTransport()
        registry = ExtensionRegistry(self.root, transport=transport)
        self.states.set(
            parsed.extension_id,
            enabled=True,
            approved_capabilities=["search.read"],
        )
        with patch.dict(
            "os.environ",
            {"SIMPLEOFFICE_V3_EXTENSIONS_ENABLED": "1"},
        ):
            result = registry.invoke(
                parsed.extension_id,
                "search_provider",
                "search",
                {"query": "x"},
            )
        self.assertEqual("Result", result["results"][0]["title"])
        self.assertEqual(1, len(transport.calls))

        self.states.set(parsed.extension_id, enabled=False)
        with patch.dict(
            "os.environ",
            {"SIMPLEOFFICE_V3_EXTENSIONS_ENABLED": "1"},
        ):
            with self.assertRaises(ExtensionUnavailable):
                registry.invoke(
                    parsed.extension_id,
                    "search_provider",
                    "search",
                    {"query": "x"},
                )

    def test_failure_of_one_extension_does_not_block_another(self):
        first = self.manifests.save(manifest("example.first"))
        second = self.manifests.save(manifest("example.second"))
        for item in (first, second):
            self.states.set(
                item.extension_id,
                enabled=True,
                approved_capabilities=["search.read"],
            )
        transport = FakeTransport()
        transport.fail.add(first.extension_id)
        registry = ExtensionRegistry(self.root, transport=transport)
        with patch.dict(
            "os.environ",
            {"SIMPLEOFFICE_V3_EXTENSIONS_ENABLED": "1"},
        ):
            rows, failures = registry.invoke_all(
                "search_provider",
                "search",
                {"query": "x"},
            )
        self.assertEqual(["example.first"], failures)
        self.assertEqual(
            ["example.second"],
            [row["extension_id"] for row in rows],
        )

    def test_builtin_registration_is_explicit_not_dynamic_import(self):
        registry = ExtensionRegistry(self.root, transport=FakeTransport())
        registry.register_builtin(
            {
                "id": "builtin.example",
                "name": "Built-in Example",
                "version": "1.0.0",
                "api_version": 1,
                "extension_points": ["health_check"],
                "capabilities": ["health.read"],
            },
            {"health_check": lambda operation, payload: {"status": "ok"}},
            approved_capabilities={"health.read"},
        )
        with patch.dict(
            "os.environ",
            {"SIMPLEOFFICE_V3_EXTENSIONS_ENABLED": "1"},
        ):
            result = registry.invoke(
                "builtin.example",
                "health_check",
                "check",
                {},
            )
        self.assertEqual("ok", result["status"])

    def test_circuit_breaker_opens_after_bounded_failures(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        self.assertTrue(breaker.allow("x"))
        breaker.failure("x")
        self.assertTrue(breaker.allow("x"))
        breaker.failure("x")
        self.assertFalse(breaker.allow("x"))
        breaker.success("x")
        self.assertTrue(breaker.allow("x"))

    def test_non_allowlisted_remote_host_is_rejected_before_network_io(self):
        parsed = ExtensionManifest.from_dict(
            manifest(endpoint="https://extensions.example.test/api"),
            external=True,
        )
        transport = HttpExtensionTransport(allow_hosts=set())
        with self.assertRaises(ExtensionDenied):
            transport.invoke(
                parsed,
                "search_provider",
                "search",
                {"query": "x"},
            )

    def test_external_search_does_not_send_local_username(self):
        parsed = self.manifests.save(manifest())
        self.states.set(
            parsed.extension_id,
            enabled=True,
            approved_capabilities=["search.read"],
        )
        transport = FakeTransport()
        with patch(
            "app.v3_extensions.ExtensionRegistry",
            lambda root: ExtensionRegistry(root, transport=transport),
        ), patch.dict(
            "os.environ",
            {"SIMPLEOFFICE_V3_EXTENSIONS_ENABLED": "1"},
        ):
            rows, failures = external_search_hits(
                self.root,
                "kunde",
                actor="private-username",
                limit=5,
            )
        self.assertEqual([], failures)
        self.assertEqual("Result", rows[0]["title"])
        sent = transport.calls[0][3]
        self.assertNotIn("actor", sent)
        self.assertNotIn("private-username", json.dumps(sent))
        self.assertTrue(sent["authenticated"])


if __name__ == "__main__":
    unittest.main()
