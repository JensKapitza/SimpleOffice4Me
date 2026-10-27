from pathlib import Path
import tempfile
import unittest

from app.resource_mail import MailResourceProvider
from app.resource_provider import ProviderError


class MailResourceProviderPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "documents"
        self.root.mkdir()
        self.provider = MailResourceProvider(
            self.root, b"test-mail-master-key-long-enough", "alice",
        )
        self.provider.store.accounts = lambda actor: [{"id": "account-1"}]

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive_path_uses_validated_account_id_below_document_root(self):
        archive = self.provider._archive_provider("account-1")

        self.assertEqual(archive.root.name, "account-1")
        self.assertEqual(archive.root.parent.parent.name, "email")
        self.assertTrue(archive.root.is_dir())
        self.assertTrue(archive.root.is_relative_to(self.root.resolve()))

    def test_invalid_account_id_is_rejected_before_archive_path_creation(self):
        with self.assertRaises(ProviderError):
            self.provider._archive_provider("../../outside")

        self.assertFalse((self.root.parent / "outside").exists())

    def test_archive_symlink_cannot_escape_document_root(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        try:
            (self.root / "email").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable")

        with self.assertRaises(ProviderError):
            self.provider._archive_provider("account-1")

        self.assertFalse((outside / "account-1").exists())


if __name__ == "__main__":
    unittest.main()
