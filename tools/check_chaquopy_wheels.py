#!/usr/bin/env python3
"""Fail fast when required native Chaquopy wheels are unavailable for Android.

Reads Gradle's pinned pip packages and the actual public Chaquopy wheel index.
Does not mistake ordinary Linux/PyPI wheels for Android-compatible binaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from html.parser import HTMLParser
import os
from pathlib import Path
import tempfile
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import re
from urllib.error import URLError
from urllib.request import urlopen

from packaging.utils import canonicalize_name, parse_wheel_filename

INDEX = "https://chaquo.com/pypi-13.1"
NATIVE = ("cffi", "argon2-cffi-bindings", "pillow", "cryptography")
PURE_PYTHON = ("reportlab",)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            filename = unquote(urlsplit(href).path.rsplit("/", 1)[-1])
            if filename.endswith(".whl"):
                self.names.append(filename)


def pinned_versions(gradle: str) -> dict[str, str]:
    pins = dict(re.findall(r"install ['\"]([\w.-]+)==([^'\"]+)['\"]", gradle))
    return {canonicalize_name(name): version for name, version in pins.items()}


def compatible_wheels(html: str, package: str, version: str, python: str, abi: str) -> bool:
    parser = Links()
    parser.feed(html)
    python_tag = "cp" + python.replace(".", "")
    python_minor = int(python.split(".")[1])
    abi_tag = abi.replace("-", "_")
    for filename in parser.names:
        try:
            name, wheel_version, _, tags = parse_wheel_filename(filename)
        except ValueError:
            continue
        if name != canonicalize_name(package) or str(wheel_version) != version:
            continue
        if any(
            tag.platform.startswith("android_")
            and tag.platform.endswith(abi_tag)
            and (
                (tag.interpreter == python_tag and tag.abi in (python_tag, "abi3", "none"))
                or (
                    tag.abi == "abi3"
                    and tag.interpreter.startswith("cp3")
                    and tag.interpreter[3:].isdigit()
                    and int(tag.interpreter[3:]) <= python_minor
                )
                or (tag.interpreter == "py3" and tag.abi == "none")
            )
            for tag in tags
        ):
            return True
    return False


def compatible_pypi_pure_wheel(payload: dict, package: str, version: str) -> bool:
    return pypi_pure_wheel(payload, package, version) is not None


def pypi_pure_wheel(payload: dict, package: str, version: str) -> tuple[str, str, str] | None:
    """Return filename, trusted HTTPS URL, and SHA-256 for an exact universal wheel."""
    info = payload.get("info")
    files = payload.get("urls")
    if not isinstance(info, dict) or not isinstance(files, list):
        raise ValueError("PyPI metadata must contain package info and release files")
    if canonicalize_name(str(info.get("name", ""))) != canonicalize_name(package):
        raise ValueError("PyPI metadata package name does not match the requested package")
    if str(info.get("version", "")) != version:
        raise ValueError("PyPI metadata version does not match the requested version")
    for artifact in files:
        if not isinstance(artifact, dict):
            continue
        filename = artifact.get("filename")
        try:
            name, wheel_version, _, tags = parse_wheel_filename(str(filename))
        except ValueError:
            continue
        if name != canonicalize_name(package) or str(wheel_version) != version:
            continue
        if not any(tag.interpreter == "py3" and tag.abi == "none" and tag.platform == "any" for tag in tags):
            continue
        digest_data = artifact.get("digests")
        digest = digest_data.get("sha256") if isinstance(digest_data, dict) else None
        url = urlsplit(str(artifact.get("url", "")))
        if (
            isinstance(filename, str)
            and Path(filename).name == filename
            and "/" not in filename
            and "\\" not in filename
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-fA-F]{64}", digest)
            and url.scheme == "https"
            and url.hostname == "files.pythonhosted.org"
        ):
            return str(filename), url.geturl(), digest.lower()
    return None


def download_pypi_pure_wheel(payload: dict, package: str, version: str, directory: Path) -> Path:
    """Download an exact, hash-verified universal wheel to the Android wheelhouse."""
    selected = pypi_pure_wheel(payload, package, version)
    if selected is None:
        raise ValueError(f"{package}=={version}: no hash-verified universal Python 3 wheel on PyPI")
    filename, url, expected_hash = selected
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / filename
    digest = hashlib.sha256()
    class TrustedArtifactRedirects(HTTPRedirectHandler):
        def redirect_request(self, request, response, code, message, headers, new_url):
            redirected = urlsplit(new_url)
            if redirected.scheme != "https" or redirected.hostname != "files.pythonhosted.org":
                raise ValueError("PyPI wheel redirect left the trusted HTTPS artifact host")
            return super().redirect_request(request, response, code, message, headers, new_url)

    try:
        with build_opener(TrustedArtifactRedirects()).open(
            Request(url, headers={"Accept": "application/octet-stream"}), timeout=30
        ) as response:  # nosec B310 - fixed HTTPS host with validated redirects
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".wheel-", delete=False) as output:
                temporary = Path(output.name)
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise ValueError("PyPI wheel exceeds the 100 MiB download limit")
                    digest.update(chunk)
                    output.write(chunk)
        if digest.hexdigest() != expected_hash:
            raise ValueError(f"SHA-256 mismatch while downloading {filename}")
        os.replace(temporary, destination)
    except (OSError, URLError, ValueError):
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
        raise
    return destination


def pypi_release_metadata(package: str, version: str) -> dict:
    """Fetch one release record from the fixed, TLS-validated public PyPI host."""
    url = f"https://pypi.org/pypi/{quote(package, safe='')}/{quote(version, safe='')}/json"
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "pypi.org":
        raise ValueError("PyPI metadata URL must use the trusted HTTPS host")
    with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=15) as response:  # nosec B310 - fixed HTTPS host and validated scheme
        return json.loads(response.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gradle", type=Path, default=Path("android/apk/app/build.gradle"))
    parser.add_argument("--python", required=True)
    parser.add_argument("--abi", required=True)
    parser.add_argument("--wheel-dir", type=Path, help="Local Android wheels produced by the native-wheel build job")
    args = parser.parse_args()
    pins = pinned_versions(args.gradle.read_text(encoding="utf-8"))
    errors = []
    for package in PURE_PYTHON:
        version = pins.get(package)
        if version is None:
            errors.append(f"{package}: missing explicit version pin")
            continue
        try:
            payload = pypi_release_metadata(package, version)
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{package}: could not inspect PyPI release metadata ({exc})")
            continue
        if not compatible_pypi_pure_wheel(payload, package, version):
            errors.append(f"{package}=={version}: no hash-verified universal Python 3 wheel on PyPI")
        else:
            if args.wheel_dir:
                try:
                    wheel = download_pypi_pure_wheel(payload, package, version, args.wheel_dir)
                except (OSError, URLError, ValueError) as exc:
                    errors.append(f"{package}=={version}: could not install verified PyPI wheel ({exc})")
                    continue
                print(f"OK PyPI universal wheel {wheel.name} ({args.python}, {args.abi})")
            else:
                print(f"OK PyPI universal wheel {package}=={version} ({args.python}, {args.abi})")
    for package in NATIVE:
        version = pins.get(package)
        if version is None:
            errors.append(f"{package}: missing explicit version pin")
            continue
        if args.wheel_dir and args.wheel_dir.is_dir():
            local = " ".join(f'<a href="{wheel.name}">wheel</a>' for wheel in args.wheel_dir.glob("*.whl"))
            if compatible_wheels(local, package, version, args.python, args.abi):
                print(f"OK local {package}=={version} ({args.python}, {args.abi})")
                continue
        try:
            url = f"{INDEX}/{package}/"
            if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "chaquo.com":
                raise ValueError("wheel index must use trusted HTTPS origin")
            with urlopen(Request(url, headers={"Accept": "text/html"}), timeout=15) as response:  # nosec B310 - fixed HTTPS host and validated scheme
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
