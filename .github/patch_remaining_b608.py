from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one match, found {count}')
    p.write_text(text.replace(old, new, 1), encoding='utf-8')


# OSM batch lookup: replace variable IN-list with a temporary key table.
replace_once(
    'app/osm_address.py',
    '''        for offset in range(0, len(keys), 300):
            chunk = keys[offset:offset + 300]
            placeholders = ",".join("(?,?)" for _ in chunk)
            params = [value for key in chunk for value in key]
            for current in db.execute(
                f"SELECT street,house_number,postal,city,country,state,lat,lon,osm_type,osm_id,normalized FROM address WHERE (osm_type,osm_id) IN ({placeholders})",
                params,
            ):
                value = tuple(str(item) for item in current)
                existing[(value[8], value[9])] = value
''',
    '''        db.execute(
            """CREATE TEMP TABLE IF NOT EXISTS requested_address_key (
                   osm_type TEXT NOT NULL,
                   osm_id TEXT NOT NULL,
                   PRIMARY KEY(osm_type, osm_id)
               )"""
        )
        for offset in range(0, len(keys), 300):
            chunk = keys[offset:offset + 300]
            db.execute("DELETE FROM requested_address_key")
            db.executemany(
                "INSERT OR IGNORE INTO requested_address_key(osm_type,osm_id) VALUES(?,?)",
                chunk,
            )
            for current in db.execute(
                """SELECT a.street,a.house_number,a.postal,a.city,a.country,a.state,
                          a.lat,a.lon,a.osm_type,a.osm_id,a.normalized
                   FROM address a
                   JOIN requested_address_key r
                     ON r.osm_type=a.osm_type AND r.osm_id=a.osm_id"""
            ):
                value = tuple(str(item) for item in current)
                existing[(value[8], value[9])] = value
'''
)

# Admin audit queries: the filter is fixed, so keep the complete statements static.
replace_once(
    'app/admin.py',
    '''    events = get_db().execute(
        "SELECT * FROM security_event" + AUDIT_WHERE + " ORDER BY occurred_at DESC LIMIT ? OFFSET ?",
        (*parameters, limit + 1, (event_page - 1) * limit),
    ).fetchall()
''',
    '''    events = get_db().execute(
        """SELECT * FROM security_event
           WHERE (?='' OR actor_name LIKE ? OR action LIKE ? OR target_type LIKE ? OR target_id LIKE ? OR detail LIKE ?)
             AND (?='' OR actor_name LIKE ?)
             AND (?='' OR action LIKE ?)
             AND (?='' OR target_type = ?)
             AND (?='' OR target_id LIKE ?)
             AND (?='' OR outcome = ?)
             AND (?='' OR detail LIKE ?)
             AND (?='' OR detail LIKE ?)
             AND (?='' OR occurred_at >= ?)
             AND (?='' OR occurred_at < datetime(?, '+1 day'))
           ORDER BY occurred_at DESC LIMIT ? OFFSET ?""",
        (*parameters, limit + 1, (event_page - 1) * limit),
    ).fetchall()
'''
)
replace_once(
    'app/admin.py',
    '''    rows = get_db().execute(
        "SELECT * FROM security_event" + AUDIT_WHERE + " ORDER BY occurred_at DESC LIMIT 20000",
        parameters,
    ).fetchall()
''',
    '''    rows = get_db().execute(
        """SELECT * FROM security_event
           WHERE (?='' OR actor_name LIKE ? OR action LIKE ? OR target_type LIKE ? OR target_id LIKE ? OR detail LIKE ?)
             AND (?='' OR actor_name LIKE ?)
             AND (?='' OR action LIKE ?)
             AND (?='' OR target_type = ?)
             AND (?='' OR target_id LIKE ?)
             AND (?='' OR outcome = ?)
             AND (?='' OR detail LIKE ?)
             AND (?='' OR detail LIKE ?)
             AND (?='' OR occurred_at >= ?)
             AND (?='' OR occurred_at < datetime(?, '+1 day'))
           ORDER BY occurred_at DESC LIMIT 20000""",
        parameters,
    ).fetchall()
'''
)

