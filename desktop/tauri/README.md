# SimpleOffice4Me Tauri 2 (experimental)

This is a separate Tauri 2 client for desktop and Android. Existing Electron and Chaquopy packages remain unchanged.

## What is included

- Rust/Tauri system-WebView application.
- HTTPS endpoint selection screen with URL validation.
- No native shell or filesystem permissions to remote web content.
- Linux desktop and ARM64 Android build jobs.

## Critical runtime limitation

This client connects to an **existing HTTPS Flask server**. It does not
currently package or launch Python, Flask, SQLite, OCR, ClamAV, LibreOffice,
Ghostscript, or other SimpleOffice4Me back-end dependencies. It is **not**
offline-capable or a drop-in replacement for the existing Android APK.
An Android localhost URL refers to the phone, not the computer hosting Flask.

## Developer builds

Prerequisites: Node.js 22, Rust stable, Tauri 2 platform dependencies.
For Linux install WebKitGTK 4.1 development libraries. Windows needs MSVC
build tools and WebView2; macOS needs Xcode command line tools.

From desktop/tauri:

    npm install
    npm run build

Android additionally needs JDK 17, Android SDK, Android NDK, and Rust
target aarch64-linux-android:

    rustup target add aarch64-linux-android
    npm install
    npm run android:init
    npm run android:build

CI workflow: .github/workflows/tauri-build.yml.
Build artifacts are for testing and are not signed production releases.

## Necessary follow-up for parity

- Desktop offline mode: bundle the existing desktop/python-setup backend as
  a platform-specific binary, start it, monitor health and shutdown, protect
  per-user storage, coordinate migrations, and test packaged binaries.
- Android offline mode: integrate an Android Python runtime such as Chaquopy
  through a mobile plugin; desktop sidecars are not Android executables.
- Security: review external navigation, cookie handling, auth, HTTPS TLS,
  CSRF and access-control in the existing application.
- Mobile integration: camera, QR, file picker, share intent, download,
  Android permissions, touch, lifecycle, and deep-link regression testing.
- Dependency quality: version lockfiles, compatible Python wheels, license
  inventory, SBOM, CI security scans, reproducible signing and ARM32 testing.
- Windows/macOS builds and signed release distribution.

References:
https://v2.tauri.app/start/prerequisites/
https://v2.tauri.app/develop/sidecar/
https://v2.tauri.app/develop/plugins/develop-mobile/
