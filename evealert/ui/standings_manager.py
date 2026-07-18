"""Standings Manager dialog (#173, v7.1).

Makes the threat_tiers block (manual RED/ORANGE/YELLOW/BLUE overrides,
previously opaque JSON) and ESI-synced personal standings visible,
editable, and shareable in one place.
"""

import json
import time

from PySide6.QtCore import QThread, Signal as _Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from evealert.settings.store import SettingsStore

_VALID_TIERS = ("red", "orange", "yellow", "blue")
_EXPORT_SCHEMA_VERSION = 1


class _StandingsSyncThread(QThread):
    """Runs the ESI personal-standings fetch + name resolution on a
    background thread so Qt's event loop is not blocked."""

    finished = _Signal(list, str)  # rows, error_message

    def run(self) -> None:
        import asyncio  # noqa: PLC0415

        try:
            rows = asyncio.run(self._fetch())
            self.finished.emit(rows, "")
        except Exception as exc:
            self.finished.emit([], str(exc))

    async def _fetch(self) -> list:
        from evealert.tools.esi_auth import (  # noqa: PLC0415
            get_esi_auth,
            get_personal_standings,
        )
        from evealert.tools.universe import resolve_names  # noqa: PLC0415

        auth = get_esi_auth()
        if not auth.is_authenticated:
            return []
        standings = await get_personal_standings(auth)
        ids = [s["from_id"] for s in standings if "from_id" in s]
        names = await resolve_names(ids)
        rows = []
        for s in standings:
            from_id = s.get("from_id")
            rows.append({
                "id": from_id,
                "name": names.get(from_id, f"#{from_id}"),
                "type": s.get("from_type", "?"),
                "standing": s.get("standing", 0.0),
            })
        return rows


