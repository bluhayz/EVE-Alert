"""Tests for the #173 Standings Manager dialog.

Uses the offscreen Qt platform so no display is needed in CI.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_dialog(store):
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    from evealert.ui.standings_manager import StandingsManagerDialog  # noqa: PLC0415

    return StandingsManagerDialog(None, store)


class StandingsManagerTestCase(unittest.TestCase):
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


class ManualOverrideTableTests(StandingsManagerTestCase):
    def test_load_populates_table_from_threat_tiers(self):
        settings = self.store.load()
        settings["threat_tiers"] = {"Bad Guy": "red", "Some Corp": "blue"}
        self.store.save(settings)

        self.dialog._load()

        self.assertEqual(
            self.dialog._collect_manual_overrides(),
            {"Bad Guy": "red", "Some Corp": "blue"},
        )

    def test_add_manual_override_appends_row(self):
        self.dialog._add_name_entry.setText("Bad Guy")
        self.dialog._add_tier_combo.setCurrentText("orange")
        self.dialog._add_manual_override()

        self.assertEqual(
            self.dialog._collect_manual_overrides(), {"Bad Guy": "orange"}
        )
        self.assertEqual(self.dialog._add_name_entry.text(), "")

    def test_add_manual_override_ignores_empty_name(self):
        self.dialog._add_name_entry.setText("   ")
        self.dialog._add_manual_override()
        self.assertEqual(self.dialog._manual_table.rowCount(), 0)

    def test_add_existing_name_updates_tier_instead_of_duplicating(self):
        self.dialog._add_name_entry.setText("Bad Guy")
        self.dialog._add_tier_combo.setCurrentText("red")
        self.dialog._add_manual_override()

        self.dialog._add_name_entry.setText("bad guy")  # different casing
        self.dialog._add_tier_combo.setCurrentText("blue")
        self.dialog._add_manual_override()

        self.assertEqual(self.dialog._manual_table.rowCount(), 1)
        self.assertEqual(
            self.dialog._collect_manual_overrides(), {"Bad Guy": "blue"}
        )

    def test_remove_row_via_button(self):
        self.dialog._add_name_entry.setText("Bad Guy")
        self.dialog._add_manual_override()
        remove_btn = self.dialog._manual_table.cellWidget(0, 2)

        self.dialog._remove_manual_row(remove_btn)

        self.assertEqual(self.dialog._manual_table.rowCount(), 0)

    def test_save_writes_threat_tiers_without_disturbing_other_settings(self):
        settings = self.store.load()
        settings["intelligence"] = {"zkillboard_cooldown": 900}
        self.store.save(settings)
        self.dialog._load()

        self.dialog._add_name_entry.setText("Bad Guy")
        self.dialog._add_tier_combo.setCurrentText("blue")
        self.dialog._add_manual_override()
        self.dialog._save()

        reloaded = self.store.load()
        self.assertEqual(reloaded["threat_tiers"], {"Bad Guy": "blue"})
        self.assertEqual(reloaded["intelligence"]["zkillboard_cooldown"], 900)


class ImportExportTests(StandingsManagerTestCase):
    def test_export_then_import_round_trips_identically(self):
        self.dialog._add_name_entry.setText("Bad Guy")
        self.dialog._add_tier_combo.setCurrentText("red")
        self.dialog._add_manual_override()
        self.dialog._add_name_entry.setText("Good Corp")
        self.dialog._add_tier_combo.setCurrentText("blue")
        self.dialog._add_manual_override()
        original = self.dialog._collect_manual_overrides()

        export_path = str(Path(self.temp_dir) / "export.json")
        with patch(
            "evealert.ui.standings_manager.QFileDialog.getSaveFileName",
            return_value=(export_path, ""),
        ):
            self.dialog._export()

        # Wipe the table (simulates a fresh dialog / cleared state).
        self.dialog._manual_table.setRowCount(0)
        self.assertEqual(self.dialog._collect_manual_overrides(), {})

        with patch(
            "evealert.ui.standings_manager.QFileDialog.getOpenFileName",
            return_value=(export_path, ""),
        ):
            self.dialog._import()

        self.assertEqual(self.dialog._collect_manual_overrides(), original)

    def test_export_file_has_versioned_schema(self):
        self.dialog._add_name_entry.setText("Bad Guy")
        self.dialog._add_tier_combo.setCurrentText("red")
        self.dialog._add_manual_override()

        export_path = str(Path(self.temp_dir) / "export.json")
        with patch(
            "evealert.ui.standings_manager.QFileDialog.getSaveFileName",
            return_value=(export_path, ""),
        ):
            self.dialog._export()

        with open(export_path, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["eve_alert_standings"], 1)
        self.assertEqual(payload["entries"], [{"name": "Bad Guy", "tier": "red"}])

    def test_import_rejects_unrecognized_schema_version(self):
        bad_path = Path(self.temp_dir) / "bad.json"
        with open(bad_path, "w") as f:
            json.dump({"eve_alert_standings": 999, "entries": []}, f)

        with patch(
            "evealert.ui.standings_manager.QFileDialog.getOpenFileName",
            return_value=(str(bad_path), ""),
        ), patch("evealert.ui.standings_manager.QMessageBox.warning") as mock_warn:
            self.dialog._import()

        mock_warn.assert_called_once()
        self.assertEqual(self.dialog._manual_table.rowCount(), 0)

    def test_import_skips_entries_with_invalid_tier(self):
        path = Path(self.temp_dir) / "mixed.json"
        with open(path, "w") as f:
            json.dump({
                "eve_alert_standings": 1,
                "entries": [
                    {"name": "Good Guy", "tier": "blue"},
                    {"name": "Bad Entry", "tier": "purple"},
                ],
            }, f)

        with patch(
            "evealert.ui.standings_manager.QFileDialog.getOpenFileName",
            return_value=(str(path), ""),
        ):
            self.dialog._import()

        self.assertEqual(
            self.dialog._collect_manual_overrides(), {"Good Guy": "blue"}
        )

    def test_import_cancelled_dialog_leaves_table_unchanged(self):
        self.dialog._add_name_entry.setText("Bad Guy")
        self.dialog._add_manual_override()

        with patch(
            "evealert.ui.standings_manager.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ):
            self.dialog._import()

        self.assertEqual(self.dialog._manual_table.rowCount(), 1)


class EsiSyncTests(StandingsManagerTestCase):
    def test_sync_now_shows_message_when_not_authenticated(self):
        mock_auth = MagicMock()
        mock_auth.is_authenticated = False
        with patch(
            "evealert.tools.esi_auth.get_esi_auth", return_value=mock_auth
        ):
            self.dialog._sync_now()

        self.assertIn(
            "Not authenticated", self.dialog._sync_status_label.text()
        )
        self.assertIsNone(self.dialog._sync_thread)

    def test_on_sync_done_populates_table_and_status(self):
        rows = [
            {"id": 1, "name": "Good Guy", "type": "character", "standing": 8.0},
            {"id": 2, "name": "Bad Corp", "type": "corporation", "standing": -5.0},
        ]
        self.dialog._on_sync_done(rows, "")

        self.assertEqual(self.dialog._esi_table.rowCount(), 2)
        self.assertIn("2 entries", self.dialog._sync_status_label.text())
        # Sorted by standing ascending -- most hostile first.
        self.assertEqual(self.dialog._esi_table.item(0, 0).text(), "Bad Corp")

    def test_on_sync_done_shows_error_on_failure(self):
        self.dialog._on_sync_done([], "network error")
        self.assertIn("Sync failed", self.dialog._sync_status_label.text())
        self.assertEqual(self.dialog._esi_table.rowCount(), 0)


class StandingsSyncThreadFetchTests(unittest.IsolatedAsyncioTestCase):
    """#173: the background thread's fetch logic (auth + standings +
    name resolution), tested directly without spinning up a real QThread."""

    async def test_fetch_returns_empty_when_not_authenticated(self):
        from evealert.ui.standings_manager import _StandingsSyncThread

        mock_auth = MagicMock()
        mock_auth.is_authenticated = False
        with patch("evealert.tools.esi_auth.get_esi_auth", return_value=mock_auth):
            result = await _StandingsSyncThread()._fetch()
        self.assertEqual(result, [])

    async def test_fetch_resolves_names_for_standings(self):
        from evealert.ui.standings_manager import _StandingsSyncThread

        mock_auth = MagicMock()
        mock_auth.is_authenticated = True
        with patch(
            "evealert.tools.esi_auth.get_esi_auth", return_value=mock_auth
        ), patch(
            "evealert.tools.esi_auth.get_personal_standings",
            new=AsyncMock(
                return_value=[
                    {"from_id": 42, "from_type": "character", "standing": 5.0}
                ]
            ),
        ), patch(
            "evealert.tools.universe.resolve_names",
            new=AsyncMock(return_value={42: "Good Guy"}),
        ):
            result = await _StandingsSyncThread()._fetch()

        self.assertEqual(
            result,
            [{"id": 42, "name": "Good Guy", "type": "character", "standing": 5.0}],
        )

    async def test_fetch_falls_back_to_id_when_name_unresolved(self):
        from evealert.ui.standings_manager import _StandingsSyncThread

        mock_auth = MagicMock()
        mock_auth.is_authenticated = True
        with patch(
            "evealert.tools.esi_auth.get_esi_auth", return_value=mock_auth
        ), patch(
            "evealert.tools.esi_auth.get_personal_standings",
            new=AsyncMock(
                return_value=[
                    {"from_id": 999, "from_type": "character", "standing": 1.0}
                ]
            ),
        ), patch(
            "evealert.tools.universe.resolve_names", new=AsyncMock(return_value={})
        ):
            result = await _StandingsSyncThread()._fetch()

        self.assertEqual(result[0]["name"], "#999")


if __name__ == "__main__":
    unittest.main()
