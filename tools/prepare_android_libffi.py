"""Apply the Android ELF symbol-prefix compatibility patch to pinned libffi source."""
from __future__ import annotations

import argparse
from pathlib import Path
import re


LIBFFI_VERSION = "3.4.6"
PATCH_MARKER = "SO4ME_ANDROID_SYMBOL_PREFIX"
LIBFFI_VERSION_RE = re.compile(r"(?m)^AC_INIT\(\[libffi\],\[([^\]]+)\]")
SYMBOL_PROBE_RE = re.compile(r"(?m)^[ \t]*LT_SYS_SYMBOL_USCORE[ \t]*$")
ANDROID_SYMBOL_FALLBACK = (
    "dnl SO4ME_ANDROID_SYMBOL_PREFIX: Android AArch64 ELF symbols are not underscore-prefixed.\n"
    "sys_symbol_underscore=no"
)


def patch_android_libffi(source_directory: Path) -> bool:
    """Patch only the known libffi release and exact obsolete configure probe."""
    configure = Path(source_directory) / "configure.ac"
    text = configure.read_text(encoding="utf-8")
    version_match = LIBFFI_VERSION_RE.search(text)
    if version_match is None or version_match.group(1) != LIBFFI_VERSION:
        raise ValueError(f"expected libffi {LIBFFI_VERSION} configure.ac: {configure}")
    if PATCH_MARKER in text:
        if SYMBOL_PROBE_RE.search(text):
            raise ValueError("libffi source contains both the Android patch and obsolete symbol probe")
        return False
    patched, replacements = SYMBOL_PROBE_RE.subn(ANDROID_SYMBOL_FALLBACK, text)
    if replacements != 1:
        raise ValueError(
            "expected exactly one LT_SYS_SYMBOL_USCORE probe in libffi 3.4.6 configure.ac; "
            f"found {replacements}"
        )
    configure.write_text(patched, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_directory", type=Path)
    args = parser.parse_args()
    try:
        changed = patch_android_libffi(args.source_directory)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print("Applied Android ELF compatibility patch" if changed else "Android ELF compatibility patch already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