class StandingsManagerDialog(QDialog):
    """View/override/import/export the ally/hostile lists (#173).

    Two independent tables:
      - Manual Overrides: read/write, backed by settings["threat_tiers"].
      - ESI-Synced Personal Standings: read-only, fetched on demand via
        "Sync Now" (same underlying call as the periodic 5-min monitor in
        AlertAgent._esi_standings_monitor -- this dialog doesn't touch the
        live engine, it just calls the same tool function independently).
    """

    def __init__(self, parent, store: SettingsStore):
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Standings Manager")
        self.setMinimumSize(640, 560)
        self._store = store
        self._esi_last_sync: float | None = None
        self._sync_thread: _StandingsSyncThread | None = None
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        root.addWidget(QLabel("<b>Manual Overrides</b>"))
        self._manual_table = QTableWidget(0, 3)
        self._manual_table.setHorizontalHeaderLabels(["Entity", "Tier", ""])
        self._manual_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        root.addWidget(self._manual_table, 1)

        add_row = QHBoxLayout()
        self._add_name_entry = QLineEdit()
        self._add_name_entry.setPlaceholderText("Pilot, corp, or alliance name…")
        self._add_tier_combo = QComboBox()
        self._add_tier_combo.addItems(list(_VALID_TIERS))
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_manual_override)
        add_row.addWidget(self._add_name_entry, 1)
        add_row.addWidget(self._add_tier_combo)
        add_row.addWidget(add_btn)
        root.addLayout(add_row)

        io_row = QHBoxLayout()
        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._export)
        import_btn = QPushButton("Import…")
        import_btn.clicked.connect(self._import)
        io_row.addWidget(export_btn)
        io_row.addWidget(import_btn)
        io_row.addStretch()
        root.addLayout(io_row)

        root.addWidget(QLabel("<b>ESI-Synced Personal Standings (read-only)</b>"))
        sync_row = QHBoxLayout()
        self._sync_status_label = QLabel("Not synced yet")
        self._sync_status_label.setProperty("class", "muted")
        self._sync_btn = QPushButton("Sync Now")
        self._sync_btn.clicked.connect(self._sync_now)
        sync_row.addWidget(self._sync_status_label, 1)
        sync_row.addWidget(self._sync_btn)
        root.addLayout(sync_row)

        self._esi_table = QTableWidget(0, 3)
        self._esi_table.setHorizontalHeaderLabels(["Entity", "Type", "Standing"])
        self._esi_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        root.addWidget(self._esi_table, 1)

        footer = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.setProperty("class", "primary")
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(save_btn)
        footer.addStretch()
        footer.addWidget(close_btn)
        root.addLayout(footer)

    # ------------------------------------------------------------------
    # Manual overrides
    # ------------------------------------------------------------------

    def _load(self) -> None:
        settings = self._store.load()
        self._populate_manual_table(settings.get("threat_tiers", {}))

    def _populate_manual_table(self, tiers: dict) -> None:
        self._manual_table.setRowCount(0)
        for name, tier in sorted(tiers.items()):
            self._add_manual_row(name, tier)

    def _add_manual_row(self, name: str, tier: str) -> None:
        row = self._manual_table.rowCount()
        self._manual_table.insertRow(row)
        self._manual_table.setItem(row, 0, QTableWidgetItem(name))

        combo = QComboBox()
        combo.addItems(list(_VALID_TIERS))
        idx = combo.findText(tier)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._manual_table.setCellWidget(row, 1, combo)

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(lambda: self._remove_manual_row(remove_btn))
        self._manual_table.setCellWidget(row, 2, remove_btn)

    def _remove_manual_row(self, button) -> None:
        for row in range(self._manual_table.rowCount()):
            if self._manual_table.cellWidget(row, 2) is button:
                self._manual_table.removeRow(row)
                return

    def _add_manual_override(self) -> None:
        name = self._add_name_entry.text().strip()
        if not name:
            return
        tier = self._add_tier_combo.currentText()
        # Re-use an existing row (case-insensitive) instead of duplicating.
        for row in range(self._manual_table.rowCount()):
            existing = self._manual_table.item(row, 0)
            if existing and existing.text().lower() == name.lower():
                combo = self._manual_table.cellWidget(row, 1)
                idx = combo.findText(tier)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                self._add_name_entry.clear()
                return
        self._add_manual_row(name, tier)
        self._add_name_entry.clear()

    def _collect_manual_overrides(self) -> dict:
        result: dict = {}
        for row in range(self._manual_table.rowCount()):
            name_item = self._manual_table.item(row, 0)
            combo = self._manual_table.cellWidget(row, 1)
            if name_item is None or combo is None:
                continue
            name = name_item.text().strip()
            if name:
                result[name] = combo.currentText()
        return result

    def _save(self) -> None:
        try:
            settings = self._store.load()
            settings["threat_tiers"] = self._collect_manual_overrides()
            self._store.save(settings)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Standings", "eve_alert_standings.json", "JSON Files (*.json)"
        )
        if not path:
            return
        entries = [
            {"name": name, "tier": tier}
            for name, tier in sorted(self._collect_manual_overrides().items())
        ]
        payload = {"eve_alert_standings": _EXPORT_SCHEMA_VERSION, "entries": entries}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", str(exc))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Standings", "", "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Import Failed", str(exc))
            return
        if payload.get("eve_alert_standings") != _EXPORT_SCHEMA_VERSION:
            QMessageBox.warning(
                self,
                "Import Failed",
                "Unrecognized standings file format "
                f"(expected schema version {_EXPORT_SCHEMA_VERSION}).",
            )
            return
        tiers: dict = {}
        for entry in payload.get("entries", []):
            name = str(entry.get("name", "")).strip()
            tier = str(entry.get("tier", "")).strip().lower()
            if name and tier in _VALID_TIERS:
                tiers[name] = tier
        self._populate_manual_table(tiers)

    # ------------------------------------------------------------------
    # ESI sync
    # ------------------------------------------------------------------

    def _sync_now(self) -> None:
        from evealert.tools.esi_auth import get_esi_auth  # noqa: PLC0415

        auth = get_esi_auth()
        if not auth.is_authenticated:
            self._sync_status_label.setText(
                "Not authenticated — login via Settings → Intel & ESI first"
            )
            return
        self._sync_btn.setEnabled(False)
        self._sync_status_label.setText("Syncing…")
        self._sync_thread = _StandingsSyncThread()
        self._sync_thread.finished.connect(self._on_sync_done)
        self._sync_thread.start()

    def _on_sync_done(self, rows: list, error: str) -> None:
        self._sync_btn.setEnabled(True)
        if error:
            self._sync_status_label.setText(f"Sync failed: {error}")
            return
        self._esi_last_sync = time.time()
        self._populate_esi_table(rows)
        synced_at = time.strftime("%H:%M:%S", time.localtime(self._esi_last_sync))
        self._sync_status_label.setText(f"Last synced {synced_at} — {len(rows)} entries")

    def _populate_esi_table(self, rows: list) -> None:
        self._esi_table.setRowCount(0)
        for row_data in sorted(rows, key=lambda r: r["standing"]):
            row = self._esi_table.rowCount()
            self._esi_table.insertRow(row)
            self._esi_table.setItem(row, 0, QTableWidgetItem(row_data["name"]))
            self._esi_table.setItem(row, 1, QTableWidgetItem(row_data["type"]))
            self._esi_table.setItem(
                row, 2, QTableWidgetItem(f"{row_data['standing']:+.1f}")
            )
