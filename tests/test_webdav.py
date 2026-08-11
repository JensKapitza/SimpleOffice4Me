import base64
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from unittest import mock
from xml.etree import ElementTree

from app import app
from app.attachment_security import ClamAV, ScanResult
from app.db import ensure_auth_database
from app.document_store import CONTROL_DIR, POLICY_FILE, DocumentStore
from app.webdav import MAX_ACTIVE_CREDENTIALS, activate, revoke


class WebDavDocumentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "MAX_CONTENT_LENGTH", "WEBDAV_QUOTA_BYTES", "WEBDAV_UPLOAD_SCAN", "WEBDAV_QUARANTINE_BYTES")}
        root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(root), MAX_CONTENT_LENGTH=1024 * 1024, WEBDAV_QUOTA_BYTES=0, WEBDAV_UPLOAD_SCAN=False, WEBDAV_QUARANTINE_BYTES=1024 * 1024)
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "browser-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"})
        root.mkdir(parents=True, exist_ok=True)
        (root / "angebot.odt").write_bytes(b"first office version")
        self.store = DocumentStore(root)
        self.store.scan()
        self.document = self.store.get_document("angebot.odt")
        with app.test_request_context():
            self.password = activate("jens", "jens", label="LibreOffice Test", expires_days=30)
        token = base64.b64encode(f"jens:{self.password}".encode()).decode()
        self.auth = {"Authorization": f"Basic {token}"}
        self.url = f"/webdav/documents/jens/{self.document['document_id']}--angebot.odt"
        self.files = "/webdav/files/jens"
        self.lock_body = "<d:lockinfo xmlns:d='DAV:'><d:lockscope><d:exclusive/></d:lockscope><d:locktype><d:write/></d:locktype><d:owner>LibreOffice</d:owner></d:lockinfo>"

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_libreoffice_page_exposes_url_but_never_app_password(self):
        response = self.client.get(f"/documents/{self.document['document_id']}/libreoffice")
        body = response.get_data(as_text=True)
        credentials = json.loads((self.store.control / "webdav-credentials.json").read_text())

        self.assertEqual(200, response.status_code)
        self.assertIn(self.url, body)
        self.assertIn(self.files, body)
        self.assertIn("Datei → Öffnen", body)
        record = credentials["users"]["jens"]["credentials"][0]
        self.assertIn("LibreOffice Test", body)
        self.assertNotIn(record["hash"], body)
        self.assertNotIn(record["salt"], body)

    def test_device_credentials_are_independent_and_individually_revocable(self):
        with app.test_request_context():
            second_password = activate("jens", "jens", label="Nautilus", scope="write", expires_days=90)
        second_auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{second_password}".encode()).decode()}
        payload = json.loads((self.store.control / "webdav-credentials.json").read_text())
        records = payload["users"]["jens"]["credentials"]
        first_id = records[0]["credential_id"]

        with app.test_request_context():
            removed = revoke("jens", "jens", first_id)

        self.assertTrue(removed)
        self.assertEqual(401, self.client.get(self.url, headers=self.auth).status_code)
        self.assertEqual(200, self.client.get(self.url, headers=second_auth).status_code)
        updated = json.loads((self.store.control / "webdav-credentials.json").read_text())
        self.assertEqual(["Nautilus"], [item["label"] for item in updated["users"]["jens"]["credentials"]])
        events = [row for row in self.store.logbook() if row.get("action") == "webdav_credential_revoked"]
        self.assertTrue(events)
        snapshot = json.loads(next((self.store.history.root / "snapshots" / "webdav").glob("*.json")).read_text())
        self.assertEqual(first_id, snapshot["credential_id"])

    def test_read_only_credential_advertises_and_enforces_least_privilege(self):
        with app.test_request_context():
            password = activate("jens", "jens", label="Backup-Prüfung", scope="read", expires_days=30)
        auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}

        options = self.client.open(self.files, method="OPTIONS", headers=auth)
        listing = self.client.open(self.files, method="PROPFIND", headers={**auth, "Depth": "1"})
        fetched = self.client.get(f"{self.files}/angebot.odt", headers=auth)
        put = self.client.put(f"{self.files}/neu.txt", data=b"blocked", headers=auth)
        folder = self.client.open(f"{self.files}/blocked", method="MKCOL", headers=auth)
        lock = self.client.open(self.url, method="LOCK", headers=auth)

        self.assertEqual("OPTIONS, PROPFIND, REPORT, GET, HEAD", options.headers["Allow"])
        self.assertEqual(207, listing.status_code)
        self.assertEqual(200, fetched.status_code)
        self.assertEqual([403, 403, 403], [put.status_code, folder.status_code, lock.status_code])
        self.assertFalse((self.store.root / "neu.txt").exists())

    def test_expired_and_malformed_credentials_are_rejected(self):
        credential_path = self.store.control / "webdav-credentials.json"
        payload = json.loads(credential_path.read_text())
        payload["users"]["jens"]["credentials"][0]["expires_at"] = "2000-01-01T00:00:00+00:00"
        credential_path.write_text(json.dumps(payload))

        self.assertEqual(401, self.client.get(self.url, headers=self.auth).status_code)
        with app.test_request_context():
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="", scope="write")
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="Client", scope="admin")
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="Client", expires_days=366)

    def test_legacy_single_password_record_remains_usable(self):
        credential_path = self.store.control / "webdav-credentials.json"
        payload = json.loads(credential_path.read_text())
        record = payload["users"]["jens"]["credentials"][0]
        payload["users"]["jens"] = {key: record[key] for key in ("salt", "hash", "created_at", "created_by")}
        credential_path.write_text(json.dumps(payload))

        self.assertEqual(200, self.client.get(self.url, headers=self.auth).status_code)
        page = self.client.get(f"/documents/{self.document['document_id']}/libreoffice").get_data(as_text=True)
        self.assertIn("Bestehender Desktop-Zugang", page)
        self.assertIn("Ohne Ablauf (Bestand)", page)

    def test_active_credential_limit_prevents_unbounded_secret_growth(self):
        with app.test_request_context():
            for number in range(1, MAX_ACTIVE_CREDENTIALS):
                activate("jens", "jens", label=f"Gerät {number}", expires_days=30)
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="Zu viel", expires_days=30)

        payload = json.loads((self.store.control / "webdav-credentials.json").read_text())
        self.assertEqual(MAX_ACTIVE_CREDENTIALS, len(payload["users"]["jens"]["credentials"]))

    def test_folder_scoped_credential_is_validated_displayed_and_audited(self):
        (self.store.root / "Projekte").mkdir()
        response = self.client.post(
            f"/documents/{self.document['document_id']}/libreoffice",
            data={
                "action": "activate", "label": "Projekt-Laptop", "scope": "write",
                "expires_days": "30", "path_prefix": "Projekte/",
            },
        )
        with app.test_request_context():
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="Traversal", path_prefix="../privat")
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="Fehlt", path_prefix="NichtVorhanden")
            with self.assertRaises(ValueError):
                activate("jens", "jens", label="Steuerdaten", path_prefix=CONTROL_DIR)

        payload = json.loads((self.store.control / "webdav-credentials.json").read_text())
        record = next(item for item in payload["users"]["jens"]["credentials"] if item["label"] == "Projekt-Laptop")
        page = response.get_data(as_text=True)
        snapshots = list((self.store.history.root / "snapshots" / "webdav").glob("*.json"))

        self.assertEqual("Projekte", record["path_prefix"])
        self.assertIn("Projekt-Laptop", page)
        self.assertIn("/webdav/files/jens/Projekte/", page)
        self.assertNotIn(record["hash"], page)
        self.assertTrue(any(json.loads(path.read_text()).get("path_prefix") == "Projekte" for path in snapshots))

    def test_folder_scoped_credential_hides_siblings_and_stable_document_urls(self):
        (self.store.root / "Projekte").mkdir()
        (self.store.root / "Privat").mkdir()
        allowed = self.store.create_document_at("Projekte/Plan.odt", b"plan", "jens")
        private = self.store.create_document_at("Privat/Geheim.odt", b"secret", "jens")
        with app.test_request_context():
            password = activate("jens", "jens", label="Projekt", path_prefix="Projekte", expires_days=30)
        auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}
        allowed_stable = f"/webdav/documents/jens/{allowed['document_id']}--Plan.odt"
        private_stable = f"/webdav/documents/jens/{private['document_id']}--Geheim.odt"

        listing = self.client.open(f"{self.files}/Projekte", method="PROPFIND", headers={**auth, "Depth": "1"})
        stable_listing = self.client.open("/webdav/documents/jens", method="PROPFIND", headers={**auth, "Depth": "1"})
        statuses = [
            self.client.open(self.files, method="PROPFIND", headers=auth).status_code,
            self.client.get(f"{self.files}/Privat/Geheim.odt", headers=auth).status_code,
            self.client.get(private_stable, headers=auth).status_code,
            self.client.open(f"{self.files}/Privat", method="OPTIONS", headers=auth).status_code,
        ]

        self.assertEqual(207, listing.status_code)
        self.assertIn("Plan.odt", listing.get_data(as_text=True))
        self.assertEqual(200, self.client.get(allowed_stable, headers=auth).status_code)
        self.assertIn("Plan.odt", stable_listing.get_data(as_text=True))
        self.assertNotIn("Geheim.odt", stable_listing.get_data(as_text=True))
        self.assertEqual([404, 404, 404, 404], statuses)

    def test_folder_scope_covers_writes_destinations_locks_and_sync_tokens(self):
        (self.store.root / "Projekte").mkdir()
        (self.store.root / "Privat").mkdir()
        self.store.create_document_at("Projekte/Quelle.txt", b"source", "jens")
        with app.test_request_context():
            password = activate("jens", "jens", label="Projekt-Sync", path_prefix="Projekte", expires_days=30)
        auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}
        scoped = f"{self.files}/Projekte"
        sync_body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>infinite</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(scoped, method="REPORT", data=sync_body, headers=auth)
        token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")

        created = self.client.put(f"{scoped}/Neu.txt", data=b"new", headers={**auth, "If-None-Match": "*"})
        folder = self.client.open(f"{scoped}/Unterordner", method="MKCOL", headers=auth)
        copied = self.client.open(
            f"{scoped}/Quelle.txt", method="COPY",
            headers={**auth, "Destination": f"http://localhost/webdav/files/jens/Projekte/Kopie.txt"},
        )
        moved_outside = self.client.open(
            f"{scoped}/Quelle.txt", method="MOVE",
            headers={**auth, "Destination": "http://localhost/webdav/files/jens/Privat/Quelle.txt"},
        )
        locked_outside = self.client.open(f"{self.files}/Privat/gesperrt.txt", method="LOCK", data=self.lock_body, headers=auth)
        tagged_outside = self.client.put(
            f"{scoped}/Token.txt", data=b"blocked",
            headers={**auth, "If": f"<{self.files}/Privat/> (<{token}>)"},
        )
        boundary_delete = self.client.delete(scoped, headers=auth)

        self.assertEqual([201, 201, 201, 502, 404, 412, 403], [created.status_code, folder.status_code, copied.status_code, moved_outside.status_code, locked_outside.status_code, tagged_outside.status_code, boundary_delete.status_code])
        self.assertTrue((self.store.root / "Projekte").is_dir())
        self.assertTrue((self.store.root / "Projekte" / "Quelle.txt").is_file())
        self.assertTrue((self.store.root / "Projekte" / "Kopie.txt").is_file())
        self.assertFalse((self.store.root / "Privat" / "Quelle.txt").exists())
        self.assertFalse((self.store.root / "Projekte" / "Token.txt").exists())

    def test_folder_scoped_read_access_remains_read_only_inside_boundary(self):
        (self.store.root / "Archiv").mkdir()
        self.store.create_document_at("Archiv/Beleg.pdf", b"pdf", "jens")
        with app.test_request_context():
            password = activate("jens", "jens", label="Archivprüfung", scope="read", path_prefix="Archiv", expires_days=30)
        auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}
        root = f"{self.files}/Archiv"

        options = self.client.open(root, method="OPTIONS", headers=auth)
        listing = self.client.open(root, method="PROPFIND", headers={**auth, "Depth": "1"})
        fetched = self.client.get(f"{root}/Beleg.pdf", headers=auth)
        rejected = self.client.put(f"{root}/Neu.pdf", data=b"no", headers=auth)

        self.assertEqual("OPTIONS, PROPFIND, REPORT, GET, HEAD", options.headers["Allow"])
        self.assertEqual([207, 200, 403], [listing.status_code, fetched.status_code, rejected.status_code])
        self.assertFalse((self.store.root / "Archiv" / "Neu.pdf").exists())

    def test_options_propfind_get_and_head_are_libreoffice_compatible(self):
        options = self.client.open(self.url, method="OPTIONS", headers=self.auth)
        listing = self.client.open("/webdav/documents/jens", method="PROPFIND", headers={**self.auth, "Depth": "1"})
        fetched = self.client.get(self.url, headers=self.auth)
        head = self.client.head(self.url, headers=self.auth)

        self.assertEqual("1, 2", options.headers["DAV"])
        self.assertIn("LOCK", options.headers["Allow"])
        self.assertEqual(207, listing.status_code)
        self.assertIn("angebot.odt", listing.get_data(as_text=True))
        self.assertEqual(b"first office version", fetched.data)
        self.assertEqual(len(fetched.data), int(head.headers["Content-Length"]))
        self.assertEqual(fetched.headers["ETag"], head.headers["ETag"])

    def test_single_ranges_resume_downloads_on_tree_and_stable_document_urls(self):
        first = self.client.get(f"{self.files}/angebot.odt", headers={**self.auth, "Range": "bytes=0-4"})
        suffix = self.client.get(self.url, headers={**self.auth, "Range": "bytes=-7"})
        remainder = self.client.get(self.url, headers={**self.auth, "Range": "bytes=6-"})
        head = self.client.head(self.url, headers={**self.auth, "Range": "bytes=0-4"})

        self.assertEqual([206, 206, 206, 200], [first.status_code, suffix.status_code, remainder.status_code, head.status_code])
        self.assertEqual(b"first", first.data)
        self.assertEqual("bytes 0-4/20", first.headers["Content-Range"])
        self.assertEqual(b"version", suffix.data)
        self.assertEqual("bytes 13-19/20", suffix.headers["Content-Range"])
        self.assertEqual(b"office version", remainder.data)
        self.assertEqual("bytes", first.headers["Accept-Ranges"])
        self.assertEqual("20", head.headers["Content-Length"])
        self.assertEqual(b"", head.data)

    def test_multiple_ranges_are_bounded_and_return_multipart_byteranges(self):
        response = self.client.get(
            self.url,
            headers={**self.auth, "Range": "bytes=0-4,13-19"},
        )

        self.assertEqual(206, response.status_code)
        self.assertTrue(response.headers["Content-Type"].startswith("multipart/byteranges; boundary="))
        self.assertEqual(len(response.data), int(response.headers["Content-Length"]))
        self.assertIn(b"Content-Range: bytes 0-4/20", response.data)
        self.assertIn(b"Content-Range: bytes 13-19/20", response.data)
        self.assertIn(b"\r\n\r\nfirst\r\n", response.data)
        self.assertIn(b"\r\n\r\nversion\r\n", response.data)

    def test_unsatisfiable_invalid_overlapping_and_excessive_ranges_return_416(self):
        headers = [
            "bytes=99-100",
            "items=0-1",
            "bytes=broken",
            "bytes=0-5,3-8",
            "bytes=" + ",".join(f"{number}-{number}" for number in range(9)),
        ]
        responses = [self.client.get(self.url, headers={**self.auth, "Range": value}) for value in headers]

        self.assertEqual([416] * len(headers), [response.status_code for response in responses])
        self.assertTrue(all(response.headers["Content-Range"] == "bytes */20" for response in responses))
        self.assertTrue(all(response.headers["ETag"] for response in responses))

    def test_etag_and_date_preconditions_follow_rfc_precedence(self):
        current = self.client.get(self.url, headers=self.auth)
        etag = current.headers["ETag"]
        last_modified = current.headers["Last-Modified"]
        not_modified = self.client.get(self.url, headers={**self.auth, "If-None-Match": etag})
        weak_not_modified = self.client.head(self.url, headers={**self.auth, "If-None-Match": f"W/{etag}"})
        stale_match = self.client.get(self.url, headers={**self.auth, "If-Match": '"stale"'})
        weak_match = self.client.get(self.url, headers={**self.auth, "If-Match": f"W/{etag}"})
        date_not_modified = self.client.get(self.url, headers={**self.auth, "If-Modified-Since": last_modified})
        changed_since = self.client.get(self.url, headers={**self.auth, "If-Unmodified-Since": "Thu, 01 Jan 1970 00:00:00 GMT"})
        invalid_date = self.client.get(self.url, headers={**self.auth, "If-Modified-Since": "not-a-date"})
        etag_takes_precedence = self.client.get(
            self.url,
            headers={**self.auth, "If-None-Match": '"other"', "If-Modified-Since": last_modified},
        )

        self.assertEqual([304, 304, 412, 412, 304, 412, 200, 200], [
            not_modified.status_code, weak_not_modified.status_code, stale_match.status_code,
            weak_match.status_code, date_not_modified.status_code, changed_since.status_code,
            invalid_date.status_code, etag_takes_precedence.status_code,
        ])
        self.assertEqual(b"", not_modified.data)
        self.assertEqual(etag, not_modified.headers["ETag"])

    def test_unsafe_etag_preconditions_support_lists_weak_comparison_and_precedence(self):
        target = f"{self.files}/angebot.odt"
        current = self.client.get(target, headers=self.auth)
        etag = current.headers["ETag"]
        saved = self.client.put(
            target,
            data=b"list-selected version",
            headers={
                **self.auth,
                "If-Match": f'"other,opaque", {etag}',
                "If-Unmodified-Since": "Thu, 01 Jan 1970 00:00:00 GMT",
            },
        )
        fresh = self.client.get(target, headers=self.auth).headers["ETag"]
        weak_match = self.client.put(
            target, data=b"weak must fail", headers={**self.auth, "If-Match": f"W/{fresh}"},
        )
        none_match = self.client.put(
            target,
            data=b"none-match must fail",
            headers={**self.auth, "If-Match": fresh, "If-None-Match": f'"other", W/{fresh}'},
        )

        self.assertEqual([204, 412, 412], [saved.status_code, weak_match.status_code, none_match.status_code])
        self.assertEqual(b"list-selected version", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(fresh, weak_match.headers["ETag"])
        self.assertTrue(weak_match.headers["Last-Modified"])

    def test_stale_dates_block_all_file_mutations_before_any_side_effect(self):
        target = f"{self.files}/angebot.odt"
        property_body = '<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:test"><d:set><d:prop><m:state>unsafe</m:state></d:prop></d:set></d:propertyupdate>'
        stale = {**self.auth, "If-Unmodified-Since": "Thu, 01 Jan 1970 00:00:00 GMT"}
        deleted = self.client.delete(target, headers=stale)
        property_change = self.client.open(target, method="PROPPATCH", data=property_body, headers=stale)
        copied = self.client.open(
            target, method="COPY",
            headers={**stale, "Destination": f"http://localhost{self.files}/stale-copy.odt"},
        )
        moved = self.client.open(
            target, method="MOVE",
            headers={**stale, "Destination": f"http://localhost{self.files}/stale-move.odt"},
        )

        self.assertEqual([412, 412, 412, 412], [deleted.status_code, property_change.status_code, copied.status_code, moved.status_code])
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        self.assertFalse((self.store.root / "stale-copy.odt").exists())
        self.assertFalse((self.store.root / "stale-move.odt").exists())
        self.assertFalse((self.store.control / "webdav-properties.json").exists())

    def test_invalid_etag_conditions_fail_closed_and_are_safely_audited(self):
        target = f"{self.files}/angebot.odt"
        secret = "validator-that-must-not-enter-history"
        malformed = self.client.delete(target, headers={**self.auth, "If-None-Match": secret})
        excessive = self.client.delete(
            target,
            headers={**self.auth, "If-Match": ", ".join(f'"tag-{number}"' for number in range(65))},
        )
        oversized = self.client.delete(
            target, headers={**self.auth, "If-None-Match": f'"{("x" * 8192)}"'},
        )

        self.assertEqual([400, 413, 413], [malformed.status_code, excessive.status_code, oversized.status_code])
        self.assertTrue((self.store.root / "angebot.odt").is_file())
        events = [row for row in self.store.logbook() if row.get("action") == "webdav_http_precondition_rejected"]
        self.assertEqual(3, len(events))
        snapshots = list((self.store.history.root / "snapshots" / "webdav-preconditions").glob("*.json"))
        self.assertTrue(snapshots)
        self.assertNotIn(secret, "\n".join(path.read_text() for path in snapshots))

    def test_collection_and_create_preconditions_use_resource_existence(self):
        collection = f"{self.files}/Bedingt"
        missing_match = self.client.open(
            collection, method="MKCOL", headers={**self.auth, "If-Match": "*"},
        )
        created = self.client.open(
            collection, method="MKCOL", headers={**self.auth, "If-None-Match": "*"},
        )
        blocked = self.client.open(
            collection,
            method="PROPPATCH",
            data='<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:test"><d:set><d:prop><m:x>1</m:x></d:prop></d:set></d:propertyupdate>',
            headers={**self.auth, "If-None-Match": "*"},
        )

        self.assertEqual([412, 201, 412], [missing_match.status_code, created.status_code, blocked.status_code])
        self.assertTrue((self.store.root / "Bedingt").is_dir())

    def test_invalid_unmodified_since_is_ignored_for_copy(self):
        copied = self.client.open(
            f"{self.files}/angebot.odt",
            method="COPY",
            headers={
                **self.auth,
                "If-Unmodified-Since": "not-an-http-date",
                "Destination": f"http://localhost{self.files}/date-fallback.odt",
            },
        )

        self.assertEqual(201, copied.status_code)
        self.assertEqual(b"first office version", (self.store.root / "date-fallback.odt").read_bytes())

    def test_if_range_returns_partial_only_for_the_current_strong_validator(self):
        current = self.client.get(self.url, headers=self.auth)
        etag = current.headers["ETag"]
        last_modified = current.headers["Last-Modified"]
        matching_etag = self.client.get(self.url, headers={**self.auth, "Range": "bytes=0-4", "If-Range": etag})
        matching_date = self.client.get(self.url, headers={**self.auth, "Range": "bytes=0-4", "If-Range": last_modified})
        stale = self.client.get(self.url, headers={**self.auth, "Range": "bytes=0-4", "If-Range": '"stale"'})
        weak = self.client.get(self.url, headers={**self.auth, "Range": "bytes=0-4", "If-Range": f"W/{etag}"})

        self.assertEqual([206, 206, 200, 200], [matching_etag.status_code, matching_date.status_code, stale.status_code, weak.status_code])
        self.assertEqual(b"first", matching_etag.data)
        self.assertEqual(b"first", matching_date.data)
        self.assertEqual(b"first office version", stale.data)
        self.assertEqual(b"first office version", weak.data)

    def test_rfc9530_digests_cover_full_partial_and_multipart_downloads(self):
        full = self.client.get(self.url, headers=self.auth)
        head = self.client.head(f"{self.files}/angebot.odt", headers=self.auth)
        partial = self.client.get(self.url, headers={**self.auth, "Range": "bytes=0-4"})
        multiple = self.client.get(self.url, headers={**self.auth, "Range": "bytes=0-4,13-19"})
        options = self.client.open(self.files, method="OPTIONS", headers=self.auth)

        representation = "sha-256=:" + base64.b64encode(hashlib.sha256(b"first office version").digest()).decode() + ":"
        first = "sha-256=:" + base64.b64encode(hashlib.sha256(b"first").digest()).decode() + ":"
        multipart = "sha-256=:" + base64.b64encode(hashlib.sha256(multiple.data).digest()).decode() + ":"
        self.assertEqual(representation, full.headers["Repr-Digest"])
        self.assertEqual(representation, full.headers["Content-Digest"])
        self.assertEqual(representation, head.headers["Repr-Digest"])
        self.assertNotIn("Content-Digest", head.headers)
        self.assertEqual(representation, partial.headers["Repr-Digest"])
        self.assertEqual(first, partial.headers["Content-Digest"])
        self.assertEqual(representation, multiple.headers["Repr-Digest"])
        self.assertEqual(multipart, multiple.headers["Content-Digest"])
        self.assertEqual("sha-512=9, sha-256=10", options.headers["Want-Content-Digest"])

    def test_valid_content_digest_is_verified_before_put_and_describes_stored_result(self):
        payload = b"integrity checked office version"
        current = self.client.get(f"{self.files}/angebot.odt", headers=self.auth)
        sha256 = base64.b64encode(hashlib.sha256(payload).digest()).decode()
        sha512 = base64.b64encode(hashlib.sha512(payload).digest()).decode()

        response = self.client.put(
            f"{self.files}/angebot.odt",
            data=payload,
            headers={
                **self.auth,
                "If-Match": current.headers["ETag"],
                "Content-Digest": f"sha-512=:{sha512}:, sha-256=:{sha256}:",
            },
        )

        self.assertEqual(204, response.status_code)
        self.assertEqual(payload, (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(f"sha-256=:{sha256}:", response.headers["Repr-Digest"])
        self.assertEqual(f"{self.files}/angebot.odt", response.headers["Content-Location"])
        self.assertEqual("sha-512=9, sha-256=10", response.headers["Want-Content-Digest"])
        audits = [item for item in self.store.logbook() if item.get("action") == "webdav_content_digest_verified"]
        snapshot = json.loads(next((self.store.history.root / "snapshots" / "webdav-integrity").glob("*.json")).read_text())
        self.assertTrue(audits)
        self.assertEqual(["sha-256", "sha-512"], snapshot["algorithms"])
        self.assertNotIn(sha256, json.dumps(snapshot))

    def test_sha512_digest_can_protect_new_file_and_stable_document_put(self):
        created_payload = b"new synchronized file"
        created_sha512 = base64.b64encode(hashlib.sha512(created_payload).digest()).decode()
        created = self.client.put(
            f"{self.files}/new.txt",
            data=created_payload,
            headers={**self.auth, "If-None-Match": "*", "Content-Digest": f"sha-512=:{created_sha512}:"},
        )
        stable_payload = b"stable url update"
        stable_sha256 = base64.b64encode(hashlib.sha256(stable_payload).digest()).decode()
        stable = self.client.put(
            self.url,
            data=stable_payload,
            headers={**self.auth, "Content-Digest": f"sha-256=:{stable_sha256}:"},
        )

        self.assertEqual([201, 204], [created.status_code, stable.status_code])
        self.assertEqual(created_payload, (self.store.root / "new.txt").read_bytes())
        self.assertEqual(stable_payload, (self.store.root / "angebot.odt").read_bytes())
        self.assertTrue(created.headers["Repr-Digest"].startswith("sha-256=:"))
        self.assertEqual(f"sha-256=:{stable_sha256}:", stable.headers["Repr-Digest"])

    def test_bad_malformed_and_unsupported_content_digests_never_mutate_files(self):
        original = (self.store.root / "angebot.odt").read_bytes()
        current = self.client.get(f"{self.files}/angebot.odt", headers=self.auth)
        payload = b"must never be stored"
        wrong = base64.b64encode(hashlib.sha256(b"different").digest()).decode()
        valid = base64.b64encode(hashlib.sha256(payload).digest()).decode()
        requests = [
            (f"sha-256=:{wrong}:", 422),
            ("sha-256=:not base64!:", 400),
            ("md5=:CY9rzUYh03PK3k6DJie09g==:", 400),
            (f"sha-256=:{valid}:, sha-256=:{valid}:", 400),
        ]
        responses = [
            self.client.put(
                f"{self.files}/angebot.odt",
                data=payload,
                headers={**self.auth, "If-Match": current.headers["ETag"], "Content-Digest": field},
            )
            for field, _status in requests
        ]
        new_file = self.client.put(
            f"{self.files}/rejected.txt",
            data=payload,
            headers={**self.auth, "If-None-Match": "*", "Content-Digest": f"sha-256=:{wrong}:"},
        )

        self.assertEqual([status for _field, status in requests], [response.status_code for response in responses])
        self.assertEqual(422, new_file.status_code)
        self.assertEqual(original, (self.store.root / "angebot.odt").read_bytes())
        self.assertFalse((self.store.root / "rejected.txt").exists())
        self.assertTrue(all(response.headers["Want-Content-Digest"] for response in [*responses, new_file]))
        actions = [item.get("action") for item in self.store.logbook()]
        self.assertIn("webdav_content_digest_mismatch", actions)
        self.assertIn("webdav_content_digest_rejected", actions)

    def test_download_stream_uses_one_open_file_snapshot_during_atomic_replace(self):
        response = self.client.get(self.url, headers=self.auth, buffered=False)
        replacement = self.store.root / ".replacement"
        replacement.write_bytes(b"new file after response")
        replacement.replace(self.store.root / "angebot.odt")

        self.assertEqual(b"first office version", b"".join(response.response))
        self.assertEqual("20", response.headers["Content-Length"])
        self.assertEqual(b"new file after response", (self.store.root / "angebot.odt").read_bytes())

    def test_dead_properties_roundtrip_propname_and_remove(self):
        target = f"{self.files}/angebot.odt"
        update = '''<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test">
          <d:set><d:prop><m:tags><m:tag>rechnung</m:tag><m:tag>kunde-a</m:tag></m:tags></d:prop></d:set>
          <d:set><d:prop><m:rating>5</m:rating></d:prop></d:set>
        </d:propertyupdate>'''
        saved = self.client.open(target, method="PROPPATCH", data=update, headers=self.auth)
        requested = self.client.open(
            target, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:tags/><m:rating/><m:missing/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )
        names = self.client.open(
            target, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:"><d:propname/></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )
        removed = self.client.open(
            target, method="PROPPATCH",
            data='<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:remove><d:prop><m:rating/></d:prop></d:remove></d:propertyupdate>',
            headers=self.auth,
        )
        after = self.client.open(
            target, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:rating/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )

        self.assertEqual([207, 207, 207, 207, 207], [saved.status_code, requested.status_code, names.status_code, removed.status_code, after.status_code])
        requested_xml = ElementTree.fromstring(requested.data)
        tags = requested_xml.find(".//{urn:simpleoffice:test}tags")
        self.assertEqual(["rechnung", "kunde-a"], [item.text for item in tags])
        self.assertEqual("5", requested_xml.findtext(".//{urn:simpleoffice:test}rating"))
        self.assertIn("404 Not Found", requested.get_data(as_text=True))
        self.assertIsNotNone(ElementTree.fromstring(names.data).find(".//{urn:simpleoffice:test}tags"))
        self.assertIn("404 Not Found", after.get_data(as_text=True))
        self.assertTrue(list((self.store.history.root / "snapshots" / "webdav-properties").glob("*.json")))

    def test_proppatch_is_atomic_and_live_properties_are_protected(self):
        target = f"{self.files}/angebot.odt"
        attempted = self.client.open(
            target, method="PROPPATCH",
            data='''<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test">
              <d:set><d:prop><m:author>Jens</m:author></d:prop></d:set>
              <d:set><d:prop><d:getetag>forged</d:getetag></d:prop></d:set>
            </d:propertyupdate>''',
            headers=self.auth,
        )
        checked = self.client.open(
            target, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:author/><d:getetag/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )

        text = attempted.get_data(as_text=True)
        self.assertEqual(207, attempted.status_code)
        self.assertIn("403 Forbidden", text)
        self.assertIn("424 Failed Dependency", text)
        self.assertIn("cannot-modify-protected-property", text)
        self.assertIn("404 Not Found", checked.get_data(as_text=True))
        self.assertEqual(f'"{self.document["sha256"]}"', ElementTree.fromstring(checked.data).findtext(".//{DAV:}getetag"))
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())

    def test_writable_live_displayname_language_and_inherited_xml_lang(self):
        target = f"{self.files}/angebot.odt"
        saved = self.client.open(
            target, method="PROPPATCH",
            data='''<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test">
              <d:set><d:prop xml:lang="de"><d:displayname>Angebot Kunde A</d:displayname><d:getcontentlanguage>de-DE</d:getcontentlanguage><m:note>Geprüft</m:note></d:prop></d:set>
            </d:propertyupdate>''',
            headers=self.auth,
        )
        properties = self.client.open(
            target, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><d:displayname/><d:getcontentlanguage/><m:note/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )
        fetched = self.client.get(target, headers=self.auth)
        invalid = self.client.open(
            target, method="PROPPATCH",
            data='<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:set><d:prop><m:other>must roll back</m:other><d:getcontentlanguage>not a language!</d:getcontentlanguage></d:prop></d:set></d:propertyupdate>',
            headers=self.auth,
        )
        check = self.client.open(
            target, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:other/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )

        root = ElementTree.fromstring(properties.data)
        note = root.find(".//{urn:simpleoffice:test}note")
        self.assertEqual([207, 207, 207], [saved.status_code, properties.status_code, invalid.status_code])
        self.assertEqual("Angebot Kunde A", root.findtext(".//{DAV:}displayname"))
        self.assertEqual("de", note.get("{http://www.w3.org/XML/1998/namespace}lang"))
        self.assertEqual("de-DE", fetched.headers["Content-Language"])
        self.assertIn("409 Conflict", invalid.get_data(as_text=True))
        self.assertIn("424 Failed Dependency", invalid.get_data(as_text=True))
        self.assertIn("404 Not Found", check.get_data(as_text=True))

    def test_proppatch_honors_read_scope_locks_retention_and_user_boundary(self):
        target = f"{self.files}/angebot.odt"
        body = '<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:set><d:prop><m:status>review</m:status></d:prop></d:set></d:propertyupdate>'
        with app.test_request_context():
            read_password = activate("jens", "jens", label="Property Reader", scope="read", expires_days=30)
        read_auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{read_password}".encode()).decode()}
        read_only = self.client.open(target, method="PROPPATCH", data=body, headers=read_auth)
        locked = self.client.open(target, method="LOCK", data=self.lock_body, headers=self.auth)
        missing_token = self.client.open(target, method="PROPPATCH", data=body, headers=self.auth)
        token = locked.headers["Lock-Token"]
        accepted = self.client.open(target, method="PROPPATCH", data=body, headers={**self.auth, "If": f"(<{token.strip('<>')}>)"})
        foreign = self.client.open("/webdav/files/other/angebot.odt", method="PROPPATCH", data=body, headers=self.auth)
        self.client.open(target, method="UNLOCK", headers={**self.auth, "Lock-Token": token})
        metadata = self.store.get_document(self.document["document_id"])
        metadata["cleanup_state"] = "staged"
        self.store._save_document(metadata)
        retention = self.client.open(target, method="PROPPATCH", data=body.replace("review", "changed"), headers=self.auth)

        self.assertEqual([403, 423, 207, 404, 423], [read_only.status_code, missing_token.status_code, accepted.status_code, foreign.status_code, retention.status_code])

    def test_proppatch_rejects_entities_malformed_xml_and_size_abuse(self):
        target = f"{self.files}/angebot.odt"
        entity = self.client.open(
            target, method="PROPPATCH",
            data='<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:test"><d:set><d:prop><m:x>&secret;</m:x></d:prop></d:set></d:propertyupdate>',
            headers=self.auth,
        )
        malformed = self.client.open(target, method="PROPPATCH", data="<broken", headers=self.auth)
        oversized = self.client.open(target, method="PROPPATCH", data=b"x" * (64 * 1024 + 1), headers=self.auth)

        self.assertEqual([400, 400, 413], [entity.status_code, malformed.status_code, oversized.status_code])
        self.assertIn("no-external-entities", entity.get_data(as_text=True))
        self.assertFalse((self.store.control / "webdav-properties.json").exists())

    def test_collection_and_legacy_libreoffice_url_support_properties(self):
        body = '<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:set><d:prop><m:label>Desktop</m:label></d:prop></d:set></d:propertyupdate>'
        query = '<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:label/></d:prop></d:propfind>'
        self.client.open(f"{self.files}/Kunden", method="MKCOL", headers=self.auth)
        collection_saved = self.client.open(f"{self.files}/Kunden", method="PROPPATCH", data=body, headers=self.auth)
        collection_read = self.client.open(f"{self.files}/Kunden", method="PROPFIND", data=query, headers={**self.auth, "Depth": "0"})
        legacy_saved = self.client.open(self.url, method="PROPPATCH", data=body.replace("Desktop", "LibreOffice"), headers=self.auth)
        legacy_read = self.client.open(self.url, method="PROPFIND", data=query, headers={**self.auth, "Depth": "0"})

        self.assertEqual([207, 207, 207, 207], [collection_saved.status_code, collection_read.status_code, legacy_saved.status_code, legacy_read.status_code])
        self.assertEqual("Desktop", ElementTree.fromstring(collection_read.data).findtext(".//{urn:simpleoffice:test}label"))
        self.assertEqual("LibreOffice", ElementTree.fromstring(legacy_read.data).findtext(".//{urn:simpleoffice:test}label"))

    def test_copy_move_and_sync_preserve_and_report_dead_properties(self):
        target = f"{self.files}/angebot.odt"
        body = '<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:set><d:prop><m:workflow>approved</m:workflow></d:prop></d:set></d:propertyupdate>'
        sync_body = '<d:sync-collection xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/><m:workflow/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=sync_body, headers=self.auth)
        token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")
        changed = self.client.open(target, method="PROPPATCH", data=body, headers=self.auth)
        report = self.client.open(
            self.files, method="REPORT",
            data=sync_body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>"),
            headers=self.auth,
        )
        copied = self.client.open(
            target, method="COPY",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/Kopie.odt"},
        )
        moved = self.client.open(
            target, method="MOVE",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/Verschoben.odt"},
        )
        propfind = '<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:workflow/></d:prop></d:propfind>'
        copy_properties = self.client.open(f"{self.files}/Kopie.odt", method="PROPFIND", data=propfind, headers={**self.auth, "Depth": "0"})
        move_properties = self.client.open(f"{self.files}/Verschoben.odt", method="PROPFIND", data=propfind, headers={**self.auth, "Depth": "0"})

        self.assertEqual([207, 207, 201, 201, 207, 207], [changed.status_code, report.status_code, copied.status_code, moved.status_code, copy_properties.status_code, move_properties.status_code])
        self.assertIn("angebot.odt", report.get_data(as_text=True))
        self.assertIn("approved", report.get_data(as_text=True))
        self.assertEqual("approved", ElementTree.fromstring(copy_properties.data).findtext(".//{urn:simpleoffice:test}workflow"))
        self.assertEqual("approved", ElementTree.fromstring(move_properties.data).findtext(".//{urn:simpleoffice:test}workflow"))

    def test_lock_put_unlock_persists_and_audits_new_revision(self):
        lock_body = "<d:lockinfo xmlns:d='DAV:'><d:lockscope><d:exclusive/></d:lockscope><d:locktype><d:write/></d:locktype><d:owner>LibreOffice</d:owner></d:lockinfo>"
        locked = self.client.open(self.url, method="LOCK", data=lock_body, headers={**self.auth, "Timeout": "Second-600"})
        token = locked.headers["Lock-Token"]
        updated = self.client.put(self.url, data=b"saved by libreoffice", headers={**self.auth, "If": f"(<{token.strip('<>')}>)"})
        unlocked = self.client.open(self.url, method="UNLOCK", headers={**self.auth, "Lock-Token": token})

        self.assertEqual(200, locked.status_code)
        self.assertEqual(204, updated.status_code)
        self.assertEqual(204, unlocked.status_code)
        self.assertEqual(b"saved by libreoffice", (self.store.root / "angebot.odt").read_bytes())
        metadata = self.store.get_document(self.document["document_id"])
        self.assertEqual(1, metadata["content_revision"])
        self.assertEqual("webdav:jens", metadata["content_history"][-1]["actor"])
        archive = self.store.control / metadata["content_history"][-1]["archive"]
        self.assertEqual(b"first office version", archive.read_bytes())
        self.assertTrue(any(event.get("type") == "document_content_replaced" for event in self.store.logbook(self.document["document_id"])))

    def test_stale_etag_and_foreign_or_missing_lock_token_cannot_overwrite(self):
        stale = self.client.get(self.url, headers=self.auth).headers["ETag"]
        self.client.put(self.url, data=b"newer", headers={**self.auth, "If-Match": stale})
        rejected = self.client.put(self.url, data=b"stale", headers={**self.auth, "If-Match": stale})
        lock = self.client.open(self.url, method="LOCK", data=self.lock_body, headers=self.auth)
        missing_token = self.client.put(self.url, data=b"without token", headers=self.auth)

        self.assertEqual(412, rejected.status_code)
        self.assertEqual(b"newer", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(200, lock.status_code)
        self.assertEqual(423, missing_token.status_code)

    def test_wrong_password_other_user_path_and_unbounded_depth_are_rejected(self):
        bad = {"Authorization": "Basic " + base64.b64encode(b"jens:wrong").decode()}

        self.assertEqual(401, self.client.get(self.url, headers=bad).status_code)
        self.assertEqual(404, self.client.get(self.url.replace("/jens/", "/other/"), headers=self.auth).status_code)
        self.assertEqual(403, self.client.open("/webdav/documents/jens", method="PROPFIND", headers={**self.auth, "Depth": "infinity"}).status_code)

    def test_retention_edit_lock_also_blocks_webdav_put(self):
        metadata = self.store.get_document(self.document["document_id"])
        metadata["cleanup_state"] = "staged"
        self.store._save_document(metadata)

        response = self.client.put(self.url, data=b"must not be written", headers=self.auth)

        self.assertEqual(423, response.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())

    def test_file_manager_creates_collection_and_uploads_new_document(self):
        root = self.client.open(self.files, method="PROPFIND", headers={**self.auth, "Depth": "1"})
        created_folder = self.client.open(f"{self.files}/Projekte", method="MKCOL", headers=self.auth)
        created_file = self.client.put(
            f"{self.files}/Projekte/Planung.odt",
            data=b"new plan",
            headers={**self.auth, "If-None-Match": "*"},
        )
        listing = self.client.open(f"{self.files}/Projekte", method="PROPFIND", headers={**self.auth, "Depth": "1"})

        self.assertEqual(207, root.status_code)
        self.assertNotIn(".simpleoffice-meta", root.get_data(as_text=True))
        self.assertEqual(201, created_folder.status_code)
        self.assertEqual(201, created_file.status_code)
        self.assertIn("Planung.odt", listing.get_data(as_text=True))
        document = self.store.get_document("Projekte/Planung.odt")
        self.assertEqual(1, document["content_revision"])
        self.assertEqual("webdav:jens", document["content_history"][0]["actor"])

    def test_existing_tree_put_requires_precondition_and_versions_content(self):
        tree_url = f"{self.files}/angebot.odt"
        current = self.client.get(tree_url, headers=self.auth)
        create_only = self.client.put(tree_url, data=b"duplicate create", headers={**self.auth, "If-None-Match": "*"})
        rejected = self.client.put(tree_url, data=b"blind overwrite", headers=self.auth)
        stale = self.client.put(tree_url, data=b"stale", headers={**self.auth, "If-Match": '"wrong"'})
        saved = self.client.put(tree_url, data=b"checked update", headers={**self.auth, "If-Match": current.headers["ETag"]})

        self.assertEqual(412, create_only.status_code)
        self.assertEqual(428, rejected.status_code)
        self.assertEqual(412, stale.status_code)
        self.assertEqual(204, saved.status_code)
        self.assertEqual(b"checked update", (self.store.root / "angebot.odt").read_bytes())
        updated = self.store.get_document(self.document["document_id"])
        self.assertEqual(1, updated["content_revision"])
        self.assertTrue((self.store.control / updated["content_history"][-1]["archive"]).is_file())

    def test_optional_clamav_scans_tree_and_stable_put_before_publish(self):
        app.config["WEBDAV_UPLOAD_SCAN"] = True
        observed = []

        def clean_scan(_scanner, path):
            observed.append((Path(path).read_bytes(), Path(path).stat().st_mode & 0o777))
            return ScanResult("clean", "test signature database", "fake-clamav")

        with mock.patch.object(ClamAV, "scan", autospec=True, side_effect=clean_scan) as scan:
            created = self.client.put(
                f"{self.files}/Geprueft.odt",
                data=b"new checked file",
                headers={**self.auth, "If-None-Match": "*"},
            )
            current = self.client.get(self.url, headers=self.auth)
            updated = self.client.put(
                self.url,
                data=b"checked office revision",
                headers={**self.auth, "If-Match": current.headers["ETag"]},
            )

        self.assertEqual([201, 204], [created.status_code, updated.status_code])
        self.assertEqual(2, scan.call_count)
        self.assertEqual(
            [(b"new checked file", 0o600), (b"checked office revision", 0o600)],
            observed,
        )
        self.assertEqual(b"new checked file", (self.store.root / "Geprueft.odt").read_bytes())
        self.assertEqual(b"checked office revision", (self.store.root / "angebot.odt").read_bytes())
        quarantine = self.store.control / "webdav-upload-quarantine"
        self.assertEqual([], list(quarantine.iterdir()))
        registry = json.loads((self.store.control / "malware-scan.json").read_text())
        self.assertEqual(2, len([row for row in registry["scans"] if row.get("source_type") == "webdav-put"]))
        actions = [row.get("action") for row in self.store.logbook()]
        self.assertEqual(2, actions.count("webdav_upload_malware_scanned"))

    def test_infected_webdav_put_is_quarantined_without_publishing(self):
        app.config["WEBDAV_UPLOAD_SCAN"] = True
        infected = ScanResult("infected", "Eicar-Test-Signature FOUND", "fake-clamav")
        original_etag = self.client.get(f"{self.files}/angebot.odt", headers=self.auth).headers["ETag"]

        with mock.patch.object(ClamAV, "scan", autospec=True, return_value=infected):
            created = self.client.put(
                f"{self.files}/Schadcode.bin",
                data=b"not safe",
                headers={**self.auth, "If-None-Match": "*"},
            )
            overwritten = self.client.put(
                f"{self.files}/angebot.odt",
                data=b"infected replacement",
                headers={**self.auth, "If-Match": original_etag},
            )

        self.assertEqual([422, 422], [created.status_code, overwritten.status_code])
        self.assertEqual("no-store", created.headers["Cache-Control"])
        self.assertFalse((self.store.root / "Schadcode.bin").exists())
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        retained = list((self.store.control / "webdav-upload-quarantine").glob("*.infected"))
        self.assertEqual(2, len(retained))
        self.assertEqual([], list((self.store.control / "webdav-upload-quarantine").glob("*.pending")))
        actions = [row.get("action") for row in self.store.logbook()]
        self.assertEqual(2, actions.count("webdav_upload_malware_blocked"))

    def test_scanner_failure_is_retryable_and_preserves_current_revision(self):
        app.config["WEBDAV_UPLOAD_SCAN"] = True
        current = self.client.get(self.url, headers=self.auth)

        with mock.patch.object(ClamAV, "scan", autospec=True, side_effect=RuntimeError("daemon down")):
            response = self.client.put(
                self.url,
                data=b"must stay quarantined",
                headers={**self.auth, "If-Match": current.headers["ETag"]},
            )

        self.assertEqual(503, response.status_code)
        self.assertEqual("60", response.headers["Retry-After"])
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(1, len(list((self.store.control / "webdav-upload-quarantine").glob("*.error"))))
        self.assertTrue(any(row.get("action") == "webdav_upload_malware_scan_failed" for row in self.store.logbook()))

    def test_quarantine_capacity_returns_507_before_scanner_or_mutation(self):
        app.config.update(WEBDAV_UPLOAD_SCAN=True, WEBDAV_QUARANTINE_BYTES=8)
        quarantine = self.store.control / "webdav-upload-quarantine"
        quarantine.mkdir(mode=0o700)
        (quarantine / "previous.infected").write_bytes(b"123456")

        with mock.patch.object(ClamAV, "scan", autospec=True) as scan:
            response = self.client.put(
                f"{self.files}/ZuGross.bin",
                data=b"7890",
                headers={**self.auth, "If-None-Match": "*"},
            )

        self.assertEqual(507, response.status_code)
        self.assertIn("sufficient-disk-space", response.get_data(as_text=True))
        self.assertEqual("no-store", response.headers["Cache-Control"])
        scan.assert_not_called()
        self.assertFalse((self.store.root / "ZuGross.bin").exists())

    def test_unsafe_quarantine_entry_fails_closed_before_scanner(self):
        app.config["WEBDAV_UPLOAD_SCAN"] = True
        quarantine = self.store.control / "webdav-upload-quarantine"
        quarantine.mkdir(mode=0o700)
        (quarantine / "unexpected-directory").mkdir()

        with mock.patch.object(ClamAV, "scan", autospec=True) as scan:
            response = self.client.put(
                f"{self.files}/NichtFreigeben.bin",
                data=b"untrusted",
                headers={**self.auth, "If-None-Match": "*"},
            )

        self.assertEqual(503, response.status_code)
        scan.assert_not_called()
        self.assertFalse((self.store.root / "NichtFreigeben.bin").exists())

    def test_rejected_puts_never_reach_optional_malware_scanner(self):
        app.config["WEBDAV_UPLOAD_SCAN"] = True
        with app.test_request_context():
            read_password = activate("jens", "jens", label="Nur lesen", scope="read", expires_days=30)
        read_auth = {
            "Authorization": "Basic " + base64.b64encode(f"jens:{read_password}".encode()).decode()
        }

        with mock.patch.object(ClamAV, "scan", autospec=True) as scan:
            forbidden = self.client.put(
                f"{self.files}/Verboten.bin",
                data=b"forbidden",
                headers={**read_auth, "If-None-Match": "*"},
            )
            stale = self.client.put(
                f"{self.files}/angebot.odt",
                data=b"stale",
                headers={**self.auth, "If-Match": '"wrong"'},
            )
            invalid_digest = self.client.put(
                f"{self.files}/Digest.bin",
                data=b"digest mismatch",
                headers={
                    **self.auth,
                    "If-None-Match": "*",
                    "Content-Digest": "sha-256=:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=:",
                },
            )

        self.assertEqual([403, 412, 422], [forbidden.status_code, stale.status_code, invalid_digest.status_code])
        scan.assert_not_called()
        self.assertFalse((self.store.root / "Verboten.bin").exists())
        self.assertFalse((self.store.root / "Digest.bin").exists())

    def test_disabled_upload_scan_preserves_webdav_compatibility(self):
        with mock.patch.object(ClamAV, "scan", autospec=True) as scan:
            response = self.client.put(
                f"{self.files}/OhneScanner.txt",
                data=b"compatible default",
                headers={**self.auth, "If-None-Match": "*"},
            )

        self.assertEqual(201, response.status_code)
        scan.assert_not_called()
        self.assertEqual(b"compatible default", (self.store.root / "OhneScanner.txt").read_bytes())

    def test_copy_move_and_soft_delete_keep_audit_and_recovery(self):
        self.client.open(f"{self.files}/Ablage", method="MKCOL", headers=self.auth)
        copied = self.client.open(
            f"{self.files}/angebot.odt",
            method="COPY",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/Ablage/Kopie.odt", "Overwrite": "F"},
        )
        copied_document = self.store.get_document("Ablage/Kopie.odt")
        moved = self.client.open(
            f"{self.files}/Ablage/Kopie.odt",
            method="MOVE",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/Ablage/Umbenannt.odt", "Overwrite": "F"},
        )
        deleted = self.client.delete(f"{self.files}/Ablage/Umbenannt.odt", headers=self.auth)

        self.assertEqual(201, copied.status_code)
        self.assertNotEqual(self.document["document_id"], copied_document["document_id"])
        self.assertEqual(self.document["document_id"], copied_document["attributes"]["copied_from"])
        self.assertEqual(201, moved.status_code)
        self.assertEqual(204, deleted.status_code)
        tombstone = self.store.get_document(copied_document["document_id"])
        self.assertEqual("webdav_deleted", tombstone["system_state"])
        self.assertFalse((self.store.root / "Ablage/Umbenannt.odt").exists())
        self.assertEqual(1, len(list((self.store.control / "webdav-trash" / copied_document["document_id"]).glob("*--Umbenannt.odt"))))
        actions = {row.get("type") for row in self.store.logbook()}
        self.assertTrue({"document_copied", "document_moved", "document_soft_deleted"}.issubset(actions))

    def test_owner_can_restore_soft_deleted_document_from_confirmed_web_page(self):
        deleted = self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        (self.store.root / "Wiederhergestellt").mkdir()
        page = self.client.get("/documents/recovery")
        restored = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "Wiederhergestellt/angebot.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )

        self.assertEqual(204, deleted.status_code)
        self.assertEqual(200, page.status_code)
        self.assertIn("angebot.odt", page.get_data(as_text=True))
        self.assertIn("WIEDERHERSTELLEN", page.get_data(as_text=True))
        self.assertEqual(200, restored.status_code)
        target = self.store.root / "Wiederhergestellt" / "angebot.odt"
        self.assertEqual(b"first office version", target.read_bytes())
        metadata = self.store.get_document(self.document["document_id"])
        self.assertEqual("Wiederhergestellt/angebot.odt", metadata["last_path"])
        self.assertEqual("indexed", metadata["system_state"])
        self.assertEqual("jens", metadata["restored_by"])
        self.assertEqual(self.document["sha256"], metadata["recovery_history"][-1]["sha256"])
        self.assertTrue(any(row.get("type") == "document_restored" for row in self.store.logbook()))

    def test_recovery_is_user_isolated_and_requires_explicit_confirmation(self):
        self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        missing_confirmation = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={"destination_path": "angebot.odt", "expected_sha256": self.document["sha256"]},
        )
        self.assertEqual(302, missing_confirmation.status_code)
        self.assertFalse((self.store.root / "angebot.odt").exists())

        self.client.get("/auth/logout")
        self.client.post("/auth/register", data={"username": "other", "password": "other-browser-password"})
        self.client.post("/auth/login", data={"username": "other", "password": "other-browser-password"})
        page = self.client.get("/documents/recovery")
        forbidden = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "gestohlen.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
        )

        self.assertNotIn("angebot.odt", page.get_data(as_text=True))
        self.assertEqual(404, forbidden.status_code)
        self.assertFalse((self.store.root / "gestohlen.odt").exists())

    def test_recovery_never_overwrites_and_detects_tampered_payload(self):
        self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        (self.store.root / "angebot.odt").write_bytes(b"new independent file")
        conflict = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "angebot.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )
        self.assertIn("never overwrites", conflict.get_data(as_text=True))
        self.assertEqual(b"new independent file", (self.store.root / "angebot.odt").read_bytes())

        tombstone = self.store.get_document(self.document["document_id"])
        recovery = self.store.control / tombstone["recovery_path"]
        recovery.write_bytes(b"tampered")
        rejected = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "anderer-name.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )
        self.assertIn("integrity verification", rejected.get_data(as_text=True))
        self.assertFalse((self.store.root / "anderer-name.odt").exists())
        self.assertEqual("webdav_deleted", self.store.get_document(self.document["document_id"])["system_state"])

    def test_legacy_soft_delete_metadata_can_be_restored_by_original_actor(self):
        self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        tombstone = self.store.get_document(self.document["document_id"])
        tombstone.pop("deleted_by", None)
        tombstone.pop("recovery_path", None)
        self.store._save_document(tombstone)

        restored = self.client.post(
            f"/documents/recovery/{self.document['document_id']}/restore",
            data={
                "destination_path": "legacy.odt",
                "expected_sha256": self.document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
        )

        self.assertEqual(302, restored.status_code)
        self.assertEqual(b"first office version", (self.store.root / "legacy.odt").read_bytes())

    def test_archived_content_can_be_restored_as_a_new_audited_revision(self):
        current = self.client.get(self.url, headers=self.auth)
        self.client.put(self.url, data=b"second office version", headers={**self.auth, "If-Match": current.headers["ETag"]})
        metadata = self.store.get_document(self.document["document_id"])
        page = self.client.get(f"/documents/{self.document['document_id']}")
        restored = self.client.post(
            f"/documents/{self.document['document_id']}/restore-content",
            data={
                "archived_sha256": self.document["sha256"],
                "expected_current_sha256": metadata["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )

        self.assertIn(self.document["sha256"], page.get_data(as_text=True))
        self.assertEqual(200, restored.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        recovered = self.store.get_document(self.document["document_id"])
        self.assertEqual(2, recovered["content_revision"])
        self.assertEqual(self.document["sha256"], recovered["content_recovery_history"][-1]["restored_sha256"])
        self.assertTrue(any(row.get("type") == "document_content_restored" for row in self.store.logbook(self.document["document_id"])))

    def test_content_recovery_rejects_stale_page_and_retention_lock(self):
        original_sha = self.document["sha256"]
        current = self.client.get(self.url, headers=self.auth)
        self.client.put(self.url, data=b"second", headers={**self.auth, "If-Match": current.headers["ETag"]})
        second = self.store.get_document(self.document["document_id"])
        self.client.put(self.url, data=b"third", headers={**self.auth, "If-Match": f'"{second["sha256"]}"'})

        stale = self.client.post(
            f"/documents/{self.document['document_id']}/restore-content",
            data={
                "archived_sha256": original_sha,
                "expected_current_sha256": second["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )
        self.assertIn("changed since the recovery page", stale.get_data(as_text=True))
        self.assertEqual(b"third", (self.store.root / "angebot.odt").read_bytes())

        locked = self.store.get_document(self.document["document_id"])
        locked["cleanup_state"] = "staged"
        self.store._save_document(locked)
        blocked = self.client.post(
            f"/documents/{self.document['document_id']}/restore-content",
            data={
                "archived_sha256": original_sha,
                "expected_current_sha256": locked["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )
        self.assertIn("staged for manual deletion", blocked.get_data(as_text=True))
        self.assertEqual(b"third", (self.store.root / "angebot.odt").read_bytes())

    def test_lock_null_resource_can_be_created_and_unlocked(self):
        target = f"{self.files}/LibreOffice-neu.odt"
        locked = self.client.open(target, method="LOCK", data=self.lock_body, headers={**self.auth, "Timeout": "Second-600"})
        token = locked.headers["Lock-Token"]
        empty = self.client.get(target, headers=self.auth)
        listed = self.client.open(
            self.files, method="PROPFIND", headers={**self.auth, "Depth": "1"}
        )
        created = self.client.put(target, data=b"office payload", headers={**self.auth, "If": f"(<{token.strip('<>')}>)"})
        unlocked = self.client.open(target, method="UNLOCK", headers={**self.auth, "Lock-Token": token})

        self.assertEqual(201, locked.status_code)
        self.assertEqual(200, empty.status_code)
        self.assertEqual(b"", empty.data)
        self.assertIn("LibreOffice-neu.odt", listed.get_data(as_text=True))
        self.assertEqual(204, created.status_code)
        self.assertEqual(204, unlocked.status_code)
        self.assertEqual(b"office payload", (self.store.root / "LibreOffice-neu.odt").read_bytes())

    def test_lock_refresh_and_discovery_follow_rfc4918(self):
        target = f"{self.files}/angebot.odt"
        locked = self.client.open(
            target, method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "0", "Timeout": "Second-60"},
        )
        token = locked.headers["Lock-Token"]
        before = json.loads((self.store.control / "webdav-locks.json").read_text())["locks"][self.document["document_id"]]
        missing_token = self.client.open(target, method="LOCK", headers=self.auth)
        refreshed = self.client.open(
            target, method="LOCK",
            headers={**self.auth, "If": f"(<{token.strip('<>')}>)", "Timeout": "Second-3600", "Depth": "invalid-but-ignored"},
        )
        after = json.loads((self.store.control / "webdav-locks.json").read_text())["locks"][self.document["document_id"]]
        query = '<d:propfind xmlns:d="DAV:"><d:prop><d:lockdiscovery/></d:prop></d:propfind>'
        discovered = self.client.open(target, method="PROPFIND", data=query, headers={**self.auth, "Depth": "0"})
        duplicate = self.client.open(
            target, method="LOCK", data=self.lock_body,
            headers={**self.auth, "If": f"(<{token.strip('<>')}>)"},
        )

        self.assertEqual(200, locked.status_code)
        self.assertEqual(412, missing_token.status_code)
        self.assertEqual(200, refreshed.status_code)
        self.assertNotIn("Lock-Token", refreshed.headers)
        self.assertEqual(before["created_at"], after["created_at"])
        self.assertGreater(after["expires_at"], before["expires_at"])
        self.assertIn("LibreOffice", refreshed.get_data(as_text=True))
        self.assertIn(token.strip("<>"), discovered.get_data(as_text=True))
        self.assertIn("lockroot", discovered.get_data(as_text=True))
        self.assertEqual(423, duplicate.status_code)
        self.assertIn("no-conflicting-lock", duplicate.get_data(as_text=True))
        lock_actions = {row.get("action") for row in self.store.logbook() if row.get("category") == "webdav-locks"}
        self.assertTrue({"webdav_lock_created", "webdav_lock_refreshed"}.issubset(lock_actions))

    def test_if_header_never_applies_a_lock_token_tagged_to_another_resource(self):
        target = f"{self.files}/angebot.odt"
        locked = self.client.open(target, method="LOCK", data=self.lock_body, headers=self.auth)
        token = locked.headers["Lock-Token"].strip("<>")

        foreign_tag = self.client.put(
            target, data=b"must not be stored",
            headers={**self.auth, "If": f"<http://localhost{self.files}/other.odt> (<{token}>)"},
        )
        cross_server = self.client.put(
            target, data=b"must not be stored either",
            headers={**self.auth, "If": f"<https://attacker.invalid{target}> (<{token}>)"},
        )

        self.assertEqual([412, 412], [foreign_tag.status_code, cross_server.status_code])
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())

    def test_tagged_if_header_combines_lock_etag_and_not_conditions(self):
        target = f"{self.files}/angebot.odt"
        locked = self.client.open(target, method="LOCK", data=self.lock_body, headers=self.auth)
        token = locked.headers["Lock-Token"].strip("<>")
        etag = self.client.get(target, headers=self.auth).headers["ETag"]
        valid_if = f"<http://localhost{target}> (<{token}> [{etag}] Not <urn:example:unknown>)"

        saved = self.client.put(target, data=b"checked", headers={**self.auth, "If": valid_if})
        stale = self.client.put(
            target, data=b"stale",
            headers={**self.auth, "If": f"<{target}> (<{token}> [{etag}])"},
        )

        self.assertEqual(204, saved.status_code)
        self.assertEqual(412, stale.status_code)
        self.assertEqual(b"checked", (self.store.root / "angebot.odt").read_bytes())

    def test_if_header_uses_or_between_lists_and_and_inside_each_list(self):
        target = f"{self.files}/angebot.odt"
        locked = self.client.open(target, method="LOCK", data=self.lock_body, headers=self.auth)
        token = locked.headers["Lock-Token"].strip("<>")
        wrong = "opaquelocktoken:00000000-0000-0000-0000-000000000000"

        saved = self.client.put(
            target, data=b"second",
            headers={**self.auth, "If": f"(<{wrong}>)(<{token}>)"},
        )
        rejected = self.client.put(
            target, data=b"third",
            headers={**self.auth, "If": f"(<{token}> [\"wrong\"])"},
        )

        self.assertEqual(204, saved.status_code)
        self.assertEqual(412, rejected.status_code)
        self.assertEqual(b"second", (self.store.root / "angebot.odt").read_bytes())

    def test_tagged_source_lock_allows_copy_but_unrelated_tag_does_not(self):
        target = f"{self.files}/angebot.odt"
        locked = self.client.open(target, method="LOCK", data=self.lock_body, headers=self.auth)
        token = locked.headers["Lock-Token"].strip("<>")
        copied = self.client.open(
            target, method="COPY",
            headers={
                **self.auth,
                "Destination": f"http://localhost{self.files}/Kopie.odt",
                "If": f"<http://localhost{target}> (<{token}>)",
            },
        )
        rejected = self.client.open(
            target, method="COPY",
            headers={
                **self.auth,
                "Destination": f"http://localhost{self.files}/Nicht-erlaubt.odt",
                "If": f"<http://localhost{self.files}/Kopie.odt> (<{token}>)",
            },
        )

        self.assertEqual(201, copied.status_code)
        self.assertEqual(412, rejected.status_code)
        self.assertEqual(b"first office version", (self.store.root / "Kopie.odt").read_bytes())
        self.assertFalse((self.store.root / "Nicht-erlaubt.odt").exists())

    def test_if_header_rejects_malformed_mixed_and_oversized_input_before_write(self):
        target = f"{self.files}/angebot.odt"
        current = self.client.get(target, headers=self.auth).headers["ETag"]
        malformed = self.client.put(
            target, data=b"bad", headers={**self.auth, "If-Match": current, "If": "(<broken>"},
        )
        mixed = self.client.put(
            target, data=b"bad", headers={**self.auth, "If-Match": current, "If": "(<urn:a>) </tag> (<urn:b>)"},
        )
        oversized = self.client.put(
            target, data=b"bad",
            headers={**self.auth, "If-Match": current, "If": "(" + " Not <urn:x>" * 1400 + ")"},
        )

        self.assertEqual([400, 400, 413], [malformed.status_code, mixed.status_code, oversized.status_code])
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())

    def test_lock_refresh_requires_token_for_exact_request_uri(self):
        target = f"{self.files}/angebot.odt"
        locked = self.client.open(target, method="LOCK", data=self.lock_body, headers=self.auth)
        token = locked.headers["Lock-Token"].strip("<>")
        wrong_resource = self.client.open(
            target, method="LOCK",
            headers={**self.auth, "If": f"<{self.files}/other.odt> (<{token}>)"},
        )
        refreshed = self.client.open(
            target, method="LOCK",
            headers={**self.auth, "If": f"<http://localhost{target}> (<{token}>)"},
        )

        self.assertEqual(412, wrong_resource.status_code)
        self.assertEqual(200, refreshed.status_code)

    def test_unlock_accepts_only_one_exact_lock_token_header(self):
        target = f"{self.files}/angebot.odt"
        locked = self.client.open(target, method="LOCK", data=self.lock_body, headers=self.auth)
        token = locked.headers["Lock-Token"]
        if_only = self.client.open(
            target, method="UNLOCK", headers={**self.auth, "If": f"(<{token.strip('<>')}>)"},
        )
        malformed = self.client.open(
            target, method="UNLOCK", headers={**self.auth, "Lock-Token": token + " " + token},
        )
        unlocked = self.client.open(target, method="UNLOCK", headers={**self.auth, "Lock-Token": token})

        self.assertEqual([400, 400, 204], [if_only.status_code, malformed.status_code, unlocked.status_code])

    def test_lock_rejects_unsupported_scope_and_depth_but_accepts_recursive_collection(self):
        shared = self.lock_body.replace("exclusive", "shared")
        wrong_scope = self.client.open(f"{self.files}/angebot.odt", method="LOCK", data=shared, headers=self.auth)
        wrong_depth = self.client.open(
            f"{self.files}/angebot.odt", method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "1"},
        )
        recursive = self.client.open(self.files, method="LOCK", data=self.lock_body, headers=self.auth)

        self.assertEqual([400, 400, 200], [wrong_scope.status_code, wrong_depth.status_code, recursive.status_code])
        self.assertEqual("infinity", ElementTree.fromstring(recursive.data).findtext(".//{DAV:}depth"))

    def test_depth_infinity_collection_lock_protects_existing_and_new_members(self):
        folder = f"{self.files}/Projekte"
        self.client.open(folder, method="MKCOL", headers=self.auth)
        self.client.put(f"{folder}/Plan.odt", data=b"first", headers=self.auth)
        locked = self.client.open(
            folder, method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "infinity", "Timeout": "Second-600"},
        )
        token = locked.headers["Lock-Token"].strip("<>")
        etag = self.client.get(f"{folder}/Plan.odt", headers=self.auth).headers["ETag"]

        blocked_existing = self.client.put(
            f"{folder}/Plan.odt", data=b"blocked",
            headers={**self.auth, "If-Match": etag},
        )
        blocked_new = self.client.put(f"{folder}/Neu.odt", data=b"blocked", headers=self.auth)
        blocked_folder = self.client.open(f"{folder}/Unterordner", method="MKCOL", headers=self.auth)
        saved = self.client.put(
            f"{folder}/Plan.odt", data=b"saved",
            headers={**self.auth, "If": f"(<{token}>)"},
        )
        created = self.client.put(
            f"{folder}/Neu.odt", data=b"new",
            headers={**self.auth, "If": f"(<{token}>)"},
        )
        created_folder = self.client.open(
            f"{folder}/Unterordner", method="MKCOL",
            headers={**self.auth, "If": f"(<{token}>)"},
        )

        self.assertEqual(200, locked.status_code)
        self.assertEqual([423, 423, 423], [blocked_existing.status_code, blocked_new.status_code, blocked_folder.status_code])
        self.assertEqual([204, 201, 201], [saved.status_code, created.status_code, created_folder.status_code])
        self.assertEqual(b"saved", (self.store.root / "Projekte" / "Plan.odt").read_bytes())

    def test_inherited_lock_is_discoverable_on_descendants_without_leaking_token_elsewhere(self):
        folder = f"{self.files}/Kunden"
        self.client.open(folder, method="MKCOL", headers=self.auth)
        self.client.put(f"{folder}/A.odt", data=b"a", headers=self.auth)
        locked = self.client.open(folder, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "infinity"})
        token = locked.headers["Lock-Token"].strip("<>")
        query = '<d:propfind xmlns:d="DAV:"><d:prop><d:lockdiscovery/></d:prop></d:propfind>'

        child = self.client.open(
            f"{folder}/A.odt", method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        root = self.client.open(
            self.files, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )

        self.assertEqual([207, 207], [child.status_code, root.status_code])
        self.assertIn(token, child.get_data(as_text=True))
        self.assertIn(f"{folder}", child.get_data(as_text=True))
        self.assertNotIn(token, root.get_data(as_text=True))

    def test_overlapping_recursive_locks_are_rejected_in_both_directions(self):
        parent = f"{self.files}/Projekte"
        child = f"{parent}/Unterordner"
        self.client.open(parent, method="MKCOL", headers=self.auth)
        self.client.open(child, method="MKCOL", headers=self.auth)
        parent_lock = self.client.open(parent, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "infinity"})
        child_conflict = self.client.open(child, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "0"})
        self.client.open(parent, method="UNLOCK", headers={**self.auth, "Lock-Token": parent_lock.headers["Lock-Token"]})
        child_lock = self.client.open(child, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "0"})
        parent_conflict = self.client.open(parent, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "infinity"})

        self.assertEqual(200, parent_lock.status_code)
        self.assertEqual(423, child_conflict.status_code)
        self.assertEqual(200, child_lock.status_code)
        self.assertEqual(423, parent_conflict.status_code)
        self.assertIn("no-conflicting-lock", parent_conflict.get_data(as_text=True))

    def test_depth_zero_collection_lock_does_not_lock_members(self):
        folder = f"{self.files}/Projekte"
        self.client.open(folder, method="MKCOL", headers=self.auth)
        self.client.put(f"{folder}/Plan.odt", data=b"first", headers=self.auth)
        locked = self.client.open(folder, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "0"})
        current = self.client.get(f"{folder}/Plan.odt", headers=self.auth)
        saved = self.client.put(
            f"{folder}/Plan.odt", data=b"second",
            headers={**self.auth, "If-Match": current.headers["ETag"]},
        )
        blocked_delete = self.client.delete(folder, headers=self.auth)

        self.assertEqual(200, locked.status_code)
        self.assertEqual(204, saved.status_code)
        self.assertEqual(423, blocked_delete.status_code)

    def test_collection_lock_copy_requires_token_for_locked_destination(self):
        folder = f"{self.files}/Projekte"
        self.client.open(folder, method="MKCOL", headers=self.auth)
        self.client.put(f"{folder}/Quelle.odt", data=b"source", headers=self.auth)
        locked = self.client.open(folder, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "infinity"})
        token = locked.headers["Lock-Token"].strip("<>")
        source = f"{folder}/Quelle.odt"
        destination = f"{folder}/Kopie.odt"

        missing_destination = self.client.open(
            source, method="COPY",
            headers={
                **self.auth, "Destination": f"http://localhost{destination}",
                "If": f"<http://localhost{source}> (<{token}>)",
            },
        )
        copied = self.client.open(
            source, method="COPY",
            headers={
                **self.auth, "Destination": f"http://localhost{destination}",
                "If": f"<http://localhost{source}> (<{token}>) <http://localhost{destination}> (<{token}>)",
            },
        )

        self.assertEqual(423, missing_destination.status_code)
        self.assertEqual(201, copied.status_code)
        self.assertEqual(b"source", (self.store.root / "Projekte" / "Kopie.odt").read_bytes())

    def test_collection_lock_refresh_and_unlock_must_target_lock_root(self):
        folder = f"{self.files}/Projekte"
        child = f"{folder}/Plan.odt"
        self.client.open(folder, method="MKCOL", headers=self.auth)
        self.client.put(child, data=b"plan", headers=self.auth)
        locked = self.client.open(folder, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "infinity"})
        token_header = locked.headers["Lock-Token"]
        token = token_header.strip("<>")

        child_refresh = self.client.open(child, method="LOCK", headers={**self.auth, "If": f"(<{token}>)"})
        root_refresh = self.client.open(folder, method="LOCK", headers={**self.auth, "If": f"(<{token}>)", "Timeout": "Second-3600"})
        child_unlock = self.client.open(child, method="UNLOCK", headers={**self.auth, "Lock-Token": token_header})
        root_unlock = self.client.open(folder, method="UNLOCK", headers={**self.auth, "Lock-Token": token_header})
        current = self.client.get(child, headers=self.auth)
        saved = self.client.put(child, data=b"after", headers={**self.auth, "If-Match": current.headers["ETag"]})

        self.assertEqual([412, 200, 409, 204, 204], [child_refresh.status_code, root_refresh.status_code, child_unlock.status_code, root_unlock.status_code, saved.status_code])

    def test_collection_lock_applies_to_stable_url_and_is_released_with_deleted_root(self):
        folder = f"{self.files}/Projekte"
        child = f"{folder}/Plan.odt"
        self.client.open(folder, method="MKCOL", headers=self.auth)
        created = self.client.put(child, data=b"first", headers=self.auth)
        document = self.store.get_document("Projekte/Plan.odt")
        stable = f"/webdav/documents/jens/{document['document_id']}--Plan.odt"
        locked = self.client.open(folder, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "infinity"})
        token = locked.headers["Lock-Token"].strip("<>")

        blocked_stable = self.client.put(stable, data=b"blocked", headers=self.auth)
        saved_stable = self.client.put(stable, data=b"saved", headers={**self.auth, "If": f"(<{token}>)"})
        deleted_file = self.client.delete(child, headers={**self.auth, "If": f"(<{token}>)"})
        deleted_root = self.client.delete(folder, headers={**self.auth, "If": f"(<{token}>)"})
        recreated = self.client.open(folder, method="MKCOL", headers=self.auth)
        created_without_old_token = self.client.put(f"{folder}/Neu.odt", data=b"new", headers=self.auth)

        self.assertEqual(201, created.status_code)
        self.assertEqual([423, 204, 204, 204, 201, 201], [
            blocked_stable.status_code, saved_stable.status_code, deleted_file.status_code,
            deleted_root.status_code, recreated.status_code, created_without_old_token.status_code,
        ])

    def test_expired_recursive_lock_no_longer_blocks_descendants(self):
        folder = f"{self.files}/Projekte"
        child = f"{folder}/Plan.odt"
        self.client.open(folder, method="MKCOL", headers=self.auth)
        self.client.put(child, data=b"first", headers=self.auth)
        self.client.open(folder, method="LOCK", data=self.lock_body, headers={**self.auth, "Depth": "infinity"})
        lock_path = self.store.control / "webdav-locks.json"
        payload = json.loads(lock_path.read_text())
        next(iter(payload["locks"].values()))["expires_at"] = "2000-01-01T00:00:00+00:00"
        lock_path.write_text(json.dumps(payload))
        current = self.client.get(child, headers=self.auth)

        saved = self.client.put(child, data=b"after expiry", headers={**self.auth, "If-Match": current.headers["ETag"]})

        self.assertEqual(204, saved.status_code)
        self.assertEqual(b"after expiry", (self.store.root / "Projekte" / "Plan.odt").read_bytes())

    def test_empty_collection_delete_never_removes_unknown_internal_metadata(self):
        folder = f"{self.files}/Projekte"
        self.client.open(folder, method="MKCOL", headers=self.auth)
        internal = self.store.root / "Projekte" / CONTROL_DIR
        internal.mkdir()
        (internal / "keep.bin").write_bytes(b"unknown")

        deleted = self.client.delete(folder, headers=self.auth)

        self.assertEqual(409, deleted.status_code)
        self.assertEqual(b"unknown", (internal / "keep.bin").read_bytes())

    def test_recursive_lock_respects_folder_scoped_device_boundary_and_audit(self):
        (self.store.root / "Projekte").mkdir()
        (self.store.root / "Privat").mkdir()
        with app.test_request_context():
            password = activate("jens", "jens", label="Projektgerät", path_prefix="Projekte", expires_days=30)
        scoped_auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}
        folder = f"{self.files}/Projekte"

        locked = self.client.open(folder, method="LOCK", data=self.lock_body, headers={**scoped_auth, "Depth": "infinity"})
        token = locked.headers["Lock-Token"].strip("<>")
        blocked = self.client.put(f"{folder}/Plan.odt", data=b"blocked", headers=scoped_auth)
        created = self.client.put(f"{folder}/Plan.odt", data=b"plan", headers={**scoped_auth, "If": f"(<{token}>)"})
        outside = self.client.open(self.files, method="LOCK", data=self.lock_body, headers={**scoped_auth, "Depth": "infinity"})
        sibling = self.client.put(
            f"{folder}/Zweite.odt", data=b"no",
            headers={**scoped_auth, "If": f"<{self.files}/Privat> (<{token}>)"},
        )
        lock_record = next(iter(json.loads((self.store.control / "webdav-locks.json").read_text())["locks"].values()))
        audit = [row for row in self.store.logbook() if row.get("action") == "webdav_lock_created"]
        lock_snapshots = [
            json.loads(path.read_text())
            for path in (self.store.history.root / "snapshots" / "webdav-locks").glob("*.json")
        ]

        self.assertEqual([200, 423, 201, 404, 412], [locked.status_code, blocked.status_code, created.status_code, outside.status_code, sibling.status_code])
        self.assertEqual("Projekte", lock_record["resource"])
        self.assertEqual("infinity", lock_record["depth"])
        self.assertTrue(audit)
        self.assertTrue(any(row.get("depth") == "infinity" and row.get("resource") == "Projekte" for row in lock_snapshots))
        self.assertFalse((self.store.root / "Projekte" / "Zweite.odt").exists())

    def test_rfc4331_quota_properties_are_explicit_protected_live_properties(self):
        app.config["WEBDAV_QUOTA_BYTES"] = 1024 * 1024
        query = '<d:propfind xmlns:d="DAV:"><d:prop><d:quota-used-bytes/><d:quota-available-bytes/></d:prop></d:propfind>'
        explicit = self.client.open(self.files, method="PROPFIND", data=query, headers={**self.auth, "Depth": "0"})
        names = self.client.open(
            self.files, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:"><d:propname/></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )
        all_properties = self.client.open(self.files, method="PROPFIND", headers={**self.auth, "Depth": "0"})
        protected = self.client.open(
            self.files, method="PROPPATCH",
            data='<d:propertyupdate xmlns:d="DAV:"><d:set><d:prop><d:quota-used-bytes>0</d:quota-used-bytes></d:prop></d:set></d:propertyupdate>',
            headers=self.auth,
        )
        page = self.client.get(f"/documents/{self.document['document_id']}/libreoffice")
        root = ElementTree.fromstring(explicit.data)

        self.assertEqual(207, explicit.status_code)
        self.assertEqual(len(b"first office version"), int(root.findtext(".//{DAV:}quota-used-bytes")))
        self.assertEqual(1024 * 1024 - len(b"first office version"), int(root.findtext(".//{DAV:}quota-available-bytes")))
        self.assertIn("quota-used-bytes", names.get_data(as_text=True))
        self.assertNotIn("quota-used-bytes", all_properties.get_data(as_text=True))
        self.assertIn("403 Forbidden", protected.get_data(as_text=True))
        self.assertIn("WebDAV-Speicher", page.get_data(as_text=True))

    def test_quota_atomically_blocks_growth_but_allows_shrink_and_move(self):
        app.config["WEBDAV_QUOTA_BYTES"] = 24
        allowed = self.client.put(f"{self.files}/vier.bin", data=b"1234", headers=self.auth)
        rejected_create = self.client.put(f"{self.files}/eins.bin", data=b"1", headers=self.auth)
        rejected_copy = self.client.open(
            f"{self.files}/angebot.odt", method="COPY",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/kopie.odt"},
        )
        current = self.client.get(f"{self.files}/angebot.odt", headers=self.auth)
        rejected_growth = self.client.put(
            f"{self.files}/angebot.odt", data=b"x" * 21,
            headers={**self.auth, "If-Match": current.headers["ETag"]},
        )
        shrunk = self.client.put(
            f"{self.files}/angebot.odt", data=b"short",
            headers={**self.auth, "If-Match": current.headers["ETag"]},
        )
        moved = self.client.open(
            f"{self.files}/vier.bin", method="MOVE",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/umbenannt.bin"},
        )

        self.assertEqual(201, allowed.status_code)
        self.assertEqual([507, 507, 507], [rejected_create.status_code, rejected_copy.status_code, rejected_growth.status_code])
        self.assertIn("quota-not-exceeded", rejected_create.get_data(as_text=True))
        self.assertFalse((self.store.root / "eins.bin").exists())
        self.assertFalse((self.store.root / "kopie.odt").exists())
        self.assertEqual(b"short", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(204, shrunk.status_code)
        self.assertEqual(201, moved.status_code)
        self.assertTrue((self.store.root / "umbenannt.bin").is_file())
        rejections = [row for row in self.store.logbook() if row.get("action") == "webdav_quota_rejected"]
        self.assertGreaterEqual(len(rejections), 3)

    def test_disabled_quota_keeps_existing_unlimited_behavior(self):
        query = '<d:propfind xmlns:d="DAV:"><d:prop><d:quota-used-bytes/><d:quota-available-bytes/></d:prop></d:propfind>'
        response = self.client.open(self.files, method="PROPFIND", data=query, headers={**self.auth, "Depth": "0"})
        created = self.client.put(f"{self.files}/unlimited.bin", data=b"payload", headers=self.auth)

        self.assertIn("404 Not Found", response.get_data(as_text=True))
        self.assertEqual(201, created.status_code)

    def test_collection_delete_is_recursive_recoverable_and_sync_visible(self):
        report_body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>infinite</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=report_body, headers=self.auth)
        token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")
        self.client.open(f"{self.files}/Ordner", method="MKCOL", headers=self.auth)
        self.client.open(f"{self.files}/Ordner/Unterordner", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Ordner/datei.txt", data=b"content", headers=self.auth)
        self.client.put(f"{self.files}/Ordner/Unterordner/zweite.txt", data=b"second", headers=self.auth)
        first = self.store.get_document("Ordner/datei.txt")
        second = self.store.get_document("Ordner/Unterordner/zweite.txt")
        portable = self.store.root / "Ordner" / CONTROL_DIR
        portable.mkdir(exist_ok=True)
        (portable / f"{first['document_id']}.json").write_text(json.dumps(first))

        deleted = self.client.delete(f"{self.files}/Ordner", headers=self.auth)
        incremental = self.client.open(
            self.files, method="REPORT",
            data=report_body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>"),
            headers=self.auth,
        )
        page = self.client.get("/documents/recovery")
        missing_parent = self.client.open(f"{self.files}/fehlt/Kind", method="MKCOL", headers=self.auth)
        reserved = self.client.open(f"{self.files}/.simpleoffice-meta", method="PROPFIND", headers={**self.auth, "Depth": "0"})

        self.assertEqual(204, deleted.status_code)
        self.assertFalse((self.store.root / "Ordner").exists())
        self.assertEqual(404, self.client.get(f"{self.files}/Ordner/datei.txt", headers=self.auth).status_code)
        tombstones = [self.store.get_document(item["document_id"]) for item in (first, second)]
        self.assertTrue(all(item["system_state"] == "webdav_deleted" for item in tombstones))
        self.assertEqual(1, len({item["collection_recovery_id"] for item in tombstones}))
        self.assertEqual([b"content", b"second"], [self.store._recovery_file(item).read_bytes() for item in tombstones])
        recovery_tree = self.store._recovery_file(tombstones[0]).parent
        self.assertTrue((recovery_tree / CONTROL_DIR / f"{first['document_id']}.json").is_file())
        self.assertIn("datei.txt", page.get_data(as_text=True))
        self.assertIn("zweite.txt", page.get_data(as_text=True))
        self.assertIn("404 Not Found", incremental.get_data(as_text=True))
        self.assertIn("Ordner/", incremental.get_data(as_text=True))
        actions = {row.get("type") for row in self.store.logbook()}
        self.assertTrue({"document_soft_deleted", "webdav_collection_soft_deleted"}.issubset(actions))
        self.assertEqual(409, missing_parent.status_code)
        self.assertEqual(404, reserved.status_code)

    def test_collection_delete_requires_infinite_depth_and_preflights_retention(self):
        self.client.open(f"{self.files}/Geschuetzt", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Geschuetzt/A.txt", data=b"a", headers=self.auth)
        self.client.put(f"{self.files}/Geschuetzt/B.txt", data=b"b", headers=self.auth)
        blocked = self.store.get_document("Geschuetzt/B.txt")
        blocked["cleanup_state"] = "staged"
        self.store._save_document(blocked)

        wrong_depth = self.client.delete(
            f"{self.files}/Geschuetzt", headers={**self.auth, "Depth": "0"},
        )
        retention = self.client.delete(f"{self.files}/Geschuetzt", headers=self.auth)

        self.assertEqual(400, wrong_depth.status_code)
        self.assertEqual(423, retention.status_code)
        self.assertTrue((self.store.root / "Geschuetzt/A.txt").is_file())
        self.assertTrue((self.store.root / "Geschuetzt/B.txt").is_file())
        self.assertEqual("indexed", self.store.get_document("Geschuetzt/A.txt")["system_state"])

    def test_collection_delete_requires_descendant_lock_token_then_destroys_lock(self):
        self.client.open(f"{self.files}/Gesperrt", method="MKCOL", headers=self.auth)
        child = f"{self.files}/Gesperrt/Plan.odt"
        self.client.put(child, data=b"plan", headers=self.auth)
        locked = self.client.open(child, method="LOCK", data=self.lock_body, headers=self.auth)
        token = locked.headers["Lock-Token"]

        rejected = self.client.delete(f"{self.files}/Gesperrt", headers=self.auth)
        self.assertEqual(423, rejected.status_code)
        self.assertTrue((self.store.root / "Gesperrt/Plan.odt").is_file())
        accepted = self.client.delete(
            f"{self.files}/Gesperrt",
            headers={**self.auth, "If": f"<http://localhost{child}> ({token})"},
        )

        self.assertEqual(204, accepted.status_code)
        locks = json.loads((self.store.control / "webdav-locks.json").read_text())["locks"]
        self.assertEqual({}, locks)
        actions = [row.get("action") for row in self.store.logbook()]
        self.assertIn("webdav_lock_destroyed_by_delete", actions)

    def test_collection_delete_refuses_unsafe_member_without_partial_change(self):
        self.client.open(f"{self.files}/Unsicher", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Unsicher/sicher.txt", data=b"safe", headers=self.auth)
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_bytes(b"outside")
        (self.store.root / "Unsicher/verweis.txt").symlink_to(outside)

        response = self.client.delete(f"{self.files}/Unsicher", headers=self.auth)

        self.assertEqual(409, response.status_code)
        self.assertTrue((self.store.root / "Unsicher/sicher.txt").is_file())
        self.assertTrue((self.store.root / "Unsicher/verweis.txt").is_symlink())
        self.assertEqual("indexed", self.store.get_document("Unsicher/sicher.txt")["system_state"])

    def test_collection_delete_rolls_back_namespace_and_metadata_on_storage_failure(self):
        self.client.open(f"{self.files}/Rollback-Loeschen", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Rollback-Loeschen/A.txt", data=b"a", headers=self.auth)
        self.client.put(f"{self.files}/Rollback-Loeschen/B.txt", data=b"b", headers=self.auth)
        original_save = DocumentStore._save_document
        calls = 0

        def fail_second(store, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated metadata failure")
            return original_save(store, *args, **kwargs)

        with mock.patch.object(DocumentStore, "_save_document", fail_second):
            response = self.client.delete(f"{self.files}/Rollback-Loeschen", headers=self.auth)

        self.assertEqual(507, response.status_code)
        self.assertEqual(b"a", (self.store.root / "Rollback-Loeschen/A.txt").read_bytes())
        self.assertEqual(b"b", (self.store.root / "Rollback-Loeschen/B.txt").read_bytes())
        self.assertEqual("indexed", self.store.get_document("Rollback-Loeschen/A.txt")["system_state"])
        self.assertEqual("indexed", self.store.get_document("Rollback-Loeschen/B.txt")["system_state"])
        actions = [row.get("action") for row in self.store.logbook()]
        self.assertIn("webdav_collection_delete_rolled_back", actions)

    def test_interrupted_collection_delete_is_recovered_during_initialize(self):
        self.client.open(f"{self.files}/Absturz", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Absturz/Plan.txt", data=b"plan", headers=self.auth)
        document = self.store.get_document("Absturz/Plan.txt")
        original_save = DocumentStore._save_document
        interrupted = False

        def interrupt_once(store, *args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt("simulated process interruption")
            return original_save(store, *args, **kwargs)

        with mock.patch.object(DocumentStore, "_save_document", interrupt_once):
            with self.assertRaises(KeyboardInterrupt):
                self.store.soft_delete_collection("Absturz", "webdav:jens")

        self.assertFalse((self.store.root / "Absturz").exists())
        recovered_store = DocumentStore(self.store.root)
        recovered_store.initialize()

        self.assertEqual(b"plan", (self.store.root / "Absturz/Plan.txt").read_bytes())
        self.assertEqual("indexed", recovered_store.get_document(document["document_id"])["system_state"])
        pending = list((self.store.control / "webdav-collection-trash").glob("*/manifest.json"))
        self.assertEqual([], pending)
        self.assertTrue(any(row.get("type") == "webdav_collection_delete_recovered" for row in recovered_store.logbook()))

    def test_file_from_deleted_collection_can_be_restored_individually(self):
        self.client.open(f"{self.files}/Alt", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Alt/Brief.odt", data=b"brief", headers=self.auth)
        document = self.store.get_document("Alt/Brief.odt")
        self.client.delete(f"{self.files}/Alt", headers=self.auth)
        self.client.open(f"{self.files}/Neu", method="MKCOL", headers=self.auth)

        restored = self.client.post(
            f"/documents/recovery/{document['document_id']}/restore",
            data={
                "destination_path": "Neu/Brief.odt",
                "expected_sha256": document["sha256"],
                "confirm": "WIEDERHERSTELLEN",
            },
            follow_redirects=True,
        )

        self.assertEqual(200, restored.status_code)
        self.assertEqual(b"brief", (self.store.root / "Neu/Brief.odt").read_bytes())
        metadata = self.store.get_document(document["document_id"])
        self.assertEqual("indexed", metadata["system_state"])
        self.assertTrue(metadata["recovery_history"][-1]["collection_recovery_id"])
        self.assertNotIn("collection_recovery_id", metadata)

    def test_symlinks_and_retention_locks_cannot_be_bypassed(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        (self.store.root / "shortcut").symlink_to(outside, target_is_directory=True)
        symlink = self.client.get(f"{self.files}/shortcut/secret.txt", headers=self.auth)

        metadata = self.store.get_document(self.document["document_id"])
        metadata["cleanup_state"] = "staged"
        self.store._save_document(metadata)
        deleted = self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        copied = self.client.open(
            f"{self.files}/angebot.odt",
            method="COPY",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/copy.odt"},
        )

        self.assertEqual(404, symlink.status_code)
        self.assertEqual(423, deleted.status_code)
        self.assertEqual(423, copied.status_code)
        self.assertTrue((self.store.root / "angebot.odt").is_file())

    def test_destination_and_user_boundaries_are_enforced(self):
        other_host = self.client.open(
            f"{self.files}/angebot.odt",
            method="COPY",
            headers={**self.auth, "Destination": "https://attacker.invalid/webdav/files/jens/stolen.odt"},
        )
        other_user = self.client.open(
            f"{self.files}/angebot.odt",
            method="MOVE",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/other/stolen.odt"},
        )
        existing = self.client.open(
            f"{self.files}/angebot.odt",
            method="COPY",
            headers={**self.auth, "Destination": "http://localhost/webdav/files/jens/angebot.odt"},
        )

        self.assertEqual(502, other_host.status_code)
        self.assertEqual(502, other_user.status_code)
        self.assertEqual(412, existing.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())

    def test_move_can_safely_replace_a_file_with_tagged_destination_etag(self):
        target = f"{self.files}/angebot.odt"
        target_before = self.store.get_document("angebot.odt")
        target_before["tags"] = ["vertrag", "freigegeben"]
        target_before["grants"] = [{"username": "other", "role": "reader"}]
        self.store._save_document(target_before)
        property_body = '<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:set><d:prop><m:classification>intern</m:classification></d:prop></d:set></d:propertyupdate>'
        self.client.open(target, method="PROPPATCH", data=property_body, headers=self.auth)
        created = self.client.put(f"{self.files}/angebot.odt.tmp", data=b"saved by LibreOffice", headers=self.auth)
        source = self.store.get_document("angebot.odt.tmp")
        source["tags"] = ["temporaer"]
        self.store._save_document(source)
        target_etag = self.client.get(target, headers=self.auth).headers["ETag"]

        moved = self.client.open(
            f"{self.files}/angebot.odt.tmp", method="MOVE",
            headers={
                **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target}",
                "If": f"<http://localhost{target}> ([{target_etag}])",
            },
        )

        target_after = self.store.get_document("angebot.odt")
        consumed = self.store.get_document(source["document_id"])
        properties = self.client.open(
            target, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:classification/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )
        recovery = self.store.control / consumed["recovery_path"]
        versions = self.store.content_recovery_versions(target_after["document_id"])

        self.assertEqual(201, created.status_code)
        self.assertEqual(204, moved.status_code)
        self.assertEqual(moved.headers["Location"], moved.headers["Content-Location"])
        self.assertEqual(target_before["document_id"], target_after["document_id"])
        self.assertEqual(["vertrag", "freigegeben"], target_after["tags"])
        self.assertEqual([{"username": "other", "role": "reader"}], target_after["grants"])
        self.assertEqual(b"saved by LibreOffice", (self.store.root / "angebot.odt").read_bytes())
        self.assertFalse((self.store.root / "angebot.odt.tmp").exists())
        self.assertEqual("webdav_deleted", consumed["system_state"])
        self.assertEqual(b"saved by LibreOffice", recovery.read_bytes())
        self.assertTrue(any(item["sha256"] == target_before["sha256"] for item in versions))
        self.assertEqual("intern", ElementTree.fromstring(properties.data).findtext(".//{urn:simpleoffice:test}classification"))
        actions = {row.get("type") for row in self.store.logbook()}
        self.assertIn("webdav_document_replaced_via_move", actions)
        self.assertIn("document_soft_deleted", actions)

    def test_move_overwrite_requires_explicit_fresh_destination_guard(self):
        source_url = f"{self.files}/save.tmp"
        target_url = f"{self.files}/angebot.odt"
        self.client.put(source_url, data=b"candidate", headers=self.auth)
        target_etag = self.client.get(target_url, headers=self.auth).headers["ETag"]

        missing = self.client.open(
            source_url, method="MOVE",
            headers={**self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}"},
        )
        forbidden = self.client.open(
            source_url, method="MOVE",
            headers={
                **self.auth, "Overwrite": "F", "Destination": f"http://localhost{target_url}",
                "If": f"<http://localhost{target_url}> ([{target_etag}])",
            },
        )
        stale = self.client.open(
            source_url, method="MOVE",
            headers={
                **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                "If": f'<http://localhost{target_url}> (["{"0" * 64}"])',
            },
        )

        self.assertEqual(428, missing.status_code)
        self.assertEqual(target_etag, missing.headers["ETag"])
        self.assertEqual(412, forbidden.status_code)
        self.assertEqual(412, stale.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(b"candidate", (self.store.root / "save.tmp").read_bytes())

    def test_move_overwrite_accepts_target_lock_and_retains_it(self):
        source_url = f"{self.files}/office-save.tmp"
        target_url = f"{self.files}/angebot.odt"
        self.client.put(source_url, data=b"locked save", headers=self.auth)
        source_document = self.store.get_document("office-save.tmp")
        source_locked = self.client.open(
            source_url, method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "0", "Timeout": "Second-600"},
        )
        source_token = source_locked.headers["Lock-Token"].strip("<>")
        locked = self.client.open(
            target_url, method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "0", "Timeout": "Second-600"},
        )
        token = locked.headers["Lock-Token"].strip("<>")

        moved = self.client.open(
            source_url, method="MOVE",
            headers={
                **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                "If": (
                    f"<http://localhost{source_url}> (<{source_token}>) "
                    f"<http://localhost{target_url}> (<{token}>)"
                ),
            },
        )
        blocked = self.client.put(target_url, data=b"without token", headers=self.auth)
        current = self.client.get(target_url, headers=self.auth)
        saved = self.client.put(
            target_url, data=b"with token",
            headers={
                **self.auth, "If-Match": current.headers["ETag"],
                "If": f"<http://localhost{target_url}> (<{token}>)",
            },
        )

        self.assertEqual(200, locked.status_code)
        self.assertEqual(200, source_locked.status_code)
        self.assertEqual(204, moved.status_code)
        self.assertEqual(423, blocked.status_code)
        self.assertEqual(204, saved.status_code)
        self.assertEqual(b"with token", (self.store.root / "angebot.odt").read_bytes())
        locks = json.loads((self.store.control / "webdav-locks.json").read_text())["locks"]
        self.assertNotIn(source_document["document_id"], locks)
        self.assertIn(self.document["document_id"], locks)
        actions = [json.loads(path.read_text()).get("action") for path in (self.store.history.root / "events").glob("*.json")]
        self.assertIn("webdav_lock_released_by_move", actions)

    def test_move_overwrite_rolls_destination_back_when_source_consumption_fails(self):
        source_url = f"{self.files}/rollback-save.tmp"
        target_url = f"{self.files}/angebot.odt"
        self.client.put(source_url, data=b"not committed", headers=self.auth)
        target_etag = self.client.get(target_url, headers=self.auth).headers["ETag"]

        with mock.patch.object(DocumentStore, "soft_delete_document", side_effect=OSError("simulated trash failure")):
            response = self.client.open(
                source_url, method="MOVE",
                headers={
                    **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                    "If": f"<http://localhost{target_url}> ([{target_etag}])",
                },
            )

        self.assertEqual(507, response.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(b"not committed", (self.store.root / "rollback-save.tmp").read_bytes())
        actions = {row.get("type") for row in self.store.logbook()}
        self.assertIn("webdav_document_replace_rolled_back", actions)

    def test_move_overwrite_detects_late_source_change_and_restores_destination(self):
        source_url = f"{self.files}/racing-save.tmp"
        target_url = f"{self.files}/angebot.odt"
        self.client.put(source_url, data=b"initial temporary bytes", headers=self.auth)
        target_etag = self.client.get(target_url, headers=self.auth).headers["ETag"]
        original_replace = DocumentStore.replace_content

        def change_source_after_target_write(store, *args, **kwargs):
            result = original_replace(store, *args, **kwargs)
            if kwargs.get("source") == "webdav-move-overwrite":
                (store.root / "racing-save.tmp").write_bytes(b"newer external bytes")
            return result

        with mock.patch.object(DocumentStore, "replace_content", change_source_after_target_write):
            response = self.client.open(
                source_url, method="MOVE",
                headers={
                    **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                    "If": f"<http://localhost{target_url}> ([{target_etag}])",
                },
            )

        self.assertEqual(412, response.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(b"newer external bytes", (self.store.root / "racing-save.tmp").read_bytes())
        actions = {row.get("type") for row in self.store.logbook()}
        self.assertIn("webdav_document_replace_rolled_back", actions)

    def test_move_overwrite_rechecks_destination_after_if_evaluation(self):
        source_url = f"{self.files}/destination-race.tmp"
        target_url = f"{self.files}/angebot.odt"
        self.client.put(source_url, data=b"candidate", headers=self.auth)
        target_etag = self.client.get(target_url, headers=self.auth).headers["ETag"]
        original_move_replace = DocumentStore.replace_document_via_move

        def change_destination_before_locked_check(store, *args, **kwargs):
            (store.root / "angebot.odt").write_bytes(b"newer destination bytes")
            return original_move_replace(store, *args, **kwargs)

        with mock.patch.object(DocumentStore, "replace_document_via_move", change_destination_before_locked_check):
            response = self.client.open(
                source_url, method="MOVE",
                headers={
                    **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                    "If": f"<http://localhost{target_url}> ([{target_etag}])",
                },
            )

        self.assertEqual(412, response.status_code)
        self.assertEqual(b"newer destination bytes", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(b"candidate", (self.store.root / "destination-race.tmp").read_bytes())

    def test_copy_can_safely_replace_a_file_without_replacing_target_metadata(self):
        source_url = f"{self.files}/freigabe-vorlage.odt"
        target_url = f"{self.files}/angebot.odt"
        target_before = self.store.get_document("angebot.odt")
        target_before["tags"] = ["kunde", "freigegeben"]
        target_before["grants"] = [{"username": "other", "role": "reader"}]
        self.store._save_document(target_before)
        target_property = '<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:set><d:prop><m:classification>ziel-vertraulich</m:classification></d:prop></d:set></d:propertyupdate>'
        self.client.open(target_url, method="PROPPATCH", data=target_property, headers=self.auth)
        self.client.put(source_url, data=b"copied office contents", headers=self.auth)
        source = self.store.get_document("freigabe-vorlage.odt")
        source["tags"] = ["vorlage"]
        self.store._save_document(source)
        source_property = target_property.replace("ziel-vertraulich", "quelle-oeffentlich")
        self.client.open(source_url, method="PROPPATCH", data=source_property, headers=self.auth)
        target_etag = self.client.get(target_url, headers=self.auth).headers["ETag"]

        copied = self.client.open(
            source_url, method="COPY",
            headers={
                **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                "If": f"<http://localhost{target_url}> ([{target_etag}])",
            },
        )

        target_after = self.store.get_document("angebot.odt")
        source_after = self.store.get_document(source["document_id"])
        properties = self.client.open(
            target_url, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:classification/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )
        versions = self.store.content_recovery_versions(target_after["document_id"])

        self.assertEqual(204, copied.status_code)
        self.assertEqual(copied.headers["Location"], copied.headers["Content-Location"])
        self.assertEqual(target_before["document_id"], target_after["document_id"])
        self.assertEqual(["kunde", "freigegeben"], target_after["tags"])
        self.assertEqual([{"username": "other", "role": "reader"}], target_after["grants"])
        self.assertEqual(b"copied office contents", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(b"copied office contents", (self.store.root / "freigabe-vorlage.odt").read_bytes())
        self.assertEqual("indexed", source_after["system_state"])
        self.assertTrue(any(item["sha256"] == target_before["sha256"] for item in versions))
        self.assertEqual("ziel-vertraulich", ElementTree.fromstring(properties.data).findtext(".//{urn:simpleoffice:test}classification"))
        self.assertEqual(target_after["sha256"], copied.headers["ETag"].strip('"'))
        self.assertIn("sha-256=", copied.headers["Repr-Digest"])
        actions = {row.get("type") for row in self.store.logbook()}
        self.assertIn("webdav_document_replaced_via_copy", actions)
        self.assertIn("document_content_replaced", actions)

    def test_copy_overwrite_requires_explicit_fresh_destination_guard(self):
        source_url = f"{self.files}/copy-source.txt"
        target_url = f"{self.files}/angebot.odt"
        self.client.put(source_url, data=b"candidate", headers=self.auth)
        target_etag = self.client.get(target_url, headers=self.auth).headers["ETag"]

        missing = self.client.open(
            source_url, method="COPY",
            headers={**self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}"},
        )
        forbidden = self.client.open(
            source_url, method="COPY",
            headers={
                **self.auth, "Overwrite": "F", "Destination": f"http://localhost{target_url}",
                "If": f"<http://localhost{target_url}> ([{target_etag}])",
            },
        )
        stale = self.client.open(
            source_url, method="COPY",
            headers={
                **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                "If": f'<http://localhost{target_url}> (["{"0" * 64}"])',
            },
        )

        self.assertEqual(428, missing.status_code)
        self.assertEqual(target_etag, missing.headers["ETag"])
        self.assertEqual(412, forbidden.status_code)
        self.assertEqual(412, stale.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(b"candidate", (self.store.root / "copy-source.txt").read_bytes())

    def test_copy_overwrite_needs_only_target_lock_and_retains_both_locks(self):
        source_url = f"{self.files}/locked-source.txt"
        target_url = f"{self.files}/angebot.odt"
        self.client.put(source_url, data=b"copy through locks", headers=self.auth)
        source_document = self.store.get_document("locked-source.txt")
        source_locked = self.client.open(
            source_url, method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "0", "Timeout": "Second-600"},
        )
        target_locked = self.client.open(
            target_url, method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "0", "Timeout": "Second-600"},
        )
        target_token = target_locked.headers["Lock-Token"].strip("<>")

        copied = self.client.open(
            source_url, method="COPY",
            headers={
                **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                "If": f"<http://localhost{target_url}> (<{target_token}>)",
            },
        )
        blocked = self.client.put(target_url, data=b"without token", headers=self.auth)
        locks = json.loads((self.store.control / "webdav-locks.json").read_text())["locks"]

        self.assertEqual(200, source_locked.status_code)
        self.assertEqual(200, target_locked.status_code)
        self.assertEqual(204, copied.status_code)
        self.assertEqual(423, blocked.status_code)
        self.assertIn(source_document["document_id"], locks)
        self.assertIn(self.document["document_id"], locks)
        self.assertEqual(b"copy through locks", (self.store.root / "locked-source.txt").read_bytes())
        self.assertEqual(b"copy through locks", (self.store.root / "angebot.odt").read_bytes())

    def test_copy_overwrite_rechecks_source_and_destination_after_if_evaluation(self):
        source_url = f"{self.files}/racing-copy.txt"
        target_url = f"{self.files}/angebot.odt"
        self.client.put(source_url, data=b"initial source", headers=self.auth)
        target_etag = self.client.get(target_url, headers=self.auth).headers["ETag"]
        original_copy_replace = DocumentStore.replace_document_via_copy

        def change_source_before_locked_check(store, *args, **kwargs):
            (store.root / "racing-copy.txt").write_bytes(b"newer source")
            return original_copy_replace(store, *args, **kwargs)

        with mock.patch.object(DocumentStore, "replace_document_via_copy", change_source_before_locked_check):
            source_race = self.client.open(
                source_url, method="COPY",
                headers={
                    **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                    "If": f"<http://localhost{target_url}> ([{target_etag}])",
                },
            )

        self.assertEqual(412, source_race.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(b"newer source", (self.store.root / "racing-copy.txt").read_bytes())

        source = self.store.get_document("racing-copy.txt")
        source["sha256"] = hashlib.sha256(b"newer source").hexdigest()
        source["content_sha256"] = source["sha256"]
        self.store._save_document(source)
        source_etag = self.client.get(source_url, headers=self.auth).headers["ETag"]

        def change_destination_before_locked_check(store, *args, **kwargs):
            (store.root / "angebot.odt").write_bytes(b"newer target")
            return original_copy_replace(store, *args, **kwargs)

        with mock.patch.object(DocumentStore, "replace_document_via_copy", change_destination_before_locked_check):
            destination_race = self.client.open(
                source_url, method="COPY",
                headers={
                    **self.auth, "If-Match": source_etag, "Overwrite": "T",
                    "Destination": f"http://localhost{target_url}",
                    "If": f"<http://localhost{target_url}> ([{target_etag}])",
                },
            )

        self.assertEqual(412, destination_race.status_code)
        self.assertEqual(b"newer target", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(b"newer source", (self.store.root / "racing-copy.txt").read_bytes())

    def test_copy_overwrite_respects_target_retention_and_needs_no_visible_quota_growth(self):
        source_url = f"{self.files}/quota-copy.txt"
        target_url = f"{self.files}/angebot.odt"
        self.client.put(source_url, data=b"replacement", headers=self.auth)
        target_etag = self.client.get(target_url, headers=self.auth).headers["ETag"]
        metadata = self.store.get_document("angebot.odt")
        metadata["cleanup_state"] = "staged"
        self.store._save_document(metadata)

        blocked = self.client.open(
            source_url, method="COPY",
            headers={
                **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                "If": f"<http://localhost{target_url}> ([{target_etag}])",
            },
        )
        self.assertEqual(423, blocked.status_code)
        self.assertEqual(b"first office version", (self.store.root / "angebot.odt").read_bytes())

        metadata.pop("cleanup_state")
        self.store._save_document(metadata)
        visible_bytes = sum(
            path.stat().st_size for path in self.store.root.rglob("*")
            if path.is_file() and CONTROL_DIR not in path.parts and ".history" not in path.parts
        )
        app.config["WEBDAV_QUOTA_BYTES"] = visible_bytes
        copied = self.client.open(
            source_url, method="COPY",
            headers={
                **self.auth, "Overwrite": "T", "Destination": f"http://localhost{target_url}",
                "If": f"<http://localhost{target_url}> ([{target_etag}])",
            },
        )

        self.assertEqual(204, copied.status_code)
        self.assertEqual(b"replacement", (self.store.root / "angebot.odt").read_bytes())
        self.assertEqual(b"replacement", (self.store.root / "quota-copy.txt").read_bytes())

    def test_rfc6578_initial_and_incremental_sync_reports_changes_and_tombstones(self):
        initial_body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>infinite</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        properties = self.client.open(
            self.files, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:"><d:prop><d:sync-token/><d:supported-report-set/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )
        initial = self.client.open(self.files, method="REPORT", data=initial_body, headers={**self.auth, "Depth": "0"})
        initial_xml = ElementTree.fromstring(initial.data)
        token = initial_xml.findtext("{DAV:}sync-token")

        current = self.client.get(f"{self.files}/angebot.odt", headers=self.auth)
        updated = self.client.put(
            f"{self.files}/angebot.odt", data=b"changed for sync",
            headers={**self.auth, "If-Match": current.headers["ETag"]},
        )
        self.client.open(f"{self.files}/Projekte", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Projekte/Plan.odt", data=b"plan", headers=self.auth)
        incremental_body = initial_body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>")
        changed = self.client.open(self.files, method="REPORT", data=incremental_body, headers={**self.auth, "Depth": "0"})
        changed_xml = ElementTree.fromstring(changed.data)
        changed_token = changed_xml.findtext("{DAV:}sync-token")

        self.client.delete(f"{self.files}/Projekte/Plan.odt", headers=self.auth)
        removed_body = initial_body.replace("<d:sync-token/>", f"<d:sync-token>{changed_token}</d:sync-token>")
        removed = self.client.open(self.files, method="REPORT", data=removed_body, headers={**self.auth, "Depth": "0"})
        removed_text = removed.get_data(as_text=True)
        removed_token = ElementTree.fromstring(removed.data).findtext("{DAV:}sync-token")
        quiet_body = initial_body.replace("<d:sync-token/>", f"<d:sync-token>{removed_token}</d:sync-token>")
        quiet = self.client.open(self.files, method="REPORT", data=quiet_body, headers={**self.auth, "Depth": "0"})

        self.assertEqual(207, initial.status_code)
        self.assertIn("angebot.odt", initial.get_data(as_text=True))
        self.assertIn("supported-report-set", properties.get_data(as_text=True))
        self.assertIn("sync-token", properties.get_data(as_text=True))
        self.assertEqual(204, updated.status_code)
        self.assertEqual(207, changed.status_code)
        self.assertNotEqual(token, changed_token)
        self.assertIn("angebot.odt", changed.get_data(as_text=True))
        self.assertIn("Projekte/Plan.odt", changed.get_data(as_text=True))
        self.assertEqual(207, removed.status_code)
        self.assertIn("Plan.odt", removed_text)
        self.assertIn("404 Not Found", removed_text)
        self.assertEqual([], ElementTree.fromstring(quiet.data).findall("{DAV:}response"))

    def test_sync_level_scope_tokens_and_user_isolation(self):
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")
        self.client.open(f"{self.files}/Unterordner", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Unterordner/tief.txt", data=b"nested", headers=self.auth)
        with_token = body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>")
        shallow = self.client.open(self.files, method="REPORT", data=with_token, headers=self.auth)
        deep = self.client.open(self.files, method="REPORT", data=with_token.replace(">1<", ">infinite<"), headers=self.auth)

        self.client.get("/auth/logout")
        self.client.post("/auth/register", data={"username": "other", "password": "other-browser-password"})
        with app.test_request_context():
            other_password = activate("other", "other", label="Other sync", expires_days=30)
        other_auth = {"Authorization": "Basic " + base64.b64encode(f"other:{other_password}".encode()).decode()}
        foreign = self.client.open("/webdav/files/other", method="REPORT", data=with_token, headers=other_auth)

        self.assertIn("Unterordner/", shallow.get_data(as_text=True))
        self.assertNotIn("tief.txt", shallow.get_data(as_text=True))
        self.assertIn("tief.txt", deep.get_data(as_text=True))
        self.assertEqual(403, foreign.status_code)
        self.assertIn("valid-sync-token", foreign.get_data(as_text=True))

    def test_sync_report_rejects_bad_depth_shape_limit_and_file_target(self):
        valid = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        bad_depth = self.client.open(self.files, method="REPORT", data=valid, headers={**self.auth, "Depth": "1"})
        bad_shape = self.client.open(self.files, method="REPORT", data='<d:sync-collection xmlns:d="DAV:"/>', headers=self.auth)
        limited = self.client.open(self.files, method="REPORT", data=valid.replace("<d:prop>", "<d:limit><d:nresults>1</d:nresults></d:limit><d:prop>"), headers=self.auth)
        file_target = self.client.open(f"{self.files}/angebot.odt", method="REPORT", data=valid, headers=self.auth)

        self.assertEqual([400, 400, 507, 400], [bad_depth.status_code, bad_shape.status_code, limited.status_code, file_target.status_code])
        self.assertIn("number-of-matches-within-limits", limited.get_data(as_text=True))

    def test_sync_reports_remove_then_remap_as_changed_not_deleted(self):
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")
        self.client.delete(f"{self.files}/angebot.odt", headers=self.auth)
        recreated = self.client.put(f"{self.files}/angebot.odt", data=b"recreated", headers={**self.auth, "If-None-Match": "*"})
        report = self.client.open(
            self.files, method="REPORT",
            data=body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>"),
            headers=self.auth,
        )
        text = report.get_data(as_text=True)

        self.assertEqual(201, recreated.status_code)
        self.assertEqual(207, report.status_code)
        self.assertIn("angebot.odt", text)
        self.assertNotIn("404 Not Found", text)

    def test_collection_sync_token_can_guard_writes_against_tree_changes(self):
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        stale_token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")
        self.client.put(f"{self.files}/parallel.txt", data=b"another change", headers=self.auth)
        current = self.client.get(f"{self.files}/angebot.odt", headers=self.auth)
        tagged_if = f"<{self.files}/> (<{stale_token}>)"
        rejected = self.client.put(
            f"{self.files}/angebot.odt", data=b"must not win",
            headers={**self.auth, "If": tagged_if, "If-Match": current.headers["ETag"]},
        )
        after_rejected = (self.store.root / "angebot.odt").read_bytes()
        refreshed = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        fresh_token = ElementTree.fromstring(refreshed.data).findtext("{DAV:}sync-token")
        accepted = self.client.put(
            f"{self.files}/angebot.odt", data=b"guarded write",
            headers={**self.auth, "If": f"<{self.files}/> (<{fresh_token}>)", "If-Match": current.headers["ETag"]},
        )

        self.assertEqual(412, rejected.status_code)
        self.assertEqual(b"first office version", after_rejected)
        self.assertEqual(204, accepted.status_code)
        self.assertEqual(b"guarded write", (self.store.root / "angebot.odt").read_bytes())

    def test_collection_copy_recurses_preserves_properties_and_creates_independent_documents(self):
        source = f"{self.files}/Projekte"
        self.client.open(source, method="MKCOL", headers=self.auth)
        self.client.open(f"{source}/Texte", method="MKCOL", headers=self.auth)
        self.client.put(f"{source}/Plan.odt", data=b"plan", headers=self.auth)
        self.client.put(f"{source}/Texte/Notiz.txt", data=b"note", headers=self.auth)
        property_body = '<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:set><d:prop><m:label>Projekt A</m:label></d:prop></d:set></d:propertyupdate>'
        self.client.open(source, method="PROPPATCH", data=property_body, headers=self.auth)
        source_document = self.store.get_document("Projekte/Plan.odt")
        source_document["tags"] = ["planung"]
        source_document["grants"] = [{"username": "other", "role": "editor"}]
        self.store._save_document(source_document)

        copied = self.client.open(
            source, method="COPY",
            headers={
                **self.auth, "Depth": "infinity", "Overwrite": "F",
                "Destination": f"http://localhost{self.files}/Projekte-Kopie",
            },
        )
        copied_document = self.store.get_document("Projekte-Kopie/Plan.odt")
        property_query = '<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:label/></d:prop></d:propfind>'
        copied_properties = self.client.open(
            f"{self.files}/Projekte-Kopie", method="PROPFIND", data=property_query,
            headers={**self.auth, "Depth": "0"},
        )

        self.assertEqual(201, copied.status_code)
        self.assertEqual(b"plan", (self.store.root / "Projekte-Kopie" / "Plan.odt").read_bytes())
        self.assertEqual(b"note", (self.store.root / "Projekte-Kopie" / "Texte" / "Notiz.txt").read_bytes())
        self.assertNotEqual(source_document["document_id"], copied_document["document_id"])
        self.assertEqual(["planung"], copied_document["tags"])
        self.assertEqual([], copied_document.get("grants", []))
        self.assertEqual("Projekt A", ElementTree.fromstring(copied_properties.data).findtext(".//{urn:simpleoffice:test}label"))
        self.assertIn("Projekte-Kopie/", copied.headers["Location"])
        actions = {row.get("type") for row in self.store.logbook()}
        self.assertIn("webdav_collection_copied", actions)

    def test_collection_copy_depth_zero_copies_only_collection_and_dead_properties(self):
        source = f"{self.files}/Vorlage"
        self.client.open(source, method="MKCOL", headers=self.auth)
        self.client.put(f"{source}/Inhalt.txt", data=b"not copied", headers=self.auth)
        property_body = '<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:set><d:prop><m:kind>Vorlage</m:kind></d:prop></d:set></d:propertyupdate>'
        self.client.open(source, method="PROPPATCH", data=property_body, headers=self.auth)

        copied = self.client.open(
            source, method="COPY",
            headers={**self.auth, "Depth": "0", "Destination": f"http://localhost{self.files}/Leere-Vorlage"},
        )
        query = '<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:kind/></d:prop></d:propfind>'
        properties = self.client.open(
            f"{self.files}/Leere-Vorlage", method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )

        self.assertEqual(201, copied.status_code)
        self.assertTrue((self.store.root / "Leere-Vorlage").is_dir())
        self.assertFalse((self.store.root / "Leere-Vorlage" / "Inhalt.txt").exists())
        self.assertEqual("Vorlage", ElementTree.fromstring(properties.data).findtext(".//{urn:simpleoffice:test}kind"))

    def test_collection_copy_does_not_require_or_duplicate_source_lock(self):
        source = f"{self.files}/Gesperrte-Quelle"
        self.client.open(source, method="MKCOL", headers=self.auth)
        self.client.put(f"{source}/Lesbar.txt", data=b"copy me", headers=self.auth)
        locked = self.client.open(
            source, method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "infinity"},
        )

        copied = self.client.open(
            source, method="COPY",
            headers={**self.auth, "Destination": f"http://localhost{self.files}/Ungesperrte-Kopie"},
        )
        destination_properties = self.client.open(
            f"{self.files}/Ungesperrte-Kopie", method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:"><d:prop><d:lockdiscovery/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )

        self.assertEqual(200, locked.status_code)
        self.assertEqual(201, copied.status_code)
        self.assertNotIn("activelock", destination_properties.get_data(as_text=True))
        self.assertEqual(b"copy me", (self.store.root / "Ungesperrte-Kopie" / "Lesbar.txt").read_bytes())

    def test_collection_move_is_recursive_keeps_ids_and_releases_source_lock(self):
        source = f"{self.files}/Team"
        self.client.open(source, method="MKCOL", headers=self.auth)
        self.client.open(f"{source}/Unterordner", method="MKCOL", headers=self.auth)
        self.client.put(f"{source}/Unterordner/Plan.txt", data=b"v1", headers=self.auth)
        original = self.store.get_document("Team/Unterordner/Plan.txt")
        locked = self.client.open(
            source, method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "infinity", "Timeout": "Second-600"},
        )
        token = locked.headers["Lock-Token"].strip("<>")

        moved = self.client.open(
            source, method="MOVE",
            headers={
                **self.auth, "Depth": "infinity",
                "Destination": f"http://localhost{self.files}/Archiv-Team",
                "If": f"<http://localhost{source}> (<{token}>)",
            },
        )
        moved_document = self.store.get_document("Archiv-Team/Unterordner/Plan.txt")
        current = self.client.get(f"{self.files}/Archiv-Team/Unterordner/Plan.txt", headers=self.auth)
        saved = self.client.put(
            f"{self.files}/Archiv-Team/Unterordner/Plan.txt", data=b"v2",
            headers={**self.auth, "If-Match": current.headers["ETag"]},
        )

        self.assertEqual(201, moved.status_code)
        self.assertFalse((self.store.root / "Team").exists())
        self.assertEqual(original["document_id"], moved_document["document_id"])
        self.assertEqual("Archiv-Team/Unterordner/Plan.txt", moved_document["last_path"])
        self.assertEqual(204, saved.status_code)
        self.assertEqual(b"v2", (self.store.root / "Archiv-Team" / "Unterordner" / "Plan.txt").read_bytes())
        actions = [json.loads(path.read_text()).get("action") for path in (self.store.history.root / "events").glob("*.json")]
        self.assertIn("webdav_lock_released_by_move", actions)

    def test_collection_operations_reject_cycles_invalid_depth_quota_and_unsafe_members(self):
        source = f"{self.files}/Quelle"
        self.client.open(source, method="MKCOL", headers=self.auth)
        self.client.put(f"{source}/Gross.bin", data=b"123456", headers=self.auth)
        cycle = self.client.open(
            source, method="COPY",
            headers={**self.auth, "Destination": f"http://localhost{source}/Kind"},
        )
        invalid_depth = self.client.open(
            source, method="COPY",
            headers={**self.auth, "Depth": "1", "Destination": f"http://localhost{self.files}/Invalid"},
        )
        app.config["WEBDAV_QUOTA_BYTES"] = 10
        quota = self.client.open(
            source, method="COPY",
            headers={**self.auth, "Destination": f"http://localhost{self.files}/Zu-Gross"},
        )
        app.config["WEBDAV_QUOTA_BYTES"] = 0
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_bytes(b"outside")
        (self.store.root / "Quelle" / "Verweis").symlink_to(outside)
        unsafe = self.client.open(
            source, method="MOVE",
            headers={**self.auth, "Destination": f"http://localhost{self.files}/Unsicher"},
        )

        self.assertEqual(403, cycle.status_code)
        self.assertEqual(400, invalid_depth.status_code)
        self.assertEqual(507, quota.status_code)
        self.assertIn("quota-not-exceeded", quota.get_data(as_text=True))
        self.assertEqual(409, unsafe.status_code)
        self.assertTrue((self.store.root / "Quelle" / "Gross.bin").is_file())
        self.assertFalse((self.store.root / "Unsicher").exists())

    def test_collection_copy_rolls_back_visible_destination_after_member_failure(self):
        source = f"{self.files}/Rollback"
        self.client.open(source, method="MKCOL", headers=self.auth)
        self.client.put(f"{source}/A.txt", data=b"a", headers=self.auth)
        self.client.put(f"{source}/B.txt", data=b"b", headers=self.auth)
        original_copy = DocumentStore.copy_document
        calls = 0

        def fail_second(store, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated storage failure")
            return original_copy(store, *args, **kwargs)

        with mock.patch.object(DocumentStore, "copy_document", fail_second):
            response = self.client.open(
                source, method="COPY",
                headers={**self.auth, "Destination": f"http://localhost{self.files}/Rollback-Kopie"},
            )

        self.assertEqual(507, response.status_code)
        self.assertFalse((self.store.root / "Rollback-Kopie").exists())
        rolled_back = [row for row in self.store.logbook() if row.get("type") == "document_soft_deleted"]
        self.assertTrue(rolled_back)

    def test_portable_unicode_name_round_trips_through_propfind_and_get(self):
        name = "Käse 📄.odt"
        url = f"{self.files}/{quote(name, safe='')}"

        created = self.client.put(url, data=b"portable office document", headers=self.auth)
        listing = self.client.open(self.files, method="PROPFIND", headers={**self.auth, "Depth": "1"})
        fetched = self.client.get(url, headers=self.auth)

        self.assertEqual(201, created.status_code)
        self.assertEqual(207, listing.status_code)
        self.assertIn(quote(name, safe=""), listing.get_data(as_text=True))
        self.assertEqual(b"portable office document", fetched.data)
        self.assertTrue((self.store.root / name).is_file())

    def test_new_names_reject_non_nfc_reserved_invisible_and_oversized_segments(self):
        cases = {
            "Cafe\u0301.txt": "unicode-nfc-required",
            "CON.txt": "windows-reserved-device-name",
            "COM¹.log": "windows-reserved-device-name",
            "bad:name.txt": "windows-reserved-character",
            "trailing.": "leading-or-trailing-space-or-dot",
            " leading.txt": "leading-or-trailing-space-or-dot",
            "report\u202Ecod.exe": "bidirectional-control-character",
            "private\uE000.txt": "non-interchange-character",
            f"{'a' * 201}.txt": "name-too-long",
        }

        for name, reason in cases.items():
            with self.subTest(name=repr(name)):
                response = self.client.put(
                    f"{self.files}/{quote(name, safe='')}", data=b"must not appear", headers=self.auth,
                )
                self.assertEqual(409, response.status_code)
                self.assertEqual(reason, response.headers["X-SimpleOffice-Name-Reason"])
                self.assertIn("portable-file-name", response.get_data(as_text=True))
                self.assertFalse((self.store.root / name).exists())

        rejections = [
            row for row in self.store.logbook()
            if row.get("action") == "webdav_portable_name_rejected"
        ]
        self.assertEqual(len(cases), len(rejections))
        snapshots = list((self.store.history.root / "snapshots" / "webdav-name-policy").glob("*.json"))
        self.assertEqual(len(cases), len(snapshots))
        serialized = "\n".join(path.read_text(encoding="utf-8") for path in snapshots)
        self.assertNotIn("CON.txt", serialized)
        self.assertNotIn("bad:name.txt", serialized)

    def test_case_and_normalization_collisions_are_blocked_for_put_mkcol_and_copy(self):
        source = f"{self.files}/Bericht.odt"
        self.assertEqual(201, self.client.put(source, data=b"source", headers=self.auth).status_code)
        self.assertEqual(201, self.client.open(f"{self.files}/Daten", method="MKCOL", headers=self.auth).status_code)

        put_collision = self.client.put(f"{self.files}/bericht.ODT", data=b"other", headers=self.auth)
        folder_collision = self.client.open(f"{self.files}/daten", method="MKCOL", headers=self.auth)
        copy_collision = self.client.open(
            source,
            method="COPY",
            headers={**self.auth, "Destination": f"http://localhost{self.files}/BERICHT.ODT"},
        )

        self.assertEqual([409, 409, 409], [put_collision.status_code, folder_collision.status_code, copy_collision.status_code])
        self.assertEqual(
            ["case-or-normalization-collision"] * 3,
            [put_collision.headers["X-SimpleOffice-Name-Reason"], folder_collision.headers["X-SimpleOffice-Name-Reason"], copy_collision.headers["X-SimpleOffice-Name-Reason"]],
        )
        self.assertEqual(b"source", (self.store.root / "Bericht.odt").read_bytes())
        self.assertFalse((self.store.root / "bericht.ODT").exists())
        self.assertFalse((self.store.root / "daten").exists())
        self.assertFalse((self.store.root / "BERICHT.ODT").exists())

    def test_legacy_non_nfc_resource_remains_editable_and_can_be_renamed_safely(self):
        legacy_name = "Cafe\u0301.txt"
        canonical_name = "Café.txt"
        legacy_path = self.store.root / legacy_name
        legacy_path.write_bytes(b"legacy")
        self.store.scan()
        before = self.store.get_document(legacy_name)
        legacy_url = f"{self.files}/{quote(legacy_name, safe='')}"
        canonical_url = f"{self.files}/{quote(canonical_name, safe='')}"

        current = self.client.get(legacy_url, headers=self.auth)
        updated = self.client.put(
            legacy_url, data=b"legacy updated",
            headers={**self.auth, "If-Match": current.headers["ETag"]},
        )
        collision = self.client.put(canonical_url, data=b"duplicate", headers=self.auth)
        moved = self.client.open(
            legacy_url,
            method="MOVE",
            headers={**self.auth, "Destination": f"http://localhost{canonical_url}"},
        )
        after = self.store.get_document(canonical_name)

        self.assertEqual(204, updated.status_code)
        self.assertEqual(409, collision.status_code)
        self.assertEqual("case-or-normalization-collision", collision.headers["X-SimpleOffice-Name-Reason"])
        self.assertEqual(201, moved.status_code)
        self.assertFalse(legacy_path.exists())
        self.assertEqual(b"legacy updated", (self.store.root / canonical_name).read_bytes())
        self.assertEqual(before["document_id"], after["document_id"])

    def test_case_only_move_is_allowed_but_copying_an_alias_is_not(self):
        source_url = f"{self.files}/Plan.txt"
        target_url = f"{self.files}/plan.txt"
        self.assertEqual(201, self.client.put(source_url, data=b"plan", headers=self.auth).status_code)
        before = self.store.get_document("Plan.txt")

        copied = self.client.open(
            source_url,
            method="COPY",
            headers={**self.auth, "Destination": f"http://localhost{target_url}"},
        )
        moved = self.client.open(
            source_url,
            method="MOVE",
            headers={**self.auth, "Destination": f"http://localhost{target_url}"},
        )
        after = self.store.get_document("plan.txt")

        self.assertEqual(409, copied.status_code)
        self.assertEqual(201, moved.status_code)
        self.assertFalse((self.store.root / "Plan.txt").exists())
        self.assertEqual(b"plan", (self.store.root / "plan.txt").read_bytes())
        self.assertEqual(before["document_id"], after["document_id"])

    def test_lock_null_and_read_only_requests_cannot_bypass_name_policy_or_rights(self):
        unsafe = f"{self.files}/NUL.txt"
        locked = self.client.open(
            unsafe, method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "0", "Timeout": "Second-600"},
        )
        self.assertEqual(409, locked.status_code)
        self.assertFalse((self.store.root / "NUL.txt").exists())

        with app.test_request_context():
            password = activate("jens", "jens", label="Read-only policy test", scope="read", expires_days=30)
        read_auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}
        before = len([row for row in self.store.logbook() if row.get("action") == "webdav_portable_name_rejected"])
        denied = self.client.put(f"{self.files}/AUX.txt", data=b"blocked by rights", headers=read_auth)
        after = len([row for row in self.store.logbook() if row.get("action") == "webdav_portable_name_rejected"])

        self.assertEqual(403, denied.status_code)
        self.assertEqual(before, after)
        self.assertFalse((self.store.root / "AUX.txt").exists())

    def test_depth_infinity_returns_a_bounded_flat_snapshot_for_sync_clients(self):
        project = f"{self.files}/Projekte"
        nested = f"{project}/2026"
        unicode_name = "Käse 📄.odt"
        self.assertEqual(201, self.client.open(project, method="MKCOL", headers=self.auth).status_code)
        self.assertEqual(201, self.client.open(nested, method="MKCOL", headers=self.auth).status_code)
        self.assertEqual(201, self.client.put(f"{project}/Plan.txt", data=b"plan", headers=self.auth).status_code)
        self.assertEqual(
            201,
            self.client.put(
                f"{nested}/{quote(unicode_name, safe='')}", data=b"office", headers=self.auth,
            ).status_code,
        )
        private = Path(self.temp.name) / "private"
        private.mkdir()
        (private / "Geheim.txt").write_bytes(b"must not be followed")
        (self.store.root / "Projekte" / "Verknuepfung").symlink_to(
            private, target_is_directory=True,
        )
        internal = self.store.root / "Projekte" / CONTROL_DIR
        internal.mkdir(exist_ok=True)
        (internal / "niemals-sichtbar.txt").write_bytes(b"private")
        query = (
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:displayname/><d:getetag/><d:sync-token/>'
            '</d:prop></d:propfind>'
        )

        recursive = self.client.open(
            self.files, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "infinity"},
        )
        implicit_recursive = self.client.open(project, method="PROPFIND", headers=self.auth)
        root = ElementTree.fromstring(recursive.data)
        hrefs = [node.text for node in root.findall("{DAV:}response/{DAV:}href")]

        self.assertEqual(207, recursive.status_code)
        self.assertEqual("private, no-store", recursive.headers["Cache-Control"])
        vary = {
            value.strip().casefold()
            for value in recursive.headers["Vary"].split(",")
        }
        self.assertTrue({"authorization", "depth"}.issubset(vary))
        self.assertIn("/webdav/files/jens/", hrefs)
        self.assertIn("/webdav/files/jens/Projekte/", hrefs)
        self.assertIn("/webdav/files/jens/Projekte/2026/", hrefs)
        self.assertIn("/webdav/files/jens/Projekte/Plan.txt", hrefs)
        self.assertIn(
            f"/webdav/files/jens/Projekte/2026/{quote(unicode_name, safe='')}", hrefs,
        )
        self.assertEqual(len(hrefs), len(set(hrefs)))
        self.assertNotIn(CONTROL_DIR, recursive.get_data(as_text=True))
        self.assertNotIn("niemals-sichtbar", recursive.get_data(as_text=True))
        self.assertNotIn("Geheim.txt", recursive.get_data(as_text=True))
        self.assertNotIn("Verknuepfung", recursive.get_data(as_text=True))
        self.assertEqual(207, implicit_recursive.status_code)
        self.assertIn(quote(unicode_name, safe=""), implicit_recursive.get_data(as_text=True))

    def test_recursive_propfind_respects_read_only_folder_scope(self):
        (self.store.root / "Projekte").mkdir()
        (self.store.root / "Projekte" / "Unterordner").mkdir()
        (self.store.root / "Privat").mkdir()
        self.store.create_document_at("Projekte/Plan.txt", b"plan", "jens")
        self.store.create_document_at("Projekte/Unterordner/Notiz.txt", b"note", "jens")
        self.store.create_document_at("Privat/Geheim.txt", b"secret", "jens")
        with app.test_request_context():
            password = activate(
                "jens", "jens", label="FreeFileSync Lesetest", scope="read",
                path_prefix="Projekte", expires_days=30,
            )
        auth = {
            "Authorization": "Basic "
            + base64.b64encode(f"jens:{password}".encode()).decode()
        }

        listing = self.client.open(
            f"{self.files}/Projekte", method="PROPFIND",
            headers={**auth, "Depth": "infinity"},
        )
        outside = self.client.open(
            self.files, method="PROPFIND", headers={**auth, "Depth": "infinity"},
        )
        write = self.client.put(f"{self.files}/Projekte/Neu.txt", data=b"blocked", headers=auth)

        body = listing.get_data(as_text=True)
        self.assertEqual(207, listing.status_code)
        self.assertIn("Plan.txt", body)
        self.assertIn("Unterordner/Notiz.txt", body)
        self.assertNotIn("Geheim.txt", body)
        self.assertEqual(404, outside.status_code)
        self.assertEqual(403, write.status_code)
        self.assertFalse((self.store.root / "Projekte" / "Neu.txt").exists())

    def test_recursive_propfind_rejects_member_and_depth_exhaustion_without_partial_result(self):
        (self.store.root / "A").mkdir()
        (self.store.root / "A" / "B").mkdir()
        (self.store.root / "A" / "B" / "C").mkdir()
        self.store.create_document_at("A/Datei.txt", b"content", "jens")

        with mock.patch("app.webdav.MAX_WEBDAV_COLLECTION_MEMBERS", 1):
            member_limit = self.client.open(
                self.files, method="PROPFIND", headers={**self.auth, "Depth": "infinity"},
            )
        with mock.patch("app.webdav.MAX_WEBDAV_COLLECTION_DEPTH", 1):
            depth_limit = self.client.open(
                f"{self.files}/A", method="PROPFIND",
                headers={**self.auth, "Depth": "infinity"},
            )

        self.assertEqual([507, 507], [member_limit.status_code, depth_limit.status_code])
        self.assertEqual("member-count", member_limit.headers["X-SimpleOffice-Propfind-Limit"])
        self.assertEqual("nesting-depth", depth_limit.headers["X-SimpleOffice-Propfind-Limit"])
        self.assertIn("propfind-resource-limit", member_limit.get_data(as_text=True))
        self.assertNotIn("Datei.txt", member_limit.get_data(as_text=True))
        self.assertNotIn("Datei.txt", depth_limit.get_data(as_text=True))
        audits = [
            row for row in self.store.logbook()
            if row.get("action") == "webdav_propfind_limit_rejected"
        ]
        self.assertEqual(2, len(audits))
        snapshots = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (
                self.store.history.root / "snapshots" / "webdav-propfind"
            ).glob("*.json")
        ]
        self.assertEqual(
            {"member-count", "nesting-depth"},
            {snapshot.get("reason") for snapshot in snapshots},
        )

    def test_propfind_response_size_is_limited_and_rejection_is_audited(self):
        with mock.patch("app.webdav.MAX_PROPFIND_RESPONSE_BYTES", 128):
            response = self.client.open(
                self.files, method="PROPFIND", headers={**self.auth, "Depth": "0"},
            )

        self.assertEqual(507, response.status_code)
        self.assertEqual("response-bytes", response.headers["X-SimpleOffice-Propfind-Limit"])
        self.assertNotIn("multistatus", response.get_data(as_text=True))
        snapshots = list(
            (self.store.history.root / "snapshots" / "webdav-propfind").glob("*.json")
        )
        self.assertTrue(snapshots)
        snapshot = json.loads(snapshots[-1].read_text(encoding="utf-8"))
        self.assertEqual("response-bytes", snapshot["reason"])
        self.assertGreater(snapshot["observed"], snapshot["limit"])

    def test_recursive_propfind_uses_the_webdav_mutation_lock(self):
        with mock.patch("app.webdav.exclusive_file_lock") as locking:
            response = self.client.open(
                self.files, method="PROPFIND",
                headers={**self.auth, "Depth": "infinity"},
            )

        self.assertEqual(207, response.status_code)
        lock_paths = [str(call.args[0]) for call in locking.call_args_list if call.args]
        self.assertTrue(any(path.endswith("webdav-sync.mutation.lock") for path in lock_paths))

    def test_rfc_creationdate_and_getlastmodified_cover_files_and_collections(self):
        folder_url = f"{self.files}/Zeitdaten"
        file_url = f"{folder_url}/Bericht.odt"
        self.assertEqual(201, self.client.open(folder_url, method="MKCOL", headers=self.auth).status_code)
        self.assertEqual(201, self.client.put(file_url, data=b"report", headers=self.auth).status_code)
        query = (
            '<d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:creationdate/><d:getlastmodified/>'
            '</d:prop></d:propfind>'
        )

        folder = self.client.open(
            folder_url, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        before = self.client.open(
            file_url, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        folder_root = ElementTree.fromstring(folder.data)
        before_root = ElementTree.fromstring(before.data)
        folder_created = folder_root.findtext(".//{DAV:}creationdate")
        file_created = before_root.findtext(".//{DAV:}creationdate")
        modified_before = parsedate_to_datetime(
            before_root.findtext(".//{DAV:}getlastmodified") or "",
        )

        policy = json.loads((self.store.root / "Zeitdaten" / POLICY_FILE).read_text())
        document = self.store.get_document("Zeitdaten/Bericht.odt")
        self.assertEqual("webdav:jens", policy["created_by"])
        self.assertEqual(
            datetime.fromisoformat(policy["created_at"]).astimezone(timezone.utc),
            datetime.fromisoformat((folder_created or "").replace("Z", "+00:00")),
        )
        self.assertEqual(
            datetime.fromisoformat(document["first_seen_at"]).astimezone(timezone.utc),
            datetime.fromisoformat((file_created or "").replace("Z", "+00:00")),
        )

        path = self.store.root / "Zeitdaten" / "Bericht.odt"
        future = path.stat().st_mtime + 5
        path.touch()
        os.utime(path, (future, future))
        after = self.client.open(
            file_url, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        after_root = ElementTree.fromstring(after.data)
        modified_after = parsedate_to_datetime(
            after_root.findtext(".//{DAV:}getlastmodified") or "",
        )

        self.assertEqual([207, 207, 207], [folder.status_code, before.status_code, after.status_code])
        self.assertEqual(file_created, after_root.findtext(".//{DAV:}creationdate"))
        self.assertGreater(modified_after, modified_before)

    def test_creationdate_is_protected_preserved_by_move_and_reset_by_copy(self):
        source_url = f"{self.files}/Original.odt"
        moved_url = f"{self.files}/Verschoben.odt"
        copied_url = f"{self.files}/Kopie.odt"
        self.assertEqual(201, self.client.put(source_url, data=b"same body", headers=self.auth).status_code)
        query = '<d:propfind xmlns:d="DAV:"><d:prop><d:creationdate/></d:prop></d:propfind>'

        original = self.client.open(
            source_url, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        copied = self.client.open(
            source_url, method="COPY",
            headers={**self.auth, "Destination": f"http://localhost{copied_url}"},
        )
        moved = self.client.open(
            source_url, method="MOVE",
            headers={**self.auth, "Destination": f"http://localhost{moved_url}"},
        )
        moved_props = self.client.open(
            moved_url, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        copied_props = self.client.open(
            copied_url, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        original_created = ElementTree.fromstring(original.data).findtext(".//{DAV:}creationdate")
        moved_created = ElementTree.fromstring(moved_props.data).findtext(".//{DAV:}creationdate")
        copied_created = ElementTree.fromstring(copied_props.data).findtext(".//{DAV:}creationdate")

        protected = self.client.open(
            moved_url, method="PROPPATCH",
            data=(
                '<d:propertyupdate xmlns:d="DAV:"><d:set><d:prop>'
                '<d:creationdate>2000-01-01T00:00:00Z</d:creationdate>'
                '<d:getlastmodified>Sat, 01 Jan 2000 00:00:00 GMT</d:getlastmodified>'
                '</d:prop></d:set></d:propertyupdate>'
            ),
            headers=self.auth,
        )

        self.assertEqual([201, 201, 207, 207], [copied.status_code, moved.status_code, moved_props.status_code, copied_props.status_code])
        self.assertEqual(original_created, moved_created)
        self.assertNotEqual(original_created, copied_created)
        protected_body = protected.get_data(as_text=True)
        self.assertEqual(207, protected.status_code)
        self.assertEqual(1, protected_body.count("403 Forbidden"))
        self.assertIn("creationdate", protected_body)
        self.assertIn("getlastmodified", protected_body)
        self.assertIn("cannot-modify-protected-property", protected_body)

    def test_windows_webdav_properties_round_trip_copy_and_fail_atomically(self):
        source_url = f"{self.files}/Windows.odt"
        copy_url = f"{self.files}/Windows-Kopie.odt"
        self.assertEqual(201, self.client.put(source_url, data=b"windows", headers=self.auth).status_code)
        update = '''
        <d:propertyupdate xmlns:d="DAV:" xmlns:Z="urn:schemas-microsoft-com:" xmlns:Office="urn:schemas-microsoft-com:office:office">
          <d:set><d:prop>
            <Z:Win32FileAttributes>00000020</Z:Win32FileAttributes>
            <Z:Win32CreationTime>2026-08-11T07:00:00Z</Z:Win32CreationTime>
            <Z:Win32LastAccessTime>2026-08-11T07:05:00Z</Z:Win32LastAccessTime>
            <Z:Win32LastModifiedTime>2026-08-11T07:10:00Z</Z:Win32LastModifiedTime>
            <Office:specialFolderType>42</Office:specialFolderType>
          </d:prop></d:set>
        </d:propertyupdate>
        '''
        saved = self.client.open(source_url, method="PROPPATCH", data=update, headers=self.auth)
        copied = self.client.open(
            source_url, method="COPY",
            headers={**self.auth, "Destination": f"http://localhost{copy_url}"},
        )
        query = '''
        <d:propfind xmlns:d="DAV:" xmlns:Z="urn:schemas-microsoft-com:" xmlns:Office="urn:schemas-microsoft-com:office:office">
          <d:prop><Z:Win32FileAttributes/><Z:Win32CreationTime/><Z:Win32LastAccessTime/><Z:Win32LastModifiedTime/><Office:specialFolderType/><d:iscollection/><d:isFolder/><d:ishidden/></d:prop>
        </d:propfind>
        '''
        roundtrip = self.client.open(
            copy_url, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        root = ElementTree.fromstring(roundtrip.data)

        invalid = self.client.open(
            copy_url, method="PROPPATCH",
            data='''<d:propertyupdate xmlns:d="DAV:" xmlns:Office="urn:schemas-microsoft-com:office:office" xmlns:m="urn:simpleoffice:test"><d:set><d:prop><Office:specialFolderType>not-an-integer</Office:specialFolderType><m:must-not-stick>rollback</m:must-not-stick></d:prop></d:set></d:propertyupdate>''',
            headers=self.auth,
        )
        after_invalid = self.client.open(
            copy_url, method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:" xmlns:m="urn:simpleoffice:test"><d:prop><m:must-not-stick/></d:prop></d:propfind>',
            headers={**self.auth, "Depth": "0"},
        )
        with app.test_request_context():
            password = activate(
                "jens", "jens", label="Windows read only", scope="read", expires_days=30,
            )
        read_auth = {
            "Authorization": "Basic "
            + base64.b64encode(f"jens:{password}".encode()).decode()
        }
        denied = self.client.open(copy_url, method="PROPPATCH", data=update, headers=read_auth)

        self.assertEqual([207, 201, 207], [saved.status_code, copied.status_code, roundtrip.status_code])
        self.assertEqual("00000020", root.findtext(".//{urn:schemas-microsoft-com:}Win32FileAttributes"))
        self.assertEqual("2026-08-11T07:10:00Z", root.findtext(".//{urn:schemas-microsoft-com:}Win32LastModifiedTime"))
        self.assertEqual("42", root.findtext(".//{urn:schemas-microsoft-com:office:office}specialFolderType"))
        self.assertEqual("0", root.findtext(".//{DAV:}iscollection"))
        self.assertEqual("f", root.findtext(".//{DAV:}isFolder"))
        self.assertEqual("0", root.findtext(".//{DAV:}ishidden"))
        invalid_body = invalid.get_data(as_text=True)
        self.assertIn("409 Conflict", invalid_body)
        self.assertIn("424 Failed Dependency", invalid_body)
        self.assertIn("404 Not Found", after_invalid.get_data(as_text=True))
        self.assertEqual(403, denied.status_code)
        snapshots = list((self.store.history.root / "snapshots" / "webdav-properties").glob("*.json"))
        serialized = "\n".join(path.read_text(encoding="utf-8") for path in snapshots)
        self.assertIn("Win32LastModifiedTime", serialized)
        self.assertNotIn("2026-08-11T07:10:00Z", serialized)


if __name__ == "__main__":
    unittest.main()
