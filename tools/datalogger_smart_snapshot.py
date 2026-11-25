"""Convert an administrator-provided smartctl JSON snapshot into safe metrics."""

import json
import sys

MAX_BYTES = 256 * 1024


def _path(value, *keys):
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def normalize(payload):
    """Return only numeric metrics; never include identifiers or tool output."""
    status = _path(payload, "smartctl", "exit_status")
    # Bits 0–2 describe invocation/access/read problems. Bit 3 means the
    # device itself reports failure, which is an important measurement.
    if type(status) is not int or not 0 <= status <= 255 or status & 7:
        raise ValueError("SMART snapshot unavailable")
    result = {}
    healthy = _path(payload, "smart_status", "passed")
    if status & 8:
        result["healthy"] = 0
    elif isinstance(healthy, bool):
        result["healthy"] = int(healthy)
    temperature = _path(payload, "temperature", "current")
    if type(temperature) is int and -50 <= temperature <= 150:
        result["temperature_c"] = temperature
    hours = _path(payload, "power_on_time", "hours")
    if type(hours) is int and 0 <= hours < 2**53:
        result["power_on_hours"] = hours
    if not result:
        raise ValueError("SMART snapshot has no supported metrics")
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("SMART snapshot too large")
        result = normalize(json.loads(raw))
    except (ValueError, RecursionError):
        print("Keine verwertbaren SMART-Messwerte.", file=sys.stderr)
        return 1
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
