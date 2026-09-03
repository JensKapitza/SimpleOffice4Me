import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.document_store import DocumentStore
from app.federation_catalog import FederationCatalog
from app.federation_core import build_manifest, preallocate, write_chunk
from app.federation_download_worker import process_download
from app.federation_store import FederationStore


class FakeResponse:
    def __init__(self, body: bytes, status: int = 206):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit: int = -1):
        return self.body if limit < 0 else self.body[:limit]


class FederationDownloadWorkerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test-secret", DOCUMENT_ROOT=str(self.root))
        self.context = self.app.app_context()
        self.context.push()
        self.source = self.root / ".remote-source.bin"
        self.source.write_bytes(b"abcdefghijklmnopqrstuvwxyz0123456789")
        self.manifest = build_manifest(self.source, chunk_size=8)
        self.federation = FederationStore(self.root)
        self.federation.save_peer(
            "server-a", "Server A", "http://server-a.invalid:8080", "peer-token",
            {"documents": {"receive": True}}, True,
        )
        self.catalog = FederationCatalog(self.root)
        self.remote = {
            "document_id": "remote-doc-1",
            "blob_hash": self.manifest["blob_hash"],
            "path": "Kunden/Vertrag.pdf",
            "size": self.manifest["size"],
            "modified_at": "2026-09-03T12:00:00Z",
            "tags": ["vertrag", "kunde:test"],
            "origin_tags": ["origin:email", "source:imap"],
        }
        self.catalog.begin_index("server-a", "g1")
        self.catalog.ingest("server-a", "g1", [self.remote])
        self.catalog.finish_index("server-a", "g1")
        self.request = self.catalog.request_download(
            "server-a", "remote-doc-1", requested_by="alice", transfer_priority=10,
        )

    def tearDown(self):
        self.context.pop()
        self.temp.cleanup()

    def _chunk_response(self, request, **kwargs):
        index = int(str(request).rsplit("/", 1)[-1])
        chunk = self.manifest["chunks"][index]
        start = int(chunk["offset"])
        end = start + int(chunk["length"])
        return FakeResponse(self.source.read_bytes()[start:end])

    def test_complete_download_imports_document_with_provenance_tags(self):
        with patch("app.federation_download_worker.remote_blob_manifest", return_value=self.manifest), patch(
            "app.federation_download_worker._request", side_effect=self._chunk_response
        ):
            result = process_download(self.root, self.request["request_id"])
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["local_document_id"])
        document = DocumentStore(self.root).get_document(result["local_document_id"])
        self.assertIn("source:federation", document["tags"])
        self.assertIn("federation-peer:server-a", document["tags"])
        self.assertIn("federation-document:remote-doc-1", document["tags"])
        self.assertIn("origin:email", document["tags"])
        self.assertIn("source:imap", document["tags"])
        self.assertEqual(document["attributes"]["federation_origin"]["remote_document_id"], "remote-doc-1")
        downloaded = self.root / document["last_path"]
        self.assertEqual(downloaded.read_bytes(), self.source.read_bytes())
        actions = [event["action"] for event in self.catalog.events(20)]
        self.assertIn("download_completed", actions)

    def test_resume_skips_chunks_already_present(self):
        request_id = self.request["request_id"]
        partial = self.federation.incoming / f"pull-{request_id}.part"
        preallocate(partial, self.manifest["size"])
        first = self.manifest["chunks"][0]
        first_data = self.source.read_bytes()[first["offset"]:first["offset"] + first["length"]]
        write_chunk(partial, first["offset"], first_data)
        self.federation.create_transfer(
            request_id,
            direction="incoming-pull",
            operation="COPY",
            blob_hash=self.manifest["blob_hash"],
            source_peer="server-a",
            status="paused",
            total_bytes=self.manifest["size"],
            total_chunks=self.manifest["chunk_count"],
            manifest=self.manifest,
        )
        self.federation.update_transfer(request_id, final_path=str(partial))
        self.federation.set_have(request_id, {0}, self.manifest["chunk_count"])
        requested_indices = []

        def response(url, **kwargs):
            index = int(str(url).rsplit("/", 1)[-1])
            requested_indices.append(index)
            return self._chunk_response(url, **kwargs)

        with patch("app.federation_download_worker.remote_blob_manifest", return_value=self.manifest), patch(
            "app.federation_download_worker._request", side_effect=response
        ):
            process_download(self.root, request_id)
        self.assertNotIn(0, requested_indices)
        self.assertEqual(sorted(requested_indices), list(range(1, self.manifest["chunk_count"])))

    def test_network_failure_moves_request_to_waiting_peer_and_keeps_catalog(self):
        with patch(
            "app.federation_download_worker.remote_blob_manifest",
            side_effect=urllib.error.URLError("offline"),
        ):
            with self.assertRaises(urllib.error.URLError):
                process_download(self.root, self.request["request_id"])
        row = self.catalog.get_request(self.request["request_id"])
        self.assertEqual(row["status"], "waiting_peer")
        self.assertGreater(row["next_attempt_at"], 0)
        self.assertEqual(self.catalog.get_remote("server-a", "remote-doc-1")["path"], "Kunden/Vertrag.pdf")


if __name__ == "__main__":
    unittest.main()