# Federation mail: optional filters and hash columns become fixed statements.
replace_once(
    'app/federation_mail.py',
    '''        params: list[Any] = [_owner(actor), account_id]
        condition = ""
        if message_row_id is not None:
            condition = " AND message_row_id=?"
            params.append(int(message_row_id))
        params.append(limit)
        with self._db() as db:
            rows = db.execute(
                f"SELECT * FROM mail_federation_source WHERE owner_key=? AND account_id=?{condition} ORDER BY availability='index_only',confidence DESC,last_seen_at DESC LIMIT ?",
                params,
            ).fetchall()
''',
    '''        row_filter = int(message_row_id) if message_row_id is not None else None
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM mail_federation_source
                   WHERE owner_key=? AND account_id=?
                     AND (? IS NULL OR message_row_id=?)
                   ORDER BY availability='index_only',confidence DESC,last_seen_at DESC
                   LIMIT ?""",
                (_owner(actor), account_id, row_filter, row_filter, limit),
            ).fetchall()
'''
)
replace_once(
    'app/federation_mail.py',
    '''                for digest, column, kind in ((raw_hash, "raw_sha512", "raw"), (content_hash, "content_sha512", "content")):
                    if not digest:
                        continue
                    rows = db.execute(
                        f"SELECT * FROM mail_message_index WHERE owner_key=? AND account_id=? AND {column}=? ORDER BY present DESC,last_seen_at DESC LIMIT ?",
                        (owner_key, account_id, digest, MAX_MATCHES_PER_QUERY),
                    ).fetchall()
                    for dbrow in rows:
                        row = dict(dbrow)
                        found.setdefault(int(row["id"]), {"row": row, "actor": actor, "kind": kind, "distance": 0, "confidence": 100})
''',
    '''                for digest, kind in ((raw_hash, "raw"), (content_hash, "content")):
                    if not digest:
                        continue
                    if kind == "raw":
                        rows = db.execute(
                            """SELECT * FROM mail_message_index
                               WHERE owner_key=? AND account_id=? AND raw_sha512=?
                               ORDER BY present DESC,last_seen_at DESC LIMIT ?""",
                            (owner_key, account_id, digest, MAX_MATCHES_PER_QUERY),
                        ).fetchall()
                    else:
                        rows = db.execute(
                            """SELECT * FROM mail_message_index
                               WHERE owner_key=? AND account_id=? AND content_sha512=?
                               ORDER BY present DESC,last_seen_at DESC LIMIT ?""",
                            (owner_key, account_id, digest, MAX_MATCHES_PER_QUERY),
                        ).fetchall()
                    for dbrow in rows:
                        row = dict(dbrow)
                        found.setdefault(int(row["id"]), {"row": row, "actor": actor, "kind": kind, "distance": 0, "confidence": 100})
'''
)

