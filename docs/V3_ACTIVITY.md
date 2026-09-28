# V3 Domain Events and Activity Stream

This layer is an activity feed and integration signal, **not event sourcing**. Existing domain data remains authoritative.

Events contain a stable event ID, schema version, UTC timestamp, actor, optional entity reference fields, correlation ID, source and bounded metadata. Known credential/secret fields are rejected.

Existing modules emit through a best-effort adapter only when `v3.activity` is enabled. Failure to persist an activity event never rolls back or breaks an already successful contact/project/document operation. Consumers are likewise isolated.

Security/audit logging remains separate. Activity retention or later pruning must never be used as an audit-log replacement.
