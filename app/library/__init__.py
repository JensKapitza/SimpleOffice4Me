"""Integrated library workflows and Brother-compatible label printing."""

import re

from . import printer as _printer
from .routes import bp


_original_send = _printer.send


def _send_with_bluetooth_mac(uri: str, data: bytes) -> None:
    """Parse RFCOMM MAC URIs without treating MAC colons as a TCP port."""
    target = str(uri or "").strip()
    lowered = target.casefold()
    if lowered.startswith(("bluetooth://", "bt://")):
        remainder = target.split("://", 1)[1]
        address, separator, channel_text = remainder.partition("/")
        if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", address):
            raise _printer.PrinterError("Bluetooth-Adresse ist ungültig")
        try:
            channel = int(channel_text or "1") if separator else 1
        except ValueError as exc:
            raise _printer.PrinterError("Bluetooth-RFCOMM-Kanal ist ungültig") from exc
        _printer._send_bluetooth(address, channel, data)
        return
    _original_send(target, data)


_printer.send = _send_with_bluetooth_mac

__all__ = ["bp"]
