#!/usr/bin/env python3
"""Verify and copy locally supplied official MediaPipe files; never download."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import runpy
import tarfile
import tempfile

REPO = Path(__file__).resolve().parents[1]
# Load only the ordinary-library manifest, without importing the Flask app.
manifest = runpy.run_path(str(REPO / "app" / "kids_hand_assets.py"))
ASSETS = manifest["ASSETS"]
TARBALL_MAX_BYTES = 20 * 1024 * 1024
NOTICE = """SimpleOffice4Me optional Kids Webcam Finger Game
MediaPipe Tasks Vision 0.10.21 — Copyright The MediaPipe Authors.
Official Hand Landmarker full FP16 bundle, version /1/ — MediaPipe Authors.
Both are provided under Apache License, Version 2.0; see LICENSE.txt.
No modifications were made to the upstream runtime or weights.
Code: https://github.com/google-ai-edge/mediapipe/tree/v0.10.21
Model: https://huggingface.co/litert-community/MediaPipe-HandLandmarker-Full
Model paper: Zhang et al., MediaPipe Hands, arXiv:2006.10214.
"""


def load_assets(package: Path, model: Path) -> dict[str, bytes]:
    if package.stat().st_size > TARBALL_MAX_BYTES:
        raise ValueError("Runtime-Paket ist zu groß")
    with package.open("rb") as reader:
        if hashlib.file_digest(reader, "sha256").hexdigest() != manifest["PACKAGE_SHA256"]:
            raise ValueError("Runtime-Paket hat nicht die erwartete Prüfsumme")
        reader.seek(0)
        with tarfile.open(fileobj=reader, mode="r:gz") as archive:
            data = {}
            for name, (_, length, _) in ASSETS.items():
                if name == "hand_landmarker.task":
                    with model.open("rb") as source:
                        data[name] = source.read(length + 1)
                    continue
                path = "package/" + ("wasm/" if name.startswith("vision_wasm_") else "") + name
                member = archive.getmember(path)
                if not member.isfile() or member.size != length:
                    raise ValueError(f"Ungültiges Paketmitglied: {name}")
                with archive.extractfile(member) as source:
                    data[name] = source.read(length + 1)
    for name, value in data.items():
        if not manifest["verify_bytes"](name, value):
            raise ValueError(f"Falsche Prüfsumme: {name}")
    return data


def install(destination: Path, assets: dict[str, bytes]) -> None:
    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    assets = {**assets, "LICENSE.txt": (REPO / "licenses" / "MEDIAPIPE-APACHE-2.0.txt").read_bytes(),
              "NOTICE.txt": NOTICE.encode()}
    for name, data in assets.items():
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as stream:
            scratch = Path(stream.name)
            try:
                stream.write(data)
                stream.close()
                scratch.chmod(0o644)
                os.replace(scratch, destination / name)
            finally:
                scratch.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.destination, load_assets(args.package, args.model))
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        parser.exit(1, f"Lokales Handspiel nicht eingerichtet: {exc}\n")
    print("Handspiel vorbereitet. SIMPLEOFFICE_KIDS_HAND_GAME_ASSET_DIR vor dem Start auf den Zielordner setzen.")


if __name__ == "__main__":
    main()
