import unittest

from app.documents_routes_content import _single_byte_range


class V2VideoRangePreviewTests(unittest.TestCase):
    def test_parses_bounded_open_and_suffix_ranges(self):
        self.assertEqual((10, 19), _single_byte_range("bytes=10-19", 100))
        self.assertEqual((10, 99), _single_byte_range("bytes=10-", 100))
        self.assertEqual((90, 99), _single_byte_range("bytes=-10", 100))
        self.assertEqual((90, 99), _single_byte_range("bytes=90-120", 100))

    def test_empty_header_means_full_response(self):
        self.assertIsNone(_single_byte_range("", 100))

    def test_rejects_invalid_or_multiple_ranges(self):
        for value in ("items=0-1", "bytes=0-1,4-5", "bytes=100-", "bytes=20-10", "bytes=-0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _single_byte_range(value, 100)

    def test_range_on_empty_object_is_unsatisfiable(self):
        with self.assertRaises(ValueError):
            _single_byte_range("bytes=0-", 0)


if __name__ == "__main__":
    unittest.main()
