from __future__ import annotations

import hashlib
import hmac
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, parse_qsl, urlsplit

from app import app
from app.db import ensure_auth_database, get_db
from app.document_store import DocumentStore
from app.s3_overlay import credentials


def _signing_key(secret: str, day: str, region: str = "us-east-1") -> bytes:
    def sign(key: bytes, value: str) -> bytes:
        return hmac.new(key, value.encode(), hashlib.sha256).digest()
    key = sign(("AWS4" + secret).encode(), day)
    return sign(sign(sign(key, region), "s3"), "aws4_request")


def signed_headers(method: str, path: str, access: str, secret: str, body: bytes = b"", extra: dict | None = None) -> dict:
    date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    day = date[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {"Host": "localhost", "X-Amz-Date": date, "X-Amz-Content-Sha256": payload_hash, **(extra or {})}
    signed = ";".join(sorted(name.casefold() for name in headers))
    canonical_headers = "".join(f"{name.casefold()}:{' '.join(value.strip().split())}\n" for name, value in sorted(headers.items(), key=lambda row: row[0].casefold()))
    scope = f"{day}/us-east-1/s3/aws4_request"
    parts = urlsplit(path)
    query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(parse_qsl(parts.query, keep_blank_values=True)))
    canonical = "\n".join((method, parts.path, query, canonical_headers, signed, payload_hash))
    string_to_sign = "\n".join(("AWS4-HMAC-SHA256", date, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    signature = hmac.new(_signing_key(secret, day), string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed}, Signature={signature}"
    return headers


def presigned_path(path: str, access: str, secret: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    day = date[:8]
    scope = f"{day}/us-east-1/s3/aws4_request"
    params = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{access}/{scope}",
              "X-Amz-Date": date, "X-Amz-Expires": "300", "X-Amz-SignedHeaders": "host"}
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items()))
    canonical = "\n".join(("GET", path, canonical_query, "host:localhost\n", "host", "UNSIGNED-PAYLOAD"))
    string_to_sign = "\n".join(("AWS4-HMAC-SHA256", date, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    params["X-Amz-Signature"] = hmac.new(_signing_key(secret, day), string_to_sign.encode(), hashlib.sha256).hexdigest()
    encoded = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items()))
    return f"{path}?{encoded}"


