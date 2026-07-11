"""Tests for evealert.hotkeys — hotkey parsing and matching helpers."""

import unittest

from pynput.keyboard import Key

from evealert.hotkeys import DEFAULT_HOTKEYS, key_matches, parse_hotkey


class TestParseHotkey(unittest.TestCase):
    def test_f_keys(self):
        self.assertEqual(parse_hotkey("f1"), Key.f1)
        self.assertEqual(parse_hotkey("f2"), Key.f2)
        self.assertEqual(parse_hotkey("f12"), Key.f12)

    def test_named_keys(self):
        from pynput.keyboard import Key

        # Only test keys that are guaranteed to exist on all platforms
        self.assertEqual(parse_hotkey("esc"), Key.esc)
        self.assertEqual(parse_hotkey("space"), Key.space)
        self.assertEqual(parse_hotkey("tab"), Key.tab)
        self.assertEqual(parse_hotkey("enter"), Key.enter)

    def test_single_char_keys(self):
        # Single printable characters map to pynput KeyCode via char attribute
        result = parse_hotkey("a")
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "char", None), "a")

    def test_case_insensitive(self):
        self.assertEqual(parse_hotkey("F1"), parse_hotkey("f1"))
        self.assertEqual(parse_hotkey("ESC"), parse_hotkey("esc"))

    def test_unknown_key_returns_none(self):
        self.assertIsNone(parse_hotkey("notakey_xyz"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_hotkey(""))


class TestKeyMatches(unittest.TestCase):
    def test_matches_f_key(self):
        self.assertTrue(key_matches(Key.f1, "f1"))
        self.assertTrue(key_matches(Key.f2, "f2"))

    def test_no_match_wrong_key(self):
        self.assertFalse(key_matches(Key.f1, "f2"))
        self.assertFalse(key_matches(Key.esc, "f1"))

    def test_matches_named_key(self):
        self.assertTrue(key_matches(Key.esc, "esc"))

    def test_no_match_unknown(self):
        self.assertFalse(key_matches(Key.f1, "notakey_xyz"))


class TestDefaultHotkeys(unittest.TestCase):
    def test_has_required_keys(self):
        self.assertIn("alert_region", DEFAULT_HOTKEYS)
        self.assertIn("faction_region", DEFAULT_HOTKEYS)

    def test_default_values(self):
        self.assertEqual(DEFAULT_HOTKEYS["alert_region"], "f1")
        self.assertEqual(DEFAULT_HOTKEYS["faction_region"], "f2")


if __name__ == "__main__":
    unittest.main()
