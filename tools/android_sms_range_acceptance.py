"""Exercise a real Android save, verify and restore of a synthetic 51-SMS range."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from playwright.sync_api import expect

from .android_sms_download_acceptance import (
    APP, DOCS_UI, LOCAL, TARGET, Device, archive_page, check_saved_file,
    choose_recovery_file, pull_complete_backup, system_save, wait_for,
)

RANGE = "/communication/sms/backup-range"
RESTORE = "/communication/sms/restore"
DAY = "2025-06-15"
OUTSIDE = "Synthetic SMS outside selected range"
MESSAGES = tuple(f"Synthetic range SMS {number:02d}" for number in range(1, 52))


def seed_range(page):
    # Populate only via the authenticated manual-import HTTP route.
    # The acceptance itself drives all backup and restore controls normally.
    result = page.evaluate("""async ({day, messages, outside}) => {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
        if (!csrf) return false;
        for (const [index, body] of [...messages, outside].entries()) {
            const date = index === messages.length ? "2025-06-16" : day;
            const form = new URLSearchParams({
                address: "Synthetic fixture", when: date + "T11:" + String(index).padStart(2, "0"),
                zone: "UTC", direction: "inbox", body, confirm: "1"
            });
            const reply = await fetch("/communication/sms/manual", {
                method: "POST", headers: {"X-CSRF-Token": csrf}, body: form
            });
            if (!reply.ok || new URL(reply.url).pathname !== "/communication/sms") return false;
        }
        return true;
    }""", {"day": DAY, "messages": MESSAGES, "outside": OUTSIDE})
    assert result is True, "Could not populate the synthetic archive through HTTP"


def native_range_export(device, fixture: Path):
    with archive_page(device) as page:
        seed_range(page)
        page.locator(f'main a[href="{RANGE}"]').click()
        page.wait_for_url(LOCAL + RANGE)
        page.locator("#sms-backup-from").fill(DAY)
        page.locator("#sms-backup-to").fill(DAY)
        page.locator('button[name="stage"][value="preview"]').click()
        expect(page.locator("[data-sms-range-count]")).to_have_text("51")
        device.adb("shell", "test", "!", "-e", TARGET)
        save = page.locator('button[name="stage"][value="save"]')
        for attempt in (1, 2):
            with page.expect_response(
                lambda response: response.url == LOCAL + RANGE and response.request.method == "POST"
            ) as submitted:
                save.click(no_wait_after=True)
            assert submitted.value.status == 303, "Range confirmation did not prepare a download"
            control = device.wait_node(system_save, "save backup to Android documents")
            if attempt == 1:
                device.adb("shell", "input", "keyevent", "KEYCODE_BACK")
                device.wait_node(lambda node: node.get("package") == APP, "return after backup cancellation")
                wait_for(lambda: "picker-canceled" in device.download_phases(), "native cancellation", 15)
                device.adb("shell", "test", "!", "-e", TARGET)
            else:
                if not any(node.get("package") in DOCS_UI and node.get("text") == "Downloads"
                           for node in device.nodes()):
                    drawer = next((node for node in device.nodes() if node.get("package") in DOCS_UI
                                   and node.get("content-desc") in {"Show roots", "Open navigation drawer"}), None)
                    if drawer is not None:
                        device.click(drawer)
                        device.click(device.wait_node(lambda node: node.get("package") in DOCS_UI
                                                     and node.get("text") == "Downloads", "Downloads root"))
                        control = device.wait_node(system_save, "save backup in Downloads")
                device.click(control)
        content, backup = pull_complete_backup(device)
        payload = backup["payload"]
        assert payload["version"] == 2 and payload["owner"] == "android"
        assert len(payload["records"]) == len(MESSAGES)
        assert {row["body"] for row in payload["records"]} == set(MESSAGES)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        assert backup["sha256"] == hashlib.sha256(encoded).hexdigest()
        with fixture.open("xb") as output:
            output.write(content)
        print("simpleoffice_sms_range_native_backup=pass", flush=True)


def native_range_restore(device):
    with archive_page(device) as page:
        rows = page.locator('form[action="/communication/sms/export"] li')
        expect(rows).to_have_count(0)
        check_saved_file(page, device, matching=0, missing=51)
        expect(rows).to_have_count(0)
        for amount in (51, 0):
            choose_recovery_file(page, device)
            page.locator("#sms-confirm").check()
            with page.expect_response(lambda r: r.url == LOCAL + RESTORE and r.request.method == "POST") as submitted:
                page.locator(f'form[action="{RESTORE}"] button[type=submit]').click()
            assert submitted.value.status == 303
            expect(page.locator('main [role="status"]').filter(
                has_text=re.compile(rf"^{amount} SMS (?:messages restored|wiederhergestellt)\.")
            )).to_be_visible()
        expect(rows).to_have_count(25)
        expect(rows.filter(has_text=OUTSIDE)).to_have_count(0)
        check_saved_file(page, device, matching=51, missing=0)
        print("simpleoffice_sms_range_native_restore=pass", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--save-fixture", type=Path)
    mode.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"emulator-\d+", args.serial):
        parser.error("Only disposable emulators are accepted")
    device = Device(args.adb, args.serial)
    if args.restore:
        native_range_restore(device)
    else:
        native_range_export(device, args.save_fixture)


if __name__ == "__main__":
    main()
