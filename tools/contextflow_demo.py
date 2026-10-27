"""Smallest reproducible ContextFlow demo starter (Batch 3).

Creates an isolated synthetic root (never production data), seeds the P0
story, creates a local demo user in a separate demo auth database, and
serves the existing Flask app on 127.0.0.1 only. No workers, no cloud, no
external messages. Run from the repository root with the project venv:

    .venv/Scripts/python.exe tools/contextflow_demo.py [--port 5088] [--root DIR] [--fresh]
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

MARKER = ".contextflow-demo.json"
DEFAULT_DIR = REPO_ROOT / ".runtime-tools" / "contextflow-demo"


def _fail(message: str) -> "NoReturn":
    raise SystemExit(f"contextflow_demo: {message}")


def prepare_root(path: Path, fresh: bool) -> Path:
    """Return an isolated synthetic root; refuse anything that looks real."""
    if path.exists() and any(path.iterdir()):
        marker = path / MARKER
        if not marker.exists():
            _fail(
                f"refusing non-empty root without demo marker: {path} "
                "(pass an empty dir or a previous demo root)"
            )
        if fresh:
            for child in path.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    path.mkdir(parents=True, exist_ok=True)
    (path / MARKER).write_text(
        json.dumps({"synthetic": True, "tool": "contextflow_demo"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local ContextFlow demo.")
    parser.add_argument("--port", type=int, default=5088)
    parser.add_argument("--root", default=str(DEFAULT_DIR))
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    root = prepare_root(Path(args.root).expanduser(), args.fresh)

    from app import app as flask_app
    from app.contextflow.demo import seed_demo
    from app.db import ensure_auth_database, get_db
    from app.password_security import hash_password

    flask_app.config.update(
        DATABASE=str(root / "demo-users.sqlite"),
        DOCUMENT_ROOT=str(root),
        CONTEXTFLOW_ENABLED=True,
        TESTING=False,
    )
    with flask_app.app_context():
        ensure_auth_database()
        db = get_db()
        password = secrets.token_urlsafe(12)
        db.execute("DELETE FROM user WHERE username = ?", ("demo",))
        db.execute(
            "INSERT INTO user(username,password,display_name,is_admin,is_disabled,auth_version,"
            "created_at,updated_at) VALUES (?,?,?,1,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            ("demo", hash_password(password), "Demo (synthetisch)"),
        )
        db.commit()
        summary = seed_demo(root, actor="demo")

    print("ContextFlow Demo bereit (alle Daten synthetisch).")
    print(f"  URL:      http://127.0.0.1:{args.port}/contextflow/")
    print("  Benutzer: demo")
    print(f"  Passwort: {password}  (nur Demo-Zugang, nur lokal)")
    print(f"  Root:     {root}")
    print(f"  Fall:     {summary['link_id']} (abgeschlossen {summary['completed_on']})")
    print("Kein Cloud-Versand, keine Worker, keine externen Nachrichten.")
    flask_app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
