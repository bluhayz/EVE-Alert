"""Tests for the #240 Watchlist Manager dialog.

Uses the offscreen Qt platform so no display is needed in CI.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_dialog(store):
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    from evealert.ui.watchlist_manager import WatchlistManagerDialog  # noqa: PLC0415

    return WatchlistManagerDialog(None, store)


class WatchlistManagerTestCase(unittest.TestCase):
    def setUp(self):
        from evealert.settings.store import SettingsStore  # noqa: PLC0415

        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        with open(self.settings_path, "w") as f:
            json.dump({}, f)
        self.store = SettingsStore(self.settings_path)
        self.dialog = _make_dialog(self.store)

    def tearDown(self):
        import shutil

        self.dialog.deleteLater()
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class LoadTests(WatchlistManagerTestCase):
    def test_load_populates_lists_from_settings(self):
        settings = self.store.load()
        settings["watchlist"] = {
            "pilots": ["Bad Guy"], "corporations": ["Evil Corp"],
            "alliances": ["Evil Alliance"],
        }
        self.store.save(settings)

        self.dialog._load()

        self.assertEqual(self.dialog._collect(), {
            "pilots": ["Bad Guy"], "corporations": ["Evil Corp"],
            "alliances": ["Evil Alliance"],
        })

    def test_load_with_no_watchlist_block_leaves_lists_empty(self):
        self.dialog._load()
        self.assertEqual(
            self.dialog._collect(),
            {"pilots": [], "corporations": [], "alliances": []},
        )


class AddRemoveTests(WatchlistManagerTestCase):
    def test_add_entry_appends_to_the_right_list(self):
        self.dialog._entries["pilots"].setText("Bad Guy")
        self.dialog._add_entry("pilots")

        self.assertEqual(self.dialog._collect()["pilots"], ["Bad Guy"])
        self.assertEqual(self.dialog._collect()["corporations"], [])
        self.assertEqual(self.dialog._entries["pilots"].text(), "")  # cleared after add

    def test_add_ignores_empty_name(self):
        self.dialog._entries["pilots"].setText("   ")
        self.dialog._add_entry("pilots")
        self.assertEqual(self.dialog._lists["pilots"].count(), 0)

    def test_add_does_not_duplicate_case_insensitive(self):
        self.dialog._entries["pilots"].setText("Bad Guy")
        self.dialog._add_entry("pilots")
        self.dialog._entries["pilots"].setText("bad guy")
        self.dialog._add_entry("pilots")

        self.assertEqual(self.dialog._lists["pilots"].count(), 1)

    def test_remove_selected_removes_only_selected_item(self):
        self.dialog._entries["pilots"].setText("Bad Guy")
        self.dialog._add_entry("pilots")
        self.dialog._entries["pilots"].setText("Other Guy")
        self.dialog._add_entry("pilots")

        list_widget = self.dialog._lists["pilots"]
        list_widget.item(0).setSelected(True)  # select "Bad Guy"
        self.dialog._remove_selected("pilots")

        self.assertEqual(self.dialog._collect()["pilots"], ["Other Guy"])

    def test_remove_with_nothing_selected_is_a_no_op(self):
        self.dialog._entries["pilots"].setText("Bad Guy")
        self.dialog._add_entry("pilots")
        self.dialog._remove_selected("pilots")
        self.assertEqual(self.dialog._collect()["pilots"], ["Bad Guy"])

    def test_the_three_lists_are_independent(self):
        self.dialog._entries["pilots"].setText("Bad Guy")
        self.dialog._add_entry("pilots")
        self.dialog._entries["corporations"].setText("Evil Corp")
        self.dialog._add_entry("corporations")
        self.dialog._entries["alliances"].setText("Evil Alliance")
        self.dialog._add_entry("alliances")

        self.assertEqual(self.dialog._collect(), {
            "pilots": ["Bad Guy"], "corporations": ["Evil Corp"],
            "alliances": ["Evil Alliance"],
        })


class SaveTests(WatchlistManagerTestCase):
    def test_save_writes_watchlist_without_disturbing_other_settings(self):
        settings = self.store.load()
        settings["intelligence"] = {"zkillboard_cooldown": 900}
        self.store.save(settings)
        self.dialog._load()

        self.dialog._entries["pilots"].setText("Bad Guy")
        self.dialog._add_entry("pilots")
        self.dialog._save()

        reloaded = self.store.load()
        self.assertEqual(reloaded["watchlist"]["pilots"], ["Bad Guy"])
        self.assertEqual(reloaded["intelligence"]["zkillboard_cooldown"], 900)

    def test_save_failure_shows_warning_not_raises(self):
        with patch.object(
            self.store, "save", side_effect=OSError("disk full")
        ), patch("evealert.ui.watchlist_manager.QMessageBox.warning") as mock_warn:
            self.dialog._save()  # must not raise
        mock_warn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
