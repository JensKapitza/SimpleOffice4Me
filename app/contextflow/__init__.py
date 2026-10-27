"""ContextFlow: authorized read layer for verified office follow-ups.

Batch 1 provides read-only, actor-scoped domain adapters plus an evidence
contract. No writes, no network calls and no model calls in this package.
"""

from .adapters import ContextflowReads

__all__ = ["ContextflowReads"]
