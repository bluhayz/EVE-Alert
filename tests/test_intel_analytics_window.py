"""Tests for the #244 Intel Analytics window.

Uses the offscreen Qt platform so no display is needed in CI. Signal
handlers (_on_*_ready) are exercised directly with synthetic payloads --
the same "call the slot with a payload" approach statistics_window's
_on_heatmap_ready would use -- plus one end-to-end test that a `_start_*`
call genuinely runs its query on a background thread and the result
arrives back via the Qt signal/slot mechanism.
"""

import csv
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_window():
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    from evealert.ui.intel_analytics_window import IntelAnalyticsWindow  # noqa: PLC0415

    return IntelAnalyticsWindow()


def _pump_until(predicate, timeout=3.0):
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _dossier(**overrides):
    from evealert.tools.pilot_dossier import PilotDossier  # noqa: PLC0415

    defaults = dict(
        pilot_name="Bad Guy",
        top_ships=[("Sabre", 60.0), ("Loki", 40.0)],
        top_hunt_systems=[("Jita", 5)],
        active_hours=[0] * 19 + [3] + [0] * 4,
        prime_window="19:00-22:00",
        avg_gang_size=4.0,
        solo_pct=10.0,
        frequent_fleetmates=[("Wingman", 3)],
        sighting_summary=None,
        pathing=None,
        kill_loss_ratio=2.0,
        last_active=time.time(),
    )
    defaults.update(overrides)
    return PilotDossier(**defaults)


class IntelAnalyticsWindowTestCase(unittest.TestCase):
    def setUp(self):
        self.window = _make_window()

    def tearDown(self):
        self.window.deleteLater()


class SearchReadyTests(IntelAnalyticsWindowTestCase):
    def test_results_populate_list_and_status(self):
        self.window._on_search_ready(["Alice", "Bob"])
        self.assertEqual(self.window._search_results.count(), 2)
        self.assertIn("2 match", self.window._dossier_status.text())

    def test_empty_results_show_guidance(self):
        self.window._on_search_ready([])
        self.assertEqual(self.window._search_results.count(), 0)
        self.assertIn("No matching pilots", self.window._dossier_status.text())

    def test_exception_shows_error(self):
        self.window._on_search_ready(RuntimeError("boom"))
        self.assertIn("Search error", self.window._dossier_status.text())


class DossierReadyTests(IntelAnalyticsWindowTestCase):
    def test_populates_all_tables(self):
        sightings = []
        self.window._on_dossier_ready((_dossier(), sightings))

        self.assertEqual(self.window._ship_table.rowCount(), 2)
        self.assertEqual(self.window._ship_table.item(0, 0).text(), "Sabre")
        self.assertEqual(self.window._ship_table.item(0, 1).text(), "60%")

        self.assertEqual(self.window._hunt_table.rowCount(), 1)
        self.assertEqual(self.window._hunt_table.item(0, 0).text(), "Jita")

        self.assertEqual(self.window._fleetmate_table.rowCount(), 1)
        self.assertEqual(self.window._fleetmate_table.item(0, 0).text(), "Wingman")

        self.assertIs(self.window._current_dossier, self.window._current_dossier)
        self.assertIn("Bad Guy", self.window._dossier_status.text())

    def test_sighting_timeline_populated(self):
        from evealert.tools.pilot_history_store import Sighting

        sightings = [
            Sighting(pilot_name="Bad Guy", system="Jita", ship="Sabre",
                      source="local", corp=None, alliance=None, seen_at=time.time()),
        ]
        self.window._on_dossier_ready((_dossier(), sightings))
        self.assertEqual(self.window._sighting_table.rowCount(), 1)
        self.assertEqual(self.window._sighting_table.item(0, 1).text(), "Jita")

    def test_none_dossier_shows_empty_state(self):
        self.window._on_dossier_ready((None, []))
        self.assertEqual(self.window._ship_table.rowCount(), 0)
        self.assertIsNone(self.window._current_dossier)
        self.assertIn("No dossier data", self.window._dossier_status.text())

    def test_exception_shows_error(self):
        self.window._on_dossier_ready(RuntimeError("boom"))
        self.assertIn("Dossier error", self.window._dossier_status.text())
        self.assertIsNone(self.window._current_dossier)


class TopHostilesReadyTests(IntelAnalyticsWindowTestCase):
    def test_populates_table_with_trend_arrow(self):
        from evealert.tools.intel_analytics import TopHostileEntry

        entries = [
            TopHostileEntry(
                pilot_name="Bad Guy", corp="Evil Corp", encounters=5,
                top_ship="Sabre", last_seen=time.time(), score=3.0, trend="up",
            ),
        ]
        self.window._on_top_hostiles_ready(entries)
        self.assertEqual(self.window._top_hostiles_table.rowCount(), 1)
        self.assertEqual(self.window._top_hostiles_table.item(0, 0).text(), "Bad Guy")
        self.assertIn("up", self.window._top_hostiles_table.item(0, 5).text())

    def test_empty_shows_guidance(self):
        self.window._on_top_hostiles_ready([])
        self.assertEqual(self.window._top_hostiles_table.rowCount(), 0)
        self.assertIn("No hostile activity", self.window._top_hostiles_status.text())

    def test_exception_shows_error(self):
        self.window._on_top_hostiles_ready(RuntimeError("boom"))
        self.assertIn("Error", self.window._top_hostiles_status.text())


