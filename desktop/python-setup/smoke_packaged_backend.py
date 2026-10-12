"""Smoke-test the frozen desktop backend without a system Python dependency."""
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    binary = Path(sys.argv[1]).resolve()
    if not binary.is_file():
        raise SystemExit(f"Missing packaged backend: {binary}")

    with tempfile.TemporaryDirectory(prefix="simpleoffice-smoke-") as tmp:
        root = Path(tmp)
        env = os.environ.copy()
        env.update({
            "SIMPLEOFFICE_DATA_DIR": str(root / "data"),
            "SIMPLEOFFICE_INSTANCE_DIR": str(root / "data" / "instance"),
            "SIMPLEOFFICE_DOCUMENT_ROOT": str(root / "documents"),
            "SIMPLEOFFICE_PORT": "18765",
            "SIMPLEOFFICE_HOST": "127.0.0.1",
            "SIMPLEOFFICE_BACKGROUND_INDEX": "0",
            "SIMPLEOFFICE_OSM_INDEX": "0",
            "SIMPLEOFFICE_DATALOGGER": "0",
            "SIMPLEOFFICE_LOG_STDERR_ONLY": "1",
            "PATH": os.pathsep.join(filter(None, (str(binary.parent), os.environ.get("PATH", "")))),
            "PYTHONHOME": "",
            "PYTHONPATH": "",
        })
        # Keep captured output outside the instance directory: Windows
        # PyInstaller child processes can retain inherited log handles after
        # the parent exits, blocking deletion of the temporary data directory.
        with tempfile.TemporaryFile(mode="w+b") as log:
            proc = subprocess.Popen(
                [str(binary)], cwd=tmp, env=env, stdout=log,
                stderr=subprocess.STDOUT,
            )
            failure = None
            try:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        failure = f"Backend exited with code {proc.returncode}"
                        break
                    try:
                        with urllib.request.urlopen(
                            "http://127.0.0.1:18765/", timeout=2
                        ) as response:
                            if 200 <= response.status < 400:
                                print(f"Packaged backend HTTP smoke passed: {response.status}")
                                break
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(1)
                else:
                    failure = "Packaged backend did not respond within 90 seconds"
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                if failure:
                    log.flush()
                    log.seek(0)
                    log.seek(max(0, log.seek(0, os.SEEK_END) - 120000))
                    output = log.read().decode("utf-8", errors="replace")
                    print("=== Packaged backend stdout/stderr ===", file=sys.stderr)
                    print(output[-30000:] if output else "(no backend output)", file=sys.stderr)
                    print("=== End packaged backend output ===", file=sys.stderr)
            if failure:
                raise RuntimeError(failure)


if __name__ == "__main__":
    main()
