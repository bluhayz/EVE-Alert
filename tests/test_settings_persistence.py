"""Regression tests for settings persistence (issues #99 / #108).

These target the pure data-merge logic in SettingsStore.  After Phase 7 there
is no SettingMenu; all logic lives in SettingsStore._merge and SettingsStore.load.
"""

import json
import tempfile
import unittest
from pathlib import Path

from evealert.settings.store import DEFAULT_SETTINGS, SettingsStore, reset_settings_store


class MergeSettingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._path = Path(self._tmp) / "settings.json"
        self._path.write_text("{}", encoding="utf-8")
        self._store = reset_settings_store(self._path)

    def _merge(self, user: dict) -> dict:
        self._path.write_text(json.dumps(user), encoding="utf-8")
        return self._store.load()

    def test_merge_preserves_saved_profiles(self):
        merged = self._merge({
            "active_profile": "Nullsec",
            "profiles": {"Nullsec": {"log_level": "DEBUG"}},
        })
        self.assertEqual(merged["profiles"], {"Nullsec": {"log_level": "DEBUG"}})
        self.assertEqual(merged["active_profile"], "Nullsec")

    def test_merge_preserves_image_thresholds(self):
        merged = self._merge({"image_thresholds": {"image_enemy.png": 0.85}})
        self.assertEqual(merged["image_thresholds"], {"image_enemy.png": 0.85})

    def test_merge_preserves_user_only_list_values(self):
        merged = self._merge({"kos_list": ["BadGuy Corp", "Hostile Alliance"]})
        self.assertEqual(merged["kos_list"], ["BadGuy Corp", "Hostile Alliance"])

    def test_merge_fills_missing_default_blocks(self):
        merged = self._merge({})
        self.assertIn("dscan", merged)
        self.assertEqual(merged["dscan"], DEFAULT_SETTINGS["dscan"])

    def test_merge_does_not_mutate_defaults(self):
        merged = self._merge({})
        merged["dscan"]["enabled"] = True
        self.assertFalse(DEFAULT_SETTINGS["dscan"]["enabled"])

    def test_merge_overlays_scalar_user_values(self):
        merged = self._merge({"log_level": "DEBUG"})
        self.assertEqual(merged["log_level"], "DEBUG")


class LoadPreservesNonWidgetKeysTests(unittest.TestCase):
    """SettingsStore.load() must preserve non-UI keys (profiles, thresholds, etc.)."""

    def test_load_preserves_non_widget_keys(self):
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
            store = reset_settings_store(path)
            result = store.load()
        self.assertEqual(result["profiles"], {"Home": {"log_level": "INFO"}})
        self.assertEqual(result["image_thresholds"], {"image_enemy.png": 0.9})
        self.assertEqual(result["active_profile"], "Home")
        self.assertEqual(result["kos_list"], ["Enemy Inc"])

    def test_missing_file_returns_defaults_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "does_not_exist.json"
            store = reset_settings_store(path)
            result = store.load()
        result["log_level"] = "MUTATED"
        self.assertNotEqual(DEFAULT_SETTINGS["log_level"], "MUTATED")


if __name__ == "__main__":
    unittest.main()
