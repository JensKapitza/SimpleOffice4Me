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

    @staticmethod
    def classify_focus(dump):
        """Do not return arbitrary activity names or intent arguments."""
        for line in dump.splitlines():
            if not re.search(r"mResumedActivity|topResumedActivity|mCurrentFocus|mFocusedApp", line, re.I):
                continue
            for name in ("BootstrapActivity", "NavigationActivity"):
                if re.search(re.escape(APP) + r"/(?:" + re.escape(APP) + r")?\." + name + r"\b", line):
                    return name
            if "com.android.systemui" in line or "Keyguard" in line:
                return "system-ui"
            if re.search(r"(?:com\.android\.launcher|com\.google\.android\.apps\.nexuslauncher)", line):
                return "launcher"
            if "com.google.android.gms" in line or "com.android.settings" in line:
                return "account-or-settings"
        return "other-or-unknown"

    def foreground(self, label):
        try:
            activities = self.adb("shell", "dumpsys", "activity", "activities")
            windows = self.adb("shell", "dumpsys", "window", "windows")
            display = self.adb("shell", "dumpsys", "power")
            wakeful = re.search(r"mWakefulness=(Awake|Asleep|Dreaming|Dozing)\b", display)
            print(f"Android {label}:", "activity=" + self.classify_focus(activities),
                  "window=" + self.classify_focus(windows),
                  "screen=" + (wakeful[1] if wakeful else "unknown"), flush=True)
        except (OSError, subprocess.SubprocessError):
            print(f"Android {label}: foreground unavailable", flush=True)

    def backend_probe(self):
        port = None
        try:
            port = int(self.adb("forward", "tcp:0", "tcp:8765"))
            connection = HTTPConnection("127.0.0.1", port, timeout=4)
            try:
                connection.request("GET", "/", headers={"Connection": "close"})
                print("Android embedded backend:", f"HTTP-{connection.getresponse().status}", flush=True)
            finally:
                connection.close()
        except (OSError, HTTPException, ValueError, subprocess.SubprocessError) as error:
            print("Android embedded backend:", type(error).__name__, flush=True)
        finally:
            if port is not None:
                self.adb("forward", "--remove", f"tcp:{port}")

    def startup_milestones(self):
        allowed = {
            "launcher-created", "local-choice-present", "account-picker", "account-picker-returned",
            "clearing-session", "cleared-session", "assets-ready", "python-ready", "python-start-returned",
            "python-module-loading", "python-module-loaded", "before-python-start",
            "http-ready", "bootstrap-failed", "open-navigation", "launcher-destroyed",
            "navigation-creating", "navigation-created",
        }
        try:
            log = self.adb("logcat", "-d", "-s", "SO4MEStartup:I")
            events = [line.rsplit(": ", 1)[-1] for line in log.splitlines() if ": " in line]
            print("Debug startup milestones:", " ".join(item for item in events if item in allowed) or "none",
                  flush=True)
        except (OSError, subprocess.SubprocessError):
            print("Debug startup milestones: unavailable", flush=True)
        try:
            python_allowed = {"python-entry", "environment-ready", "symbols-ready", "rust-ready",
                              "flask-imported", "routes-ready", "backend-listening"}
            log = self.adb("logcat", "-d", "-s", "python.stdout:I")
            events = [line.split("SO4ME_PY_START ", 1)[1].strip() for line in log.splitlines()
                      if "SO4ME_PY_START " in line]
            print("Debug Python milestones:",
                  " ".join(item for item in events if item in python_allowed) or "none", flush=True)
            exit_info = self.adb("shell", "dumpsys", "activity", "exit-info", APP)
            reasons = re.findall(r"\breason=([0-9]{1,3})\b", exit_info)
            print("Android package exit reasons:", " ".join(reasons[:6]) or "none", flush=True)
        except (OSError, subprocess.SubprocessError):
            print("Debug Python milestones or exit reasons unavailable", flush=True)

    def diagnose_startup(self, probe):
        """Report fixed phases, never a raw hierarchy, response, cookie or credential."""
        print("DevTools probe:", probe, flush=True)
        self.foreground("at timeout")
        self.startup_milestones()
        self.backend_probe()
        try:
            statuses = [node.get("text", "") for node in self.nodes() if node.get("package") == APP]
            details = next((item for item in statuses if item.startswith("Start fehlgeschlagen:")), "")
            if details:
                error = re.search(r"\b(?:ModuleNotFoundError|ImportError|AttributeError|ConnectException|"
                                  r"IllegalStateException|OSError|TypeError|UnsatisfiedLinkError)\b", details)
                module = re.search(r"No module named ['\"]([a-zA-Z_][a-zA-Z0-9_.]{0,90})['\"]", details)
                status_code = re.search(r"\bHTTP ([1-5][0-9]{2})\b", details)
                stage = next((name for fragment, name in (
                    ("Backend antwortet nicht", "backend-response"),
                    ("Sitzung konnte nicht", "native-session"),
                    ("Konto konnte nicht", "native-account"),
                    ("dlopen failed", "native-linker"),
                ) if fragment in details), "unknown")
                print("Native startup failure:", stage, error[0] if error else "other",
                      "missing-module=" + module[1] if module else "",
                      "http=" + status_code[1] if status_code else "", flush=True)
            else:
                for prefix, stage in (("Android-Konto", "account-selection"), ("Lokale Programmdateien", "assets"),
                                      ("Lokale Python-Laufzeit", "python"), ("Lokales Benutzerkonto", "account")):
                    if any(item.startswith(prefix) for item in statuses):
                        print("Visible startup phase:", stage, flush=True)
                        break
        except (OSError, subprocess.SubprocessError, ElementTree.ParseError):
            print("Visible startup phase: unavailable", flush=True)
        try:
            webview = self.adb("shell", "dumpsys", "webviewupdate")
            version = re.search(r"Current WebView package[^:]*:\s*\(([a-zA-Z0-9_.]+),\s*([0-9.]+)\)", webview)
            if version:
                print("WebView provider:", version[1], version[2], flush=True)
        except (OSError, subprocess.SubprocessError):
            print("WebView provider: unavailable", flush=True)

    def forwarded_socket(self):
        # Android restricts /proc/net/unix to privileged callers. Ask adb to
        # forward the exact known WebView socket instead and probe it over TCP.
        seen_process = False
        probe = "no-pid"

        def locate():
            nonlocal seen_process, probe
            pid = self.adb("shell", "pidof", APP).split()[0]
            if not pid.isdecimal():
                return None
            seen_process = True
            probe = "adb-forward"
            port = int(self.adb("forward", "tcp:0", f"localabstract:webview_devtools_remote_{pid}"))
            ready = False
            try:
                connection = HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    connection.request("GET", "/json/list")
                    response = connection.getresponse()
                    probe = f"HTTP-{response.status}"
                    if response.status == 200:
                        targets = json.loads(response.read(65536))
                        ready = any(item.get("type") == "page" for item in targets)
                        probe = "valid-page" if ready else "no-WebView-target"
                finally:
                    connection.close()
                return port if ready else None
            except (OSError, HTTPException, ValueError) as error:
                probe = type(error).__name__
                return None
            finally:
                if not ready:
                    self.adb("forward", "--remove", f"tcp:{port}")

        try:
            return wait_for(locate, "debug WebView target through adb", 120)
        except AssertionError as error:
            details = "App process exists" if seen_process else "App process did not stay alive"
            print(details, flush=True)
            self.diagnose_startup(probe)
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
        except (OSError, subprocess.SubprocessError, ElementTree.ParseError, HTTPException, ValueError, IndexError):
            # The app, WebView, or native UI can be starting up.
            pass
        time.sleep(1)
    raise AssertionError(f"Timed out waiting for {label}")


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


