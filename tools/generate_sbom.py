#!/usr/bin/env python3
"""Create a CycloneDX 1.6 SBOM from Python packages or locked Cargo metadata."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import uuid
from xml.etree import ElementTree as ET
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
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "serialNumber": f"urn:uuid:{serial}", "version": 1,
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
    return {'bomFormat': 'CycloneDX', 'specVersion': '1.6',
            'serialNumber': f'urn:uuid:{serial}', 'version': 1,
            'metadata': {'timestamp': datetime.now(timezone.utc).isoformat(),
                         'component': {'type': 'application', 'name': root['name'],
                                       'version': root['version']}},
            'components': components}



CYCLONEDX_NAMESPACE = "http://cyclonedx.org/schema/bom/1.6"
ET.register_namespace("", CYCLONEDX_NAMESPACE)


def _xml_tag(name: str) -> str:
    return f"{{{CYCLONEDX_NAMESPACE}}}{name}"


def _xml_element(parent: ET.Element, name: str, value: str | None = None, **attrs: str) -> ET.Element:
    child = ET.SubElement(parent, _xml_tag(name), attrs)
    if value is not None:
        child.text = str(value)
    return child


def write_xml_sbom(document: dict, path: Path) -> None:
    """Serialize a generated CycloneDX inventory as schema-compatible 1.6 XML."""
    if document.get("specVersion") != "1.6":
        raise ValueError("CycloneDX XML requires specVersion 1.6")
    root = ET.Element(_xml_tag("bom"), {"serialNumber": document["serialNumber"], "version": str(document["version"])})
    metadata = _xml_element(root, "metadata")
    _xml_element(metadata, "timestamp", document["metadata"]["timestamp"])
    application = document["metadata"]["component"]
    app_node = _xml_element(metadata, "component", type=application["type"])
    _xml_element(app_node, "name", application["name"])
    if application.get("version"):
        _xml_element(app_node, "version", application["version"])
    components = _xml_element(root, "components")
    for component in document["components"]:
        node = _xml_element(components, "component", type=component["type"], **{"bom-ref": component["purl"]})
        _xml_element(node, "name", component["name"])
        _xml_element(node, "version", component["version"])
        if component.get("licenses"):
            licenses = _xml_element(node, "licenses")
            for license_item in component["licenses"]:
                license_node = _xml_element(licenses, "license")
                if license_item.get("expression"):
                    # SPDX expressions are represented directly under <licenses>.
                    licenses.remove(license_node)
                    _xml_element(licenses, "expression", license_item["expression"])
        _xml_element(node, "purl", component["purl"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/sbom.cdx.json')
    parser.add_argument('--xml-output', type=Path, default=None,
                        help='Also write CycloneDX 1.6 XML to this path')
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
    if args.xml_output is not None:
        write_xml_sbom(document, args.xml_output)
        print(args.xml_output)
    print(path)


if __name__ == "__main__":
    main()