class S3OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = {key: app.config.get(key) for key in ("TESTING", "SECRET_KEY", "DOCUMENT_ROOT", "DATABASE", "DATABASE_FILEDIR", "S3_OVERLAY_ENABLED", "S3_OVERLAY_REGION", "S3_OVERLAY_MAX_UPLOAD_BYTES")}
        app.config.update(TESTING=True, SECRET_KEY="test-s3-master-key", DOCUMENT_ROOT=str(self.root / "documents"),
                          DATABASE=str(self.root / "auth.sqlite"), DATABASE_FILEDIR=str(self.root / "db"),
                          S3_OVERLAY_ENABLED=True, S3_OVERLAY_REGION="us-east-1", S3_OVERLAY_MAX_UPLOAD_BYTES=2 * 1024 * 1024)
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute("INSERT INTO user(username,password,is_admin,is_disabled,auth_version) VALUES('s3-user','x',0,0,1)")
            db.commit()
        (self.root / "documents").mkdir(parents=True, exist_ok=True)
        inbox = self.root / "documents" / "inbox"
        inbox.mkdir()
        (inbox / "readme.txt").write_bytes(b"S3 overlay test data")
        doc = DocumentStore(self.root / "documents")
        doc.scan()
        self.document = doc.get_document(inbox / "readme.txt")
        with app.app_context():
            self.keypair = credentials.create("s3-user", "test client", ["read", "inbox:put"], "", 7)
        self.client = app.test_client()

    def tearDown(self):
        for key, value in self.old.items():
            app.config[key] = value
        self.temp.cleanup()

    def request(self, method: str, path: str, body: bytes = b"", extra: dict | None = None):
        headers = signed_headers(method, path, self.keypair["access_key"], self.keypair["secret_key"], body, extra)
        return self.client.open(path, method=method, data=body, headers=headers)

    def test_sigv4_lists_and_reads_document_with_range_and_head(self):
        bucket = self.request("GET", "/s3/")
        self.assertEqual(200, bucket.status_code)
        self.assertIn(b"simpleoffice", bucket.data)
        key = f"documents/{self.document['document_id']}/original/readme.txt"
        path = "/s3/simpleoffice/" + key
        response = self.request("GET", path)
        self.assertEqual(b"S3 overlay test data", response.data)
        head = self.request("HEAD", path)
        self.assertEqual("20", head.headers["Content-Length"])
        ranged = self.request("GET", path, extra={"Range": "bytes=3-9"})
        self.assertEqual(206, ranged.status_code)
        self.assertEqual(b"overlay", ranged.data)
        self.assertEqual("bytes 3-9/20", ranged.headers["Content-Range"])

    def test_inbox_put_is_idempotent_and_rejects_different_content(self):
        path = "/s3/simpleoffice/inbox/from-client.txt"
        uploaded = self.request("PUT", path, b"incoming bytes")
        self.assertEqual(200, uploaded.status_code)
        retry = self.request("PUT", path, b"incoming bytes")
        self.assertEqual(200, retry.status_code)
        collision = self.request("PUT", path, b"different")
        self.assertEqual(412, collision.status_code)
        self.assertTrue((self.root / "documents" / "inbox" / "s3-user" / "from-client.txt").is_file())

    def test_list_objects_v2_paginates_and_checks_continuation_token(self):
        prefix = f"documents/{self.document['document_id']}/"
        first_path = f"/s3/simpleoffice?list-type=2&max-keys=1&prefix={quote(prefix, safe='')}"
        first = self.request("GET", first_path)
        self.assertEqual(200, first.status_code)
        self.assertIn(b"IsTruncated>true", first.data)
        import re
        token = re.search(rb"<NextContinuationToken>([^<]+)", first.data).group(1).decode()
        next_path = f"/s3/simpleoffice?continuation-token={quote(token, safe='')}&list-type=2&max-keys=1&prefix={quote(prefix, safe='')}"
        second = self.request("GET", next_path)
        self.assertEqual(200, second.status_code)
        self.assertIn(b"original/readme.txt", second.data)
        tampered = next_path.replace("continuation-token=", "continuation-token=x")
        self.assertEqual(400, self.request("GET", tampered).status_code)

    def test_presigned_get_is_read_only_and_verifies_signature(self):
        path = presigned_path("/s3/", self.keypair["access_key"], self.keypair["secret_key"])
        self.assertEqual(200, self.client.get(path).status_code)
        forbidden = presigned_path("/s3/simpleoffice/inbox/a", self.keypair["access_key"], self.keypair["secret_key"])
        self.assertEqual(403, self.client.put(forbidden, data=b"data").status_code)

    def test_bad_signature_and_forbidden_write_are_rejected(self):
        path = "/s3/simpleoffice/documents/missing/original/x.bin"
        headers = signed_headers("GET", path, self.keypair["access_key"], self.keypair["secret_key"])
        headers["Authorization"] = headers["Authorization"].replace("Signature=", "Signature=0")
        bad = self.client.get(path, headers=headers)
        self.assertEqual(403, bad.status_code)
        self.assertIn(b"SignatureDoesNotMatch", bad.data)
        forbidden = self.request("PUT", "/s3/simpleoffice/documents/nope/original/nope", b"x")
        self.assertEqual(403, forbidden.status_code)

    def test_secret_is_encrypted_at_rest_and_revocation_takes_effect(self):
        secret = self.keypair["secret_key"].encode()
        database = (self.root / "documents" / ".simpleoffice-meta" / "s3-overlay.sqlite3").read_bytes()
        self.assertNotIn(secret, database)
        with app.app_context():
            self.assertTrue(credentials.revoke("s3-user", self.keypair["access_key"]))
            self.assertIsNone(credentials.get(self.keypair["access_key"]))
        denied = self.request("GET", "/s3/")
        self.assertEqual(403, denied.status_code)

    def test_overlay_is_disabled_by_default(self):
        app.config["S3_OVERLAY_ENABLED"] = False
        response = self.client.get("/s3/")
        self.assertEqual(404, response.status_code)
        self.assertIn(b"NotFound", response.data)


if __name__ == "__main__":
    unittest.main()
