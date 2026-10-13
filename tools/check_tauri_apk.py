#!/usr/bin/env python3
"""Check ARM64 native libraries in a Tauri APK for Android 16 KiB page support."""
import argparse
import json
import struct
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    args = parser.parse_args()
    try:
        with zipfile.ZipFile(args.apk) as archive:
            libraries = [item for item in archive.infolist() if item.filename.startswith('lib/') and item.filename.endswith('.so')]
            if not libraries:
                raise ValueError('ARM64 native libraries are missing')
            for library in libraries:
                if not library.filename.startswith('lib/arm64-v8a/') or library.file_size > 100 * 1024 * 1024:
                    raise ValueError('Expected bounded ARM64 native libraries')
                payload = archive.read(library)
                if payload[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', payload, 18)[0] != 183:
                    raise ValueError('Expected a little-endian ARM64 ELF64 library')
                offset = struct.unpack_from('<Q', payload, 32)[0]
                size, count = struct.unpack_from('<HH', payload, 54)
                if size != 56 or not 1 <= count <= 1000:
                    raise ValueError('ELF program headers are invalid')
                segments = [struct.unpack_from('<IIQQQQQQ', payload, offset + index * size) for index in range(count)]
                loads = [segment for segment in segments if segment[0] == 1]
                if not loads or any(segment[7] < 16384 or segment[7] & (segment[7] - 1) for segment in loads):
                    raise ValueError('ELF load segments require at least 16 KiB alignment')
        print(json.dumps({'ok': True, 'abi': 'arm64-v8a', 'page_size': 16384, 'libraries': len(libraries)}))
        return 0
    except (OSError, ValueError, KeyError, struct.error, zipfile.BadZipFile):
        print(json.dumps({'ok': False, 'error': 'APK must contain valid ARM64 ELF libraries with 16 KiB load alignment'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
