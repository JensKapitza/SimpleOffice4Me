"""Verify the offline OCR payload in the APK, including actual model hashes."""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

from tools.prepare_android_ocr_models import MODELS

ROOT = Path(__file__).resolve().parents[1]


def verify(path: Path) -> None:
    with ZipFile(path) as apk:
        info = apk.getinfo("lib/arm64-v8a/libsimpleoffice_ocr.so")
        with apk.open(info) as library:
            if library.read(4) != b"\x7fELF":
                raise ValueError("Android OCR native library is not ELF")
        for name, (size, digest) in MODELS.items():
            info = apk.getinfo(f"assets/android-ocr/tessdata/{name}")
            if info.file_size != size or sha256(apk.read(info)).hexdigest() != digest:
                raise ValueError(f"Incorrect packaged OCR model: {name}")
        for license_file in sorted((ROOT / "licenses/android-ocr").glob("*.txt")):
            asset = f"assets/simpleoffice/licenses/android-ocr/{license_file.name}"
            if apk.read(asset) != license_file.read_bytes():
                raise ValueError(f"Incorrect packaged source license: {license_file.name}")
        model_license = "TESSDATA-FAST-APACHE-2.0.txt"
        if apk.read(f"assets/android-ocr/licenses/{model_license}") != (ROOT / "licenses" / model_license).read_bytes():
            raise ValueError("Incorrect packaged language model license")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    verify(parser.parse_args().apk)
    print("Android OCR library, DE/EN model sizes/hashes and licenses verified")


if __name__ == "__main__":
    main()
