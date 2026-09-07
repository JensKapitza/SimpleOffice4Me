import tempfile
import unittest
from pathlib import Path

from app.vault_packages import (
    create_vault_package,
    extract_vault_package,
    federation_chunk_plan,
    package_capabilities,
)


class VaultPackageTests(unittest.TestCase):
    def test_password_package_hides_content_and_filename_and_extracts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "steuerbescheid-geheim.txt"
            source.write_text("hochvertraulich-4711", encoding="utf-8")
            package = root / "backup.sovp"
            result = create_vault_package([source], package, password="Sehr langes Paket Passwort 2026!")
            raw = package.read_bytes()
            self.assertNotIn(b"hochvertraulich-4711", raw)
            self.assertNotIn(b"steuerbescheid-geheim.txt", raw)
            self.assertGreaterEqual(result["chunk_count"], 1)
            destination = root / "restore"
            extracted = extract_vault_package(package, destination, password="Sehr langes Paket Passwort 2026!")
            self.assertEqual(1, extracted["files"])
            self.assertEqual("hochvertraulich-4711", (destination / source.name).read_text(encoding="utf-8"))

    def test_wrong_password_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "secret.txt"
            source.write_text("secret", encoding="utf-8")
            package = root / "secret.sovp"
            create_vault_package([source], package, password="Richtiges langes Passwort 2026!")
            with self.assertRaises(ValueError):
                extract_vault_package(package, root / "bad", password="Falsches aber langes Passwort 2026!")

    def test_generated_recovery_key_restores_without_password(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "photo.jpg"
            source.write_bytes(b"fake-photo-data")
            package = root / "photo.sovp"
            created = create_vault_package([source], package)
            self.assertTrue(created["recovery_key"])
            destination = root / "restore"
            extract_vault_package(package, destination, recovery_key=created["recovery_key"])
            self.assertEqual(b"fake-photo-data", (destination / "photo.jpg").read_bytes())

    def test_tampered_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "data.bin"
            source.write_bytes(b"x" * 100000)
            package = root / "data.sovp"
            created = create_vault_package([source], package)
            raw = bytearray(package.read_bytes())
            raw[-20] ^= 0x01
            package.write_bytes(bytes(raw))
            with self.assertRaises(ValueError):
                extract_vault_package(package, root / "restore", recovery_key=created["recovery_key"])

    def test_round_robin_chunk_plan_uses_partial_peer_sets(self):
        plan = federation_chunk_plan(6, ["a", "b", "c"], replicas=2)
        self.assertEqual([0, 2, 3, 5], plan["a"])
        self.assertEqual([0, 1, 3, 4], plan["b"])
        self.assertEqual([1, 2, 4, 5], plan["c"])
        self.assertTrue(all(len(chunks) < 6 for chunks in plan.values()))

    def test_capabilities_are_encrypt_first(self):
        caps = package_capabilities()
        self.assertTrue(caps["encrypt_before_federation"])
        self.assertTrue(caps["encrypted_metadata"])
        self.assertTrue(caps["federation_partial_storage"])


if __name__ == "__main__":
    unittest.main()
