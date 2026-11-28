"""Exercise the real APK NFC finder on a disposable emulator without active NFC."""
from __future__ import annotations

import argparse
from http.client import HTTPConnection, HTTPException
import json
import re
import subprocess
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from tools.android_emulator_smoke import _adb

APP = "de.simpleoffice4me.android"
ORIGIN = "http://127.0.0.1:8765"
LOOKUP = "/inventory/nfc-find"
FIXTURE = "04AA12FF"
READER_FALLBACK = re.compile(r"No direct NFC reader|Kein NFC-Direktleser|Enable NFC in Android|NFC in den Android-Einstellungen")


def webview_port(serial):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        port = None
        ready = False
        try:
            pids = _adb("shell", "pidof", APP, serial=serial, timeout=5).split()
            if not pids or not pids[0].isdecimal():
                time.sleep(1)
                continue
            port = int(_adb("forward", "tcp:0", f"localabstract:webview_devtools_remote_{pids[0]}",
                            serial=serial, timeout=5).strip())
            http = HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                http.request("GET", "/json/list")
                reply = http.getresponse()
                ready = reply.status == 200 and any(
                    item.get("type") == "page" for item in json.loads(reply.read(65536))
                )
            finally:
                http.close()
            if ready:
                return port
        except (OSError, HTTPException, ValueError, subprocess.SubprocessError):
            pass
        finally:
            if port is not None and not ready:
                _adb("forward", "--remove", f"tcp:{port}", serial=serial, timeout=5)
        time.sleep(1)
    raise AssertionError("No app WebView became available over the exact ADB socket")


def wait_for_native_bridge(page):
    # The APK injects the shim in WebViewClient.onPageFinished. A visible
    # HTML control after DOMContentLoaded does not prove that callback ran.
    try:
        page.wait_for_function(
            "() => location.pathname === '/inventory'"
            " && typeof window.SimpleOfficeNativeNfc?.start === 'function'",
        )
    except PlaywrightTimeoutError:
        raise AssertionError("Missing native NFC bridge after inventory navigation") from None


def verify(page):
    page.wait_for_url(f"{ORIGIN}/**", timeout=120000)
    page.goto(f"{ORIGIN}/inventory", wait_until="domcontentloaded")
    panel = page.locator("#inventory-nfc-find")
    button = panel.locator("[data-nfc-find-scan]")
    status = panel.locator("[data-nfc-find-status]")
    uid = panel.locator("#nfc-find-uid")
    button.wait_for(state="visible")
    wait_for_native_bridge(page)
    requests = []

    def seen(request):
        if request.url == ORIGIN + LOOKUP:
            requests.append(request.method)

    page.on("request", seen)
    button.click()
    status.filter(has_text=READER_FALLBACK).wait_for()
    assert not requests, "NFC failure incorrectly triggered a lookup"
    if "Direktleser" in status.inner_text() or "direct NFC reader" in status.inner_text():
        assert uid.evaluate("(field) => document.activeElement === field"), "Fallback did not focus the UID field"
    print("Android NFC finder displays the native no-reader/disabled fallback", flush=True)
    uid.fill(FIXTURE)
    with page.expect_response(lambda reply: reply.url == ORIGIN + LOOKUP and reply.request.method == "POST") as result:
        panel.locator('button[type="submit"]').click()
    reply = result.value
    assert reply.status == 200 and reply.request.post_data_json == {"uid": FIXTURE}, "Manual UID did not reach lookup"
    assert reply.json()["matches"] == [], "Disposable empty inventory unexpectedly matched a UID"
    status.filter(has_text=re.compile(r"No item has this tag|Kein Gegenstand mit diesem Tag")).wait_for()
    assert requests == ["POST"] and page.url == f"{ORIGIN}/inventory", "Fallback sent extra requests or navigated"
    assert page.locator("#nfc-id").input_value() == "", "Manual finder changed the capture form"
    print("simpleoffice_nfc_native_fallback=pass", flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--serial", required=True)
    args = cli.parse_args()
    serial = args.serial
    if not re.fullmatch(r"emulator-\d+", serial):
        cli.error("Only a fresh disposable emulator serial is accepted")
    if _adb("shell", "getprop", "ro.hardware", serial=serial).strip() not in {"ranchu", "goldfish"}:
        cli.error("Android hardware is not an emulator")
    launch = _adb("shell", "am", "start", "-W", "-a", "android.intent.action.MAIN",
                  "-c", "android.intent.category.LAUNCHER", "-n", f"{APP}/.BootstrapActivity",
                  serial=serial, timeout=45)
    if "Status: ok" not in launch:
        raise AssertionError("Android launcher did not start the APK")
    port = webview_port(serial)
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}", no_defaults=True, timeout=15000,
            )
            page, = browser.contexts[0].pages
            page.set_default_timeout(25000)
            verify(page)
    finally:
        _adb("forward", "--remove", f"tcp:{port}", serial=serial, timeout=5)


if __name__ == "__main__":
    main()
