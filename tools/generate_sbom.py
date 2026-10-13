#!/usr/bin/env python3
"""Create a CycloneDX 1.5 SBOM from Python packages or locked Cargo metadata."""
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
    release_version = str(build_info(PROJECT_ROOT).get("release_version") or "2.0.0")
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{serial}", "version": 1,
            "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(), "component": {"type": "application", "name": "SimpleOffice4Me", "version": release_version}}, "components": components}


def build_cargo_sbom(metadata: dict) -> dict:
    packages = metadata['packages']
    root_id = metadata['resolve']['root']
    root = next(package for package in packages if package['id'] == root_id)
    components = []
    for package in sorted(packages, key=lambda item: (item['name'], item['version'])):
        component = {'type': 'application' if package['id'] == root_id else 'library',
                     'name': package['name'], 'version': package['version'],
                     'purl': f"pkg:cargo/{package['name']}@{package['version']}"}
        if package.get('license'):
            component['licenses'] = [{'expression': package['license']}]
        components.append(component)
    inventory = "\n".join(item['purl'] for item in components)
    serial = uuid.uuid5(uuid.NAMESPACE_URL, inventory)
    return {'bomFormat': 'CycloneDX', 'specVersion': '1.5',
            'serialNumber': f'urn:uuid:{serial}', 'version': 1,
            'metadata': {'timestamp': datetime.now(timezone.utc).isoformat(),
                         'component': {'type': 'application', 'name': root['name'],
                                       'version': root['version']}},
            'components': components}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/sbom.cdx.json')
    parser.add_argument('--cargo-metadata', type=Path,
                        help='JSON from cargo metadata --locked --format-version 1')
    args = parser.parse_args()
    try:
        document = build_cargo_sbom(json.loads(args.cargo_metadata.read_text(encoding='utf-8'))) if args.cargo_metadata else build_sbom()
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        parser.error('Cargo metadata must contain a resolved root package and valid package records')
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    print(path)


if __name__ == "__main__":
    main()
