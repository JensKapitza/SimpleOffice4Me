import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from app import app
from app.db import ensure_auth_database
from app.document_store import CONTROL_DIR, DocumentStore
from app.webdav import MAX_ACTIVE_CREDENTIALS, activate, revoke


class WebDavDocumentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "MAX_CONTENT_LENGTH", "WEBDAV_QUOTA_BYTES")}
        root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(root), MAX_CONTENT_LENGTH=1024 * 1024, WEBDAV_QUOTA_BYTES=0)
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

    def test_lock_rejects_unsupported_scope_depth_and_recursive_collection(self):
        shared = self.lock_body.replace("exclusive", "shared")
        wrong_scope = self.client.open(f"{self.files}/angebot.odt", method="LOCK", data=shared, headers=self.auth)
        wrong_depth = self.client.open(
            f"{self.files}/angebot.odt", method="LOCK", data=self.lock_body,
            headers={**self.auth, "Depth": "1"},
        )
        recursive = self.client.open(self.files, method="LOCK", data=self.lock_body, headers=self.auth)

        self.assertEqual([400, 400, 501], [wrong_scope.status_code, wrong_depth.status_code, recursive.status_code])
        self.assertFalse((self.store.control / "webdav-locks.json").exists())

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

    def test_collections_are_non_recursive_and_reserved_paths_are_hidden(self):
        self.client.open(f"{self.files}/Ordner", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Ordner/datei.txt", data=b"content", headers=self.auth)
        non_empty = self.client.delete(f"{self.files}/Ordner", headers=self.auth)
        missing_parent = self.client.open(f"{self.files}/fehlt/Kind", method="MKCOL", headers=self.auth)
        reserved = self.client.open(f"{self.files}/.simpleoffice-meta", method="PROPFIND", headers={**self.auth, "Depth": "0"})

        self.assertEqual(409, non_empty.status_code)
        self.assertEqual(409, missing_parent.status_code)
        self.assertEqual(404, reserved.status_code)

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


if __name__ == "__main__":
    unittest.main()