class GroupReadyTests(IntelAnalyticsWindowTestCase):
    def test_populates_tables_and_status(self):
        from evealert.tools.hunting_grounds import GroupActivity

        group = GroupActivity(
            group_name="Snuffed Out", top_systems=[("Jita", 5)],
            hour_histogram=[0] * 19 + [5] + [0] * 4, top_pilots=[("Bad Guy", 5)],
            avg_gang_size=4.0, kills_7d=3, kills_30d=5, trend="rising",
        )
        self.window._on_group_ready(group)
        self.assertEqual(self.window._group_systems_table.rowCount(), 1)
        self.assertEqual(self.window._group_pilots_table.rowCount(), 1)
        self.assertIn("Snuffed Out", self.window._group_status.text())
        self.assertIn("rising", self.window._group_status.text())
        self.assertIs(self.window._current_group, group)

    def test_none_shows_guidance(self):
        self.window._on_group_ready(None)
        self.assertEqual(self.window._group_systems_table.rowCount(), 0)
        self.assertIsNone(self.window._current_group)
        self.assertIn("No tracked activity", self.window._group_status.text())

    def test_exception_shows_error(self):
        self.window._on_group_ready(RuntimeError("boom"))
        self.assertIn("Error", self.window._group_status.text())


class ExportTests(IntelAnalyticsWindowTestCase):
    def setUp(self):
        super().setUp()
        import shutil
        import tempfile

        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)

    def test_dossier_json_export_round_trips(self):
        import dataclasses

        dossier = _dossier()
        self.window._current_dossier = dossier
        out_path = os.path.join(self.temp_dir, "dossier.json")

        with patch(
            "evealert.ui.intel_analytics_window.QFileDialog.getSaveFileName",
            return_value=(out_path, ""),
        ):
            self.window._export_dossier_json()

        with open(out_path, encoding="utf-8") as f:
            loaded = json.load(f)
        # JSON has no tuple type -- compare against a JSON round-trip of
        # the expected data, not the raw dataclasses.asdict() (whose
        # tuple fields would never compare equal to JSON's lists).
        expected = json.loads(json.dumps(dataclasses.asdict(dossier)))
        self.assertEqual(loaded, expected)

    def test_dossier_json_export_with_no_dossier_shows_info_and_writes_nothing(self):
        out_path = os.path.join(self.temp_dir, "dossier.json")
        with patch(
            "evealert.ui.intel_analytics_window.QFileDialog.getSaveFileName",
            return_value=(out_path, ""),
        ), patch(
            "evealert.ui.intel_analytics_window.QMessageBox.information"
        ) as mock_info:
            self.window._current_dossier = None
            self.window._export_dossier_json()
        mock_info.assert_called_once()
        self.assertFalse(os.path.exists(out_path))

    def test_top_hostiles_csv_export_round_trips(self):
        from evealert.tools.intel_analytics import TopHostileEntry

        entries = [
            TopHostileEntry(
                pilot_name="Bad Guy", corp="Evil Corp", encounters=5,
                top_ship="Sabre", last_seen=time.time(), score=3.0, trend="up",
            ),
        ]
        self.window._on_top_hostiles_ready(entries)
        out_path = os.path.join(self.temp_dir, "hostiles.csv")

        with patch(
            "evealert.ui.intel_analytics_window.QFileDialog.getSaveFileName",
            return_value=(out_path, ""),
        ):
            self.window._export_top_hostiles_csv()

        with open(out_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["Pilot", "Corp", "Encounters", "Top Ship", "Last Seen (UTC)", "Trend"])
        self.assertEqual(rows[1][0], "Bad Guy")
        self.assertEqual(rows[1][2], "5")

    def test_cancelled_dialog_writes_nothing(self):
        out_path = os.path.join(self.temp_dir, "should_not_exist.csv")
        with patch(
            "evealert.ui.intel_analytics_window.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ):
            self.window._export_top_hostiles_csv()
        self.assertFalse(os.path.exists(out_path))


class BackgroundThreadDeliveryTests(IntelAnalyticsWindowTestCase):
    """#244 acceptance criterion: all queries run off the Qt thread,
    delivered back via signal emission."""

    def test_search_runs_on_a_background_thread(self):
        main_thread = threading.current_thread()
        seen = {}

        def _fake_search(query, limit=50):
            seen["thread"] = threading.current_thread()
            return ["Alice"]

        with patch(
            "evealert.tools.intel_analytics.search_pilot_names",
            side_effect=_fake_search,
        ):
            self.window._search_input.setText("ali")
            self.window._start_search()
            self.assertTrue(_pump_until(lambda: self.window._search_results.count() > 0))

        self.assertIn("thread", seen)
        self.assertIsNot(seen["thread"], main_thread)
        self.assertEqual(self.window._search_results.item(0).text(), "Alice")

    def test_top_hostiles_runs_on_a_background_thread(self):
        main_thread = threading.current_thread()
        seen = {}

        def _fake_top_hostiles(limit=20):
            seen["thread"] = threading.current_thread()
            return []

        with patch(
            "evealert.tools.intel_analytics.top_hostiles",
            side_effect=_fake_top_hostiles,
        ):
            self.window._start_top_hostiles()
            self.assertTrue(_pump_until(lambda: "thread" in seen))

        self.assertIsNot(seen["thread"], main_thread)


if __name__ == "__main__":
    unittest.main()
