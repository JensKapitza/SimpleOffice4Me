#!/usr/bin/env python3
"""Fail fast when required native Chaquopy wheels are unavailable for Android.

Reads Gradle's pinned pip packages and the actual public Chaquopy wheel index.
Does not mistake ordinary Linux/PyPI wheels for Android-compatible binaries.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.error import URLError
from urllib.request import urlopen

from packaging.utils import canonicalize_name, parse_wheel_filename

INDEX = "https://chaquo.com/pypi-13.1"
NATIVE = ("cffi", "argon2-cffi-bindings", "pillow", "cryptography", "reportlab")


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href.endswith(".whl"):
                self.names.append(href.rsplit("/", 1)[-1])


def pinned_versions(gradle: str) -> dict[str, str]:
    pins = dict(re.findall(r"install ['\"]([\w.-]+)==([^'\"]+)['\"]", gradle))
    return {canonicalize_name(name): version for name, version in pins.items()}


def compatible_wheels(html: str, package: str, version: str, python: str, abi: str) -> bool:
    parser = Links()
    parser.feed(html)
    python_tag = "cp" + python.replace(".", "")
    abi_tag = abi.replace("-", "_")
    for filename in parser.names:
        try:
            name, wheel_version, _, tags = parse_wheel_filename(filename)
        except ValueError:
            continue
        if name != canonicalize_name(package) or str(wheel_version) != version:
            continue
        if any(
            (tag.interpreter == python_tag or tag.interpreter == "py3")
            and ("android_" in tag.platform)
            and tag.platform.endswith(abi_tag)
            and (tag.abi in (python_tag, "abi3", "none"))
            for tag in tags
        ):
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gradle", type=Path, default=Path("android/apk/app/build.gradle"))
    parser.add_argument("--python", required=True)
    parser.add_argument("--abi", required=True)
    args = parser.parse_args()
    pins = pinned_versions(args.gradle.read_text(encoding="utf-8"))
    errors = []
    for package in NATIVE:
        version = pins.get(package)
        if version is None:
            errors.append(f"{package}: missing explicit version pin")
            continue
        try:
            with urlopen(f"{INDEX}/{package}/", timeout=15) as response:
                html = response.read().decode("utf-8")
        except (OSError, URLError) as exc:
            errors.append(f"{package}: could not inspect wheel index ({exc})")
            continue
        if not compatible_wheels(html, package, version, args.python, args.abi):
            errors.append(f"{package}=={version}: no verified Android wheel for Python {args.python} / {args.abi}")
        else:
            print(f"OK {package}=={version} ({args.python}, {args.abi})")
    if errors:
        parser.exit(1, "Android native wheel compatibility failed:\n" + "\n".join(errors) + "\n")


if __name__ == "__main__":
    main()
