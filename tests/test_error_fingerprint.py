import unittest

from app.access_control import error_fingerprint


class ErrorFingerprintTests(unittest.TestCase):
    def test_dynamic_paths_share_fingerprint_for_same_endpoint(self):
        first = error_fingerprint(
            "UndefinedError", "contacts.detail", "GET", "/contacts/123"
        )
        second = error_fingerprint(
            "UndefinedError", "contacts.detail", "GET", "/contacts/999999"
        )
        self.assertEqual(first, second)

    def test_endpoint_method_or_exception_changes_fingerprint(self):
        baseline = error_fingerprint(
            "UndefinedError", "contacts.detail", "GET", "/contacts/123"
        )
        self.assertNotEqual(
            baseline,
            error_fingerprint("ValueError", "contacts.detail", "GET", "/contacts/123"),
        )
        self.assertNotEqual(
            baseline,
            error_fingerprint("UndefinedError", "contacts.detail", "POST", "/contacts/123"),
        )
        self.assertNotEqual(
            baseline,
            error_fingerprint("UndefinedError", "contacts.edit", "GET", "/contacts/123"),
        )

    def test_path_remains_fallback_when_endpoint_is_unknown(self):
        first = error_fingerprint("RuntimeError", "", "GET", "/one")
        second = error_fingerprint("RuntimeError", "", "GET", "/two")
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
