"""Exercise real SMS provider, runtime consent and packaged archive on a fresh AVD."""
import argparse
import re

from playwright.sync_api import expect

from .android_sms_download_acceptance import APP, Device, archive_page

FIXTURE = "Synthetic Android direct-import fixture"
SENDER = "15550100001"


def native_confirm(device):
    button = device.wait_node(
        lambda node: node.get("package") == APP and node.get("resource-id") == "android:id/button1"
        and "sms" in node.get("text", "").casefold(), "native SMS confirmation",
    )
    device.click(button)


def system_permission(device, *, allow):
    ending = ":id/permission_allow_button" if allow else ":id/permission_deny_button"
    button = device.wait_node(
        lambda node: node.get("package", "").endswith("permissioncontroller")
        and node.get("resource-id", "").endswith(ending), "Android SMS permission dialog",
    )
    device.click(button)


def run(device):
    if device.adb("shell", "getprop", "ro.hardware") not in {"ranchu", "goldfish"}:
        raise AssertionError("Only a fresh disposable Android emulator is supported")
    device.adb("emu", "sms", "send", SENDER, FIXTURE)
    with archive_page(device) as page:
        importer = page.locator("#sms-device-import")
        expect(importer).to_be_visible(timeout=20_000)
        button = importer.locator("#sms-device-preview")
        commit = importer.locator("#sms-device-commit")
        status = importer.locator("#sms-device-status")
        row = page.locator('form[action="/communication/sms/export"] li').filter(has_text=FIXTURE)
        expect(row).to_have_count(0)
        button.click()
        native_confirm(device)
        system_permission(device, allow=False)
        expect(status).to_contain_text(re.compile("did not grant|nicht erteilt"), timeout=30_000)
        expect(row).to_have_count(0)
        button.click()
        native_confirm(device)
        system_permission(device, allow=True)
        expect(status).to_contain_text(re.compile(r"1 (new messages|neue SMS)"), timeout=35_000)
        expect(row).to_have_count(0)
        expect(commit).to_be_visible()
        commit.click()
        native_confirm(device)
        expect(row).to_have_count(1, timeout=35_000)
        expect(row).to_contain_text(SENDER)
        expect(status).to_contain_text(re.compile(r"1 (messages added|SMS hinzugefügt)"))
        button.click()
        native_confirm(device)
        expect(status).to_contain_text(re.compile(r"0 (new messages|neue SMS)"), timeout=35_000)
        expect(commit).to_be_hidden()
        expect(row).to_have_count(1)
    device.adb("shell", "am", "force-stop", APP)
    with archive_page(device) as page:
        expect(page.locator('form[action="/communication/sms/export"] li').filter(has_text=FIXTURE)).to_have_count(1)
    print("Native SMS provider: deny, allow, read-only preview, explicit archive, replay and restart passed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"emulator-\d+", args.serial):
        parser.error("emulator serial required")
    run(Device(args.adb, args.serial))


if __name__ == "__main__":
    main()
