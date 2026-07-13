"""Full MainWindow for the PySide6 UI (Phase 2, #126).

Replaces the Phase 1 placeholder.  The detection engine (AlertAgent) runs
in a daemon thread; all engine→UI traffic flows exclusively through QtBridge
signals, never via direct widget access from the engine thread.
"""

import threading
from datetime import datetime

from PySide6.QtCore import QTimer, Signal, Slot
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from evealert import __version__
from evealert.manager.alertmanager import AlertAgent
from evealert.settings.helper import get_resource_path
from evealert.settings.store import SettingsStore, get_settings_store
from evealert.ui import theme
from evealert.ui.qt_bridge import QtBridge
from evealert.ui.tray import AppTray


# ---------------------------------------------------------------------------
# _MainProxy — thin stub passed to AlertAgent so self.main.xxx still resolves
# ---------------------------------------------------------------------------

class _MainProxy:
    """Minimal object passed as ``main`` to AlertAgent in the Qt path.

    AlertAgent._ui() calls functions on self.main for identity checking.
    This proxy satisfies those checks, routing everything to QtBridge.
    """

    def __init__(self, bridge: QtBridge):
        self._bridge = bridge
        # Legacy attribute checked by alertmanager (now superseded by self._webhook)
        self.webhook = None

    def after(self, ms: int, fn=None) -> None:  # noqa: ARG002 — ms ignored
        if fn is not None:
            QTimer.singleShot(0, fn)

    def write_message(self, text: str, color: str = "normal") -> None:
        self._bridge.log(text, color)

    def update_alert_button(self) -> None:
        self._bridge.refresh_region_toggles()

    def update_faction_button(self) -> None:
        self._bridge.refresh_region_toggles()

    def open_error_window(self, msg: str) -> None:
        self._bridge.show_error(msg)


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Primary application window for EVE Alert (Qt path).

    Layout (top → bottom):
      header card  — status dot + title + version
      context line — system, webhook state, web UI state
      row 1        — Start (primary) | Stop (danger) | [stretch] | Exit
      row 2        — Config Mode | Settings | Statistics
      row 3        — Show Alert Region | Show Faction Region
      log pane     — QPlainTextEdit, stretch=1
      status bar   — hotkey hints
    """

    # Signal emitted from the pynput thread; connected to _on_hotkey (main thread)
    hotkey_pressed = Signal(str)  # "alert" | "faction" | "esc"

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"EVE Alert — v{__version__}")
        self.setMinimumSize(640, 520)

        # Engine objects
        self.store: SettingsStore = get_settings_store()
        self.bridge = QtBridge(self)
        self._proxy = _MainProxy(self.bridge)
        self.alert = AlertAgent(self._proxy)

        # Connect bridge signals → slots (queued to main thread automatically)
        self.bridge.log_message.connect(self.append_log)
        self.bridge.toggles_changed.connect(self.refresh_toggles)
        self.bridge.error.connect(self._on_engine_error)

        self._build_ui()
        self._build_tray()
        self._setup_hotkeys()

        # Dialog instances (lazy creation)
        self._settings_dlg = None
        self._stats_dlg = None
        self._config_dlg = None
        self._current_profile: str | None = None  # active space profile key (#143)

        # 1 s poll timer to sync status from engine state
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._sync_run_state)
        self._poll_timer.start()

        # Initial UI state
        self.refresh_context_line()
        self._sync_run_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # Header card
        header_frame = QWidget()
        header_frame.setProperty("class", "card")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(10, 6, 10, 6)

        self._status_label = QLabel("● Stopped")
        self._status_label.setProperty("class", "status-off")
        header_layout.addWidget(self._status_label)

        title_label = QLabel("EVE Alert")
        title_label.setProperty("class", "title")
        header_layout.addWidget(title_label, 1)

        ver_label = QLabel(f"v{__version__}")
        ver_label.setProperty("class", "muted")
        header_layout.addWidget(ver_label)

        root.addWidget(header_frame)

        # Context line
        self._context_label = QLabel("")
        self._context_label.setProperty("class", "muted")
        root.addWidget(self._context_label)

        # Row 1 — Start | Stop | [stretch] | Exit
        row1 = QHBoxLayout()
        self._btn_start = QPushButton("▶  Start")
        self._btn_start.setProperty("class", "primary")
        self._btn_start.clicked.connect(self.start_detection)

        self._btn_stop = QPushButton("■  Stop")
        self._btn_stop.setProperty("class", "danger")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self.stop_detection)

        btn_exit = QPushButton("Exit")
        btn_exit.clicked.connect(self.exit_app)

        row1.addWidget(self._btn_start)
        row1.addWidget(self._btn_stop)
        row1.addStretch()
        row1.addWidget(btn_exit)
        root.addLayout(row1)

        # Row 2 — Config Mode | Settings | Statistics
        row2 = QHBoxLayout()
        self._btn_config = QPushButton("Config Mode")
        self._btn_config.clicked.connect(self._open_config)

        self._btn_settings = QPushButton("Settings")
        self._btn_settings.clicked.connect(self._open_settings)

        self._btn_stats = QPushButton("Statistics")
        self._btn_stats.clicked.connect(self._open_statistics)

        for btn in (self._btn_config, self._btn_settings, self._btn_stats):
            row2.addWidget(btn)
        row2.addStretch()
        root.addLayout(row2)

        # Row 3 — region toggles
        row3 = QHBoxLayout()
        self._btn_alert_region = QPushButton("Show Alert Region")
        self._btn_alert_region.setProperty("class", "primary")
        self._btn_alert_region.clicked.connect(self._toggle_alert_region)

        self._btn_faction_region = QPushButton("Show Faction Region")
        self._btn_faction_region.setProperty("class", "primary")
        self._btn_faction_region.clicked.connect(self._toggle_faction_region)

        row3.addWidget(self._btn_alert_region)
        row3.addWidget(self._btn_faction_region)
        row3.addStretch()
        root.addLayout(row3)

        # Log pane
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.document().setMaximumBlockCount(500)
        root.addWidget(self._log, 1)

        # Status bar
        self.statusBar().showMessage(
            "F1: alert region · F2: faction region · F3: cycle space profile · F4: status readout · ESC: cancel selection"
        )

        self._apply_dynamic_properties()

    def _apply_dynamic_properties(self) -> None:
        """Force QSS re-evaluation after setting dynamic properties."""
        for w in (self._status_label, self._context_label):
            w.style().unpolish(w)
            w.style().polish(w)

    def _build_tray(self) -> None:
        self._tray = AppTray(self)
        self._tray.show()

    def _setup_hotkeys(self) -> None:
        """Start the pynput global-hotkey listener on a daemon thread."""
        try:
            from pynput import keyboard  # noqa: PLC0415

            hotkeys = self.store.load().get("hotkeys", {})
            alert_key = hotkeys.get("alert_region", "f1")
            faction_key = hotkeys.get("faction_region", "f2")

            from evealert.hotkeys import key_matches  # noqa: PLC0415

            def _on_release(key):
                if key_matches(key, alert_key):
                    self.hotkey_pressed.emit("alert")
                elif key_matches(key, faction_key):
                    self.hotkey_pressed.emit("faction")
                elif key_matches(key, "f3"):
                    self.hotkey_pressed.emit("profile")
                elif key_matches(key, "f4"):
                    self.hotkey_pressed.emit("status")
                elif key_matches(key, "esc"):
                    self.hotkey_pressed.emit("esc")

            self._hotkey_listener = keyboard.Listener(on_release=_on_release)
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
        except Exception:
            self._hotkey_listener = None

        self.hotkey_pressed.connect(self._on_hotkey)

    # ------------------------------------------------------------------
    # Engine control
    # ------------------------------------------------------------------

    def start_detection(self) -> None:
        if self.alert.is_running:
            return
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        t = threading.Thread(target=self.alert.start, daemon=True)
        t.start()

    def stop_detection(self) -> None:
        self.alert.stop()

    # ------------------------------------------------------------------
    # Log pane
    # ------------------------------------------------------------------

    @Slot(str, str)
    def append_log(self, text: str, color: str = "normal") -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{now}] {text}"

        fmt = QTextCharFormat()
        hex_color = theme.LOG_COLORS.get(color, theme.TEXT)
        fmt.setForeground(QColor(hex_color))

        cursor = self._log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(line + "\n", fmt)

        # Auto-scroll only when already at the bottom
        scrollbar = self._log.verticalScrollBar()
        if scrollbar.value() >= scrollbar.maximum() - 4:
            self._log.ensureCursorVisible()

        # Mirror to web server log buffer
        try:
            from evealert.tools.web_server import append_to_log_buffer  # noqa: PLC0415
            append_to_log_buffer(line)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Region toggles
    # ------------------------------------------------------------------

    @Slot()
    def refresh_toggles(self) -> None:
        alert_active = self.alert.alert_vision.is_vision_open
        faction_active = self.alert.alert_vision_faction.is_faction_vision_open

        self._btn_alert_region.setProperty(
            "class", "danger" if alert_active else "primary"
        )
        self._btn_faction_region.setProperty(
            "class", "danger" if faction_active else "primary"
        )
        for btn in (self._btn_alert_region, self._btn_faction_region):
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _toggle_alert_region(self) -> None:
        QTimer.singleShot(0, self.alert.set_vision)

    def _toggle_faction_region(self) -> None:
        QTimer.singleShot(0, self.alert.set_vision_faction)

    # ------------------------------------------------------------------
    # Context line + state sync
    # ------------------------------------------------------------------

    def refresh_context_line(self) -> None:
        try:
            settings = self.store.load()
            system = settings.get("server", {}).get("system", "")
            if system == "Enter a System Name":
                system = ""
            webhook_on = bool(settings.get("server", {}).get("webhook", ""))
            web_on = settings.get("web_ui", {}).get("enabled", False)
            parts = []
            if system:
                parts.append(f"System: {system}")
            parts.append(f"Webhook: {'on' if webhook_on else 'off'}")
            parts.append(f"Web UI: {'on' if web_on else 'off'}")
            self._context_label.setText(" · ".join(parts))
        except Exception:
            pass

    def _sync_run_state(self) -> None:
        running = self.alert.is_running
        if running:
            self._status_label.setText("● Running")
            self._status_label.setProperty("class", "status-on")
        else:
            self._status_label.setText("● Stopped")
            self._status_label.setProperty("class", "status-off")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)

        self._btn_start.setEnabled(not running)
        self._btn_stop.setEnabled(running)
        self._tray.sync_run_state(running)

    # ------------------------------------------------------------------
    # Dialog launchers (filled in by Phases 3–5)
    # ------------------------------------------------------------------

    def _open_config(self) -> None:
        if self._config_dlg is None:
            from evealert.ui.config_dialog import ConfigDialog  # noqa: PLC0415
            self._config_dlg = ConfigDialog(self, self.store)
            self._config_dlg.closed.connect(lambda: self._btn_config.setProperty("class", "") or self._restyle(self._btn_config))
        self._btn_config.setProperty("class", "warning")
        self._restyle(self._btn_config)
        self._config_dlg.show_dialog()

    def _open_settings(self) -> None:
        if self._settings_dlg is None:
            from evealert.ui.settings_dialog import SettingsDialog  # noqa: PLC0415
            self._settings_dlg = SettingsDialog(self, self.store)
        self._settings_dlg.show_dialog()
        # Refresh context line when dialog saves
        if hasattr(self._settings_dlg, 'accepted'):
            self._settings_dlg.accepted.connect(self.refresh_context_line)

    def _open_statistics(self) -> None:
        if self._stats_dlg is None:
            from evealert.ui.statistics_window import StatisticsWindow  # noqa: PLC0415
            self._stats_dlg = StatisticsWindow(self, self.alert.statistics)
        self._stats_dlg.show_window()

    # ------------------------------------------------------------------
    # Hotkeys
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_hotkey(self, kind: str) -> None:
        if kind in ("alert", "faction"):
            if self._config_dlg is not None:
                self._config_dlg.start_selection(kind)
            else:
                self.append_log(f"Hotkey {kind}: open Config Mode first", "yellow")
        elif kind == "profile":
            self._cycle_space_profile()
        elif kind == "status":
            self._speak_status()

    def _speak_status(self) -> None:
        """F4: assemble current threat state and speak it via TTS (#152)."""
        try:
            from evealert.tools.tts import speak, is_tts_available  # noqa: PLC0415
            from evealert.data.ship_classes import ShipThreatClass  # noqa: PLC0415
            from evealert.tools.threat_score import compute_threat_score  # noqa: PLC0415

            if not is_tts_available():
                self.append_log("Status readout: TTS not available (pip install pyttsx3)", "yellow")
                return

            alert = self.alert

            local_count = getattr(alert, "_local_hostile_count", 0)
            dscan_classes: set = getattr(alert, "_dscan_last_classes", set())
            top_class = ShipThreatClass.UNKNOWN
            if dscan_classes:
                top_class = max(dscan_classes,
                                key=lambda c: ShipThreatClass(c).urgency,
                                default=ShipThreatClass.UNKNOWN)
            nm = getattr(alert, "_neighbor_monitor", None)
            adj_kills = getattr(nm, "last_kill_count", 0) if nm else 0

            assessment = compute_threat_score(
                local_hostile_count=local_count,
                dscan_threat_class=top_class.value if top_class != ShipThreatClass.UNKNOWN else "",
                adjacent_kills=adj_kills,
                is_cyno=ShipThreatClass.CYNO in dscan_classes,
            )

            if assessment.score == 0 and local_count == 0:
                phrase = "All clear. No hostiles in local."
            else:
                parts = []
                if local_count:
                    parts.append(f"{local_count} hostile{'s' if local_count != 1 else ''} in local")
                if top_class not in (ShipThreatClass.UNKNOWN, ShipThreatClass.INDUSTRIAL):
                    parts.append(f"{top_class.value.replace('_', ' ')} on D-scan")
                if adj_kills:
                    parts.append(f"{adj_kills} kill{'s' if adj_kills != 1 else ''} in adjacent system")
                reason_str = "; ".join(parts) if parts else "threat detected"
                phrase = f"{assessment.label}. {reason_str}. Threat score {assessment.score} out of 10."

            speak(phrase, rate=getattr(alert, "_tts_rate", 175))
            self.append_log(f"Status: {phrase}", "cyan")
        except Exception as exc:
            self.append_log(f"Status readout failed: {exc}", "yellow")

    def _cycle_space_profile(self) -> None:
        """F3: advance to the next space profile and apply it."""
        try:
            from evealert.tools.space_profiles import next_profile, apply_profile  # noqa: PLC0415

            self._current_profile = next_profile(self._current_profile)
            label = apply_profile(self._current_profile)
            # Reload agent settings without restart
            self.alert.load_settings()
            self.append_log(f"Space profile \u2192 {label}", "cyan")
        except Exception as exc:
            self.append_log(f"Profile switch failed: {exc}", "yellow")

    # ------------------------------------------------------------------
    # Error display
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_engine_error(self, message: str) -> None:
        QMessageBox.critical(self, "EVE Alert — Engine Error", message)

    # ------------------------------------------------------------------
    # Window / tray / exit
    # ------------------------------------------------------------------

    def _restyle(self, widget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        """Hide to tray instead of closing."""
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "EVE Alert",
            "Running in the system tray. Double-click to restore.",
            AppTray.MessageIcon.Information,
            2000,
        )

    def exit_app(self) -> None:
        """Hard exit: stop engine, hide tray, quit Qt."""
        # Stop pynput listener
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
        # Stop engine cleanly
        if self.alert.is_running:
            self.alert.stop()
        # Hide tray icon so it doesn't ghost
        self._tray.hide()
        QApplication.quit()
