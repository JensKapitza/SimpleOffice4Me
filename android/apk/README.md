# SimpleOffice4Me Android APK

This project is the Android counterpart of the Electron desktop wrapper. Electron itself does not run on Android, so the APK uses an Android WebView plus an embedded Chaquopy Python runtime.

## Architecture

- Android Java activity starts Chaquopy.
- Existing `app/`, `templates/` and `static/` are copied into APK assets during the Gradle build.
- At runtime these assets are copied into the application's private files directory.
- The WebView uses `127.0.0.1:8765`; Flask also listens on the device interfaces for LAN federation.
- The WebView opens only the local application; external HTTP(S) links are handed to Android.
- SQLite, secrets and application data remain in Android private app storage.
- Cleartext HTTP is permitted only for localhost/127.0.0.1.

The installable APK requires Android API 24 / Android 7.0 or newer:

- `arm64-v8a`: modern 64-bit ARM devices, using Python 3.14.

Legacy ARM32 is not offered because no verified Pillow 13 wheel is available for that ABI.

## Native Android integration

The APK is also an Android share target for single and multiple files. Shared `content://` files are passed to the existing SimpleOffice document upload form and still require the normal user confirmation before import.

Downloads from the local authenticated WebView use Android's Storage Access Framework, so the user chooses the destination without a broad storage permission. Saved documents can then be opened with another Android app.

A fixed `simpleoffice4me://open/...` deep-link namespace supports navigation to selected SimpleOffice screens. The mapping is an allowlist rather than an arbitrary internal URL launcher.

Lifecycle handling pauses and resumes the WebView together with the Android activity, which is especially useful on older tablets. Details, security boundaries and examples are documented in `docs/ANDROID_INTEGRATION.md`.

The **Bildschirm** page can open Android's system cast settings when provided by the device. Its device-independent fallback uses a user-approved MediaProjection foreground service and exposes the captured video to the existing SimpleOffice WebRTC sender. The active notification and the web UI can both stop capture.

### Device sources: Android calendar and contacts

In the installed APK, sign in to the embedded application and open **Communication → Device sources** (German: **Kommunikation → Gerätequellen**). Choose Calendar or Contacts, then approve just that source in the Android permission dialog. A native dialog shows up to 12 visible calendar events in the next 30 days or the first 20 contact display names. These are Android provider previews, not a Google OAuth connection or an import. Times follow the device's settings; all-day dates are shown without a time. Android may use an email address as a contact's display name.

No device entries are sent to Flask, browser scripts, paired peers or Google, and no entries are saved or logged. The browser launches the native view only on the trusted APK page. A fixed loopback preflight checks the active session, CSRF and the corresponding SimpleOffice feature before opening and rechecks after Android's permission dialog. Feature access and the OS permission are independently required. Android system settings can revoke either permission. The native view is discarded when the activity leaves the foreground or the page changes.

Acceptance on a dedicated APK test device: add one synthetic calendar event and one synthetic contact to the corresponding Android apps, start the installed ARM64 debug APK, sign in locally, and grant Calendar only. Verify its event appears; Contacts must independently describe its missing permission. Grant Contacts and verify the synthetic name. Revoke one permission in Android settings and relaunch; that source must not show records, while the other remains usable. An empty source reports that it was readable but had no matching entries. Verify the desktop browser doesn't show the device menu and a direct visit to the page only explains the APK requirement. Don't use a personal address book or Google account in this test.

## Build

Requirements: JDK 17, Android SDK 36 and Gradle 8.13.

The supported local build uses ARM64 and Python 3.14. It requires the pinned
Pillow and cryptography wheels produced by the Android native-wheel build. The
wheel preflight downloads the ReportLab universal wheel from PyPI and verifies
its published SHA-256 before Gradle uses it:

1. Download `android-cp314-arm64-Pillow-wheel` and
   `android-cp314-arm64-cryptography-wheel` from the Android native-wheel CI.
2. Extract both wheels and their `*-SHA256SUMS` files into
   `android/apk/app/wheelhouse` and verify them with:

   ```bash
   cd android/apk/app/wheelhouse
   sha256sum --check Pillow-SHA256SUMS cryptography-SHA256SUMS
   ```
3. From the repository root, run the compatibility preflight. This verifies
   the native wheels and adds the hash-verified ReportLab wheel to the wheelhouse:

   ```bash
   python tools/check_chaquopy_wheels.py \
     --python 3.14 \
     --abi arm64-v8a \
     --wheel-dir android/apk/app/wheelhouse
   ```
4. Build the APK as shown below.

