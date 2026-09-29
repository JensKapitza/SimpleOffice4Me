# V3 Background Jobs

The V3 queue is optional and SQLite-backed. It does not require Redis, Celery or RabbitMQ. Jobs use idempotency keys, bounded payloads, worker leases, retry backoff and explicit terminal states.

A crashed worker leaves a lease that another worker can reclaim after expiry. Handler exceptions are sanitized before persistence. The first pilot handler is `documents.reindex`, which invokes the existing DocumentStore scan/index path rather than duplicating indexing logic.

The web application does not require the worker. With `v3.jobs` disabled, the queue UI reports the capability as disabled and the worker CLI exits without mutation.

## Queue-Zeitbasis

Neu eingestellte Jobs sind sofort claimbar (`available_at=0`); Retry-Termine werden erst beim Fehler relativ zur jeweiligen Worker-Zeit gesetzt. Das hält Recovery- und Test-Uhren deterministisch.
