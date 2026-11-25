#!/usr/bin/env python3
"""Combine #1307's generated blank and pinned public hand frames, CI only."""
from __future__ import annotations

import argparse
from pathlib import Path

from tools.make_kids_hand_test_video import HEIGHT, WIDTH

HEADER = f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F10:1 Ip A1:1 C420jpeg\n".encode()
FRAME_LENGTH = WIDTH * HEIGHT * 3 // 2


def source_frame(path: Path) -> bytes:
    with path.open("rb") as stream:
        if stream.read(len(HEADER)) != HEADER:
            raise ValueError("Unexpected local Y4M header")
        frame = stream.read(6 + FRAME_LENGTH)
        if len(frame) != 6 + FRAME_LENGTH or not frame.startswith(b"FRAME\n"):
            raise ValueError("Invalid local fixture frame")
        return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_directory", type=Path)
    args = parser.parse_args()
    directory = args.fixture_directory
    blank = source_frame(directory / "blank.y4m")
    hand = source_frame(directory / "pointing.y4m")
    if blank == hand:
        parser.error("Public hand frame must differ from the blank frame")
    with (directory / "hand-nav-release-then-index.y4m").open("wb") as output:
        output.write(HEADER)
        for frame in (blank, hand):
            for _ in range(30):
                output.write(frame)


if __name__ == "__main__":
    main()
