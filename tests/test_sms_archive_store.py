import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.sms_archive_store import ArchiveConflict, ArchiveIntegrityError, SmsArchive


SOURCE = "6" * 32


def record(i=1, **changes):
    return {
        "source": SOURCE, "provider_id": i, "date_ms": 1_752_000_000_123 + i,
        "direction": "inbox", "address": "+4900001", "body": "Üben 🚲\nabc", **changes,
    }


class SmsArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = SmsArchive(self.root, "owner-one")

    def test_repeat_is_idempotent_and_reused_id_is_not_marked_archived(self):
        self.assertEqual(["unarchived"], self.archive.states([record()]))
        self.assertEqual(1, self.archive.append([record(), record()]))
        self.assertEqual(0, self.archive.append([record()]))
        self.assertEqual(["archived", "conflict"], self.archive.states([record(), record(body="changed")]))
        with self.assertRaises(ArchiveConflict):
            self.archive.append([record(2), record(body="changed")])
        self.assertEqual([record()], self.archive.page())

    def test_search_scope_and_pagination_survive_new_instance(self):
        self.archive.append([record(), record(2, address="Other")])
        other = SmsArchive(self.root, "other")
        other.append([record(body="Other person")])
        reopened = SmsArchive(self.root, "owner-one")
        self.assertEqual([record(2, address="Other")], reopened.page(query="oThEr"))
        self.assertEqual([record(2, address="Other"), record()], reopened.page(query="üBEN"))
        self.assertEqual([record()], reopened.page(limit=1, offset=1))
        self.assertEqual([], reopened.page(query="Other person"))
        self.assertEqual([record(body="Other person")], other.page())

    def test_verified_backup_restores_into_independent_root_without_overwriting(self):
        self.archive.append([record(), record(2)])
        data = self.archive.export([(SOURCE, 1), (SOURCE, 1), (SOURCE, 2)])
        recovered = SmsArchive(self.root / "fresh", "owner-one")
        self.assertEqual(2, recovered.restore(data))
        self.assertEqual(0, recovered.restore(data))
        self.assertEqual([record(2), record()], recovered.page())
        with self.assertRaises(ArchiveIntegrityError):
            SmsArchive(self.root / "fresh", "other").restore(data)
        changed = json.loads(data)
        changed["payload"]["records"][1]["body"] = "changed"
        untouched = SmsArchive(self.root / "take-two", "owner-one")
        with self.assertRaises(ArchiveIntegrityError):
            untouched.restore(json.dumps(changed).encode())
        self.assertEqual([], untouched.page())

    def test_download_tickets_are_bounded_and_survive_a_new_store_instance(self):
        self.archive.append([record()])
        session_key = "synthetic-session"
        first, *_, latest = [
            self.archive.prepare_export([(SOURCE, 1)], session_key, now=1000 + i)
            for i in range(9)
        ]
        reopened = SmsArchive(self.root, "owner-one")
        with self.assertRaises(ValueError):
            reopened.download_export(first, session_key, now=1010)
        self.assertEqual(self.archive.export([(SOURCE, 1)]),
                         reopened.download_export(latest, session_key, now=1010))
        with self.assertRaises(ValueError):
            SmsArchive(self.root, "other").download_export(latest, session_key, now=1010)

    def test_archive_corruption_is_not_marked_archived_or_exported(self):
        self.archive.append([record()])
        with sqlite3.connect(self.root / ".simpleoffice-meta" / "sms-archive.sqlite3") as db:
            db.execute("UPDATE archived_sms SET body='torn'")
        for operation in (lambda: self.archive.states([record()]),
                          lambda: self.archive.export([(SOURCE, 1)]),
                          self.archive.page):
            with self.assertRaises(ArchiveIntegrityError):
                operation()

    def test_concurrent_retry_results_in_one_row(self):
        with ThreadPoolExecutor(4) as pool:
            counts = list(pool.map(lambda _: self.archive.append([record()]), range(8)))
        self.assertEqual(1, sum(counts))
        self.assertEqual([record()], self.archive.page())

    def test_validation_precedes_any_write(self):
        for invalid in (record(provider_id=True), record(body="🚲" * 4001), record(body="\udcff"),
                        record(source="../etc/password"), record(direction="mms")):
            with self.assertRaises(ValueError):
                self.archive.append([record(2), invalid])
        self.assertEqual([], self.archive.page())

    def test_archive_path_never_accepts_existing_symlink(self):
        if not hasattr(Path, "symlink_to"):
            self.skipTest("Symlinks are not supported")
        path = self.root / ".simpleoffice-meta" / "sms-archive.sqlite3"
        with TemporaryDirectory() as temporary:
            outside = Path(temporary) / "unrelated"
            outside.write_text("stay untouched", encoding="utf-8")
            path.unlink()
            try:
                path.symlink_to(outside)
            except (NotImplementedError, OSError):
                self.skipTest("Platform does not permit symlinks in this workspace")
            with self.assertRaises(OSError):
                SmsArchive(self.root, "owner-one")
            self.assertEqual("stay untouched", outside.read_text(encoding="utf-8"))

    def test_restore_rejects_ambiguous_duplicate_json_fields(self):
        self.archive.append([record()])
        valid = self.archive.export([(SOURCE, 1)])
        duplicate = valid.replace(b'"version":1', b'"version":2,"version":1')
        with self.assertRaises(ArchiveIntegrityError):
            SmsArchive(self.root / "new", "owner-one").restore(duplicate)

    def test_export_does_not_resolve_another_owner_record(self):
        self.archive.append([record()])
        with self.assertRaises(ValueError):
            SmsArchive(self.root, "other").export([(SOURCE, 1)])
