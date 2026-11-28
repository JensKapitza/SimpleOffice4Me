"""Exercise a real debug APK WebView and system document picker on a fresh emulator."""
from __future__ import annotations

import argparse
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from defusedxml import ElementTree

from playwright.sync_api import sync_playwright

APP = "de.simpleoffice4me.android"
LOCAL = "http://127.0.0.1:8765"
TARGET = "/sdcard/Download/simpleoffice-sms-backup.json"
DOCS_UI = {"com.google.android.documentsui", "com.android.documentsui"}
DESIRED = "Synthetic selected backup message"
OTHER = "Synthetic unselected message"


class Device:
    def __init__(self, adb, serial):
        self.command = [adb, "-s", serial]

    def adb(self, *args, timeout=20):
        return subprocess.run([*self.command, *args], check=True, timeout=timeout,
                              capture_output=True, text=True).stdout.strip()

    def nodes(self):
        fixture = "/data/local/tmp/simpleoffice-picker-acceptance.xml"
        self.adb("shell", "uiautomator", "dump", fixture)
        content = self.adb("shell", "cat", fixture)
        return list(ElementTree.fromstring(content).iter("node"))

    def click(self, node):
        coords = [int(part) for part in re.findall(r"\d+", node.get("bounds", ""))]
        if len(coords) != 4:
            raise AssertionError("Document picker control has no visible bounds")
        x1, y1, x2, y2 = coords
        self.adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))

    def wait_node(self, predicate, label, seconds=35):
        def locate():
            return next((node for node in self.nodes() if predicate(node)), None)
        return wait_for(locate, label, seconds)

    def forwarded_socket(self):
        # Android restricts /proc/net/unix to privileged callers. Ask adb to
        # forward the exact known WebView socket instead and probe it over TCP.
        seen_process = False

        def locate():
            nonlocal seen_process
            pid = self.adb("shell", "pidof", APP).split()[0]
            if not pid.isdecimal():
                return None
            seen_process = True
            port = int(self.adb("forward", "tcp:0", f"localabstract:webview_devtools_remote_{pid}"))
            ready = False
            try:
                connection = HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    connection.request("GET", "/json/list")
                    response = connection.getresponse()
                    if response.status == 200:
                        targets = json.loads(response.read(65536))
                        ready = any(item.get("type") == "page" for item in targets)
                finally:
                    connection.close()
                return port if ready else None
            except (OSError, HTTPException, ValueError):
                return None
            finally:
                if not ready:
                    self.adb("forward", "--remove", f"tcp:{port}")

        try:
            return wait_for(locate, "debug WebView target through adb", 120)
        except AssertionError as error:
            details = "App process exists" if seen_process else "App process did not stay alive"
            print(details, flush=True)
            # Only report failure classes from the fresh synthetic emulator.
            try:
                log = self.adb("logcat", "-b", "crash", "-d", "-t", "80")
                exception_types = set(re.findall(r"\b(?:[a-zA-Z]+\.)+[A-Za-z]+(?:Exception|Error)\b", log))
                if exception_types:
                    print("Android crash classes:", ", ".join(sorted(exception_types)), flush=True)
            except (OSError, subprocess.SubprocessError):
                print("Android crash buffer unavailable", flush=True)
            raise error


