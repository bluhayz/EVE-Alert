"""Tests for OCR pilot-name detection (issue #98).

All tests mock pytesseract/mss — no real Tesseract engine required.
"""

import sys
import types
import unittest
from unittest import mock

from evealert.tools import ocr_local
from evealert.tools.ocr_local import (
    is_ocr_available,
    parse_eve_names,
    read_local_names,
    reset_availability_cache,
    resolve_region,
)


class ParseNamesTests(unittest.TestCase):
    def test_keeps_valid_names(self):
        text = "Bob McTest\nEvil Corp\nJane O'Neil-Smith\n"
        self.assertEqual(
            parse_eve_names(text), ["Bob McTest", "Evil Corp", "Jane O'Neil-Smith"]
        )

    def test_drops_noise_and_pure_numbers(self):
        text = "!!!\n***\n12345\n---\n"
        self.assertEqual(parse_eve_names(text), [])

    def test_collapses_whitespace_and_dedupes(self):
        text = "Bob   McTest\nbob mctest\nSpaced   Out\n"
        # 'bob mctest' is a case-insensitive dupe of the first
        self.assertEqual(parse_eve_names(text), ["Bob McTest", "Spaced Out"])

    def test_empty_input(self):
        self.assertEqual(parse_eve_names(""), [])
        self.assertEqual(parse_eve_names(None), [])

    def test_rejects_too_short(self):
        self.assertEqual(parse_eve_names("ab\nX\n"), [])


class RegionTests(unittest.TestCase):
    def test_override_used_when_nonzero(self):
        self.assertEqual(
            resolve_region((10, 20, 110, 220), (1, 2, 3, 4)), (10, 20, 110, 220)
        )

    def test_falls_back_to_alert_region(self):
        self.assertEqual(resolve_region((0, 0, 0, 0), (5, 5, 50, 60)), (5, 5, 50, 60))

    def test_normalizes_reversed_corners(self):
        self.assertEqual(
            resolve_region((110, 220, 10, 20), (0, 0, 0, 0)), (10, 20, 110, 220)
        )

    def test_degenerate_region_returns_none(self):
        self.assertIsNone(resolve_region((0, 0, 0, 0), (0, 0, 0, 0)))
        self.assertIsNone(resolve_region((5, 5, 5, 5), (0, 0, 0, 0)))


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        reset_availability_cache()

    def tearDown(self):
        reset_availability_cache()

    def test_unavailable_when_import_fails(self):
        # Ensure pytesseract import raises
        with mock.patch.dict(sys.modules, {"pytesseract": None}):
            self.assertFalse(is_ocr_available())

    def test_available_when_binary_present(self):
        fake = types.ModuleType("pytesseract")
        fake.get_tesseract_version = lambda: "5.3.0"
        with mock.patch.dict(sys.modules, {"pytesseract": fake}):
            self.assertTrue(is_ocr_available())

    def test_unavailable_when_binary_missing(self):
        fake = types.ModuleType("pytesseract")

        def _raise():
            raise RuntimeError("tesseract is not installed")

        fake.get_tesseract_version = _raise
        with mock.patch.dict(sys.modules, {"pytesseract": fake}):
            self.assertFalse(is_ocr_available())

    def test_read_local_names_noop_when_unavailable(self):
        reset_availability_cache()
        with mock.patch.object(ocr_local, "is_ocr_available", return_value=False):
            self.assertEqual(read_local_names((0, 0, 100, 100)), [])


if __name__ == "__main__":
    unittest.main()
