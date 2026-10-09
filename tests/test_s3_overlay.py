from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, parse_qsl, urlsplit

from app import app
from app.db import ensure_auth_database, get_db
from app.contact_store import ContactStore
from app.calendar_store import CalendarStore
from app.todo_store import TodoStore
from app.project_store import ProjectStore
from app.document_store import DocumentStore
from app.mail_client import MailStore, _owner_key
from app.object_store import ObjectStore
from app.s3_overlay import auth, credentials
from app.settings_store import SettingsStore


def _signing_key(secret: str, day: str, region: str = "us-east-1") -> bytes:
    def sign(key: bytes, value: str) -> bytes:
        return hmac.new(key, value.encode(), hashlib.sha256).digest()
    key = sign(("AWS4" + secret).encode(), day)
    return sign(sign(sign(key, region), "s3"), "aws4_request")


def signed_headers(method: str, path: str, access: str, secret: str, body: bytes = b"", extra: dict | None = None,
                   date: str | None = None) -> dict:
    date = date or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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


def presigned_path(path: str, access: str, secret: str, *, method: str = "GET") -> str:
    date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    day = date[:8]
    scope = f"{day}/us-east-1/s3/aws4_request"
    params = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{access}/{scope}",
              "X-Amz-Date": date, "X-Amz-Expires": "300", "X-Amz-SignedHeaders": "host"}
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items()))
    canonical = "\n".join((method, path, canonical_query, "host:localhost\n", "host", "UNSIGNED-PAYLOAD"))
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
        store = SettingsStore(self.root / "documents")
        settings = store.settings()
        settings["s3"]["enabled"] = True
        store.save(settings, "s3-test")
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

    def test_presigned_html_object_is_sandboxed_on_get_head_and_ranges(self):
        payload = b"<!doctype html><script>window.previewScriptRan = true;</script>"
        source = self.root / "documents" / "inbox" / "preview.html"
        source.write_bytes(payload)
        store = DocumentStore(self.root / "documents")
        store._scan_file(source, force_hash=True)
        document = store.get_document(source)
        path = f"/s3/simpleoffice/documents/{document['document_id']}/original/preview.html"
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                signed = presigned_path(path, self.keypair["access_key"], self.keypair["secret_key"], method=method)
                response = self.client.open(signed, method=method)
                self.assertEqual(200, response.status_code)
                self.assertEqual("text/html", response.mimetype)
                self.assertEqual(payload if method == "GET" else b"", response.data)
                self.assertEqual("sandbox", response.headers["Content-Security-Policy"])
                response.close()
        ranged = self.request("GET", path, extra={"Range": "bytes=0-14"})
        self.assertEqual(206, ranged.status_code)
        self.assertEqual(payload[:15], ranged.data)
        self.assertEqual("sandbox", ranged.headers["Content-Security-Policy"])
        ranged.close()

    def test_meta_schema_and_capabilities_are_stable_and_listed(self):
        overlay = self.request("GET", "/s3/simpleoffice/_meta/overlay.json")
        schema = self.request("GET", "/s3/simpleoffice/_meta/schema.json")
        capabilities = self.request("GET", "/s3/simpleoffice/_meta/capabilities.json")
        self.assertEqual(200, overlay.status_code)
        self.assertEqual(200, schema.status_code)
        self.assertEqual(200, capabilities.status_code)

        overlay_payload = overlay.get_json()
        schema_payload = schema.get_json()
        capabilities_payload = capabilities.get_json()
        self.assertEqual("simpleoffice-s3-overlay-v1", overlay_payload["schema"])
        self.assertIn("password-hashes", overlay_payload["excluded_secret_classes"])
        self.assertEqual("simpleoffice-s3-schema-v1", schema_payload["schema"])
        self.assertEqual("simpleoffice-s3-capabilities-v1", capabilities_payload["schema"])
        self.assertTrue(capabilities_payload["multipart_upload"])
        self.assertTrue(capabilities_payload["path_style"])
        self.assertFalse(capabilities_payload["virtual_hosted_style"])

        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=_meta%2F")
        self.assertEqual(200, listing.status_code)
        self.assertIn(b"_meta/overlay.json", listing.data)
        self.assertIn(b"_meta/schema.json", listing.data)
        self.assertIn(b"_meta/capabilities.json", listing.data)

        repeated = self.request("GET", "/s3/simpleoffice/_meta/capabilities.json")
        self.assertEqual(capabilities.data, repeated.data)
        self.assertEqual(capabilities.headers["ETag"], repeated.headers["ETag"])
        self.assertEqual(capabilities.headers["Last-Modified"], repeated.headers["Last-Modified"])

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

    def test_owned_mail_archive_is_listed_and_read_only(self):
        store = MailStore(self.root / "documents", b"test-s3-master-key")
        account = store.save_account("s3-user", {
            "host": "imap.example.test", "username": "s3-user@example.test",
            "security": "tls", "folder": "INBOX",
        }, "mail-password", True)
        archive = self.root / "documents" / "email" / _owner_key("s3-user") / account["id"] / "2026"
        archive.mkdir(parents=True)
        message = b"From: sender@example.test\r\nSubject: Archive test\r\n\r\nHello\r\n"
        (archive / "message.eml").write_bytes(message)

        key = f"email/{account['id']}/2026/message.eml"
        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=" + quote(f"email/{account['id']}/"))
        self.assertEqual(200, listing.status_code)
        self.assertIn(key.encode(), listing.data)
        response = self.request("GET", "/s3/simpleoffice/" + key)
        self.assertEqual(200, response.status_code)
        self.assertEqual(message, response.data)
        ranged = self.request("GET", "/s3/simpleoffice/" + key, extra={"Range": "bytes=0-4"})
        self.assertEqual(206, ranged.status_code)
        self.assertEqual(message[:5], ranged.data)
        with app.app_context():
            user_id = get_db().execute("SELECT id FROM user WHERE username='s3-user'").fetchone()["id"]
            get_db().execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at) VALUES(?,?,0,'2026-01-01T00:00:00+00:00')", (user_id, "mail"))
            get_db().commit()
        hidden = self.request("GET", "/s3/simpleoffice/" + key)
        self.assertEqual(404, hidden.status_code)
        denied = self.request("PUT", "/s3/simpleoffice/email/new.eml", b"not allowed")
        self.assertEqual(403, denied.status_code)

    def test_objects_are_exported_as_secret_filtered_json(self):
        item = ObjectStore(self.root / "documents").create({
            "name": "S3 Switch", "type": "Network", "description": "Core switch",
            "fields": "ports=24\napi_token=do-not-export",
        }, "s3-user")
        key = f"objects/{item['object_id']}.json"
        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=objects%2F")
        self.assertIn(key.encode(), listing.data)
        response = self.request("GET", "/s3/simpleoffice/" + key)
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("simpleoffice-object-v1", payload["schema"])
        self.assertEqual("24", payload["fields"]["ports"])
        self.assertNotIn("api_token", payload["fields"])
        self.assertNotIn("created_by", payload)

    def test_virtual_csv_vcard_and_manifest_exports_are_listed(self):
        contact = ContactStore(self.root / "documents").upsert(
            {"display_name": "Export Kontakt", "email": "export@example.test"}, "s3-user",
        )
        csv_response = self.request("GET", "/s3/simpleoffice/exports/contacts.csv")
        self.assertEqual(200, csv_response.status_code)
        self.assertIn(b"Export Kontakt", csv_response.data)
        vcard = self.request("GET", "/s3/simpleoffice/exports/contacts.vcf")
        self.assertEqual(200, vcard.status_code)
        self.assertIn(b"BEGIN:VCARD", vcard.data)
        manifest = self.request("GET", "/s3/simpleoffice/exports/manifest.jsonl")
        self.assertEqual(200, manifest.status_code)
        self.assertIn(f"contacts/{contact['contact_id']}.vcf".encode(), manifest.data)
        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=exports%2F")
        self.assertIn(b"exports/contacts.csv", listing.data)
        self.assertIn(b"exports/manifest.jsonl", listing.data)

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
        read_back = self.request("GET", path)
        self.assertEqual(200, read_back.status_code)
        self.assertEqual(b"incoming bytes", read_back.data)
        self.assertEqual(str(len(b"incoming bytes")), read_back.headers["Content-Length"])
        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=inbox%2F")
        self.assertEqual(200, listing.status_code)
        self.assertIn(b"<Key>inbox/from-client.txt</Key>", listing.data)
        self.assertNotIn(b"inbox/s3-user/from-client.txt", listing.data)
        with app.app_context():
            event = get_db().execute("SELECT actor_name, target_id, detail FROM security_event WHERE action='s3_inbox_uploaded' ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual("s3-user", event["actor_name"])
        self.assertEqual(json.loads(event["detail"])["source"], "s3-inbox")
        self.assertNotIn(self.keypair["secret_key"], event["detail"])

    def test_prefix_scoped_credentials_preserve_requested_listing_filter(self):
        contact = ContactStore(self.root / "documents").upsert(
            {"display_name": "Prefix Kontakt", "email": "prefix@example.test"},
            "s3-user",
        )
        with app.app_context():
            restricted = credentials.create("s3-user", "contacts only", ["read"], "contacts/", 7)
            stored_restricted = credentials.get(restricted["access_key"])
        self.assertEqual("contacts/", restricted["prefix"])
        self.assertEqual("contacts/", stored_restricted["prefix"])

        root_path = "/s3/simpleoffice?list-type=2&delimiter=%2F"
        root_headers = signed_headers(
            "GET", root_path, restricted["access_key"], restricted["secret_key"],
        )
        root_listing = self.client.get(root_path, headers=root_headers)
        self.assertEqual(200, root_listing.status_code)
        self.assertIn(b"<Prefix></Prefix>", root_listing.data)
        self.assertIn(b"<CommonPrefixes><Prefix>contacts/</Prefix></CommonPrefixes>", root_listing.data)
        self.assertNotIn(b"<Prefix>documents/</Prefix>", root_listing.data)

        disjoint_path = "/s3/simpleoffice?list-type=2&prefix=documents%2F"
        disjoint_headers = signed_headers(
            "GET", disjoint_path, restricted["access_key"], restricted["secret_key"],
        )
        disjoint = self.client.get(disjoint_path, headers=disjoint_headers)
        self.assertEqual(200, disjoint.status_code)
        self.assertIn(b"<Prefix>documents/</Prefix>", disjoint.data)
        self.assertIn(b"<KeyCount>0</KeyCount>", disjoint.data)
        self.assertNotIn(contact["contact_id"].encode(), disjoint.data)

    def test_list_objects_v2_zero_limit_and_response_selection_fields(self):
        zero = self.request("GET", "/s3/simpleoffice?list-type=2&max-keys=0")
        self.assertEqual(200, zero.status_code)
        self.assertIn(b"<KeyCount>0</KeyCount>", zero.data)
        self.assertIn(b"<MaxKeys>0</MaxKeys>", zero.data)
        self.assertIn(b"<IsTruncated>false</IsTruncated>", zero.data)
        self.assertNotIn(b"<Contents>", zero.data)
        self.assertNotIn(b"<NextContinuationToken>", zero.data)

        selected = self.request(
            "GET",
            "/s3/simpleoffice?list-type=2&delimiter=%2F&encoding-type=url&start-after=_meta%2Fcapabilities.json",
        )
        self.assertEqual(200, selected.status_code)
        self.assertIn(b"<Delimiter>/</Delimiter>", selected.data)
        self.assertIn(b"<EncodingType>url</EncodingType>", selected.data)
        self.assertIn(b"<StartAfter>_meta/capabilities.json</StartAfter>", selected.data)

        invalid_encoding = self.request("GET", "/s3/simpleoffice?list-type=2&encoding-type=invalid")
        self.assertEqual(400, invalid_encoding.status_code)
        self.assertIn(b"<Code>InvalidArgument</Code>", invalid_encoding.data)

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

        wrong_prefix = f"/s3/simpleoffice?continuation-token={quote(token, safe='')}&list-type=2&max-keys=1&prefix=contacts%2F"
        self.assertEqual(400, self.request("GET", wrong_prefix).status_code)

        with app.app_context():
            other = credentials.create("s3-user", "other client", ["read"], "", 7)
        other_headers = signed_headers("GET", next_path, other["access_key"], other["secret_key"])
        other_response = self.client.get(next_path, headers=other_headers)
        self.assertEqual(400, other_response.status_code)
        self.assertIn(b"<Code>InvalidArgument</Code>", other_response.data)

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
        marker = re.search(rb"<Key>([^<]+)", first.data).group(1).decode()
        self.assertNotIn(b"<NextMarker>", first.data)
        second = self.request("GET", f"/s3/simpleoffice?max-keys=1&marker={quote(marker, safe='')}&prefix={quote(prefix, safe='')}")
        self.assertEqual(200, second.status_code)
        self.assertIn(b"original/readme.txt", second.data)
        self.assertIn(f"<Marker>{marker}</Marker>".encode(), second.data)

        delimited = self.request("GET", "/s3/simpleoffice?delimiter=%2F&max-keys=1")
        self.assertEqual(200, delimited.status_code)
        self.assertIn(b"<NextMarker>", delimited.data)

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

    def test_invoice_export_requires_contact_management_and_omits_internal_history(self):
        contacts = ContactStore(self.root / "documents")
        own = contacts.upsert({"display_name": "Eigener Kunde"}, "s3-user")
        reader_contact = contacts.upsert({"display_name": "Nur lesbarer Kunde"}, "other-user")
        contacts.share(reader_contact["contact_id"], [], "other-user", readers=["s3-user"])
        private = contacts.upsert({"display_name": "Privater Kunde"}, "other-user")

        invoice_dir = self.root / "documents" / ".simpleoffice-meta" / "invoices"
        invoice_dir.mkdir(parents=True)
        def add_invoice(invoice_id: str, contact_id: str) -> None:
            row = {
                "invoice_id": invoice_id, "invoice_number": f"RE-{invoice_id}", "contact_id": contact_id,
                "status": "open", "issue_date": "2026-09-01", "due_date": "2026-09-30",
                "currency": "EUR", "seller": {"name": "Verkäufer", "iban": "DE00SECRET", "internal_secret": "omit-me"},
                "buyer": {"name": "Kunde"}, "lines": [{"description": "Leistung", "gross": "119.00"}],
                "totals": {"net": "100.00", "tax": "19.00", "gross": "119.00"},
                "payments": [], "history": [{"actor": "private-user", "note": "intern"}],
                "created_at": "2026-09-01T10:00:00+00:00", "updated_at": "2026-09-02T10:00:00+00:00",
            }
            (invoice_dir / f"{invoice_id}.json").write_text(json.dumps(row), encoding="utf-8")

        add_invoice("own-1", own["contact_id"])
        add_invoice("reader-1", reader_contact["contact_id"])
        add_invoice("private-1", private["contact_id"])
        own_key = "invoices/own-1.json"
        own_response = self.request("GET", f"/s3/simpleoffice/{own_key}")
        self.assertEqual(200, own_response.status_code)
        self.assertEqual("application/json", own_response.mimetype)
        self.assertIn(b"DE00SECRET", own_response.data)
        self.assertNotIn(b"omit-me", own_response.data)
        self.assertNotIn(b"history", own_response.data)
        self.assertNotIn(b"private-user", own_response.data)
        self.assertEqual(404, self.request("GET", "/s3/simpleoffice/invoices/reader-1.json").status_code)
        self.assertEqual(404, self.request("GET", "/s3/simpleoffice/invoices/private-1.json").status_code)

        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=invoices%2F")
        self.assertIn(own_key.encode(), listing.data)
        self.assertNotIn(b"reader-1", listing.data)
        self.assertNotIn(b"private-1", listing.data)
        with app.app_context():
            user_id = get_db().execute("SELECT id FROM user WHERE username='s3-user'").fetchone()["id"]
            get_db().execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at,updated_by) VALUES(?,?,0,CURRENT_TIMESTAMP,NULL)", (user_id, "contacts"))
            get_db().commit()
        self.assertEqual(404, self.request("GET", f"/s3/simpleoffice/{own_key}").status_code)
        hidden_listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=invoices%2F")
        self.assertNotIn(own_key.encode(), hidden_listing.data)

    def test_calendar_ics_export_uses_visibility_and_calendar_permission(self):
        calendar = CalendarStore(self.root / "documents")
        calendar.add("Eigener Termin", "Privater Inhalt", "2026-10-01T09:00", "2026-10-01T10:00", "", "s3-user")
        calendar.add("Fremder Termin", "Nicht freigegeben", "2026-10-02T09:00", "2026-10-02T10:00", "", "other-user")
        key = "calendar/events.ics"

        first = self.request("GET", f"/s3/simpleoffice/{key}")
        self.assertEqual(200, first.status_code)
        self.assertEqual("text/calendar", first.mimetype)
        self.assertIn(b"SUMMARY:Eigener Termin", first.data)
        self.assertNotIn(b"Fremder Termin", first.data)
        second = self.request("GET", f"/s3/simpleoffice/{key}")
        self.assertEqual(first.data, second.data)
        self.assertEqual(first.headers["ETag"], second.headers["ETag"])
        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=calendar%2F")
        self.assertIn(key.encode(), listing.data)

        with app.app_context():
            user_id = get_db().execute("SELECT id FROM user WHERE username='s3-user'").fetchone()["id"]
            for feature in ("documents", "contacts", "projects"):
                get_db().execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at,updated_by) VALUES(?,?,0,CURRENT_TIMESTAMP,NULL)", (user_id, feature))
            get_db().commit()
        self.assertEqual(200, self.request("GET", f"/s3/simpleoffice/{key}").status_code)
        with app.app_context():
            get_db().execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at,updated_by) VALUES(?,?,0,CURRENT_TIMESTAMP,NULL)", (user_id, "calendar"))
            get_db().commit()
        self.assertEqual(404, self.request("GET", f"/s3/simpleoffice/{key}").status_code)

    def test_tasks_vtodo_export_uses_existing_task_list_sharing(self):
        todos = TodoStore(self.root / "documents")
        own = todos.add("Eigene Aufgabe", "s3-user", {"due": "2026-10-04"})
        private = todos.add("Private fremde Aufgabe", "other-user")
        shared_list = todos.create_list({"name": "Geteilte Aufgaben"}, "other-user")
        todos.update_list(shared_list["list_id"], {"permissions": {"s3-user": ["read"]}}, "other-user")
        shared = todos.add("Freigegebene Aufgabe", "other-user", {"list_id": shared_list["list_id"]})

        own_key = f"tasks/{own['id']}.ics"
        shared_key = f"tasks/{shared['id']}.ics"
        private_key = f"tasks/{private['id']}.ics"
        own_response = self.request("GET", f"/s3/simpleoffice/{own_key}")
        shared_response = self.request("GET", f"/s3/simpleoffice/{shared_key}")
        self.assertEqual(200, own_response.status_code)
        self.assertEqual(200, shared_response.status_code)
        self.assertIn(b"BEGIN:VTODO", own_response.data)
        self.assertIn(b"SUMMARY:Freigegebene Aufgabe", shared_response.data)
        self.assertEqual(404, self.request("GET", f"/s3/simpleoffice/{private_key}").status_code)
        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=tasks%2F")
        self.assertIn(own_key.encode(), listing.data)
        self.assertIn(shared_key.encode(), listing.data)
        self.assertNotIn(private_key.encode(), listing.data)

    def test_project_json_export_is_feature_gated_and_uses_visible_tasks(self):
        with app.app_context():
            user_id = get_db().execute("SELECT id FROM user WHERE username='s3-user'").fetchone()["id"]
            for feature in ("documents", "contacts", "calendar"):
                get_db().execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at,updated_by) VALUES(?,?,0,CURRENT_TIMESTAMP,NULL)", (user_id, feature))
            get_db().commit()

        project = ProjectStore(self.root / "documents").create_project(
            {"title": "S3 Projekt", "description": "Sichtbare Beschreibung"}, "s3-user",
        )
        TodoStore(self.root / "documents").add(
            "Projektaufgabe", "s3-user", {"project_id": project["project_id"], "due": "2026-10-05"},
        )
        key = f"projects/{project['project_id']}.json"
        response = self.request("GET", f"/s3/simpleoffice/{key}")
        self.assertEqual(200, response.status_code)
        self.assertEqual("application/json", response.mimetype)
        self.assertIn(b"Sichtbare Beschreibung", response.data)
        self.assertIn(b"Projektaufgabe", response.data)
        self.assertNotIn(b"time_groups", response.data)

        listing = self.request("GET", "/s3/simpleoffice?list-type=2&prefix=projects%2F")
        self.assertIn(key.encode(), listing.data)
        with app.app_context():
            get_db().execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at,updated_by) VALUES(?,?,0,CURRENT_TIMESTAMP,NULL)", (user_id, "projects"))
            get_db().commit()
        self.assertEqual(404, self.request("GET", f"/s3/simpleoffice/{key}").status_code)

    def test_presigned_get_is_read_only_and_verifies_signature(self):
        path = presigned_path("/s3/", self.keypair["access_key"], self.keypair["secret_key"])
        self.assertEqual(200, self.client.get(path).status_code)
        forbidden = presigned_path("/s3/simpleoffice/inbox/a", self.keypair["access_key"], self.keypair["secret_key"])
        self.assertEqual(403, self.client.put(forbidden, data=b"data").status_code)

    def test_presigned_head_and_configured_clock_skew(self):
        key = f"documents/{self.document['document_id']}/original/readme.txt"
        object_path = "/s3/simpleoffice/" + key
        head_path = presigned_path(object_path, self.keypair["access_key"], self.keypair["secret_key"], method="HEAD")
        head = self.client.head(head_path)
        self.assertEqual(200, head.status_code)
        self.assertEqual("20", head.headers["Content-Length"])

        old_skew = app.config.get("S3_OVERLAY_CLOCK_SKEW_SECONDS")
        try:
            app.config["S3_OVERLAY_CLOCK_SKEW_SECONDS"] = 1
            stale_date = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y%m%dT%H%M%SZ")
            headers = signed_headers(
                "GET",
                object_path,
                self.keypair["access_key"],
                self.keypair["secret_key"],
                date=stale_date,
            )
            stale = self.client.get(object_path, headers=headers)
            self.assertEqual(403, stale.status_code)
            self.assertIn(b"outside the allowed clock skew", stale.data)
        finally:
            if old_skew is None:
                app.config.pop("S3_OVERLAY_CLOCK_SKEW_SECONDS", None)
            else:
                app.config["S3_OVERLAY_CLOCK_SKEW_SECONDS"] = old_skew

    def test_upload_only_credential_can_probe_bucket_without_read_scope(self):
        with app.app_context():
            upload_only = credentials.create("s3-user", "upload only", ["inbox:put"], "inbox/", 7)

        for method, path in (
            ("HEAD", "/s3/simpleoffice"),
            ("GET", "/s3/simpleoffice?location="),
            ("GET", "/s3/simpleoffice?versioning="),
        ):
            headers = signed_headers(
                method, path, upload_only["access_key"], upload_only["secret_key"],
            )
            response = self.client.open(path, method=method, headers=headers)
            self.assertEqual(200, response.status_code)

        list_path = "/s3/simpleoffice?list-type=2"
        headers = signed_headers(
            "GET", list_path, upload_only["access_key"], upload_only["secret_key"],
        )
        listing = self.client.get(list_path, headers=headers)
        self.assertEqual(403, listing.status_code)

    def test_unreadable_encrypted_credential_fails_as_s3_auth_error(self):
        path = "/s3/simpleoffice/_meta/overlay.json"
        headers = signed_headers(
            "GET", path, self.keypair["access_key"], self.keypair["secret_key"],
        )
        previous_secret = app.config["SECRET_KEY"]
        try:
            app.config["SECRET_KEY"] = "different-master-key"
            response = self.client.get(path, headers=headers)
        finally:
            app.config["SECRET_KEY"] = previous_secret
        self.assertEqual(403, response.status_code)
        self.assertIn(b"<Code>SignatureDoesNotMatch</Code>", response.data)
        self.assertEqual("application/xml", response.mimetype)

    def test_bad_signature_and_forbidden_write_are_rejected(self):
        path = "/s3/simpleoffice/documents/missing/original/x.bin"
        headers = signed_headers("GET", path, self.keypair["access_key"], self.keypair["secret_key"])
        headers["Authorization"] = headers["Authorization"].replace("Signature=", "Signature=0")
        bad = self.client.get(path, headers=headers)
        self.assertEqual(403, bad.status_code)
        self.assertIn(b"SignatureDoesNotMatch", bad.data)
        forbidden = self.request("PUT", "/s3/simpleoffice/documents/nope/original/nope", b"x")
        self.assertEqual(403, forbidden.status_code)

    def test_inbox_put_verifies_optional_sha256_checksum_header(self):
        body = b"checksum protected upload"
        checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        uploaded = self.request(
            "PUT",
            "/s3/simpleoffice/inbox/checksum.txt",
            body,
            extra={"X-Amz-Checksum-Sha256": checksum},
        )
        self.assertEqual(200, uploaded.status_code)
        self.assertEqual(checksum, uploaded.headers["x-amz-checksum-sha256"])

        bad_checksum = base64.b64encode(hashlib.sha256(b"different").digest()).decode("ascii")
        rejected = self.request(
            "PUT",
            "/s3/simpleoffice/inbox/bad-checksum.txt",
            body,
            extra={"X-Amz-Checksum-Sha256": bad_checksum},
        )
        self.assertEqual(400, rejected.status_code)
        self.assertIn(b"<Code>BadDigest</Code>", rejected.data)
        self.assertFalse((self.root / "documents" / "inbox" / "s3-user" / "bad-checksum.txt").exists())

    def test_copy_tagging_acl_multipart_and_delete_mutations_are_rejected(self):
        copy_path = "/s3/simpleoffice/inbox/copied.bin"
        copied = self.request(
            "PUT",
            copy_path,
            b"",
            extra={"X-Amz-Copy-Source": "/simpleoffice/documents/source/original/file.bin"},
        )
        self.assertEqual(403, copied.status_code)
        self.assertIn(b"AccessDenied", copied.data)

        tagged = self.request("PUT", "/s3/simpleoffice/inbox/tagged.bin", b"data", extra={"X-Amz-Tagging": "a=b"})
        self.assertEqual(403, tagged.status_code)

        acl = self.request("PUT", "/s3/simpleoffice/inbox/acl.bin", b"data", extra={"X-Amz-Acl": "private"})
        self.assertEqual(403, acl.status_code)

        invalid_part = self.request("PUT", "/s3/simpleoffice/inbox/part.bin?partNumber=1&uploadId=test", b"data")
        self.assertEqual(404, invalid_part.status_code)

        deleted = self.request("DELETE", "/s3/simpleoffice/inbox/from-client.txt")
        self.assertEqual(403, deleted.status_code)

    def test_multipart_inbox_upload_stages_parts_and_imports_only_on_completion(self):
        key = "inbox/multipart-note.txt"
        initiate = self.request("POST", f"/s3/simpleoffice/{key}?uploads=")
        self.assertEqual(200, initiate.status_code)
        upload_id = re.search(rb"<UploadId>([^<]+)</UploadId>", initiate.data).group(1).decode()

        content = b"multipart inbox content"
        part_path = f"/s3/simpleoffice/{key}?partNumber=1&uploadId={upload_id}"
        part = self.request("PUT", part_path, content)
        self.assertEqual(200, part.status_code)
        self.assertFalse((self.root / "documents" / "inbox" / "s3-user" / "multipart-note.txt").exists())

        completion = ("<CompleteMultipartUpload><Part><PartNumber>1</PartNumber>"
                      + f"<ETag>{part.headers['ETag']}</ETag></Part></CompleteMultipartUpload>").encode()
        complete = self.request("POST", f"/s3/simpleoffice/{key}?uploadId={upload_id}", completion)
        self.assertEqual(200, complete.status_code)
        self.assertRegex(complete.headers["ETag"], r'^"[a-f0-9]{32}-1"$')
        imported = self.root / "documents" / "inbox" / "s3-user" / "multipart-note.txt"
        self.assertEqual(content, imported.read_bytes())
        read_back = self.request("GET", "/s3/simpleoffice/" + key)
        self.assertEqual(content, read_back.data)

    def test_multipart_upload_listing_parts_and_abort_are_bounded_to_credential(self):
        key = "inbox/aborted.bin"
        initiate = self.request("POST", f"/s3/simpleoffice/{key}?uploads=")
        self.assertEqual(200, initiate.status_code)
        upload_id = re.search(rb"<UploadId>([^<]+)</UploadId>", initiate.data).group(1).decode()
        content = b"part data"
        part_path = f"/s3/simpleoffice/{key}?partNumber=1&uploadId={upload_id}"
        self.assertEqual(200, self.request("PUT", part_path, content).status_code)

        parts = self.request("GET", f"/s3/simpleoffice/{key}?uploadId={upload_id}&max-parts=1")
        self.assertEqual(200, parts.status_code)
        self.assertIn(b"<PartNumber>1</PartNumber>", parts.data)
        uploads = self.request("GET", "/s3/simpleoffice/?uploads=&max-uploads=1")
        self.assertEqual(200, uploads.status_code)
        self.assertIn(upload_id.encode(), uploads.data)

        aborted = self.request("DELETE", f"/s3/simpleoffice/{key}?uploadId={upload_id}")
        self.assertEqual(204, aborted.status_code)
        missing = self.request("GET", f"/s3/simpleoffice/{key}?uploadId={upload_id}")
        self.assertEqual(404, missing.status_code)
        self.assertFalse((self.root / "documents" / "inbox" / "s3-user" / "aborted.bin").exists())

    def test_failed_multipart_completion_can_be_retried(self):
        key = "inbox/retry-after-scan-error.txt"
        initiate = self.request("POST", f"/s3/simpleoffice/{key}?uploads=")
        self.assertEqual(200, initiate.status_code)
        upload_id = re.search(rb"<UploadId>([^<]+)</UploadId>", initiate.data).group(1).decode()
        content = b"retry after a temporary scanner failure"
        part_path = f"/s3/simpleoffice/{key}?partNumber=1&uploadId={upload_id}"
        part = self.request("PUT", part_path, content)
        self.assertEqual(200, part.status_code)
        manifest = ("<CompleteMultipartUpload><Part><PartNumber>1</PartNumber>"
                    + f"<ETag>{part.headers['ETag']}</ETag></Part></CompleteMultipartUpload>").encode()
        complete_path = f"/s3/simpleoffice/{key}?uploadId={upload_id}"

        from app.s3_overlay.routes import S3Error
        with patch("app.s3_overlay.routes._scan_inbox_upload",
                   side_effect=S3Error("ServiceUnavailable", "simulated scanner outage", 503)):
            failed = self.request("POST", complete_path, manifest)
        self.assertEqual(503, failed.status_code)

        retried = self.request("POST", complete_path, manifest)
        self.assertEqual(200, retried.status_code)
        self.assertEqual(content, (self.root / "documents" / "inbox" / "s3-user" / "retry-after-scan-error.txt").read_bytes())

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
