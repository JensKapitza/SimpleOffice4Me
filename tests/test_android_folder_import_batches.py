"""Integration regression tests for Android multipart folder imports."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import ensure_auth_database
from app.document_store import DocumentStore


class AndroidFolderImportBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in
                      ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=False,
                          DATABASE=str(root / "users.sqlite"),
                          DOCUMENT_ROOT=str(root / "documents"))
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        credentials = {"username": "android-batch-tester", "password": "test-password-long"}
        self.client.post("/auth/register", data=credentials)
        self.client.post("/auth/login", data=credentials)
        self.root = root / "documents"

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def upload(self, import_id, paths, source_ids=None, contents=None):
        data = {"folder_import": "1", "import_id": import_id,
                "relative_paths": json.dumps(paths),
                "files": [(io.BytesIO((contents or {}).get(path, path.encode())),
                           path.rsplit("/", 1)[-1]) for path in paths]}
        if source_ids is not None:
            data["source_ids"] = json.dumps(source_ids)
        return self.client.post("/documents/upload", data=data)

    def test_multiple_batches_share_one_import_directory(self):
        token = "a" * 32
        first = self.upload(token, ["root/first.txt"])
        second = self.upload(token, ["root/nested/second.txt"])
        self.assertEqual(200, first.status_code, first.get_data(as_text=True))
        self.assertEqual(200, second.status_code, second.get_data(as_text=True))
        self.assertEqual({"ok": True, "stored": 1}, first.get_json())
        first_paths = list(self.root.rglob("first.txt"))
        second_paths = list(self.root.rglob("second.txt"))
        self.assertEqual(1, len(first_paths))
        self.assertEqual(1, len(second_paths))
        self.assertEqual(first_paths[0].parents[1], second_paths[0].parents[2])

    def test_more_than_one_thousand_files_import_across_http_batches(self):
        token = "d" * 32
        expected = 1201
        stored = 0
        for start in range(0, expected, 100):
            paths = [f"root/nested/file-{index:04d}.txt"
                     for index in range(start, min(start + 100, expected))]
            response = self.upload(token, paths)
            self.assertEqual(200, response.status_code, response.get_data(as_text=True))
            payload = response.get_json()
            self.assertEqual({"ok": True, "stored": len(paths)}, payload)
            stored += payload["stored"]

        self.assertEqual(expected, stored)
        imported_files = list(self.root.rglob("file-*.txt"))
        self.assertEqual(expected, len(imported_files))
        self.assertEqual(expected, len({path.relative_to(self.root) for path in imported_files}))

    def test_replayed_matching_path_is_idempotently_accepted(self):
        token = "b" * 32
        self.assertEqual(200, self.upload(token, ["root/same.txt"]).status_code)
        response = self.upload(token, ["root/same.txt"])
        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True, "stored": 1}, response.get_json())
        self.assertEqual(1, len(list(self.root.rglob("same.txt"))))

    def test_replayed_path_with_different_content_is_rejected_without_overwrite(self):
        token = "f" * 32
        self.assertEqual(200, self.upload(token, ["root/same.txt"]).status_code)
        response = self.upload(
            token, ["root/same.txt"], contents={"root/same.txt": b"changed"}
        )
        self.assertEqual(409, response.status_code)
        self.assertFalse(response.get_json()["retryable"])
        self.assertEqual(b"root/same.txt", next(self.root.rglob("same.txt")).read_bytes())

    def test_replayed_path_with_different_source_identity_is_rejected(self):
        token = "g" * 32
        self.assertEqual(
            200, self.upload(token, ["root/same.txt"], ["a" * 64]).status_code
        )
        response = self.upload(token, ["root/same.txt"], ["b" * 64])
        self.assertEqual(409, response.status_code)
        self.assertFalse(response.get_json()["retryable"])

    def test_partially_saved_batch_resumes_without_duplicate_files(self):
        import app.documents_routes_upload as upload_routes

        original_begin = upload_routes.begin_web_inbox_upload
        fail_once = {"second.txt"}

        def begin_once(config, item, actor, source_key):
            if item.filename in fail_once:
                fail_once.remove(item.filename)
                return {"error": "temporary inbox failure", "item": None}
            return original_begin(config, item, actor, source_key)

        token = "h" * 32
        paths = ["root/first.txt", "root/second.txt"]
        with patch.object(upload_routes, "begin_web_inbox_upload", side_effect=begin_once):
            interrupted = self.upload(token, paths)
            resumed = self.upload(token, paths)

        self.assertEqual(409, interrupted.status_code)
        self.assertEqual({"ok": False, "stored": 1, "expected": 2}, {
            key: interrupted.get_json()[key] for key in ("ok", "stored", "expected")
        })
        self.assertTrue(interrupted.get_json()["retryable"])
        self.assertEqual(200, resumed.status_code)
        self.assertEqual({"ok": True, "stored": 2}, resumed.get_json())
        self.assertEqual(1, len(list(self.root.rglob("first.txt"))))
        self.assertEqual(1, len(list(self.root.rglob("second.txt"))))

    def test_invalid_batch_id_is_rejected(self):
        response = self.upload("../other", ["root/file.txt"])
        self.assertNotEqual(200, response.status_code)
        self.assertEqual([], list(self.root.rglob("file.txt")))

    def test_collision_suffixed_roots_keep_distinct_source_identities(self):
        token = "c" * 32
        first_id = "a" * 64
        second_id = "b" * 64
        first_path = "Books (aaaaaaaa)/nested/first.txt"
        second_path = "Books (bbbbbbbb)/nested/second.txt"

        first = self.upload(token, [first_path], [first_id])
        second = self.upload(token, [second_path], [second_id])

        self.assertEqual(200, first.status_code, first.get_data(as_text=True))
        self.assertEqual(200, second.status_code, second.get_data(as_text=True))
        first_file = list(self.root.rglob("first.txt"))
        second_file = list(self.root.rglob("second.txt"))
        self.assertEqual(1, len(first_file))
        self.assertEqual(1, len(second_file))
        self.assertNotEqual(first_file[0].parents[1], second_file[0].parents[1])
        store = DocumentStore(self.root)
        store.initialize()
        first_document = store.get_document(first_file[0])
        second_document = store.get_document(second_file[0])
        self.assertEqual(
            first_id,
            first_document["attributes"]["folder_import_origin"]["source_identity"],
        )
        self.assertEqual(
            second_id,
            second_document["attributes"]["folder_import_origin"]["source_identity"],
        )


if __name__ == "__main__":
    unittest.main()
