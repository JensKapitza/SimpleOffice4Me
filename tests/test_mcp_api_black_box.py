"""Public MCP contracts: JSON-RPC 2.0, published schemas and credential policy."""
from __future__ import annotations

from html.parser import HTMLParser
from io import BytesIO

from app import app
from http_api_fixture import PublicHttpTestCase


class TokenSettingsPage(HTMLParser):
    """Consume public credential output and the corresponding revoke form."""

    def __init__(self):
        super().__init__()
        self.secrets = []
        self.rows = []
        self.pre = None
        self.row = None
        self.action = ""

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.pre = []
        if tag == "tr":
            self.row = []
            self.action = ""
        if tag == "form" and self.row is not None:
            self.action = dict(attrs).get("action", "")

    def handle_data(self, data):
        if self.pre is not None:
            self.pre.append(data)
        if self.row is not None:
            self.row.append(data)

    def handle_endtag(self, tag):
        if tag == "pre" and self.pre is not None:
            self.secrets.append("".join(self.pre).strip())
            self.pre = None
        if tag == "tr" and self.row is not None:
            self.rows.append((" ".join(self.row), self.action))
            self.row = None


class McpApiBlackBoxTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        app.config["MCP_ENABLED"] = True
        self.read_token, self.read_revoke = self._mint("api-default-reader")
        self.write_token, self.write_revoke = self._mint("api-project-writer", write=True)

    def _mint(self, label, *, write=False, client=None):
        client = self.client if client is None else client
        data = {"name": label}
        if write:
            data["can_write"] = "1"
        created = client.post(
            "/settings/mcp", data=data,
            headers=self._csrf_headers(client, "/settings/mcp"),
        )
        self.assertEqual(200, created.status_code)
        page = TokenSettingsPage()
        page.feed(created.get_data(as_text=True))
        self.assertEqual(1, len(page.secrets))
        self.assertTrue(page.secrets[0])
        actions = [action for text, action in page.rows if label in text and action]
        self.assertEqual(1, len(actions))
        return page.secrets[0], actions[0]

    def _rpc(self, method, params=None, *, token=None, rpc_id="public-call-01", client=None):
        client = self.client if client is None else client
        payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
        if params is not None:
            payload["params"] = params
        return client.post(
            "/mcp", json=payload,
            headers={"Authorization": f"Bearer {self.read_token if token is None else token}"},
        )

    def _tool(self, name, arguments, *, token=None):
        response = self._rpc("tools/call", {"name": name, "arguments": arguments}, token=token)
        self.assertEqual(200, response.status_code)
        self.assertEqual("2.0", response.get_json()["jsonrpc"])
        self.assertEqual("public-call-01", response.get_json()["id"])
        return response.get_json()["result"]

    def _projects(self):
        result = self._tool("list_projects", {})
        self.assertFalse(result.get("isError", False))
        return result["structuredContent"]["projects"]

    def test_default_read_token_cannot_create_a_project(self):
        """Omit write permission at issuance: rejected call leaves zero projects."""
        denied = self._tool("create_project", {"title": "Must never be created"})
        self.assertTrue(denied["isError"])
        self.assertEqual([], self._projects())

    def test_write_token_accepts_the_published_title_length_boundary(self):
        """A 300-character title is valid and is returned unchanged on later read."""
        created = self._tool(
            "create_project", {"title": "A" * 300, "description": "Public boundary fixture"},
            token=self.write_token,
        )
        self.assertFalse(created.get("isError", False))
        projects = self._projects()
        self.assertEqual(1, len(projects))
        self.assertEqual("A" * 300, projects[0]["title"])
        self.assertEqual("Public boundary fixture", projects[0]["description"])

    def test_invalid_project_arguments_cannot_create_business_data(self):
        """Published required/type/length/enum/extra-field rules reject, not coerce."""
        cases = [
            {},
            {"title": ""},
            {"title": True},
            {"title": "A" * 301},
            {"title": "Wrong enum", "status": "not-a-project-status"},
            {"title": "Unknown field", "unpublished_field": "not allowed"},
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self._tool("create_project", arguments, token=self.write_token)
                self.assertTrue(result.get("isError", False), "Out-of-contract input must be rejected")
                self.assertEqual([], self._projects())

    def test_schema_declared_integer_limits_reject_boolean_and_out_of_range(self):
        """JSON booleans are not integers; list_projects declares limits 1..100."""
        for limit in (True, "1", 1.5, 0, 101):
            with self.subTest(limit=limit):
                result = self._tool("list_projects", {"limit": limit})
                self.assertTrue(result.get("isError", False))

    def test_project_list_accepts_integer_boundaries_and_applies_the_limit(self):
        """JSON Schema integer includes 1.0; limits 1 and 100 retain actual meaning."""
        for title in ("First public project", "Second public project"):
            created = self._tool("create_project", {"title": title}, token=self.write_token)
            self.assertFalse(created.get("isError", False))
        for limit, expected_count in ((1, 1), (1.0, 1), (100, 2)):
            with self.subTest(limit=limit):
                listed = self._tool("list_projects", {"limit": limit})
                self.assertFalse(listed.get("isError", False))
                self.assertEqual(expected_count, len(listed["structuredContent"]["projects"]))

    def test_nested_contact_field_values_must_follow_the_published_string_schema(self):
        """A numeric field is invalid, and no contact may appear after rejection."""
        result = self._tool("upsert_contact", {"fields": {"fn": 123}}, token=self.write_token)
        self.assertTrue(result.get("isError", False))
        contacts = self._tool("search_contacts", {"query": ""})
        self.assertEqual([], contacts["structuredContent"]["contacts"])

    def test_valid_contact_survives_a_rejected_update_unchanged(self):
        """String fields round-trip; a numeric update cannot replace saved text."""
        created = self._tool(
            "upsert_contact", {"fields": {"display_name": "Public API contact"}}, token=self.write_token,
        )
        self.assertFalse(created.get("isError", False))
        contact_id = created["structuredContent"]["contact_id"]
        denied = self._tool(
            "upsert_contact", {"contact_id": contact_id, "fields": {"display_name": 123}},
            token=self.write_token,
        )
        self.assertTrue(denied.get("isError", False))
        contacts = self._tool("search_contacts", {"query": "Public API contact"})
        self.assertEqual(1, len(contacts["structuredContent"]["contacts"]))
        self.assertEqual("Public API contact", contacts["structuredContent"]["contacts"][0]["fields"]["display_name"])

    def test_invalid_tag_arrays_cannot_change_an_uploaded_document(self):
        """Public upload and tag call succeed; invalid arrays preserve saved tags."""
        uploaded = self.client.post(
            "/documents/upload", data={"files": (BytesIO(b"API schema fixture"), "schema-fixture.txt")},
            content_type="multipart/form-data", headers=self._csrf_headers(self.client, "/documents/"),
        )
        self.assertEqual(302, uploaded.status_code)
        found = self._tool("search", {"query": "schema-fixture.txt"})
        self.assertEqual(1, len(found["structuredContent"]["results"]))
        document_id = found["structuredContent"]["results"][0]["id"]
        tagged = self._tool(
            "tag_document", {"document_id": document_id, "tags": ["public-schema-tag"]},
            token=self.write_token,
        )
        self.assertFalse(tagged.get("isError", False))
        before = self._tool("fetch", {"document_id": document_id})["structuredContent"]["tags"]
        self.assertIn("public-schema-tag", before)
        for tags in ([], "not an array", [123], [""], ["A" * 81], ["extra"] * 21):
            with self.subTest(tags=tags):
                denied = self._tool(
                    "tag_document", {"document_id": document_id, "tags": tags}, token=self.write_token,
                )
                self.assertTrue(denied.get("isError", False))
                after = self._tool("fetch", {"document_id": document_id})["structuredContent"]["tags"]
                self.assertEqual(before, after, "Rejected input must preserve previously saved tags")

    def test_non_object_call_params_produce_the_json_rpc_invalid_params_error(self):
        """params=[] cannot describe a tool call: -32602, echoed ID, no mutation."""
        for params in ([], "not an object", {}, {"name": True}):
            with self.subTest(params=params):
                response = self._rpc("tools/call", params, token=self.write_token, rpc_id="malformed-call-01")
                self.assertEqual(-32602, response.get_json()["error"]["code"])
                self.assertEqual("malformed-call-01", response.get_json()["id"])
                self.assertNotIn("result", response.get_json())
                self.assertEqual([], self._projects())

    def test_revocation_immediately_denies_bearer_even_with_an_authenticated_cookie(self):
        """Revoke through the public form: token fails, separate token still works."""
        revoked = self.client.post(
            self.read_revoke, headers=self._csrf_headers(self.client, "/settings/mcp"),
        )
        self.assertEqual(302, revoked.status_code)
        self.assertEqual(401, self._rpc("ping").status_code)
        self.assertEqual(200, self._rpc("ping", token=self.write_token).status_code)

    def test_another_user_cannot_revoke_the_owners_token(self):
        """Knowing a revoke URL gives another account no authority over the token."""
        self._register(self.client, "outsider")
        outsider = app.test_client()
        self._login(outsider, "outsider")
        attempted = outsider.post(
            self.read_revoke, headers=self._csrf_headers(outsider, "/settings/mcp"),
        )
        self.assertEqual(302, attempted.status_code)
        self.assertEqual(200, self._rpc("ping").status_code)

    def test_public_operation_journal_does_not_repeat_tokens_or_business_arguments(self):
        """Create a project: tool is journaled, secret and business text are absent."""
        result = self._tool(
            "create_project",
            {"title": "confidential-business-title", "description": "private-description-marker"},
            token=self.write_token,
        )
        self.assertFalse(result.get("isError", False))
        listing = self.client.get("/settings/mcp")
        self.assertEqual(200, listing.status_code)
        text = listing.get_data(as_text=True)
        self.assertIn("create_project", text)
        for sensitive in (self.read_token, self.write_token,
                          "confidential-business-title", "private-description-marker"):
            self.assertNotIn(sensitive, text)
