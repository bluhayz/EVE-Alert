"""Statistics window — stat cards + sortable tables (Phase 5, #129)."""

import csv
import json
import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from evealert.settings.stats_store import get_sessions_dir, list_session_reports
from evealert.statistics import AlarmStatistics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stat_card(caption: str, value: str = "0") -> tuple[QFrame, QLabel]:
    frame = QFrame()
    frame.setProperty("class", "card")
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 8, 12, 8)
    cap = QLabel(caption)
    cap.setProperty("class", "muted")
    val = QLabel(value)
    val.setProperty("class", "title")
    layout.addWidget(cap)
    layout.addWidget(val)
    return frame, val


def _ro_table(*headers) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(list(headers))
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSortingEnabled(True)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    return t


# ---------------------------------------------------------------------------
# StatisticsWindow
# ---------------------------------------------------------------------------

class StatisticsWindow(QWidget):
    """Top-level statistics window — two tabs: Live Stats and Sessions.

    Refreshes every second while visible; stops when hidden.
    """

    def __init__(self, parent, stats: AlarmStatistics):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("EVE Alert — Statistics")
        self.setMinimumSize(640, 560)
        self.resize(680, 640)
        self._stats = stats

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_live)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        # Tab 1 — Live Stats
        live_tab = QScrollArea()
        live_tab.setWidgetResizable(True)
        live_container = QWidget()
        live_layout = QVBoxLayout(live_container)
        live_layout.setAlignment(Qt.AlignTop)
        live_layout.setSpacing(10)
        live_tab.setWidget(live_container)
        tabs.addTab(live_tab, "Live Stats")

        # Session info line
        self._session_info = QLabel("")
        self._session_info.setProperty("class", "muted")
        live_layout.addWidget(self._session_info)

        # 3×2 card grid
        grid = QWidget()
        grid_layout = QGridLayout(grid)
        grid_layout.setSpacing(8)
        cards = [
            ("Lifetime Total", "lifetime_total"),
            ("Lifetime Enemy", "lifetime_enemy"),
            ("Lifetime Faction", "lifetime_faction"),
            ("Session Total", "session_total"),
            ("Session Enemy", "session_enemy"),
            ("Session Faction", "session_faction"),
        ]
        self._card_labels: dict[str, QLabel] = {}
        for i, (caption, key) in enumerate(cards):
            frame, val_label = _stat_card(caption)
            self._card_labels[key] = val_label
            grid_layout.addWidget(frame, i // 3, i % 3)
        live_layout.addWidget(grid)

        # History table
        history_hdr = QLabel("Recent Alarms")
        history_hdr.setProperty("class", "section")
        live_layout.addWidget(history_hdr)
        self._history_table = _ro_table("Time", "Type", "Details")
        live_layout.addWidget(self._history_table, 1)

        # Tab 2 — Sessions
        sessions_widget = QWidget()
        sessions_layout = QVBoxLayout(sessions_widget)
        tabs.addTab(sessions_widget, "Sessions")

        self._sessions_table = _ro_table("Date", "Duration", "Total", "Enemy", "Faction")

        # Button row
        btn_row = QHBoxLayout()
        btn_view = QPushButton("View")
        btn_export = QPushButton("Export CSV")
        btn_delete = QPushButton("Delete")
        btn_delete.setProperty("class", "danger")
        btn_refresh = QPushButton("Refresh")
        btn_view.clicked.connect(self._view_session)
        btn_export.clicked.connect(self._export_csv)
        btn_delete.clicked.connect(self._delete_session)
        btn_refresh.clicked.connect(self._refresh_sessions)
        for b in (btn_view, btn_export, btn_delete, btn_refresh):
            btn_row.addWidget(b)
        btn_row.addStretch()

        sessions_layout.addWidget(self._sessions_table, 1)
        sessions_layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_live()
        self._refresh_sessions()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    # ------------------------------------------------------------------
    # Live Stats tab
    # ------------------------------------------------------------------

    def _refresh_live(self) -> None:
        s = self._stats

        # Session info
        session_start = getattr(s, "_session_start", None)
        if session_start:
            elapsed = int(__import__("time").time() - session_start)
            h, m = divmod(elapsed // 60, 60)
            self._session_info.setText(f"Session started · {h:02d}:{m:02d} elapsed")

        # Cards
        def _c(key: str, value: int) -> None:
            lbl = self._card_labels.get(key)
            if lbl:
                lbl.setText(str(value))

        _c("lifetime_total", s.lifetime_alarms)
        _c("lifetime_enemy", getattr(s, "lifetime_enemy_alarms", 0))
        _c("lifetime_faction", getattr(s, "lifetime_faction_alarms", 0))
        _c("session_total", s.session_alarms)
        _c("session_enemy", getattr(s, "session_enemy_alarms", 0))
        _c("session_faction", getattr(s, "session_faction_alarms", 0))

        # History — read from stats if available
        events = getattr(s, "recent_events", [])
        if events:
            self._history_table.setRowCount(0)
            for ev in reversed(events):  # newest first
                row = self._history_table.rowCount()
                self._history_table.insertRow(row)
                self._history_table.setItem(row, 0, QTableWidgetItem(ev.get("time", "")))
                self._history_table.setItem(row, 1, QTableWidgetItem(ev.get("type", "")))
                self._history_table.setItem(row, 2, QTableWidgetItem(ev.get("details", "")))

    # ------------------------------------------------------------------
    # Sessions tab
    # ------------------------------------------------------------------

    def _refresh_sessions(self) -> None:
        self._sessions_table.setRowCount(0)
        for path in list_session_reports():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            row = self._sessions_table.rowCount()
            self._sessions_table.insertRow(row)
            self._sessions_table.setItem(row, 0, QTableWidgetItem(data.get("date", path.stem)))
            self._sessions_table.setItem(row, 1, QTableWidgetItem(str(data.get("duration", ""))))
            self._sessions_table.setItem(row, 2, QTableWidgetItem(str(data.get("total_alarms", 0))))
            self._sessions_table.setItem(row, 3, QTableWidgetItem(str(data.get("enemy_alarms", 0))))
            self._sessions_table.setItem(row, 4, QTableWidgetItem(str(data.get("faction_alarms", 0))))
            self._sessions_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, str(path))

    def _selected_session_path(self) -> str | None:
        row = self._sessions_table.currentRow()
        if row < 0:
            return None
        item = self._sessions_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _view_session(self) -> None:
        path = self._selected_session_path()
        if not path:
            return
        try:
            data = json.loads(open(path, encoding="utf-8").read())
            text = json.dumps(data, indent=2)
            dlg = QDialog(self)
            dlg.setWindowTitle("Session Report")
            dlg.resize(500, 400)
            from PySide6.QtWidgets import QPlainTextEdit  # noqa: PLC0415
            te = QPlainTextEdit(text)
            te.setReadOnly(True)
            lay = QVBoxLayout(dlg)
            lay.addWidget(te)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Sessions", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Date", "Duration", "Total", "Enemy", "Faction"])
                for row in range(self._sessions_table.rowCount()):
                    w.writerow([
                        self._sessions_table.item(row, c).text() if self._sessions_table.item(row, c) else ""
                        for c in range(5)
                    ])
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    def _delete_session(self) -> None:
        path = self._selected_session_path()
        if not path:
            return
        reply = QMessageBox.question(self, "Delete Session", "Delete this session report?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(path)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
        self._refresh_sessions()

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
