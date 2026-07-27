#!/usr/bin/env python3
"""Create a small CycloneDX 1.5 SBOM from the installed Python environment."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from datetime import datetime, timezone
from pathlib import Path


def build_sbom() -> dict:
    components = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].casefold()):
        name = distribution.metadata["Name"]
        if not name:
            continue
        components.append({"type": "library", "name": name, "version": distribution.version, "purl": f"pkg:pypi/{name}@{distribution.version}"})
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": "urn:uuid:00000000-0000-4000-8000-000000000001", "version": 1,
            "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(), "component": {"type": "application", "name": "SimpleOffice4Me", "version": "0.1.0"}}, "components": components}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", default="artifacts/sbom.cdx.json"); args = parser.parse_args()
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(build_sbom(), indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__": main()
