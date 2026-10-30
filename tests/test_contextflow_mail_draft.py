"""ContextFlow P0-E: editable mail drafts from evidenced hints.

Run with the project venv (``.venv``) and TEMP/TMP pointing to a short local
path, e.g. ``C:\\Users\\Jens Kapitza\\AppData\\Local\\Temp``.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from app.contextflow.actions import ApprovalStale
from app.contextflow.evaluator import FollowupEvaluator
from app.contextflow.mail_draft import prepare_hint_draft
from app.contact_store import ContactStore
from app.mail_case_store import MailCaseStore
from app.site_visit_store import SiteVisitStore

from tests import contextflow_fixtures as fx

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=BERLIN)


class PrepareHintDraftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contact_id = str(fx.make_contact(self.root)["contact_id"])
        self.case_id = fx.make_mail_case(self.root)
        self.visit_id = fx.make_visit(self.root, completed=True)
        SiteVisitStore(self.root).add_finding(
            self.visit_id,
            fx.OWNER,
            {"title": "Zweiter Befund (synthetisch)", "status": "unreviewed"},
        )
        self.link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=self.case_id,
            contact_id=self.contact_id,
            visit_id=self.visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, self.link["link_id"], NOW
        )
        assert hint is not None
        self.hint_id = str(hint["hint_id"])
        self.finding_ids = [
            str(item) for item in hint.get("pending_finding_ids", []) or []
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prepare(self, **overrides):
        params = {
            "actor": fx.OWNER,
            "link_id": self.link["link_id"],
            "expected_hint_id": self.hint_id,
            "approved": True,
            "now": NOW,
            "root": self.root,
        }
        params.update(overrides)
        return prepare_hint_draft(**params)

    def _drafts(self) -> list:
        case = MailCaseStore(self.root).get_case(fx.OWNER, self.case_id)
        return [dict(row) for row in case.get("drafts", []) or []]

    def test_happy_path_creates_editable_draft(self) -> None:
        with (
            mock.patch.object(
                MailCaseStore, "request_draft_send",
                side_effect=AssertionError("must never request sending"),
            ),
            mock.patch.object(
                MailCaseStore, "review_draft_send",
                side_effect=AssertionError("must never review sending"),
            ),
            mock.patch.object(
                MailCaseStore, "begin_draft_send",
                side_effect=AssertionError("must never begin sending"),
            ),
            mock.patch.object(
                MailCaseStore, "complete_draft_send",
                side_effect=AssertionError("must never complete sending"),
            ),
        ):
            result = self._prepare()
        self.assertTrue(result["created"])
        self.assertEqual(self.hint_id, result["hint_id"])
        self.assertEqual(self.case_id, result["case_id"])
        self.assertIn(self.case_id, result["mail_url"])
        drafts = self._drafts()
        self.assertEqual(1, len(drafts))
        draft = drafts[0]
        self.assertEqual(result["draft_id"], draft["id"])
        self.assertEqual("draft", draft["status"])
        self.assertEqual(fx.CONTACT_EMAIL, draft["recipients_to"])
        self.assertIn("Nachfrage", draft["subject"])
        self.assertIn(fx.VISIT_TITLE, draft["subject"])
        for finding_id in self.finding_ids:
            self.assertIn(finding_id, draft["body"])
        self.assertIn(fx.FINDING_TITLE, draft["body"])
        self.assertIn("Zweiter Befund (synthetisch)", draft["body"])
        self.assertIn("nicht versendet", draft["body"])
        self.assertNotIn("ContextFlow-Hinweis", draft["body"])
        self.assertNotIn("ContextFlow-Hinweis", draft["subject"])
        self.assertIsNone(re.search(r"[0-9a-f]{64}", draft["body"]))
        self.assertIsNone(re.search(r"[0-9a-f]{64}", draft["subject"]))
        self.assertNotIn(self.hint_id, draft["body"])
        MailCaseStore(self.root).update_draft(
            fx.OWNER,
            self.case_id,
            draft["id"],
            draft["recipients_to"],
            draft["subject"] + " (geprüft)",
            draft["body"] + "\nNachtrag des Eigentümers.",
        )
        edited = self._drafts()[0]
        self.assertTrue(edited["subject"].endswith("(geprüft)"))
        self.assertIn("Nachtrag des Eigentümers.", edited["body"])
        self.assertEqual("draft", edited["status"])

    def test_replay_returns_same_draft(self) -> None:
        first = self._prepare()
        second = self._prepare()
        self.assertFalse(second["created"])
        self.assertEqual(first["draft_id"], second["draft_id"])
        self.assertEqual(1, len(self._drafts()))

    def test_replay_preserves_owner_edits(self) -> None:
        first = self._prepare()
        MailCaseStore(self.root).update_draft(
            fx.OWNER,
            self.case_id,
            first["draft_id"],
            fx.CONTACT_EMAIL,
            "Eigener Betreff",
            "Eigener Text des Eigentümers.",
        )
        second = self._prepare()
        self.assertFalse(second["created"])
        self.assertEqual(first["draft_id"], second["draft_id"])
        drafts = self._drafts()
        self.assertEqual(1, len(drafts))
        self.assertEqual("Eigener Betreff", drafts[0]["subject"])
        self.assertEqual("Eigener Text des Eigentümers.", drafts[0]["body"])

    def test_stale_hint_rejected(self) -> None:
        with self.assertRaises(ApprovalStale):
            self._prepare(expected_hint_id="0" * 64)
        SiteVisitStore(self.root).add_finding(
            self.visit_id,
            fx.OWNER,
            {"title": "Dritter Befund (synthetisch)", "status": "unreviewed"},
        )
        with self.assertRaises(ApprovalStale):
            self._prepare()
        self.assertEqual(0, len(self._drafts()))

    def test_missing_facts_rejected(self) -> None:
        for finding_id in self.finding_ids:
            SiteVisitStore(self.root).update_finding(
                self.visit_id, fx.OWNER, finding_id, {"status": "confirmed"}
            )
        with self.assertRaises(ApprovalStale):
            self._prepare()
        self.assertEqual(0, len(self._drafts()))

    def test_approval_must_be_literal_true(self) -> None:
        for bad in (False, "yes", 1, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self._prepare(approved=bad)
        self.assertEqual(0, len(self._drafts()))

    def test_foreign_actor_sees_no_link(self) -> None:
        with self.assertRaisesRegex(ValueError, "case link not found"):
            self._prepare(actor="cf-fremd")
        self.assertEqual(0, len(self._drafts()))

    def test_revoked_rights_denied(self) -> None:
        contact_id = str(
            fx.make_contact(self.root, actor="cf-other")["contact_id"]
        )
        ContactStore(self.root).share(contact_id, [], "cf-other", [fx.OWNER])
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=self.case_id,
            contact_id=contact_id,
            visit_id=self.visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        ContactStore(self.root).share(contact_id, [], "cf-other", [])
        with self.assertRaisesRegex(ValueError, "not shared"):
            prepare_hint_draft(
                fx.OWNER, link["link_id"], str(hint["hint_id"]),
                True, NOW, self.root,
            )

    def test_no_compose_permission_denied(self) -> None:
        reader = "cf-leser"
        ContactStore(self.root).share(
            self.contact_id, [], fx.OWNER, [reader]
        )
        MailCaseStore(self.root).add_participant(
            fx.OWNER, self.case_id, local_user_id=reader,
            permissions=("read",),
        )
        visit_id = fx.make_visit(self.root, reader, completed=True)
        link = fx.confirm_link(
            self.root,
            reader,
            case_id=self.case_id,
            contact_id=self.contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        hint = FollowupEvaluator(self.root).evaluate(
            reader, link["link_id"], NOW
        )
        assert hint is not None
        with self.assertRaises(PermissionError):
            prepare_hint_draft(
                reader, link["link_id"], str(hint["hint_id"]),
                True, NOW, self.root,
            )

    def test_missing_contact_email_refused(self) -> None:
        contact_id = str(
            fx.make_contact(self.root, display_name="Ohne Mail", email="")["contact_id"]
        )
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=self.case_id,
            contact_id=contact_id,
            visit_id=self.visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        with self.assertRaisesRegex(ValueError, "email"):
            prepare_hint_draft(
                fx.OWNER, link["link_id"], str(hint["hint_id"]),
                True, NOW, self.root,
            )
        self.assertEqual(0, len(self._drafts()))

    def test_invoice_unknown_never_claims_created(self) -> None:
        result = self._prepare()
        self.assertTrue(result["created"])
        body = self._drafts()[0]["body"]
        self.assertNotIn("ist erstellt", body)
        self.assertIn("keine Aussage", body)

    def _body_with_invoice(self, status: str, number: str) -> str:
        invoice_id = "cf-inv-" + re.sub(r"[^a-z0-9]+", "-", status.lower())
        fx.make_invoice_row(
            self.root, self.contact_id, invoice_id,
            status=status, invoice_number=number,
        )
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=self.case_id,
            contact_id=self.contact_id,
            visit_id=self.visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            invoice_id=invoice_id,
            now=NOW,
        )
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        result = prepare_hint_draft(
            fx.OWNER, link["link_id"], str(hint["hint_id"]),
            True, NOW, self.root,
        )
        self.assertTrue(result["created"])
        bodies = {
            draft["id"]: str(draft["body"]) for draft in self._drafts()
        }
        self.assertIn(result["draft_id"], bodies)
        return bodies[result["draft_id"]]

    def test_invoice_draft_sentence_for_draft_state(self) -> None:
        body = self._body_with_invoice("draft", "RE-2026-007")
        self.assertIn("erst ein Entwurf", body)
        self.assertNotIn("ist erstellt", body)

    def test_invoice_created_only_for_finalized_state(self) -> None:
        body = self._body_with_invoice("open", "RE-2026-008")
        self.assertIn("RE-2026-008", body)
        self.assertIn("ist erstellt", body)

    def test_invoice_unexpected_state_never_claims_created(self) -> None:
        for status, number in (("cancelled", "RE-2026-009"), ("open", "")):
            with self.subTest(status=status, number=number):
                body = self._body_with_invoice(status, number)
                self.assertNotIn("ist erstellt", body)
                self.assertIn("keine Aussage", body)

    def test_diverged_evidence_fails_closed(self) -> None:
        real = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, self.link["link_id"], NOW
        )
        assert real is not None
        mutated = dict(real, hint_id="f" * 64)
        with mock.patch.object(
            FollowupEvaluator, "evaluate", side_effect=[real, mutated]
        ):
            with self.assertRaises(ApprovalStale):
                self._prepare()
        self.assertEqual(0, len(self._drafts()))

    def test_concurrent_prepare_creates_single_draft(self) -> None:
        import threading

        barrier = threading.Barrier(4)
        results: list = []

        def attempt():
            barrier.wait(timeout=30)
            results.append(self._prepare())

        threads = [threading.Thread(target=attempt) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
            self.assertFalse(thread.is_alive())
        self.assertEqual(4, len(results))
        self.assertEqual(1, len({item["draft_id"] for item in results}))
        self.assertEqual(1, sum(1 for item in results if item["created"]))
        self.assertEqual(1, len(self._drafts()))


class MailDraftWebTest(unittest.TestCase):
    password = "contextflow-mail-draft-test-password"

    def setUp(self) -> None:
        self._now_patch = mock.patch(
            "app.contextflow.web._now", return_value=NOW
        )
        self._now_patch.start()
        self.addCleanup(self._now_patch.stop)
        from app import app as flask_app
        from app.db import ensure_auth_database, get_db
        from app.password_security import hash_password

        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: flask_app.config.get(key)
            for key in (
                "DATABASE",
                "DOCUMENT_ROOT",
                "TESTING",
                "TEST_CSRF_PROTECTION",
                "CONTEXTFLOW_ENABLED",
            )
        }
        flask_app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
        )
        with flask_app.app_context():
            ensure_auth_database()
            db = get_db()
            for username, admin in (("admin", 1), ("berta", 0)):
                db.execute(
                    "INSERT INTO user(username,password,display_name,is_admin,"
                    "is_disabled,auth_version,created_at,updated_at)"
                    " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (
                        username,
                        hash_password(self.password),
                        username.title(),
                        admin,
                    ),
                )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.admin = self.login("admin")
        self.berta = self.login("berta")
        contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        case_id = fx.make_mail_case(self.root, "admin")
        visit_id = fx.make_visit(self.root, "admin", completed=True)
        self.link = fx.confirm_link(
            self.root,
            "admin",
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        hint = FollowupEvaluator(self.root).evaluate(
            "admin", self.link["link_id"], NOW
        )
        assert hint is not None
        self.hint_id = str(hint["hint_id"])
        self.case_id = case_id

    def tearDown(self) -> None:
        from app import app as flask_app

        flask_app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path="/contextflow/") -> str:
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def login(self, username: str):
        from app import app as flask_app

        client = flask_app.test_client()
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": self.csrf(client, "/auth/login"),
            },
        )
        return client

    def _post(self, client, **overrides):
        data = {
            "link_id": self.link["link_id"],
            "expected_hint_id": self.hint_id,
            "approved": "yes",
            "_csrf_token": self.csrf(client),
        }
        data.update(overrides)
        return client.post("/contextflow/mail-draft", data=data)

    def test_route_redirects_to_mail_editor(self) -> None:
        response = self._post(self.admin)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/documents/mail/reader", response.location)
        self.assertIn(self.case_id, response.location)
        drafts = MailCaseStore(self.root).get_case(
            "admin", self.case_id)["drafts"]
        self.assertEqual(1, len(drafts))
        self.assertEqual("draft", drafts[0]["status"])

    def test_route_replay_no_duplicate(self) -> None:
        first = self._post(self.admin)
        second = self._post(self.admin)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        drafts = MailCaseStore(self.root).get_case(
            "admin", self.case_id)["drafts"]
        self.assertEqual(1, len(drafts))

    def test_route_denied_and_stale(self) -> None:
        denied = self._post(self.berta)
        self.assertEqual(denied.status_code, 400)
        self.assertIn("case link not found", denied.get_data(as_text=True))
        stale = self._post(self.admin, expected_hint_id="0" * 64)
        self.assertEqual(stale.status_code, 409)
        missing = self._post(self.admin, approved="no")
        self.assertEqual(missing.status_code, 400)

    def test_route_without_token_rejected(self) -> None:
        response = self.admin.post(
            "/contextflow/mail-draft",
            data={
                "link_id": self.link["link_id"],
                "expected_hint_id": self.hint_id,
                "approved": "yes",
            },
        )
        self.assertIn(response.status_code, (400, 403))
        drafts = MailCaseStore(self.root).get_case(
            "admin", self.case_id)["drafts"]
        self.assertEqual(0, len(drafts))

    def test_dashboard_offers_draft_action(self) -> None:
        body = self.admin.get("/contextflow/").get_data(as_text=True)
        self.assertIn('action="/contextflow/mail-draft"', body)
        self.assertIn("wird nicht automatisch versendet", body)


if __name__ == "__main__":
    unittest.main()
