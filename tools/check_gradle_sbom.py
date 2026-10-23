#!/usr/bin/env python3
"""Check Android's resolved Gradle CycloneDX inventory and license evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate_gradle_sbom(document: dict) -> None:
    if document.get("bomFormat") != "CycloneDX" or document.get("specVersion") != "1.6":
        raise ValueError("Android Gradle inventory must use CycloneDX 1.6")
    components = document.get("components")
    if not isinstance(components, list):
        raise ValueError("Android Gradle inventory has no component list")
    maven_components = [
        component for component in components
        if isinstance(component, dict) and str(component.get("purl", "")).startswith("pkg:maven/")
    ]
    if not maven_components:
        raise ValueError("Android Gradle inventory contains no resolved Maven components")
    if not any(component.get("licenses") for component in maven_components):
        raise ValueError("Android Gradle inventory contains no resolved Maven license metadata")
    if not isinstance(document.get("dependencies"), list):
        raise ValueError("Android Gradle inventory has no resolved dependency graph")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sbom", type=Path)
    args = parser.parse_args()
    try:
        validate_gradle_sbom(json.loads(args.sbom.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
