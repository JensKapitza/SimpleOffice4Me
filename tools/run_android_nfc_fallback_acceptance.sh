#!/usr/bin/env bash
# Explicit local serial: fresh AVD and manually disable NFC before running.
# CI without a serial provisions a clean, disposable Android 15 AVD.
set -euo pipefail

apk=${1:?path to the debug APK required}
serial=${2:-}
test -f "$apk"
sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [[ -n "$sdk" ]]; then export PATH="$sdk/platform-tools:$PATH"; fi

if [[ -z "$serial" ]]; then
  if [[ "${GITHUB_ACTIONS:-}" != true || ! -c /dev/kvm ]]; then
    echo 'Specify a fresh disposable emulator serial outside CI.' >&2
    exit 2
  fi
  test -n "${sdk:?Android SDK required}"
  image='system-images;android-35;google_apis;x86_64'
  serial=emulator-5564
  if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
    sudo chown "$(id -u):$(id -g)" /dev/kvm
  fi
  timeout 480 "$sdk/cmdline-tools/latest/bin/sdkmanager" 'platform-tools' 'emulator' "$image"
  timeout 30 adb start-server
  export ANDROID_USER_HOME
  ANDROID_USER_HOME="$(mktemp -d "${RUNNER_TEMP:?}/nfc-android.XXXXXX")"
  export ANDROID_AVD_HOME="$ANDROID_USER_HOME/avd"
  mkdir -p "$ANDROID_AVD_HOME"
  avd='simpleoffice-nfc-fallback'
  printf 'no\n' | "$sdk/cmdline-tools/latest/bin/avdmanager" create avd \
    -n "$avd" -k "$image" -p "$ANDROID_AVD_HOME/$avd.avd"
  test -s "$ANDROID_AVD_HOME/$avd.ini"
  "$sdk/emulator/emulator" -list-avds | grep -Fx "$avd"
  "$sdk/emulator/emulator" -avd "$avd" -port 5564 -no-window -no-audio -no-boot-anim \
    -no-snapshot -wipe-data -gpu swiftshader_indirect >"$ANDROID_USER_HOME/device.log" 2>&1 &
  emulator_pid=$!
  trap 'kill "$emulator_pid" 2>/dev/null || true' EXIT
fi

[[ "$serial" =~ ^emulator-[0-9]+$ ]] || { echo 'Only disposable emulator serials are accepted.' >&2; exit 2; }
command -v adb >/dev/null || { echo 'Android platform-tools adb is required.' >&2; exit 2; }
tadb() { timeout 45 adb -s "$serial" "$@"; }
booted=false
for ((i=0; i<60; i++)); do
  if [[ -n "${emulator_pid:-}" ]] && ! kill -0 "$emulator_pid" 2>/dev/null; then
    echo 'Emulator exited before boot.' >&2; exit 2
  fi
  if [[ "$(timeout 5 adb -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; then
    booted=true; break
  fi
  sleep 3
done
[[ "$booted" == true ]] || { echo 'Emulator failed to boot.' >&2; exit 2; }
hardware="$(tadb shell getprop ro.hardware | tr -d '\r')"
[[ "$hardware" == ranchu || "$hardware" == goldfish ]] || { echo 'Not an Android emulator.' >&2; exit 2; }
if [[ -n "${emulator_pid:-}" ]]; then
  target="$(tadb shell getprop ro.boot.qemu.avd_name | tr -d '\r')"
  [[ "$target" == "$avd" ]] || { echo 'The connected emulator is not the new test AVD.' >&2; exit 2; }
fi
abis="$(tadb shell getprop ro.product.cpu.abilist | tr -d '\r')"
[[ ",$abis," == *,arm64-v8a,* ]] || { echo 'Emulator cannot run the ARM64 APK.' >&2; exit 2; }
installed="$(tadb shell pm list packages de.simpleoffice4me.android | tr -d '\r')"
[[ -z "$installed" ]] || { echo 'Use a fresh emulator with no SimpleOffice data.' >&2; exit 2; }
tadb install "$apk"
tadb shell run-as de.simpleoffice4me.android mkdir -p shared_prefs
tadb shell -T "run-as de.simpleoffice4me.android sh -c 'umask 077; cat > shared_prefs/simpleoffice-android-identity.xml'" <<'XML'
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <boolean name="identity-ready" value="true" />
    <string name="google-email"></string>
</map>
XML
if [[ -n "${emulator_pid:-}" ]]; then
  features="$(tadb shell pm list features | tr -d '\r')"
  if grep -Fxq 'feature:android.hardware.nfc' <<<"$features"; then
    tadb shell svc nfc disable
  fi
fi
tadb shell svc power stayon true
tadb shell settings put system screen_off_timeout 1800000
tadb shell input keyevent KEYCODE_WAKEUP
tadb shell wm dismiss-keyguard
timeout 280 python -m tools.android_nfc_fallback_acceptance --serial "$serial"
