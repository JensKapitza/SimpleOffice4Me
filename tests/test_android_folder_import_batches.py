"""Integration regression tests for Android multipart folder imports."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database


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

    def upload(self, import_id, paths):
        data = {"folder_import": "1", "import_id": import_id,
                "relative_paths": json.dumps(paths),
                "files": [(io.BytesIO(path.encode()), path.rsplit("/", 1)[-1])
                          for path in paths]}
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

    def test_replayed_path_fails_instead_of_reporting_success(self):
        token = "b" * 32
        self.assertEqual(200, self.upload(token, ["root/same.txt"]).status_code)
        response = self.upload(token, ["root/same.txt"])
        self.assertEqual(409, response.status_code)
        self.assertEqual({"ok": False, "stored": 0, "expected": 1}, response.get_json())

    def test_invalid_batch_id_is_rejected(self):
        response = self.upload("../other", ["root/file.txt"])
        self.assertNotEqual(200, response.status_code)
        self.assertEqual([], list(self.root.rglob("file.txt")))


if __name__ == "__main__":
    unittest.main()
