"""Brother QL/PT label rendering and direct transports.

This is intentionally self-contained. The historical brother_ql repositories are
used as a protocol reference only; SimpleOffice4Me does not depend on those
packages at runtime.
"""
from __future__ import annotations

import io
import ipaddress
import os
import re
import socket
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont, ImageOps


@dataclass(frozen=True)
class Model:
    name: str
    bytes_per_row: int = 90
    offset_r: int = 0
    mode_setting: bool = True
    cutting: bool = True
    expanded: bool = True
    two_color: bool = False
    invalidate_bytes: int = 200


MODELS: dict[str, Model] = {
    "QL-500": Model("QL-500", mode_setting=False, cutting=False, expanded=False),
    "QL-550": Model("QL-550", mode_setting=False),
    "QL-560": Model("QL-560", mode_setting=False),
    "QL-570": Model("QL-570", mode_setting=False),
    "QL-580N": Model("QL-580N"),
    "QL-650TD": Model("QL-650TD"),
    "QL-700": Model("QL-700", mode_setting=False),
    "QL-710W": Model("QL-710W"),
    "QL-720NW": Model("QL-720NW"),
    "QL-800": Model("QL-800", two_color=True, invalidate_bytes=400),
    "QL-810W": Model("QL-810W", two_color=True, invalidate_bytes=400),
    "QL-820NWB": Model("QL-820NWB", two_color=True, invalidate_bytes=400),
    "QL-1050": Model("QL-1050", bytes_per_row=162, offset_r=44),
    "QL-1060N": Model("QL-1060N", bytes_per_row=162, offset_r=44),
    "QL-1100": Model("QL-1100", bytes_per_row=162, offset_r=44),
    "QL-1110NWB": Model("QL-1110NWB", bytes_per_row=162, offset_r=44),
    "QL-1115NWB": Model("QL-1115NWB", bytes_per_row=162, offset_r=44),
    "PT-P750W": Model("PT-P750W", bytes_per_row=16),
    "PT-P900W": Model("PT-P900W", bytes_per_row=70),
}


@dataclass(frozen=True)
class Label:
    name: str
    width_mm: int
    length_mm: int
    printable_width: int
    printable_length: int
    offset_r: int
    feed_margin: int = 35
    red: bool = False

    @property
    def endless(self) -> bool:
        return self.length_mm == 0


LABELS: dict[str, Label] = {
    "12": Label("12", 12, 0, 106, 0, 29),
    "29": Label("29", 29, 0, 306, 0, 6),
    "38": Label("38", 38, 0, 413, 0, 12),
    "50": Label("50", 50, 0, 554, 0, 12),
    "54": Label("54", 54, 0, 590, 0, 0),
    "62": Label("62", 62, 0, 696, 0, 12),
    "62red": Label("62red", 62, 0, 696, 0, 12, red=True),
    "102": Label("102", 102, 0, 1164, 0, 12),
    "103": Label("103", 104, 0, 1200, 0, 12),
    "17x54": Label("17x54", 17, 54, 165, 566, 0, 0),
    "17x87": Label("17x87", 17, 87, 165, 956, 0, 0),
    "23x23": Label("23x23", 23, 23, 202, 202, 42, 0),
    "29x42": Label("29x42", 29, 42, 306, 425, 6, 0),
    "29x90": Label("29x90", 29, 90, 306, 991, 6, 0),
    "39x90": Label("39x90", 38, 90, 413, 991, 12, 0),
    "39x48": Label("39x48", 39, 48, 425, 495, 6, 0),
    "52x29": Label("52x29", 52, 29, 578, 271, 0, 0),
    "60x86": Label("60x86", 60, 87, 672, 954, 18, 0),
    "62x29": Label("62x29", 62, 29, 696, 271, 12, 0),
    "62x100": Label("62x100", 62, 100, 696, 1109, 12, 0),
    "102x51": Label("102x51", 102, 51, 1164, 526, 12, 0),
    "102x152": Label("102x152", 102, 153, 1164, 1660, 12, 0),
    "103x164": Label("103x164", 104, 164, 1200, 1822, 12, 0),
    "d12": Label("d12", 12, 12, 94, 94, 113, 35),
    "d24": Label("d24", 24, 24, 236, 236, 42, 0),
    "d58": Label("d58", 58, 58, 618, 618, 51, 0),
    "pt24": Label("pt24", 24, 0, 128, 0, 0, 14),
}


