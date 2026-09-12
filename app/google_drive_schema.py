"""Additive SQLite schema for Google Drive synchronization state."""

from __future__ import annotations

from .db import get_db


def ensure_google_drive_schema() -> None:
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS google_drive_state (
            user_id INTEGER PRIMARY KEY,
            root_folder_id TEXT,
            root_folder_name TEXT NOT NULL DEFAULT 'SimpleOffice4Me',
            page_token TEXT,
            direction TEXT NOT NULL DEFAULT 'bidirectional',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_sync_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES user(id)
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS google_drive_link (
            user_id INTEGER NOT NULL,
            drive_file_id TEXT NOT NULL,
            document_id TEXT,
            local_relative_path TEXT,
            remote_name TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
            remote_parent_id TEXT,
            remote_md5 TEXT,
            remote_modified_at TEXT,
            remote_version TEXT,
            web_view_link TEXT,
            local_sha256 TEXT,
            last_synced_local_sha256 TEXT,
            last_synced_remote_version TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            last_sync_at TEXT,
            last_error TEXT,
            PRIMARY KEY(user_id, drive_file_id),
            FOREIGN KEY (user_id) REFERENCES user(id)
        )"""
    )
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS google_drive_link_local
           ON google_drive_link(user_id, local_relative_path)
           WHERE local_relative_path IS NOT NULL AND local_relative_path <> ''"""
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS google_drive_link_status
           ON google_drive_link(user_id, status, last_sync_at DESC)"""
    )
    db.commit()


def init_app(app) -> None:
    with app.app_context():
        ensure_google_drive_schema()
