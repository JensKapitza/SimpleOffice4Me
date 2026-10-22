"""Request parser regression tests for maximum-size folder imports."""
from __future__ import annotations

import io
import json
import unittest

from werkzeug.datastructures import MultiDict

from app import app


class FolderImportMultipartLimitTests(unittest.TestCase):
    def test_one_thousand_file_parts_fit_route_scoped_form_limit(self):
        paths = [f"folder/file-{index}.txt" for index in range(1000)]
        data = MultiDict([
            ("_csrf_token", "invalid-test-token"),
            ("folder_import", "1"),
            ("relative_paths", json.dumps(paths)),
        ])
        for index in range(1000):
            data.add("files", (io.BytesIO(b"x"), f"file-{index}.txt"))

        response = app.test_client().post("/documents/upload", data=data)

        # Invalid CSRF stops before storage mutation; 413 means the parser
        # rejected the advertised 1000-file folder batch.
        self.assertNotEqual(413, response.status_code)


if __name__ == "__main__":
    unittest.main()
