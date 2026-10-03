"""Replication and backup must not consume stale projection bytes."""
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.document_store import DocumentStore
from app.replication_store import ReplicationStore
from app.v2.blob_store import BlobStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.encrypted_cutover import encrypted_blob_cutover
from app.v2.master_keys import MasterKeyProfileStore
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v2.runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID, clear_runtime_storage_master_key


class ReplicationStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self.addCleanup(lambda: clear_runtime_storage_master_key(self.root))
        self.target = self.base / "target"
        self.target.mkdir()
        self.content = b"authoritative replication bytes"
        self.document = DocumentStore(self.root).import_upload(io.BytesIO(self.content), "sample.txt", "tester")
        self.projection = self.root / self.document["last_path"]
        self.replication = ReplicationStore(self.root)
        target = self.replication.add_target({"label": "USB", "path": str(self.target)}, "tester")
        self.rule = self.replication.add_rule({"label": "Docs", "target_id": target["target_id"], "categories": ["documents"]}, "tester")
        self.relative = Path("documents") / self.document["document_id"] / "sample.txt"
        self.destination = self.target / "SimpleOffice-Spiegelung" / self.relative
        self.repository = self.replication.add_restic_repository({"label": "Backup", "repository": "synthetic-repository", "categories": ["documents"]}, "tester")
        self.stage = self.replication.control / "restic-staging" / self.repository["repository_id"]

    def migrate(self):
        backup = self.base / "backup"
        create_migration_backup(self.root, backup)
        transfer_legacy_documents(self.root, backup)
        prepare_shadow(self.root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(self.root, apply=True, acknowledge_local_plaintext=True)

    def assert_copies(self):
        result = self.replication.run_rule(self.rule["rule_id"], "tester")
        self.assertEqual(self.content, self.destination.read_bytes())
        self.assertEqual(1, result["files"])
        self.assertEqual(1, self.replication.run_rule(self.rule["rule_id"], "tester")["unchanged"])
        stage = self.replication._restic_staging(self.repository, "tester")
        self.assertEqual(self.content, (stage / self.relative).read_bytes())
        manifest = json.loads((stage / "manifest.json").read_text())
        self.assertEqual(self.document["sha256"], manifest["files"][0]["sha256"])
        if os.name == "posix":
            self.assertEqual(0o700, stage.stat().st_mode & 0o777)

    def test_v1_mirror_and_backup(self):
        self.assert_copies()

    def test_v2_missing_projection(self):
        self.migrate()
        self.projection.unlink()
        self.assert_copies()

    def test_v2_stale_projection(self):
        self.migrate()
        self.projection.write_bytes(b"stale")
        self.assert_copies()

    def test_corrupt_v2_preserves_existing_target_and_last_success(self):
        self.migrate()
        success = self.replication.run_rule(self.rule["rule_id"], "tester")
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"corrupt")
        with self.assertRaises(RuntimeError):
            self.replication.run_rule(self.rule["rule_id"], "tester")
        self.assertEqual(self.content, self.destination.read_bytes())
        self.assertEqual(success, self.replication.status()["rules"][0]["last_result"])
        with self.assertRaises(RuntimeError):
            self.replication._restic_staging(self.repository, "tester")
        self.assertFalse(self.stage.exists())

    def test_tampered_v1_rejects_mirror_and_backup(self):
        self.projection.write_bytes(b"corrupt")
        with self.assertRaises(RuntimeError):
            self.replication.run_rule(self.rule["rule_id"], "tester")
        with self.assertRaises(RuntimeError):
            self.replication._restic_staging(self.repository, "tester")
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.stage.exists())

    def test_missing_v1_is_not_reported_as_successful_empty_backup(self):
        self.projection.unlink()
        with self.assertRaises(FileNotFoundError):
            self.replication.run_rule(self.rule["rule_id"], "tester")
        with self.assertRaises(FileNotFoundError):
            self.replication._restic_staging(self.repository, "tester")
        self.assertFalse(self.stage.exists())
        self.assertEqual({}, self.replication.status()["rules"][0]["last_result"])

    def test_control_file_selection_remains_compatible(self):
        source = self.replication.control / "notes.json"
        source.write_text('{"notes": []}')
        target_id = self.replication.status()["targets"][0]["target_id"]
        rule = self.replication.add_rule({"label": "Notes", "target_id": target_id, "categories": ["notes"]}, "tester")
        result = self.replication.run_rule(rule["rule_id"], "tester")
        self.assertEqual(1, result["files"])
        self.assertEqual(source.read_bytes(), (self.target / "SimpleOffice-Spiegelung/control/notes.json").read_bytes())

    def test_partial_copy_preserves_target_and_closes_materialization(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"previous")
        seen = []
        def fail(source, pending):
            seen.append(source)
            pending.write_bytes(b"partial")
            raise OSError("synthetic copy failure")
        with patch("app.replication_store.shutil.copyfile", side_effect=fail):
            with self.assertRaises(OSError):
                self.replication.run_rule(self.rule["rule_id"], "tester")
        self.assertEqual(b"previous", self.destination.read_bytes())
        self.assertFalse(seen[0].exists())
        self.assertFalse(list(self.destination.parent.glob(".simpleoffice-replication-*")))

    def test_bad_copy_is_not_published(self):
        with patch("app.replication_store.shutil.copyfile", side_effect=lambda source, pending: pending.write_bytes(b"corrupt")):
            with self.assertRaises(ValueError):
                self.replication.run_rule(self.rule["rule_id"], "tester")
        self.assertFalse(self.destination.exists())

    def test_restic_process_outcomes_remove_working_plaintext(self):
        outcomes = [subprocess.CompletedProcess([], 0, "ok", ""), subprocess.CompletedProcess([], 1, "", "failed"), subprocess.TimeoutExpired("restic", 7200)]
        for outcome in outcomes:
            def run(*args, **kwargs):
                self.assertEqual(self.content, (self.stage / self.relative).read_bytes())
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            with self.subTest(outcome=type(outcome).__name__), patch("app.replication_store.shutil.which", return_value="restic"), patch("app.replication_store.subprocess.run", side_effect=run):
                if isinstance(outcome, Exception):
                    with self.assertRaises(subprocess.TimeoutExpired):
                        self.replication.run_restic(self.repository["repository_id"], "backup", "synthetic-password", "tester")
                elif outcome.returncode:
                    with self.assertRaises(ValueError):
                        self.replication.run_restic(self.repository["repository_id"], "backup", "synthetic-password", "tester")
                else:
                    self.replication.run_restic(self.repository["repository_id"], "backup", "synthetic-password", "tester")
            self.assertFalse(self.stage.exists())

    def test_encrypted_v2_without_plaintext_sources(self):
        self.migrate()
        phrase = "synthetic-replication-unlock"
        profile = MasterKeyProfileStore(self.root, "synthetic-setup")
        profile.create(STORAGE_PROFILE_ID, phrase)
        key = profile.unlock_with_password(STORAGE_PROFILE_ID, phrase)
        self.assertTrue(encrypted_blob_cutover(self.root, key, apply=True)["ready"])
        password_file = self.base / "unlock.txt"
        password_file.write_text(phrase)
        password_file.chmod(0o600)
        self.projection.unlink()
        for chunk in BlobStore(self.root).chunks.glob("*.bin"):
            chunk.write_bytes(b"plaintext unavailable")
        with patch.dict(os.environ, {PASSWORD_FILE_ENV: str(password_file)}):
            self.assert_copies()
