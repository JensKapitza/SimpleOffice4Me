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

The initial APK targets `arm64-v8a`, Android API 24 or newer. This includes modern ARM64 devices such as the Poco F6 Pro.

## Build

Requirements: JDK 17, Android SDK 36, Gradle 8.13 and Python 3.13 for the Chaquopy build host.

```bash
cd android/apk
gradle assembleDebug
```

The debug APK is signed automatically with the Android debug key and is directly installable:

```text
app/build/outputs/apk/debug/app-debug.apk
```

A normal `assembleRelease` without a configured release signing key creates an unsigned APK and must not be distributed as an installable package.

GitHub Actions therefore publishes only the verified signed artifact:

```text
simpleoffice4me-android-installable/
  SimpleOffice4Me-Android-arm64.apk
  SimpleOffice4Me-Android-arm64.apk.sha256
```

Before upload, CI verifies the APK signature with `apksigner` and validates 16 KiB page/alignment compatibility with `zipalign -P 16`.

## Current Android-specific limits

Background indexer, OSM indexer, data logger and MCP are disabled in the embedded Android runtime. External desktop tools such as LibreOffice, Ghostscript and ClamAV are not bundled. The Android build has its own dependency pins because binary Python packages must have Android-compatible wheels.
