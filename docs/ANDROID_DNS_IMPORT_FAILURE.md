# Android APK startup: missing dns module

## Problem
Android showed `RuntimeError: SimpleOffice4Me import failed: ModuleNotFoundError: No module named 'dns'`. The master-cluster module imported `dns.resolver` at module load, so an absent Chaquopy dependency prevented the whole Flask app from starting even on clients not using master clustering.

## Resolution
The master authority TXT lookup now uses Python's built-in `urllib.request` over HTTPS DNS JSON at `https://cloudflare-dns.com/dns-query`. The `dnspython` dependency was removed from desktop packaging and the Android Gradle configuration. The lookup only occurs when master mode requests an authority check.

The query is HTTPS-only with normal certificate verification, a three-second timeout, disabled proxy handling, no redirects, a 64-KiB response cap, exact TXT name/type checks and a strict 64-hex-character master ID. A failed query does not invent an authority. In backup-active mode, a missing authority results in `standby_unverified`.

## Operational trade-offs
This is a dependency-free Python implementation, **not** an offline or self-contained DNS resolver. Master authority checks require network access to Cloudflare; DNS split-horizon/private TXT records may not resolve there. The configured TXT record is disclosed to the DoH provider. DNS-over-HTTPS authenticates the resolver transport, not ownership of the TXT record; use DNSSEC and independent trust controls when appropriate. Clients without master mode do not need DoH to start.

## Verification
Run `python -m compileall -q app/master_cluster.py`, the master-cluster test suite, and the Android APK build and device startup smoke test. Check that no `dns` imports remain and that TXT success, malformed responses, timeouts, duplicate authorities and unreachable DoH all fail safely. An APK built before this change must be replaced.
