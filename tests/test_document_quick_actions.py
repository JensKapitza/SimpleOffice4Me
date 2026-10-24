import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from werkzeug.datastructures import MultiDict

from app import app
from app.attachment_security import ScanResult
from app.db import ensure_auth_database
from app.document_store import DocumentStore
from app.smart_folder_views import SmartFolderViews
from app.contact_store import ContactStore
from app.object_store import ObjectStore
from app.v2.contracts import ErrorCode, LogicalObjectId, OperationResult, StorageLocation, StoredObject


class DocumentQuickActionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "browser-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"})

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_unique_quick_search_match_redirects_to_object(self):
        item = ObjectStore(self.root).create({"name": "Needle-4711", "type": "Gerät"}, "jens")

        response = self.client.get("/documents/quick-search?q=Needle-4711")

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"/documents/objects/{item['object_id']}"))

    def test_ambiguous_quick_search_lists_all_matches(self):
        objects = ObjectStore(self.root)
        objects.create({"name": "Gemeinsam Alpha", "type": "Gerät"}, "jens")
        objects.create({"name": "Gemeinsam Beta", "type": "Gerät"}, "jens")

        response = self.client.get("/documents/quick-search?q=Gemeinsam")
        body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("Gemeinsam Alpha", body)
        self.assertIn("Gemeinsam Beta", body)

    def test_unique_invoice_match_redirects_to_customer_billing(self):
        contact = ContactStore(self.root).upsert({"display_name": "Rechnungskunde"}, "jens")
        directory = self.root / ".simpleoffice-meta" / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "invoice-1.json").write_text(json.dumps({
            "invoice_id": "invoice-1", "invoice_number": "2026-UNIQUE-77",
            "contact_id": contact["contact_id"], "buyer": {"name": "Rechnungskunde"},
            "status": "draft", "totals": {"gross": "10.00"},
        }), encoding="utf-8")

        response = self.client.get("/documents/quick-search?q=2026-UNIQUE-77")

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith(f"/documents/business/contacts/{contact['contact_id']}/billing"))

    def test_document_pages_expose_original_and_clamav_actions(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"safe"), "quick-action.txt", "jens")

        detail = BeautifulSoup(self.client.get(f"/documents/{document['document_id']}").get_data(as_text=True), "html.parser")
        listing = BeautifulSoup(self.client.get("/documents/").get_data(as_text=True), "html.parser")
        search = BeautifulSoup(self.client.get("/documents/search?q=quick-action").get_data(as_text=True), "html.parser")

        for page in (detail, listing, search):
            self.assertIsNotNone(page.select_one(f"form[action$='/{document['document_id']}/security/scan']"))
            self.assertIsNotNone(page.select_one(f"a[href$='/{document['document_id']}/preview']"))

    def test_single_document_scan_route_returns_to_search(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"safe"), "scan-me.txt", "jens")
        with patch("app.documents.ClamAV.scan", return_value=ScanResult("clean", "OK", "fake")):
            response = self.client.post(
                f"/documents/{document['document_id']}/security/scan",
                data={"return_view": "search", "q": "scan-me", "page": "2"},
            )

        self.assertEqual(302, response.status_code)
        self.assertIn("/documents/search?", response.headers["Location"])
        self.assertIn("q=scan-me", response.headers["Location"])
        self.assertIn("page=2", response.headers["Location"])

    def test_upload_route_uses_v2_streaming_storage_boundary(self):
        calls = []

        class FakeStorage:
            def import_stream(self, stream, filename, *, archive=False, max_bytes=0):
                content = stream.read()
                calls.append((filename, content, archive, max_bytes))
                return OperationResult.success(
                    StoredObject(
                        object_id=LogicalObjectId("uploaded-object"),
                        version="synthetic-version",
                        size=len(content),
                        location=StorageLocation("archive/example/uploaded.txt" if archive else "inbox/uploaded.txt"),
                    )
                )

        class FakeSettings:
            def settings(self):
                return {"documents": {"default_tags": [], "default_state": "new"}}

        with patch("app.documents_routes_workflows._storage", return_value=FakeStorage()), patch(
            "app.documents_routes_workflows._settings", return_value=FakeSettings()
        ), patch.object(
            DocumentStore,
            "import_upload",
            side_effect=AssertionError("browser route bypassed StoragePort"),
        ):
            response = self.client.post(
                "/documents/upload",
                data={"archive": "1", "files": (io.BytesIO(b"stream me"), "stream.txt")},
                content_type="multipart/form-data",
            )

        self.assertEqual(302, response.status_code)
        self.assertEqual(1, len(calls))
        self.assertEqual(("stream.txt", b"stream me", True), calls[0][:3])
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], calls[0][3])

    def test_folder_upload_preserves_multiple_roots_and_reports_partial_failures(self):
        imported = []
        failed = []
        completed = []
        provenance = []

        class FakeStorage:
            def import_stream_at(self, location, stream, *, max_bytes):
                content = stream.read()
                imported.append((location.relative_path, content, max_bytes))
                if content == b"reject me":
                    return OperationResult.failure(ErrorCode.STORAGE_UNAVAILABLE, "simulated failure")
                return OperationResult.success(StoredObject(
                    object_id=LogicalObjectId("folder-uploaded-object"),
                    version="folder-version",
                    size=len(content),
                    location=location,
                ))

        class FakeStore:
            @staticmethod
            def get_document(document_id):
                return {"document_id": document_id}

            @staticmethod
            def update_metadata(document_id, *, attributes, author):
                provenance.append((document_id, attributes, author))

        class FakeSettings:
            @staticmethod
            def settings():
                return {"documents": {"default_tags": [], "default_state": "new"}}

        def complete_inbox_upload(*args, **details):
            completed.append((args, details))

        def fail_inbox_upload(*details):
            failed.append(details)

        form = MultiDict([
            ("folder_import", "1"),
            ("relative_paths", json.dumps([
                "source-a/Telegram/good.txt",
                "source-a/Telegram/other.txt",
                "source-b/Telegram/rejected.txt",
            ])),
            ("files", (io.BytesIO(b"keep me"), "good.txt")),
            ("files", (io.BytesIO(b"same folder"), "other.txt")),
            ("files", (io.BytesIO(b"reject me"), "rejected.txt")),
        ])
        with patch("app.documents_routes_workflows._storage", return_value=FakeStorage()), \
             patch("app.documents_routes_workflows._settings", return_value=FakeSettings()), \
             patch("app.documents_routes_workflows._store", return_value=FakeStore()), \
             patch("app.documents_routes_workflows.begin_web_inbox_upload", return_value={"error": "", "item": {}}), \
             patch("app.documents_routes_workflows.complete_web_inbox_upload", side_effect=complete_inbox_upload), \
             patch("app.documents_routes_workflows.fail_web_inbox_upload", side_effect=fail_inbox_upload):
            response = self.client.post("/documents/upload", data=form, content_type="multipart/form-data")

        self.assertEqual(302, response.status_code)
        self.assertTrue(imported[0][0].startswith("imports/"))
        self.assertTrue(imported[0][0].endswith("/source-a/Telegram/good.txt"))
        self.assertEqual(b"keep me", imported[0][1])
        self.assertEqual(3, len(imported))
        self.assertEqual(2, len(completed))
        self.assertEqual(1, len(failed))
        self.assertEqual(2, len(provenance))
        first_origin = provenance[0][1]["folder_import_origin"]
        second_origin = provenance[1][1]["folder_import_origin"]
        self.assertEqual("source-a/Telegram", first_origin["source_path"])
        self.assertEqual(first_origin["import_id"], second_origin["import_id"])
        self.assertEqual(first_origin["folder_id"], second_origin["folder_id"])
        self.assertEqual("jens", provenance[0][2])
        with self.client.session_transaction() as session:
            messages = [message for _category, message in session.get("_flashes", [])]
        self.assertTrue(any("simulated failure" in message for message in messages))
        self.assertTrue(any("1 Datei(en) vollständig" in message for message in messages))

    def test_folder_upload_persists_nested_file_in_storageport(self):
        form = MultiDict([
            ("folder_import", "1"),
            ("relative_paths", json.dumps(["selected-root/nested/actual.txt"])),
            ("files", (io.BytesIO(b"persisted folder content"), "actual.txt")),
        ])

        # Test the real StoragePort path without depending on optional scanner availability.
        with patch(
            "app.documents_routes_workflows.begin_web_inbox_upload",
            return_value={"item": None, "malware_status": "not_configured", "error": ""},
        ), patch("app.documents_routes_workflows.complete_web_inbox_upload"):
            response = self.client.post(
                "/documents/upload",
                data=form,
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        self.assertEqual(200, response.status_code)
        self.assertIn("1 Datei(en) vollständig", response.get_data(as_text=True))
        document = DocumentStore(self.root).get_document(
            next(path.relative_to(self.root).as_posix() for path in self.root.rglob("actual.txt"))
        )
        self.assertTrue(document["last_path"].endswith("/selected-root/nested/actual.txt"))
        self.assertEqual(b"persisted folder content", (self.root / document["last_path"]).read_bytes())
        origin = document["attributes"]["folder_import_origin"]
        self.assertEqual("selected-root/nested", origin["source_path"])
        origin_view = f"imports/{origin['import_id']}/{origin['source_path']}"
        self.assertEqual(1, SmartFolderViews(DocumentStore(self.root), "jens").view(origin_view)["count"])
        detail = self.client.get(f"/documents/{document['document_id']}")
        self.assertEqual(200, detail.status_code)
        self.assertIn("Ursprungsordner: nested", detail.get_data(as_text=True))
        self.assertIn("assign_document_id", detail.get_data(as_text=True))
        picker = self.client.get(
            "/documents/smart-folders",
            query_string={"assign_document_id": document["document_id"]},
        )
        self.assertEqual(200, picker.status_code)
        view = self.client.get(
            "/documents/smart-folders/view",
            query_string={
                "folder": origin_view,
                "assign_document_id": document["document_id"],
            },
        )
        self.assertEqual(200, view.status_code)
        self.assertIn("Dokument logisch zuordnen", view.get_data(as_text=True))

    def test_document_move_route_uses_v2_storage_boundary_and_preserves_identity(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"move me"), "move-me.txt", "jens")

        response = self.client.post(
            f"/documents/{document['document_id']}/move",
            data={"destination_folder": "Archiv/2026"},
            follow_redirects=True,
        )

        self.assertEqual(200, response.status_code)
        moved = DocumentStore(self.root).get_document(document["document_id"])
        self.assertEqual(document["document_id"], moved["document_id"])
        self.assertEqual("Archiv/2026/move-me.txt", moved["last_path"])
        self.assertEqual(b"move me", (self.root / moved["last_path"]).read_bytes())

    def test_document_move_route_does_not_call_v1_move_directly(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"move me"), "direct.txt", "jens")
        calls = []

        class FakeStorage:
            def move(self, object_id, destination):
                calls.append((object_id, destination))
                return OperationResult.success(
                    StoredObject(
                        object_id=object_id,
                        version="synthetic-version",
                        size=7,
                        location=destination,
                    )
                )

        with patch("app.documents_routes_workflows._storage", return_value=FakeStorage()), patch.object(
            DocumentStore,
            "move_document",
            side_effect=AssertionError("browser route bypassed StoragePort"),
        ):
            response = self.client.post(
                f"/documents/{document['document_id']}/move",
                data={"destination_folder": "Ziel"},
                follow_redirects=True,
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(calls))
        self.assertEqual(LogicalObjectId(document["document_id"]), calls[0][0])
        self.assertEqual(StorageLocation("Ziel/direct.txt"), calls[0][1])

    def test_document_copy_route_uses_v2_storage_boundary_and_redirects_to_copy(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"copy me"), "copy-me.txt", "jens")
        target_id = LogicalObjectId("copied-object")
        calls = []

        class FakeStorage:
            def copy(self, object_id, destination):
                calls.append((object_id, destination))
                return OperationResult.success(
                    StoredObject(
                        object_id=target_id,
                        version="synthetic-version",
                        size=7,
                        location=destination,
                    )
                )

        with patch("app.documents_routes_workflows._storage", return_value=FakeStorage()), patch.object(
            DocumentStore,
            "copy_document",
            side_effect=AssertionError("browser route bypassed StoragePort"),
        ):
            response = self.client.post(
                f"/documents/{document['document_id']}/copy",
                data={"destination_folder": "Kopien"},
            )

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/documents/copied-object"))
        self.assertEqual(1, len(calls))
        self.assertEqual(LogicalObjectId(document["document_id"]), calls[0][0])
        self.assertEqual(StorageLocation("Kopien/copy-me.txt"), calls[0][1])

    def test_document_delete_route_uses_v2_storage_boundary_and_redirects_to_recovery(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"delete me"), "delete-me.txt", "jens")
        calls = []

        class FakeStorage:
            def delete(self, object_id, *, expected_version=None):
                calls.append((object_id, expected_version))
                return OperationResult.success(expected_version or "synthetic-version")

        with patch("app.documents_routes_workflows._storage", return_value=FakeStorage()), patch.object(
            DocumentStore,
            "soft_delete_document",
            side_effect=AssertionError("browser route bypassed StoragePort"),
        ):
            response = self.client.post(
                f"/documents/{document['document_id']}/delete",
                data={"confirm": "LOESCHEN"},
            )

        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/documents/recovery"))
        self.assertEqual(1, len(calls))
        self.assertEqual(LogicalObjectId(document["document_id"]), calls[0][0])
        self.assertEqual(document["sha256"], calls[0][1])

    def test_document_recovery_route_uses_v2_storage_boundary(self):
        document = DocumentStore(self.root).import_upload(
            io.BytesIO(b"recover me"),
            "recover-me.txt",
            "jens",
        )
        calls = []

        def restore(root, actor, object_id, destination, *, expected_version=None):
            calls.append((Path(root), actor, object_id, destination, expected_version))
            return {
                **document,
                "last_path": destination,
                "system_state": "indexed",
            }

        with patch(
            "app.documents_routes_recovery.restore_document_v2",
            side_effect=restore,
        ), patch.object(
            DocumentStore,
            "restore_soft_deleted",
            side_effect=AssertionError("browser route bypassed StoragePort"),
        ), patch(
            "app.webdav._record_sync_changes",
        ):
            response = self.client.post(
                f"/documents/recovery/{document['document_id']}/restore",
                data={
                    "destination_path": "Wiederhergestellt/recover-me.txt",
                    "expected_sha256": document["sha256"],
                    "confirm": "WIEDERHERSTELLEN",
                },
            )

        self.assertEqual(302, response.status_code)
        self.assertEqual(1, len(calls))
        self.assertEqual(Path(self.root), calls[0][0])
        self.assertEqual("jens", calls[0][1])
        self.assertEqual(document["document_id"], calls[0][2])
        self.assertEqual("Wiederhergestellt/recover-me.txt", calls[0][3])
        self.assertEqual(document["sha256"], calls[0][4])

    def test_content_recovery_route_uses_v2_storage_boundary(self):
        document = DocumentStore(self.root).import_upload(
            io.BytesIO(b"current"),
            "content-recovery.txt",
            "jens",
        )
        calls = []

        def restore_content(
            root,
            actor,
            object_id,
            archived_version,
            expected_current_version,
            *,
            max_bytes=0,
        ):
            calls.append(
                (
                    Path(root),
                    actor,
                    object_id,
                    archived_version,
                    expected_current_version,
                    max_bytes,
                )
            )
            return {
                **document,
                "content_revision": 2,
            }

        with patch(
            "app.documents_routes_recovery.restore_document_content_v2",
            side_effect=restore_content,
        ), patch.object(
            DocumentStore,
            "restore_content_version",
            side_effect=AssertionError("browser route bypassed StoragePort"),
        ), patch(
            "app.webdav._record_sync_changes",
        ):
            response = self.client.post(
                f"/documents/{document['document_id']}/restore-content",
                data={
                    "archived_sha256": "a" * 64,
                    "expected_current_sha256": document["sha256"],
                    "confirm": "WIEDERHERSTELLEN",
                },
            )

        self.assertEqual(302, response.status_code)
        self.assertEqual(1, len(calls))
        self.assertEqual(Path(self.root), calls[0][0])
        self.assertEqual("jens", calls[0][1])
        self.assertEqual(document["document_id"], calls[0][2])
        self.assertEqual("a" * 64, calls[0][3])
        self.assertEqual(document["sha256"], calls[0][4])
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], calls[0][5])

    def test_document_delete_route_requires_explicit_confirmation(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"keep me"), "keep-me.txt", "jens")

        response = self.client.post(
            f"/documents/{document['document_id']}/delete",
            data={"confirm": "no"},
        )

        self.assertEqual(302, response.status_code)
        self.assertTrue((self.root / document["last_path"]).exists())


if __name__ == "__main__":
    unittest.main()
