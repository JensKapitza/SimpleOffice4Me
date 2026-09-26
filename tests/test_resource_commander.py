from pathlib import Path
import tempfile
import unittest

from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from app.resource_local import LocalResourceProvider
from app.resource_provider import ProviderError, ResourceEntry
from app.resource_registry import ResourceRegistry
from app.resource_smartview import SmartViewProvider
from app.webdav_smart_mount import _href, _virtual_name


class LocalResourceProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.provider = LocalResourceProvider(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_and_open_regular_file(self):
        (self.root / "example.txt").write_text("hello", encoding="utf-8")
        entries = list(self.provider.list())
        self.assertEqual([entry.name for entry in entries], ["example.txt"])
        with self.provider.open("example.txt") as stream:
            self.assertEqual(stream.read(), b"hello")

    def test_path_escape_is_rejected(self):
        with self.assertRaises(ProviderError):
            self.provider.stat("../outside.txt")

    def test_symlink_is_not_listed(self):
        target = self.root / "target.txt"
        target.write_text("x", encoding="utf-8")
        try:
            (self.root / "link.txt").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertNotIn("link.txt", [entry.name for entry in self.provider.list()])


class ResourceRegistryTests(unittest.TestCase):
    def test_federation_descriptor_exposes_trust_and_policy_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FederationStore(tmp)
            store.save_peer(
                "peer-one",
                "Peer One",
                "https://peer.example",
                "",
                {"documents": {"receive": True, "send": False}},
                True,
            )
            FederationTrustStore(tmp).set_trust(
                "peer-one",
                "NORMAL",
                "VERIFIED_ONE_WAY",
                "DIRECT_ONLY",
                0,
            )

            registry = ResourceRegistry(tmp, b"test-master-key-long-enough", "tester")
            descriptor = next(
                item for item in registry.descriptors()
                if item["provider_id"] == "federation:peer-one"
            )

            self.assertEqual("federation", descriptor["kind"])
            self.assertTrue(descriptor["capabilities"]["read"])
            self.assertFalse(descriptor["capabilities"]["write"])
            self.assertEqual("peer-one", descriptor["federation"]["peer_id"])
            self.assertEqual("NORMAL", descriptor["federation"]["trust_level"])
            self.assertEqual(
                "VERIFIED_ONE_WAY",
                descriptor["federation"]["verification_state"],
            )


class SmartViewProviderTests(unittest.TestCase):
    def test_self_smartview_exposes_virtual_collections(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = SmartViewProvider(tmp)
            names = {entry.name for entry in provider.list()}
            self.assertIn("Dokumente", names)
            self.assertIn("Eingang", names)
            self.assertIn("Duplikate", names)
            self.assertIn("Archiv", names)

    def test_smart_mount_href_is_stable_and_encoded(self):
        self.assertEqual("/webdav/smart/jens/", _href("jens", collection=True))
        self.assertEqual(
            "/webdav/smart/jens/invoices/Rechnung%202026.pdf",
            _href("jens", "invoices/Rechnung 2026.pdf"),
        )

    def test_smart_mount_disambiguates_duplicate_file_names(self):
        first = ResourceEntry(resource_id="Kunde-A/Rechnung.pdf", name="Rechnung.pdf", provider="smart")
        second = ResourceEntry(resource_id="Kunde-B/Rechnung.pdf", name="Rechnung.pdf", provider="smart")
        first_name = _virtual_name(first, True)
        second_name = _virtual_name(second, True)
        self.assertNotEqual(first_name, second_name)
        self.assertTrue(first_name.startswith("Rechnung ["))
        self.assertTrue(first_name.endswith("].pdf"))


if __name__ == "__main__":
    unittest.main()
