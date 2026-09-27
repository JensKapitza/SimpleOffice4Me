from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, parse_qsl, urlsplit

from app import app
from app.db import ensure_auth_database, get_db
from app.contact_store import ContactStore
from app.document_store import DocumentStore
from app.s3_overlay import auth, credentials


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

    def test_http_date_conditionals_and_unsatisfied_range_headers(self):
        key = f"documents/{self.document['document_id']}/original/readme.txt"
        path = "/s3/simpleoffice/" + key
        head = self.request("HEAD", path)
        last_modified = head.headers["Last-Modified"]
        not_modified = self.request("GET", path, extra={"If-Modified-Since": last_modified})
        self.assertEqual(304, not_modified.status_code)
        self.assertIn("ETag", not_modified.headers)
        not_changed = self.request("GET", path, extra={"If-Unmodified-Since": last_modified})
        self.assertEqual(200, not_changed.status_code)
        changed = self.request("GET", path, extra={"If-Unmodified-Since": "Sun, 06 Nov 1994 08:49:37 GMT"})
        self.assertEqual(412, changed.status_code)
        invalid = self.request("GET", path, extra={"Range": "bytes=999-"})
        self.assertEqual(416, invalid.status_code)
        self.assertEqual("bytes */20", invalid.headers["Content-Range"])

    def test_inbox_put_is_idempotent_and_rejects_different_content(self):
        path = "/s3/simpleoffice/inbox/from-client.txt"
        uploaded = self.request("PUT", path, b"incoming bytes")
        self.assertEqual(200, uploaded.status_code)
        retry = self.request("PUT", path, b"incoming bytes")
        self.assertEqual(200, retry.status_code)
        collision = self.request("PUT", path, b"different")
        self.assertEqual(412, collision.status_code)
        self.assertTrue((self.root / "documents" / "inbox" / "s3-user" / "from-client.txt").is_file())
        with app.app_context():
            event = get_db().execute("SELECT actor_name, target_id, detail FROM security_event WHERE action='s3_inbox_uploaded' ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual("s3-user", event["actor_name"])
        self.assertEqual(json.loads(event["detail"])["source"], "s3-inbox")
        self.assertNotIn(self.keypair["secret_key"], event["detail"])

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

    def test_list_objects_v2_delimiter_paginates_distinct_prefixes(self):
        second = self.root / "documents" / "inbox" / "s3-user" / "second.txt"
        second.parent.mkdir(parents=True, exist_ok=True)
        second.write_bytes(b"another document")
        store = DocumentStore(self.root / "documents")
        store.scan()
        first_path = "/s3/simpleoffice?list-type=2&max-keys=1&prefix=documents%2F&delimiter=%2F"
        first = self.request("GET", first_path)
        self.assertEqual(200, first.status_code)
        self.assertIn(b"KeyCount>1", first.data)
        import re
        prefixes = re.findall(rb"<CommonPrefixes><Prefix>(.*?)</Prefix></CommonPrefixes>", first.data)
        self.assertEqual(1, len(prefixes))
        token = re.search(rb"<NextContinuationToken>([^<]+)", first.data).group(1).decode()
        next_path = f"/s3/simpleoffice?continuation-token={quote(token, safe='')}&list-type=2&max-keys=1&prefix=documents%2F&delimiter=%2F"
        second_page = self.request("GET", next_path)
        next_prefixes = re.findall(rb"<CommonPrefixes><Prefix>(.*?)</Prefix></CommonPrefixes>", second_page.data)
        self.assertEqual(200, second_page.status_code)
        self.assertEqual(1, len(next_prefixes))
        self.assertNotEqual(prefixes[0], next_prefixes[0])

    def test_list_objects_v1_marker_paginates(self):
        prefix = f"documents/{self.document['document_id']}/"
        first = self.request("GET", f"/s3/simpleoffice?max-keys=1&prefix={quote(prefix, safe='')}")
        self.assertEqual(200, first.status_code)
        self.assertIn(b"<ListBucketResult", first.data)
        import re
        marker = re.search(rb"<NextMarker>([^<]+)", first.data).group(1).decode()
        second = self.request("GET", f"/s3/simpleoffice?max-keys=1&marker={quote(marker, safe='')}&prefix={quote(prefix, safe='')}")
        self.assertEqual(200, second.status_code)
        self.assertIn(b"original/readme.txt", second.data)
        self.assertIn(f"<Marker>{marker}</Marker>".encode(), second.data)

    def test_contacts_vcard_export_uses_contact_sharing_permissions(self):
        contacts = ContactStore(self.root / "documents")
        own = contacts.upsert({"display_name": "Eigener Kontakt", "email": "own@example.test"}, "s3-user")
        private = contacts.upsert({"display_name": "Privater Kontakt", "email": "private@example.test"}, "other-user")
        own_key = f"contacts/{own['contact_id']}.vcf"
        private_key = f"contacts/{private['contact_id']}.vcf"

        own_card = self.request("GET", f"/s3/simpleoffice/{own_key}")
        self.assertEqual(200, own_card.status_code)
        self.assertEqual("text/vcard", own_card.mimetype)
        self.assertIn("charset=utf-8", own_card.content_type)
        self.assertIn(b"FN:Eigener Kontakt", own_card.data)
        self.assertEqual(404, self.request("GET", f"/s3/simpleoffice/{private_key}").status_code)

        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=contacts%2F")
        self.assertEqual(200, listing.status_code)
        self.assertIn(own_key.encode(), listing.data)
        self.assertNotIn(private_key.encode(), listing.data)
        with app.app_context():
            user_id = get_db().execute("SELECT id FROM user WHERE username='s3-user'").fetchone()["id"]
            get_db().execute(
                "INSERT INTO user_permission(user_id,feature,enabled,updated_at,updated_by) VALUES(?,?,0,CURRENT_TIMESTAMP,NULL)",
                (user_id, "contacts"),
            )
            get_db().commit()
        self.assertEqual(404, self.request("GET", f"/s3/simpleoffice/{own_key}").status_code)
        hidden_listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=contacts%2F")
        self.assertNotIn(own_key.encode(), hidden_listing.data)

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

    def test_authorization_parser_bounds_untrusted_header_length(self):
        header = "AWS4-HMAC-SHA256 " + ("  " * 3000) + "Credential=x, SignedHeaders=host, Signature=s"
        parsed = auth._authorization_fields(header)
        self.assertEqual("x", parsed["Credential"])
        with self.assertRaises(auth.SignatureError):
            auth._authorization_fields("AWS4-HMAC-SHA256 " + (" " * 8192))

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
