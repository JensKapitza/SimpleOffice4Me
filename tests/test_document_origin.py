import unittest

from app.document_origin import document_origin_tags, provenance_summary


class DocumentOriginTest(unittest.TestCase):
    def test_existing_tags_are_preserved(self):
        document = {"tags": ["invoice", "Customer-A"], "attributes": {}}
        self.assertEqual(document_origin_tags(document), ["Customer-A", "invoice"])

    def test_email_origin_becomes_visible_tags(self):
        tags = document_origin_tags({"tags": [], "attributes": {"email_origin": {"account_id": "x"}}})
        self.assertIn("origin:email", tags)
        self.assertIn("source:imap", tags)

    def test_attachment_origin_becomes_visible_tags(self):
        tags = document_origin_tags({"tags": [], "attributes": {"attachment_origin": {"message_id": "m"}}})
        self.assertIn("origin:attachment", tags)
        self.assertIn("source:eml", tags)

    def test_webdav_origin_becomes_visible_tag(self):
        tags = document_origin_tags({"tags": [], "attributes": {"webdav_origin": {"user": "alice"}}})
        self.assertIn("source:webdav", tags)

    def test_federation_origin_contains_peer_and_remote_document(self):
        tags = document_origin_tags({
            "tags": ["contract"],
            "attributes": {
                "federation_origin": {
                    "peer_id": "backup-01",
                    "origin_peer": "office-berlin",
                    "remote_document_id": "doc-123",
                    "remote_path": "Secret/Path.pdf",
                }
            },
        })
        self.assertIn("source:federation", tags)
        self.assertIn("federation-peer:backup-01", tags)
        self.assertIn("federation-origin:office-berlin", tags)
        self.assertIn("federation-document:doc-123", tags)
        self.assertNotIn("Secret/Path.pdf", " ".join(tags))

    def test_generic_source_is_normalized(self):
        tags = document_origin_tags({"tags": [], "attributes": {"source": "Scanner"}})
        self.assertIn("source:scanner", tags)

    def test_provenance_summary_only_includes_known_origin_keys(self):
        summary = provenance_summary({
            "tags": [],
            "attributes": {
                "email_origin": {"uid": "1"},
                "secret_token": "must-not-be-exported",
            },
        })
        self.assertIn("email_origin", summary["origins"])
        self.assertNotIn("secret_token", summary["origins"])


if __name__ == "__main__":
    unittest.main()
