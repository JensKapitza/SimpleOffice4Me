from pathlib import Path

path = Path("app/osm_address.py")
source = path.read_text(encoding="utf-8")
old = '''        def select(db: sqlite3.Connection, selected: list[str]) -> list[sqlite3.Row]:
            where = " AND ".join("normalized LIKE ?" for _ in selected)
            params: list[Any] = [f"%{token}%" for token in selected]
            if country:
                where += " AND country = ?"
                params.append(country)
            params.append(maximum)
            return db.execute(
                f"SELECT * FROM address WHERE {where} ORDER BY CASE WHEN postal <> '' THEN 0 ELSE 1 END, city COLLATE NOCASE, street COLLATE NOCASE, house_number LIMIT ?",
                params,
            ).fetchall()
'''
new = '''        def select(db: sqlite3.Connection, selected: list[str]) -> list[sqlite3.Row]:
            token_patterns = [f"%{token}%" for token in selected[:8]]
            token_patterns.extend([""] * (8 - len(token_patterns)))
            params: list[Any] = []
            for pattern in token_patterns:
                params.extend((pattern, pattern))
            params.extend((country, country, maximum))
            return db.execute(
                """SELECT * FROM address
                   WHERE (? = '' OR normalized LIKE ?)
                     AND (? = '' OR normalized LIKE ?)
                     AND (? = '' OR normalized LIKE ?)
                     AND (? = '' OR normalized LIKE ?)
                     AND (? = '' OR normalized LIKE ?)
                     AND (? = '' OR normalized LIKE ?)
                     AND (? = '' OR normalized LIKE ?)
                     AND (? = '' OR normalized LIKE ?)
                     AND (? = '' OR country = ?)
                   ORDER BY CASE WHEN postal <> '' THEN 0 ELSE 1 END,
                            city COLLATE NOCASE,
                            street COLLATE NOCASE,
                            house_number
                   LIMIT ?""",
                params,
            ).fetchall()
'''
count = source.count(old)
if count != 1:
    raise SystemExit(f"expected one OSM search SQL block, found {count}")
path.write_text(source.replace(old, new, 1), encoding="utf-8")