# Mail index: temporary ID table replaces IN-list construction; optional presence is parameterized.
replace_once(
    'app/mail_index.py',
    '''        now = utc_now()
        placeholders = ",".join("?" for _ in ids)
        with self._db() as db:
            cursor = db.execute(
                f"UPDATE mail_message_index SET present=0, presence_status='target_not_found', missing_reason=?, missing_at=CASE WHEN missing_at='' THEN ? ELSE missing_at END WHERE owner_key=? AND account_id=? AND id IN ({placeholders})",
                (reason[:500], now, owner, account_id, *ids),
            )
            return int(cursor.rowcount)
''',
    '''        now = utc_now()
        with self._db() as db:
            db.execute("CREATE TEMP TABLE IF NOT EXISTS requested_mail_row(id INTEGER PRIMARY KEY)")
            db.execute("DELETE FROM requested_mail_row")
            db.executemany("INSERT OR IGNORE INTO requested_mail_row(id) VALUES(?)", ((row_id,) for row_id in ids))
            cursor = db.execute(
                """UPDATE mail_message_index
                   SET present=0, presence_status='target_not_found', missing_reason=?,
                       missing_at=CASE WHEN missing_at='' THEN ? ELSE missing_at END
                   WHERE owner_key=? AND account_id=?
                     AND id IN (SELECT id FROM requested_mail_row)""",
                (reason[:500], now, owner, account_id),
            )
            return int(cursor.rowcount)
'''
)
replace_once(
    'app/mail_index.py',
    '''        placeholders = ",".join("?" for _ in ids)
        condition = " AND present=1" if present_only else ""
        with self._db() as db:
            rows = db.execute(
                f"SELECT * FROM mail_message_index WHERE owner_key=? AND account_id=? AND id IN ({placeholders}){condition}",
                (owner, account_id, *ids),
            ).fetchall()
            return [dict(row) for row in rows]
''',
    '''        with self._db() as db:
            db.execute("CREATE TEMP TABLE IF NOT EXISTS requested_mail_row(id INTEGER PRIMARY KEY)")
            db.execute("DELETE FROM requested_mail_row")
            db.executemany("INSERT OR IGNORE INTO requested_mail_row(id) VALUES(?)", ((row_id,) for row_id in ids))
            rows = db.execute(
                """SELECT m.* FROM mail_message_index m
                   JOIN requested_mail_row r ON r.id=m.id
                   WHERE m.owner_key=? AND m.account_id=?
                     AND (?=0 OR m.present=1)""",
                (owner, account_id, int(present_only)),
            ).fetchall()
            return [dict(row) for row in rows]
'''
)
replace_once(
    'app/mail_index.py',
    '''        with self._db() as db:
            where_missing = "" if include_missing else " AND m.present=1"
            terms = [term for term in _WORD_RE.findall(unicodedata.normalize("NFKC", query).casefold()) if len(term) >= 2][:12]
''',
    '''        with self._db() as db:
            include_missing_flag = int(include_missing)
            terms = [term for term in _WORD_RE.findall(unicodedata.normalize("NFKC", query).casefold()) if len(term) >= 2][:12]
'''
)
replace_once(
    'app/mail_index.py',
    '''                    rows = db.execute(
                        f"""SELECT m.* FROM mail_search_fts f
                            JOIN mail_message_index m ON m.id=CAST(f.message_row_id AS INTEGER)
                            WHERE mail_search_fts MATCH ? AND m.owner_key=? AND m.account_id=?{where_missing}
                            ORDER BY m.present DESC, m.last_seen_at DESC LIMIT ?""",
                        (expression, owner, account_id, limit),
                    ).fetchall()
''',
    '''                    rows = db.execute(
                        """SELECT m.* FROM mail_search_fts f
                           JOIN mail_message_index m ON m.id=CAST(f.message_row_id AS INTEGER)
                           WHERE mail_search_fts MATCH ? AND m.owner_key=? AND m.account_id=?
                             AND (?=1 OR m.present=1)
                           ORDER BY m.present DESC, m.last_seen_at DESC LIMIT ?""",
                        (expression, owner, account_id, include_missing_flag, limit),
                    ).fetchall()
'''
)
replace_once(
    'app/mail_index.py',
    '''                rows = db.execute(
                    f"""SELECT m.* FROM mail_message_index m
                        WHERE m.owner_key=? AND m.account_id=?{where_missing}
                          AND lower(m.search_text) LIKE ?
                        ORDER BY m.present DESC, m.last_seen_at DESC LIMIT ?""",
                    (owner, account_id, needle, limit),
                ).fetchall()
''',
    '''                rows = db.execute(
                    """SELECT m.* FROM mail_message_index m
                       WHERE m.owner_key=? AND m.account_id=?
                         AND (?=1 OR m.present=1)
                         AND lower(m.search_text) LIKE ?
                       ORDER BY m.present DESC, m.last_seen_at DESC LIMIT ?""",
                    (owner, account_id, include_missing_flag, needle, limit),
                ).fetchall()
'''
)
replace_once(
    'app/mail_index.py',
    '''                rows = db.execute(
                    f"SELECT m.* FROM mail_message_index m WHERE m.owner_key=? AND m.account_id=?{where_missing} ORDER BY m.present DESC, m.last_seen_at DESC LIMIT ?",
                    (owner, account_id, limit),
                ).fetchall()
''',
    '''                rows = db.execute(
                    """SELECT m.* FROM mail_message_index m
                       WHERE m.owner_key=? AND m.account_id=?
                         AND (?=1 OR m.present=1)
                       ORDER BY m.present DESC, m.last_seen_at DESC LIMIT ?""",
                    (owner, account_id, include_missing_flag, limit),
                ).fetchall()
'''
)

