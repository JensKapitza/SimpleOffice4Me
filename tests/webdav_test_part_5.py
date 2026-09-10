"""WebDAV tests part 5 of 5."""
from __future__ import annotations

if __package__:
    from .webdav_test_base import *
else:
    from webdav_test_base import *


class WebDavDocumentTestPart5(WebDavTestBase):
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

    def test_current_principal_and_privileges_are_resource_and_scope_aware(self):
        folder_url = f"{self.files}/Rechte"
        file_url = f"{folder_url}/Plan.odt"
        self.assertEqual(201, self.client.open(folder_url, method="MKCOL", headers=self.auth).status_code)
        self.assertEqual(201, self.client.put(file_url, data=b"plan", headers=self.auth).status_code)
        query = '''<d:propfind xmlns:d="DAV:"><d:prop>
          <d:owner/><d:current-user-principal/><d:principal-collection-set/>
          <d:current-user-privilege-set/>
        </d:prop></d:propfind>'''

        folder = self.client.open(
            folder_url, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        file = self.client.open(
            file_url, method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        folder_root = ElementTree.fromstring(folder.data)
        file_root = ElementTree.fromstring(file.data)
        principal = "/webdav/principals/jens/self"

        self.assertEqual([207, 207], [folder.status_code, file.status_code])
        self.assertEqual(principal, folder_root.findtext(".//{DAV:}owner/{DAV:}href"))
        self.assertEqual(principal, folder_root.findtext(".//{DAV:}current-user-principal/{DAV:}href"))
        self.assertEqual(
            "/webdav/principals/jens/",
            folder_root.findtext(".//{DAV:}principal-collection-set/{DAV:}href"),
        )
        folder_privileges = {
            child.tag.removeprefix("{DAV:}")
            for privilege in folder_root.findall(
                ".//{DAV:}current-user-privilege-set/{DAV:}privilege"
            )
            for child in privilege
        }
        file_privileges = {
            child.tag.removeprefix("{DAV:}")
            for privilege in file_root.findall(
                ".//{DAV:}current-user-privilege-set/{DAV:}privilege"
            )
            for child in privilege
        }
        self.assertTrue({"read", "write", "write-properties", "write-content", "bind", "unbind", "unlock"} <= folder_privileges)
        self.assertTrue({"read", "write", "write-properties", "write-content", "unlock"} <= file_privileges)
        self.assertFalse({"bind", "unbind"} & file_privileges)
        self.assertNotIn("write-acl", folder_privileges)

    def test_read_only_principal_endpoint_is_private_and_self_consistent(self):
        with app.test_request_context():
            password = activate(
                "jens", "jens", label="Principal Reader", scope="read", expires_days=30,
            )
        read_auth = {
            "Authorization": "Basic "
            + base64.b64encode(f"jens:{password}".encode()).decode()
        }
        query = '''<d:propfind xmlns:d="DAV:"><d:prop>
          <d:displayname/><d:resourcetype/><d:principal-URL/>
          <d:alternate-URI-set/><d:group-membership/>
          <d:current-user-privilege-set/>
        </d:prop></d:propfind>'''

        principal = self.client.open(
            "/webdav/principals/jens/self", method="PROPFIND", data=query,
            headers={**read_auth, "Depth": "0"},
        )
        collection = self.client.open(
            "/webdav/principals/jens/", method="PROPFIND", data=query,
            headers={**read_auth, "Depth": "1"},
        )
        resource = self.client.open(
            f"{self.files}/angebot.odt", method="PROPFIND",
            data='<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-privilege-set/></d:prop></d:propfind>',
            headers={**read_auth, "Depth": "0"},
        )
        root = ElementTree.fromstring(principal.data)
        resource_root = ElementTree.fromstring(resource.data)

        self.assertEqual([207, 207, 207], [principal.status_code, collection.status_code, resource.status_code])
        self.assertIsNotNone(root.find(".//{DAV:}resourcetype/{DAV:}principal"))
        self.assertEqual("jens", root.findtext(".//{DAV:}displayname"))
        self.assertEqual(
            "/webdav/principals/jens/self",
            root.findtext(".//{DAV:}principal-URL/{DAV:}href"),
        )
        self.assertIsNotNone(root.find(".//{DAV:}alternate-URI-set"))
        self.assertIsNotNone(root.find(".//{DAV:}group-membership"))
        self.assertEqual(
            2,
            len(ElementTree.fromstring(collection.data).findall("{DAV:}response")),
        )
        privileges = {
            child.tag.removeprefix("{DAV:}")
            for privilege in resource_root.findall(
                ".//{DAV:}current-user-privilege-set/{DAV:}privilege"
            )
            for child in privilege
        }
        self.assertEqual({"read", "read-current-user-privilege-set"}, privileges)

    def test_principal_discovery_does_not_disclose_users_or_enable_acl_changes(self):
        query = '<d:propfind xmlns:d="DAV:"><d:allprop/><d:include><d:current-user-principal/></d:include></d:propfind>'
        included = self.client.open(
            f"{self.files}/angebot.odt", method="PROPFIND", data=query,
            headers={**self.auth, "Depth": "0"},
        )
        allprop = self.client.open(
            f"{self.files}/angebot.odt", method="PROPFIND",
            headers={**self.auth, "Depth": "0"},
        )
        protected = self.client.open(
            f"{self.files}/angebot.odt", method="PROPPATCH",
            data='''<d:propertyupdate xmlns:d="DAV:"><d:set><d:prop>
              <d:owner><d:href>/webdav/principals/other/self</d:href></d:owner>
              <d:current-user-principal><d:href>/webdav/principals/other/self</d:href></d:current-user-principal>
            </d:prop></d:set></d:propertyupdate>''',
            headers=self.auth,
        )
        foreign = self.client.open(
            "/webdav/principals/other/", method="PROPFIND", headers=self.auth,
        )
        unknown = self.client.open(
            "/webdav/principals/jens/not-self", method="PROPFIND", headers=self.auth,
        )
        unauthenticated = self.client.open(
            "/webdav/principals/jens/self", method="PROPFIND",
        )
        infinite = self.client.open(
            "/webdav/principals/jens/", method="PROPFIND",
            headers={**self.auth, "Depth": "infinity"},
        )
        virtual_root = self.client.open(
            "/webdav/", method="PROPFIND", headers={**self.auth, "Depth": "0"},
        )

        self.assertEqual(207, included.status_code)
        self.assertIn("current-user-principal", included.get_data(as_text=True))
        self.assertNotIn("current-user-principal", allprop.get_data(as_text=True))
        self.assertEqual(207, protected.status_code)
        self.assertIn("403 Forbidden", protected.get_data(as_text=True))
        self.assertEqual([404, 404, 401, 403, 207], [foreign.status_code, unknown.status_code, unauthenticated.status_code, infinite.status_code, virtual_root.status_code])
        self.assertEqual("private, no-store", included.headers["Cache-Control"])

    def test_search_is_discoverable_and_matches_names_without_reading_file_bodies(self):
        self.store.create_document_at("Projektplan.odt", b"needle-secret", "jens")
        self.store.create_document_at("Foto.jpg", b"picture", "jens")
        options = self.client.open(self.files, method="OPTIONS", headers=self.auth)
        discovery = self.client.open(
            self.files, method="PROPFIND",
            data='''<d:propfind xmlns:d="DAV:"><d:prop>
              <d:supported-method-set/><d:supported-query-grammar-set/>
            </d:prop></d:propfind>''',
            headers={**self.auth, "Depth": "0"},
        )
        result = self.search(
            where='''<d:like caseless="yes"><d:prop><d:displayname/></d:prop>
              <d:literal>%PLAN%</d:literal></d:like>''',
        )

        xml = ElementTree.fromstring(result.data)
        hrefs = [item.findtext("{DAV:}href") for item in xml.findall("{DAV:}response")]
        self.assertEqual(204, options.status_code)
        self.assertIn("SEARCH", options.headers["Allow"])
        self.assertEqual("<DAV:basicsearch>", options.headers["DASL"])
        self.assertEqual(207, discovery.status_code)
        self.assertIsNotNone(ElementTree.fromstring(discovery.data).find(".//{DAV:}basicsearch"))
        self.assertEqual([f"{self.files}/Projektplan.odt"], hrefs)
        self.assertNotIn(b"needle-secret", result.data)
        self.assertEqual("private, no-store", result.headers["Cache-Control"])
        audits = [row for row in self.store.logbook() if row.get("action") == "webdav_search_executed"]
        self.assertTrue(audits)
        snapshots = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.store.history.root / "snapshots" / "webdav-search").glob("*.json")
        ]
        executed = max((item for item in snapshots if item.get("matched") == 1), key=lambda item: item["at"])
        self.assertEqual(1, executed["matched"])
        self.assertNotIn("plan", json.dumps(executed).casefold())

    def test_search_supports_dead_property_tags_three_valued_logic_and_caseless_like(self):
        tagged = self.store.create_document_at("Rechnung.odt", b"invoice", "jens")
        plain = self.store.create_document_at("Notiz.txt", b"note", "jens")
        tagged_url = f"{self.files}/Rechnung.odt"
        set_tag = self.client.open(
            tagged_url, method="PROPPATCH",
            data='''<d:propertyupdate xmlns:d="DAV:" xmlns:t="urn:simpleoffice:test">
              <d:set><d:prop><t:tag>Kunde-A</t:tag></d:prop></d:set>
            </d:propertyupdate>''', headers=self.auth,
        )
        result = self.search(
            select="<d:displayname/><t:tag/>",
            where='''<d:and>
              <d:is-defined><d:prop><t:tag/></d:prop></d:is-defined>
              <d:like caseless="yes"><d:prop><t:tag/></d:prop><d:literal>kunde-%</d:literal></d:like>
            </d:and>''',
        )
        missing_under_not = self.search(
            where='''<d:not><d:eq><d:prop><t:missing/></d:prop>
              <d:literal>anything</d:literal></d:eq></d:not>''',
        )

        self.assertEqual(207, set_tag.status_code)
        self.assertEqual(tagged["document_id"], self.store.get_document("Rechnung.odt")["document_id"])
        self.assertIn(tagged_url, result.get_data(as_text=True))
        self.assertIn("Kunde-A", result.get_data(as_text=True))
        self.assertNotIn(f"{self.files}/{plain['last_path']}", result.get_data(as_text=True))
        self.assertEqual([], ElementTree.fromstring(missing_under_not.data).findall("{DAV:}response"))

    def test_search_orders_typed_sizes_and_applies_client_limit(self):
        self.store.create_document_at("klein.txt", b"1", "jens")
        self.store.create_document_at("mittel.txt", b"12345", "jens")
        self.store.create_document_at("gross.txt", b"x" * 100, "jens")
        result = self.search(
            select="<d:displayname/><d:getcontentlength/>",
            where='''<d:and><d:not><d:is-collection/></d:not>
              <d:gt><d:prop><d:getcontentlength/></d:prop><d:literal>0</d:literal></d:gt>
            </d:and>''',
            orderby='''<d:orderby><d:order><d:prop><d:getcontentlength/></d:prop>
              <d:descending/></d:order></d:orderby>''',
            limit="2",
        )

        root = ElementTree.fromstring(result.data)
        responses = root.findall("{DAV:}response")
        hrefs = [item.findtext("{DAV:}href") for item in responses]
        lengths = [int(item.findtext(".//{DAV:}getcontentlength")) for item in responses]
        self.assertEqual(207, result.status_code)
        self.assertEqual(2, len(hrefs))
        self.assertEqual(sorted(lengths, reverse=True), lengths)
        self.assertEqual(f"{self.files}/gross.txt", hrefs[0])

    def test_search_respects_folder_scoped_read_credentials_and_hides_other_paths(self):
        (self.store.root / "Freigabe").mkdir()
        (self.store.root / "Privat").mkdir()
        self.store.create_document_at("Freigabe/Plan.odt", b"plan", "jens")
        self.store.create_document_at("Privat/Geheim.odt", b"secret", "jens")
        with app.test_request_context():
            password = activate(
                "jens", "jens", label="Suchclient", scope="read",
                path_prefix="Freigabe", expires_days=30,
            )
        auth = {"Authorization": "Basic " + base64.b64encode(f"jens:{password}".encode()).decode()}
        endpoint = f"{self.files}/Freigabe"
        allowed = self.search(
            endpoint=endpoint, scope=endpoint, auth=auth,
            where='<d:like caseless="yes"><d:prop><d:displayname/></d:prop><d:literal>%plan%</d:literal></d:like>',
        )
        outside = self.search(endpoint=endpoint, scope=self.files, auth=auth)
        sibling = self.search(endpoint=endpoint, scope=f"{self.files}/Privat", auth=auth)
        unauthenticated = self.client.open(
            endpoint, method="SEARCH", data=b"<d:searchrequest xmlns:d='DAV:'/>",
            headers={"Content-Type": "application/xml"},
        )

        self.assertEqual(207, allowed.status_code)
        self.assertIn("Plan.odt", allowed.get_data(as_text=True))
        self.assertNotIn("Geheim.odt", allowed.get_data(as_text=True))
        self.assertEqual([409, 409, 401], [outside.status_code, sibling.status_code, unauthenticated.status_code])
        self.assertIn("search-scope-valid", outside.get_data(as_text=True))

    def test_search_rejects_unsupported_grammar_operators_scopes_and_unsafe_xml(self):
        wrong_type = self.search(content_type="application/json")
        malformed = self.client.open(
            self.files, method="SEARCH", data="<broken>",
            headers={**self.auth, "Content-Type": "application/xml"},
        )
        unsupported = self.search(
            where='<d:contains><d:prop><d:displayname/></d:prop><d:literal>x</d:literal></d:contains>',
        )
        multiple_body = f'''<d:searchrequest xmlns:d="DAV:"><d:basicsearch>
          <d:select><d:prop><d:displayname/></d:prop></d:select><d:from>
          <d:scope><d:href>{self.files}</d:href><d:depth>0</d:depth></d:scope>
          <d:scope><d:href>{self.files}</d:href><d:depth>1</d:depth></d:scope>
          </d:from></d:basicsearch></d:searchrequest>'''
        multiple = self.client.open(
            self.files, method="SEARCH", data=multiple_body,
            headers={**self.auth, "Content-Type": "application/xml"},
        )
        external = self.search(scope="https://example.invalid/webdav/files/jens")
        entity = self.client.open(
            self.files, method="SEARCH",
            data='''<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]>
              <d:searchrequest xmlns:d="DAV:">&leak;</d:searchrequest>''',
            headers={**self.auth, "Content-Type": "application/xml"},
        )

        self.assertEqual([415, 400, 422, 422, 409, 400], [
            wrong_type.status_code, malformed.status_code, unsupported.status_code,
            multiple.status_code, external.status_code, entity.status_code,
        ])
        self.assertIn("search-multiple-scope-supported", multiple.get_data(as_text=True))
        self.assertIn("search-scope-valid", external.get_data(as_text=True))
        self.assertNotIn("root:", entity.get_data(as_text=True))

    def test_search_result_limit_returns_complete_207_with_507_and_audit(self):
        self.store.create_document_at("eins.txt", b"1", "jens")
        self.store.create_document_at("zwei.txt", b"2", "jens")
        with mock.patch("app.webdav.MAX_SEARCH_RESULTS", 1):
            rejected = self.search(
                where='<d:not><d:is-collection/></d:not>',
            )
            bounded = self.search(
                where='<d:not><d:is-collection/></d:not>', limit="1",
            )

        rejected_root = ElementTree.fromstring(rejected.data)
        self.assertEqual([207, 207], [rejected.status_code, bounded.status_code])
        self.assertEqual("result-count", rejected.headers["X-SimpleOffice-Search-Limit"])
        self.assertIn("507 Insufficient Storage", rejected.get_data(as_text=True))
        self.assertEqual(1, len(rejected_root.findall("{DAV:}response")))
        self.assertEqual(1, len(ElementTree.fromstring(bounded.data).findall("{DAV:}response")))
        audits = [row for row in self.store.logbook() if row.get("action") == "webdav_search_limit_rejected"]
        self.assertTrue(audits)
        snapshots = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.store.history.root / "snapshots" / "webdav-search").glob("*.json")
        ]
        self.assertIn("result-count", {item.get("reason") for item in snapshots})

    def test_search_file_scope_forces_depth_zero_and_hidden_control_data_is_never_visible(self):
        result = self.search(
            endpoint=f"{self.files}/angebot.odt",
            scope=f"{self.files}/angebot.odt",
            depth="infinity",
        )
        broad = self.search()

        self.assertEqual(207, result.status_code)
        self.assertEqual(1, len(ElementTree.fromstring(result.data).findall("{DAV:}response")))
        body = broad.get_data(as_text=True)
        self.assertNotIn(CONTROL_DIR, body)
        self.assertNotIn("webdav-credentials.json", body)
        snapshots = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.store.history.root / "snapshots" / "webdav-search").glob("*.json")
        ]
        self.assertIn("0", {item.get("depth") for item in snapshots})

    def test_search_response_size_is_bounded_and_tree_snapshot_uses_mutation_lock(self):
        self.store.create_document_at("Mehr-Inhalt.txt", b"content", "jens")
        with mock.patch("app.webdav.MAX_PROPFIND_RESPONSE_BYTES", 128):
            limited = self.search()
        with mock.patch("app.webdav.exclusive_file_lock") as locking:
            complete = self.search(limit="1")

        self.assertEqual([207, 207], [limited.status_code, complete.status_code])
        self.assertEqual("response-bytes", limited.headers["X-SimpleOffice-Search-Limit"])
        self.assertIn("507 Insufficient Storage", limited.get_data(as_text=True))
        self.assertNotIn("angebot.odt", limited.get_data(as_text=True))
        lock_paths = [str(call.args[0]) for call in locking.call_args_list if call.args]
        self.assertTrue(any(path.endswith("webdav-sync.mutation.lock") for path in lock_paths))

