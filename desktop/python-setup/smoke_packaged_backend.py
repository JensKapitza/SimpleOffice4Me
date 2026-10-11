"""Smoke-test the frozen desktop backend without a system Python dependency."""
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

binary = Path(sys.argv[1]).resolve()
if not binary.is_file():
    raise SystemExit(f"Missing packaged backend: {binary}")
with tempfile.TemporaryDirectory(prefix="simpleoffice-smoke-") as tmp:
    env = os.environ.copy()
    env.update({
        "SIMPLEOFFICE_DATA_DIR": str(Path(tmp) / "data"),
        "SIMPLEOFFICE_INSTANCE_DIR": str(Path(tmp) / "data" / "instance"),
        "SIMPLEOFFICE_DOCUMENT_ROOT": str(Path(tmp) / "documents"),
        "SIMPLEOFFICE_PORT": "18765",
        "SIMPLEOFFICE_HOST": "127.0.0.1",
        "SIMPLEOFFICE_BACKGROUND_INDEX": "0",
        "SIMPLEOFFICE_OSM_INDEX": "0",
        "SIMPLEOFFICE_DATALOGGER": "0",
        "PATH": str(binary.parent),
        "PYTHONHOME": "",
        "PYTHONPATH": "",
    })
    proc = subprocess.Popen([str(binary)], cwd=tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Backend exited with code {proc.returncode}")
            try:
                with urllib.request.urlopen("http://127.0.0.1:18765/", timeout=2) as response:
                    if response.status < 500:
                        print(f"Packaged backend HTTP smoke passed: {response.status}")
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(1)
        else:
            raise RuntimeError("Packaged backend did not respond within 90 seconds")
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
