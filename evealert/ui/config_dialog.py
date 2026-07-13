"""Config mode dialog — guides the user through selecting detection regions (Phase 4, #128)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from evealert.settings.store import SettingsStore
from evealert.ui.region_overlay import RegionOverlay, pick_screen_for_cursor


class ConfigDialog(QDialog):
    """Modeless dialog that guides the user through region selection.

    Hotkeys F1/F2 in MainWindow trigger the overlays; this dialog shows
    their current status.  The Config Mode button in MainWindow turns
    danger-variant while this dialog is open.
    """

    closed = Signal()  # emitted when the dialog is hidden/closed

    def __init__(self, parent, store: SettingsStore):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("EVE Alert — Config Mode")
        self.setFixedSize(480, 300)
        self._store = store
        self._overlay: RegionOverlay | None = None
        self._pending: str | None = None  # "alert" | "faction"

        self._build_ui()
        self._refresh_status()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)

        title = QLabel("Set up detection regions")
        title.setProperty("class", "title")
        root.addWidget(title)

        desc = QLabel(
            "Drag a rectangle over the <b>Local</b> area of the EVE Online client window."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        # Alert region row
        alert_row = self._region_row("1", "Press [F1] or click", "Select Alert Region", "alert")
        root.addWidget(alert_row)

        # Faction region row
        faction_row = self._region_row("2", "Press [F2] or click", "Select Faction Region", "faction")
        root.addWidget(faction_row)

        # ESC hint
        esc = QLabel("Press [ESC] to cancel an active selection.")
        esc.setProperty("class", "muted")
        root.addWidget(esc)

        # Status labels
        self._alert_status = QLabel()
        self._faction_status = QLabel()
        root.addWidget(self._alert_status)
        root.addWidget(self._faction_status)

        root.addStretch()

        # Close button
        btn_close = QPushButton("Close Config Mode")
        btn_close.clicked.connect(self.hide)
        root.addWidget(btn_close)

    def _region_row(self, num: str, hint: str, btn_text: str, kind: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        chip = QLabel(f"[F{num}]")
        chip.setProperty("class", "keychip")
        layout.addWidget(QLabel(f"{num}."))
        layout.addWidget(chip)
        layout.addWidget(QLabel(hint))
        layout.addStretch()
        btn = QPushButton(btn_text)
        btn.setProperty("class", "primary")
        btn.clicked.connect(lambda: self.start_selection(kind))
        layout.addWidget(btn)
        return row

    # ------------------------------------------------------------------
    # Region selection
    # ------------------------------------------------------------------

    def start_selection(self, kind: str) -> None:
        """Open a fullscreen overlay for the given region kind."""
        if self._overlay is not None:
            self._overlay.close()
        screen = pick_screen_for_cursor()
        self._overlay = RegionOverlay(screen)
        self._pending = kind
        self._overlay.region_selected.connect(self._on_region_selected)
        self._overlay.cancelled.connect(self._on_cancelled)
        self._overlay.showFullScreen()

    def _on_region_selected(self, x1: int, y1: int, x2: int, y2: int) -> None:
        kind = self._pending
        self._overlay = None
        self._pending = None
        try:
            settings = self._store.load()
            if kind == "alert":
                settings["alert_region_1"] = {"x": x1, "y": y1}
                settings["alert_region_2"] = {"x": x2, "y": y2}
            else:
                settings["faction_region_1"] = {"x": x1, "y": y1}
                settings["faction_region_2"] = {"x": x2, "y": y2}
            self._store.save(settings)
        except Exception:
            pass
        self._refresh_status()

    def _on_cancelled(self) -> None:
        self._overlay = None
        self._pending = None

    def _refresh_status(self) -> None:
        try:
            settings = self._store.load()
            r1 = settings.get("alert_region_1", {}); r2 = settings.get("alert_region_2", {})
            f1 = settings.get("faction_region_1", {}); f2 = settings.get("faction_region_2", {})
            if r1.get("x") or r2.get("x"):
                self._alert_status.setText(
                    f"✓ Alert: ({r1.get('x')},{r1.get('y')}) → ({r2.get('x')},{r2.get('y')})"
                )
            else:
                self._alert_status.setText("✗ Alert region not set")
            if f1.get("x") or f2.get("x"):
                self._faction_status.setText(
                    f"✓ Faction: ({f1.get('x')},{f1.get('y')}) → ({f2.get('x')},{f2.get('y')})"
                )
            else:
                self._faction_status.setText("✗ Faction region not set")
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        if self._overlay:
            self._overlay.close()
        self.closed.emit()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        self.closed.emit()
        super().hideEvent(event)

    def show_dialog(self) -> None:
        self._refresh_status()
        self.show()
        self.raise_()
        self.activateWindow()
