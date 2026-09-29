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
        with self.assertRaises(PermissionError):
            self.store.get_case("bob", self.case_id)

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
        self.assertEqual([], self.store.read_state("bob", self.case_id, "sha512:one"))
        rows = self.store.read_state("alice", self.case_id, "sha512:one")
        self.assertEqual("local:alice", rows[0]["participant_reference"])

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


if __name__ == "__main__":
    unittest.main()
