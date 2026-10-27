import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.file_lock import exclusive_file_lock
from app.printershare_store import PrinterShareStore, effective_retention


TEST_PRINTER = {
    "printer_id": "a" * 24,
    "name": "Office Printer",
    "driver": "Test Driver",
    "port": "TEST:",
    "is_default": True,
    "kind": "normal",
    "backend": "test",
}


class PrinterShareStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="printershare-test-secret")
        self.discover = patch("app.printershare_store.discover_printers", return_value=[dict(TEST_PRINTER)])
        self.spool = patch("app.printershare_store.spool_payload", return_value="test-spool-42")
        self.discover.start()
        self.spool_mock = self.spool.start()
        self.store = PrinterShareStore(self.root, self.app.config["SECRET_KEY"])

    def tearDown(self):
        self.spool.stop()
        self.discover.stop()
        self.temp.cleanup()

    def test_retention_ceiling_never_allows_more_storage(self):
        self.assertEqual(effective_retention("permanent", "no_store"), "no_store")
        self.assertEqual(effective_retention("permanent", "ttl"), "ttl")
        self.assertEqual(effective_retention("no_store", "permanent"), "no_store")

    def test_no_store_keeps_only_job_metadata(self):
        result = self.store.submit(
            TEST_PRINTER["printer_id"], b"private print payload",
            content_type="application/pdf", filename="test.pdf", retention_ceiling="no_store",
        )
        self.assertEqual(result["retention"], "no_store")
        self.assertFalse(result["application_archive"])
        job = self.store.jobs()[0]
        self.assertEqual(job["payload_path"], "")
        self.assertEqual(list(self.store.retained.iterdir()), [])
        self.spool_mock.assert_called_once()

    def test_sender_no_store_overrides_permanent_local_default(self):
        self.store.update_settings({"default_retention": "permanent"})
        result = self.store.submit(
            TEST_PRINTER["printer_id"], b"must not persist",
            content_type="application/pdf", retention_ceiling="no_store",
        )
        self.assertEqual(result["retention"], "no_store")
        self.assertEqual(self.store.jobs()[0]["payload_path"], "")

    def test_ttl_backlog_is_encrypted_and_can_be_removed(self):
        payload = b"secret backlog payload"
        self.store.update_settings({"default_retention": "permanent", "ttl_seconds": 3600})
        result = self.store.submit(
            TEST_PRINTER["printer_id"], payload,
            content_type="application/pdf", retention_ceiling="ttl", ttl_ceiling_seconds=600,
        )
        self.assertEqual(result["retention"], "ttl")
        self.assertGreater(result["expires_at"], int(time.time()))
        job = self.store.jobs()[0]
        retained = self.store.control / job["payload_path"]
        self.assertTrue(retained.is_file())
        self.assertNotIn(payload, retained.read_bytes())
        self.store.delete_retained(result["job_id"])
        self.assertFalse(retained.exists())
        self.assertEqual(self.store.jobs()[0]["payload_path"], "")

    def test_retained_job_can_be_printed_again(self):
        self.store.update_settings({"default_retention": "permanent"})
        result = self.store.submit(
            TEST_PRINTER["printer_id"], b"print twice",
            content_type="application/pdf", retention_ceiling="permanent",
        )
        self.spool_mock.reset_mock()
        retried = self.store.retry(result["job_id"])
        self.assertEqual(retried["status"], "spooled")
        self.spool_mock.assert_called_once()

    def test_federation_only_exposes_explicitly_shared_printers(self):
        self.store.update_settings({"federation_enabled": True})
        self.assertEqual(self.store.federation_capabilities()["printers"], [])
        self.store.set_printer(TEST_PRINTER["printer_id"], kind="label", federation_shared=True, label="Etiketten")
        capabilities = self.store.federation_capabilities()
        self.assertTrue(capabilities["enabled"])
        self.assertTrue(capabilities["retention_contract"]["ceiling_enforced"])
        self.assertTrue(capabilities["retention_contract"]["no_store_means_no_application_archive"])
        self.assertEqual(capabilities["printers"][0]["label"], "Etiketten")
        self.assertEqual(capabilities["printers"][0]["kind"], "label")

    def test_policy_revision_changes_with_retention_policy(self):
        first = self.store.policy_revision()
        self.store.update_settings({"federation_default_retention": "ttl"})
        self.assertNotEqual(first, self.store.policy_revision())

    def test_user_preferences_are_persistent_and_role_specific(self):
        label_printer = {
            **TEST_PRINTER,
            "printer_id": "b" * 24,
            "name": "Brother QL",
            "is_default": False,
            "kind": "label",
        }
        with patch(
            "app.printershare_store.discover_printers",
            return_value=[dict(TEST_PRINTER), label_printer],
        ):
            saved = self.store.set_user_preferences(
                "alice", standard_printer_id=TEST_PRINTER["printer_id"],
                label_printer_id=label_printer["printer_id"],
            )
            reopened = PrinterShareStore(self.root, self.app.config["SECRET_KEY"])
            context = reopened.user_printer_context("alice")

        self.assertEqual(TEST_PRINTER["printer_id"], saved["standard"])
        self.assertEqual(label_printer["printer_id"], saved["label"])
        self.assertEqual(TEST_PRINTER["printer_id"], context["roles"]["standard"]["printer_id"])
        self.assertEqual(label_printer["printer_id"], context["roles"]["label"]["printer_id"])
        self.assertEqual("personal", context["roles"]["label"]["source"])

    def test_policy_precedence_and_forced_printer_are_server_enforced(self):
        second = {**TEST_PRINTER, "printer_id": "c" * 24, "name": "Printer 2", "is_default": False}
        with patch(
            "app.printershare_store.discover_printers",
            return_value=[dict(TEST_PRINTER), second],
        ):
            self.store.set_user_preferences(
                "alice", standard_printer_id=TEST_PRINTER["printer_id"],
            )
            self.store.set_user_printer_policies("alice", {
                "standard": {"mode": "recommended", "printer_id": second["printer_id"]},
                "label": {"mode": "none", "printer_id": ""},
            })
            recommended = self.store.user_printer_context("alice")["roles"]["standard"]
            self.store.set_user_printer_policies("alice", {
                "standard": {"mode": "forced", "printer_id": second["printer_id"]},
                "label": {"mode": "none", "printer_id": ""},
            })
            forced = self.store.user_printer_context("alice")["roles"]["standard"]
            resolved = self.store.resolve_user_printer("alice", "standard")
            with self.assertRaisesRegex(ValueError, "erzwungenen Druckerrichtlinie"):
                self.store.resolve_user_printer("alice", "standard", TEST_PRINTER["printer_id"])

        self.assertEqual(TEST_PRINTER["printer_id"], recommended["printer_id"])
        self.assertEqual("personal", recommended["source"])
        self.assertEqual(second["printer_id"], forced["printer_id"])
        self.assertEqual(second["printer_id"], resolved["printer_id"])

    def test_user_printer_preferences_are_isolated_between_accounts(self):
        second = {**TEST_PRINTER, "printer_id": "c" * 24, "name": "Printer 2", "is_default": False}
        with patch(
            "app.printershare_store.discover_printers",
            return_value=[dict(TEST_PRINTER), second],
        ):
            self.store.set_user_preferences(
                "alice", standard_printer_id=second["printer_id"],
            )
            self.store.set_user_preferences(
                "bob", standard_printer_id=TEST_PRINTER["printer_id"],
            )
            reopened = PrinterShareStore(self.root, self.app.config["SECRET_KEY"])
            alice = reopened.resolve_user_printer("alice", "standard")
            bob = reopened.resolve_user_printer("bob", "standard")
        self.assertEqual(second["printer_id"], alice["printer_id"])
        self.assertEqual(TEST_PRINTER["printer_id"], bob["printer_id"])

    def test_forced_policy_blocks_preference_change_and_preserves_saved_value(self):
        second = {**TEST_PRINTER, "printer_id": "c" * 24, "name": "Printer 2", "is_default": False}
        with patch(
            "app.printershare_store.discover_printers",
            return_value=[dict(TEST_PRINTER), second],
        ):
            self.store.set_user_preferences(
                "alice", standard_printer_id=TEST_PRINTER["printer_id"],
            )
            self.store.set_user_printer_policies("alice", {
                "standard": {"mode": "forced", "printer_id": second["printer_id"]},
                "label": {"mode": "none", "printer_id": ""},
            })
            with self.assertRaisesRegex(ValueError, "Administrationsrichtlinie"):
                self.store.set_user_preferences(
                    "alice", standard_printer_id=TEST_PRINTER["printer_id"],
                )
            context = self.store.user_printer_context("alice")["roles"]["standard"]
        self.assertEqual(second["printer_id"], context["printer_id"])
        self.assertEqual(TEST_PRINTER["printer_id"], context["preference_printer_id"])

    def test_single_role_policy_preserves_other_role_and_validates_mode(self):
        self.store.set_user_printer_policies("alice", {
            "standard": {"mode": "recommended", "printer_id": TEST_PRINTER["printer_id"]},
            "label": {"mode": "none", "printer_id": ""},
        })
        updated = self.store.set_user_printer_policy("alice", "label", "none")
        self.assertEqual("none", updated["mode"])
        saved = self.store.settings()["user_printer_policies"]["alice"]
        self.assertEqual("recommended", saved["standard"]["mode"])
        with self.assertRaisesRegex(ValueError, "Unbekannte Druckerrichtlinie"):
            self.store.set_user_printer_policy("alice", "label", "invalid")
        self.assertEqual(saved, self.store.settings()["user_printer_policies"]["alice"])

    def test_missing_forced_device_fails_closed_without_fallback(self):
        self.store.set_user_printer_policies("alice", {
            "standard": {"mode": "forced", "printer_id": TEST_PRINTER["printer_id"]},
            "label": {"mode": "none", "printer_id": ""},
        })
        with patch("app.printershare_store.discover_printers", return_value=[]):
            context = self.store.user_printer_context("alice")["roles"]["standard"]
            self.assertFalse(context["available"])
            self.assertEqual("forced", context["policy"])
            with self.assertRaisesRegex(ValueError, "erzwungene Drucker ist nicht verfügbar"):
                self.store.resolve_user_printer("alice", "standard")

    def test_removed_personal_printer_uses_compatible_system_fallback(self):
        replacement = {**TEST_PRINTER, "printer_id": "e" * 24, "name": "Replacement"}
        self.store.set_user_preferences("alice", standard_printer_id=TEST_PRINTER["printer_id"])
        with patch("app.printershare_store.discover_printers", return_value=[replacement]):
            context = self.store.user_printer_context("alice")["roles"]["standard"]

        self.assertEqual(replacement["printer_id"], context["printer_id"])
        self.assertEqual("fallback", context["source"])

    def test_legacy_brother_profile_is_migrated_into_private_central_label_choices(self):
        with patch(
            "app.library.store.LibraryStore.printer_settings",
            return_value={"uri": "tcp://192.168.1.50:9100", "model": "QL-820NWB"},
        ):
            context = self.store.user_printer_context("alice")

        label = context["roles"]["label"]
        self.assertTrue(label["available"])
        self.assertEqual("label", label["options"][0]["kind"])
        self.assertEqual("brother", label["options"][0]["backend"])
        self.assertTrue(all("uri" not in printer for printer in context["printers"]))

    def test_first_printer_mutations_migrate_before_taking_write_lock(self):
        policies = {
            "standard": {"mode": "recommended", "printer_id": TEST_PRINTER["printer_id"]},
            "label": {"mode": "none", "printer_id": ""},
        }
        mutations = (
            lambda store: store.set_printer(
                TEST_PRINTER["printer_id"], kind="normal", federation_shared=False,
            ),
            lambda store: store.set_user_preferences(
                "alice", standard_printer_id=TEST_PRINTER["printer_id"],
            ),
            lambda store: store.set_user_printer_policies("alice", policies),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                store = PrinterShareStore(self.root / f"write-lock-{index}", self.app.config["SECRET_KEY"])
                lock_active = False

                @contextmanager
                def non_reentrant_lock(path):
                    nonlocal lock_active
                    if lock_active:
                        raise AssertionError("printer settings write lock was re-entered")
                    with exclusive_file_lock(path):
                        lock_active = True
                        try:
                            yield
                        finally:
                            lock_active = False

                with patch("app.printershare_store.exclusive_file_lock", side_effect=non_reentrant_lock):
                    mutate(store)


if __name__ == "__main__":
    unittest.main()
