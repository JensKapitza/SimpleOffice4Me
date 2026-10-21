# Android build profiles

The supported Android artifact is **ARM64** (`arm64-v8a`) using Chaquopy 17.1
and Python 3.13. It is built automatically by the
[Android APK build](../.github/workflows/android-apk-build.yml) GitHub Actions
workflow on `main` and on relevant pull requests.

Legacy ARM32 (`armeabi-v7a`) is not offered because the patched Pillow 13
baseline has no verified wheel for that ABI.

## Security and dependency verification

Each built APK is checked for native ABI, signing, alignment and embedded
Python dependencies. CycloneDX 1.6 JSON/XML inventories are published
separately for the resolved Gradle runtime and APK-bundled Chaquopy packages.

The APK-bundled Pillow version must be at least 13.0.0. An unsupported or
unavailable Android wheel must fail the build; **do not** bypass this gate or
substitute a host-Python package inventory. The ARM64 APK remains blocked until
the Pillow 13 and cryptography 48 native wheels, plus the hash-verified
ReportLab universal wheel, are installed into and verified in a real APK.

Reintroduce an ABI only after its pinned native wheels pass compatibility
preflight and a real APK build proves the security baseline.
