"""Production runtime policy; stdlib-only so installers can check before pip.

Requires-Python describes syntax compatibility, not vendor security support.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile

_RUNTIME_PROBLEM_UNSET = object()
_RUNTIME_PROBLEM_CACHE = _RUNTIME_PROBLEM_UNSET


JAMMY_SUPPORT_END = datetime.date(2027, 5, 31)
SUPPORTED_SERIES = {(3, 11), (3, 12), (3, 13), (3, 14)}
JAMMY_PACKAGES = ("python3.10-minimal", "python3.10", "libpython3.10-minimal", "libpython3.10-stdlib")
RUNTIME_PROOF_DIR = Path(os.environ.get("SIMPLEOFFICE_RUNTIME_PROOF_DIR", "/var/cache/simpleoffice4me/runtime-proof"))


def _command(*args: str) -> str:
    return subprocess.check_output(
        args, text=True, stderr=subprocess.DEVNULL, timeout=10,
        env={**os.environ, "LC_ALL": "C"},
    ).strip()


def _canonical_package_version(version: str, package: str = "python3.10-minimal") -> bool:
    """Match the installed version to Ubuntu-origin Jammy indexes, including mirrors."""
    package_policy = _command("apt-cache", "policy", package)
    candidate = re.search(r"(?m)^\s*Candidate:\s*(\S+)\s*$", package_policy)
    if not candidate or candidate.group(1) != version:
        return False
    current_version = None
    version_indexes = {}
    for line in package_policy.splitlines():
        header = re.fullmatch(r"\s*(?:\*\*\*\s+)?(\S+)\s+-?\d+\s*", line)
        if header:
            current_version = header.group(1)
            continue
        fields = line.split()
        if current_version and len(fields) >= 5 and fields[0].lstrip("-").isdigit():
            version_indexes.setdefault(current_version, set()).add(tuple(fields[1:]))
    current_index = None
    official_indexes = set()
    suites = set()
    for line in _command("apt-cache", "policy").splitlines():
        fields = line.split()
        if fields and fields[0].lstrip("-").isdigit():
            current_index = tuple(fields[1:])
        elif line.strip().startswith("release "):
            release = dict(field.split("=", 1) for field in line.strip()[8:].split(",") if "=" in field)
            if (release.get("o") == "Ubuntu" and release.get("l") == "Ubuntu"
                    and release.get("n") == "jammy" and release.get("c") == "main"
                    and release.get("a") in {"jammy", "jammy-updates", "jammy-security"}):
                suites.add(release["a"])
                official_indexes.add(current_index)
    indexes = version_indexes.get(version, set())
    if not indexes or not indexes <= official_indexes or not {"jammy-updates", "jammy-security"} <= suites:
        return False
    for advertised, sources in version_indexes.items():
        if advertised != version and sources & official_indexes:
            try:
                _command("dpkg", "--compare-versions", advertised, "le", version)
            except subprocess.CalledProcessError:
                return False
    return True


def _metadata(path: Path) -> tuple[int, int, int]:
    stat = path.lstat()
    return stat.st_uid, stat.st_gid, stat.st_mode & 0o7777


def _same_file(left: Path, right: Path) -> bool:
    if _metadata(left) != _metadata(right):
        return False
    if left.is_symlink() or right.is_symlink():
        return left.is_symlink() and right.is_symlink() and os.readlink(left) == os.readlink(right)
    if not left.is_file() or not right.is_file():
        return left.exists() == right.exists()
    def digest(path: Path) -> str:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()
    return digest(left) == digest(right)


def _canonical_package_bytes(package: str, version: str) -> bool:
    """Compare the complete installed payload with the authenticated APT archive."""
    with tempfile.TemporaryDirectory(prefix="simpleoffice-runtime-") as directory:
        root = Path(directory)
        env = {**os.environ, "LC_ALL": "C"}
        subprocess.run(
            ["apt-get", "download", f"{package}={version}"],
            cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=60, check=True,
        )
        archives = list(root.glob("*.deb"))
        if len(archives) != 1:
            return False
        extracted = root / "archive"
        subprocess.run(
            ["dpkg-deb", "-x", str(archives[0]), str(extracted)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=True,
        )
        installed_paths = {
            item for item in _command("dpkg-query", "-L", package).splitlines()
            if item.startswith("/") and item not in {"/", "/."}
        }
        archive_paths = {
            "/" + str(path.relative_to(extracted))
            for path in extracted.rglob("*")
        }
        if installed_paths != archive_paths:
            return False
        return all(_same_file(Path(item), extracted / item.lstrip("/")) for item in archive_paths)


def _package_manifest(package: str) -> dict[str, str]:
    manifest = {}
    for item in _command("dpkg-query", "-L", package).splitlines():
        if not item.startswith("/") or item in {"/", "/."}:
            continue
        path = Path(item)
        metadata = "%d:%d:%o:" % _metadata(path)
        if path.is_symlink():
            manifest[item] = metadata + "link:" + os.readlink(path)
        elif path.is_file():
            value = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(chunk)
            manifest[item] = metadata + "sha256:" + value.hexdigest()
        elif path.is_dir():
            manifest[item] = metadata + "dir"
        else:
            manifest[item] = metadata + "other"
    return manifest


def _jammy_runtime_failure_details() -> str:
    """Explain a rejected distro interpreter without weakening the runtime gate."""
    problems = []
    try:
        release = dict(
            line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line and not line.startswith("#")
        )
        if release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "22.04":
            problems.append("Betriebssystem ist nicht Ubuntu 22.04.")
        base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        if base != Path("/usr/bin/python3.10"):
            problems.append(f"Python-Basis {base} ist nicht /usr/bin/python3.10.")
        elif _command("dpkg-query", "-S", str(base)) != "python3.10-minimal: /usr/bin/python3.10":
            problems.append("Python-Basis wird nicht von python3.10-minimal bereitgestellt.")
        for package in JAMMY_PACKAGES:
            try:
                status = _command("dpkg-query", "-W", "-f=${Status}\\n${Version}", package).splitlines()
                if len(status) != 2 or status[0] != "install ok installed":
                    problems.append(f"{package}: nicht vollstaendig installiert.")
                    continue
                if not _canonical_package_version(status[1], package):
                    problems.append(f"{package} ({status[1]}): APT-Kandidat oder Ubuntu-Jammy-Herkunft nicht bestaetigt; apt-cache policy pruefen.")
                verification = _command("dpkg", "--verify", package)
                if verification:
                    problems.append(f"{package}: dpkg --verify meldet Abweichungen: {verification[:300]}")
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                problems.append(f"{package}: Paketpruefung fehlgeschlagen ({type(exc).__name__}: {exc}).")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        problems.append(f"Systempruefung fehlgeschlagen ({type(exc).__name__}: {exc}).")
    return "\\n".join(problems) if problems else "Keine Einzelursache ermittelt; siehe APT-Konfiguration und Interpreter-Pfad."


def write_runtime_proof(directory: Path = RUNTIME_PROOF_DIR) -> None:
    """Persist root-owned Jammy evidence after local package-integrity checks.

    APT provenance/version checks and dpkg --verify remain mandatory. Downloading
    and byte-comparing Canonical .deb archives is deliberately not required:
    mirrors and CI runners do not reliably retain/download the exact archive.
    """
    if not _jammy_python(allow_network=True, verify_archive_bytes=False):
        raise RuntimeError("Ubuntu-22.04-Runtime konnte nicht verifiziert werden.\\n" + _jammy_runtime_failure_details())
    proof = {"packages": {}}
    for package in JAMMY_PACKAGES:
        version = _command("dpkg-query", "-W", "-f=${Version}", package)
        proof["packages"][package] = {"version": version, "manifest": _package_manifest(package)}
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "jammy-python310.json"
    fd, temporary_name = tempfile.mkstemp(prefix=".jammy-python310.", suffix=".tmp", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(proof, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _local_runtime_proof(directory: Path = RUNTIME_PROOF_DIR) -> bool:
    try:
        target = directory / "jammy-python310.json"
        stat = target.stat()
        if stat.st_uid != 0 or stat.st_mode & 0o022:
            return False
        proof = json.loads(target.read_text(encoding="utf-8"))
        for package in JAMMY_PACKAGES:
            version = _command("dpkg-query", "-W", "-f=${Version}", package)
            expected = proof["packages"][package]
            if expected["version"] != version or expected["manifest"] != _package_manifest(package):
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return False


def _jammy_python(allow_network: bool = False, verify_archive_bytes: bool = True) -> bool:
    """Recognize the packaged base interpreter, including venvs, fail closed."""
    try:
        release = dict(
            line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line and not line.startswith("#")
        )
        if release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "22.04":
            return False
        base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        if base != Path("/usr/bin/python3.10"):
            return False
        if _command("dpkg-query", "-S", str(base)) != "python3.10-minimal: /usr/bin/python3.10":
            return False
        for package in JAMMY_PACKAGES:
            status = _command("dpkg-query", "-W", "-f=${Status}\n${Version}", package).splitlines()
            if len(status) != 2 or status[0] != "install ok installed" or not _canonical_package_version(status[1], package):
                return False
            if _command("dpkg", "--verify", package):
                return False
            if allow_network and verify_archive_bytes and not _canonical_package_bytes(package, status[1]):
                return False
        return True if allow_network else _local_runtime_proof()
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def runtime_problem() -> str | None:
    version = sys.version_info
    series = tuple(version[:2])
    if platform.python_implementation() != "CPython":
        return "Unterstützt wird CPython; siehe docs/PYTHON_RUNTIME_SUPPORT.md."
    if (tuple(version[:3]) == (3, 15, 0) and version.releaselevel == "candidate"
            and version.serial == 3 and os.environ.get("SIMPLEOFFICE_ALLOW_PRERELEASE") == "1"):
        return None
    if version.releaselevel != "final":
        return "Python-Vorabversionen sind nur mit SIMPLEOFFICE_ALLOW_PRERELEASE=1 für Kompatibilitätstests zugelassen."
    if series in SUPPORTED_SERIES:
        return None
    if series == (3, 10):
        if datetime.date.today() <= JAMMY_SUPPORT_END and _jammy_python():
            return None
        return (
            "Python 3.10 ist upstream EOL. Ausnahme: unverändertes Ubuntu-22.04-Systempaket "
            "python3.10-minimal bis 31.05.2027 (auch dessen venv). Sicherheitsupdates müssen aktiviert "
            "und installiert sein. Sonst CPython 3.11–3.14 verwenden."
        )
    return "Freigegeben sind CPython 3.11–3.14; Python 3.15 benötigt nach dem Final-Release eine gesonderte Freigabe."


def require_supported_runtime() -> None:
    global _RUNTIME_PROBLEM_CACHE
    if _RUNTIME_PROBLEM_CACHE is _RUNTIME_PROBLEM_UNSET:
        _RUNTIME_PROBLEM_CACHE = runtime_problem()
    problem = _RUNTIME_PROBLEM_CACHE
    if problem:
        raise RuntimeError(problem)


if __name__ == "__main__":
    if "--write-proof" in sys.argv:
        write_runtime_proof()
        raise SystemExit(0)
    problem = runtime_problem()
    if problem:
        print(problem, file=sys.stderr)
        raise SystemExit(1)
