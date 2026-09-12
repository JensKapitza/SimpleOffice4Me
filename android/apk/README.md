# SimpleOffice4Me Android APK

This project is the Android counterpart of the Electron desktop wrapper. Electron itself does not run on Android, so the APK uses an Android WebView plus an embedded Chaquopy Python runtime.

## Architecture

- Android Java activity starts Chaquopy.
- Existing `app/`, `templates/` and `static/` are copied into APK assets during the Gradle build.
- At runtime these assets are copied into the application's private files directory.
- Flask starts only on `127.0.0.1:8765`.
- The WebView opens only the local application; external HTTP(S) links are handed to Android.
- SQLite, secrets and application data remain in Android private app storage.
- Cleartext HTTP is permitted only for localhost/127.0.0.1.

Both APK variants require Android API 24 / Android 7.0 or newer:

- `arm64-v8a`: modern 64-bit ARM devices, using Python 3.13.
- `armeabi-v7a`: legacy 32-bit ARM devices, using Python 3.11. This is intended for devices such as a Galaxy Tab 3 running an Android 7 ROM when the device userspace is 32-bit.

The two builds are intentionally separate. Keeping ARM32 out of the modern APK avoids increasing the normal package size and lets the current ARM64/Python 3.13 runtime stay unchanged.

## Build

Requirements: JDK 17, Android SDK 36 and Gradle 8.13.

The default local build remains ARM64 and uses Python 3.13:

```bash
cd android/apk
export CHAQUOPY_BUILD_PYTHON="$(command -v python3.13)"
gradle assembleDebug
```

For ARM32, use a Python 3.11 build interpreter and select the matching runtime profile:

```bash
cd android/apk
export SIMPLEOFFICE_ANDROID_ABI=armeabi-v7a
export SIMPLEOFFICE_ANDROID_PYTHON=3.11
export CHAQUOPY_BUILD_PYTHON="$(command -v python3.11)"
gradle assembleDebug
```

The debug APK is signed automatically with the Android debug key and is directly installable:

```text
app/build/outputs/apk/debug/app-debug.apk
```

A normal `assembleRelease` without a configured release signing key creates an unsigned APK and must not be distributed as an installable package.

GitHub Actions builds and verifies both architectures independently. The ARM64 artifact name remains unchanged for compatibility, and ARM32 is published separately:

```text
simpleoffice4me-android-installable/
  SimpleOffice4Me-Android-arm64.apk
  SimpleOffice4Me-Android-arm64.apk.sha256

simpleoffice4me-android-arm32-installable/
  SimpleOffice4Me-Android-arm32.apk
  SimpleOffice4Me-Android-arm32.apk.sha256
```

CI verifies that each APK contains only the requested native ABI. It also verifies the APK signature with `apksigner`. ARM64 is checked for 16 KiB native-library alignment, while the Android-7-oriented ARM32 package uses the legacy 4 KiB alignment check.

## Current Android-specific limits

Background indexer, OSM indexer, data logger and MCP are disabled in the embedded Android runtime. External desktop tools such as LibreOffice, Ghostscript and ClamAV are not bundled. The Android build has its own dependency pins because binary Python packages must have Android-compatible wheels.
