#!/usr/bin/env python3
"""Benchmark the quick-search workspace API against large synthetic datasets.

Example::

    python -m tools.benchmark_quicksearch --scale 500 --runs 7

Builds an isolated temporary document root with real stores (documents,
objects, projects, contacts, events, tasks, chats, bookmarks) and measures
collect_quick_matches() latency through a real Flask request context.
Output is JSON so runs can be compared or archived easily.
"""
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path


def _build_dataset(root: Path, scale: int, actor: str) -> dict[str, int]:
    from app.browser_bookmarks import _ensure_schema
    from app import db as database
    from app.calendar_store import CalendarStore
    from app.chat_store import ChatStore
    from app.contact_store import ContactStore
    from app.document_store import DocumentStore
    from app.object_store import ObjectStore
    from app.project_store import ProjectStore
    from app.todo_store import TodoStore
    from flask import g

    counts: dict[str, int] = {}
    store = DocumentStore(root)
    store.initialize()
    for index in range(scale):
        path = root / f"benchmark-{index:06d}.txt"
        path.write_text(f"quicksearch benchmark document {index} quicksearchtoken", encoding="utf-8")
        store._scan_file(path, force_hash=True)
    counts["documents"] = scale
    objects = ObjectStore(root)
    for index in range(scale // 5):
        objects.create({"name": f"quicksearch object {index}", "type": "Gerät"}, actor)
    counts["objects"] = scale // 5
    projects = ProjectStore(root)
    for index in range(scale // 10):
        projects.create_project({"title": f"quicksearch project {index}"}, actor)
    counts["projects"] = scale // 10
    contacts = ContactStore(root)
    for index in range(scale // 5):
        contacts.upsert({"display_name": f"Quicksearch Contact {index}", "company": "Benchmark"}, actor)
    counts["contacts"] = scale // 5
    calendar = CalendarStore(root)
    for index in range(scale // 5):
        calendar.add(
            f"quicksearch event {index}", "benchmark", "2026-12-01T10:00:00",
            "2026-12-01T11:00:00", "", actor,
        )
    counts["events"] = scale // 5
    todos = TodoStore(root)
    for index in range(scale // 5):
        todos.add(f"quicksearch task {index}", actor, {})
    counts["tasks"] = scale // 5
    chat = ChatStore(root)
    rooms = 0
    for index in range(min(20, max(1, scale // 25))):
        room = chat.create_room(f"quicksearch room {index}", actor, [])
        for msg in range(min(300, scale // 5)):
            chat.add_message(room["room_id"], actor, f"quicksearch message {msg}")
        rooms += 1
    counts["rooms"] = rooms
    _ensure_schema()
    db = database.get_db()
    user_id = db.execute("SELECT id FROM user WHERE username=?", (actor,)).fetchone()["id"]
    for index in range(scale // 5):
        db.execute(
            "INSERT INTO browser_bookmark(id,user_id,title,url,folder,tags) VALUES(?,?,?,?,?,?)",
            (f"benchmark-{index}", user_id, f"quicksearch link {index}",
             f"https://example.org/quicksearch-{index}", "", ""),
        )
    db.commit()
    counts["links"] = scale // 5
    g.user = {"username": actor, "id": user_id, "is_admin": False, "is_disabled": False}
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=int, default=500, help="synthetic documents; other kinds scale down")
    parser.add_argument("--runs", type=int, default=7, help="measured repetitions after one warmup")
    parser.add_argument("--query", default="quicksearch", help="search term")
    args = parser.parse_args()

    from flask import g

    from app import app
    from app import db as database
    from app.documents_routes_quicksearch import collect_quick_matches

    temp = tempfile.TemporaryDirectory(prefix="quicksearch-benchmark-")
    base = Path(temp.name)
    app.config.update(
        TESTING=True, DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
    )
    with app.app_context():
        database.ensure_auth_database()
        db = database.get_db()
        db.execute(
            """INSERT INTO user(username,password,display_name,is_admin,is_disabled,auth_version,created_at,updated_at)
               VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            ("benchmark", "x", "Benchmark", 0, 0, 1),
        )
        db.commit()
    with app.test_request_context("/documents/quick-search/api"):
        counts = _build_dataset(base / "documents", args.scale, "benchmark")
        collect_quick_matches(args.query)
        samples: list[float] = []
        match_total = 0
        for _ in range(args.runs):
            started = time.perf_counter()
            matches = collect_quick_matches(args.query)
            samples.append((time.perf_counter() - started) * 1000.0)
            match_total = len(matches)
    result = {
        "scale": args.scale,
        "runs": args.runs,
        "counts": counts,
        "matches": match_total,
        "median_ms": round(statistics.median(samples), 1),
        "max_ms": round(max(samples), 1),
    }
    print(json.dumps(result, indent=1, sort_keys=True))
    temp.cleanup()


if __name__ == "__main__":
    main()
