"""Extract and report Android Python ELF linkage from a built APK."""

from __future__ import annotations

import argparse
import io
import subprocess
from pathlib import Path
from zipfile import BadZipFile, ZipFile


_RUST_ELF = "cryptography/hazmat/bindings/_rust.abi3.so"
_PYTHON_ELF = "libpython3.15.so"


def _is_relevant_elf(name: str) -> bool:
    return name.endswith(_RUST_ELF) or name.endswith(_PYTHON_ELF)


def _iter_relevant_elfs(
    archive: ZipFile,
    *,
    source: str,
    depth: int = 0,
):
    for item in archive.infolist():
        if item.is_dir():
            continue
        if _is_relevant_elf(item.filename):
            yield f"{source}!/{item.filename}", archive.read(item)
            continue
        if depth >= 1 or not (
            item.filename.startswith("assets/chaquopy/requirements-")
            and item.filename.endswith(".imy")
        ):
            continue
        try:
            nested = ZipFile(io.BytesIO(archive.read(item)))
        except BadZipFile as exc:
            raise ValueError(f"Invalid Chaquopy requirements archive: {item.filename}") from exc
        with nested:
            yield from _iter_relevant_elfs(
                nested,
                source=f"{source}!/{item.filename}",
                depth=depth + 1,
            )


def extract_linkage_diagnostics(apk: Path, output: Path) -> list[Path]:
    """Write readelf dynamic and symbol tables for cryptography and libpython."""
    output.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    with ZipFile(apk) as archive:
        for origin, contents in _iter_relevant_elfs(archive, source=str(apk)):
            name = _RUST_ELF.rsplit("/", 1)[-1] if _RUST_ELF in origin else _PYTHON_ELF
            target = output / name
            target.write_bytes(contents)
            found[name] = target
            print(f"ELF inspected: {origin}")

    expected = {_RUST_ELF.rsplit("/", 1)[-1], _PYTHON_ELF}
    if missing := expected - found.keys():
        raise ValueError(f"APK is missing linkage diagnostic ELF(s): {', '.join(sorted(missing))}")

    reports: list[Path] = []
    for target in found.values():
        for flag, suffix in (("-d", "dynamic"), ("-Ws", "symbols")):
            result = subprocess.run(
                ["readelf", flag, str(target)],
                capture_output=True,
                text=True,
                check=True,
            )
            report = output / f"{target.name}.{suffix}.txt"
            report.write_text(result.stdout, encoding="utf-8")
            reports.append(report)
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    extract_linkage_diagnostics(args.apk, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
