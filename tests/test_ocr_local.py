"""Tests for OCR pilot-name detection (issue #98).

All tests mock winsdk / pytesseract / mss — no real Tesseract engine or
Windows Runtime required.
"""

import sys
import types
import unittest
from unittest import mock

from evealert.tools import ocr_local
from evealert.tools.ocr_local import (
    is_ocr_available,
    is_tesseract_available,
    is_winrt_ocr_available,
    parse_eve_names,
    read_local_names,
    reset_availability_cache,
    resolve_region,
)


# ---------------------------------------------------------------------------
# parse_eve_names — backend-independent
# ---------------------------------------------------------------------------

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
        self.assertEqual(parse_eve_names(text), ["Bob McTest", "Spaced Out"])

    def test_empty_input(self):
        self.assertEqual(parse_eve_names(""), [])
        self.assertEqual(parse_eve_names(None), [])

    def test_rejects_too_short(self):
        self.assertEqual(parse_eve_names("ab\nX\n"), [])

    def test_strips_eve_standing_icons_before_name(self):
        """EVE Local list prefixes names with standing icons (■ ★ etc.)
        that OCR picks up as non-alphanumeric leading chars — they must be
        stripped so the name is still detected."""
        # ■ Anulos (hostile standing icon)
        self.assertIn("Anulos", parse_eve_names("\u25a0 Anulos\n"))
        # ★ AquaHades Mono (friendly / fleet standing icon)
        self.assertIn("AquaHades Mono", parse_eve_names("\u2605 AquaHades Mono\n"))
        # * bluhayz (corp member asterisk)
        self.assertIn("bluhayz", parse_eve_names("* bluhayz\n"))

    def test_strips_icon_but_keeps_full_name(self):
        """Multi-word name after icon strip is preserved intact."""
        text = "\u25a0 Ilex Calix Invicta\n\u2605 Lycan Hunter Wolf\n"
        names = parse_eve_names(text)
        self.assertIn("Ilex Calix Invicta", names)
        self.assertIn("Lycan Hunter Wolf", names)

    def test_pure_icon_line_is_dropped(self):
        """A line that is nothing but icon characters after stripping is dropped."""
        self.assertEqual(parse_eve_names("\u25a0\u2605\u2b50\n"), [])


# ---------------------------------------------------------------------------
# resolve_region
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------

class WinRTAvailabilityTests(unittest.TestCase):
    def setUp(self):
        reset_availability_cache()

    def tearDown(self):
        reset_availability_cache()

    def test_winrt_unavailable_on_non_windows(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(is_winrt_ocr_available())

    def test_winrt_unavailable_when_winsdk_missing(self):
        # Make the nested imports fail for BOTH package families (winsdk and
        # its winrt-* successor — ocr_local falls back between them, #199).
        blocked = {
            name: None
            for name in (
                "winsdk", "winsdk.windows", "winsdk.windows.media",
                "winsdk.windows.media.ocr", "winsdk.windows.graphics",
                "winsdk.windows.graphics.imaging", "winsdk.windows.storage",
                "winsdk.windows.storage.streams",
                "winrt", "winrt.windows", "winrt.windows.media",
                "winrt.windows.media.ocr", "winrt.windows.graphics",
                "winrt.windows.graphics.imaging", "winrt.windows.storage",
                "winrt.windows.storage.streams",
            )
        }
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, blocked):
                self.assertFalse(is_winrt_ocr_available())

    def test_winrt_available_when_engine_creates(self):
        fake_engine = object()
        fake_ocr_mod = types.ModuleType("winsdk.windows.media.ocr")
        fake_ocr_mod.OcrEngine = mock.MagicMock(
            try_create_from_user_profile_languages=mock.MagicMock(return_value=fake_engine)
        )
        # Python's import machinery requires parent packages to be in sys.modules.
        fake_winsdk = types.ModuleType("winsdk")
        fake_winsdk_windows = types.ModuleType("winsdk.windows")
        fake_winsdk_windows_media = types.ModuleType("winsdk.windows.media")
        mod_patch = {
            "winsdk": fake_winsdk,
            "winsdk.windows": fake_winsdk_windows,
            "winsdk.windows.media": fake_winsdk_windows_media,
            "winsdk.windows.media.ocr": fake_ocr_mod,
        }
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, mod_patch):
                self.assertTrue(is_winrt_ocr_available())

    def test_winrt_unavailable_when_engine_returns_none(self):
        fake_ocr_mod = types.ModuleType("winsdk.windows.media.ocr")
        fake_ocr_mod.OcrEngine = mock.MagicMock(
            try_create_from_user_profile_languages=mock.MagicMock(return_value=None)
        )
        # _import_winrt_modules imports all three winsdk modules; fake each so
        # the winsdk path succeeds and the None-engine branch is exercised
        # (otherwise the import falls through to a real winrt install).
        mod_patch = {
            "winsdk": types.ModuleType("winsdk"),
            "winsdk.windows": types.ModuleType("winsdk.windows"),
            "winsdk.windows.media": types.ModuleType("winsdk.windows.media"),
            "winsdk.windows.media.ocr": fake_ocr_mod,
            "winsdk.windows.graphics": types.ModuleType("winsdk.windows.graphics"),
            "winsdk.windows.graphics.imaging": types.ModuleType(
                "winsdk.windows.graphics.imaging"
            ),
            "winsdk.windows.storage": types.ModuleType("winsdk.windows.storage"),
            "winsdk.windows.storage.streams": types.ModuleType(
                "winsdk.windows.storage.streams"
            ),
        }
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, mod_patch):
                self.assertFalse(is_winrt_ocr_available())


