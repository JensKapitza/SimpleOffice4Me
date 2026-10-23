#!/usr/bin/env python3
"""Fail unless a container SBOM covers both OS and Python inventories."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate_combined_sbom(python_sbom: dict, image_sbom: dict) -> None:
    """Require the image inventory to contain the verified app inventory and OS packages."""
    for document in (python_sbom, image_sbom):
        if document.get("bomFormat") != "CycloneDX" or document.get("specVersion") != "1.6":
            raise ValueError("container inventories must be CycloneDX 1.6 documents")
        if not isinstance(document.get("components"), list):
            raise ValueError("container inventory has no component list")

    python_purls = _component_purls(python_sbom)
    image_purls = _component_purls(image_sbom)
    missing = python_purls - image_purls
    if missing:
        raise ValueError("combined image SBOM omits Python components: " + ", ".join(sorted(missing)))
    if not any(purl.startswith("pkg:deb/") for purl in image_purls):
        raise ValueError("combined image SBOM contains no Debian OS packages")
    if not any(purl.startswith("pkg:pypi/") for purl in image_purls):
        raise ValueError("combined image SBOM contains no Python packages")


def _component_purls(document: dict) -> set[str]:
    purls = set()
    for component in document["components"]:
        if not isinstance(component, dict) or not isinstance(component.get("purl"), str):
            raise ValueError("container inventory contains a component without a package URL")
        purls.add(component["purl"])
    return purls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-sbom", required=True, type=Path)
    parser.add_argument("--image-sbom", required=True, type=Path)
    args = parser.parse_args()
    try:
        validate_combined_sbom(
            json.loads(args.python_sbom.read_text(encoding="utf-8")),
            json.loads(args.image_sbom.read_text(encoding="utf-8")),
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
