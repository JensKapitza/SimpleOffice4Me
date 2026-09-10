"""WebDAV tests part 4 of 5."""
from __future__ import annotations

if __package__:
    from .webdav_test_base import *
else:
    from webdav_test_base import *


class WebDavDocumentTestPart4(WebDavTestBase):
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

    def test_sync_report_rejects_bad_depth_shape_invalid_limit_and_file_target(self):
        valid = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        bad_depth = self.client.open(self.files, method="REPORT", data=valid, headers={**self.auth, "Depth": "1"})
        bad_shape = self.client.open(self.files, method="REPORT", data='<d:sync-collection xmlns:d="DAV:"/>', headers=self.auth)
        limited = self.client.open(self.files, method="REPORT", data=valid.replace("<d:prop>", "<d:limit><d:nresults>0</d:nresults></d:limit><d:prop>"), headers=self.auth)
        file_target = self.client.open(f"{self.files}/angebot.odt", method="REPORT", data=valid, headers=self.auth)

        self.assertEqual([400, 400, 400, 400], [bad_depth.status_code, bad_shape.status_code, limited.status_code, file_target.status_code])
        self.assertIn("positive integer", limited.get_data(as_text=True))

    def test_sync_limit_pages_initial_inventory_without_duplicates(self):
        self.store.create_document_at("alpha.txt", b"a", "jens")
        self.store.create_document_at("beta.txt", b"b", "jens")
        self.store.create_document_at("gamma.txt", b"c", "jens")
        template = '<d:sync-collection xmlns:d="DAV:"><d:sync-token>{token}</d:sync-token><d:sync-level>infinite</d:sync-level><d:limit><d:nresults>1</d:nresults></d:limit><d:prop><d:getetag/></d:prop></d:sync-collection>'
        token = ""
        hrefs = []
        pages = 0
        while True:
            response = self.client.open(
                self.files, method="REPORT", data=template.format(token=token),
                headers={**self.auth, "Depth": "0"},
            )
            root = ElementTree.fromstring(response.data)
            pages += 1
            for item in root.findall("{DAV:}response"):
                status = item.findtext("{DAV:}status", "")
                if "507" not in status:
                    hrefs.append(item.findtext("{DAV:}href"))
            token = root.findtext("{DAV:}sync-token")
            if "507 Insufficient Storage" not in response.get_data(as_text=True):
                break
            self.assertEqual("result-count", response.headers["X-SimpleOffice-Sync-Limit"])
            self.assertLess(pages, 10)

        self.assertEqual(4, pages)
        self.assertEqual(4, len(hrefs))
        self.assertEqual(len(hrefs), len(set(hrefs)))
        self.assertTrue(any(href.endswith("angebot.odt") for href in hrefs))
        quiet = self.client.open(
            self.files, method="REPORT", data=template.format(token=token), headers=self.auth,
        )
        self.assertEqual([], ElementTree.fromstring(quiet.data).findall("{DAV:}response"))

    def test_sync_server_cap_pages_without_client_limit_and_audits_cursor(self):
        self.store.create_document_at("alpha.txt", b"a", "jens")
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        with mock.patch("app.webdav.MAX_SYNC_PAGE_RESULTS", 1):
            first = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
            token = ElementTree.fromstring(first.data).findtext("{DAV:}sync-token")
            second = self.client.open(
                self.files, method="REPORT",
                data=body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>"),
                headers=self.auth,
            )

        self.assertEqual([207, 207], [first.status_code, second.status_code])
        self.assertIn("507 Insufficient Storage", first.get_data(as_text=True))
        self.assertNotIn("507 Insufficient Storage", second.get_data(as_text=True))
        events = [row for row in self.store.logbook() if row.get("action") == "webdav_sync_truncated"]
        self.assertTrue(events)
        snapshots = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.store.history.root / "snapshots" / "webdav-sync").glob("*.json")
        ]
        self.assertEqual(1, snapshots[-1]["returned"])
        self.assertEqual(1, snapshots[-1]["remaining"])
        self.assertNotIn("urn:uuid", json.dumps(snapshots[-1]))

    def test_sync_paging_includes_parallel_change_after_partial_token(self):
        self.store.create_document_at("zulu.txt", b"z", "jens")
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>infinite</d:sync-level><d:limit><d:nresults>1</d:nresults></d:limit><d:prop><d:getetag/></d:prop></d:sync-collection>'
        first = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        token = ElementTree.fromstring(first.data).findtext("{DAV:}sync-token")
        first_href = next(
            item.findtext("{DAV:}href") for item in ElementTree.fromstring(first.data).findall("{DAV:}response")
            if "507" not in item.findtext("{DAV:}status", "")
        )
        path = first_href.rsplit("/", 1)[-1]
        current = self.client.get(f"{self.files}/{path}", headers=self.auth)
        changed = self.client.put(
            f"{self.files}/{path}", data=b"parallel update",
            headers={**self.auth, "If-Match": current.headers["ETag"]},
        )
        seen_after = []
        for _page in range(6):
            page = self.client.open(
                self.files, method="REPORT",
                data=body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>"),
                headers=self.auth,
            )
            root = ElementTree.fromstring(page.data)
            seen_after.extend(
                item.findtext("{DAV:}href") for item in root.findall("{DAV:}response")
                if "507" not in item.findtext("{DAV:}status", "")
            )
            token = root.findtext("{DAV:}sync-token")
            if "507 Insufficient Storage" not in page.get_data(as_text=True):
                break

        self.assertEqual(204, changed.status_code)
        self.assertIn(first_href, seen_after)
        self.assertTrue(any(href.endswith("zulu.txt") for href in [first_href, *seen_after]))

    def test_sync_paging_suppresses_removed_collection_descendants_across_boundary(self):
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>infinite</d:sync-level><d:limit><d:nresults>1</d:nresults></d:limit><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=body.replace("<d:limit><d:nresults>1</d:nresults></d:limit>", ""), headers=self.auth)
        token = ElementTree.fromstring(initial.data).findtext("{DAV:}sync-token")
        self.client.open(f"{self.files}/Ordner", method="MKCOL", headers=self.auth)
        self.client.put(f"{self.files}/Ordner/eins.txt", data=b"1", headers=self.auth)
        self.client.put(f"{self.files}/Ordner/zwei.txt", data=b"2", headers=self.auth)
        deleted = self.client.delete(f"{self.files}/Ordner", headers=self.auth)
        report = self.client.open(
            self.files, method="REPORT",
            data=body.replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>"),
            headers=self.auth,
        )
        text = report.get_data(as_text=True)

        self.assertEqual(204, deleted.status_code)
        self.assertEqual(1, len(ElementTree.fromstring(report.data).findall("{DAV:}response")))
        self.assertIn("Ordner/", text)
        self.assertNotIn("eins.txt", text)
        self.assertNotIn("zwei.txt", text)
        self.assertNotIn("507 Insufficient Storage", text)

    def test_sync_limit_validation_and_partial_token_user_isolation(self):
        base = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level>{limit}<d:prop><d:getetag/></d:prop></d:sync-collection>'
        invalid = []
        for limit in (
            "<d:limit/>",
            "<d:limit><d:nresults>-1</d:nresults></d:limit>",
            "<d:limit><d:nresults>abc</d:nresults></d:limit>",
            "<d:limit><d:nresults>1</d:nresults><d:nresults>2</d:nresults></d:limit>",
        ):
            invalid.append(self.client.open(self.files, method="REPORT", data=base.format(limit=limit), headers=self.auth))
        self.store.create_document_at("alpha.txt", b"a", "jens")
        partial = self.client.open(
            self.files, method="REPORT",
            data=base.format(limit="<d:limit><d:nresults>1</d:nresults></d:limit>"), headers=self.auth,
        )
        token = ElementTree.fromstring(partial.data).findtext("{DAV:}sync-token")
        self.client.get("/auth/logout")
        self.client.post("/auth/register", data={"username": "other", "password": "other-browser-password"})
        with app.test_request_context():
            password = activate("other", "other", label="Other", expires_days=30)
        auth = {"Authorization": "Basic " + base64.b64encode(f"other:{password}".encode()).decode()}
        foreign = self.client.open(
            "/webdav/files/other", method="REPORT",
            data=base.format(limit="").replace("<d:sync-token/>", f"<d:sync-token>{token}</d:sync-token>"),
            headers=auth,
        )

        self.assertEqual([400, 400, 400, 400], [item.status_code for item in invalid])
        self.assertEqual(207, partial.status_code)
        self.assertEqual(403, foreign.status_code)
        self.assertIn("valid-sync-token", foreign.get_data(as_text=True))

    def test_sync_paging_migrates_legacy_journal_without_changing_documents(self):
        body = '<d:sync-collection xmlns:d="DAV:"><d:sync-token/><d:sync-level>1</d:sync-level><d:limit><d:nresults>1</d:nresults></d:limit><d:prop><d:getetag/></d:prop></d:sync-collection>'
        initial = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        sync_path = self.store.control / "webdav-sync.json"
        payload = json.loads(sync_path.read_text(encoding="utf-8"))
        state = payload["users"]["jens"]["collections"]["."]
        state.pop("path_revisions")
        sync_path.write_text(json.dumps(payload), encoding="utf-8")
        before = (self.store.root / "angebot.odt").read_bytes()

        migrated = self.client.open(self.files, method="REPORT", data=body, headers=self.auth)
        persisted = json.loads(sync_path.read_text(encoding="utf-8"))
        migrated_state = persisted["users"]["jens"]["collections"]["."]

        self.assertEqual(207, initial.status_code)
        self.assertEqual(207, migrated.status_code)
        self.assertEqual(before, (self.store.root / "angebot.odt").read_bytes())
        self.assertIn("angebot.odt", migrated_state["path_revisions"])
        self.assertGreater(migrated_state["revision"], state["revision"])

    def test_sync_report_rejects_external_entities_without_leaking_content(self):
        response = self.client.open(
            self.files, method="REPORT",
            data='''<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]>
              <d:sync-collection xmlns:d="DAV:"><d:sync-token>&leak;</d:sync-token>
              <d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>''',
            headers=self.auth,
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("no-external-entities", response.get_data(as_text=True))
        self.assertNotIn("root:", response.get_data(as_text=True))

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

