from __future__ import annotations

import ast
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import app_config_store


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
        self.assertIn('federation_network', source)
        self.assertNotIn('SIMPLEOFFICE_FEDERATION_LAN_ADDRESS', source)
        self.assertIn('def set_lan_addresses(', source)
        self.assertIn('make_server(\n            "0.0.0.0",', source)

    def test_runtime_accepts_native_lan_addresses_without_restarting_server(self):
        spec = importlib.util.spec_from_file_location("android_runtime_lan_test", RUNTIME)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                app_config_store, "settings_path",
                return_value=Path(tmp) / "app-config.json",
            ):
                self.assertTrue(runtime.set_lan_addresses(
                    "192.168.4.22,192.168.4.22,10.0.0.8,172.20.10.1,100.64.0.2,169.254.8.9"
                ))
                self.assertEqual(
                    "192.168.4.22,10.0.0.8,172.20.10.1,100.64.0.2,169.254.8.9",
                    app_config_store.get("federation_network", "lan_addresses", ""),
                )
                runtime.set_lan_addresses("")
                self.assertEqual(
                    "",
                    app_config_store.get("federation_network", "lan_addresses", ""),
                )

    def test_runtime_receives_native_identity_and_registers_local_auth_before_serving(self):
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('account_email: str = ""', source)
        self.assertIn('bootstrap_token: str = ""', source)
        self.assertIn('SIMPLEOFFICE_ANDROID_ACCOUNT', source)
        self.assertIn('SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN', source)
        self.assertIn('from app import android_auth', source)
        self.assertIn('app.register_blueprint(android_auth.bp)', source)
        self.assertIn('timedelta(days=365)', source)

    def test_embedded_python_symbols_are_global_before_app_native_extensions_import(self):
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertLess(
            source.index("    _promote_android_python_symbols()"),
            source.index("from app import app"),
        )
        self.assertLess(
            source.index("    _verify_android_cryptography_extension()"),
            source.index("from app import app"),
        )
        spec = importlib.util.spec_from_file_location("android_runtime_symbols_test", RUNTIME)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)

        with mock.patch("ctypes.CDLL") as load_library:
            runtime._promote_android_python_symbols()

        import ctypes
        import os

        load_library.assert_called_once_with(
            f"libpython{sys.version_info.major}.{sys.version_info.minor}.so",
            mode=ctypes.RTLD_GLOBAL | os.RTLD_NOW,
        )

    def test_cold_start_sets_environment_and_links_native_modules_before_any_app_import(self):
        spec = importlib.util.spec_from_file_location("android_runtime_cold_order_test", RUNTIME)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        steps = []
        real_import = __import__

        def track_import(name, *args, **kwargs):
            if name == "app":
                self.assertEqual(["symbols", "rust"], steps[:2])
                self.assertEqual("synthetic@example.com", os.environ["SIMPLEOFFICE_ANDROID_ACCOUNT"])
                self.assertEqual("x" * 64, os.environ["SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN"])
                steps.append("app")
            return real_import(name, *args, **kwargs)

        from contextlib import ExitStack
        from app import app as flask_app
        cwd = Path.cwd()
        path_before = sys.path[:]
        try:
            with ExitStack() as fixtures:
                fixtures.enter_context(mock.patch.dict(os.environ))
                fixtures.enter_context(mock.patch("builtins.__import__", side_effect=track_import))
                fixtures.enter_context(mock.patch.object(runtime, "_promote_android_python_symbols",
                                                         side_effect=lambda: steps.append("symbols")))
                fixtures.enter_context(mock.patch.object(runtime, "_verify_android_cryptography_extension",
                                                         side_effect=lambda: steps.append("rust")))
                fixtures.enter_context(mock.patch.object(runtime, "_android_speech_class", return_value=mock.Mock()))
                fixtures.enter_context(mock.patch.object(runtime, "make_server"))
                fixtures.enter_context(mock.patch.object(runtime.threading, "Thread"))
                fixtures.enter_context(mock.patch.dict(flask_app.config))
                fixtures.enter_context(mock.patch.dict(flask_app.blueprints,
                                                       android_auth=True, android_paired_folder=True))
                fixtures.enter_context(mock.patch.object(flask_app, "register_blueprint"))
                fixtures.enter_context(mock.patch.object(app_config_store, "put"))
                self.assertTrue(runtime.start(str(ROOT), account_email="synthetic@example.com",
                                              bootstrap_token="x" * 64))
        finally:
            os.chdir(cwd)
            sys.path[:] = path_before
        self.assertIn("app", steps)
        self.assertEqual(["symbols", "rust", "app"], steps[:3])

    def test_cryptography_rust_extension_preflight_uses_the_expected_module(self):
        spec = importlib.util.spec_from_file_location("android_runtime_crypto_test", RUNTIME)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)

        with mock.patch("importlib.import_module") as import_module:
            runtime._verify_android_cryptography_extension()

        import_module.assert_called_once_with("cryptography.hazmat.bindings._rust")

    def test_chaquopy_offline_speech_forwards_only_the_native_contract(self):
        spec = importlib.util.spec_from_file_location("android_runtime_speech_contract_test", RUNTIME)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        native = mock.Mock()
        java = mock.Mock()
        java.jclass.return_value = native
        native.render.return_value = True
        native.status.return_value = "ready"
        native.refresh.return_value = True
        native.voicesJson.return_value = '[{"id":"de-installed","language":"de-DE","label":"Deutsch"}, {"id":23}]'
        output = Path("/synthetic/private/speech.wav")
        with mock.patch.dict(sys.modules, java=java):
            self.assertTrue(runtime._render_android_speech("Synthetisch", "de_DE", output, "de-installed", 1.2, .8))
            native.render.assert_called_once_with("Synthetisch", "de-DE", str(output), "de-installed", 1.2, .8)
            self.assertEqual([{"id": "de-installed", "language": "de-DE", "label": "Deutsch"}],
                             runtime._android_speech_voices())
            self.assertEqual("ready", runtime._android_speech_status())
            native.refresh.assert_not_called()
            self.assertTrue(runtime._refresh_android_speech())
            native.refresh.assert_called_once_with()
            java.jclass.assert_called_with("de.simpleoffice4me.android.AndroidOfflineSpeech")

    def test_chaquopy_unavailable_speech_inventory_fails_closed(self):
        spec = importlib.util.spec_from_file_location("android_runtime_speech_unavailable_test", RUNTIME)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        native = mock.Mock()
        native.voicesJson.return_value = '{"incorrect":"shape"}'
        native.status.return_value = "unknown"
        with mock.patch.object(runtime, "_android_speech_class", return_value=native):
            self.assertEqual([], runtime._android_speech_voices())
            self.assertEqual("unavailable", runtime._android_speech_status())
            native.voicesJson.side_effect = RuntimeError("opaque Android error")
            native.status.side_effect = RuntimeError("opaque Android error")
            self.assertEqual([], runtime._android_speech_voices())
            self.assertEqual("unavailable", runtime._android_speech_status())
            native.render.assert_not_called()
            native.refresh.assert_not_called()

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
        with (mock.patch.object(runtime, "_configure_environment") as configure,
              mock.patch.object(runtime, "_configure_lan_addresses") as configure_lan):
            self.assertTrue(runtime.start(str(ROOT), "", "user@example.com", "x" * 64, "10.0.0.8"))
            configure.assert_called_once_with(ROOT.resolve(), "", "user@example.com", "x" * 64)
            configure_lan.assert_called_once_with("10.0.0.8")

            configure.reset_mock()
            configure_lan.reset_mock()
            self.assertTrue(runtime.start(str(ROOT)))
            configure.assert_not_called()
            configure_lan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
