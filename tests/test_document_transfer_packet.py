import unittest

from app.document_transfer_packet import (
    MAX_AUDIO_PAYLOAD_BYTES,
    MAX_PACKET_FILE_BYTES,
    decode_packet,
    encode_packet,
    sanitize_filename,
)


class DocumentTransferPacketTests(unittest.TestCase):
    def test_round_trip_is_ascii_and_fixed_mime(self):
        packet = encode_packet("../../voice:note.wav", b"\x00\xffaudio")
        self.assertTrue(packet.isascii())
        result = decode_packet(packet)
        self.assertEqual(result, {
            "filename": "voice_note.wav", "data": b"\x00\xffaudio",
            "mime": "application/octet-stream",
        })

    def test_filename_is_path_free_and_bounded(self):
        self.assertEqual(sanitize_filename("C:\\temp\\../../x?.bin"), "x_.bin")
        self.assertLessEqual(len(sanitize_filename("a" * 200)), 80)

    def test_rejects_bad_checksum_size_and_limits(self):
        packet = encode_packet("a.bin", b"abc")
        with self.assertRaises(ValueError):
            decode_packet(packet.replace("|3|", "|4|"))
        with self.assertRaises(ValueError):
            decode_packet(packet[:-1] + "A")
        with self.assertRaises(ValueError):
            encode_packet("large.bin", b"x" * (MAX_PACKET_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            encode_packet("audio.bin", b"x" * (MAX_AUDIO_PAYLOAD_BYTES + 1), max_bytes=MAX_AUDIO_PAYLOAD_BYTES)


if __name__ == "__main__":
    unittest.main()
