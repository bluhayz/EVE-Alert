"""Intel Analytics window (#244, v7.4).

Makes the accumulated intelligence explorable outside the heat of an
alarm: a pilot dossier browser (#241), a top-hostiles board, and a
corp/alliance group view (#242), with CSV/JSON export.

All store/rollup reads run on a background thread; results are delivered
back to the Qt main thread via Signal -- the same _heatmap_ready pattern
statistics_window.py's Threat Heatmap tab already uses (#158) -- so a
slow or cold SQLite read never blocks the UI thread.
"""

import asyncio
import csv
import dataclasses
import json
import threading
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

_TREND_ARROWS = {"up": "▲ up", "down": "▼ down", "flat": "→ flat", "rising": "▲ rising", "falling": "▼ falling", "steady": "→ steady"}


def _ro_table(*headers) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(list(headers))
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSortingEnabled(True)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    return t


def _set_row(table: QTableWidget, row: int, values: list) -> None:
    for col, val in enumerate(values):
        item = QTableWidgetItem(str(val))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, col, item)


class IntelAnalyticsWindow(QWidget):
    """Pilot dossier browser + top-hostiles board + group view."""

    # Payload is the successful result on success, an Exception on
    # failure -- same convention as statistics_window's _heatmap_ready.
    _search_ready = Signal(object)
    _dossier_ready = Signal(object)
    _top_hostiles_ready = Signal(object)
    _group_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("EVE Alert — Intel Analytics")
        self.setMinimumSize(760, 600)
        self.resize(840, 680)
        self._current_dossier = None
        self._current_group = None

        self._build_ui()

        self._search_ready.connect(self._on_search_ready)
        self._dossier_ready.connect(self._on_dossier_ready)
        self._top_hostiles_ready.connect(self._on_top_hostiles_ready)
        self._group_ready.connect(self._on_group_ready)

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)
        tabs.addTab(self._build_dossier_tab(), "Pilot Dossier")
        tabs.addTab(self._build_top_hostiles_tab(), "Top Hostiles")
        tabs.addTab(self._build_group_tab(), "Group View")

    def _build_dossier_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search pilot name…")
        self._search_input.returnPressed.connect(self._start_search)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._start_search)
        search_row.addWidget(self._search_input, 1)
        search_row.addWidget(search_btn)
        layout.addLayout(search_row)

        self._search_results = QListWidget()
        self._search_results.setMaximumHeight(90)
        self._search_results.itemClicked.connect(self._on_search_result_clicked)
        layout.addWidget(self._search_results)

        self._dossier_status = QLabel(
            "Search for a pilot to view their dossier — ships flown, hunting "
            "grounds, activity pattern, and fleetmates."
        )
        self._dossier_status.setProperty("class", "muted")
        self._dossier_status.setWordWrap(True)
        layout.addWidget(self._dossier_status)

        layout.addWidget(QLabel("<b>Ships Flown</b>"))
        self._ship_table = _ro_table("Ship", "Share")
        self._ship_table.setMaximumHeight(110)
        layout.addWidget(self._ship_table)

        layout.addWidget(QLabel("<b>Hunting Grounds</b>"))
        self._hunt_table = _ro_table("System", "Kills")
        self._hunt_table.setMaximumHeight(110)
        layout.addWidget(self._hunt_table)

        layout.addWidget(QLabel("<b>Fleetmates</b>"))
        self._fleetmate_table = _ro_table("Pilot", "Shared Kills")
        self._fleetmate_table.setMaximumHeight(90)
        layout.addWidget(self._fleetmate_table)

        layout.addWidget(QLabel("<b>24h Activity (UTC hour: kill count)</b>"))
        self._activity_label = QLabel("")
        self._activity_label.setProperty("class", "muted")
        self._activity_label.setWordWrap(True)
        layout.addWidget(self._activity_label)

        layout.addWidget(QLabel("<b>Recent Sightings</b>"))
        self._sighting_table = _ro_table("Time (UTC)", "System", "Ship", "Source")
        layout.addWidget(self._sighting_table, 1)

        export_row = QHBoxLayout()
        export_btn = QPushButton("Export Dossier JSON")
        export_btn.clicked.connect(self._export_dossier_json)
        export_row.addWidget(export_btn)
        export_row.addStretch()
        layout.addLayout(export_row)

        return widget

    def _build_top_hostiles_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._start_top_hostiles)
        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_top_hostiles_csv)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(export_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._top_hostiles_status = QLabel(
            "No data yet — click Refresh once you've tracked some hostile "
            "activity (ranked by recency-weighted encounters, last 30 days)."
        )
        self._top_hostiles_status.setProperty("class", "muted")
        self._top_hostiles_status.setWordWrap(True)
        layout.addWidget(self._top_hostiles_status)

        self._top_hostiles_table = _ro_table(
            "Pilot", "Corp", "Encounters", "Top Ship", "Last Seen (UTC)", "Trend"
        )
        layout.addWidget(self._top_hostiles_table, 1)
        return widget

    def _build_group_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        input_row = QHBoxLayout()
        self._group_input = QLineEdit()
        self._group_input.setPlaceholderText("Corp or alliance name…")
        self._group_input.returnPressed.connect(self._start_group_load)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._start_group_load)
        input_row.addWidget(self._group_input, 1)
        input_row.addWidget(load_btn)
        layout.addLayout(input_row)

        self._group_status = QLabel(
            "Enter a corp or alliance name to view where and when they hunt."
        )
        self._group_status.setProperty("class", "muted")
        self._group_status.setWordWrap(True)
        layout.addWidget(self._group_status)

        layout.addWidget(QLabel("<b>Top Systems</b>"))
        self._group_systems_table = _ro_table("System", "Kills")
        layout.addWidget(self._group_systems_table)

        layout.addWidget(QLabel("<b>Top Pilots</b>"))
        self._group_pilots_table = _ro_table("Pilot", "Kills")
        layout.addWidget(self._group_pilots_table)

        export_row = QHBoxLayout()
        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_group_csv)
        export_row.addWidget(export_btn)
        export_row.addStretch()
        layout.addLayout(export_row)

        return widget

    # ------------------------------------------------------------------
    # Worker-thread helpers -- never touch Qt widgets from these threads;
    # only ever emit a Signal, whose slot runs back on the Qt main thread.
    # ------------------------------------------------------------------

    def _run_sync_in_worker(self, fn, ready_signal: Signal, name: str) -> None:
        def _run() -> None:
            try:
                ready_signal.emit(fn())
            except Exception as exc:
                ready_signal.emit(exc)

        threading.Thread(target=_run, daemon=True, name=name).start()

    def _run_async_in_worker(self, coro_factory, ready_signal: Signal, name: str) -> None:
        def _run() -> None:
            try:
                loop = asyncio.new_event_loop()
                result = loop.run_until_complete(coro_factory())
                loop.close()
                ready_signal.emit(result)
            except Exception as exc:
                ready_signal.emit(exc)

        threading.Thread(target=_run, daemon=True, name=name).start()

    # ------------------------------------------------------------------
    # Pilot Dossier tab
    # ------------------------------------------------------------------

    def _start_search(self) -> None:
        query = self._search_input.text().strip()
        if not query:
            return
        self._dossier_status.setText("Searching…")

        def _search():
            from evealert.tools.intel_analytics import search_pilot_names  # noqa: PLC0415

            return search_pilot_names(query)

        self._run_sync_in_worker(_search, self._search_ready, "eve-alert-intel-search")

    def _on_search_ready(self, payload: object) -> None:
        if isinstance(payload, Exception):
            self._dossier_status.setText(f"Search error: {payload}")
            return
        self._search_results.clear()
        if not payload:
            self._dossier_status.setText("No matching pilots found.")
            return
        self._search_results.addItems(payload)
        self._dossier_status.setText(
            f"{len(payload)} match(es) — select a pilot to view their dossier."
        )

    def _on_search_result_clicked(self, item) -> None:
        name = item.text()
        self._dossier_status.setText(f"Loading dossier for {name}…")

        def _fetch():
            from evealert.tools.pilot_dossier import build_dossier  # noqa: PLC0415
            from evealert.tools.pilot_history_store import get_sightings  # noqa: PLC0415

            async def _run():
                dossier = await build_dossier(name)
                sightings = get_sightings(name, limit=20)
                return dossier, sightings

            return _run()

        self._run_async_in_worker(_fetch, self._dossier_ready, "eve-alert-intel-dossier")

    def _on_dossier_ready(self, payload: object) -> None:
        self._ship_table.setRowCount(0)
        self._hunt_table.setRowCount(0)
        self._fleetmate_table.setRowCount(0)
        self._sighting_table.setRowCount(0)
        self._activity_label.setText("")

        if isinstance(payload, Exception):
            self._dossier_status.setText(f"Dossier error: {payload}")
            self._current_dossier = None
            return

        dossier, sightings = payload
        self._current_dossier = dossier

        if dossier is None:
            self._dossier_status.setText("No dossier data for this pilot yet.")
        else:
            self._dossier_status.setText(f"Dossier: {dossier.pilot_name}")
            self._ship_table.setRowCount(len(dossier.top_ships))
            for row, (name, pct) in enumerate(dossier.top_ships):
                _set_row(self._ship_table, row, [name, f"{pct:.0f}%"])

            self._hunt_table.setRowCount(len(dossier.top_hunt_systems))
            for row, (system, count) in enumerate(dossier.top_hunt_systems):
                _set_row(self._hunt_table, row, [system, count])

            self._fleetmate_table.setRowCount(len(dossier.frequent_fleetmates))
            for row, (name, count) in enumerate(dossier.frequent_fleetmates):
                _set_row(self._fleetmate_table, row, [name, count])

            self._activity_label.setText(
                " ".join(f"{h:02d}:{v}" for h, v in enumerate(dossier.active_hours) if v)
                or "No activity recorded."
            )

        self._sighting_table.setRowCount(len(sightings))
        for row, s in enumerate(sightings):
            _set_row(self._sighting_table, row, [
                time.strftime("%Y-%m-%d %H:%M", time.gmtime(s.seen_at)),
                s.system or "", s.ship or "", s.source,
            ])

    def _export_dossier_json(self) -> None:
        if self._current_dossier is None:
            QMessageBox.information(self, "Export Dossier", "No dossier loaded.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Dossier", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dataclasses.asdict(self._current_dossier), f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    # ------------------------------------------------------------------
    # Top Hostiles tab
    # ------------------------------------------------------------------

    def _start_top_hostiles(self) -> None:
        self._top_hostiles_status.setText("Loading…")

        def _fetch():
            from evealert.tools.intel_analytics import top_hostiles  # noqa: PLC0415

            return top_hostiles()

        self._run_sync_in_worker(
            _fetch, self._top_hostiles_ready, "eve-alert-intel-tophostiles"
        )

    def _on_top_hostiles_ready(self, payload: object) -> None:
        self._top_hostiles_table.setRowCount(0)
        if isinstance(payload, Exception):
            self._top_hostiles_status.setText(f"Error: {payload}")
            return
        entries = payload
        if not entries:
            self._top_hostiles_status.setText(
                "No hostile activity recorded in the last 30 days."
            )
            return
        self._top_hostiles_status.setText(
            f"{len(entries)} pilot(s) tracked in the last 30 days."
        )
        self._top_hostiles_table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            _set_row(self._top_hostiles_table, row, [
                e.pilot_name, e.corp or "", e.encounters, e.top_ship or "",
                time.strftime("%Y-%m-%d %H:%M", time.gmtime(e.last_seen)),
                _TREND_ARROWS.get(e.trend, e.trend),
            ])

    def _export_top_hostiles_csv(self) -> None:
        self._export_table_csv(self._top_hostiles_table, "top_hostiles.csv")

    # ------------------------------------------------------------------
    # Group View tab
    # ------------------------------------------------------------------

    def _start_group_load(self) -> None:
        name = self._group_input.text().strip()
        if not name:
            return
        self._group_status.setText("Loading…")

        def _fetch():
            from evealert.tools.hunting_grounds import group_activity  # noqa: PLC0415

            return group_activity(name)

        self._run_sync_in_worker(_fetch, self._group_ready, "eve-alert-intel-group")

    def _on_group_ready(self, payload: object) -> None:
        self._group_systems_table.setRowCount(0)
        self._group_pilots_table.setRowCount(0)

        if isinstance(payload, Exception):
            self._group_status.setText(f"Error: {payload}")
            self._current_group = None
            return
        if payload is None:
            self._group_status.setText(
                "No tracked activity found for that corp/alliance."
            )
            self._current_group = None
            return

        group = payload
        self._current_group = group

        from evealert.tools.hunting_grounds import hot_window  # noqa: PLC0415

        total = sum(group.hour_histogram)
        prime, prime_pct = hot_window(group.hour_histogram, total)
        prime_desc = f", prime {prime} EVE ({prime_pct:.0f}%)" if prime else ""
        gang_desc = (
            f", avg gang ~{group.avg_gang_size:.0f}" if group.avg_gang_size is not None else ""
        )
        self._group_status.setText(
            f"{group.group_name}: {group.kills_7d} kills (7d) / "
            f"{group.kills_30d} kills (30d), trend {group.trend}{gang_desc}{prime_desc}"
        )

        self._group_systems_table.setRowCount(len(group.top_systems))
        for row, (system, count) in enumerate(group.top_systems):
            _set_row(self._group_systems_table, row, [system, count])

        self._group_pilots_table.setRowCount(len(group.top_pilots))
        for row, (pilot, count) in enumerate(group.top_pilots):
            _set_row(self._group_pilots_table, row, [pilot, count])

    def _export_group_csv(self) -> None:
        self._export_table_csv(self._group_systems_table, "group_systems.csv")

    # ------------------------------------------------------------------
    # Shared export helper
    # ------------------------------------------------------------------

    def _export_table_csv(self, table: QTableWidget, default_name: str) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", default_name, "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                headers = [
                    table.horizontalHeaderItem(c).text() for c in range(table.columnCount())
                ]
                w.writerow(headers)
                for row in range(table.rowCount()):
                    w.writerow([
                        table.item(row, c).text() if table.item(row, c) else ""
                        for c in range(table.columnCount())
                    ])
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))
