"""Per-image threshold editor dialog (Phase 6, #130)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from evealert.manager.alertmanager import _load_image_files
from evealert.settings.store import SettingsStore


class ThresholdEditorDialog(QDialog):
    """Scrollable per-image threshold overrides.

    Shows one row per template image: filename | slider(1-100) | value | Clear.
    'Clear' removes the override so the global threshold applies.
    Saving writes ``image_thresholds`` via the store (patch-merge pattern).
    """

    def __init__(self, parent, store: SettingsStore):
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Per-Image Thresholds")
        self.setMinimumSize(520, 420)
        self._store = store
        self._rows: list[dict] = []  # {path, fname, slider, label}
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        self._rows_layout = QVBoxLayout(container)
        self._rows_layout.setAlignment(Qt.AlignTop)
        self._rows_layout.setSpacing(4)
        area.setWidget(container)
        root.addWidget(area, 1)

        # Footer
        footer = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_save.setProperty("class", "primary")
        btn_save.clicked.connect(self._save)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_save)
        footer.addStretch()
        footer.addWidget(btn_close)
        root.addLayout(footer)

    def _load(self) -> None:
        settings = self._store.load()
        overrides: dict = settings.get("image_thresholds", {})
        global_val: int = settings.get("detectionscale", {}).get("value", 90)

        # Clear existing rows
        for r in self._rows:
            r["widget"].deleteLater()
        self._rows.clear()

        try:
            alert_files, faction_files = _load_image_files()
        except Exception:
            alert_files, faction_files = [], []

        for path in alert_files + faction_files:
            fname = path.replace("\\", "/").split("/")[-1]
            override = overrides.get(fname)
            current = int(override) if override is not None else global_val
            is_override = override is not None

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 2, 4, 2)

            name_lbl = QLabel(fname)
            name_lbl.setMinimumWidth(200)
            name_lbl.setProperty("class", "" if is_override else "muted")

            slider = QSlider(Qt.Horizontal)
            slider.setRange(1, 100)
            slider.setValue(current)

            val_label = QLabel(str(current))
            val_label.setFixedWidth(36)
            slider.valueChanged.connect(lambda v, lbl=val_label: lbl.setText(str(v)))

            btn_clear = QPushButton("Clear")
            btn_clear.setFixedWidth(55)
            btn_clear.setEnabled(is_override)

            entry = {"path": path, "fname": fname, "slider": slider, "val_label": val_label,
                     "name_lbl": name_lbl, "btn_clear": btn_clear, "widget": row_widget}

            def _make_clear(e=entry):
                def _clear():
                    e["slider"].setValue(global_val)
                    e["name_lbl"].setProperty("class", "muted")
                    e["name_lbl"].style().unpolish(e["name_lbl"])
                    e["name_lbl"].style().polish(e["name_lbl"])
                    e["btn_clear"].setEnabled(False)
                    e["_cleared"] = True
                return _clear

            entry["_cleared"] = False
            btn_clear.clicked.connect(_make_clear(entry))
            slider.valueChanged.connect(lambda v, e=entry: (
                e["btn_clear"].setEnabled(True),
                e["name_lbl"].setProperty("class", ""),
            ))

            row_layout.addWidget(name_lbl)
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(val_label)
            row_layout.addWidget(btn_clear)
            self._rows_layout.addWidget(row_widget)
            self._rows.append(entry)

    def _save(self) -> None:
        try:
            settings = self._store.load()
            overrides = dict(settings.get("image_thresholds", {}))
            for e in self._rows:
                if e.get("_cleared"):
                    overrides.pop(e["fname"], None)
                elif not e["btn_clear"].isEnabled() and not e.get("_cleared"):
                    # Not overridden — skip
                    pass
                else:
                    overrides[e["fname"]] = e["slider"].value()
            settings["image_thresholds"] = overrides
            self._store.save(settings)
        except Exception as ex:
            QMessageBox.warning(self, "Error", str(ex))
