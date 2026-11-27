#!/usr/bin/env bash
# In CI, provision a new emulator. Otherwise accept only an explicitly selected
# fresh emulator; the test APK never accesses a production Android installation.
set -euo pipefail

apk=${1:?path to the debug APK required}
test_apk=${2:?path to the instrumentation APK required}
serial=${3:-}
test -f "$apk"
test -f "$test_apk"

if [[ -z "$serial" ]]; then
  if [[ "${GITHUB_ACTIONS:-}" != true || ! -c /dev/kvm ]]; then
    echo 'Use a fresh disposable AVD and specify its emulator-NNNN serial.' >&2
    exit 2
  fi
  sdk="${ANDROID_HOME:?Android SDK not installed}"
  image='system-images;android-30;google_apis;x86_64'
  serial=emulator-5560
  if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
    sudo chown "$(id -u):$(id -g)" /dev/kvm
  fi
  timeout 480 "$sdk/cmdline-tools/latest/bin/sdkmanager" 'platform-tools' 'emulator' "$image"
  avd='simpleoffice-provider-acceptance'
  printf 'no\n' | "$sdk/cmdline-tools/latest/bin/avdmanager" create avd -n "$avd" -k "$image"
  "$sdk/emulator/emulator" -avd "$avd" -port 5560 -no-window -no-audio -no-boot-anim \
    -no-snapshot -wipe-data -gpu swiftshader_indirect >"${RUNNER_TEMP:?}/android-provider-emulator.log" 2>&1 &
  emulator_pid=$!
  trap 'kill "$emulator_pid" 2>/dev/null || true' EXIT
fi

[[ "$serial" =~ ^emulator-[0-9]+$ ]] || { echo 'Not an Android emulator serial' >&2; exit 2; }
adb_for_test() { timeout 120 adb -s "$serial" "$@"; }
adb_for_test wait-for-device
booted=false
for ((i=0; i<60; i++)); do
  if [[ "$(timeout 5 adb -s "$serial" shell getprop sys.boot_completed | tr -d '\r')" == 1 ]]; then
    booted=true
    break
  fi
  sleep 3
done
[[ "$booted" == true ]] || { echo 'Emulator did not complete Android boot' >&2; exit 2; }
hardware="$(adb_for_test shell getprop ro.hardware | tr -d '\r')"
[[ "$hardware" == ranchu || "$hardware" == goldfish ]] || { echo 'Not a disposable Android AVD' >&2; exit 2; }
abis="$(adb_for_test shell getprop ro.product.cpu.abilist | tr -d '\r')"
[[ ",$abis," == *,arm64-v8a,* ]] || { echo 'Emulator does not support the APK ARM64 ABI' >&2; exit 2; }
installed="$(adb_for_test shell pm list packages de.simpleoffice4me.android | tr -d '\r')"
[[ -z "$installed" ]] || { echo 'Use a fresh AVD without SimpleOffice already installed' >&2; exit 2; }
adb_for_test install "$apk"
adb_for_test install -t "$test_apk"
result="$(adb_for_test shell am instrument -w -e disposable yes \
  de.simpleoffice4me.android.test/de.simpleoffice4me.android.ManagedContactsProviderAcceptance | tr -d '\r')"
printf '%s\n' "$result"
grep -Fxq 'SIMPLEOFFICE_CONTACTS_PROVIDER_PASS' <<<"$result"
grep -Fxq 'INSTRUMENTATION_CODE: -1' <<<"$result"
