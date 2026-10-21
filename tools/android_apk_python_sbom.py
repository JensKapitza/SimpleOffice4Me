#!/usr/bin/env python3
"""Inventory Python wheels actually embedded in a Chaquopy Android APK.

Only dist-info METADATA found in APK-contained Chaquopy requirements archives
is trusted. Fail closed when packaging changes; never substitute build-host pip.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
from pathlib import Path
import sys
import zipfile

from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.generate_sbom import build_sbom, write_xml_sbom  # noqa: E402
from unittest.mock import patch  # noqa: E402
from types import SimpleNamespace  # noqa: E402
import json  # noqa: E402


def packaged_distributions(apk_path: Path) -> list:
    found = {}
    archives = []
    with zipfile.ZipFile(apk_path) as apk:
        for member in apk.namelist():
            if member.startswith("assets/chaquopy/") and member.endswith(".zip") and "requirements" in member:
                archives.append(member)
        if not archives:
            raise ValueError("APK has no Chaquopy requirements archives")
        for archive in archives:
            with zipfile.ZipFile(BytesIO(apk.read(archive))) as wheels:
                for path in wheels.namelist():
                    if not path.endswith(".dist-info/METADATA"):
                        continue
                    metadata = BytesParser(policy=default).parsebytes(wheels.read(path))
                    name, version = metadata.get("Name"), metadata.get("Version")
                    if not name or not version:
                        raise ValueError(f"incomplete wheel metadata in {archive}:{path}")
                    key = canonicalize_name(name)
                    requirements = metadata.get_all("Requires-Dist", [])
                    record = SimpleNamespace(metadata={"Name": name}, version=version, requires=requirements)
                    if key in found and found[key].version != version:
                        raise ValueError(f"conflicting bundled versions for {name}")
                    found[key] = record
    if not found:
        raise ValueError("APK requirements archives contain no dist-info METADATA")
    return list(found.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--xml-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        distributions = packaged_distributions(args.apk)
        with patch("tools.generate_sbom.importlib.metadata.distributions", return_value=distributions):
            document = build_sbom()
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    write_xml_sbom(document, args.xml_output)
    print(f"Inventoried {len(distributions)} bundled Python distributions")


if __name__ == "__main__":
    main()
