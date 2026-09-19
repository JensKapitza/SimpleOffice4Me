"""Announcement transport using the existing trusted-LAN RTP/Opus receiver."""
import ipaddress
import shutil
from pathlib import Path


def validate_transport(value, node_id):
    if value is None or value == {}:
        return {}
    if node_id == "local" or not isinstance(value, dict) or set(value) != {"kind", "host", "port"}:
        raise ValueError("RTP-Transport benötigt einen externen Knoten sowie kind, host und port")
    if value["kind"] != "rtp-udp" or type(value["port"]) is not int or not 1024 <= value["port"] <= 65534:
        raise ValueError("Transport muss rtp-udp mit Port 1024–65534 sein; Folgeport für RTCP freihalten")
    try:
        address = ipaddress.IPv4Address(value["host"])
    except (ValueError, TypeError, ipaddress.AddressValueError):
        raise ValueError("RTP-Ziel benötigt eine konkrete private IPv4-Adresse") from None
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
    if not any(address in ipaddress.ip_network(network) for network in networks):
        raise ValueError("RTP-Ausgabe ist auf vertrauenswürdige private Netze und Loopback begrenzt")
    if not isinstance(value["host"], str):
        raise ValueError("RTP-Ziel muss eine IPv4-Adresse als Text sein")
    return {"kind": "rtp-udp", "host": str(address), "port": value["port"]}


def announcement_command(path, transport):
    target = validate_transport(transport, "remote")
    if not target:
        raise ValueError("RTP-Transport fehlt")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg fehlt für entfernte Durchsagen")
    # Absolute local path prevents URL/protocol interpretation of filenames.
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-re",
            "-i", str(Path(path).resolve()), "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000",
            "-c:a", "libopus", "-application", "lowdelay", "-frame_duration", "20",
            "-b:a", "64k", "-payload_type", "111", "-f", "rtp",
            f"rtp://{target['host']}:{target['port']}?pkt_size=1200"]
