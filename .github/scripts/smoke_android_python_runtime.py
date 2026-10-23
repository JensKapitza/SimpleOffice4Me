#!/usr/bin/env python3
"""Install an ARM64 APK and require its embedded Python backend to answer."""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


PACKAGE = "de.simpleoffice4me.android"
HOST_PORT = 18765
BACKEND_PORT = 8765


def _adb(*arguments: str) -> str:
    result = subprocess.run(
        ["adb", *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    return result.stdout


def _backend_responds() -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{HOST_PORT}/", timeout=2) as response:
            return 200 <= response.status < 500
    except HTTPError as error:
        return 200 <= error.code < 500
    except (OSError, URLError, TimeoutError):
        return False


def _wait_for_backend(timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _backend_responds():
            print("PASS: Android Python runtime, cryptography _rust extension, and Flask backend started")
            return
        time.sleep(1)
    raise AssertionError(f"Android backend did not answer within {timeout_seconds} seconds")


def _print_startup_diagnostics() -> None:
    result = subprocess.run(
        ["adb", "logcat", "-d", "-t", "500"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    markers = (
        "FATAL EXCEPTION",
        "SimpleOffice4Me import failed",
        "cannot locate symbol",
        "cryptography native extension",
        "AndroidRuntime",
    )
    lines = [line for line in result.stdout.splitlines() if any(marker in line for marker in markers)]
    print("Android startup diagnostics (filtered):")
    print("\n".join(lines[-80:]) if lines else "No matching Android startup error was logged.")


def smoke(apk: Path, timeout_seconds: int = 90) -> None:
    _adb("install", "-r", str(apk.resolve()))
    _adb("shell", "pm", "clear", PACKAGE)
    _adb("forward", f"tcp:{HOST_PORT}", f"tcp:{BACKEND_PORT}")
    try:
        _adb("shell", "monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(2)
        # Cancel the optional system account chooser on the clean emulator.
        subprocess.run(["adb", "shell", "input", "keyevent", "4"], check=False, timeout=15)
        try:
            _wait_for_backend(timeout_seconds)
        except AssertionError:
            _print_startup_diagnostics()
            raise
    finally:
        subprocess.run(["adb", "shell", "am", "force-stop", PACKAGE], check=False, timeout=15)
        subprocess.run(["adb", "forward", "--remove", f"tcp:{HOST_PORT}"], check=False, timeout=15)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if args.timeout < 30 or args.timeout > 180:
        parser.error("--timeout must be between 30 and 180 seconds")
    if not args.apk.is_file():
        parser.error("APK file does not exist")
    smoke(args.apk, args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
