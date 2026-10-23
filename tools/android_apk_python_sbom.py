#!/usr/bin/env python3
"""Inventory Python wheels actually embedded in a Chaquopy Android APK.

Only dist-info METADATA found in APK-contained Chaquopy requirements archives
is trusted. Aggregate metadata across common and ABI archives because the latter
may contain native payloads without metadata. Never substitute build-host pip.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
import json
from pathlib import Path
import sys
import zipfile
from types import SimpleNamespace

from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_chaquopy_wheels import pinned_versions  # noqa: E402
from tools.generate_sbom import build_sbom, write_xml_sbom  # noqa: E402


def packaged_distributions(apk_path: Path) -> list:
    found = {}
    archives = []
    with zipfile.ZipFile(apk_path) as apk:
        for member in apk.namelist():
            if (
                member.startswith("assets/chaquopy/")
                and Path(member).name.startswith("requirements-")
                and Path(member).suffix in {".imy", ".zip"}
            ):
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
                    if key in found and (
                        found[key].version != version or found[key].requires != requirements
                    ):
                        raise ValueError(f"conflicting bundled package metadata for {name}")
                    found[key] = record
    if not found:
        raise ValueError("APK requirements archives contain no dist-info METADATA")
    return list(found.values())


def enforce_android_pillow_policy(distributions: list) -> None:
    """Reject known legacy Pillow versions in an actual installable APK."""
    versions = {
        canonicalize_name(distribution.metadata["Name"]): distribution.version
        for distribution in distributions
    }
    if "pillow" not in versions:
        raise ValueError("APK is missing Pillow metadata; cannot verify security baseline")
    if Version(versions["pillow"]) < Version("13.0.0"):
        raise ValueError(
            f"APK bundles Pillow {versions['pillow']} below the 13.0.0 security baseline"
        )


def enforce_gradle_package_pins(distributions: list, gradle_path: Path) -> None:
    """Require APK metadata for every directly pinned Chaquopy distribution."""
    expected = pinned_versions(gradle_path.read_text(encoding="utf-8"))
    if not expected:
        raise ValueError("Gradle build has no pinned Python distributions to inventory")
    actual = {
        canonicalize_name(distribution.metadata["Name"]): str(distribution.version)
        for distribution in distributions
    }
    missing = sorted(name for name in expected if name not in actual)
    mismatched = sorted(
        f"{name}=={expected[name]} (APK has {actual[name]})"
        for name in expected
        if name in actual and actual[name] != expected[name]
    )
    if missing or mismatched:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if mismatched:
            details.append("version_mismatch=" + ",".join(mismatched))
        raise ValueError("APK Python SBOM does not match Gradle pins: " + "; ".join(details))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--gradle", type=Path, default=Path("android/apk/app/build.gradle"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--xml-output", type=Path, required=True)
    parser.add_argument("--enforce-pillow-baseline", action="store_true")
    args = parser.parse_args()
    try:
        distributions = packaged_distributions(args.apk)
        enforce_gradle_package_pins(distributions, args.gradle)
        if args.enforce_pillow_baseline:
            enforce_android_pillow_policy(distributions)
        document = build_sbom(distributions)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    write_xml_sbom(document, args.xml_output)
    print(f"Inventoried {len(distributions)} bundled Python distributions")


if __name__ == "__main__":
    main()
