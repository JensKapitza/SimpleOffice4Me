# Software bill of materials

CI generates CycloneDX 1.6 JSON and XML inventories from resolved build inputs
and installed runtime environments. Validators are downloaded from a pinned
CycloneDX CLI release and checked against a pinned SHA-256 digest before use.

## Platform inventories

- Python inventories resolve the installed distribution metadata graph. The
  `--python-environment` option reads a copied virtual environment's
  `site-packages`, allowing CI to inventory packages from the built Docker image
  without copying packages from the host. CI also reads PEP 508 marker values
  from the built image itself, so architecture- and Python-version-specific
  edges are resolved for the target container rather than the runner. The
  container inventory includes the `ocr` extra used by the image.
- Android records the resolved Gradle runtime graph and reads the Chaquopy wheel
  metadata from the completed APK. Android SBOMs are uploaded before the Pillow
  security gate so a blocked APK still has a reviewable package inventory.
- Tauri inventories locked Cargo metadata and the platform-filtered npm lock;
  Electron inventories its npm lock for Linux, Windows, and macOS.
- The published multi-platform Docker image also enables BuildKit's SBOM
  attestation for image-level packages. Per-architecture CycloneDX XML and JSON
  artifacts inventory the Python application environment from the built image.

Artifacts are retained by CI for 14 days. Release workflows remain responsible
for attaching the final release SBOMs. Missing or unresolved inventory records
fail closed rather than being represented as a complete SBOM.

## Current Android constraint

The ARM64 Android profile targets Python 3.15, and the legacy ARM32/Python 3.11
profile is only available through manual workflow dispatch. The Android APK job
checks the actual Chaquopy wheel index before Gradle runs and rejects the APK if
its bundled Pillow is below the patched baseline. If a required ABI wheel is not
available, the APK build remains blocked; an experimental source-build result
is not treated as a supported runtime until it is installed into and verified
in a real APK.
