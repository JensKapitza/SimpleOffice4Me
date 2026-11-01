"""ContextFlow Batch 2: persistent case links over real synthetic stores.

Run with the project venv (``.venv``) and TEMP/TMP pointing to a short local
path, e.g. ``C:\\Users\\Jens Kapitza\\AppData\\Local\\Temp``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.contact_store import ContactStore
from app.contextflow.case_links import CaseLinkStore
from app.mail_case_store import MailCaseStore

from tests import contextflow_fixtures as fx


class CaseLinkStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CaseLinkStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _objects(self, actor: str = fx.OWNER) -> tuple[str, str, str]:
        contact_id = str(fx.make_contact(self.root, actor)["contact_id"])
        case_id = fx.make_mail_case(self.root, actor)
        visit_id = fx.make_visit(self.root, actor, completed=True)
        return contact_id, case_id, visit_id

    def test_confirm_get_roundtrip(self) -> None:
        contact_id, case_id, visit_id = self._objects()
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        self.assertTrue(link["link_id"])
        self.assertTrue(link["revision"])
        self.assertEqual(link["completed_on"], "2026-10-24")
        self.assertEqual(
            link["source_revisions"].keys(), {"mail_case", "contact", "visit"}
        )
        fetched = self.store.get(fx.OWNER, link["link_id"])
        self.assertEqual(fetched, link)
        self.assertEqual(
            [row["link_id"] for row in self.store.list_links(fx.OWNER)],
            [link["link_id"]],
        )

    def test_dedupe_same_assignment(self) -> None:
        contact_id, case_id, visit_id = self._objects()
        first = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        same = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        self.assertEqual(same["link_id"], first["link_id"])
        self.assertEqual(same["revision"], first["revision"])
        changed = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-23",
        )
        self.assertEqual(changed["link_id"], first["link_id"])
        self.assertNotEqual(changed["revision"], first["revision"])
        self.assertEqual(changed["completed_on"], "2026-10-23")
        self.assertEqual(len(self.store.list_links(fx.OWNER)), 1)

    def test_foreign_actor_no_access(self) -> None:
        contact_id, case_id, visit_id = self._objects()
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        with self.assertRaises(ValueError):
            self.store.get(fx.FOREIGN, link["link_id"])
        self.assertEqual(self.store.list_links(fx.FOREIGN), [])
        with self.assertRaises((PermissionError, ValueError)):
            fx.confirm_link(
                self.root,
                fx.FOREIGN,
                case_id=case_id,
                contact_id=contact_id,
                visit_id=visit_id,
                completed_on="2026-10-24",
            )

    def test_invalid_completed_on(self) -> None:
        contact_id, case_id, visit_id = self._objects()
        for bad in (
            "",
            "24.10.2026",
            "October 24",
            "2026-13-01",
            "2999-01-01",
            "20261024",
            "2026-W43-6",
        ):
            with self.assertRaises(ValueError):
                fx.confirm_link(
                    self.root,
                    fx.OWNER,
                    case_id=case_id,
                    contact_id=contact_id,
                    visit_id=visit_id,
                    completed_on=bad,
                )

    def test_invoice_reference_must_be_confirmed(self) -> None:
        contact_id, case_id, visit_id = self._objects()
        with self.assertRaises(ValueError):
            fx.confirm_link(
                self.root,
                fx.OWNER,
                case_id=case_id,
                contact_id=contact_id,
                visit_id=visit_id,
                completed_on="2026-10-24",
                invoice_id="cf-inv-fehlend",
            )
        fx.make_invoice_row(self.root, contact_id, "cf-inv-003")
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
            invoice_id="cf-inv-003",
        )
        self.assertEqual(link["invoice_id"], "cf-inv-003")
        self.assertIn("invoice", link["source_revisions"])

    def test_visit_must_be_completed(self) -> None:
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root, completed=False)
        with self.assertRaises(ValueError):
            fx.confirm_link(
                self.root,
                fx.OWNER,
                case_id=case_id,
                contact_id=contact_id,
                visit_id=visit_id,
                completed_on="2026-10-24",
            )

    def test_missing_objects_raise(self) -> None:
        with self.assertRaises((PermissionError, ValueError)):
            fx.confirm_link(
                self.root,
                fx.OWNER,
                case_id="mail-case-fehlend",
                contact_id="kontakt-fehlend",
                visit_id="besuch-fehlend",
                completed_on="2026-10-24",
            )

    def test_no_name_linking_twins(self) -> None:
        first_id = str(fx.make_contact(self.root)["contact_id"])
        second_id = str(
            fx.make_contact(self.root, email="mueller-zweit@example.test")[
                "contact_id"
            ]
        )
        self.assertNotEqual(first_id, second_id)
        case_id = fx.make_mail_case(self.root)
        visit_id = fx.make_visit(self.root, completed=True)
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=second_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        self.assertEqual(link["contact_id"], second_id)

    def test_persistence_across_instances(self) -> None:
        contact_id, case_id, visit_id = self._objects()
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        reopened = CaseLinkStore(self.root)
        self.assertEqual(
            reopened.get(fx.OWNER, link["link_id"])["link_id"], link["link_id"]
        )


    def test_corrupt_store_raises_and_preserves_file(self) -> None:
        contact_id, case_id, visit_id = self._objects()
        link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        self.assertEqual(
            self.store.get(fx.OWNER, link["link_id"])["link_id"], link["link_id"]
        )
        raw = self.store.path.read_bytes()
        self.store.path.write_text("{kein json", encoding="utf-8")
        with self.assertRaises(ValueError):
            fx.confirm_link(
                self.root,
                fx.OWNER,
                case_id=case_id,
                contact_id=contact_id,
                visit_id=visit_id,
                completed_on="2026-10-24",
            )
        self.assertEqual(self.store.path.read_bytes(), b"{kein json")
        with self.assertRaises(ValueError):
            self.store.get(fx.OWNER, link["link_id"])
        with self.assertRaises(ValueError):
            self.store.list_links(fx.OWNER)
        self.store.path.write_text('{"links": "kein-objekt"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.list_links(fx.OWNER)
        self.store.path.write_bytes(raw)
        self.assertEqual(
            self.store.get(fx.OWNER, link["link_id"])["link_id"], link["link_id"]
        )

    def test_invoice_added_to_assignment_keeps_link_id(self) -> None:
        contact_id, case_id, visit_id = self._objects()
        plain = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        fx.make_invoice_row(self.root, contact_id, "cf-inv-020")
        with_invoice = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
            invoice_id="cf-inv-020",
        )
        self.assertEqual(with_invoice["link_id"], plain["link_id"])
        self.assertEqual(with_invoice["invoice_id"], "cf-inv-020")
        self.assertNotEqual(with_invoice["revision"], plain["revision"])
        self.assertEqual(len(self.store.list_links(fx.OWNER)), 1)

    def test_revoked_source_rights_hide_link(self) -> None:
        manager = "cf-manager"
        contact_id = str(fx.make_contact(self.root)["contact_id"])
        case_id = fx.make_mail_case(self.root)
        ContactStore(self.root).share(contact_id, [], fx.OWNER, [manager])
        participant_id = MailCaseStore(self.root).add_participant(
            fx.OWNER, case_id, local_user_id=manager, permissions=("read",)
        )
        visit_id = fx.make_visit(self.root, actor=manager, completed=True)
        link = fx.confirm_link(
            self.root,
            manager,
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on="2026-10-24",
        )
        self.assertEqual(
            self.store.get(manager, link["link_id"])["link_id"], link["link_id"]
        )
        self.assertEqual(len(self.store.list_links(manager)), 1)
        MailCaseStore(self.root).remove_participant(fx.OWNER, case_id, participant_id)
        ContactStore(self.root).share(contact_id, [], fx.OWNER, readers=[])
        with self.assertRaises((PermissionError, ValueError)):
            self.store.get(manager, link["link_id"])
        self.assertEqual(self.store.list_links(manager), [])


if __name__ == "__main__":
    unittest.main()
