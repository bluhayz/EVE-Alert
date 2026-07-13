"""Placeholder MainWindow — replaced in Phase 2 (issue #126)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

from evealert import __version__


class MainWindow(QMainWindow):
    """Minimal placeholder — Phase 2 will replace this with the full layout."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"EVE Alert — v{__version__}")
        self.setMinimumSize(640, 520)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignCenter)

        label = QLabel("EVE Alert UI — Phase 1 scaffold\nPhase 2 will wire the detection engine.")
        label.setAlignment(Qt.AlignCenter)
        label.setProperty("class", "muted")
        layout.addWidget(label)
