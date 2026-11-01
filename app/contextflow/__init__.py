"""ContextFlow: verified office follow-ups (reads, hints, approved writes).

Batch 1 provides read-only, actor-scoped domain adapters plus an evidence
contract. Approved writes (tasks, findings) always need explicit user
confirmation; Meta-model backends stay local by default, Meta cloud only
with consent on synthetic demo roots.
"""

from .adapters import ContextflowReads

__all__ = ["ContextflowReads"]
