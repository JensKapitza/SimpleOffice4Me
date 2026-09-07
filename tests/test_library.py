import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app.library.printer import (
    LABELS,
    MODELS,
    PrinterError,
    build_raster,
    render_barcode,
    render_text,
    send,
)
from app.library.store import LibraryStore
from app.object_store import ObjectStore


class LibraryStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = LibraryStore(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_locations_get_stable_codes_and_paths(self):
        room = self.store.create_location("Wohnzimmer", "tester")
        shelf = self.store.create_location("Regal 2", "tester", parent_id=room["location_id"])
        self.assertEqual("LIB-L0001", room["code"])
        self.assertEqual("LIB-L0002", shelf["code"])
        self.assertEqual("Wohnzimmer / Regal 2", self.store.location_path(shelf["location_id"]))
        self.assertEqual(shelf["location_id"], self.store.find_location("lib-l0002")["location_id"])

    def test_assignment_and_printer_settings_persist(self):
        location = self.store.create_location("Fach A", "tester", code="KRIMI-A")
        assigned = self.store.assign("object-1", location["location_id"], "tester", scanned="978123")
        self.assertEqual("KRIMI-A", assigned["location_code"])
        self.assertEqual(["object-1"], self.store.contents(location["location_id"]))
        settings = self.store.update_printer_settings({
            "uri": "tcp://192.168.1.50:9100", "model": "QL-1110NWB", "label": "102",
            "font_name": "DejaVuSans", "font_size": 44, "color": "black", "align": "left",
            "bold": True, "cut": False,
        }, "tester")
        self.assertEqual("QL-1110NWB", settings["model"])
        self.assertTrue(LibraryStore(self.temp.name).printer_settings()["bold"])


class BrotherPrinterTest(unittest.TestCase):
    def test_modern_models_and_common_labels_are_available(self):
        self.assertIn("QL-820NWB", MODELS)
        self.assertIn("QL-1110NWB", MODELS)
        self.assertIn("QL-1115NWB", MODELS)
        self.assertIn("62red", LABELS)
        self.assertIn("102x51", LABELS)

    def test_text_and_barcode_render_to_label_width(self):
        settings = {"model": "QL-820NWB", "label": "62", "font_size": 42, "font_name": "DejaVuSans", "color": "black", "align": "center", "bold": False, "cut": True}
        text = render_text("Regal A", settings)
        barcode = render_barcode("LIB-L0001", settings)
        self.assertEqual(LABELS["62"].printable_width, text.width)
        self.assertEqual(LABELS["62"].printable_width, barcode.width)
        self.assertGreater(text.height, 0)
        self.assertGreater(barcode.height, 0)

    def test_raster_contains_initialize_rows_and_print_terminator(self):
        settings = {"model": "QL-1110NWB", "label": "102", "font_size": 36, "font_name": "DejaVuSans", "color": "black", "align": "center", "bold": False, "cut": True}
        image = render_text("Bibliothek", settings)
        payload = build_raster(image, settings)
        self.assertIn(b"\x1b@", payload)
        self.assertIn(b"\x1biz", payload)
        self.assertIn(b"g\x00", payload)
        self.assertTrue(payload.endswith(b"\x1a"))

    def test_red_requires_two_color_media_and_printer(self):
        settings = {"model": "QL-820NWB", "label": "62", "font_size": 36, "font_name": "DejaVuSans", "color": "red", "align": "center", "bold": False, "cut": True}
        image = render_text("ROT", settings)
        with self.assertRaises(PrinterError):
            build_raster(image, settings)
        settings["label"] = "62red"
        image = render_text("ROT", settings)
        payload = build_raster(image, settings)
        self.assertIn(b"w\x01", payload)
        self.assertIn(b"w\x02", payload)

    def test_bad_usb_path_is_rejected(self):
        with self.assertRaises(PrinterError):
            send("usb:///tmp/not-a-printer", b"test")

    def test_private_tcp_transport_calls_socket(self):
        fake_info = [(2, 1, 6, "", ("192.168.1.25", 9100))]
        fake_socket = unittest.mock.MagicMock()
        fake_socket.__enter__.return_value = fake_socket
        with patch("app.library.printer.socket.getaddrinfo", return_value=fake_info), patch("app.library.printer.socket.socket", return_value=fake_socket):
            send("tcp://printer.local:9100", b"abc")
        fake_socket.connect.assert_called_once_with(("192.168.1.25", 9100))
        fake_socket.sendall.assert_called_once_with(b"abc")

    def test_public_tcp_address_is_rejected(self):
        fake_info = [(2, 1, 6, "", ("8.8.8.8", 9100))]
        with patch("app.library.printer.socket.getaddrinfo", return_value=fake_info):
            with self.assertRaises(PrinterError):
                send("tcp://example.test:9100", b"abc")


if __name__ == "__main__":
    unittest.main()
