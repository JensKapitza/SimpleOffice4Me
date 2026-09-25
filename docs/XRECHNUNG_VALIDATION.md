# XRechnung validation baseline (2026)

SimpleOffice uses the published KoSIT toolchain as an **offline validation
boundary** for XRechnung XML. It does not reimplement the XRechnung rules in
Python.

## Pinned baseline

- XRechnung: **3.0.2**
- KoSIT Validator: **1.6.3**
- validator configuration: **2026-08-31**
- CEN Schematron used by that configuration: **1.3.16**
- Java: **11 or newer** (KoSIT requirement for the 1.6.x line)

The bootstrap in `tools/install_xrechnung_validator.py` downloads only the two
named release artifacts and verifies hard-coded SHA-256 release digests before
publication:

- `validator-1.6.3-standalone.jar`
  - SHA-256 `799e64befca97d4080e03608c80b85dd5a5ecc5f4ae4f35d1116ec2855b9a7c9`
- `xrechnung-3.0.2-validator-configuration-2026-08-31.zip`
  - SHA-256 `2530cd107c414511c5d0462ec10f886910395abfca820db82e83d70bf01221a8`

Those digests are the release-asset digests published by the corresponding
KoSIT GitHub releases. The configuration is extracted with traversal, symlink,
file-count and expanded-size checks. A local SHA-256 manifest is generated and
rechecked before every validation.

Validator 1.6.3 is required rather than an older 1.6.x release because it fixes
the upstream unrestricted URI-resolution issue in strict-local resource
handling.

## Runtime boundary

`app.xrechnung_validation.validate_xrechnung(...)`:

1. bounds the XML size;
2. parses with `defusedxml` before Java is started;
3. verifies the installed JAR and every extracted configuration file;
4. runs Java without a shell, with the local `scenarios.xml` and local
   configuration repository;
5. discards validator stdout/stderr so invoice contents cannot be copied into
   application logs by this boundary;
6. returns only structured acceptance/status metadata.

Run manually with:

```bash
python -m tools.validate_xrechnung path/to/invoice.xml
```

Exit status is 0 for an accepted document, 1 for a completed validation that
rejects the document and 2 when validation could not be completed.

## CI evidence

The standards CI job installs the pinned runtime and validates two unmodified
Apache-2.0 KoSIT fixtures from configuration release `v2026-08-31`:

- `ubl001-valid.xml` must be accepted;
- `ubl002-rejected.xml` must be rejected.

Their provenance and upstream license are stored beside the fixtures.

## ZUGFeRD / Factur-X boundary

SimpleOffice invoice generation remains separately pinned to **ZUGFeRD 2.5.2 /
EN16931** and uses the existing Mustang/PDF-A finalization path. Adding the
XRechnung validator does **not** claim that every generated ZUGFeRD invoice is
an XRechnung, and it does not remove any existing Factur-X/ZUGFeRD capability.
