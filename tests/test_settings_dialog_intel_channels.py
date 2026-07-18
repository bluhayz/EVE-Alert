"""Tests for the #191 intel-channel discovery UI in SettingsDialog.

Uses the offscreen Qt platform so no display is needed in CI.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_dialog(store):
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    from evealert.ui.settings_dialog import SettingsDialog  # noqa: PLC0415

    return SettingsDialog(None, store)


class IntelChannelUiTestCase(unittest.TestCase):
    def setUp(self):
        from evealert.settings.store import SettingsStore  # noqa: PLC0415

        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        with open(self.settings_path, "w") as f:
            json.dump({}, f)
        self.store = SettingsStore(self.settings_path)
        self.dialog = _make_dialog(self.store)
        # QWidget.isVisible() reflects the whole ancestor chain, not just
        # this widget's own setVisible() state: it also requires (a) the
        # top-level window to be shown and (b) this widget's tab page to
        # be the QTabWidget's CURRENT tab (a non-current tab's contents
        # report isVisible() == False regardless of setVisible()).
        self.dialog.show()
        for i in range(self.dialog._tabs.count()):
            if self.dialog._tabs.tabText(i) == "Intel & ESI":
                self.dialog._tabs.setCurrentIndex(i)
                break

    def tearDown(self):
        import shutil

        self.dialog.deleteLater()
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class ScanForChannelsTests(IntelChannelUiTestCase):
    def test_scan_populates_list_from_discovered_channels(self):
        with tempfile.TemporaryDirectory() as chatlog_dir:
            d = Path(chatlog_dir)
            (d / "Intel_20240501_090000.txt").write_text("x")
            (d / "Alliance_20240501_090000.txt").write_text("x")

            self.dialog._intel_log_dir_entry.setText(chatlog_dir)
            self.dialog._scan_for_intel_channels()

        names = [
            self.dialog._intel_channels_list.item(i).text()
            for i in range(self.dialog._intel_channels_list.count())
        ]
        self.assertEqual(sorted(names), ["Alliance", "Intel"])
        self.assertFalse(self.dialog._intel_channels_empty_label.isVisible())

    def test_scan_shows_friendly_message_for_empty_directory(self):
        with tempfile.TemporaryDirectory() as chatlog_dir:
            self.dialog._intel_log_dir_entry.setText(chatlog_dir)
            self.dialog._scan_for_intel_channels()

        self.assertEqual(self.dialog._intel_channels_list.count(), 0)
        self.assertTrue(self.dialog._intel_channels_empty_label.isVisible())

    def test_scan_preserves_prior_checked_selection(self):
        with tempfile.TemporaryDirectory() as chatlog_dir:
            d = Path(chatlog_dir)
            (d / "Intel_20240501_090000.txt").write_text("x")
            (d / "Alliance_20240501_090000.txt").write_text("x")
            self.dialog._intel_log_dir_entry.setText(chatlog_dir)

            self.dialog._scan_for_intel_channels()
            # Check "Intel" only.
            for i in range(self.dialog._intel_channels_list.count()):
                item = self.dialog._intel_channels_list.item(i)
                from PySide6.QtCore import Qt  # noqa: PLC0415

                item.setCheckState(
                    Qt.CheckState.Checked
                    if item.text() == "Intel"
                    else Qt.CheckState.Unchecked
                )

            # A new channel appears; re-scan must keep "Intel" checked.
            (d / "NC-INT_20240501_090000.txt").write_text("x")
            self.dialog._scan_for_intel_channels()

        self.assertEqual(self.dialog._checked_intel_channels(), {"Intel"})

    def test_scan_with_no_directory_configured_and_no_auto_detect_shows_message(self):
        from unittest.mock import patch

        self.dialog._intel_log_dir_entry.setText("")
        with patch(
            "evealert.tools.intel_watcher.get_eve_chatlog_dir", return_value=None
        ):
            self.dialog._scan_for_intel_channels()

        self.assertTrue(self.dialog._intel_channels_empty_label.isVisible())


class ManualAddChannelTests(IntelChannelUiTestCase):
    def test_add_manual_channel_appends_checked_item(self):
        self.dialog._intel_channel_add_entry.setText("NC-INT")
        self.dialog._add_manual_intel_channel()

        self.assertEqual(self.dialog._checked_intel_channels(), {"NC-INT"})
        self.assertEqual(self.dialog._intel_channel_add_entry.text(), "")

    def test_add_manual_channel_ignores_empty_input(self):
        self.dialog._intel_channel_add_entry.setText("   ")
        self.dialog._add_manual_intel_channel()
        self.assertEqual(self.dialog._intel_channels_list.count(), 0)

    def test_add_manual_channel_rechecks_existing_row_instead_of_duplicating(self):
        self.dialog._intel_channel_add_entry.setText("Intel")
        self.dialog._add_manual_intel_channel()
        # Uncheck it, then "add" it again -- should re-check, not duplicate.
        from PySide6.QtCore import Qt  # noqa: PLC0415

        self.dialog._intel_channels_list.item(0).setCheckState(Qt.CheckState.Unchecked)

        self.dialog._intel_channel_add_entry.setText("intel")  # different casing
        self.dialog._add_manual_intel_channel()

        self.assertEqual(self.dialog._intel_channels_list.count(), 1)
        self.assertEqual(self.dialog._checked_intel_channels(), {"Intel"})


class LoadAndCollectRoundTripTests(IntelChannelUiTestCase):
    def test_load_populates_directory_and_checked_channels(self):
        settings = self.store.load()
        settings["intelligence"] = {
            "intel_log_dir": "/my/eve/logs",
            "intel_channels": ["Intel", "Alliance"],
        }
        self.store.save(settings)

        self.dialog.load()

        self.assertEqual(self.dialog._intel_log_dir_entry.text(), "/my/eve/logs")
        self.assertEqual(self.dialog._checked_intel_channels(), {"Intel", "Alliance"})

    def test_load_migrates_legacy_single_channel_as_checked_row(self):
        settings = self.store.load()
        settings["intelligence"] = {"intel_log_channel": "Intel"}
        self.store.save(settings)

        self.dialog.load()

        self.assertEqual(self.dialog._checked_intel_channels(), {"Intel"})

    def test_collect_reflects_directory_and_checked_channels(self):
        self.dialog._intel_log_dir_entry.setText("/custom/dir")
        self.dialog._intel_channel_add_entry.setText("Intel")
        self.dialog._add_manual_intel_channel()
        self.dialog._intel_channel_add_entry.setText("Alliance")
        self.dialog._add_manual_intel_channel()

        patch = self.dialog._collect()

        self.assertEqual(patch["intelligence"]["intel_log_dir"], "/custom/dir")
        self.assertEqual(
            patch["intelligence"]["intel_channels"], ["Alliance", "Intel"]
        )

    def test_collect_excludes_unchecked_channels(self):
        self.dialog._intel_channel_add_entry.setText("Intel")
        self.dialog._add_manual_intel_channel()
        from PySide6.QtCore import Qt  # noqa: PLC0415

        self.dialog._intel_channels_list.item(0).setCheckState(Qt.CheckState.Unchecked)

        patch = self.dialog._collect()

        self.assertEqual(patch["intelligence"]["intel_channels"], [])

    def test_save_and_reload_round_trips_without_disturbing_other_settings(self):
        settings = self.store.load()
        settings["intelligence"]["zkillboard_cooldown"] = 900  # not touched by this UI
        self.store.save(settings)
        self.dialog.load()

        self.dialog._intel_log_dir_entry.setText("/round/trip")
        self.dialog._intel_channel_add_entry.setText("Intel")
        self.dialog._add_manual_intel_channel()
        self.dialog._save_and_apply()

        reloaded = self.store.load()
        self.assertEqual(reloaded["intelligence"]["intel_log_dir"], "/round/trip")
        self.assertEqual(reloaded["intelligence"]["intel_channels"], ["Intel"])
        # Untouched sibling key under the same "intelligence" dict survives
        # the deep-merge save.
        self.assertEqual(reloaded["intelligence"]["zkillboard_cooldown"], 900)


if __name__ == "__main__":
    unittest.main()
