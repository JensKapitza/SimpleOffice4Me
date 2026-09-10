"""WebDAV tests part 1 of 5."""
from __future__ import annotations

if __package__:
    from .webdav_test_base import *
else:
    from webdav_test_base import *


class WebDavDocumentTestPart1(WebDavTestBase):
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

    def test_user_settings_create_one_whole_tree_credential(self):
        settings = self.client.get("/documents/settings").get_data(as_text=True)
        self.assertIn("/settings/webdav", settings)
        response = self.client.post("/settings/webdav", data={
            "action": "activate", "label": "Mein Dateisystem",
            "scope": "write", "expires_days": "365",
            "path_prefix": "darf-nicht-übernommen-werden",
        })
        body = response.get_data(as_text=True)
        payload = json.loads((self.store.control / "webdav-credentials.json").read_text())
        record = next(item for item in payload["users"]["jens"]["credentials"] if item["label"] == "Mein Dateisystem")
        self.assertEqual(200, response.status_code)
        self.assertIn("App-Passwort jetzt kopieren", body)
        self.assertIn("Alle Dokumente", body)
        self.assertEqual("", record["path_prefix"])
        self.assertNotIn(record["hash"], body)
        self.assertNotIn(record["salt"], body)
        password = body.split('id="app-password"', 1)[1].split('value="', 1)[1].split('"', 1)[0]
        auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}
        self.assertEqual(207, self.client.open(self.files, method="PROPFIND", headers={**auth, "Depth": "1"}).status_code)

    def test_user_can_add_and_revoke_own_sshfs_public_key(self):
        key_type = b"ssh-ed25519"
        blob = len(key_type).to_bytes(4, "big") + key_type + b"browser-test-public-key"
        public_key = f"ssh-ed25519 {base64.b64encode(blob).decode()} laptop-comment"
        created = self.client.post("/settings/webdav", data={
            "action": "add_ssh_key", "public_key": public_key,
            "ssh_key_label": "Laptop SSHFS", "ssh_key_scope": "read",
            "ssh_key_expires_days": "90",
        })
        body = created.get_data(as_text=True)
        payload = json.loads((self.store.control / "ssh-authorized-keys.json").read_text())
        record = payload["users"]["jens"][0]
        self.assertEqual(200, created.status_code)
        self.assertIn("Laptop SSHFS", body)
        self.assertIn(record["fingerprint"], body)
        self.assertNotIn("laptop-comment", json.dumps(payload))

        revoked = self.client.post("/settings/webdav", data={
            "action": "revoke_ssh_key", "ssh_key_id": record["key_id"],
        }, follow_redirects=True)
        self.assertIn("SSH-Schlüssel widerrufen", revoked.get_data(as_text=True))
        payload = json.loads((self.store.control / "ssh-authorized-keys.json").read_text())
        self.assertEqual([], payload["users"]["jens"])

    def test_folder_acl_filters_listing_and_denies_write_for_readers(self):
        root = Path(app.config["DOCUMENT_ROOT"])
        (root / "private").mkdir()
        (root / "private" / "secret.txt").write_bytes(b"secret")
        self.store.scan()
        acl = VirtualFileSystem(root, {"admin"})
        acl.set_grants(".", {"jens": "manage", "bob": "read"}, "admin")
        acl.set_grants("private", {"jens": "manage"}, "admin", inherit=False)
        self.client.post("/auth/register", data={"username": "bob", "password": "bob-browser-passwort"})
        with app.test_request_context():
            password = activate("bob", "bob", label="SFTP/WebDAV", scope="write", expires_days=30)
        auth = {"Authorization": "Basic " + base64.b64encode(f"bob:{password}".encode()).decode()}

        listing = self.client.open(self.files.replace("jens", "bob"), method="PROPFIND", headers={**auth, "Depth": "infinity"})
        hidden = self.client.get("/webdav/files/bob/private/secret.txt", headers=auth)
        denied = self.client.put("/webdav/files/bob/angebot.odt", data=b"blocked", headers=auth)
        stable_denied = self.client.put(
            f"/webdav/documents/bob/{self.document['document_id']}--angebot.odt",
            data=b"blocked through stable URL", headers=auth,
        )

        self.assertEqual(207, listing.status_code)
        self.assertIn("angebot.odt", listing.get_data(as_text=True))
        self.assertNotIn("private", listing.get_data(as_text=True))
        self.assertEqual(404, hidden.status_code)
        self.assertEqual(403, denied.status_code)
        self.assertEqual(403, stable_denied.status_code)
        self.assertEqual(b"first office version", (root / "angebot.odt").read_bytes())

    def test_single_user_can_bootstrap_configurable_folder_access(self):
        response = self.client.post("/settings/webdav", data={
            "action": "save_access", "folder": ".", "inherit": "1",
            "access_jens": "manage",
        })
        policy = json.loads((Path(app.config["DOCUMENT_ROOT"]) / POLICY_FILE).read_text())

        self.assertEqual(200, response.status_code)
        self.assertIn("Ordnerrechte gespeichert", response.get_data(as_text=True))
        self.assertTrue(policy["access_enabled"])
        self.assertEqual([{"principal": "jens", "role": "manage"}], policy["grants"])

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

    def test_rotation_atomically_invalidates_old_password_and_preserves_scope(self):
        payload = json.loads((self.store.control / "webdav-credentials.json").read_text())
        before = payload["users"]["jens"]["credentials"][0]
        before["rotation_count"] = "corrupt-legacy-counter"
        (self.store.control / "webdav-credentials.json").write_text(json.dumps(payload))
        with app.test_request_context():
            replacement = rotate("jens", "jens", before["credential_id"], 90)
        replacement_auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{replacement}".encode()).decode()}

        self.assertEqual(401, self.client.get(self.url, headers=self.auth).status_code)
        self.assertEqual(200, self.client.get(self.url, headers=replacement_auth).status_code)
        after = json.loads((self.store.control / "webdav-credentials.json").read_text())["users"]["jens"]["credentials"][0]
        self.assertEqual(before["credential_id"], after["credential_id"])
        self.assertEqual(before["label"], after["label"])
        self.assertEqual(before["scope"], after["scope"])
        self.assertNotEqual(before["hash"], after["hash"])
        self.assertEqual(1, after["rotation_count"])
        snapshots = list((self.store.history.root / "events").glob("*.json"))
        self.assertTrue(any("webdav_credential_rotated" in path.read_text() for path in snapshots))

    def test_settings_rotation_requires_confirmation_and_shows_secret_once(self):
        record = json.loads((self.store.control / "webdav-credentials.json").read_text())["users"]["jens"]["credentials"][0]
        denied = self.client.post("/settings/webdav", data={
            "action": "rotate", "credential_id": record["credential_id"], "expires_days": "365",
        })
        self.assertIn("sofortige Ungültigkeit", denied.get_data(as_text=True))
        self.assertEqual(200, self.client.get(self.url, headers=self.auth).status_code)

        rotated = self.client.post("/settings/webdav", data={
            "action": "rotate", "credential_id": record["credential_id"],
            "expires_days": "365", "confirm_rotation": "ROTATE",
        })
        body = rotated.get_data(as_text=True)
        password = body.split('id="app-password"', 1)[1].split('value="', 1)[1].split('"', 1)[0]
        new_auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}
        self.assertIn("Das alte Passwort ist ab sofort ungültig", body)
        self.assertEqual(401, self.client.get(self.url, headers=self.auth).status_code)
        self.assertEqual(200, self.client.get(self.url, headers=new_auth).status_code)
        follow_up = self.client.get("/settings/webdav").get_data(as_text=True)
        self.assertNotIn(password, follow_up)

    def test_last_use_is_coarse_private_and_write_throttled(self):
        first = self.client.open(self.files, method="PROPFIND", headers={
            **self.auth, "Depth": "0", "User-Agent": "LibreOffice/26.2 confidential-build-detail",
        })
        usage_path = self.store.control / "webdav-credential-usage.json"
        first_payload = json.loads(usage_path.read_text())
        record = json.loads((self.store.control / "webdav-credentials.json").read_text())["users"]["jens"]["credentials"][0]
        usage = first_payload["users"]["jens"][record["credential_id"]]

        second = self.client.get(self.url, headers={**self.auth, "User-Agent": "SecretClient/99 serial-123"})
        second_payload = json.loads(usage_path.read_text())
        with app.test_request_context():
            display = credentials_for("jens")[0]

        self.assertEqual([207, 200], [first.status_code, second.status_code])
        self.assertEqual(first_payload, second_payload)
        self.assertEqual("PROPFIND", usage["method"])
        self.assertEqual("LibreOffice", usage["client"])
        self.assertNotIn("confidential-build-detail", usage_path.read_text())
        self.assertNotIn("serial-123", usage_path.read_text())
        self.assertEqual("LibreOffice", display["last_client"])
        self.assertTrue(display["last_used_at"])

    def test_usage_projection_failure_never_blocks_valid_webdav_authentication(self):
        with mock.patch("app.webdav.atomic_json_write", side_effect=OSError("read-only usage storage")):
            response = self.client.get(self.url, headers=self.auth)

        self.assertEqual(200, response.status_code)

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

        self.assertEqual("OPTIONS, PROPFIND, REPORT, SEARCH, GET, HEAD", options.headers["Allow"])
        self.assertEqual(207, listing.status_code)
        self.assertEqual(200, fetched.status_code)
        self.assertEqual([403, 403, 403], [put.status_code, folder.status_code, lock.status_code])
        put_error = ElementTree.fromstring(put.data)
        self.assertEqual(f"{self.files}/neu.txt", put_error.findtext(".//{DAV:}href"))
        self.assertIsNotNone(put_error.find(".//{DAV:}privilege/{DAV:}write"))
        self.assertEqual("private, no-store", put.headers["Cache-Control"])
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

        self.assertEqual("OPTIONS, PROPFIND, REPORT, SEARCH, GET, HEAD", options.headers["Allow"])
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

