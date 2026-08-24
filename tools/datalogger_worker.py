"""Independent low-priority periodic data collector."""

import argparse
import json
import os
import signal
import time
from pathlib import Path

from app.datalogger_collectors import CollectionError, collect
from app.datalogger_store import DataLoggerStore

running = True


def stop(*_args):
    global running; running = False


def run(root, once=False):
    if hasattr(os, "nice"):
        try: os.nice(10)
        except OSError: pass
    store = DataLoggerStore(root)
    while running:
        for source in store.due_sources():
            try:
                value = collect(source["kind"], json.loads(source["config"]), root)
                store.add_sample(source["channel_id"], value, "system:datalogger", source_id=source["source_id"])
                store.finish_source(source["source_id"], "ok")
            except CollectionError as exc:
                store.finish_source(source["source_id"], "error", exc.code)
            except Exception:
                store.finish_source(source["source_id"], "error", "internal_error")
        if once: break
        time.sleep(max(1, min(int(os.environ.get("SIMPLEOFFICE_DATALOGGER_TICK_SECONDS", "5")), 60)))


def acquire_lock(root):
    """Keep multiple launchers from polling every source more than once."""
    path = Path(root).resolve() / ".simpleoffice-meta" / "datalogger-worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    if os.name == "nt":
        import msvcrt
        try: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError: handle.close(); return None
    else:
        import fcntl
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: handle.close(); return None
    return handle


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--root", required=True); parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    lock = acquire_lock(args.root)
    if lock is None:
        return
    try: run(args.root, args.once)
    finally: lock.close()


if __name__ == "__main__": main()
