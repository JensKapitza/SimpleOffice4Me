"""Local range-format proof; no transport or UI."""
from pathlib import Path
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.sms_archive_store import (
    ArchiveConflict, ArchiveIntegrityError, MAX_BACKUP_BYTES, MAX_RANGE_RECORDS, MAX_RECORDS, SmsArchive,
)
from tests.test_sms_archive_store import record


class SmsRangeProof(unittest.TestCase):
    def test_date_range_exceeds_old_max_and_restores_into_fresh_archive(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = SmsArchive(root, "owner")
            rows = [record(i) for i in range(1, 52)]
            archive.append(rows[:50])
            archive.append([rows[-1], record(52)])
            SmsArchive(root, "other").append([record(53, date_ms=rows[0]["date_ms"])])
            original = archive.export([(rows[0]["source"], rows[0]["provider_id"])])
            data = archive.export_date_range(rows[0]["date_ms"], rows[-1]["date_ms"])
            payload = json.loads(data)["payload"]
            self.assertEqual(2, payload["version"])
            self.assertEqual(list(reversed(rows)), payload["records"])
            new = SmsArchive(root / "fresh", "owner")
            self.assertEqual({"matching": 0, "missing": 51, "conflicting": 0}, new.verify_backup(data))
            self.assertEqual([], new.page())
            with self.assertRaises(ArchiveIntegrityError):
                SmsArchive(root / "fresh", "other").restore(data)
            self.assertEqual(51, new.restore(data))
            self.assertEqual(0, new.restore(data))
            self.assertEqual(0, new.restore(original))
            self.assertEqual({"matching": 51, "missing": 0, "conflicting": 0}, new.verify_backup(data))
            conflicted = SmsArchive(root / "conflict", "owner")
            conflicted.append([record(1, body="Other data")])
            with self.assertRaises(ArchiveConflict):
                conflicted.restore(data)
            self.assertEqual([record(1, body="Other data")], conflicted.page())

    def test_range_rejects_truncation_and_encoded_size_overflow(self):
        with TemporaryDirectory() as temp:
            archive = SmsArchive(Path(temp), "owner")
            archive.append([record(1), record(2)])
            with patch("app.sms_archive_store.MAX_RANGE_RECORDS", 1):
                with self.assertRaises(ValueError):
                    archive.export_date_range(0, 2_000_000_000_000)
            with patch("app.sms_archive_store.MAX_BACKUP_BYTES", 16):
                with self.assertRaises(ValueError):
                    archive.export_date_range(0, 2_000_000_000_000)
            with self.assertRaises(ValueError):
                archive.export_date_range(True, 100)


    def test_real_record_limit_is_inclusive_without_silent_truncation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = SmsArchive(root, "owner")
            rows = [record(i) for i in range(1, MAX_RANGE_RECORDS + 2)]
            for start in range(0, len(rows), MAX_RECORDS):
                archive.append(rows[start:start + MAX_RECORDS])
            first = rows[0]["date_ms"]
            last_within_limit = rows[-2]["date_ms"]
            backup = archive.export_date_range(first, last_within_limit)
            self.assertEqual(MAX_RANGE_RECORDS, len(json.loads(backup)["payload"]["records"]))
            recovered = SmsArchive(root / "fresh", "owner")
            self.assertEqual(MAX_RANGE_RECORDS, recovered.verify_backup(backup)["missing"])
            self.assertEqual(MAX_RANGE_RECORDS, recovered.restore(backup))
            self.assertEqual(0, recovered.restore(backup))
            with self.assertRaisesRegex(ValueError, "smaller backup range"):
                archive.preview_range_export(first, rows[-1]["date_ms"])

    def test_real_byte_limit_counts_utf8_and_escaped_json_not_just_characters(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = SmsArchive(root, "owner")
            # Raw UTF-8 is 16 KB per record; JSON escaping expands it to
            # about 20 KB. Derive the crossing point from actual archive bytes.
            body = ("🚲" + chr(1)) * 3200
            sample = record(1, body=body)
            archive.append([sample])
            sample_bytes = len(archive.export_date_range(sample["date_ms"], sample["date_ms"]))
            exceed_count = MAX_BACKUP_BYTES // (sample_bytes - 256) + 2
            self.assertLess(exceed_count, MAX_RANGE_RECORDS)
            self.assertLess(exceed_count * len(body.encode("utf-8")), MAX_BACKUP_BYTES)
            rows = [record(i, body=body) for i in range(2, exceed_count + 1)]
            for start in range(0, len(rows), MAX_RECORDS):
                archive.append(rows[start:start + MAX_RECORDS])
            with self.assertRaisesRegex(ValueError, "smaller backup range"):
                archive.export_date_range(sample["date_ms"], rows[-1]["date_ms"])
            backup = archive.export_date_range(sample["date_ms"], rows[0]["date_ms"])
            self.assertEqual([rows[0], sample], json.loads(backup)["payload"]["records"])

    def test_range_ticket_pins_snapshot_and_scope_until_expiry(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive = SmsArchive(root, "owner")
            rows = [record(i) for i in range(1, 52)]
            archive.append(rows[:50])
            archive.append(rows[50:])
            first, last = rows[0]["date_ms"], rows[-1]["date_ms"] + 10
            peek = archive.preview_range_export(first, last)
            self.assertEqual(51, peek["count"])
            ticket = archive.prepare_range_export(first, last, peek["snapshot"], "session", now=100)
            archive.append([record(52)])
            with self.assertRaises(ArchiveConflict):
                archive.prepare_range_export(first, last, peek["snapshot"], "session", now=101)
            with self.assertRaises(ValueError):
                archive.download_export(ticket, "another-session", now=102)
            with self.assertRaises(ValueError):
                SmsArchive(root, "another-owner").download_export(ticket, "session", now=102)
            with self.assertRaises(ValueError):
                archive.download_export(ticket, "session", now=400)
            data = archive.download_export(ticket, "session", now=103)
            self.assertEqual(51, len(json.loads(data)["payload"]["records"]))
            self.assertEqual(data, archive.download_export(ticket, "session", now=104))
