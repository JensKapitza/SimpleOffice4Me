from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "android" / "apk" / "app" / "src" / "main" / "python" / "android_runtime.py"


class AndroidRuntimeTests(unittest.TestCase):
    def test_runtime_uses_bounded_request_pool(self):
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertIn("class PooledServer(WSGIServer)", source)
        self.assertIn("ThreadPoolExecutor", source)
        self.assertIn("MAX_SERVER_WORKERS = 6", source)
        self.assertIn("MAX_PENDING_REQUESTS = 12", source)
        self.assertIn("threading.BoundedSemaphore(MAX_PENDING_REQUESTS)", source)
        self.assertIn("server_class=PooledServer", source)
        self.assertIn("cancel_futures=True", source)
        self.assertNotIn("ThreadingMixIn", source)

    def test_runtime_marks_android_environment_and_remains_valid_python(self):
        source = RUNTIME.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn('os.environ["SIMPLEOFFICE_ANDROID"] = "1"', source)
        self.assertIn('os.environ["SIMPLEOFFICE_HOST"] = "127.0.0.1"', source)
        self.assertIn('os.environ["SIMPLEOFFICE_BACKGROUND_INDEX"] = "0"', source)
        self.assertIn('SIMPLEOFFICE_FEDERATION_LAN_ADDRESS', source)
        self.assertIn('def set_lan_addresses(', source)

    def test_runtime_accepts_native_lan_addresses_without_restarting_server(self):
        spec = importlib.util.spec_from_file_location("android_runtime_lan_test", RUNTIME)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)

        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("SIMPLEOFFICE_FEDERATION_LAN_ADDRESS", None)
            self.assertTrue(runtime.set_lan_addresses("192.168.4.22,192.168.4.22,10.0.0.8"))
            self.assertEqual(
                "192.168.4.22,10.0.0.8",
                os.environ["SIMPLEOFFICE_FEDERATION_LAN_ADDRESS"],
            )
            runtime.set_lan_addresses("")
            self.assertNotIn("SIMPLEOFFICE_FEDERATION_LAN_ADDRESS", os.environ)

    def test_runtime_receives_native_identity_and_registers_local_auth_before_serving(self):
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('account_email: str = ""', source)
        self.assertIn('bootstrap_token: str = ""', source)
        self.assertIn('SIMPLEOFFICE_ANDROID_ACCOUNT', source)
        self.assertIn('SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN', source)
        self.assertIn('from app import android_auth', source)
        self.assertIn('app.register_blueprint(android_auth.bp)', source)
        self.assertIn('timedelta(days=365)', source)

    def test_warm_runtime_refreshes_native_token_without_navigation_clearing_it(self):
        spec = importlib.util.spec_from_file_location("android_runtime_warm_start_test", RUNTIME)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)

        class RunningThread:
            @staticmethod
            def is_alive():
                return True

        runtime._THREAD = RunningThread()
        runtime._SERVER = object()
        with mock.patch.object(runtime, "_configure_environment") as configure:
            self.assertTrue(runtime.start(str(ROOT), "", "user@example.com", "x" * 64))
            configure.assert_called_once_with(
                ROOT.resolve(), "", "user@example.com", "x" * 64, ""
            )

            configure.reset_mock()
            self.assertTrue(runtime.start(str(ROOT)))
            configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
