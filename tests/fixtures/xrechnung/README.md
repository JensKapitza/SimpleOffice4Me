# KoSIT XRechnung validation fixtures

The files `ubl001-valid.xml` and `ubl002-rejected.xml` are unmodified test
instances from `itplr-kosit/validator-configuration-xrechnung`, release
`v2026-08-31`.

- upstream paths:
  - `src/test/instances/processing-valid/ubl001.xml`
  - `src/test/instances/processing-valid/ubl002.xml`
- upstream license: Apache License 2.0
- purpose here: positive/negative regression checks against the pinned
  XRechnung 3.0.2 / KoSIT Validator 1.6.3 runtime.

The upstream release assertions mark `ubl001-report.xml` valid and
`ubl002-report.xml` invalid/rejected.


## Build-time placeholder

The upstream `processing-valid` source instances intentionally contain
`@xrechnung.spec.id@`. The upstream Ant build replaces that marker before running
KoSIT. The integration test keeps the source fixtures byte-for-byte unchanged and
applies the same published XRechnung 3.0 scenario identifier in memory immediately
before validation. This prevents a false rejection caused only by an unexpanded
upstream build placeholder.
