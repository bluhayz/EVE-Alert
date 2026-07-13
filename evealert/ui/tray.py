"""QSystemTrayIcon wrapper for EVE Alert (#126, extended #168).

Adds three runtime-generated icon variants (status dot composited onto the
base icon with QPainter), alarm flash, a last-alarm tooltip, and a Mute
toggle menu item.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from evealert.settings.helper import get_resource_path


def _build_icon(base_pixmap: QPixmap, dot_color: str) -> QIcon:
    """Return a QIcon with a 10×10 coloured status dot in the bottom-right corner."""
    pix = QPixmap(base_pixmap)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    dot_size = max(10, pix.width() // 3)
    x = pix.width() - dot_size - 2
    y = pix.height() - dot_size - 2
    # Shadow
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#000000"))
    painter.drawEllipse(x + 1, y + 1, dot_size, dot_size)
    # Dot
    painter.setBrush(QColor(dot_color))
    painter.drawEllipse(x, y, dot_size, dot_size)
    painter.end()
    return QIcon(pix)


class AppTray(QSystemTrayIcon):
    """System tray icon with state dot, alarm flash, and Mute toggle."""

    def __init__(self, win) -> None:
        # Build base pixmap and three icon variants
        base_path = get_resource_path("img/eve.png")
        base_px = QPixmap(base_path)
        if base_px.isNull():
            # Fallback: create a plain 32×32 square as the base
            base_px = QPixmap(32, 32)
            base_px.fill(QColor("#1a1a2e"))

        self._icon_stopped = _build_icon(base_px, "#888888")  # grey
        self._icon_running = _build_icon(base_px, "#4ade80")  # green
        self._icon_alarm   = _build_icon(base_px, "#f85149")  # red

        super().__init__(self._icon_stopped, win)
        self._win = win
        self._is_running = False
        self._alarm_timer = QTimer()
        self._alarm_timer.setSingleShot(True)
        self._alarm_timer.timeout.connect(self._revert_icon)
        self._build_menu()
        self.activated.connect(self._on_activated)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = QMenu()

        self._show_action = menu.addAction("Show EVE Alert")
        self._show_action.triggered.connect(self._win.show_and_raise)
        menu.setDefaultAction(self._show_action)

        menu.addSeparator()

        self._start_action = menu.addAction("\u25b6 Start Detection")
        self._start_action.triggered.connect(self._win.start_detection)

        self._stop_action = menu.addAction("\u25a0 Stop Detection")
        self._stop_action.triggered.connect(self._win.stop_detection)

        menu.addSeparator()

        self._mute_action = menu.addAction("Mute alarms")
        self._mute_action.setCheckable(True)
        self._mute_action.triggered.connect(self._toggle_mute)

        menu.addSeparator()

        exit_action = menu.addAction("Exit")
        exit_action.triggered.connect(self._win.exit_app)

        self.setContextMenu(menu)
        self.setToolTip("EVE Alert \u2014 Stopped")

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def sync_run_state(self, running: bool) -> None:
        """Enable/disable Start/Stop and update icon when not flashing alarm."""
        self._is_running = running
        self._start_action.setEnabled(not running)
        self._stop_action.setEnabled(running)
        if not self._alarm_timer.isActive():
            self._set_icon_state("running" if running else "stopped")

    def on_alarm(self, text: str) -> None:
        """Flash red for 10 s then revert; update tooltip with last alarm."""
        self._set_icon_state("alarm")
        self._alarm_timer.stop()
        self._alarm_timer.start(10_000)
        from datetime import datetime  # noqa: PLC0415
        ts = datetime.now().strftime("%H:%M:%S")
        # Extract first ~40 chars of text for the tooltip
        short = text[:60].rstrip()
        self.setToolTip(
            f"EVE Alert \u2014 {'Running' if self._is_running else 'Stopped'} "
            f"\u00b7 last alarm {ts}: {short}"
        )

    def _revert_icon(self) -> None:
        self._set_icon_state("running" if self._is_running else "stopped")

    def _set_icon_state(self, state: str) -> None:
        icon_map = {
            "stopped": self._icon_stopped,
            "running": self._icon_running,
            "alarm":   self._icon_alarm,
        }
        self.setIcon(icon_map.get(state, self._icon_stopped))

    # ------------------------------------------------------------------
    # Mute toggle
    # ------------------------------------------------------------------

    def _toggle_mute(self, muted: bool) -> None:
        try:
            store = self._win.store
            settings = store.load_raw()
            settings.setdefault("server", {})["mute"] = muted
            store.save(settings)
            self._win.alert.load_settings()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._win.show_and_raise()
