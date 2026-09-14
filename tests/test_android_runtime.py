from __future__ import annotations

import ast
import unittest
from pathlib import Path


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

    def test_runtime_receives_native_identity_and_registers_local_auth_before_serving(self):
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertIn('account_email: str = ""', source)
        self.assertIn('bootstrap_token: str = ""', source)
        self.assertIn('SIMPLEOFFICE_ANDROID_ACCOUNT', source)
        self.assertIn('SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN', source)
        self.assertIn('from app import android_auth', source)
        self.assertIn('app.register_blueprint(android_auth.bp)', source)
        self.assertIn('timedelta(days=365)', source)


if __name__ == "__main__":
    unittest.main()