# Document inbox filter is a fixed condition; keep it directly in static statements.
replace_once(
    'app/document_store.py',
    '''        where = "state = 'new' AND has_notes = 0 AND has_relationships = 0"
        with self._db() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM document_listing WHERE {where}").fetchone()[0])
            rows = db.execute(
                f"""SELECT document_id FROM document_listing WHERE {where}
                    ORDER BY last_seen_at DESC, path LIMIT ? OFFSET ?""",
                (page_size, (page - 1) * page_size),
            ).fetchall()
''',
    '''        with self._db() as db:
            total = int(db.execute(
                """SELECT COUNT(*) FROM document_listing
                   WHERE state='new' AND has_notes=0 AND has_relationships=0"""
            ).fetchone()[0])
            rows = db.execute(
                """SELECT document_id FROM document_listing
                   WHERE state='new' AND has_notes=0 AND has_relationships=0
                   ORDER BY last_seen_at DESC, path LIMIT ? OFFSET ?""",
                (page_size, (page - 1) * page_size),
            ).fetchall()
'''
)

# Advanced document query fallback is generated only by the internal parser.
# Add an explicit token allow-list before using the generated fragment, then
# assemble the statement without interpolating raw input.
insert_after = '''_WAL_CONFIGURED_INDEXES: set[Path] = set()\n'''
helper = '''\n_SEARCH_SQL_WORDS = {\n    "path", "state", "tags", "notes", "attributes", "content",\n    "LIKE", "ESCAPE", "AND", "OR", "NOT", "CASE", "WHEN", "THEN",\n    "ELSE", "END",\n}\n\ndef _validated_search_where(fragment: str) -> str:\n    words = set(re.findall(r"[A-Za-z_]+", fragment))\n    if not words.issubset(_SEARCH_SQL_WORDS):\n        raise ValueError("compiled document search contains an unsupported SQL token")\n    stripped = re.sub(r"[A-Za-z_]+", "", fragment)\n    if re.search(r"[^\\s()?'=0-9\\\\]", stripped):\n        raise ValueError("compiled document search contains unsupported SQL syntax")\n    return fragment\n\n'''
p = Path('app/document_store.py')
text = p.read_text(encoding='utf-8')
if text.count(insert_after) != 1:
    raise SystemExit('document_store helper insertion point mismatch')
text = text.replace(insert_after, insert_after + helper, 1)
p.write_text(text, encoding='utf-8')
replace_once(
    'app/document_store.py',
    '''                rows = db.execute(
                    f"SELECT document_id, path, state FROM document_search WHERE {compiled.where} LIMIT ? OFFSET ?",
                    (*compiled.parameters, limit, offset),
                ).fetchall()
''',
    '''                where_fragment = _validated_search_where(compiled.where)
                statement = "".join((
                    "SELECT document_id, path, state FROM document_search WHERE ",
                    where_fragment,
                    " LIMIT ? OFFSET ?",
                ))
                rows = db.execute(
                    statement,
                    (*compiled.parameters, limit, offset),
                ).fetchall()
'''
)

print('patched remaining B608 sites')
