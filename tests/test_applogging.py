import os
import unittest
from unittest.mock import patch

from app.applogging import LOG_OUTPUT_LIMIT, initlogging, redact


class ApplicationLoggingTests(unittest.TestCase):
    def test_oversized_log_record_is_bounded(self):
        cleaned = redact("A" * 1_000_000)
        self.assertEqual(LOG_OUTPUT_LIMIT, len(cleaned))

    @patch("app.applogging.dictConfig")
    def test_stream_only_mode_does_not_create_file_handler(self, configure):
        with patch.dict(os.environ, {"SIMPLEOFFICE_LOG_STDERR_ONLY": "1"}, clear=False):
            initlogging()

        config = configure.call_args.args[0]
        self.assertEqual(["wsgi"], config["root"]["handlers"])
        self.assertEqual({"wsgi"}, set(config["handlers"]))
        self.assertEqual(["redact"], config["handlers"]["wsgi"]["filters"])

    @patch("app.applogging.dictConfig")
    def test_default_mode_keeps_bounded_rotating_file_handler(self, configure):
        with patch.dict(os.environ, {}, clear=True):
            initlogging()

        config = configure.call_args.args[0]
        self.assertEqual(["wsgi", "file"], config["root"]["handlers"])
        file_handler = config["handlers"]["file"]
        self.assertEqual("logging.handlers.RotatingFileHandler", file_handler["class"])
        self.assertEqual(1024 * 1024, file_handler["maxBytes"])
        self.assertEqual(3, file_handler["backupCount"])
        self.assertEqual(["redact"], file_handler["filters"])


if __name__ == "__main__":
    unittest.main()