```bash
cd android/apk
export CHAQUOPY_BUILD_PYTHON="$(command -v python3.14)"
export SIMPLEOFFICE_ANDROID_WHEEL_DIR="$PWD/app/wheelhouse"
sdkmanager 'ndk;28.2.13676358' 'cmake;3.22.1'
gradle assembleDebug
```

The debug APK is signed automatically with the Android debug key and is directly installable:

```text
app/build/outputs/apk/debug/app-debug.apk
```

A normal `assembleRelease` without a complete release signing configuration fails with a clear error instead of producing an unsigned APK. Debug builds are unaffected.

## Release signing (persistent update-stable key)

CI debug APKs are development-only artifacts: every ephemeral runner signs with its own debug key, so they are not update-stable across runs or machines. For installable releases that support in-place updates, create one persistent release key once and keep it outside the repository:

1. Generate the keystore once, outside the repository (for example `~/.android/simpleoffice-release.jks` on Linux/macOS or `%USERPROFILE%\.android\simpleoffice-release.jks` on Windows). **Back it up securely and separately: losing the keystore or its passwords permanently ends the update chain for this certificate — a replacement key requires all users to uninstall and reinstall.**
2. Configure the four settings as per-user Gradle properties in `~/.gradle/gradle.properties` (or the equivalent Windows path), or as environment variables. Never commit them:

   ```properties
   simpleofficeReleaseKeystore=/home/user/.android/simpleoffice-release.jks
   simpleofficeReleaseKeystorePassword=<keystore password>
   simpleofficeReleaseKeyAlias=<key alias>
   simpleofficeReleaseKeyPassword=<key password>
   ```

   Environment equivalents: `SIMPLEOFFICE_RELEASE_KEYSTORE`, `SIMPLEOFFICE_RELEASE_KEYSTORE_PASSWORD`, `SIMPLEOFFICE_RELEASE_KEY_ALIAS`, `SIMPLEOFFICE_RELEASE_KEY_PASSWORD`. The build reads only these user-level properties or environment variables — never repository files — and never prints credential values.
3. Build the signed release from `android/apk`:

   ```bash
   gradle assembleRelease
   ```

   The build refuses to run when any of the four settings is missing or the keystore file is unreadable.
4. Verify both signature schemes and the certificate SHA-256 fingerprint:

   ```bash
   apksigner verify --verbose --print-certs --min-sdk-version 23 app/build/outputs/apk/release/app-release.apk
   ```

   Compare the SHA-256 digest against the fingerprint recorded at key creation
   before distributing the APK.

   The APK's minimum SDK is 24. Passing `--min-sdk-version 23` makes apksigner
   verify the backward-compatible v1 scheme as well as v2; its default
   verification skips v1 when v2 is present and minSdk is 24 or newer.

Signature migration: if a device has a previously installed APK signed with a different certificate (for example a CI debug APK), Android requires uninstalling it once before the first release install — certificates cannot be migrated. Thereafter, the same release certificate plus a higher `versionCode` enables in-place updates.

## CI release builds (signed, update-stable)

The `Android APK release (signed)` workflow builds `assembleRelease` with the persistent update key and asserts both v1/v2 signatures plus the signer fingerprint before upload. Because the APK's minSdk is 24, CI invokes `apksigner verify --min-sdk-version 23` so the verification actually exercises v1; the ordinary verifier otherwise skips v1 when v2 is present. Signing is restricted to `main` and version tags whose commit is already reachable from `main`; feature-branch dispatches are refused before secrets are loaded. It runs on manual dispatch from `main` and as part of every valid tag release (`project-release.yml`); the signed APK is collected into the release package as `SimpleOffice4Me-Android-arm64-RELEASE-<date>-<sequence>.apk`. PR CI separately builds `assembleRelease` with an ephemeral throwaway key and verifies both signature schemes using the same minimum-SDK override; that smoke artifact is not uploaded and does not use production secrets. Debug CI APKs remain reinstall-only test artifacts.

Working copies of the signing material live in GitHub Secrets (`ANDROID_RELEASE_KEYSTORE_BASE64`, `ANDROID_RELEASE_KEYSTORE_PASSWORD`, `ANDROID_RELEASE_KEY_PASSWORD`; alias `simpleoffice-release`); the only backup is outside the repository — losing it ends the update chain (see above). Expected release certificate (public, verify before trusting an APK):

```text
SHA256: 4A:53:91:1F:19:6F:8C:A6:C9:1A:6E:C9:26:DB:2C:55:5B:65:EB:8F:5F:58:F0:17:18:A3:0E:53:7F:8A:F4:4C
```

