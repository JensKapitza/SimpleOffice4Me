#!/usr/bin/env python3
"""Verify the launcher image contract in a built, installable Android APK."""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile

from PIL import Image

# Android's 48dp legacy launcher icon at its five standard screen densities.
LAUNCHER_SIZES = {'mdpi': 48, 'hdpi': 72, 'xhdpi': 96, 'xxhdpi': 144, 'xxxhdpi': 192}


def verify_apk(path):
    result = {}
    with zipfile.ZipFile(path) as apk:
        names = apk.namelist()
        for density, size in LAUNCHER_SIZES.items():
            for kind in ('ic_launcher', 'ic_launcher_round'):
                pattern = re.compile(r'res/mipmap-' + density + r'(?:-v\d+)?/' + kind + r'\.png')
                matches = [name for name in names if pattern.fullmatch(name)]
                if len(matches) != 1:
                    raise ValueError(f'Missing or ambiguous {density} {kind} bitmap')
                dimensions, visible = inspect_png(apk, matches[0])
                if dimensions != (size, size) or not visible:
                    raise ValueError(f'Invalid {density} {kind} bitmap')
            result[density] = [size, size]
        foregrounds = [name for name in names if re.fullmatch(
            r'res/drawable(?:-nodpi)?(?:-v\d+)?/simpleoffice_logo_foreground\.png', name)]
        if len(foregrounds) != 1:
            raise ValueError('Missing or ambiguous adaptive launcher foreground')
        dimensions, visible = inspect_png(apk, foregrounds[0])
        if not visible or min(dimensions) < 108 or dimensions[0] != dimensions[1]:
            raise ValueError('Invalid adaptive launcher foreground')
        return {'ok': True, 'launcher_sizes': result, 'adaptive_foreground': list(dimensions)}


def inspect_png(apk, name):
    if apk.getinfo(name).file_size > 1024 * 1024:
        raise ValueError('Launcher image exceeds the allowed size')
    with Image.open(io.BytesIO(apk.read(name))) as image:
        if image.format != 'PNG' or image.width * image.height > 4_000_000:
            raise ValueError('Invalid launcher image format or dimensions')
        image.load()
        return image.size, image.convert('RGBA').getchannel('A').getbbox() is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk')
    args = parser.parse_args()
    try:
        result = verify_apk(args.apk)
    except (OSError, ValueError, zipfile.BadZipFile, Image.DecompressionBombError):
        print(json.dumps({'ok': False, 'error': 'APK launcher assets are missing or invalid'}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
