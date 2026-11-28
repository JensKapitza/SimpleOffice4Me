"""Exercise actual SensorManager-to-WebView-to-store flow on a disposable AVD."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import math
import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright

from tools.android_emulator_smoke import _adb
from tools.android_nfc_fallback_acceptance import APP, ORIGIN, webview_port

PAGE = ORIGIN + "/datalogger/android-motion"
IDENTIFIER = re.compile(r"/datalogger/channels/([a-f0-9-]{36})\Z")


def open_motion(page):
    page.goto(PAGE, wait_until="domcontentloaded")
    status = page.locator("[data-motion-status]")
    expect(status).to_contain_text("Beschleunigung: verfügbar", timeout=20000)
    expect(status).to_contain_text("Drehgeschwindigkeit:")
    return status


def capture(page, status):
    measure = page.locator('[data-motion-kind="acceleration"]')
    expect(measure).to_be_enabled()
    measure.click()
    expect(status).to_contain_text("Noch nicht gespeichert.", timeout=12000)
    magnitude = float(page.locator('[data-motion-save] [name="value"]').input_value())
    assert math.isfinite(magnitude) and 0 < magnitude <= 50, "Expected a gravity-bearing virtual accelerometer sample"
    return magnitude


def fixture(page):
    location = page.evaluate("""async () => {
        const csrf = document.querySelector('[data-motion-csrf]').value;
        const response = await fetch('/datalogger/channels', {
            method: 'POST', headers: {'X-CSRF-Token': csrf},
            body: new URLSearchParams({name:'Synthetic motion acceptance', unit:'m/s²'}),
        });
        return response.ok ? response.url : '';
    }""")
    path = urlsplit(location).path
    match = IDENTIFIER.fullmatch(path)
    assert match, "Channel setup did not return a canonical channel location"
    return match[1]


def samples(page, channel):
    rows = page.evaluate("""async channel => {
        const r = await fetch('/datalogger/channels/' + channel + '/data.json', {cache:'no-store'});
        return r.ok ? (await r.json()).samples : null;
    }""", channel)
    assert isinstance(rows, list), "Could not read the synthetic motion channel"
    return rows


def launch(serial):
    result = _adb("shell", "am", "start", "-W", "-a", "android.intent.action.MAIN",
                  "-c", "android.intent.category.LAUNCHER", "-n", f"{APP}/.BootstrapActivity",
                  serial=serial, timeout=45)
    assert "Status: ok" in result, "SimpleOffice did not return to the foreground"


def verify_pause(page, serial):
    assert page.url.startswith(ORIGIN + "/"), "Test was not attached to the bundled WebView"
    open_motion(page)
    channel = fixture(page)
    status = open_motion(page)
    save = page.locator('[data-motion-save] button[type="submit"]')
    capture(page, status)
    page.locator("#motion-channel").select_option(channel)
    expect(save).to_be_enabled()
    assert samples(page, channel) == [], "Sensor preview already changed persistent data"
    _adb("shell", "input", "keyevent", "KEYCODE_HOME", serial=serial)
    expect(save).to_be_disabled(timeout=15000)
    expect(page.locator('[data-motion-save] [name="value"]')).to_have_value("")
    print("Android motion: background clears the unsaved preview", flush=True)
    return channel


def verify_save(page, channel):
    status = open_motion(page)
    assert samples(page, channel) == [], "Background cancellation wrote a sample"
    magnitude = capture(page, status)
    save = page.locator('[data-motion-save] button[type="submit"]')
    page.locator("#motion-channel").select_option(channel)
    assert samples(page, channel) == [], "A second preview wrote an unexpected sample"
    with page.expect_response(lambda r: r.url == PAGE and r.request.method == "POST") as submitted:
        save.click()
    assert submitted.value.status == 303, "The explicit Save did not complete"
    page.wait_for_url(f"{ORIGIN}/datalogger/channels/{channel}")
    page.reload(wait_until="domcontentloaded")
    row, = samples(page, channel)
    assert row["value"] == magnitude and row["source_kind"] == "manual", "Wrong saved observation"
    assert row["metadata"] == {"observation": "acceleration"}, "Unexpected stored motion metadata"
    print("simpleoffice_android_motion_native=pass", flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--serial", required=True)
    serial = cli.parse_args().serial
    if not re.fullmatch(r"emulator-\d+", serial):
        cli.error("This test accepts disposable Android emulator serials only")
    if _adb("shell", "getprop", "ro.hardware", serial=serial).strip() not in {"ranchu", "goldfish"}:
        cli.error("Android hardware is not an emulator")
    with android_page(serial) as page:
        channel = verify_pause(page, serial)
    launch(serial)
    with android_page(serial) as page:
        verify_save(page, channel)


def choose_foreground(candidates):
    focused = [page for page, visible, focus in candidates if visible and focus]
    if len(focused) == 1:
        return focused[0]
    visible = [page for page, visible, _ in candidates if visible]
    return visible[0] if len(visible) == 1 else None


def foreground_page(browser, *, seconds=45):
    # Launcher re-entry can add another Activity/WebView while the previous
    # one remains a debuggable target. Never trust the DevTools list order.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        candidates = []
        for context in browser.contexts:
            for page in context.pages:
                if not page.url.startswith(ORIGIN + "/") or page.is_closed():
                    continue
                try:
                    visible, focused = page.evaluate(
                        "() => [document.visibilityState === 'visible', document.hasFocus()]"
                    )
                    candidates.append((page, visible, focused))
                except PlaywrightError:
                    continue
        selected = choose_foreground(candidates)
        if selected is not None:
            return selected
        time.sleep(0.25)
    raise AssertionError("No unique foreground SimpleOffice WebView after launcher resume")


@contextmanager
def android_page(serial):
    port = webview_port(serial)
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}", no_defaults=True, timeout=15000
            )
            page = foreground_page(browser)
            page.set_default_timeout(20000)
            page.wait_for_url(ORIGIN + "/**", timeout=60000)
            yield page
    finally:
        _adb("forward", "--remove", f"tcp:{port}", serial=serial, timeout=5)


if __name__ == "__main__":
    main()
