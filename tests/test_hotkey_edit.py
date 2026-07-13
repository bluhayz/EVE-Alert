"""Tests for evealert.ui.hotkey_edit — Qt key → pynput string conversion (#165)."""

import unittest

from evealert.ui.hotkey_edit import qt_key_to_pynput

try:
    from PySide6.QtCore import Qt
    _HAS_QT = True
except Exception:
    _HAS_QT = False


@unittest.skipUnless(_HAS_QT, "PySide6 not available")
class QtKeyConversionTests(unittest.TestCase):
    def test_f1_through_f12(self):
        for i in range(1, 13):
            key = getattr(Qt.Key, f"Key_F{i}")
            result = qt_key_to_pynput(key.value)
            self.assertEqual(result, f"f{i}", f"F{i} failed")

    def test_letter_keys(self):
        for c in "abcdefghijklmnopqrstuvwxyz":
            key = getattr(Qt.Key, f"Key_{c.upper()}")
            result = qt_key_to_pynput(key.value)
            self.assertEqual(result, c)

    def test_escape_key(self):
        self.assertEqual(qt_key_to_pynput(Qt.Key.Key_Escape.value), "esc")

    def test_enter_key(self):
        self.assertEqual(qt_key_to_pynput(Qt.Key.Key_Return.value), "enter")

    def test_space_key(self):
        self.assertEqual(qt_key_to_pynput(Qt.Key.Key_Space.value), "space")

    def test_unknown_key_returns_none(self):
        # Key_Control is a modifier — should not have a pynput string
        self.assertIsNone(qt_key_to_pynput(Qt.Key.Key_Control.value))

    def test_arrow_keys(self):
        self.assertEqual(qt_key_to_pynput(Qt.Key.Key_Up.value),    "up")
        self.assertEqual(qt_key_to_pynput(Qt.Key.Key_Down.value),  "down")
        self.assertEqual(qt_key_to_pynput(Qt.Key.Key_Left.value),  "left")
        self.assertEqual(qt_key_to_pynput(Qt.Key.Key_Right.value), "right")


if __name__ == "__main__":
    unittest.main()
