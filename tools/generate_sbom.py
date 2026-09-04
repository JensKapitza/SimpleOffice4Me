#!/usr/bin/env python3
"""Create a small CycloneDX 1.5 SBOM from the installed Python environment."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simpleoffice_version import build_info  # noqa: E402


def build_sbom() -> dict:
    components = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].casefold()):
        name = distribution.metadata["Name"]
        if not name:
            continue
        components.append({"type": "library", "name": name, "version": distribution.version, "purl": f"pkg:pypi/{name}@{distribution.version}"})
    inventory = "\n".join(f"{item['name']}=={item['version']}" for item in components)
    serial = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/JensKapitza/SimpleOffice4Me\n" + inventory)
    release_version = str(build_info(PROJECT_ROOT).get("release_version") or "1.0.0")
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{serial}", "version": 1,
            "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(), "component": {"type": "application", "name": "SimpleOffice4Me", "version": release_version}}, "components": components}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", default="artifacts/sbom.cdx.json"); args = parser.parse_args()
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(build_sbom(), indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__": main()
