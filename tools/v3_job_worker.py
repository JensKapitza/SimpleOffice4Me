"""Worker CLI for the optional SimpleOffice 3.0 job queue."""
from __future__ import annotations
import argparse
import os
import socket
import time

from app.v3_capabilities import enabled
from app.v3_jobs import JobStore, JobWorker, default_handlers


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True)
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--poll-seconds",type=float,default=2.0)
    args=parser.parse_args()
    if not enabled("v3.jobs"):
        print("v3.jobs is disabled; worker exits without changing jobs")
        return 0
    worker_id=f"{socket.gethostname()}:{os.getpid()}"
    worker=JobWorker(JobStore(args.root),default_handlers(args.root),worker_id)
    if args.once:
        worker.run_once()
        return 0
    delay=max(0.2,min(30.0,args.poll_seconds))
    try:
        while True:
            if worker.run_once() is None:
                time.sleep(delay)
    except KeyboardInterrupt:
        return 0


if __name__=="__main__":
    raise SystemExit(main())
