#!/usr/bin/env python3
"""Check the installed Android client through adb and public accessibility UI.

Uses an isolated emulator and the public IANA HTTPS example site; no account,
credentials or application data are involved. Does not disable TLS checking.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path


def capture_filtered_logcat(output: Path) -> None:
    """Persist bounded app/Android UI errors to diagnose emulator smoke failures."""
    command = [
        'adb', 'logcat', '-d', '-t', '2000', '-v', 'threadtime', '-s',
        'AndroidRuntime:E', 'ActivityManager:W', 'ActivityTaskManager:W',
        'WindowManager:W', 'chromium:E', 'System.err:E',
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        contents = f'Filtered Android logcat capture unavailable: {type(error).__name__}\n'
    else:
        contents = result.stdout or ''
        if result.stderr:
            contents += '\n--- logcat stderr ---\n' + result.stderr
        contents += f'\nlogcat_exit_code={result.returncode}\n'
    try:
        (output / 'filtered-logcat.txt').write_text(contents[-100_000:], encoding='utf-8')
    except OSError as error:
        print(
            f'Could not save filtered Android logcat ({type(error).__name__})',
            file=sys.stderr,
        )


def adb(*arguments):
    return subprocess.check_output(['adb', *arguments], text=True, timeout=30)


def main():
    from defusedxml import ElementTree as ET

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--output', type=Path, default=Path('artifacts/tauri-android-ui'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    adb('install', '-r', str(args.apk.resolve()))
    adb('shell', 'am', 'start', '-W', '-n', 'de.simpleoffice4me.tauri/.MainActivity')

    def nodes():
        adb('shell', 'rm', '-f', '/sdcard/simpleoffice-ui.xml')
        adb('shell', 'uiautomator', 'dump', '/sdcard/simpleoffice-ui.xml')
        xml = adb('shell', 'cat', '/sdcard/simpleoffice-ui.xml')
        (args.output / 'last-screen.xml').write_text(xml, encoding='utf-8')
        return list(ET.fromstring(xml).iter('node'))

    def wait_for(predicate):
        deadline = time.monotonic() + 45
        last_error = None
        while time.monotonic() < deadline:
            try:
                items = nodes()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ET.ParseError) as error:
                last_error = error
                items = []
            match = next((item for item in items if predicate(item)), None)
            if match is not None:
                return match
            time.sleep(0.5)
        visible = []
        try:
            visible = [(item.get('class'), item.get('text'), item.get('content-desc'))
                       for item in nodes() if item.get('text') or item.get('content-desc')]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ET.ParseError) as error:
            last_error = error
        raise AssertionError(
            f'Expected public Android UI state was not displayed; '
            f'last accessibility error: {last_error}; visible nodes: {visible[:40]}'
        )

    def has_text(expected):
        return lambda item: expected in (item.get('text', '') + ' ' + item.get('content-desc', ''))

    def tap(item):
        bounds = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', item.get('bounds', ''))
        if bounds is None:
            raise AssertionError('Accessible control has no screen bounds')
        left, top, right, bottom = map(int, bounds.groups())
        adb('shell', 'input', 'tap', str((left + right) // 2), str((top + bottom) // 2))

    def enter_server_address(address):
        # The field can retain the previous HTTP attempt after activity restart.
        # ADB input text inserts at the cursor; select all before replacing it.
        field = wait_for(lambda item: item.get('class') == 'android.widget.EditText')
        tap(field)
        # Android WebView may treat CTRL+A as a literal "a" in the emulator.
        # Move to the end and erase the previous value using Android key events.
        adb('shell', 'input', 'keyevent', '123')  # KEYCODE_MOVE_END
        adb('shell', 'input', 'keyevent', *(['67'] * 128))  # KEYCODE_DEL
        adb('shell', 'input', 'text', address)
        adb('shell', 'input', 'keyevent', '111')  # Hide keyboard.
        wait_for(lambda item: item.get('class') == 'android.widget.EditText'
                 and item.get('text') == address)

    try:
        wait_for(has_text('Mit Server verbinden'))
        enter_server_address('http://example.com/')
        tap(wait_for(lambda item: item.get('text') == 'Verbinden'))
        wait_for(has_text('Eine HTTPS-Verbindung wird benötigt.'))
        wait_for(has_text('Mit Server verbinden'))
        # Restart the activity with its still-empty saved preference and use HTTPS.
        adb('shell', 'am', 'force-stop', 'de.simpleoffice4me.tauri')
        adb('shell', 'am', 'start', '-W', '-n', 'de.simpleoffice4me.tauri/.MainActivity')
        enter_server_address('https://example.com/')
        tap(wait_for(lambda item: item.get('text') == 'Verbinden'))
        wait_for(has_text('Example Domain'))
        # The documented Android recovery path is reopening the app.
        adb('shell', 'am', 'force-stop', 'de.simpleoffice4me.tauri')
        adb('shell', 'am', 'start', '-W', '-n', 'de.simpleoffice4me.tauri/.MainActivity')
        wait_for(has_text('Mit Server verbinden'))
        wait_for(lambda item: item.get('class') == 'android.widget.EditText'
                 and item.get('text') == 'https://example.com/')
        print(json.dumps({'ok': True, 'checks': ['client-launch', 'http-rejected',
              'public-trusted-https-opens', 'reopen-prefills-saved-endpoint']}))
    finally:
        capture_filtered_logcat(args.output)
        with (args.output / 'screen.png').open('wb') as stream:
            subprocess.run(['adb', 'exec-out', 'screencap', '-p'], stdout=stream, check=True, timeout=30)
        subprocess.run(['adb', 'shell', 'am', 'force-stop', 'de.simpleoffice4me.tauri'], check=True, timeout=30)


if __name__ == '__main__':
    main()
