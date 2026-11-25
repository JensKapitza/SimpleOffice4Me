#!/usr/bin/env python3
"""Report optional native tools and OS-specific installation help."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess


TOOLS = {
    "ClamAV": ("clamdscan", "clamscan"),
    "ClamAV-Signaturupdate": ("freshclam",),
    "ImageMagick": ("magick", "convert"),
    "PDF-Vorschau": ("pdftoppm",),
    "PDF-Text": ("pdftotext",),
    "Foto-/Scan-OCR": ("tesseract",),
    "Audio/Video-Vorschau": ("ffmpeg",),
    "Office-Konvertierung": ("libreoffice", "soffice"),
    "PDF/A-Erzeugung": ("gs", "gswin64c", "gswin32c"),
    "EN16931-Validator": ("java",),
}
DOCS = {
    "ClamAV": "https://docs.clamav.net/manual/Installing.html",
    "ImageMagick": "https://imagemagick.org/script/download.php",
    "PDF-Vorschau": "https://poppler.freedesktop.org/",
    "PDF-Text": "https://poppler.freedesktop.org/",
    "Foto-/Scan-OCR": "https://tesseract-ocr.github.io/tessdoc/Installation.html",
    "Audio/Video-Vorschau": "https://ffmpeg.org/download.html",
    "Office-Konvertierung": "https://www.libreoffice.org/download/download-libreoffice/",
    "PDF/A-Erzeugung": "https://www.ghostscript.com/releases/gsdnld.html",
    "EN16931-Validator": "https://adoptium.net/temurin/releases/",
}


def system_family() -> str:
    name = platform.system().casefold()
    if name == "windows":
        return "windows"
    if name == "darwin":
        return "macos"
    try:
        os_release = open("/etc/os-release", encoding="utf-8").read().casefold()
    except OSError:
        os_release = ""
    if any(value in os_release for value in ("debian", "ubuntu", "linux mint")):
        return "debian"
    if any(value in os_release for value in ("fedora", "rhel", "centos", "rocky", "almalinux")):
        return "fedora"
    return "linux"


def _detected_tools() -> dict[str, str | None]:
    return {
        label: next(filter(None, (shutil.which(command) for command in _commands(label, commands))), None)
        for label, commands in TOOLS.items()
    }


def missing_tools() -> list[str]:
    return [label for label, path in _detected_tools().items() if path is None]


def _commands(label: str, commands: tuple[str, ...]) -> tuple[str, ...]:
    # Windows ships an unrelated filesystem utility named convert.exe.
    return ("magick",) if label == "ImageMagick" and os.name == "nt" else commands


def install_help(family: str) -> list[str]:
    if family == "debian":
        return ["sudo apt update", "sudo apt install clamav clamav-daemon imagemagick poppler-utils ffmpeg libreoffice ghostscript default-jre-headless", "sudo freshclam", "sudo systemctl enable --now clamav-daemon"]
    if family == "fedora":
        return ["sudo dnf install clamav clamav-update clamd ImageMagick poppler-utils ffmpeg-free libreoffice-headless ghostscript java-21-openjdk-headless", "sudo freshclam", "sudo systemctl enable --now clamd@scan"]
    if family == "macos":
        return ["brew install clamav imagemagick poppler ffmpeg ghostscript openjdk", "brew install --cask libreoffice", "freshclam"]
    if family == "windows":
        return ["ClamAV: offiziellen Windows-Installer verwenden (Link unten).", "winget install ImageMagick.Q16", "winget install Gyan.FFmpeg", "winget install TheDocumentFoundation.LibreOffice"]
    return ["Installiere ClamAV, ImageMagick, Poppler, FFmpeg und LibreOffice mit dem Paketmanager deines Systems."]


def _ocr_install_hint(family: str) -> str:
    return {
        "debian": "sudo apt install tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng",
        "fedora": "sudo dnf install tesseract tesseract-langpack-deu tesseract-langpack-eng",
        "macos": "brew install tesseract tesseract-lang",
    }.get(family, "Tesseract und die Sprachdaten deu/eng nach der offiziellen Anleitung bereitstellen.")


def _ocr_languages(executable: str) -> set[bytes] | None:
    try:
        result = subprocess.run(
            [executable, "--list-langs"], stdin=subprocess.DEVNULL,
            capture_output=True, timeout=4, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip() in {b"deu", b"eng"}}


def report(only_missing: bool = False) -> int:
    family = system_family()
    detected = _detected_tools()
    missing = [label for label, path in detected.items() if path is None]
    if only_missing and not missing:
        return 0
    print(f"Systemprüfung ({family}):")
    for label, found in detected.items():
        if not only_missing or not found:
            candidates = _commands(label, TOOLS[label])
            print(f"  {'OK' if found else 'FEHLT'} {label}: {found or '/'.join(candidates)}")

    tesseract = detected["Foto-/Scan-OCR"]
    missing_languages = []
    if not only_missing and tesseract:
        languages = _ocr_languages(tesseract)
        if languages is None:
            print("  UNKLAR OCR-Sprachdaten: Tesseract konnte sie nicht auflisten. Installation und TESSDATA_PREFIX prüfen.")
        else:
            missing_languages = [code for code in ("deu", "eng") if code.encode("ascii") not in languages]
            if missing_languages:
                print(f"  FEHLT OCR-Sprachdaten: {', '.join(missing_languages)}")
            else:
                print("  OK OCR-Sprachdaten gelistet: deu, eng")
    if missing:
        print("Optionale Systemwerkzeuge fehlen. Passende Installation:")
        if any(label != "Foto-/Scan-OCR" for label in missing):
            for line in install_help(family):
                print(f"  {line}")
        for label in dict.fromkeys("ClamAV" if name.startswith("ClamAV") else name for name in missing):
            if label in DOCS:
                print(f"  {label}: {DOCS[label]}")
    if not tesseract or missing_languages:
        print(f"  Optionale OCR-Installation: {_ocr_install_hint(family)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Optionale SimpleOffice-Systemwerkzeuge prüfen")
    parser.add_argument("--missing-only", action="store_true")
    return report(parser.parse_args().missing_only)


if __name__ == "__main__":
    raise SystemExit(main())
