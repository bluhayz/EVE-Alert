"""Qt application bootstrap — create_app() and run()."""

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from evealert.settings.helper import get_resource_path
from evealert.ui import theme


def create_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("EVE Alert")
    app.setOrganizationName("EVE Alert")
    icon_path = get_resource_path("img/eve.png")
    app.setWindowIcon(QIcon(icon_path))
    qss = theme.load_qss()
    if qss:
        app.setStyleSheet(qss)
    return app


def run() -> int:
    app = create_app()

    from evealert.settings.store import get_settings_store  # noqa: PLC0415
    from evealert.tools.crash_reporter import install, install_qt_handler  # noqa: PLC0415

    settings = get_settings_store().load()
    crash_reports_enabled = bool(
        settings.get("diagnostics", {}).get("crash_reports", True)
    )
    install_qt_handler()
    # Hooks go live immediately (no dialog callback yet) so even a crash
    # during MainWindow construction is captured to a bundle.
    install(enabled=crash_reports_enabled)

    from evealert.ui.main_window import MainWindow  # noqa: PLC0415

    win = MainWindow()

    if crash_reports_enabled:
        install(on_crash=lambda bundle_dir: win.bridge.notify_crash(str(bundle_dir)))
        from evealert.ui.crash_dialog import maybe_show_pending_crash  # noqa: PLC0415

        maybe_show_pending_crash(win)

    win.show()
    return app.exec()
