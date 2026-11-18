"""Resource cleanup for verified document preview responses."""
import tempfile
import unittest
from unittest.mock import patch

from app import app
from app import documents_routes_content as routes


class VerifiedPreviewCleanupTests(unittest.TestCase):
    def test_spool_closes_when_storage_copy_raises(self):
        original_factory = tempfile.SpooledTemporaryFile
        created = []

        def tracked_spool(*args, **kwargs):
            spool = original_factory(*args, **kwargs)
            created.append(spool)
            return spool

        class BrokenStorage:
            def copy_verified_to(self, object_id, destination):
                raise OSError("storage read failed")

        with app.test_request_context("/"):
            routes.g.user = {"username": "tester"}
            with patch.object(routes.tempfile, "SpooledTemporaryFile", side_effect=tracked_spool), \
                    patch.object(routes, "_storage", return_value=BrokenStorage()):
                with self.assertRaisesRegex(OSError, "storage read failed"):
                    routes._verified_preview_response({"document_id": "doc-1"})

        self.assertEqual(1, len(created))
        self.assertTrue(created[0].closed)


if __name__ == "__main__":
    unittest.main()
