# Android build profiles

The supported default Android artifact is **ARM64** (`arm64-v8a`) using
Chaquopy 17.1 and Python 3.15. It is built automatically by the
[Android APK build](../.github/workflows/android-apk-build.yml) GitHub Actions
workflow on `main` and on relevant pull requests.

The **legacy ARM32** (`armeabi-v7a`) artifact remains available on demand.
It uses Python 3.11 because Chaquopy does not provide Python 3.15 for
32-bit Android. ARM32 is **not** part of the automatic build matrix.

## Build ARM32 on demand

1. Open **GitHub → Actions → Android APK build → Run workflow**.
2. Select the branch to test.
3. Check **Also build legacy ARM32 APK (Python 3.11)** (`build_arm32`).
4. Run the workflow. It builds both ARM64 and ARM32 and uploads the APKs
   as separate artifacts:
   - `simpleoffice4me-android-installable` (ARM64)
   - `simpleoffice4me-android-arm32-installable` (ARM32)

A manual run with the checkbox **off** builds only ARM64.

## Security and dependency verification

Each built APK is checked for native ABI, signing, alignment and embedded
Python dependencies. CycloneDX 1.6 JSON/XML inventories are published
separately for the resolved Gradle runtime and APK-bundled Chaquopy packages.

The APK-bundled Pillow version must be at least 12.2.0. An unsupported
or unavailable Android wheel must fail the build; **do not** bypass this
gate or substitute a host-Python package inventory.

The legacy ARM32 path is a compatibility build, not a promise of ongoing
Python 3.11 security maintenance. If its pinned native wheels cannot
meet the security baseline, leave ARM32 unavailable until a safe build
can be demonstrated.
