"""Optional real Opus/RTP test using existing ffmpeg; never opens audio hardware."""
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from app.audio_streamer import decoder_command, receiver_sdp


@unittest.skipUnless(shutil.which("ffmpeg"), "Existing optional ffmpeg is unavailable")
class AudioRtpLoopbackTests(unittest.TestCase):
    def test_opus_stream_decodes_to_pcm_on_loopback(self):
        ffmpeg = shutil.which("ffmpeg")
        encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, timeout=5, check=True).stdout
        if b"libopus" not in encoders:
            self.skipTest("Existing ffmpeg has no libopus encoder")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as first, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as second:
            first.bind(("127.0.0.1", 0))
            port = first.getsockname()[1]
            if port >= 65535:
                self.skipTest("No adjacent RTCP port")
            try:
                second.bind(("127.0.0.1", port + 1))
            except OSError:
                self.skipTest("Adjacent RTCP port is busy")
        with tempfile.TemporaryDirectory() as temp:
            sdp = Path(temp) / "stream.sdp"
            sdp.write_text(receiver_sdp(port))
            command = decoder_command(sdp, "127.0.0.1")
            command[-1:-1] = ["-t", "1"]
            receiver = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            sender = None
            try:
                time.sleep(0.2)
                sender = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=48000", "-t", "8", "-ac", "2", "-c:a", "libopus",
                    "-payload_type", "111", "-f", "rtp", f"rtp://127.0.0.1:{port}"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                pcm, errors = receiver.communicate(timeout=12)
                self.assertEqual(0, receiver.returncode, errors.decode(errors="replace"))
                self.assertGreater(len(pcm), 48000)
                self.assertTrue(any(pcm))
            finally:
                for process in (sender, receiver):
                    if process is not None:
                        if process.poll() is None:
                            process.terminate()
                        try:
                            process.communicate(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
