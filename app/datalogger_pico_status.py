"""Private read model of the worker's recorded Pico gateway observations."""
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit
from uuid import uuid5

from .datalogger_collectors import read_sensor_allowed_hosts
from .datalogger_store import pico_example_namespace

_GATEWAY = re.compile(r"http://(?:[0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}/status\Z")
_PATH = re.compile(r"nodes\.(room_[ab])\.(value|available)\Z")


def _observation(row, now, worker_tick, allowed):
    when = row["last_run_at"]
    if not row["enabled"]:
        return "paused"
    if not allowed:
        return "blocked"
    if not when:
        return "pending"
    observed = datetime.fromisoformat(when)
    stale_after = timedelta(seconds=2 * max(row["interval_seconds"], worker_tick))
    if observed.tzinfo is None or now - observed > stale_after:
        return "stale"
    if row["last_status"] == "error":
        return {
            "http_failed": "gateway_error", "dns_failed": "gateway_error",
            "host_denied": "blocked", "json_path_missing": "missing",
        }.get(row["last_error_code"], "invalid")
    return "ok" if row["last_status"] == "ok" and row["measured_at"] is not None else "pending"


def pico_status(store, actor, root, *, now=None):
    now = now or datetime.now(timezone.utc)
    allowed = set(read_sensor_allowed_hosts(root))
    worker_tick = store.poll_interval_seconds()
    gateways = {}
    for row in store.pico_source_observations(actor):
        try:
            config = json.loads(row["config"])
            url, path = config.get("url"), config.get("json_path")
            match = _PATH.fullmatch(path) if isinstance(path, str) else None
            if not match or not isinstance(url, str) or not _GATEWAY.fullmatch(url):
                continue
            node, metric = match.groups()
            identity = pico_example_namespace(actor, url)
            channel = uuid5(identity, f"{node}:{metric}")
            if row["channel_id"] != str(channel) or row["source_id"] != str(uuid5(channel, "source")):
                continue
            parsed = urlsplit(url)
            state = _observation(row, now, worker_tick, parsed.hostname in allowed)
            if state == "ok" and metric == "available":
                state = {1: "online", 0: "offline"}.get(row["value"], "invalid")
            gateway = gateways.setdefault(parsed.netloc, {})
            gateway.setdefault(node, {})[metric] = {
                "state": state, "channel": row["channel_id"], "unit": row["unit"],
                "value": row["value"], "sample_time": row["measured_at"], "poll_time": row["last_run_at"],
            }
        except (AttributeError, TypeError, ValueError):
            continue
    return gateways
