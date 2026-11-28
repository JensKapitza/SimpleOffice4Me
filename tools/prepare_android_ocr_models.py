#!/usr/bin/env python3
"""Stage pinned tessdata_fast models for inclusion in the Android APK."""
from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

COMMIT = "65727574dfcd264acbb0c3e07860e4e9e9b22185"  # Signed 4.1.0 tag
BASE = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{COMMIT}"
MODELS = {
    "deu.traineddata": (1525436, "19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d"),
    "eng.traineddata": (4113088, "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"),
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Unexpected OCR model redirect", headers, fp)


def _verified(content: bytes, size: int, digest: str) -> bool:
    return len(content) == size and hashlib.sha256(content).hexdigest() == digest


def stage_models(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(NoRedirect())
    for name, (size, digest) in MODELS.items():
        target = destination / name
        if target.is_file() and target.stat().st_size == size and _verified(target.read_bytes(), size, digest):
            continue
        with opener.open(f"{BASE}/{name}", timeout=30) as response:
            data = response.read(size + 1)
        if not _verified(data, size, digest):
            raise RuntimeError(f"Pinned OCR model {name} failed size/SHA-256 verification")
        staging = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination, delete=False) as output:
                staging = Path(output.name)
                output.write(data)
            os.replace(staging, target)
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    stage_models(parser.parse_args().output)


if __name__ == "__main__":
    main()
