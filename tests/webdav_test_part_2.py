"""WebDAV tests part 2 of 5."""
from __future__ import annotations

if __package__:
    from .webdav_test_base import *
else:
    from webdav_test_base import *


class WebDavDocumentTestPart2(WebDavTestBase):
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