class TesseractAvailabilityTests(unittest.TestCase):
    def setUp(self):
        reset_availability_cache()

    def tearDown(self):
        reset_availability_cache()

    def test_unavailable_when_import_fails(self):
        with mock.patch.dict(sys.modules, {"pytesseract": None}):
            self.assertFalse(is_tesseract_available())

    def test_available_when_binary_present(self):
        fake = types.ModuleType("pytesseract")
        fake.get_tesseract_version = lambda: "5.3.0"
        with mock.patch.dict(sys.modules, {"pytesseract": fake}):
            self.assertTrue(is_tesseract_available())

    def test_unavailable_when_binary_missing(self):
        fake = types.ModuleType("pytesseract")

        def _raise():
            raise RuntimeError("tesseract is not installed")

        fake.get_tesseract_version = _raise
        with mock.patch.dict(sys.modules, {"pytesseract": fake}):
            self.assertFalse(is_tesseract_available())


class OverallAvailabilityTests(unittest.TestCase):
    def setUp(self):
        reset_availability_cache()

    def tearDown(self):
        reset_availability_cache()

    def test_available_when_winrt_present(self):
        with mock.patch.object(ocr_local, "is_winrt_ocr_available", return_value=True):
            with mock.patch.object(ocr_local, "is_tesseract_available", return_value=False):
                self.assertTrue(is_ocr_available())

    def test_available_when_only_tesseract_present(self):
        with mock.patch.object(ocr_local, "is_winrt_ocr_available", return_value=False):
            with mock.patch.object(ocr_local, "is_tesseract_available", return_value=True):
                self.assertTrue(is_ocr_available())

    def test_unavailable_when_both_absent(self):
        with mock.patch.object(ocr_local, "is_winrt_ocr_available", return_value=False):
            with mock.patch.object(ocr_local, "is_tesseract_available", return_value=False):
                self.assertFalse(is_ocr_available())


# ---------------------------------------------------------------------------
# read_local_names
# ---------------------------------------------------------------------------

