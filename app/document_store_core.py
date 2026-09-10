"""File based document storage and repairable scan index.

The filesystem is the source of truth.  SQLite only caches scan results and
can therefore be deleted and rebuilt at any time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import fnmatch
import shutil
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from xml.etree import ElementTree

import click
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from flask import current_app
from flask.cli import with_appcontext

from .retention import evaluate_deadlines, parse_deadline
from .revision_history import RevisionHistory
from .search_query import compile_query


CONTROL_DIR = ".simpleoffice-meta"
PREVIEW_CACHE_DIR = ".webcache"
POLICY_FILE = ".simpleoffice-folder.json"
EVENT_FILE = "events.ndjson"
HISTORY_DIR = ".simpleoffice-history"
ARCHIVES_FILE = "archives.json"
ARCHIVE_MARKER = ".simpleoffice-archive.json"
SHARES_FILE = "shares.json"
SSH_SOURCES_FILE = "ssh-sources.json"
MAX_WEBDAV_COLLECTION_MEMBERS = 2_000
MAX_WEBDAV_COLLECTION_DEPTH = 64
COLLECTION_TRASH_DIR = "webdav-collection-trash"
_STORE_INITIALIZATION_LOCK = threading.Lock()
_INITIALIZED_INDEXES: set[Path] = set()
_WAL_CONFIGURATION_LOCK = threading.Lock()
_WAL_CONFIGURED_INDEXES: set[Path] = set()

_SEARCH_SQL_WORDS = {
    "path", "state", "tags", "notes", "attributes", "content",
    "LIKE", "ESCAPE", "AND", "OR", "NOT", "CASE", "WHEN", "THEN",
    "ELSE", "END",
}

def _validated_search_where(fragment: str) -> str:
    words = set(re.findall(r"[A-Za-z_]+", fragment))
    if not words.issubset(_SEARCH_SQL_WORDS):
        raise ValueError("compiled document search contains an unsupported SQL token")
    stripped = re.sub(r"[A-Za-z_]+", "", fragment)
    if re.search(r"[^\s()?'=0-9\\]", stripped):
        raise ValueError("compiled document search contains unsupported SQL syntax")
    return fragment



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def ocr_subprocess_environment() -> dict[str, str]:
    """Return a bounded OpenMP environment for one Tesseract process."""
    environment = os.environ.copy()
    requested = environment.get(
        "SIMPLEOFFICE_OCR_THREADS",
        environment.get("OMP_THREAD_LIMIT", "1"),
    ).strip()
    try:
        threads = int(requested)
    except ValueError:
        threads = 1
    environment["OMP_THREAD_LIMIT"] = str(max(1, min(threads, 8)))
    return environment


@dataclass(frozen=True)
class ScanReport:
    files: int = 0
    new_files: int = 0
    updated_files: int = 0
    duplicates: int = 0
    symlinks: int = 0
    skipped_boundaries: int = 0
    errors: int = 0

# Mixins import private helpers too; this mirrors the original single-module
# globals without changing DocumentStore behavior.
__all__ = [name for name in globals() if not name.startswith("__")]
