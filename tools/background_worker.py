from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def _write_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        return 2

    meta = root / ".simpleoffice-meta"
    meta.mkdir(parents=True, exist_ok=True)
    heartbeat_path = meta / "companion-agent-heartbeat.json"
    stop_path = meta / "companion-agent-stop.json"
    started_at = int(time.time())
    pid = os.getpid()

    try:
        while True:
            stop = _read_json(stop_path)
            if stop.get("instance_token") == args.token:
                break
            _write_json(
                heartbeat_path,
                {
                    "instance_token": args.token,
                    "pid": pid,
                    "started_at": started_at,
                    "heartbeat_at": int(time.time()),
                },
            )
            time.sleep(2)
    finally:
        heartbeat = _read_json(heartbeat_path)
        if heartbeat.get("instance_token") == args.token:
            try:
                heartbeat_path.unlink()
            except FileNotFoundError:
                pass
        stop = _read_json(stop_path)
        if stop.get("instance_token") == args.token:
            try:
                stop_path.unlink()
            except FileNotFoundError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
