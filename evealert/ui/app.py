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
    from evealert.ui.main_window import MainWindow  # noqa: PLC0415

    win = MainWindow()
    win.show()
    return app.exec()