GitHub Actions builds and verifies the supported architecture. The APK filename includes the UTC
build date and GitHub Actions workflow run number:

```text
simpleoffice4me-android-installable/
  SimpleOffice4Me-Android-arm64-20261021-123.apk
  SimpleOffice4Me-Android-arm64-20261021-123.apk.sha256
```

The Android package displays a version such as `2.20261021.123` (major project
version, UTC date, build sequence). `versionCode` is an independent, increasing
integer computed from the date and sequence. Local builds without an explicit
sequence use a deterministic UTC time slot (10-second slots, 1..8640) so
same-day rebuilds increase `versionCode`; set `SIMPLEOFFICE_BUILD_DATE=YYYYMMDD`
and `SIMPLEOFFICE_BUILD_SEQUENCE=123` to reproduce a particular CI version —
an explicit sequence always wins.

**Update signing:** CI debug APKs use a debug signing key that may differ between
workflow runs or machines. A higher `versionCode` alone does not permit an
in-place update: Android also requires the same signing certificate and
application ID. Use the persistent release signing key above before
promising upgrades without reinstalling; never commit signing credentials.

CI verifies that the APK contains only the requested native ABI and checks the signature with `apksigner`. ARM64 is checked for 16 KiB native-library alignment.

## Current Android-specific limits

Background indexer, OSM indexer, data logger and MCP are disabled in the embedded
Android runtime. External tools such as LibreOffice, Ghostscript, ClamAV,
Poppler, the Tesseract command-line program, and Piper with its voice model
are not bundled. The app includes a native Tesseract backend for image OCR;
it does not provide PDF OCR or a command-line executable. Android's built-in
PDF renderer can display PDF pages without those tools. For photo
capture, the WebView opens Android's file chooser; if it offers no camera,
take the photo in the Android Camera app first and then select or share it.

To use the OCR or speech tools on a separately hosted SimpleOffice server,
open that server's HTTPS address in the browser or install its PWA. The native
APK uses its own backend and private data on the phone. See
[SimpleOffice4Me on Android](../../docs/ANDROID.md) for the two workflows.

The Android build has its own dependency pins because binary Python packages
must have Android-compatible wheels.

## Bundled offline image OCR

Use **Documents → Image analysis → Run OCR** on a JPEG or PNG to recognize
German and English text locally, including when the device is offline. The
document keeps its original bytes. The result uses that exact content digest
and is made available in existing document search. Server/Docker/PWA and
PDF OCR paths are unchanged.

The build stages only the upstream [tessdata_fast 4.1.0](https://github.com/tesseract-ocr/tessdata_fast/releases/tag/4.1.0)
German and English models from commit `65727574dfcd264acbb0c3e07860e4e9e9b22185`.
The build checks size and SHA-256, embeds both files and their Apache-2.0
license under `assets/android-ocr/`, and fails on missing or changed bytes.
At first use, the APK verifies and copies those included assets to its private
no-backup area. No model bytes, images or OCR results are sent over the network.

Pillow checks the image size, applies orientation, composites transparency
onto white and downscales before passing only fixed-stride 8-bit grayscale
pixels to the native engine. There is no browser-to-native JavaScript OCR
interface. Existing authenticated document access and CSRF checks still apply.
The caller waits at most 25 seconds for the native worker. If it has not yet
exited, the same worker retains the single admission slot rather than starting
another native job.

## Launcher-Logo im fertigen APK prüfen

Das Launcher-Logo stammt aus `docs/assets/simpleoffice-documentation-logo.png`.
Der Android-Build erzeugt daraus die fünf Standarddichten für das 48dp-Icon
sowie den Vordergrund für Adaptive Icons. Nach dem Build prüft die CI das
fertige APK einschließlich PNG-Dekodierung und sichtbar belegter Bildfläche:

```sh
python -m pip install 'Pillow==13.0.0'
python tools/check_android_launcher_assets.py android/apk/app/build/outputs/apk/debug/app-debug.apk
```

Der Befehl liefert JSON und Exitcode 0 bei gültigen Ressourcen; fehlende,
beschädigte oder falsch dimensionierte Launcher-Bilder liefern Exitcode 1.
ARM64 wird geprüft. Die bestehenden APK-Auslöser bleiben erhalten:
Push auf `main` und manueller Workflow-Start. Für die PR-Abnahme wird der
Workflow manuell gegen den geprüften PR-Branch gestartet. Die Prüfung ersetzt
keinen visuellen Test des Launchers auf einem Android-Gerät.
