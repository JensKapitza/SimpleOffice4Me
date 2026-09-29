# System Health 3.0

Issue #502 introduces a bounded internal health registry without an external monitoring dependency.

## Endpoints

- GET /health/live is intentionally minimal and only reports that the web process can answer.
- GET /health/ready runs the registered core checks and returns HTTP 503 only when a required component is unavailable or unknown. Optional components never make readiness false.
- GET /admin/v3/health shows detailed, authenticated administrator diagnostics when the v3.health capability is enabled.
- GET /admin/v3/health.json exposes the same bounded details to administrators.

Public responses do not include component names, filesystem paths, exception strings, credentials, configuration values or stack traces.

## Status model

Checks return one of healthy, degraded, unavailable, not_configured or unknown. Core checks participate in readiness. Optional not_configured checks are neutral. Optional degraded or unavailable checks make the detailed overall status degraded while the application remains ready.

The initial registry covers the authentication database, document storage, V2 cutover/migration state, document index, background jobs, ClamAV, S3 overlay and Federation 3.0. Components can add checks through HealthRegistry without changing the public endpoint contract.

## Time limits and metrics

Every check has an individual timeout and every registry run has a total deadline. Timed-out or failed probes return stable error codes instead of exception text. Metrics are deliberately bounded scalar values only, for example queue counts, oldest queued job age, indexed file count and free storage in MiB.

## Rollout

SIMPLEOFFICE_V3_HEALTH_ENABLED enables the detailed admin dashboard. The capability is disabled by default. Liveness/readiness remain available because deployment supervisors must not depend on an optional UI flag.

No repair action is executed by a health check.
