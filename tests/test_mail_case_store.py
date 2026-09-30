import tempfile
import unittest
from pathlib import Path

from app.mail_case_store import MailCaseStore


class MailCaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MailCaseStore(self.root)
        self.case_id = self.store.create_case(
            "alice", "Angebot Müller", "acc-1", "sha512:one",
            message_id="<one@example.test>",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_creator_gets_full_permissions_and_case_is_private(self):
        case = self.store.get_case("alice", self.case_id)
        self.assertIn("read", case["permissions"])
        self.assertIn("manage_participants", case["permissions"])
        self.assertEqual("alice", case["account_owner"])
        with self.assertRaises(PermissionError):
            self.store.get_case("bob", self.case_id)

    def test_list_and_thread_lookup_require_read_permission(self):
        self.store.add_participant(
            "alice", self.case_id, local_user_id="bob", permissions={"comment"},
        )
        self.assertEqual([], self.store.list_cases("bob"))
        self.assertIsNone(
            self.store.find_thread_case("bob", in_reply_to="<one@example.test>")
        )

    def test_participant_permissions_are_server_side(self):
        self.store.add_participant(
            "alice", self.case_id, local_user_id="bob",
            permissions={"read", "comment"},
        )
        self.assertEqual(self.case_id, self.store.get_case("bob", self.case_id)["id"])
        self.store.add_comment("bob", self.case_id, "Intern geprüft.")
        with self.assertRaises(PermissionError):
            self.store.create_draft("bob", self.case_id, "x@example.test", "Re: Test", "Body")
        with self.assertRaises(PermissionError):
            self.store.add_participant("bob", self.case_id, local_user_id="mallory")

    def test_personal_read_state_is_independent_per_user(self):
        self.store.add_participant("alice", self.case_id, local_user_id="bob", permissions={"read"})
        first = self.store.mark_read("alice", self.case_id, "sha512:one")
        second = self.store.mark_read("alice", self.case_id, "sha512:one")
        self.assertEqual(first["first_read_at"], second["first_read_at"])

        bob_view = self.store.read_state("bob", self.case_id, "sha512:one")
        self.assertEqual(["local:alice"], [row["participant_reference"] for row in bob_view])
        self.assertNotIn("local:bob", [row["participant_reference"] for row in bob_view])

        self.store.mark_read("bob", self.case_id, "sha512:one")
        rows = self.store.read_state("alice", self.case_id, "sha512:one")
        self.assertEqual(
            {"local:alice", "local:bob"},
            {row["participant_reference"] for row in rows},
        )

    def test_multiple_messages_can_share_one_case_but_not_cross_account(self):
        self.store.add_message(
            "alice", self.case_id, "acc-1", "sha512:two",
            message_id="<two@example.test>", in_reply_to="<one@example.test>",
        )
        case = self.store.get_case("alice", self.case_id)
        self.assertEqual(2, len(case["messages"]))
        with self.assertRaises(PermissionError):
            self.store.add_message("alice", self.case_id, "foreign-account", "sha512:evil")

    def test_internal_comment_is_metadata_not_mail(self):
        comment_id = self.store.add_comment("alice", self.case_id, "Nur intern.")
        case = self.store.get_case("alice", self.case_id)
        self.assertEqual(comment_id, case["comments"][0]["id"])
        self.assertEqual("Nur intern.", case["comments"][0]["body"])
        self.assertEqual(1, len(case["messages"]))

    def test_draft_requires_compose_and_keeps_sender_identity_separate(self):
        draft = self.store.create_draft(
            "alice", self.case_id, "kunde@example.test", "Re: Angebot", "Hallo",
            sender_identity="alice-delegated",
        )
        row = self.store.get_case("alice", self.case_id)["drafts"][0]
        self.assertEqual(draft, row["id"])
        self.assertEqual("alice-delegated", row["sender_identity"])
        self.assertEqual("draft", row["status"])

    def test_exact_reply_headers_can_find_one_case_subject_is_irrelevant(self):
        found = self.store.find_thread_case("alice", in_reply_to="<one@example.test>")
        self.assertEqual(self.case_id, found)
        self.assertIsNone(self.store.find_thread_case("alice", in_reply_to="<unknown@example.test>"))

        other = self.store.create_case(
            "alice", "Ganz anderer Betreff", "acc-1", "sha512:other",
            message_id="<one@example.test>",
        )
        self.assertNotEqual(self.case_id, other)
        self.assertIsNone(self.store.find_thread_case("alice", references=["<one@example.test>"]))

    def test_duplicate_participants_are_rejected(self):
        self.store.add_participant(
            "alice", self.case_id, local_user_id="bob", permissions={"read"},
        )
        with self.assertRaises(ValueError):
            self.store.add_participant(
                "alice", self.case_id, local_user_id="bob", permissions={"read"},
            )
        self.store.add_participant(
            "alice", self.case_id, peer_id="peer-7", remote_user_id="remote-9",
            permissions={"read"},
        )
        with self.assertRaises(ValueError):
            self.store.add_participant(
                "alice", self.case_id, peer_id="peer-7", remote_user_id="remote-9",
                permissions={"read"},
            )

    def test_federated_participant_shape_does_not_require_local_user(self):
        participant_id = self.store.add_participant(
            "alice", self.case_id, peer_id="peer-7", remote_user_id="remote-9",
            permissions={"read", "comment"},
        )
        participants = self.store.get_case("alice", self.case_id)["participants"]
        row = next(item for item in participants if item["id"] == participant_id)
        self.assertEqual("federated_user", row["participant_type"])
        self.assertIsNone(row["local_user_id"])
        self.assertEqual("peer-7", row["peer_id"])

    def test_status_is_not_an_imap_flag(self):
        self.store.set_status("alice", self.case_id, "in_bearbeitung")
        self.assertEqual("in_bearbeitung", self.store.get_case("alice", self.case_id)["status"])
        with self.assertRaises(ValueError):
            self.store.set_status("alice", self.case_id, "\\Seen")


    def test_store_uses_existing_control_directory(self):
        self.assertEqual(
            self.root / ".simpleoffice-meta" / "mail-cases.sqlite3",
            self.store.path,
        )

    def test_participant_permissions_can_be_changed_and_participant_removed(self):
        participant_id = self.store.add_participant(
            "alice", self.case_id, local_user_id="bob", permissions={"read"},
        )
        self.store.update_participant_permissions(
            "alice", self.case_id, participant_id, {"read", "comment"},
        )
        row = next(
            item for item in self.store.get_case("alice", self.case_id)["participants"]
            if item["id"] == participant_id
        )
        self.assertEqual({"read", "comment"}, set(row["permissions"]))
        self.store.remove_participant("alice", self.case_id, participant_id)
        self.assertFalse(any(
            item["id"] == participant_id
            for item in self.store.get_case("alice", self.case_id)["participants"]
        ))
        with self.assertRaises(PermissionError):
            self.store.get_case("bob", self.case_id)

    def test_account_owner_cannot_be_removed_or_have_permissions_reduced(self):
        owner = next(
            item for item in self.store.get_case("alice", self.case_id)["participants"]
            if item["local_user_id"] == "alice"
        )
        with self.assertRaises(ValueError):
            self.store.update_participant_permissions(
                "alice", self.case_id, owner["id"], {"read"},
            )
        with self.assertRaises(ValueError):
            self.store.remove_participant("alice", self.case_id, owner["id"])

    def test_message_relation_can_be_removed_without_changing_other_case_data(self):
        self.store.add_message(
            "alice", self.case_id, "acc-1", "sha512:two",
            message_id="<two@example.test>",
        )
        self.store.mark_read("alice", self.case_id, "sha512:two")
        self.store.add_comment("alice", self.case_id, "Bleibt erhalten.")
        self.store.remove_message("alice", self.case_id, "sha512:two")
        case = self.store.get_case("alice", self.case_id)
        self.assertEqual(["sha512:one"], [row["mail_reference"] for row in case["messages"]])
        self.assertEqual("Bleibt erhalten.", case["comments"][0]["body"])
        self.assertEqual([], self.store.read_state("alice", self.case_id, "sha512:two"))

    def test_draft_can_be_updated_but_not_without_compose_permission(self):
        draft_id = self.store.create_draft(
            "alice", self.case_id, "kunde@example.test", "Re: Angebot", "Alt",
        )
        self.store.update_draft(
            "alice", self.case_id, draft_id,
            "neu@example.test", "Re: Neu", "Neuer Text", cc="cc@example.test",
        )
        row = self.store.get_case("alice", self.case_id)["drafts"][0]
        self.assertEqual("neu@example.test", row["recipients_to"])
        self.assertEqual("cc@example.test", row["recipients_cc"])
        self.assertEqual("Neuer Text", row["body"])
        self.store.add_participant(
            "alice", self.case_id, local_user_id="bob", permissions={"read"},
        )
        with self.assertRaises(PermissionError):
            self.store.update_draft(
                "bob", self.case_id, draft_id,
                "x@example.test", "Re: X", "Nicht erlaubt",
            )

    def test_case_for_message_is_account_scoped_and_requires_read(self):
        self.assertEqual(
            self.case_id,
            self.store.case_for_message("alice", "acc-1", "sha512:one"),
        )
        self.assertIsNone(
            self.store.case_for_message("alice", "other-account", "sha512:one")
        )
        self.assertIsNone(
            self.store.case_for_message("bob", "acc-1", "sha512:one")
        )

    def test_thread_lookup_is_scoped_to_mail_account(self):
        other = self.store.create_case(
            "alice", "Anderes Konto", "acc-2", "sha512:other-account",
            message_id="<same@example.test>",
        )
        same_account = self.store.create_case(
            "alice", "Gleiches Konto", "acc-1", "sha512:same-account",
            message_id="<same@example.test>",
        )
        self.assertEqual(
            same_account,
            self.store.find_thread_case(
                "alice", account_id="acc-1", in_reply_to="<same@example.test>",
            ),
        )
        self.assertEqual(
            other,
            self.store.find_thread_case(
                "alice", account_id="acc-2", references=["<same@example.test>"],
            ),
        )


    def test_send_request_is_frozen_until_account_owner_reviews_it(self):
        self.store.add_participant(
            "alice", self.case_id, local_user_id="bob",
            permissions={"read", "compose", "send_request", "manage_mail"},
        )
        draft_id = self.store.create_draft(
            "bob", self.case_id, "kunde@example.test", "Re: Angebot", "Antwort",
            cc="team@example.test", bcc="audit@example.test",
        )
        self.store.request_draft_send("bob", self.case_id, draft_id)
        row = self.store.get_case("bob", self.case_id)["drafts"][0]
        self.assertEqual("ready", row["status"])
        with self.assertRaises(ValueError):
            self.store.update_draft(
                "bob", self.case_id, draft_id,
                "changed@example.test", "Manipuliert", "Nach Freigabe geändert",
            )
        with self.assertRaises(PermissionError):
            self.store.review_draft_send(
                "bob", self.case_id, draft_id, approve=True,
            )

        self.store.review_draft_send(
            "alice", self.case_id, draft_id, approve=True,
        )
        sending = self.store.begin_draft_send("alice", self.case_id, draft_id)
        self.assertEqual("sending", sending["status"])
        self.assertEqual("acc-1", sending["account_id"])
        self.store.complete_draft_send(
            "alice", self.case_id, draft_id,
            "sha512:sent-message", "<sent@example.test>",
        )
        case = self.store.get_case("alice", self.case_id)
        self.assertEqual("sent", case["drafts"][0]["status"])
        self.assertEqual(
            "sha512:sent-message",
            case["messages"][-1]["mail_reference"],
        )
        self.assertEqual("outbound", case["messages"][-1]["direction"])

    def test_failed_send_requires_explicit_new_request(self):
        draft_id = self.store.create_draft(
            "alice", self.case_id, "kunde@example.test", "Re: Angebot", "Antwort",
        )
        self.store.request_draft_send("alice", self.case_id, draft_id)
        self.store.review_draft_send("alice", self.case_id, draft_id, approve=True)
        self.store.begin_draft_send("alice", self.case_id, draft_id)
        self.store.fail_draft_send("alice", self.case_id, draft_id, "TimeoutError")
        self.assertEqual(
            "failed", self.store.get_case("alice", self.case_id)["drafts"][0]["status"]
        )
        with self.assertRaises(ValueError):
            self.store.begin_draft_send("alice", self.case_id, draft_id)
        self.store.request_draft_send("alice", self.case_id, draft_id)
        self.assertEqual(
            "ready", self.store.get_case("alice", self.case_id)["drafts"][0]["status"]
        )


    def test_draft_attachment_metadata_is_bounded_visible_and_frozen_after_request(self):
        draft_id = self.store.create_draft(
            "alice", self.case_id, "kunde@example.test", "Re: Angebot", "Antwort",
        )
        attachment = {
            "document_id": "doc-1",
            "filename": "angebot.pdf",
            "content_type": "application/pdf",
            "size": 1234,
            "sha256": "a" * 64,
            "scan_id": "scan-1",
        }
        self.store.add_draft_attachment("alice", self.case_id, draft_id, attachment)
        case = self.store.get_case("alice", self.case_id)
        self.assertEqual([attachment], case["drafts"][0]["attachments"])
        self.assertEqual(
            attachment,
            self.store.draft_attachment("alice", self.case_id, draft_id, "doc-1"),
        )

        self.store.request_draft_send("alice", self.case_id, draft_id)
        with self.assertRaises(ValueError):
            self.store.add_draft_attachment(
                "alice", self.case_id, draft_id,
                {**attachment, "document_id": "doc-2", "sha256": "b" * 64},
            )
        with self.assertRaises(ValueError):
            self.store.remove_draft_attachment(
                "alice", self.case_id, draft_id, "doc-1",
            )

    def test_draft_attachment_can_be_removed_while_editable(self):
        draft_id = self.store.create_draft(
            "alice", self.case_id, "kunde@example.test", "Re: Angebot", "Antwort",
        )
        attachment = {
            "document_id": "doc-remove",
            "filename": "notiz.txt",
            "content_type": "text/plain",
            "size": 7,
            "sha256": "c" * 64,
            "scan_id": "scan-remove",
        }
        self.store.add_draft_attachment("alice", self.case_id, draft_id, attachment)
        self.store.remove_draft_attachment(
            "alice", self.case_id, draft_id, "doc-remove",
        )
        self.assertEqual([], self.store.get_case("alice", self.case_id)["drafts"][0]["attachments"])

    def test_case_projection_includes_personal_read_state_per_message(self):
        self.store.add_participant(
            "alice", self.case_id, local_user_id="bob", permissions={"read"},
        )
        self.store.mark_read("alice", self.case_id, "sha512:one")
        self.store.mark_read("bob", self.case_id, "sha512:one")
        message = self.store.get_case("alice", self.case_id)["messages"][0]
        self.assertEqual(
            {"local:alice", "local:bob"},
            {row["participant_reference"] for row in message["read_state"]},
        )
        for row in message["read_state"]:
            self.assertTrue(row["first_read_at"])
            self.assertTrue(row["last_read_at"])


    def test_draft_rejects_header_injection_before_approval(self):
        with self.assertRaises(ValueError):
            self.store.create_draft(
                "alice", self.case_id, "kunde@example.test\r\nBcc: evil@example.test",
                "Re: Angebot", "Antwort",
            )
        with self.assertRaises(ValueError):
            self.store.create_draft(
                "alice", self.case_id, "kunde@example.test",
                "Re: Angebot\nX-Evil: yes", "Antwort",
            )
        with self.assertRaises(ValueError):
            self.store.create_draft(
                "alice", self.case_id, "kunde@example.test",
                "Re: Angebot", "Antwort", sender_identity="identity\r\nX-Evil: yes",
            )


if __name__ == "__main__":
    unittest.main()
