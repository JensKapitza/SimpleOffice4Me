"""Check the packaged backend smoke test leaves its temp data dir unlocked."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "desktop" / "python-setup" / "smoke_packaged_backend.py"


class PackagedSmokeTests(unittest.TestCase):
    def test_backend_cwd_is_outside_temporary_data_directory(self):
        spec = importlib.util.spec_from_file_location("simpleoffice_packaged_smoke", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root) / "simpleoffice-python"
            binary.touch()
            process = MagicMock()
            process.poll.return_value = None
            response = MagicMock()
            response.status = 200
            response.__enter__.return_value = response
            with patch.object(module.sys, "argv", [str(SCRIPT), str(binary)]), \
                 patch.object(module.subprocess, "Popen", return_value=process) as popen, \
                 patch.object(module.urllib.request, "urlopen", return_value=response):
                module.main()
            self.assertEqual(popen.call_args.kwargs["cwd"], str(binary.parent))
            env = popen.call_args.kwargs["env"]
            temp_data = Path(env["SIMPLEOFFICE_DATA_DIR"]).parent
            self.assertNotEqual(Path(popen.call_args.kwargs["cwd"]), temp_data)
            process.terminate.assert_called_once()
            process.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
