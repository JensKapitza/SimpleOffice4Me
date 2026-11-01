"""Lookup-only document resolution plus internal auto-scan.

``get_document`` treats references as opaque catalog keys (ids read
metadata, paths only query the scan index, nothing unregistered is
touched). ``scan_and_get_document`` keeps auto-scan for app-produced
paths. Tests prove lookups, traversal rejection and scan behavior.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore


def _seed(root: Path, name: str = "note.txt", body: bytes = b"hello") -> Path:
    target = Path(root) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return target


def _store(root: Path) -> DocumentStore:
    store = DocumentStore(root)
    store.scan()
    return store


class LookupTest(unittest.TestCase):
    def test_id_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = _store(root)
            first = store.get_document(root / "note.txt")
            again = store.get_document(first["document_id"])
            self.assertEqual(first["document_id"], again["document_id"])

    def test_relative_lookup_native_and_posix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root, "sub/deep.txt")
            store = _store(root)
            native = os.path.join("sub", "deep.txt")
            self.assertEqual(
                store.get_document(native)["document_id"],
                store.get_document("sub/deep.txt")["document_id"],
            )

    def test_absolute_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = _seed(root)
            store = _store(root)
            self.assertEqual(
                store.get_document(target)["document_id"],
                store.get_document(str(target))["document_id"],
            )

    def test_unregistered_and_unknown_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = _store(root)
            _seed(root, "fresh.txt")
            with self.assertRaises(ValueError):
                store.get_document("fresh.txt")
            with self.assertRaises(ValueError):
                store.get_document(root / "fresh.txt")
            with self.assertRaises(ValueError):
                store.get_document("no-such-id")

    def test_traversal_and_garbage_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = _store(root)
            for bad in ("", ".", "..", "../x", "..\\..\\x", "a/../../b",
                        "/etc/passwd", "C:\\Windows\\win.ini", "a\x00b"):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        store.get_document(bad)

    def test_outside_file_is_not_leaked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = _store(root)
            with tempfile.TemporaryDirectory() as other:
                secret = Path(other) / "secret.txt"
                secret.write_bytes(b"top-secret")
                with self.assertRaises(ValueError):
                    store.get_document(secret)
                with self.assertRaises(ValueError):
                    store.get_document(str(secret))

    def test_last_path_is_relativized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root, "sub/deep.txt")
            store = _store(root)
            metadata = store.get_document("sub/deep.txt")
            self.assertFalse(os.path.isabs(str(metadata.get("last_path", ""))))

    def test_error_messages_distinguish_miss_and_garbage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = _store(root)
            with self.assertRaisesRegex(ValueError, "unknown document: no-such-id"):
                store.get_document("no-such-id")
            with self.assertRaisesRegex(ValueError, "unknown document"):
                store.get_document("missing/file.txt")
            for bad in ("", "a b", "x" * 250):
                with self.subTest(bad=bad[:8]):
                    with self.assertRaisesRegex(ValueError, "invalid document reference"):
                        store.get_document(bad)


class LookupKeysTest(unittest.TestCase):
    def test_absolute_strips_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DocumentStore(root)
            keys = store._lookup_keys(str(root / "sub" / "f.txt"))
            self.assertIn(os.path.join("sub", "f.txt"), keys)

    def test_foreign_absolute_has_no_remainder(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DocumentStore(root)
            with tempfile.TemporaryDirectory() as other:
                keys = store._lookup_keys(str(Path(other) / "f.txt"))
            self.assertNotIn("f.txt", keys)

    def test_keys_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DocumentStore(Path(temp))
            keys = store._lookup_keys("plain.txt")
            self.assertEqual(len(keys), len(set(keys)))


class ScanMethodTest(unittest.TestCase):
    def test_scan_registers_fresh_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = _seed(root)
            store = DocumentStore(root)
            metadata = store.scan_and_get_document(target)
            self.assertTrue(metadata.get("document_id"))
            self.assertEqual(
                metadata["document_id"],
                store.get_document("note.txt")["document_id"],
            )

    def test_scan_accepts_relative_fresh_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root, "sub/new.txt")
            store = DocumentStore(root)
            metadata = store.scan_and_get_document(os.path.join("sub", "new.txt"))
            self.assertTrue(metadata.get("document_id"))

    def test_scan_falls_back_to_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = _store(root)
            document_id = store.get_document("note.txt")["document_id"]
            self.assertEqual(
                document_id,
                store.scan_and_get_document(document_id)["document_id"],
            )
            with self.assertRaises(ValueError):
                store.scan_and_get_document("missing.txt")

    def test_scan_skips_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = _seed(root, "real.txt")
            link = root / "link.txt"
            try:
                link.symlink_to(real)
            except OSError:
                self.skipTest("symlinks need privileges")
            store = DocumentStore(root)
            with self.assertRaises(ValueError):
                store.scan_and_get_document(link)

    def test_scan_outside_root_keeps_external_marker(self) -> None:
        # Intentional: external archives register outside files with the
        # "[external]" key (see relative() and its ~12 consumers); the
        # lookup-only get_document never scans, so user input cannot
        # trigger outside registration.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = DocumentStore(root)
            with tempfile.TemporaryDirectory() as other:
                secret = Path(other) / "outside.txt"
                secret.write_bytes(b"x")
                metadata = store.scan_and_get_document(secret)
            self.assertTrue(metadata.get("document_id"))


class HelperTest(unittest.TestCase):
    def test_self_heal_registers_unregistered_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = DocumentStore(root)
            metadata = store.get_or_scan_document("note.txt")
            self.assertTrue(metadata.get("document_id"))
            self.assertEqual(
                metadata["document_id"],
                store.get_document("note.txt")["document_id"],
            )

    def test_registered_needs_no_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = _store(root)
            before = store.get_document("note.txt")["document_id"]
            self.assertEqual(
                before, store.get_or_scan_document("note.txt")["document_id"]
            )

    def test_outside_traversal_and_symlink_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _seed(root)
            store = DocumentStore(root)
            with tempfile.TemporaryDirectory() as other:
                secret = Path(other) / "secret.txt"
                secret.write_bytes(b"x")
                with self.assertRaises(ValueError):
                    store.get_or_scan_document(secret)
            for bad in ("../note.txt", "..\\..\\x", "/etc/passwd"):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        store.get_or_scan_document(bad)
            link = root / "link.txt"
            try:
                link.symlink_to(root / "note.txt")
            except OSError:
                self.skipTest("symlinks need privileges")
            with self.assertRaises(ValueError):
                store.get_or_scan_document("link.txt")