class PrinterError(RuntimeError):
    pass


def _font_candidates(name: str, bold: bool) -> list[str]:
    family = str(name or "DejaVuSans").strip()
    suffix = "-Bold" if bold else ""
    values = [family]
    if not family.lower().endswith((".ttf", ".otf")):
        values.extend([
            f"{family}{suffix}.ttf",
            f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
            f"/system/fonts/Roboto{'-Bold' if bold else '-Regular'}.ttf",
        ])
    return list(dict.fromkeys(values))


def load_font(name: str, size: int, bold: bool = False):
    for candidate in _font_candidates(name, bold):
        try:
            return ImageFont.truetype(candidate, int(size))
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int, int, int]:
    try:
        return draw.textbbox((0, 0), text, font=font)
    except AttributeError:
        width, height = draw.textsize(text, font=font)
        return (0, 0, width, height)


def render_text(text: str, settings: dict[str, Any]) -> Image.Image:
    label = LABELS.get(str(settings.get("label", "62")).lower())
    if label is None:
        raise PrinterError("Unbekannte Etikettengröße")
    clean = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split()).strip()
    if not clean:
        raise PrinterError("Text fehlt")
    size = max(8, min(int(settings.get("font_size", 56)), 300))
    font = load_font(str(settings.get("font_name", "DejaVuSans")), size, bool(settings.get("bold")))
    probe = Image.new("RGB", (label.printable_width, max(size * 3, 100)), "white")
    draw = ImageDraw.Draw(probe)
    box = _text_bbox(draw, clean, font)
    text_width = max(1, box[2] - box[0])
    text_height = max(1, box[3] - box[1])
    padding = max(12, size // 3)
    if label.endless:
        height = max(80, text_height + padding * 2)
    else:
        height = label.printable_length
    image = Image.new("RGB", (label.printable_width, height), "white")
    draw = ImageDraw.Draw(image)
    align = str(settings.get("align", "center"))
    if align == "left":
        x = padding
    elif align == "right":
        x = max(padding, label.printable_width - text_width - padding)
    else:
        x = max(0, (label.printable_width - text_width) // 2)
    y = max(0, (height - text_height) // 2 - box[1])
    color = (220, 0, 0) if str(settings.get("color", "black")) == "red" else (0, 0, 0)
    draw.text((x, y), clean, font=font, fill=color)
    return image


def _code128_bits(value: str) -> str:
    """Return Code 128-B modules for printable ASCII text."""
    clean = str(value or "").strip()
    if not clean or any(ord(char) < 32 or ord(char) > 126 for char in clean):
        raise PrinterError("Code128 unterstützt hier druckbare ASCII-Zeichen")
    patterns = (
        "212222","222122","222221","121223","121322","131222","122213","122312","132212","221213",
        "221312","231212","112232","122132","122231","113222","123122","123221","223211","221132",
        "221231","213212","223112","312131","311222","321122","321221","312212","322112","322211",
        "212123","212321","232121","111323","131123","131321","112313","132113","132311","211313",
        "231113","231311","112133","112331","132131","113123","113321","133121","313121","211331",
        "231131","213113","213311","213131","311123","311321","331121","312113","312311","332111",
        "314111","221411","431111","111224","111422","121124","121421","141122","141221","112214",
        "112412","122114","122411","142112","142211","241211","221114","413111","241112","134111",
        "111242","121142","121241","114212","124112","124211","411212","421112","421211","212141",
        "214121","412121","111143","111341","131141","114113","114311","411113","411311","113141",
        "114131","311141","411131","211412","211214","211232","2331112",
    )
    codes = [104] + [ord(char) - 32 for char in clean]
    checksum = (104 + sum(index * code for index, code in enumerate(codes[1:], start=1))) % 103
    codes.extend([checksum, 106])
    modules = []
    for code in codes:
        pattern = patterns[code]
        black = True
        for digit in pattern:
            modules.append(("1" if black else "0") * int(digit))
            black = not black
    return "".join(modules)


def render_barcode(value: str, settings: dict[str, Any], caption: str = "") -> Image.Image:
    label = LABELS.get(str(settings.get("label", "62")).lower())
    if label is None:
        raise PrinterError("Unbekannte Etikettengröße")
    bits = _code128_bits(value)
    quiet = 12
    usable = max(1, label.printable_width - quiet * 2)
    module = max(1, usable // len(bits))
    barcode_width = len(bits) * module
    font = load_font(str(settings.get("font_name", "DejaVuSans")), max(16, min(int(settings.get("font_size", 56)) // 2, 60)), bool(settings.get("bold")))
    caption_text = " ".join(str(caption or value).split())[:120]
    probe = Image.new("RGB", (label.printable_width, 100), "white")
    box = _text_bbox(ImageDraw.Draw(probe), caption_text, font)
    caption_height = max(1, box[3] - box[1])
    height = label.printable_length if not label.endless else max(170, caption_height + 135)
    image = Image.new("RGB", (label.printable_width, height), "white")
    draw = ImageDraw.Draw(image)
    left = max(0, (label.printable_width - barcode_width) // 2)
    bar_top = 12
    bar_bottom = max(bar_top + 40, height - caption_height - 24)
    for index, bit in enumerate(bits):
        if bit == "1":
            x0 = left + index * module
            draw.rectangle((x0, bar_top, x0 + module - 1, bar_bottom), fill="black")
    text_width = box[2] - box[0]
    draw.text((max(0, (label.printable_width - text_width) // 2), height - caption_height - 8 - box[1]), caption_text, font=font, fill="black")
    return image


def render_image(upload: bytes, settings: dict[str, Any]) -> Image.Image:
    label = LABELS.get(str(settings.get("label", "62")).lower())
    if label is None:
        raise PrinterError("Unbekannte Etikettengröße")
    if not upload or len(upload) > 12 * 1024 * 1024:
        raise PrinterError("Bild fehlt oder ist größer als 12 MiB")
    try:
        source = Image.open(io.BytesIO(upload))
        source.load()
    except Exception as exc:
        raise PrinterError("Bild konnte nicht gelesen werden") from exc
    source = ImageOps.exif_transpose(source).convert("RGB")
    max_height = label.printable_length if not label.endless else max(160, min(1200, int(label.printable_width * source.height / max(1, source.width))))
    source.thumbnail((label.printable_width, max_height), Image.Resampling.LANCZOS)
    height = label.printable_length if not label.endless else max(80, source.height)
    canvas = Image.new("RGB", (label.printable_width, height), "white")
    canvas.paste(source, ((canvas.width - source.width) // 2, (canvas.height - source.height) // 2))
    return canvas


def preview_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _raster_layer(image: Image.Image, device_width: int, left: int, *, red: bool = False) -> Image.Image:
    rgb = image.convert("RGB")
    if red:
        pixels = Image.new("L", rgb.size, 255)
        src = rgb.load(); dst = pixels.load()
        for y in range(rgb.height):
            for x in range(rgb.width):
                r, g, b = src[x, y]
                dst[x, y] = 0 if r > 120 and r > g * 1.35 and r > b * 1.35 else 255
    else:
        gray = ImageOps.grayscale(rgb)
        pixels = gray.point(lambda value: 0 if value < 150 else 255, mode="1")
        if any(pixel[0] > 120 and pixel[0] > pixel[1] * 1.35 and pixel[0] > pixel[2] * 1.35 for pixel in list(rgb.getdata())[::max(1, rgb.width * rgb.height // 5000)]):
            # Red is intentionally omitted from the black layer where detected.
            black = Image.new("1", rgb.size, 1)
            src = rgb.load(); dst = black.load()
            for y in range(rgb.height):
                for x in range(rgb.width):
                    r, g, b = src[x, y]
                    is_red = r > 120 and r > g * 1.35 and r > b * 1.35
                    dst[x, y] = 1 if is_red else pixels.getpixel((x, y))
            pixels = black
    canvas = Image.new("1", (device_width, image.height), 1)
    canvas.paste(pixels, (left, 0))
    return canvas


def build_raster(image: Image.Image, settings: dict[str, Any]) -> bytes:
    model_name = str(settings.get("model", "QL-820NWB")).upper()
    model = MODELS.get(model_name)
    if model is None:
        raise PrinterError(f"Druckermodell {model_name} wird noch nicht unterstützt")
    label_name = str(settings.get("label", "62")).lower()
    label = LABELS.get(label_name)
    if label is None:
        raise PrinterError("Unbekannte Etikettengröße")
    if image.width != label.printable_width:
        raise PrinterError("Etikettbreite passt nicht zum ausgewählten Medium")
    use_red = str(settings.get("color", "black")) == "red" or label.red
    if use_red and (not model.two_color or not label.red):
        raise PrinterError("Rotdruck benötigt einen QL-8xx-Drucker und 62red-Medium")
    device_width = model.bytes_per_row * 8
    right_margin = label.offset_r + model.offset_r
    left = device_width - image.width - right_margin
    if left < 0:
        raise PrinterError("Etikett ist für dieses Druckermodell zu breit")
    black = _raster_layer(image, device_width, left, red=False)
    red = _raster_layer(image, device_width, left, red=True) if use_red else None
    cut = bool(settings.get("cut", True))
    data = bytearray(b"\x00" * model.invalidate_bytes)
    data.extend(b"\x1b@")
    if model.mode_setting:
        data.extend(b"\x1bia\x01")
    data.extend(b"\x1biS")
    mtype = 0x0A if label.endless else 0x0B
    if label_name.startswith("pt"):
        mtype = 0x00
    flags = 0x80 | 0x02 | 0x04 | 0x08 | 0x40
    data.extend(b"\x1biz")
    data.extend(bytes([flags, mtype, label.width_mm & 0xFF, label.length_mm & 0xFF]))
    data.extend(struct.pack("<L", image.height))
    data.extend(b"\x00\x00")
    if cut and model.cutting:
        data.extend(b"\x1biM\x40")
        data.extend(b"\x1biA\x01")
    if model.expanded:
        expanded = (0x08 if cut else 0x00) | (0x01 if use_red else 0x00)
        data.extend(b"\x1biK" + bytes([expanded]))
    data.extend(b"\x1bid" + struct.pack("<H", label.feed_margin))
    black = black.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    red = red.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if red else None
    black_bytes = black.tobytes()
    red_bytes = red.tobytes() if red else b""
    row_len = model.bytes_per_row
    for row in range(image.height):
        start = row * row_len
        if red:
            data.extend(b"w\x01" + bytes([row_len]) + black_bytes[start:start + row_len])
            data.extend(b"w\x02" + bytes([row_len]) + red_bytes[start:start + row_len])
        elif model.name.startswith("PT"):
            data.extend(b"G" + struct.pack("<H", row_len) + black_bytes[start:start + row_len])
        else:
            data.extend(b"g\x00" + bytes([row_len]) + black_bytes[start:start + row_len])
    data.extend(b"\x1a")
    return bytes(data)


def _private_addresses(host: str, port: int) -> list[tuple]:
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PrinterError("Druckername/IP konnte nicht aufgelöst werden") from exc
    allowed = []
    for row in rows:
        address = row[4][0]
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            allowed.append(row)
    if not allowed:
        raise PrinterError("Netzwerkdruck ist nur zu lokalen/privaten Adressen erlaubt")
    return allowed


def _send_tcp(host: str, port: int, data: bytes) -> None:
    rows = _private_addresses(host, port)
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in rows:
        try:
            with socket.socket(family, socktype, proto) as connection:
                connection.settimeout(5)
                connection.connect(sockaddr)
                connection.sendall(data)
                return
        except OSError as exc:
            last_error = exc
    raise PrinterError(f"WLAN/LAN-Drucker nicht erreichbar: {last_error or 'unbekannter Fehler'}")


def _send_device(path: str, data: bytes) -> None:
    resolved = str(Path(path).resolve())
    if not re.fullmatch(r"/dev/(?:usb/)?lp\d+", resolved):
        raise PrinterError("USB-Druckerpfad ist nicht erlaubt")
    try:
        with open(resolved, "wb", buffering=0) as device:
            device.write(data)
    except OSError as exc:
        raise PrinterError(f"USB-Drucker konnte nicht beschrieben werden: {exc}") from exc


def _send_bluetooth(mac: str, channel: int, data: bytes) -> None:
    if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac):
        raise PrinterError("Bluetooth-Adresse ist ungültig")
    if not 1 <= int(channel) <= 30:
        raise PrinterError("Bluetooth-RFCOMM-Kanal ist ungültig")
    if not all(hasattr(socket, name) for name in ("AF_BLUETOOTH", "BTPROTO_RFCOMM")):
        raise PrinterError("Python/Plattform unterstützt Bluetooth-RFCOMM hier nicht")
    try:
        connection = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        connection.settimeout(8)
        try:
            connection.connect((mac, int(channel)))
            connection.sendall(data)
        finally:
            connection.close()
    except OSError as exc:
        raise PrinterError(f"Bluetooth-Drucker nicht erreichbar: {exc}") from exc


def send(uri: str, data: bytes) -> None:
    target = str(uri or "").strip()
    if not target:
        raise PrinterError("Kein Drucker konfiguriert")
    if target == "auto://":
        candidates = discover_printers()
        if not candidates:
            raise PrinterError("Kein lokaler Drucker automatisch gefunden")
        target = candidates[0]["uri"]
    parsed = urlsplit(target)
    scheme = parsed.scheme.casefold()
    if scheme in {"tcp", "socket"}:
        host = parsed.hostname or ""
        port = parsed.port or 9100
        if not host:
            raise PrinterError("Drucker-IP/Hostname fehlt")
        _send_tcp(host, port, data)
        return
    if scheme in {"usb", "file", "device"}:
        path = parsed.path or ("/" + parsed.netloc if parsed.netloc else "")
        _send_device(path, data)
        return
    if scheme in {"bluetooth", "bt"}:
        mac = parsed.hostname or parsed.netloc.split("/", 1)[0]
        channel_text = parsed.path.strip("/") or "1"
        _send_bluetooth(mac, int(channel_text), data)
        return
    raise PrinterError("Drucker-URI muss tcp://, usb://, bluetooth:// oder auto:// verwenden")


def _run_optional(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def discover_printers() -> list[dict[str, str]]:
    """Best-effort local discovery without introducing native dependencies."""
    found: list[dict[str, str]] = []
    for pattern in ("/dev/usb/lp*", "/dev/lp*"):
        for path in sorted(Path("/").glob(pattern.lstrip("/"))):
            if path.is_char_device() or path.exists():
                found.append({"kind": "usb", "name": path.name, "uri": f"usb://{path}"})
    for line in _run_optional(["lpstat", "-v"]).splitlines():
        match = re.search(r"device for\s+([^:]+):\s+(socket|ipp|ipps)://([^\s]+)", line, re.I)
        if not match:
            continue
        name, scheme, remainder = match.groups()
        if scheme.casefold() == "socket":
            candidate = "tcp://" + remainder
            parsed = urlsplit(candidate)
            try:
                _private_addresses(parsed.hostname or "", parsed.port or 9100)
            except PrinterError:
                continue
            found.append({"kind": "network", "name": name.strip(), "uri": candidate})
    for line in _run_optional(["bluetoothctl", "devices"]).splitlines():
        match = re.match(r"Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s+(.+)", line.strip())
        if match and any(token in match.group(2).casefold() for token in ("brother", "ql-", "pt-")):
            found.append({"kind": "bluetooth", "name": match.group(2).strip(), "uri": f"bluetooth://{match.group(1)}/1"})
    unique: dict[str, dict[str, str]] = {}
    for row in found:
        unique[row["uri"]] = row
    return list(unique.values())


def print_image(image: Image.Image, settings: dict[str, Any]) -> int:
    data = build_raster(image, settings)
    send(str(settings.get("uri", "")), data)
    return len(data)


def cut_feed(settings: dict[str, Any]) -> int:
    label = LABELS.get(str(settings.get("label", "62")).lower())
    if label is None:
        raise PrinterError("Unbekannte Etikettengröße")
    height = label.printable_length if not label.endless else 80
    image = Image.new("RGB", (label.printable_width, height), "white")
    forced = dict(settings)
    forced["cut"] = True
    return print_image(image, forced)
