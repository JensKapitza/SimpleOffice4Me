"""ContextFlow Batch 1: authorized read adapters over real synthetic stores.

Run with the project venv (``.venv``) and TEMP/TMP pointing to a short local
path, e.g. ``C:\\Users\\Jens Kapitza\\AppData\\Local\\Temp``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from unittest import mock

from flask import Flask

from app.contact_store import ContactStore
from app.contextflow import adapters as adapters_module
from app.contextflow.adapters import ContextflowReads
from app.site_visit_store import SiteVisitStore
from tests import contextflow_fixtures as fx


class ContextflowReadsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.reads = ContextflowReads(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_positive_case_context_reads(self) -> None:
        contact = fx.make_contact(self.root)
        contact_id = str(contact["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root)
        todo = fx.make_todo(self.root, contact_id=contact_id)
        event = fx.make_event(self.root, contact_id=contact_id)

        mail = self.reads.read_mail_case(fx.OWNER, case_id)
        self.assertEqual(mail["summary"]["case_id"], case_id)
        self.assertEqual(mail["summary"]["title"], fx.CASE_TITLE)
        self.assertEqual(mail["summary"]["message_count"], 1)
        self.assertEqual(mail["evidence"]["type"], "mail_case")
        self.assertIn(case_id, mail["evidence"]["url"])
        self.assertTrue(mail["evidence"]["revision"])

        contact_read = self.reads.read_contact(fx.OWNER, contact_id)
        self.assertEqual(contact_read["summary"]["email"], fx.CONTACT_EMAIL)

        visit = self.reads.read_visit(fx.OWNER, visit_id)
        self.assertEqual(visit["summary"]["visit_id"], visit_id)
        self.assertEqual(visit["summary"]["visit_date"], fx.VISIT_DATE)
        self.assertEqual(len(visit["summary"]["findings"]), 1)
        self.assertEqual(
            visit["summary"]["findings"][0]["title"], fx.FINDING_TITLE
        )
        self.assertEqual(
            visit["summary"]["findings"][0]["status"], "unreviewed"
        )
        self.assertEqual(
            visit["evidence"]["url"], f"/site-visits/{visit_id}"
        )

        todos = self.reads.read_todos(fx.OWNER, contact_id=contact_id)
        self.assertEqual([item["id"] for item in todos["items"]], [todo["id"]])
        self.assertEqual(todos["evidence"][0]["url"], "/tasks/")

        window = self.reads.read_calendar(
            fx.OWNER,
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            [item["event_id"] for item in window["items"]], [event["event_id"]]
        )
        self.assertEqual(window["items"][0]["start"], fx.EVENT_START)

    def test_foreign_actor_denied_everywhere(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root)
        fx.make_todo(self.root)
        fx.make_event(self.root)

        with self.assertRaises(PermissionError):
            self.reads.read_mail_case(fx.FOREIGN, case_id)
        with self.assertRaises(ValueError):
            self.reads.read_visit(fx.FOREIGN, visit_id)
        with self.assertRaises(ValueError):
            self.reads.read_contact(fx.FOREIGN, contact_id)
        self.assertEqual(self.reads.read_todos(fx.FOREIGN)["items"], [])
        window = self.reads.read_calendar(
            fx.FOREIGN,
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(window["items"], [])

    def test_empty_actor_rejected(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root)
        bounds = (
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        for actor in ("", "   "):
            with self.assertRaises(ValueError):
                self.reads.read_mail_case(actor, case_id)
            with self.assertRaises(ValueError):
                self.reads.read_contact(actor, contact_id)
            with self.assertRaises(ValueError):
                self.reads.read_visit(actor, visit_id)
            with self.assertRaises(ValueError):
                self.reads.read_todos(actor)
            with self.assertRaises(ValueError):
                self.reads.read_calendar(actor, *bounds)
            with self.assertRaises(ValueError):
                self.reads.read_invoice_state(
                    actor,
                    contact_id=contact_id,
                    case_link_confirmed=False,
                    invoice_id="irrelevant",
                )

    def test_invoice_unknown_without_confirmed_link(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        fx.make_invoice_row(self.root, contact_id, "cf-inv-001")
        state = self.reads.read_invoice_state(
            fx.OWNER,
            contact_id=contact_id,
            case_link_confirmed=False,
            invoice_id="cf-inv-001",
        )
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(state["reason"], "no_confirmed_case_link")

    def test_invoice_unknown_without_reference(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        state = self.reads.read_invoice_state(
            fx.OWNER, contact_id=contact_id, case_link_confirmed=True
        )
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(state["reason"], "no_confirmed_invoice_reference")

    def test_invoice_requires_contact_manage_rights(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        ContactStore(self.root).share(contact_id, [], fx.OWNER, [fx.FOREIGN])
        with self.assertRaises(PermissionError):
            self.reads.read_invoice_state(
                fx.FOREIGN,
                contact_id=contact_id,
                case_link_confirmed=True,
                invoice_id="cf-inv-001",
            )

    def test_invoice_linked_draft_and_contact_mismatch(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        other_id = str(
            fx.make_contact(
                self.root,
                display_name="Anderer Kunde (synthetisch)",
                email="anderer@example.test",
            )["contact_id"]
        )
        fx.make_invoice_row(self.root, contact_id, "cf-inv-001")

        linked = self.reads.read_invoice_state(
            fx.OWNER,
            contact_id=contact_id,
            case_link_confirmed=True,
            invoice_id="cf-inv-001",
        )
        self.assertEqual(linked["state"], "linked")
        self.assertEqual(linked["invoice_id"], "cf-inv-001")
        self.assertEqual(linked["status"], "draft")
        self.assertEqual(
            linked["evidence"]["url"], "/documents/business/invoices/cf-inv-001"
        )
        self.assertTrue(linked["evidence"]["revision"])
        first_revision = str(linked["evidence"]["revision"])
        fx.make_invoice_row(
            self.root, contact_id, "cf-inv-001", invoice_number="RE-2026-002"
        )
        changed = self.reads.read_invoice_state(
            fx.OWNER,
            contact_id=contact_id,
            case_link_confirmed=True,
            invoice_id="cf-inv-001",
        )
        self.assertEqual(changed["state"], "linked")
        self.assertNotEqual(first_revision, changed["evidence"]["revision"])

        mismatch = self.reads.read_invoice_state(
            fx.OWNER,
            contact_id=other_id,
            case_link_confirmed=True,
            invoice_id="cf-inv-001",
        )
        self.assertEqual(mismatch["state"], "unknown")
        self.assertEqual(mismatch["reason"], "contact_mismatch")

        missing = self.reads.read_invoice_state(
            fx.OWNER,
            contact_id=contact_id,
            case_link_confirmed=True,
            invoice_id="cf-inv-fehlend",
        )
        self.assertEqual(missing["state"], "unknown")
        self.assertEqual(missing["reason"], "not_linked")

    def test_visit_credentials_stripped(self) -> None:
        store = SiteVisitStore(self.root)
        visit_id = fx.make_visit(self.root)
        asset = store.add_asset(visit_id, fx.OWNER, {"name": "Drucker (synthetisch)"})
        assets = [
            item
            for item in store.get(visit_id, fx.OWNER).get("assets", [])
            if isinstance(item, dict)
        ]
        asset_id = next(
            item["asset_id"]
            for item in assets
            if item.get("name") == "Drucker (synthetisch)"
        )
        self.assertTrue(asset or asset_id)
        flask_app = Flask(__name__)
        flask_app.config["SECRET_KEY"] = "cf-test-secret-key"
        with flask_app.app_context():
            store.set_credentials(
                visit_id, fx.OWNER, asset_id, "cf-techniker", "s3cret-pw-1"
            )

        visit = self.reads.read_visit(fx.OWNER, visit_id)
        dumped = json.dumps(visit, ensure_ascii=False)
        self.assertNotIn("s3cret-pw-1", dumped)
        self.assertEqual(len(visit["summary"]["findings"]), 1)

    def test_missing_objects_raise(self) -> None:
        with self.assertRaises(PermissionError):
            self.reads.read_mail_case(fx.OWNER, "mail-case-fehlend")
        with self.assertRaises(ValueError):
            self.reads.read_visit(fx.OWNER, "besuch-fehlend")
        with self.assertRaises(ValueError):
            self.reads.read_contact(fx.OWNER, "kontakt-fehlend")


    def test_invoice_post_read_rights_revoked(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        ContactStore(self.root).share(contact_id, ["cf-manager"], fx.OWNER)
        fx.make_invoice_row(self.root, contact_id, "cf-inv-002")
        real_reader = adapters_module._read_invoice_row

        def revoked_read(root: Path, invoice_id: str) -> dict:
            ContactStore(root).share(contact_id, [], fx.OWNER)
            return real_reader(root, invoice_id)

        with mock.patch(
            "app.contextflow.adapters._read_invoice_row", side_effect=revoked_read
        ):
            with self.assertRaisesRegex(
                ValueError, "contact is not shared with this user"
            ):
                self.reads.read_invoice_state(
                    "cf-manager",
                    contact_id=contact_id,
                    case_link_confirmed=True,
                    invoice_id="cf-inv-002",
                )

    def test_case_link_confirmed_requires_literal_true(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        state = self.reads.read_invoice_state(
            fx.OWNER,
            contact_id=contact_id,
            case_link_confirmed="false",  # type: ignore[arg-type]
            invoice_id="cf-inv-001",
        )
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(state["reason"], "no_confirmed_case_link")


if __name__ == "__main__":
    unittest.main()