def verify_backup(device, *, startup_only=False):
    if device.adb("shell", "getprop", "ro.hardware") not in {"ranchu", "goldfish"}:
        raise AssertionError("This acceptance runs on an Android emulator only")
    result = device.adb("shell", "am", "start", "-W", "-a", "android.intent.action.MAIN",
                        "-c", "android.intent.category.LAUNCHER", "-n", f"{APP}/.BootstrapActivity", timeout=45)
    if "Status: ok" not in result:
        raise AssertionError("Android did not start the SimpleOffice activity")
    print("Android launcher started; waiting for local WebView", flush=True)
    device.foreground("after launcher start")
    port = device.forwarded_socket()
    try:
        url = f"http://127.0.0.1:{port}"
        if startup_only:
            def local_page():
                connection = HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    connection.request("GET", "/json/list")
                    response = connection.getresponse()
                    targets = json.loads(response.read(65536)) if response.status == 200 else []
                    return any(target.get("type") == "page" and target.get("url", "").startswith(LOCAL + "/")
                               for target in targets)
                finally:
                    connection.close()

            try:
                wait_for(local_page, "baseline local WebView navigation", 150)
            except AssertionError:
                device.diagnose_startup("baseline-navigation-timeout")
                raise
            print("simpleoffice_sms_control_startup=pass")
            return
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
    args.add_argument("--startup-only", action="store_true", help="Measure only the baseline cold startup")
    values = args.parse_args()
    if not re.fullmatch(r"emulator-\d+", values.serial):
        args.error("Only disposable emulator serials are accepted")
    verify_backup(Device(values.adb, values.serial), startup_only=values.startup_only)


if __name__ == "__main__":
    main()
