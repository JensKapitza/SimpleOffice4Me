#!/usr/bin/env bash
# Real provider (only synthetic SMS), only a fresh CI-owned AVD.
set -euo pipefail
apk=${1:?path to the optional SMS debug APK required}
test -r "$apk"
sdk=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
if [[ "${GITHUB_ACTIONS:-}" != true || ! -c /dev/kvm || -z "$sdk" ]]; then
  echo 'This acceptance provisions a fresh CI-owned AVD and never touches existing devices.' >&2
  exit 2
fi
if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
  sudo chown "$(id -u):$(id -g)" /dev/kvm
fi
image='system-images;android-35;google_apis;x86_64'
timeout 480 "$sdk/cmdline-tools/latest/bin/sdkmanager" 'platform-tools' 'emulator' "$image"
adb="$sdk/platform-tools/adb"
export ANDROID_USER_HOME ANDROID_AVD_HOME
ANDROID_USER_HOME="$(mktemp -d "${RUNNER_TEMP:?}/sms-device-android.XXXXXX")"
ANDROID_AVD_HOME="$ANDROID_USER_HOME/avd"
mkdir -p "$ANDROID_AVD_HOME"
serial=emulator-5576
timeout 30 "$adb" start-server
if "$adb" devices | grep -q "^$serial[[:space:]]"; then
  echo 'Refusing to touch an existing emulator.' >&2
  exit 2
fi
printf 'no\n' | "$sdk/cmdline-tools/latest/bin/avdmanager" create avd \
  -n so4me-direct-sms -k "$image" -p "$ANDROID_AVD_HOME/so4me-direct-sms.avd"
"$sdk/emulator/emulator" -avd so4me-direct-sms -port 5576 -no-window -no-audio \
  -no-boot-anim -no-snapshot -wipe-data -gpu swiftshader_indirect >"$ANDROID_USER_HOME/emulator.log" 2>&1 &
pid=$!
cleanup() {
  status=$?
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  if ((status != 0)); then grep -E '^(ERROR|FATAL)' "$ANDROID_USER_HOME/emulator.log" | tail -n 10 >&2 || true; fi
}
trap cleanup EXIT
device() { timeout 100 "$adb" -s "$serial" "$@"; }
booted=false
for ((i=0; i<60; i++)); do
  kill -0 "$pid"
  if [[ "$(timeout 4 "$adb" -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; then
    booted=true
    break
  fi
  sleep 3
done
[[ "$booted" == true ]]
[[ "$(device shell getprop ro.build.version.sdk | tr -d '\r')" == 35 ]]
[[ "$(device shell pm list packages de.simpleoffice4me.android | tr -d '\r')" == '' ]]
device install "$apk"  # Do not pre-grant permissions: drive the actual Android dialog.
device shell run-as de.simpleoffice4me.android mkdir -p shared_prefs
device shell -T "run-as de.simpleoffice4me.android sh -c 'umask 077; cat > shared_prefs/simpleoffice-android-identity.xml'" <<'XML'
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <boolean name="identity-ready" value="true" />
    <string name="google-email"></string>
</map>
XML
device shell svc power stayon true
device shell settings put system screen_off_timeout 1800000
device shell input keyevent KEYCODE_WAKEUP
device shell wm dismiss-keyguard
timeout 450 python -m tools.android_sms_device_import_acceptance --adb "$adb" --serial "$serial"