def wait_for(action, label, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            found = action()
            if found is not None and found is not False:
                return found
        except (OSError, subprocess.SubprocessError, ElementTree.ParseError, IndexError):
            # The app, WebView, or native UI can be starting up.
            pass
        time.sleep(1)
    raise AssertionError(f"Timed out waiting for {label}")


def finish_native_bootstrap(device):
    """Choose the existing offline fallback, without linking a Google account."""
    def startup_node(node):
        label = (node.get("text") or "").casefold()
        if node.get("package") == APP:
            return (node.get("class") == "android.webkit.WebView"
                    or "lokale python-laufzeit" in label or "lokales benutzerkonto" in label)
        return (node.get("package") in {"android", "com.android.settings", "com.google.android.gms"}
                and any(text in label for text in ("choose an account", "add account", "konto hinzufügen",
                                                  "konto für simpleoffice4me")))

    ready = device.wait_node(startup_node, "offline first-launch choice", 60)
    if ready.get("package") != APP:
        device.adb("shell", "input", "keyevent", "KEYCODE_BACK")
    print("Android launcher reached local account setup", flush=True)


def seed(page, selected=DESIRED):
    page.locator("#sms-manual-address").fill("Synthetic sender")
    page.locator("#sms-manual-body").fill(selected)
    page.locator("#sms-manual-when").fill("2026-11-27T14:00:01")
    page.locator("#sms-manual-zone").select_option("UTC")
    page.locator("#sms-manual-confirm").check()
    page.locator('form[action="/communication/sms/manual"] button[type=submit]').click()


def select_backup(page):
    form = page.locator('form[action="/communication/sms/export"]')
    form.locator("li").filter(has_text=DESIRED).locator("input[name=selected]").check()
    # The WebView runs the real authenticated form navigation and DownloadListener.
    form.evaluate("form => form.requestSubmit(form.querySelector('button[type=submit]'))")


def system_save(node):
    return (node.get("package") in DOCS_UI and node.get("enabled") == "true"
            and (node.get("text", "").casefold() == "save"
                 or node.get("resource-id", "").endswith(":id/option_menu_save")))


def verify_backup(device):
    if device.adb("shell", "getprop", "ro.hardware") not in {"ranchu", "goldfish"}:
        raise AssertionError("This acceptance runs on an Android emulator only")
    result = device.adb("shell", "am", "start", "-W", "-a", "android.intent.action.MAIN",
                        "-c", "android.intent.category.LAUNCHER", "-n", f"{APP}/.BootstrapActivity", timeout=45)
    if "Status: ok" not in result:
        raise AssertionError("Android did not start the SimpleOffice activity")
    finish_native_bootstrap(device)
    port = device.forwarded_socket()
    try:
        url = f"http://127.0.0.1:{port}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(url, timeout=15000)
            page, = browser.contexts[0].pages
            page.set_default_timeout(20000)
            page.wait_for_url(f"{LOCAL}/**", timeout=150000)
            page.goto(f"{LOCAL}/communication/sms", wait_until="domcontentloaded")
            seed(page)
            seed(page, OTHER)
            export = page.locator('form[action="/communication/sms/export"]')
            assert export.locator("input[name=selected]").count() == 2, "Both fixture SMS must be archived"
            select_backup(page)
            device.wait_node(system_save, "Android document picker")
            device.adb("shell", "input", "keyevent", "KEYCODE_BACK")
            device.wait_node(lambda node: node.get("package") == APP, "return from canceled picker")
            device.adb("shell", "test", "!", "-e", TARGET)
            select_backup(page)
            save = device.wait_node(system_save, "Android document picker after retry")
            # Fresh DocumentsUI normally starts on Downloads for a create-document
            # intent; use its navigation drawer if it shows another location.
            nodes = device.nodes()
            if not any(n.get("package") in DOCS_UI and n.get("text") == "Downloads" for n in nodes):
                drawer = next((n for n in nodes if n.get("package") in DOCS_UI
                               and n.get("content-desc") in {"Show roots", "Open navigation drawer"}), None)
                if drawer is not None:
                    device.click(drawer)
                    device.click(device.wait_node(
                        lambda n: n.get("package") in DOCS_UI and n.get("text") == "Downloads",
                        "Downloads root",
                    ))
                    save = device.wait_node(system_save, "Save in Downloads")
            device.click(save)
            wait_for(lambda: device.adb("shell", "test", "-s", TARGET) == "", "nonempty native backup", 45)
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "backup.json"
                device.adb("pull", TARGET, str(path))
                data = json.loads(path.read_bytes())
            payload = json.dumps(data["payload"], ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode()
            assert data["sha256"] == hashlib.sha256(payload).hexdigest(), "Backup checksum mismatch"
            records = data["payload"]["records"]
            assert data["payload"]["owner"] == "android", "Backup does not belong to the native offline account"
            assert len(records) == 1 and records[0]["body"] == DESIRED, "Backup selection mismatch"
            print("simpleoffice_sms_native_backup=pass")
    finally:
        device.adb("forward", "--remove", f"tcp:{port}")


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--adb", required=True)
    args.add_argument("--serial", required=True)
    values = args.parse_args()
    if not re.fullmatch(r"emulator-\d+", values.serial):
        args.error("Only disposable emulator serials are accepted")
    verify_backup(Device(values.adb, values.serial))


if __name__ == "__main__":
    main()
