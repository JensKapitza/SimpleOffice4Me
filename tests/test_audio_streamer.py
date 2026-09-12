from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.audio_streamer import (
    decoder_command,
    normalize_destinations,
    paplay_command,
    receiver_sdp,
    sender_command,
)


ROOT = Path(__file__).resolve().parents[1]


class AudioStreamerTests(unittest.TestCase):
    def test_destinations_are_validated_and_deduplicated(self) -> None:
        values = [
            {"host": "192.168.1.20", "port": 5004},
            {"host": "192.168.1.20", "port": 5004},
            {"host": "pi-kueche.local", "port": 5006},
        ]
        self.assertEqual(
            normalize_destinations(values),
            [("192.168.1.20", 5004), ("pi-kueche.local", 5006)],
        )
        with self.assertRaises(ValueError):
            normalize_destinations([{"host": "x;touch /tmp/pwn", "port": 5004}])
        with self.assertRaises(ValueError):
            normalize_destinations([{"host": "127.0.0.1", "port": 22}])

    @patch("app.audio_streamer.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_sender_uses_opus_low_delay_rtp_without_shell(self, _which) -> None:
        command = sender_command(
            source="default",
            backend="pulse",
            destinations=[("192.168.1.20", 5004), ("127.0.0.1", 5006)],
            bitrate_kbps=64,
        )
        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertIn("libopus", command)
        self.assertIn("lowdelay", command)
        self.assertIn("111", command)
        self.assertIn("rtp://192.168.1.20:5004?pkt_size=1200", command)
        self.assertIn("rtp://127.0.0.1:5006?pkt_size=1200", command)
        self.assertNotIn("shell=True", command)

    def test_sdp_declares_opus_payload(self) -> None:
        sdp = receiver_sdp(5004)
        self.assertIn("m=audio 5004 RTP/AVP 111", sdp)
        self.assertIn("a=rtpmap:111 opus/48000/2", sdp)
        self.assertIn("a=recvonly", sdp)

    @patch("app.audio_streamer.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_decoder_is_low_latency_pcm(self, _which) -> None:
        command = decoder_command("/tmp/live.sdp")
        self.assertIn("file,udp,rtp", command)
        self.assertIn("nobuffer", command)
        self.assertEqual(command[-1], "pipe:1")
        self.assertIn("s16le", command)

    @patch("app.audio_streamer.shutil.which", return_value="/usr/bin/paplay")
    def test_virtual_microphone_playback_uses_named_sink(self, _which) -> None:
        command = paplay_command("simpleoffice_stream")
        self.assertIn("--device=simpleoffice_stream", command)
        self.assertIn("--rate=48000", command)
        self.assertIn("--channels=2", command)

    def test_admin_does_not_expose_exception_text(self) -> None:
        source = (ROOT / "app" / "audio_streamer_admin.py").read_text(encoding="utf-8")
        self.assertNotIn('"error": str(exc)', source)
        self.assertIn('"error_type": type(exc).__name__', source)
        self.assertIn("Audio-Dienst ist auf diesem System nicht verfügbar.", source)
        self.assertIn("Ungültiger RTP-Port.", source)


if __name__ == "__main__":
    unittest.main()