class ReadLocalNamesTests(unittest.TestCase):
    def setUp(self):
        reset_availability_cache()

    def tearDown(self):
        reset_availability_cache()

    def test_noop_when_unavailable(self):
        with mock.patch.object(ocr_local, "is_ocr_available", return_value=False):
            self.assertEqual(read_local_names((0, 0, 100, 100)), [])

    def test_uses_winrt_when_available(self):
        """WinRT backend is tried first when available."""
        fake_shot = mock.MagicMock(size=(10, 10), rgb=b"\x00" * 300)
        fake_img = mock.MagicMock()

        with mock.patch.object(ocr_local, "is_ocr_available", return_value=True), \
             mock.patch.object(ocr_local, "is_winrt_ocr_available", return_value=True), \
             mock.patch.object(ocr_local, "is_tesseract_available", return_value=False), \
             mock.patch("mss.mss") as mock_mss, \
             mock.patch("PIL.Image.frombytes", return_value=fake_img), \
             mock.patch.object(ocr_local, "_ocr_with_winrt", return_value="Bob McTest\n") as mock_winrt:
            mock_mss.return_value.__enter__.return_value.grab.return_value = fake_shot
            result = read_local_names((0, 0, 100, 100))
        mock_winrt.assert_called_once_with(fake_img)
        self.assertEqual(result, ["Bob McTest"])

    def test_falls_back_to_tesseract_when_winrt_fails(self):
        """If WinRT OCR raises, pytesseract is used as fallback."""
        fake_shot = mock.MagicMock(size=(10, 10), rgb=b"\x00" * 300)
        fake_img = mock.MagicMock()

        with mock.patch.object(ocr_local, "is_ocr_available", return_value=True), \
             mock.patch.object(ocr_local, "is_winrt_ocr_available", return_value=True), \
             mock.patch.object(ocr_local, "is_tesseract_available", return_value=True), \
             mock.patch("mss.mss") as mock_mss, \
             mock.patch("PIL.Image.frombytes", return_value=fake_img), \
             mock.patch.object(ocr_local, "_ocr_with_winrt", side_effect=RuntimeError("fail")), \
             mock.patch.object(ocr_local, "_ocr_with_tesseract", return_value="Jane Smith\n") as mock_tess:
            mock_mss.return_value.__enter__.return_value.grab.return_value = fake_shot
            result = read_local_names((0, 0, 100, 100))
        mock_tess.assert_called_once_with(fake_img)
        self.assertEqual(result, ["Jane Smith"])

    def test_uses_tesseract_when_winrt_absent(self):
        """When WinRT is not available but Tesseract is, use Tesseract."""
        fake_shot = mock.MagicMock(size=(10, 10), rgb=b"\x00" * 300)
        fake_img = mock.MagicMock()

        with mock.patch.object(ocr_local, "is_ocr_available", return_value=True), \
             mock.patch.object(ocr_local, "is_winrt_ocr_available", return_value=False), \
             mock.patch.object(ocr_local, "is_tesseract_available", return_value=True), \
             mock.patch("mss.mss") as mock_mss, \
             mock.patch("PIL.Image.frombytes", return_value=fake_img), \
             mock.patch.object(ocr_local, "_ocr_with_tesseract", return_value="Capsuleer One\n") as mock_tess:
            mock_mss.return_value.__enter__.return_value.grab.return_value = fake_shot
            result = read_local_names((0, 0, 100, 100))
        mock_tess.assert_called_once_with(fake_img)
        self.assertEqual(result, ["Capsuleer One"])

    def test_returns_empty_on_capture_failure(self):
        with mock.patch.object(ocr_local, "is_ocr_available", return_value=True), \
             mock.patch("mss.mss", side_effect=RuntimeError("no display")):
            self.assertEqual(read_local_names((0, 0, 100, 100)), [])


# ---------------------------------------------------------------------------
# #199 regression tests — WinRT line structure, icon glyphs, preprocessing
# ---------------------------------------------------------------------------

