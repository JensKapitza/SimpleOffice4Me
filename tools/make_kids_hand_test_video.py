#!/usr/bin/env python3
"""Build offline Y4M camera fixtures from one pinned public MediaPipe image."""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path

from PIL import Image, ImageOps

WIDTH, HEIGHT = 640, 480
FIXTURE_SHA256 = "ecf8ca2611d08fa25948a4fc10710af9120e88243a54da6356bacea17ff3e36e"


def write_fixture(destination: Path, frame: Image.Image) -> None:
    y, u, v = frame.convert("YCbCr").split()
    uv_size = WIDTH // 2, HEIGHT // 2
    block = (b"FRAME\n" + y.tobytes() + u.resize(uv_size, Image.Resampling.BOX).tobytes()
             + v.resize(uv_size, Image.Resampling.BOX).tobytes())
    with destination.open("wb") as stream:
        stream.write(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F10:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(30):
            stream.write(block)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream_pointing_up_jpg", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    with args.upstream_pointing_up_jpg.open("rb") as stream:
        source = stream.read(1_000_001)
    if len(source) > 1_000_000 or hashlib.sha256(source).hexdigest() != FIXTURE_SHA256:
        parser.error("Only the exact upstream public regression image is accepted")
    args.destination.mkdir(parents=True, exist_ok=True)
    base = Image.new("RGB", (WIDTH, HEIGHT))
    write_fixture(args.destination / "blank.y4m", base)
    with Image.open(io.BytesIO(source)) as image:
        resized = ImageOps.contain(image.convert("RGB"), (WIDTH, HEIGHT))
        base.paste(resized, ((WIDTH - resized.width) // 2, (HEIGHT - resized.height) // 2))
    write_fixture(args.destination / "pointing.y4m", base)


if __name__ == "__main__":
    main()
