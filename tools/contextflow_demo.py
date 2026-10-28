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
import os
import sys
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_DIR = REPO_ROOT / ".runtime-tools" / "contextflow-demo"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
# Demo CPUs need headroom: the first cold call (model load + inference).
DEFAULT_OLLAMA_TIMEOUT = 180
# Fixed demo credential: restart-proof, no transcription errors. Localhost-only,
# synthetic demo database — never use for real data.
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-contextflow-1"


def _fail(message: str) -> "NoReturn":
    raise SystemExit(f"contextflow_demo: {message}")


def vision_config_from_env(
    env: Mapping[str, str] | None = None,
) -> dict[str, str | int]:
    """Vision backend config from optional environment variables.

    CONTEXTFLOW_OLLAMA_URL defaults to loopback :11434;
    CONTEXTFLOW_OLLAMA_MODEL is empty unless configured; a configured
    model selects real local inference with a 180s timeout default for
    slow demo-CPU cold starts (overridable via CONTEXTFLOW_OLLAMA_TIMEOUT).
    Without a model the labeled mock keeps the finding-draft demo
    live-testable. No API keys, no cloud paths.
    """
    source = os.environ if env is None else env
    url = str(source.get("CONTEXTFLOW_OLLAMA_URL", "") or "").strip()
    model = str(source.get("CONTEXTFLOW_OLLAMA_MODEL", "") or "").strip()
    raw_timeout = str(source.get("CONTEXTFLOW_OLLAMA_TIMEOUT", "") or "").strip()
    try:
        timeout = int(raw_timeout) if raw_timeout else DEFAULT_OLLAMA_TIMEOUT
    except ValueError:
        timeout = DEFAULT_OLLAMA_TIMEOUT
    if timeout <= 0:
        timeout = DEFAULT_OLLAMA_TIMEOUT
    return {
        "CONTEXTFLOW_VISION_PROVIDER": "local" if model else "mock",
        "CONTEXTFLOW_OLLAMA_URL": url or DEFAULT_OLLAMA_URL,
        "CONTEXTFLOW_OLLAMA_MODEL": model,
        "CONTEXTFLOW_OLLAMA_TIMEOUT": timeout,
    }


def prepare_root(path: Path, fresh: bool) -> Path:
    """Return an isolated synthetic root; refuse anything that looks real.

    The safety checks live in app.contextflow.demo (importable by the test
    suite); the import stays here so --help never starts the app package.
    """
    from app.contextflow.demo import (
        DEMO_MARKER,
        DemoRootError,
        is_verified_demo_root,
        reset_demo_root,
    )

    if path.exists() and any(path.iterdir()):
        if not is_verified_demo_root(path):
            _fail(
                f"refusing non-empty root without verified demo marker: {path} "
                "(pass an empty dir or a previous demo root)"
            )
        if fresh:
            try:
                reset_demo_root(path)
            except DemoRootError as exc:
                _fail(str(exc))
            path.mkdir(parents=True, exist_ok=True)
            return path
    path.mkdir(parents=True, exist_ok=True)
    (path / DEMO_MARKER).write_text(
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

    vision = vision_config_from_env()
    flask_app.config.update(
        DATABASE=str(root / "demo-users.sqlite"),
        DOCUMENT_ROOT=str(root),
        CONTEXTFLOW_ENABLED=True,
        TESTING=False,
    )
    flask_app.config.update(vision)
    with flask_app.app_context():
        ensure_auth_database()
        db = get_db()
        password = DEMO_PASSWORD
        db.execute("DELETE FROM user WHERE username = ?", (DEMO_USERNAME,))
        db.execute(
            "INSERT INTO user(username,password,display_name,is_admin,is_disabled,auth_version,"
            "created_at,updated_at) VALUES (?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (DEMO_USERNAME, hash_password(password), "Demo (synthetisch)"),
        )
        db.commit()
        summary = seed_demo(root, actor="demo")

    print("ContextFlow Demo bereit (alle Daten synthetisch).")
    print(f"  URL im Browser oeffnen:")
    print(f"    http://127.0.0.1:{args.port}/contextflow/")
    print(f"  Benutzername (genau so, nicht admin):")
    print(f"    {DEMO_USERNAME}")
    print(f"  Passwort (genau so, nur Demo-Zugang, nur lokal):")
    print(f"    {password}")
    print(f"  Root:     {root}")
    print(f"  Fall:     {summary['link_id']} (abgeschlossen {summary['completed_on']})")
    if vision["CONTEXTFLOW_VISION_PROVIDER"] == "local":
        print(f"  Vision:   lokal, Modell {vision['CONTEXTFLOW_OLLAMA_MODEL']}")
        print(f"  Ollama:   {vision['CONTEXTFLOW_OLLAMA_URL']}")
        print(f"  Timeout:  {vision['CONTEXTFLOW_OLLAMA_TIMEOUT']}s (Kaltstart)")
    else:
        print("  Vision:   Mock (Befunddemo; Inventar braucht lokales Modell)")
    print("Kein Cloud-Versand, keine Worker, keine externen Nachrichten.")
    print("Bei 'Zu viele Versuche': kurz warten oder mit --fresh neu starten.")
    flask_app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
