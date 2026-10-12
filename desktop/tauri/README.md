# SimpleOffice4Me Tauri 2 HTTPS client

![SimpleOffice4Me](../../docs/assets/simpleoffice-documentation-logo.png)

This client opens an existing SimpleOffice4Me HTTPS server in the operating
system WebView. Its version follows `pyproject.toml`; Bootstrap, application
styles and the logo are copied from the existing project by
`tools/prepare_tauri_client.py`. Existing Electron and Chaquopy packages
remain supported.

## Connect and change servers

Enter the server base URL, including its deployment path if applicable, and
select **Verbinden**. HTTPS is required. URLs containing credentials, a query,
a fragment or the reserved `tauri.localhost` host are rejected. The validated
base URL is retained locally and prefilled next time; opening the application
does not automatically connect. No passwords or tokens are stored by the
chooser. Sign in through the existing server login; roles, CSRF, sessions and
access checks remain the responsibility of that server.

On desktop, **SimpleOffice4Me → Serveradresse ändern** or **Ctrl+L**
(**Cmd+L** on macOS) returns to the chooser. On Android, reopen the app to
select a server. The endpoint must be reachable from that device: localhost
on a phone refers to the phone itself. Certificate validation is provided by
the system WebView and is never disabled. Private CAs must be trusted through
the operating system configuration. HTTP, file and data top-level navigation
are blocked by the native navigation handler; remote pages receive no native
filesystem, shell or application-command permissions.

## Developer builds

Prerequisites: Node.js 22, Python 3.11 or newer for metadata preparation, Rust
stable and the Tauri 2 platform dependencies. Linux requires WebKitGTK 4.1
development libraries; Windows requires MSVC and WebView2; macOS requires
Xcode command line tools. Node and Cargo dependencies have committed
lockfiles. From `desktop/tauri`:

```sh
npm ci --no-audit --no-fund
npm run prepare:client
npx tauri icon ../../docs/assets/simpleoffice-documentation-logo.png
npm run build -- -- --locked
```

Android additionally requires JDK 17, Android SDK platform/build-tools 35,
NDK 27.2.12479018 and the Rust ARM64 target:

```sh
rustup target add aarch64-linux-android
npm run android:init -- --ci
npm run android:build -- --target aarch64 -- --locked
```

`npm run tauri` is also available because the generated Android Gradle
project uses this script. Generated icons, assets, Android project, build
outputs and `node_modules` are excluded from Git.

CI: `.github/workflows/tauri-build.yml` (Linux and Android ARM64) and
`.github/workflows/tauri-desktop-crossplatform.yml` (Windows and macOS).
Artifacts are test builds, not signed production releases.

## Scope and operating limits

This HTTPS client does not bundle or start Python, Flask, SQLite, OCR,
ClamAV, LibreOffice or Ghostscript. It requires a running server and network
access; it is not an offline replacement for the existing embedded Android
APK. Camera, sharing, deep links and device-specific file/download handling
have no added native plugins in this client. Their availability depends on
the platform WebView. ARM32 packaging and production signing are outside
this client PR. Existing server workflows and the established desktop/mobile
packages are the appropriate choices when those capabilities are required.

The local chooser uses a restrictive content security policy and only basic
local window permissions. Native permissions have no remote URL allowlist.
Remote server content keeps its own CSP and security policy; connecting to
an untrusted HTTPS server does not establish that server's trustworthiness.

References: [prerequisites](https://v2.tauri.app/start/prerequisites/),
[capabilities](https://v2.tauri.app/security/capabilities/).
