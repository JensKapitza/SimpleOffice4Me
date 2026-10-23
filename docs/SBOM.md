# Software bill of materials

CI generates CycloneDX 1.6 JSON and XML inventories from resolved build inputs
and installed runtime environments. Validators are downloaded from a pinned
CycloneDX CLI release and checked against a pinned SHA-256 digest before use.
Install the optional `sbom` extra (`python -m pip install -e '.[sbom]'`) before
running `tools/generate_sbom.py` outside the pinned CI environments.

## Platform inventories

- Python inventories resolve the installed distribution metadata graph. The
  `--python-environment` option reads a copied virtual environment's
  `site-packages`, allowing CI to inventory packages from the built Docker image
  without copying packages from the host. CI also reads PEP 508 marker values
  from the built image itself, so architecture- and Python-version-specific
  edges are resolved for the target container rather than the runner. The
  container inventory includes the `ocr` extra used by the image.
- Android uses the pinned CycloneDX Gradle plugin on the resolved runtime
  configuration, including available Maven license metadata, and separately
  reads Chaquopy wheel metadata from the completed APK. Android SBOMs are
  uploaded before the Pillow security gate so a blocked APK still has a
  reviewable package inventory.
- Tauri inventories locked Cargo metadata and the platform-filtered npm lock;
  Electron inventories its npm lock for Linux, Windows, and macOS.
- The published multi-platform Docker image also enables BuildKit's SBOM
  attestation for image-level packages. Per-architecture CycloneDX XML and JSON
  artifacts inventory the Python application environment from the built image.
  The container build also generates a combined image inventory so OS packages
  and Python packages from the same built image are represented together.

Artifacts are retained by CI for 14 days. Release workflows remain responsible
for attaching the final release SBOMs. Before checksumming and publishing, the
project release workflow validates every collected CycloneDX JSON/XML artifact
against the pinned 1.6 validator and requires both formats. Missing or
unresolved inventory records fail closed rather than being represented as a
complete SBOM.

## Vulnerability follow-up mapping

`docs/sbom-vulnerability-findings.json` records verified findings with their
component purl, affected versions/platforms, advisory severity source, fixed
version, blocker, follow-up issue and PR, relevant build, and last-reviewed
commit. A finding remains open until its fix is merged and verified in the
released platform artifact; a green PR build alone is recorded as
`remediated_pending_merge` rather than as a released fix. OSV severity is not
invented when the advisory supplies only a CVSS vector.

## Current Android constraint

The installable APK profile targets ARM64/Python 3.15. ARM32 is not offered
because the patched Pillow 13 baseline has no verified wheel for that ABI. CI
builds hash-verified ARM64 wheels for Pillow 13.0.0, cryptography 48.0.1, cffi
2.0.0, and argon2-cffi-bindings 21.2.0, installs them into the APK, then checks
that the APK metadata matches every direct Gradle pin and validates its
CycloneDX JSON/XML inventories. The Pillow 13.0.0 baseline is enforced against
the actual APK. This is a CI build/inventory acceptance; it does not claim
physical-device SAF or runtime acceptance.
