"""SQLite connection helpers with explicit resource ownership."""
from __future__ import annotations

import sqlite3
from typing import Any


class ClosingConnection(sqlite3.Connection):
    """Connection that preserves sqlite3 transaction semantics and closes on exit."""

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def connect(database: str | bytes, *args: Any, **kwargs: Any) -> sqlite3.Connection:
    """Create a connection whose context-manager exit also closes the handle."""
    kwargs.setdefault("factory", ClosingConnection)
    return sqlite3.connect(database, *args, **kwargs)
