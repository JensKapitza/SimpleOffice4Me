#!/usr/bin/env bash
# In CI, provision a new emulator. Otherwise accept only an explicitly selected
# fresh emulator; the test APK never accesses a production Android installation.
set -euo pipefail

apk=${1:?path to the debug APK required}
test_apk=${2:?path to the instrumentation APK required}
serial=${3:-}
test -f "$apk"
test -f "$test_apk"

sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
adb_bin=""
if [[ -n "$sdk" ]]; then
  adb_bin="$sdk/platform-tools/adb"
fi

if [[ -z "$serial" ]]; then
  if [[ "${GITHUB_ACTIONS:-}" != true || ! -c /dev/kvm ]]; then
    echo 'Use a fresh disposable AVD and specify its emulator-NNNN serial.' >&2
    exit 2
  fi
  test -n "${sdk:?Android SDK not installed}"
  image='system-images;android-30;google_apis;x86_64'
  serial=emulator-5560
  if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
    sudo chown "$(id -u):$(id -g)" /dev/kvm
  fi
  timeout 480 "$sdk/cmdline-tools/latest/bin/sdkmanager" 'platform-tools' 'emulator' "$image"
  test -x "$adb_bin" || { echo 'SDK platform-tools installed without usable adb' >&2; exit 2; }
  timeout 30 "$adb_bin" start-server
  avd='simpleoffice-provider-acceptance'
  # The CLI and emulator may look at different homes on CI unless both are set.
  export ANDROID_USER_HOME
  ANDROID_USER_HOME="$(mktemp -d "${RUNNER_TEMP:?}/contacts-android.XXXXXX")"
  export ANDROID_AVD_HOME="$ANDROID_USER_HOME/avd"
  mkdir -p "$ANDROID_AVD_HOME"
  printf 'no\n' | "$sdk/cmdline-tools/latest/bin/avdmanager" create avd \
    -n "$avd" -k "$image" -p "$ANDROID_AVD_HOME/$avd.avd"
  if ! test -s "$ANDROID_AVD_HOME/$avd.ini" ||
     ! "$sdk/emulator/emulator" -list-avds | grep -Fx "$avd"; then
    echo 'The newly created AVD is not visible to the emulator' >&2
    exit 2
  fi
  emulator_log="$ANDROID_USER_HOME/emulator.log"
  "$sdk/emulator/emulator" -avd "$avd" -port 5560 -no-window -no-audio -no-boot-anim \
    -no-snapshot -wipe-data -gpu swiftshader_indirect >"$emulator_log" 2>&1 &
  emulator_pid=$!
  trap 'status=$?; kill "$emulator_pid" 2>/dev/null || true; if ((status != 0)); then tail -n 60 "$emulator_log" >&2; fi' EXIT
fi

[[ "$serial" =~ ^emulator-[0-9]+$ ]] || { echo 'Not an Android emulator serial' >&2; exit 2; }
if [[ ! -x "$adb_bin" ]]; then
  adb_bin="$(command -v adb)" || { echo 'adb is missing from the Android SDK and PATH' >&2; exit 2; }
fi
adb_for_test() { timeout 120 "$adb_bin" -s "$serial" "$@"; }
booted=false
for ((i=0; i<60; i++)); do
  if [[ -n "${emulator_pid:-}" ]] && ! kill -0 "$emulator_pid" 2>/dev/null; then
    echo 'Emulator process exited before Android boot' >&2
    exit 2
  fi
  if [[ "$(timeout 5 "$adb_bin" -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; then
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
result="$(adb_for_test shell am instrument -w -r -e disposable yes \
  de.simpleoffice4me.android.test/de.simpleoffice4me.android.ManagedContactsProviderAcceptance | tr -d '\r')"
printf '%s\n' "$result"
grep -Fxq 'INSTRUMENTATION_RESULT: simpleoffice_contacts_provider=pass' <<<"$result"
grep -Fxq 'INSTRUMENTATION_RESULT: simpleoffice_calendar_provider=pass' <<<"$result"
grep -Fxq 'INSTRUMENTATION_CODE: -1' <<<"$result"