class WinrtLineStructureTests(unittest.TestCase):
    """Regression for the #199 root cause: OcrResult.text flattens all lines
    into one space-joined string, so parse_eve_names (which splits on
    newlines) saw a single >37-char token and returned [] for every capture.
    _winrt_recognize_async must build its output from result.lines."""

    def test_recognize_joins_lines_never_uses_flat_text(self):
        import asyncio
        from types import SimpleNamespace

        from PIL import Image

        fake_result = SimpleNamespace(
            # The flattened text (what OcrResult.text returns) — one line.
            text="1DuMBasS1 AschRafie Bronwen Morgan",
            lines=[
                SimpleNamespace(text="1DuMBasS1"),
                SimpleNamespace(text="AschRafie"),
                SimpleNamespace(text="Bronwen Morgan"),
            ],
        )
        engine = mock.MagicMock()
        engine.recognize_async = mock.AsyncMock(return_value=fake_result)
        wmo = mock.MagicMock()
        wmo.OcrEngine.try_create_from_user_profile_languages.return_value = engine

        decoder = mock.MagicMock()
        decoder.get_software_bitmap_async = mock.AsyncMock(return_value=object())
        wgi = mock.MagicMock()
        wgi.BitmapDecoder.create_async = mock.AsyncMock(return_value=decoder)

        writer = mock.MagicMock()
        writer.store_async = mock.AsyncMock()
        wss = mock.MagicMock()
        wss.DataWriter.return_value = writer

        with mock.patch.object(
            ocr_local, "_import_winrt_modules", return_value=(wgi, wmo, wss)
        ):
            text = asyncio.run(
                ocr_local._winrt_recognize_async(Image.new("RGB", (4, 4)))
            )

        self.assertEqual(text, "1DuMBasS1\nAschRafie\nBronwen Morgan")
        # And the parser must now extract every pilot.
        self.assertEqual(
            parse_eve_names(text), ["1DuMBasS1", "AschRafie", "Bronwen Morgan"]
        )


class IconGlyphTokenTests(unittest.TestCase):
    """Standing icons frequently OCR as short LETTER tokens ('S Naveia'),
    which the non-alphanumeric strip cannot remove (#199)."""

    def test_short_letter_token_stripped_candidate_first(self):
        names = parse_eve_names("S Naveia\nCS Bronwen Morgan")
        self.assertIn("Naveia", names)
        self.assertIn("Bronwen Morgan", names)
        # Stripped candidate ranks before the full-line fallback.
        self.assertLess(names.index("Naveia"), names.index("S Naveia"))

    def test_legitimate_short_first_name_keeps_both_candidates(self):
        names = parse_eve_names("Al Capone")
        # Ambiguous — both candidates emitted; ESI exact-match resolves it.
        self.assertIn("Capone", names)
        self.assertIn("Al Capone", names)

    def test_long_first_token_untouched(self):
        self.assertEqual(parse_eve_names("Bronwen Morgan"), ["Bronwen Morgan"])


class PreprocessTests(unittest.TestCase):
    def test_preprocess_is_3x_rgba(self):
        from PIL import Image

        proc = ocr_local._preprocess_for_ocr(Image.new("RGB", (10, 20)))
        self.assertEqual(proc.mode, "RGBA")
        self.assertEqual(proc.size, (30, 60))


class WinrtImportFallbackTests(unittest.TestCase):
    def setUp(self):
        reset_availability_cache()

    def tearDown(self):
        reset_availability_cache()

    def test_falls_back_to_winrt_namespace_packages(self):
        """When winsdk is absent, the winrt-* successor packages are used."""
        fake_engine = object()
        fake_ocr = types.ModuleType("winrt.windows.media.ocr")
        fake_ocr.OcrEngine = mock.MagicMock(
            try_create_from_user_profile_languages=mock.MagicMock(
                return_value=fake_engine
            )
        )
        fake_imaging = types.ModuleType("winrt.windows.graphics.imaging")
        fake_streams = types.ModuleType("winrt.windows.storage.streams")
        mod_patch = {
            # winsdk family absent
            "winsdk": None,
            "winsdk.windows": None,
            "winsdk.windows.media": None,
            "winsdk.windows.media.ocr": None,
            "winsdk.windows.graphics": None,
            "winsdk.windows.graphics.imaging": None,
            "winsdk.windows.storage": None,
            "winsdk.windows.storage.streams": None,
            # winrt family present
            "winrt": types.ModuleType("winrt"),
            "winrt.windows": types.ModuleType("winrt.windows"),
            "winrt.windows.media": types.ModuleType("winrt.windows.media"),
            "winrt.windows.media.ocr": fake_ocr,
            "winrt.windows.graphics": types.ModuleType("winrt.windows.graphics"),
            "winrt.windows.graphics.imaging": fake_imaging,
            "winrt.windows.storage": types.ModuleType("winrt.windows.storage"),
            "winrt.windows.storage.streams": fake_streams,
        }
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, mod_patch):
                self.assertTrue(is_winrt_ocr_available())


if __name__ == "__main__":
    unittest.main()
