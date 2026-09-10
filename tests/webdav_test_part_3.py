"""WebDAV tests part 3 of 5."""
from __future__ import annotations

if __package__:
    from .webdav_test_base import *
else:
    from webdav_test_base import *


class WebDavDocumentTestPart3(WebDavTestBase):
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

