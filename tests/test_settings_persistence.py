"""Regression tests for settings persistence (issues #99 / #108).

These target the pure data-merge and save-base logic that previously wiped
saved profiles, per-image thresholds, active profile, and kos_list on every
save. They avoid constructing a real Tk window by invoking the affected
methods on a lightweight stub that only supplies ``default`` — the merge and
read helpers depend on nothing else.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evealert.menu.setting import DEFAULT_SETTINGS, SettingMenu


class _StubMenu:
    """Minimal stand-in exposing only what the tested methods touch."""

    default = DEFAULT_SETTINGS
    merge_settings_with_defaults = SettingMenu.merge_settings_with_defaults
    _read_saved_settings = SettingMenu._read_saved_settings


class MergeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.menu = _StubMenu()

    def test_merge_preserves_saved_profiles(self):
        """A user 'profiles' dict must survive the merge even though the
        default for 'profiles' is an empty dict."""
        user = {
            "active_profile": "Nullsec",
            "profiles": {"Nullsec": {"log_level": "DEBUG"}},
        }
        merged = self.menu.merge_settings_with_defaults(user)
        self.assertEqual(merged["profiles"], {"Nullsec": {"log_level": "DEBUG"}})
        self.assertEqual(merged["active_profile"], "Nullsec")

    def test_merge_preserves_image_thresholds(self):
        user = {"image_thresholds": {"image_enemy.png": 0.85}}
        merged = self.menu.merge_settings_with_defaults(user)
        self.assertEqual(merged["image_thresholds"], {"image_enemy.png": 0.85})

    def test_merge_preserves_user_only_list_values(self):
        user = {"kos_list": ["BadGuy Corp", "Hostile Alliance"]}
        merged = self.menu.merge_settings_with_defaults(user)
        self.assertEqual(merged["kos_list"], ["BadGuy Corp", "Hostile Alliance"])

    def test_merge_fills_missing_default_blocks(self):
        """A brand-new file missing a feature block still gets the default."""
        merged = self.menu.merge_settings_with_defaults({})
        self.assertIn("dscan", merged)
        self.assertEqual(merged["dscan"], DEFAULT_SETTINGS["dscan"])

    def test_merge_does_not_mutate_defaults(self):
        merged = self.menu.merge_settings_with_defaults({})
        merged["dscan"]["enabled"] = True
        self.assertFalse(DEFAULT_SETTINGS["dscan"]["enabled"])

    def test_merge_overlays_scalar_user_values(self):
        merged = self.menu.merge_settings_with_defaults({"log_level": "DEBUG"})
        self.assertEqual(merged["log_level"], "DEBUG")


class ReadSavedSettingsTests(unittest.TestCase):
    """save() now starts from _read_saved_settings() so non-widget keys are
    preserved. Verify a round-trip through the on-disk file keeps profiles."""

    def test_read_preserves_non_widget_keys(self):
        menu = _StubMenu()
        payload = {
            "log_level": "INFO",
            "active_profile": "Home",
            "profiles": {"Home": {"log_level": "INFO"}},
            "image_thresholds": {"image_enemy.png": 0.9},
            "kos_list": ["Enemy Inc"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch(
                "evealert.menu.setting.get_settings_path", return_value=str(path)
            ):
                base = menu._read_saved_settings()
        # These are exactly the keys that save() used to wipe.
        self.assertEqual(base["profiles"], {"Home": {"log_level": "INFO"}})
        self.assertEqual(base["image_thresholds"], {"image_enemy.png": 0.9})
        self.assertEqual(base["active_profile"], "Home")
        self.assertEqual(base["kos_list"], ["Enemy Inc"])

    def test_read_missing_file_returns_defaults_copy(self):
        menu = _StubMenu()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "does_not_exist.json"
            with mock.patch(
                "evealert.menu.setting.get_settings_path", return_value=str(path)
            ):
                base = menu._read_saved_settings()
        # Should be a defaults copy, and mutating it must not touch DEFAULT_SETTINGS.
        base["log_level"] = "MUTATED"
        self.assertNotEqual(DEFAULT_SETTINGS["log_level"], "MUTATED")


if __name__ == "__main__":
    unittest.main()
