#!/usr/bin/env python3
"""Report optional native tools and OS-specific installation help."""

from __future__ import annotations

import argparse
import os
import platform
import shutil


TOOLS = {
    "ClamAV": ("clamdscan", "clamscan"),
    "ClamAV-Signaturupdate": ("freshclam",),
    "ImageMagick": ("magick", "convert"),
    "PDF-Vorschau": ("pdftoppm",),
    "Audio/Video-Vorschau": ("ffmpeg",),
    "Office-Konvertierung": ("libreoffice", "soffice"),
    "PDF/A-Erzeugung": ("gs", "gswin64c", "gswin32c"),
    "EN16931-Validator": ("java",),
}
DOCS = {
    "ClamAV": "https://docs.clamav.net/manual/Installing.html",
    "ImageMagick": "https://imagemagick.org/script/download.php",
    "PDF-Vorschau": "https://poppler.freedesktop.org/",
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


def missing_tools() -> list[str]:
    return [label for label, commands in TOOLS.items() if not any(shutil.which(command) for command in _commands(label, commands))]


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


def report(only_missing: bool = False) -> int:
    family = system_family()
    missing = missing_tools()
    if not only_missing or missing:
        print(f"Systemprüfung ({family}):")
        for label, commands in TOOLS.items():
            candidates = _commands(label, commands)
            found = next((shutil.which(command) for command in candidates if shutil.which(command)), None)
            if found or not only_missing:
                print(f"  {'OK' if found else 'FEHLT'} {label}: {found or '/'.join(candidates)}")
    if missing:
        print("Optionale Systemwerkzeuge fehlen. Passende Installation:")
        for line in install_help(family):
            print(f"  {line}")
        for label in missing:
            link_label = "ClamAV" if label.startswith("ClamAV") else label
            if link_label in DOCS:
                print(f"  {link_label}: {DOCS[link_label]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Optionale SimpleOffice-Systemwerkzeuge prüfen")
    parser.add_argument("--missing-only", action="store_true")
    return report(parser.parse_args().missing_only)


if __name__ == "__main__":
    raise SystemExit(main())
