from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Federation download queue: use three explicit static statements instead of
# appending WHERE/ORDER fragments to a shared SQL string.
replace_once(
    "app/federation_catalog.py",
    '''    def get_request(self, request_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(self._queue_sql(" WHERE q.request_id=?"), (request_id,)).fetchone()
        return self._request(row) if row else None

    def queue(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                self._queue_sql("") +
                " ORDER BY CASE q.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'retry' THEN 2 WHEN 'waiting_peer' THEN 3 ELSE 4 END, effective_priority DESC,q.created_at ASC LIMIT ?",
                (max(1, min(int(limit), 5000)),),
            ).fetchall()
        return [self._request(row) for row in rows]

    def next_requests(self, limit: int = 1) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                self._queue_sql(
                    " WHERE q.status IN ('queued','retry','waiting_peer') AND q.next_attempt_at<=?"
                ) + " ORDER BY effective_priority DESC,q.created_at ASC LIMIT ?",
                (_now(), max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._request(row) for row in rows]
''',
    '''    def get_request(self, request_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(
                """SELECT q.*,f.path,f.size,f.tags_json,f.origin_tags_json,f.file_priority,f.available,
                          COALESCE(p.server_priority,0) AS server_priority,
                          (COALESCE(p.server_priority,0)+COALESCE(f.file_priority,0)+q.transfer_priority) AS effective_priority
                   FROM federation_download_request q
                   JOIN federation_remote_file f ON f.peer_id=q.peer_id AND f.remote_document_id=q.remote_document_id
                   LEFT JOIN federation_catalog_peer p ON p.peer_id=q.peer_id
                   WHERE q.request_id=?""",
                (request_id,),
            ).fetchone()
        return self._request(row) if row else None

    def queue(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                """SELECT q.*,f.path,f.size,f.tags_json,f.origin_tags_json,f.file_priority,f.available,
                          COALESCE(p.server_priority,0) AS server_priority,
                          (COALESCE(p.server_priority,0)+COALESCE(f.file_priority,0)+q.transfer_priority) AS effective_priority
                   FROM federation_download_request q
                   JOIN federation_remote_file f ON f.peer_id=q.peer_id AND f.remote_document_id=q.remote_document_id
                   LEFT JOIN federation_catalog_peer p ON p.peer_id=q.peer_id
                   ORDER BY CASE q.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'retry' THEN 2 WHEN 'waiting_peer' THEN 3 ELSE 4 END,
                            effective_priority DESC,q.created_at ASC LIMIT ?""",
                (max(1, min(int(limit), 5000)),),
            ).fetchall()
        return [self._request(row) for row in rows]

    def next_requests(self, limit: int = 1) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                """SELECT q.*,f.path,f.size,f.tags_json,f.origin_tags_json,f.file_priority,f.available,
                          COALESCE(p.server_priority,0) AS server_priority,
                          (COALESCE(p.server_priority,0)+COALESCE(f.file_priority,0)+q.transfer_priority) AS effective_priority
                   FROM federation_download_request q
                   JOIN federation_remote_file f ON f.peer_id=q.peer_id AND f.remote_document_id=q.remote_document_id
                   LEFT JOIN federation_catalog_peer p ON p.peer_id=q.peer_id
                   WHERE q.status IN ('queued','retry','waiting_peer') AND q.next_attempt_at<=?
                   ORDER BY effective_priority DESC,q.created_at ASC LIMIT ?""",
                (_now(), max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._request(row) for row in rows]
'''
)
replace_once(
    "app/federation_catalog.py",
    '''    @staticmethod
    def _queue_sql(where: str) -> str:
        return (
            "SELECT q.*,f.path,f.size,f.tags_json,f.origin_tags_json,f.file_priority,f.available,"
            "COALESCE(p.server_priority,0) AS server_priority,"
            "(COALESCE(p.server_priority,0)+COALESCE(f.file_priority,0)+q.transfer_priority) AS effective_priority "
            "FROM federation_download_request q "
            "JOIN federation_remote_file f ON f.peer_id=q.peer_id AND f.remote_document_id=q.remote_document_id "
            "LEFT JOIN federation_catalog_peer p ON p.peer_id=q.peer_id" + where
        )

''',
    ""
)

# Optional invoice/vault filters: use explicit static query variants.
replace_once(
    "app/license_metering.py",
    '''    def invoices(self, *, open_only: bool = False) -> list[dict[str, Any]]:
        with self._db() as db:
            sql = "SELECT * FROM license_invoice" + (" WHERE status='open'" if open_only else "") + " ORDER BY created_at DESC"
            rows = db.execute(sql).fetchall()
        return [dict(row) for row in rows]
''',
    '''    def invoices(self, *, open_only: bool = False) -> list[dict[str, Any]]:
        with self._db() as db:
            if open_only:
                rows = db.execute(
                    "SELECT * FROM license_invoice WHERE status='open' ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM license_invoice ORDER BY created_at DESC"
                ).fetchall()
        return [dict(row) for row in rows]
'''
)
replace_once(
    "app/password_vault.py",
    '''        sql = "SELECT * FROM vault_entry WHERE user_id=?" + ("" if include_deleted else " AND deleted_at IS NULL") + " ORDER BY updated_at DESC"
        with self._db() as db:
            rows = db.execute(sql, (str(user_id),)).fetchall()
''',
    '''        with self._db() as db:
            if include_deleted:
                rows = db.execute(
                    "SELECT * FROM vault_entry WHERE user_id=? ORDER BY updated_at DESC",
                    (str(user_id),),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM vault_entry WHERE user_id=? AND deleted_at IS NULL ORDER BY updated_at DESC",
                    (str(user_id),),
                ).fetchall()
'''
)

print("patched final B608 sites")
