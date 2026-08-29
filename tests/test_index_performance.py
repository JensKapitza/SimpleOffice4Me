import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.document_store import DocumentStore, ScanReport, atomic_json_write
from app.file_lock import exclusive_file_lock
from tools import launcher
from tools.index_worker import run_index


class IndexProjectionPerformanceTest(unittest.TestCase):
    def test_schema_initialization_is_cached_but_deleted_index_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as temp:
            first = DocumentStore(temp)
            first.initialize()
            second = DocumentStore(temp)
            with patch.object(second, "_initialize_once", side_effect=AssertionError("duplicate schema work")):
                second.initialize()

            first.index_path.unlink()
            third = DocumentStore(temp)
            third.initialize()
            self.assertTrue(third.index_path.is_file())

    def test_fifty_thousand_document_inbox_reads_only_one_page_of_sidecars(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DocumentStore(temp)
            store.initialize()
            rows = [
                (f"document-{number}", f"inbox/{number}.txt", "new", 0, 0, f"2026-01-01T00:{number // 60:03d}:{number % 60:02d}+00:00")
                for number in range(50_147)
            ]
            with store._db() as db:
                db.executemany(
                    """INSERT INTO document_listing(
                           document_id, path, state, has_notes, has_relationships, last_seen_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    rows,
                )
            selected = sorted(rows, key=lambda row: (row[5], row[1]), reverse=True)[:25]
            for document_id, path, *_rest in selected:
                atomic_json_write(
                    store.documents / f"{document_id}.json",
                    {"document_id": document_id, "last_path": path, "state": "new"},
                )

            with patch.object(store, "_read_json", wraps=store._read_json) as read_json:
                result = store.inbox_page(page=1, page_size=25)

            self.assertEqual(50_147, result["total"])
            self.assertEqual(25, len(result["documents"]))
            self.assertTrue(result["has_next"])
            self.assertEqual(25, read_json.call_count)

    def test_unchanged_scan_backfills_projection_without_rehashing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "existing.txt").write_text("unchanged", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            with store._db() as db:
                db.execute("DELETE FROM document_listing")

            with patch("app.document_store.sha256_file", side_effect=AssertionError("must reuse cached hash")):
                report = store.scan()

            self.assertEqual(1, report.files)
            self.assertEqual(1, store.inbox_page()["total"])


class IndexProcessIsolationTest(unittest.TestCase):
    def test_nonblocking_lock_rejects_a_duplicate_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "worker.lock"
            with exclusive_file_lock(path) as first:
                with exclusive_file_lock(path, blocking=False) as second:
                    self.assertTrue(first)
                    self.assertFalse(second)

    def test_worker_records_completion_and_uses_bounded_settings(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"SIMPLEOFFICE_INDEX_DELAY_SECONDS": "0", "SIMPLEOFFICE_INDEX_YIELD_MS": "0"},
            clear=True,
        ), patch("tools.index_worker.lower_process_priority"), patch.object(
            DocumentStore,
            "scan",
            return_value=ScanReport(files=10_000, new_files=25, updated_files=40),
        ):
            self.assertEqual(0, run_index(temp))
            status = DocumentStore(temp).scan_status()

        self.assertEqual("completed", status["state"])
        self.assertEqual(10_000, status["files"])
        self.assertEqual(25, status["new_files"])
        self.assertEqual(40, status["updated_files"])

    def test_launcher_starts_separate_python_worker_and_can_disable_it(self):
        process = type("Process", (), {"pid": 123})()
        with patch.dict(os.environ, {}, clear=True), patch("tools.launcher.subprocess.Popen", return_value=process) as popen:
            self.assertIs(process, launcher.start_index_worker("/srv/documents"))
        command = popen.call_args.args[0]
        self.assertEqual(os.sys.executable, command[0])
        self.assertEqual("tools.index_worker", command[2])
        self.assertIn("/srv/documents", command)

        with patch.dict(os.environ, {"SIMPLEOFFICE_BACKGROUND_INDEX": "0"}, clear=True), patch("tools.launcher.subprocess.Popen") as popen:
            self.assertIsNone(launcher.start_index_worker("/srv/documents"))
            popen.assert_not_called()

    def test_launcher_starts_osm_check_and_supports_forced_reindex(self):
        process = type("Process", (), {"pid": 124})()
        with patch("tools.launcher.subprocess.Popen", return_value=process) as popen:
            self.assertIs(process, launcher.start_osm_index_worker("/srv/documents", force=True))
        command = popen.call_args.args[0]
        self.assertEqual("tools.osm_index_worker", command[2])
        self.assertIn("--force", command)

        with patch("tools.launcher.subprocess.Popen", return_value=process) as popen:
            self.assertIs(process, launcher.start_osm_download_worker("/srv/documents", "germany"))
        command = popen.call_args.args[0]
        self.assertEqual("tools.osm_download_worker", command[2])
        self.assertEqual("germany", command[-1])


class LoginDashboardPerformanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user (username, password) VALUES (?, ?)",
                ("jens", generate_password_hash("sicheres-passwort")),
            )
            db.commit()
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_login_target_neither_loads_all_sidecars_nor_probes_all_mounts(self):
        self.client.post(
            "/auth/login",
            data={"username": "jens", "password": "sicheres-passwort"},
        )
        with patch.object(DocumentStore, "_all_documents", side_effect=AssertionError("full sidecar scan")), patch.object(
            DocumentStore,
            "_mounted_roots",
            side_effect=AssertionError("mount probe"),
        ):
            response = self.client.get("/", follow_redirects=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("Systemübersicht", response.get_data(as_text=True))

    def test_document_and_inbox_routes_pass_documents_only_once(self):
        self.client.post(
            "/auth/login",
            data={"username": "jens", "password": "sicheres-passwort"},
        )

        documents = self.client.get("/documents/")
        inbox = self.client.get("/documents/inbox")

        self.assertEqual(200, documents.status_code)
        self.assertEqual(200, inbox.status_code)

    def test_single_document_detail_does_not_scan_or_rewrite_fifty_thousand_sidecars(self):
        root = Path(app.config["DOCUMENT_ROOT"])
        root.mkdir(parents=True, exist_ok=True)
        source = root / "einzeln.txt"
        source.write_text("Nur diese Datei", encoding="utf-8")
        store = DocumentStore(root)
        store.scan()
        document = store.get_document(source)
        with store._db() as db:
            db.executemany(
                """INSERT INTO document_listing(
                       document_id, path, state, has_notes, has_relationships, last_seen_at
                   ) VALUES (?, ?, 'new', 0, 0, '')""",
                [(f"unrelated-{number}", f"archive/{number}.dat") for number in range(50_146)],
            )
        self.client.post(
            "/auth/login",
            data={"username": "jens", "password": "sicheres-passwort"},
        )

        with patch.object(DocumentStore, "_all_documents", side_effect=AssertionError("full sidecar scan")), patch.object(
            DocumentStore, "_save_document", side_effect=AssertionError("large sidecar rewrite")
        ), patch.object(DocumentStore, "logbook", side_effect=AssertionError("eager audit scan")), patch(
            "app.document_store.sha256_file", side_effect=AssertionError("content hashing")
        ):
            response = self.client.get(f"/documents/{document['document_id']}")

        self.assertEqual(200, response.status_code)
        self.assertIn("Nur diese Datei", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
