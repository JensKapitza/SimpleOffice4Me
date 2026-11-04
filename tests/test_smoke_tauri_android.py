from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.smoke_tauri_android import capture_filtered_logcat


class AndroidSmokeDiagnosticTests(unittest.TestCase):
    def test_logcat_capture_is_filtered_and_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='ActivityManager: ANR diagnostic\n', stderr='',
            )
            with patch(
                'tools.smoke_tauri_android.subprocess.run', return_value=result,
            ) as run:
                capture_filtered_logcat(output)

            self.assertIn('ActivityManager:W', run.call_args.args[0])
            self.assertIn('AndroidRuntime:E', run.call_args.args[0])
            self.assertEqual(
                'ActivityManager: ANR diagnostic\n\nlogcat_exit_code=0\n',
                (output / 'filtered-logcat.txt').read_text(encoding='utf-8'),
            )

    def test_logcat_timeout_is_recorded_without_masking_smoke_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch(
                'tools.smoke_tauri_android.subprocess.run',
                side_effect=subprocess.TimeoutExpired(['adb', 'logcat'], 30),
            ):
                capture_filtered_logcat(output)

            contents = (output / 'filtered-logcat.txt').read_text(encoding='utf-8')
            self.assertIn('Filtered Android logcat capture unavailable: TimeoutExpired', contents)


if __name__ == '__main__':
    unittest.main()
