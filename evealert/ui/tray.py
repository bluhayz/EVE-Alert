"""QSystemTrayIcon wrapper for EVE Alert (Phase 2, #126)."""

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from evealert.settings.helper import get_resource_path


class AppTray(QSystemTrayIcon):
    """System tray icon with a Start/Stop/Show/Exit menu.

    All menu actions call methods on the MainWindow — no threads needed
    since QSystemTrayIcon runs on the main thread natively.
    """

    def __init__(self, win):
        icon = QIcon(get_resource_path("img/eve.png"))
        super().__init__(icon, win)
        self._win = win
        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self):
        menu = QMenu()

        self._show_action = menu.addAction("Show EVE Alert")
        self._show_action.triggered.connect(self._win.show_and_raise)
        menu.setDefaultAction(self._show_action)

        menu.addSeparator()

        self._start_action = menu.addAction("▶ Start Detection")
        self._start_action.triggered.connect(self._win.start_detection)

        self._stop_action = menu.addAction("■ Stop Detection")
        self._stop_action.triggered.connect(self._win.stop_detection)

        menu.addSeparator()

        exit_action = menu.addAction("Exit")
        exit_action.triggered.connect(self._win.exit_app)

        self.setContextMenu(menu)
        self.setToolTip("EVE Alert")

    def sync_run_state(self, running: bool) -> None:
        """Enable/disable Start and Stop based on engine state."""
        self._start_action.setEnabled(not running)
        self._stop_action.setEnabled(running)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._win.show_and_raise()
