#!/usr/bin/env python3
"""Bounded Android runtime smoke gate for the packaged Chaquopy app (#811).

Two modes, both honest about what they prove:

* Contract mode (default, runs in CI): verifies that the built APK packages
  the ``android_runtime`` startup module with the real
  ``cryptography.hazmat.bindings._rust`` preflight import and the logcat
  smoke marker. This is a packaging precondition, NOT a runtime proof.
* Device mode (``--run-emulator``, owner/hardware runs only): requires
  exactly one attached ARM64 (``arm64-v8a``) device, clears logcat before
  install/launch, launches the packaged app, and requires the fresh
  ``_rust`` import marker from this exact launch in logcat within a bounded
  timeout. Every ``adb`` call is bounded. Diagnostics are sanitized before
  they are stored.

The CI job stays in contract mode: the GitHub-hosted ``ubuntu-24.04`` runner
is x86_64 while the supported APK is arm64-v8a-only, so an ARM64 emulator
there would need full CPU emulation without KVM and cannot meet a bounded,
repeatable CI timeout. Device/emulator validation remains an explicit,
documented hardware acceptance step (see docs/ANDROID_INTEGRATION.md).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from zipfile import BadZipFile, ZipFile

SUPPORTED_ABI = "arm64-v8a"
SUPPORTED_PYTHON = "3.14"
PACKAGE = "de.simpleoffice4me.android"
LAUNCH_ACTIVITY = f"{PACKAGE}/.BootstrapActivity"
RUNTIME_MODULE = "android_runtime.py"
MAX_PACKAGED_RUNTIME_BYTES = 2 * 1024 * 1024
RUST_IMPORT = "cryptography.hazmat.bindings._rust"
RUST_SMOKE_MARKER = "SIMPLEOFFICE_ANDROID_RUST_IMPORT_OK"
FAILURE_SIGNALS = (
    "Start fehlgeschlagen",
    "Traceback (most recent call last)",
    "ImportError",
    "ModuleNotFoundError",
    "PyLong_Type",
    "dlopen failed",
    "UnsatisfiedLinkError",
)
DEFAULT_TIMEOUT_S = 600
BOOT_TIMEOUT_S = 300

_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LONG_SECRET = re.compile(r"\b[0-9a-zA-Z+/=_-]{32,}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def sanitize_logcat(text: str) -> str:
    """Redact tokens, UUIDs and emails; keep import markers and errors."""
    redacted = _UUID.sub("<uuid>", text)
    redacted = _EMAIL.sub("<email>", redacted)
    redacted = _LONG_SECRET.sub(
        lambda match: match.group(0)
        if RUST_SMOKE_MARKER in match.group(0)
        or match.group(0).startswith(("SIMPLEOFFICE_ANDROID", "de.simpleoffice"))
        else "<secret>",
        redacted,
    )
    return redacted[-100_000:]


def find_packaged_runtime_sources(apk: Path) -> dict[str, bytes]:
    """Return source/bytecode for the runtime module in APK and Chaquopy IMY."""
    found: dict[str, bytes] = {}
    with ZipFile(apk) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            if item.filename.endswith("/" + RUNTIME_MODULE):
                if item.file_size <= MAX_PACKAGED_RUNTIME_BYTES:
                    found[item.filename] = archive.read(item)
                continue
            if not item.filename.endswith(".imy") or item.file_size > 128 * 1024 * 1024:
                continue
            try:
                with ZipFile(archive.open(item)) as bundled:
                    for member in bundled.infolist():
                        if member.is_dir() or not member.filename.endswith(
                            (RUNTIME_MODULE, RUNTIME_MODULE + "c")
                        ):
                            continue
                        if member.file_size <= MAX_PACKAGED_RUNTIME_BYTES:
                            found[f"{item.filename}!/{member.filename}"] = bundled.read(member)
            except BadZipFile:
                # Other Chaquopy IMY archives need not be nested ZIPs.
                continue
    return found


def check_packaged_runtime_contract(apk: Path) -> dict:
    """Fail closed when the APK cannot carry the real _rust startup import."""
    failures: list[str] = []
    try:
        sources = find_packaged_runtime_sources(apk)
    except (BadZipFile, OSError, ValueError) as exc:
        return {"ok": False, "failures": [f"APK is not readable: {type(exc).__name__}"]}
    if not sources:
        failures.append(f"APK packages no {RUNTIME_MODULE}; startup import is unverifiable")
    for entry, source in sources.items():
        if b"_verify_android_cryptography_extension" not in source:
            failures.append(f"{entry}: missing _rust preflight import wiring")
        if RUST_IMPORT.encode("ascii") not in source:
            failures.append(f"{entry}: missing real import of {RUST_IMPORT}")
        if RUST_SMOKE_MARKER.encode("ascii") not in source:
            failures.append(f"{entry}: missing logcat smoke marker for device runs")
    return {
        "ok": not failures,
        "failures": failures,
        "entries": sorted(sources),
    }


def evaluate_logcat(logcat: str) -> dict:
    """Decide device validation from logcat; never infer success."""
    marker_found = RUST_SMOKE_MARKER in logcat
    signals = sorted({signal for signal in FAILURE_SIGNALS if signal in logcat})
    validated = marker_found and not signals
    if not marker_found:
        reason = "smoke marker absent: packaged app did not prove the _rust import"
    elif signals:
        reason = f"failure signals present despite marker: {', '.join(signals)}"
    else:
        reason = "packaged app logged the real _rust import without failure signals"
    return {
        "device_validated": validated,
        "marker_found": marker_found,
        "failure_signals": signals,
        "reason": reason,
    }


def _adb(*arguments: str, timeout: int = 30, serial: str | None = None) -> str:
    """Run one bounded ``adb`` call; ``serial`` pins it to exactly one device."""
    command = ["adb"]
    if serial:
        command += ["-s", serial]
    command += list(arguments)
    return subprocess.check_output(
        command, text=True, timeout=timeout, stderr=subprocess.STDOUT
    )


def _connected_devices() -> list[str]:
    """Serials of attached devices in state ``device``; the call is bounded."""
    output = _adb("devices", timeout=30)
    serials = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _device_abi(serial: str) -> str:
    """Reported CPU ABI of exactly one attached device; the call is bounded."""
    return _adb(
        "shell", "getprop", "ro.product.cpu.abi", timeout=30, serial=serial
    ).strip()


def _wait_for_boot(deadline: float, serial: str) -> bool:
    while time.monotonic() < deadline:
        try:
            if (
                _adb(
                    "shell", "getprop", "sys.boot_completed", timeout=30, serial=serial
                ).strip()
                == "1"
            ):
                return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            pass
        time.sleep(5)
    return False


def _poll_logcat_for_marker(deadline: float, serial: str) -> str:
    latest = ""
    while time.monotonic() < deadline:
        try:
            latest = _adb("logcat", "-d", "-v", "brief", timeout=30, serial=serial)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            time.sleep(5)
            continue
        if RUST_SMOKE_MARKER in latest:
            return latest
        time.sleep(5)
    return latest


def run_device_smoke(apk: Path, output: Path, timeout_s: int) -> dict:
    """Launch the packaged app on exactly one ARM64 device and check logcat.

    Logcat is cleared after boot and before install/launch, so any smoke
    marker found later is from this exact launch, not a previous run.
    """
    if shutil.which("adb") is None:
        return {
            "device_attempted": True,
            "device_validated": False,
            "reason": "adb is not on PATH; attach an ARM64 emulator or device first",
        }
    deadline = time.monotonic() + timeout_s
    boot_deadline = min(deadline, time.monotonic() + BOOT_TIMEOUT_S)
    try:
        _adb("wait-for-device", timeout=max(1, int(boot_deadline - time.monotonic())))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return {
            "device_attempted": True,
            "device_validated": False,
            "reason": f"no Android device/emulator reachable: {type(exc).__name__}",
        }
    try:
        serials = _connected_devices()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return {
            "device_attempted": True,
            "device_validated": False,
            "reason": f"could not list attached Android devices: {type(exc).__name__}",
        }
    if not serials:
        return {
            "device_attempted": True,
            "device_validated": False,
            "reason": "no Android device/emulator attached; attach exactly one",
        }
    if len(serials) > 1:
        return {
            "device_attempted": True,
            "device_validated": False,
            "reason": (
                f"multiple Android devices attached ({len(serials)}); "
                "attach exactly one ARM64 device or emulator"
            ),
        }
    serial = serials[0]
    try:
        abi = _device_abi(serial)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return {
            "device_attempted": True,
            "device_validated": False,
            "reason": f"could not read device CPU ABI: {type(exc).__name__}",
        }
    if abi != SUPPORTED_ABI:
        return {
            "device_attempted": True,
            "device_validated": False,
            "device_serial": serial,
            "device_abi": abi,
            "reason": (
                f"device ABI is {abi or 'unknown'}; owner-run mode requires "
                f"exactly one {SUPPORTED_ABI} device or emulator"
            ),
        }
    if not _wait_for_boot(boot_deadline, serial):
        return {
            "device_attempted": True,
            "device_validated": False,
            "device_serial": serial,
            "device_abi": abi,
            "reason": "emulator did not finish boot within the bounded wait",
        }
    try:
        _adb("shell", "am", "force-stop", PACKAGE, timeout=30, serial=serial)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pass
    try:
        _adb("logcat", "-c", timeout=30, serial=serial)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return {
            "device_attempted": True,
            "device_validated": False,
            "device_serial": serial,
            "device_abi": abi,
            "reason": f"could not clear logcat before install/launch: {type(exc).__name__}",
        }
    try:
        _adb("install", "-r", str(apk.resolve()), timeout=180, serial=serial)
        _adb("shell", "am", "start", "-W", "-n", LAUNCH_ACTIVITY, timeout=120, serial=serial)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return {
            "device_attempted": True,
            "device_validated": False,
            "device_serial": serial,
            "device_abi": abi,
            "reason": f"install/launch of the packaged app failed: {type(exc).__name__}",
        }
    try:
        raw_logcat = _poll_logcat_for_marker(deadline, serial)
    finally:
        try:
            _adb("shell", "am", "force-stop", PACKAGE, timeout=30, serial=serial)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            pass
    verdict = evaluate_logcat(raw_logcat)
    (output / "device-logcat.txt").write_text(
        sanitize_logcat(raw_logcat), encoding="utf-8"
    )
    return {
        "device_attempted": True,
        "device_serial": serial,
        "device_abi": abi,
        **verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/android/runtime-smoke"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    parser.add_argument(
        "--run-emulator",
        action="store_true",
        help="Attempt the bounded install/launch/logcat run on the attached "
        "ARM64 emulator or device. Omit in CI: contract check only.",
    )
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 3600:
        parser.exit(2, "Timeout must be within 1..3600 seconds\n")
    args.output.mkdir(parents=True, exist_ok=True)

    contract = check_packaged_runtime_contract(args.apk)
    report: dict = {
        "apk": str(args.apk),
        "supported_abi": SUPPORTED_ABI,
        "supported_python": SUPPORTED_PYTHON,
        "smoke_marker": RUST_SMOKE_MARKER,
        "contract_ok": contract["ok"],
        "contract": contract,
        "device_attempted": False,
        "device_validated": False,
        "reason": "emulator_not_run: contract mode proves packaging only, "
        "not on-device runtime validation",
    }
    if args.run_emulator:
        device = run_device_smoke(args.apk, args.output, args.timeout)
        report.update(device)
        if contract["ok"] and device.get("device_validated"):
            report["reason"] = (
                "packaged app started and logged the real _rust import "
                "without failure signals"
            )
    (args.output / "runtime-smoke-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in ("contract_ok", "device_attempted",
          "device_validated", "reason")}, indent=2, sort_keys=True))
    if not contract["ok"]:
        return 1
    if args.run_emulator and not report["device_validated"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
