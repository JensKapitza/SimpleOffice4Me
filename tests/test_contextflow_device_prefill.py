"""ContextFlow device prefill: finding/asset ids, confirmed handoff.

Run with the project venv (``.venv``) and TEMP/TMP pointing to a short local
path, e.g. ``C:\\Users\\Jens Kapitza\\AppData\\Local\\Temp``. Execution by
Codex is pending; nothing here was run in this session.

Production contract (authoritative): ``SiteVisitStore.add_asset`` and
``add_finding`` return the FULL visit record — the new ids are read back
from ``record["assets"]`` / ``record["findings"]``.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app import app
from app.contact_store import ContactStore
from app.contextflow.actions import ApprovalStale, approve_task
from app.contextflow.adapters import ContextflowReads
from app.contextflow.demo import seed_demo
from app.contextflow.evaluator import FollowupEvaluator
from app.db import ensure_auth_database, get_db
from app.mail_case_store import MailCaseStore
from app.password_security import hash_password
from app.site_visit_store import SiteVisitStore
from app.todo_store import TodoStore

from tests import contextflow_fixtures as fx

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=BERLIN)


def _visit_with_scanner(root: Path, actor: str) -> tuple[str, str, str]:
    """Completed visit: Buchscanner asset + one finding bound to it."""
    store = SiteVisitStore(root)
    record = store.create(
        actor,
        {
            "title": "Ortstermin Scanner (synthetisch)",
            "customer": fx.CONTACT_NAME,
            "site": "Werkstatt (synthetisch)",
            "address": "Musterweg 1, 12345 Musterstadt",
            "visit_date": fx.VISIT_DATE,
            "notes": "Buchscanner prüfen (synthetisch)",
        },
    )
    visit_id = str(record["visit_id"])
    after_asset = store.add_asset(
        visit_id,
        actor,
        {"name": "Buchscanner (synthetisch)", "kind": "Buchscanner"},
    )
    asset_id = str(after_asset["assets"][-1]["asset_id"])
    after_finding = store.add_finding(
        visit_id,
        actor,
        {
            "title": "Einzug blockiert (synthetisch)",
            "status": "unreviewed",
            "asset_id": asset_id,
        },
    )
    finding_id = str(after_finding["findings"][-1]["finding_id"])
    store.add_finding(
        visit_id, actor, {"title": "Ohne Gerät (synthetisch)", "status": "unreviewed"}
    )
    store.update(visit_id, actor, {"status": "completed"})
    return visit_id, asset_id, finding_id


def _link(root: Path, actor: str, visit_id: str) -> dict:
    contact_id = str(fx.make_contact(root, actor)["contact_id"])
    case_id = fx.make_mail_case(root, actor)
    return fx.confirm_link(
        root,
        actor,
        case_id=case_id,
        contact_id=contact_id,
        visit_id=visit_id,
        completed_on=(NOW - timedelta(days=5)).date().isoformat(),
        now=NOW,
    )


class DevicePrefillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_adapter_exposes_asset_ids_without_credentials(self) -> None:
        visit_id, asset_id, _ = _visit_with_scanner(self.root, fx.OWNER)
        summary = ContextflowReads(self.root).read_visit(fx.OWNER, visit_id)["summary"]
        bound = [item for item in summary["findings"] if item.get("asset_id")]
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0]["asset_id"], asset_id)
        for asset in summary["assets"]:
            self.assertNotIn("credentials", asset)
        self.assertTrue(any(row["asset_id"] == asset_id for row in summary["assets"]))

    def test_evaluator_device_context_only_for_referenced_assets(self) -> None:
        visit_id, asset_id, _ = _visit_with_scanner(self.root, fx.OWNER)
        link = _link(self.root, fx.OWNER, visit_id)
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        devices = hint.get("device_context", [])
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["asset_id"], asset_id)
        self.assertIn("Buchscanner", devices[0]["name"])
        self.assertTrue(devices[0]["url"].endswith(f"#asset-{asset_id}"))

    def test_evaluator_no_device_without_asset_refs(self) -> None:
        visit_id = fx.make_visit(self.root, completed=True)
        link = _link(self.root, fx.OWNER, visit_id)
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        self.assertEqual(hint.get("device_context", []), [])

    def test_approve_with_confirmed_device_prefills_task(self) -> None:
        visit_id, asset_id, _ = _visit_with_scanner(self.root, fx.OWNER)
        link = _link(self.root, fx.OWNER, visit_id)
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        result = approve_task(
            fx.OWNER,
            link["link_id"],
            str(hint["hint_id"]),
            True,
            NOW,
            self.root,
            confirmed_asset_id=asset_id,
            confirm_device=True,
        )
        self.assertTrue(result["created"])
        self.assertEqual(result["asset_id"], asset_id)
        self.assertIn("Buchscanner", result["task"].get("description", ""))
        self.assertIn("noch nichts erledigt", result["task"].get("description", ""))

    def test_approve_device_without_tick_rejected(self) -> None:
        visit_id, asset_id, _ = _visit_with_scanner(self.root, fx.OWNER)
        link = _link(self.root, fx.OWNER, visit_id)
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        with self.assertRaises(ValueError):
            approve_task(
                fx.OWNER,
                link["link_id"],
                str(hint["hint_id"]),
                True,
                NOW,
                self.root,
                confirmed_asset_id=asset_id,
                confirm_device=False,
            )

    def test_approve_unknown_or_foreign_device_rejected(self) -> None:
        visit_id, _, _ = _visit_with_scanner(self.root, fx.OWNER)
        link = _link(self.root, fx.OWNER, visit_id)
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        with self.assertRaises(ValueError):
            approve_task(
                fx.OWNER,
                link["link_id"],
                str(hint["hint_id"]),
                True,
                NOW,
                self.root,
                confirmed_asset_id="kein-geraet",
                confirm_device=True,
            )
        other_visit, _, _ = _visit_with_scanner(self.root, fx.OWNER)
        after = SiteVisitStore(self.root).add_asset(
            other_visit, fx.OWNER, {"name": "Fremdscanner (synthetisch)"}
        )
        other_asset = str(after["assets"][-1]["asset_id"])
        with self.assertRaises(ValueError):
            approve_task(
                fx.OWNER,
                link["link_id"],
                str(hint["hint_id"]),
                True,
                NOW,
                self.root,
                confirmed_asset_id=other_asset,
                confirm_device=True,
            )
        self.assertEqual(len(TodoStore(self.root).items(fx.OWNER)), 0)

    def test_demo_seeds_scanner_with_device_context(self) -> None:
        seeded = seed_demo(self.root)
        self.assertIn("asset_id", seeded)
        hint = FollowupEvaluator(self.root).evaluate(
            str(seeded["actor"]), str(seeded["link_id"]), datetime.now(BERLIN)
        )
        assert hint is not None
        devices = hint.get("device_context", [])
        self.assertEqual(len(devices), 1)
        self.assertIn("Buchscanner", devices[0]["name"])

    def _plain_link(self) -> dict:
        """Link with one pending finding and no devices."""
        visit_id = fx.make_visit(self.root, completed=True)
        return _link(self.root, fx.OWNER, visit_id)

    def _approve(self, link: dict, hint_id: str, **overrides):
        params = {
            "actor": fx.OWNER,
            "link_id": link["link_id"],
            "expected_hint_id": hint_id,
            "approved": True,
            "now": NOW,
            "root": self.root,
        }
        params.update(overrides)
        return approve_task(**params)

    def test_replay_after_evidence_change_returns_same_task(self) -> None:
        link = self._plain_link()
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        first = self._approve(link, str(hint["hint_id"]))
        self.assertTrue(first["created"])
        # Evidence evolves after the task was committed (new finding).
        SiteVisitStore(self.root).add_finding(
            str(link["visit_id"]),
            fx.OWNER,
            {"title": "Spaeterer Befund (synthetisch)", "status": "unreviewed"},
        )
        changed = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert changed is not None
        self.assertNotEqual(changed["hint_id"], hint["hint_id"])
        # Replaying the exact original approval still resolves idempotently.
        replay = self._approve(link, str(hint["hint_id"]))
        self.assertFalse(replay["created"])
        self.assertEqual(replay["task"]["id"], first["task"]["id"])
        self.assertEqual(len(TodoStore(self.root).items(fx.OWNER)), 1)

    def test_replay_after_rights_revoked_denied(self) -> None:
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
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        hint = FollowupEvaluator(self.root).evaluate(
            manager, link["link_id"], NOW
        )
        assert hint is not None
        hint_id = str(hint["hint_id"])
        created = approve_task(
            manager, link["link_id"], hint_id, True, NOW, self.root
        )
        self.assertTrue(created["created"])
        # Benign replay with rights intact still resolves to the same task.
        replay = approve_task(
            manager, link["link_id"], hint_id, True, NOW, self.root
        )
        self.assertFalse(replay["created"])
        self.assertEqual(replay["task"]["id"], created["task"]["id"])
        # Revoke contact and mail rights, then replay the same approval.
        ContactStore(self.root).share(contact_id, [], fx.OWNER, readers=[])
        MailCaseStore(self.root).remove_participant(
            fx.OWNER, case_id, participant_id
        )
        with self.assertRaises((PermissionError, ValueError)):
            approve_task(
                manager, link["link_id"], hint_id, True, NOW, self.root
            )
        self.assertEqual(len(TodoStore(self.root).items(manager)), 1)

    def test_stale_before_first_creation_rejected(self) -> None:
        link = self._plain_link()
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        # No task committed yet; the source changes first.
        SiteVisitStore(self.root).add_finding(
            str(link["visit_id"]),
            fx.OWNER,
            {"title": "Dazwischengekommen (synthetisch)", "status": "unreviewed"},
        )
        with self.assertRaises(ApprovalStale):
            self._approve(link, str(hint["hint_id"]))
        self.assertEqual(len(TodoStore(self.root).items(fx.OWNER)), 0)

    def test_multiple_devices_idempotent_per_choice(self) -> None:
        visit_id, scanner_id, _ = _visit_with_scanner(self.root, fx.OWNER)
        store = SiteVisitStore(self.root)
        after = store.add_asset(
            visit_id, fx.OWNER, {"name": "Ersatzlüfter (synthetisch)", "kind": "Lüfter"}
        )
        fan_id = str(after["assets"][-1]["asset_id"])
        store.add_finding(
            visit_id,
            fx.OWNER,
            {
                "title": "Lüfter laut (synthetisch)",
                "status": "unreviewed",
                "asset_id": fan_id,
            },
        )
        link = _link(self.root, fx.OWNER, visit_id)
        hint = FollowupEvaluator(self.root).evaluate(
            fx.OWNER, link["link_id"], NOW
        )
        assert hint is not None
        hint_id = str(hint["hint_id"])
        task_scanner = self._approve(
            link, hint_id, confirmed_asset_id=scanner_id, confirm_device=True
        )
        task_fan = self._approve(
            link, hint_id, confirmed_asset_id=fan_id, confirm_device=True
        )
        self.assertTrue(task_scanner["created"])
        self.assertTrue(task_fan["created"])
        self.assertNotEqual(
            task_scanner["task"]["id"], task_fan["task"]["id"]
        )
        replay_scanner = self._approve(
            link, hint_id, confirmed_asset_id=scanner_id, confirm_device=True
        )
        replay_fan = self._approve(
            link, hint_id, confirmed_asset_id=fan_id, confirm_device=True
        )
        self.assertFalse(replay_scanner["created"])
        self.assertFalse(replay_fan["created"])
        self.assertEqual(replay_scanner["task"]["id"], task_scanner["task"]["id"])
        self.assertEqual(replay_fan["task"]["id"], task_fan["task"]["id"])
        self.assertEqual(len(TodoStore(self.root).items(fx.OWNER)), 2)


class DevicePrefillWebTest(unittest.TestCase):
    """Date-robust web tests: seed relative to the real today, use routes."""

    password = "contextflow-device-web-test-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE",
                "DOCUMENT_ROOT",
                "TESTING",
                "TEST_CSRF_PROTECTION",
                "CONTEXTFLOW_ENABLED",
            )
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.admin = self.login("admin")
        self.today = datetime.now(BERLIN).date()
        # Keep default quiet hours from making the dashboard fixture time-dependent.
        self.now = datetime.combine(self.today, time(12, 0), tzinfo=BERLIN)
        contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        case_id = fx.make_mail_case(self.root, "admin")
        store = SiteVisitStore(self.root)
        record = store.create(
            "admin",
            {
                "title": "Ortstermin Webgeräte (synthetisch)",
                "customer": fx.CONTACT_NAME,
                "site": "Werkstatt (synthetisch)",
                "address": "Musterweg 1, 12345 Musterstadt",
                "visit_date": "2026-10-20T10:00",
                "notes": "Zwei Geräte prüfen (synthetisch)",
            },
        )
        self.visit_id = str(record["visit_id"])
        after_first = store.add_asset(
            self.visit_id,
            "admin",
            {"name": "Buchscanner (synthetisch)", "kind": "Buchscanner"},
        )
        self.scanner_id = str(after_first["assets"][-1]["asset_id"])
        after_second = store.add_asset(
            self.visit_id,
            "admin",
            {"name": "Ersatzlüfter (synthetisch)", "kind": "Lüfter"},
        )
        self.fan_id = str(after_second["assets"][-1]["asset_id"])
        store.add_finding(
            self.visit_id,
            "admin",
            {
                "title": "Einzug blockiert (synthetisch)",
                "status": "unreviewed",
                "asset_id": self.scanner_id,
            },
        )
        store.add_finding(
            self.visit_id,
            "admin",
            {
                "title": "Lüfter laut (synthetisch)",
                "status": "unreviewed",
                "asset_id": self.fan_id,
            },
        )
        store.update(self.visit_id, "admin", {"status": "completed"})
        self.link = fx.confirm_link(
            self.root,
            "admin",
            case_id=case_id,
            contact_id=contact_id,
            visit_id=self.visit_id,
            completed_on=(self.today - timedelta(days=5)).isoformat(),
            now=self.now,
        )
        hint = FollowupEvaluator(self.root).evaluate(
            "admin", self.link["link_id"], self.now
        )
        assert hint is not None
        self.hint_id = str(hint["hint_id"])

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path="/contextflow/") -> str:
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def login(self, username: str):
        client = app.test_client()
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": self.csrf(client, "/auth/login"),
            },
        )
        return client

    def test_dashboard_lists_both_devices_as_radios(self) -> None:
        response = self.admin.get("/contextflow/")
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn("Buchscanner (synthetisch)".encode(), body)
        self.assertIn("Ersatzlüfter (synthetisch)".encode(), body)
        self.assertIn(b'type="radio"', body)
        self.assertIn(self.scanner_id.encode(), body)
        self.assertIn("unbekannt".encode("utf-8"), body)
        self.assertIn(fx.CASE_TITLE.encode(), body)
        self.assertIn(self.link["link_id"].encode(), body)
        self.assertNotIn(b"confirmed_asset_id\" value=\"" + self.scanner_id.encode() + b"\" type=\"hidden", body)

    def test_approve_with_device_replay_safe(self) -> None:
        payload = {
            "link_id": self.link["link_id"],
            "expected_hint_id": self.hint_id,
            "approved": "yes",
            "confirmed_asset_id": self.scanner_id,
            "confirm_device": "yes",
            "_csrf_token": self.csrf(self.admin),
        }
        self.assertEqual(
            self.admin.post("/contextflow/approve", data=payload).status_code, 302
        )
        rows = TodoStore(self.root).items("admin")
        self.assertEqual(len(rows), 1)
        self.assertIn("Buchscanner", rows[0].get("description", ""))
        self.assertEqual(
            self.admin.post("/contextflow/approve", data=payload).status_code, 302
        )
        self.assertEqual(len(TodoStore(self.root).items("admin")), 1)

    def test_approve_device_without_tick_rejected(self) -> None:
        response = self.admin.post(
            "/contextflow/approve",
            data={
                "link_id": self.link["link_id"],
                "expected_hint_id": self.hint_id,
                "approved": "yes",
                "confirmed_asset_id": self.scanner_id,
                "_csrf_token": self.csrf(self.admin),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(TodoStore(self.root).items("admin")), 0)


if __name__ == "__main__":
    unittest.main()
