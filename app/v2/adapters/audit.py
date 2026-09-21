"""V2 audit adapter backed by the existing tamper-evident RevisionHistory."""

from __future__ import annotations

from pathlib import Path

from ..contracts import AuditEvent, AuditPort, ErrorCode, OperationResult
from ...revision_history import RevisionHistory


class RevisionHistoryAuditAdapter(AuditPort):
    """Bridge V2 audit events to the existing append-only revision trail.

    The adapter deliberately stores only the already-bounded V2 event fields.
    RevisionHistory applies its existing secret redaction and tamper-evident
    event-chain rules.
    """

    def __init__(self, root: str | Path):
        self.history = RevisionHistory(Path(root))

    def append(self, event: AuditEvent) -> OperationResult[str]:
        try:
            snapshot = {
                "occurred_at": event.occurred_at,
                "source": event.source,
                "correlation_id": event.correlation_id,
                "changes": dict(event.changes),
            }
            revision_id = self.history.record(
                event.operation,
                event.actor,
                "v2",
                event.object_id,
                snapshot,
            )
            return OperationResult.success(revision_id)
        except (OSError, ValueError, TypeError) as exc:
            return OperationResult.failure(
                ErrorCode.STORAGE_UNAVAILABLE,
                "audit event could not be persisted",
                retryable=isinstance(exc, OSError),
            )
