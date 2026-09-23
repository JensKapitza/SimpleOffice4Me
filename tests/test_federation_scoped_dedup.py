import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.federation_blocks import FederationBlockStore
from app.federation_scoped_dedup import scoped_deduplicated_download
from app.federation_store import FederationStore
from app.v2.scoped_dedup import SCHEMA, create_dedup_session, scoped_block_token


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


class FederationScopedDedupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.secret = "peer-secret"
        self.first = b"abc"
        self.second = b"def"
        self.payload = self.first + self.second
        self.blob_hash = hashlib.sha256(self.payload).hexdigest()
        self.session = create_dedup_session(self.secret, self.blob_hash)
        self.first_hash = hashlib.sha512(self.first).hexdigest()
        self.second_hash = hashlib.sha512(self.second).hexdigest()
        self.manifest = {
            "schema": SCHEMA,
            "session": self.session,
            "size": len(self.payload),
            "block_count": 2,
            "blocks": [
                {
                    "index": 0,
                    "offset": 0,
                    "length": len(self.first),
                    "token": scoped_block_token(self.secret, self.session, self.first_hash),
                },
                {
                    "index": 1,
                    "offset": len(self.first),
                    "length": len(self.second),
                    "token": scoped_block_token(self.secret, self.session, self.second_hash),
                },
            ],
        }
        source = self.root / "local.bin"
        source.write_bytes(self.first)
        FederationBlockStore(self.root).register_manifest(
            source,
            {
                "schema": "sofp-content-blocks/v1",
                "hash_algorithm": "sha512",
                "file_sha512": self.first_hash,
                "size": len(self.first),
                "block_count": 1,
                "blocks": [{"index": 0, "offset": 0, "length": len(self.first), "sha512": self.first_hash}],
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_local_block_is_reused_without_revealing_its_hash_to_peer(self):
        request_row = {
            "request_id": "request-1",
            "peer_id": "peer-a",
            "blob_hash": self.blob_hash,
        }
        peer = {"base_url": "https://peer.invalid"}
        requested = []

        def remote_response(url, **kwargs):
            requested.append(str(url))
            return FakeResponse(self.second)

        with patch("app.federation_scoped_dedup._remote_manifest", return_value=self.manifest), patch(
            "app.federation_scoped_dedup._request", side_effect=remote_response
        ):
            partial = scoped_deduplicated_download(
                self.root,
                request_row,
                peer,
                self.secret,
                FederationStore(self.root),
            )

        self.assertIsNotNone(partial)
        self.assertEqual(self.payload, partial.read_bytes())
        self.assertEqual(1, len(requested))
        self.assertNotIn(self.first_hash, requested[0])
        self.assertNotIn(self.second_hash, requested[0])


if __name__ == "__main__":
    unittest.main()
