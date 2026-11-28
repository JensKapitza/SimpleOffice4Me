#!/usr/bin/env bash
# In CI, provision two fresh emulators in sequence. Otherwise accept only an
# explicitly selected fresh emulator and run the single-device export test.
set -euo pipefail

apk=${1:?path to the debug APK required}
serial=${2:-}
test -f "$apk"

sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [[ -n "$sdk" && -x "$sdk/platform-tools/adb" ]]; then
  adb_bin="$sdk/platform-tools/adb"
else
  adb_bin="$(command -v adb || true)"
fi

adb_for_test() { timeout 120 "$adb_bin" -s "$serial" "$@"; }
prepare_device() {
  [[ "$serial" =~ ^emulator-[0-9]+$ ]] || { echo 'Not an Android emulator serial' >&2; exit 2; }
  local booted=false hardware abis installed api i
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
  [[ "$hardware" == ranchu || "$hardware" == goldfish ]] || { echo 'Not an Android AVD' >&2; exit 2; }
  abis="$(adb_for_test shell getprop ro.product.cpu.abilist | tr -d '\r')"
  [[ ",$abis," == *,arm64-v8a,* ]] || { echo 'Emulator does not support the APK ARM64 ABI' >&2; exit 2; }
  installed="$(adb_for_test shell pm list packages de.simpleoffice4me.android | tr -d '\r')"
  [[ -z "$installed" ]] || { echo 'Use a fresh AVD without SimpleOffice already installed' >&2; exit 2; }
  adb_for_test install "$apk"
  # Seed only the choice to use the ordinary offline account, not private database state.
  adb_for_test shell run-as de.simpleoffice4me.android mkdir -p shared_prefs
  adb_for_test shell -T "run-as de.simpleoffice4me.android sh -c 'umask 077; cat > shared_prefs/simpleoffice-android-identity.xml'" <<'XML'
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <boolean name="identity-ready" value="true" />
    <string name="google-email"></string>
</map>
XML
  adb_for_test shell svc power stayon true
  adb_for_test shell settings put system screen_off_timeout 1800000
  adb_for_test shell input keyevent KEYCODE_WAKEUP
  adb_for_test shell wm dismiss-keyguard
  api="$(adb_for_test shell getprop ro.build.version.sdk | tr -d '\r')"
  printf 'Android acceptance image: API %s, ABIs %s\n' "$api" "$abis"
  if [[ -n "${emulator_pid:-}" && "$api" != 35 ]]; then
    echo 'Unexpected SDK level for this acceptance run.' >&2
    exit 2
  fi
}

if [[ -n "$serial" ]]; then
  test -x "$adb_bin" || { echo 'adb is missing' >&2; exit 2; }
  prepare_device
  timeout 360 python -m tools.android_sms_download_acceptance --adb "$adb_bin" --serial "$serial"
  exit
fi

if [[ "${GITHUB_ACTIONS:-}" != true || ! -c /dev/kvm ]]; then
  echo 'Use a fresh disposable AVD and specify its emulator-NNNN serial.' >&2
  exit 2
fi
test -n "${sdk:?Android SDK not installed}"
# The API 30 ARM translation layer crashed before WebView initialization.
image='system-images;android-35;google_apis;x86_64'
if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
  sudo chown "$(id -u):$(id -g)" /dev/kvm
fi
timeout 480 "$sdk/cmdline-tools/latest/bin/sdkmanager" 'platform-tools' 'emulator' "$image"
adb_bin="$sdk/platform-tools/adb"
test -x "$adb_bin" || { echo 'SDK platform-tools installed without usable adb' >&2; exit 2; }
timeout 30 "$adb_bin" start-server
export ANDROID_USER_HOME
ANDROID_USER_HOME="$(mktemp -d "${RUNNER_TEMP:?}/sms-roundtrip-android.XXXXXX")"
export ANDROID_AVD_HOME="$ANDROID_USER_HOME/avd"
mkdir -p "$ANDROID_AVD_HOME"
emulator_pid=""
emulator_log=""
stop_avd() {
  if [[ -n "$emulator_pid" ]]; then
    kill "$emulator_pid" 2>/dev/null || true
    wait "$emulator_pid" 2>/dev/null || true
    emulator_pid=""
  fi
}
finish() {
  local status=$?
  stop_avd
  if [[ -n "${fixture:-}" ]]; then rm -f -- "$fixture"; fi
  if ((status != 0)) && [[ -n "$emulator_log" ]]; then
    grep -E '^(ERROR|FATAL)' "$emulator_log" | tail -n 15 >&2 || true
  fi
}
trap finish EXIT

launch_fresh_avd() {
  local avd=$1 port=$2
  serial="emulator-$port"
  printf 'no\n' | "$sdk/cmdline-tools/latest/bin/avdmanager" create avd \
    -n "$avd" -k "$image" -p "$ANDROID_AVD_HOME/$avd.avd"
  test -s "$ANDROID_AVD_HOME/$avd.ini"
  "$sdk/emulator/emulator" -list-avds | grep -Fx "$avd"
  emulator_log="$ANDROID_USER_HOME/$avd.log"
  "$sdk/emulator/emulator" -avd "$avd" -port "$port" -no-window -no-audio -no-boot-anim \
    -no-snapshot -wipe-data -gpu swiftshader_indirect >"$emulator_log" 2>&1 &
  emulator_pid=$!
  prepare_device
}

umask 077
fixture="$ANDROID_USER_HOME/simpleoffice-sms-backup.json"
launch_fresh_avd 'simpleoffice-sms-export' 5560
timeout 360 python -m tools.android_sms_download_acceptance --adb "$adb_bin" --serial "$serial" --save-fixture "$fixture"
stop_avd
launch_fresh_avd 'simpleoffice-sms-recovery' 5562
# Only the file produced by the first AVD crosses to the second, never app state.
adb_for_test shell mkdir -p /sdcard/Download
adb_for_test push "$fixture" /sdcard/Download/simpleoffice-sms-backup.json
timeout 360 python -m tools.android_sms_download_acceptance --adb "$adb_bin" --serial "$serial" --restore
