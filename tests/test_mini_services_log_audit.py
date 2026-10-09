"""Regression coverage for the Mini Services diagnostic exception audit (#330)."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tools.mini_services_log_audit import findings


class MiniServicesLogAuditTests(TestCase):
    def _findings(self, source: str) -> list[str]:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text(source, encoding="utf-8")
            # The scanner reports repository-relative paths.
            from unittest.mock import patch
            with patch("tools.mini_services_log_audit.ROOT", Path(directory)):
                return findings(path)

    def test_raw_exception_in_json_is_rejected(self):
        result = self._findings(
            "def handler():\n"
            "    try:\n"
            "        work()\n"
            "    except Exception as exc:\n"
            "        return jsonify({'error': str(exc)})\n"
        )
        self.assertEqual(len(result), 1)
        self.assertIn("jsonify", result[0])

    def test_formatted_exception_in_log_is_rejected(self):
        result = self._findings(
            "def handler():\n"
            "    try:\n"
            "        work()\n"
            "    except Exception as exc:\n"
            "        logger.warning(f'failed: {exc}')\n"
        )
        self.assertEqual(len(result), 1)
        self.assertIn("warning", result[0])

    def test_exception_type_only_is_allowed(self):
        result = self._findings(
            "def handler():\n"
            "    try:\n"
            "        work()\n"
            "    except Exception as exc:\n"
            "        logger.warning(type(exc).__name__)\n"
        )
        self.assertEqual(result, [])
