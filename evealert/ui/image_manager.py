"""Image Manager dialog — add/remove alert and faction template images (Phase 6, #130)."""

import shutil

import cv2
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from evealert.constants import ALERT_IMAGE_PREFIX, FACTION_IMAGE_PREFIX
from evealert.settings.helper import get_user_img_path


class ImageManagerDialog(QDialog):
    """Manage bundled + user-added template images with thumbnails and preview."""

    def __init__(self, parent, alert_agent):
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Image Manager")
        self.setMinimumSize(560, 480)
        self._agent = alert_agent
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        # Left: list
        left = QVBoxLayout()
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_select)
        left.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        self._btn_add_alert = QPushButton("Add Alert Image…")
        self._btn_add_alert.setProperty("class", "primary")
        self._btn_add_alert.clicked.connect(lambda: self._add_image(ALERT_IMAGE_PREFIX))

        self._btn_add_faction = QPushButton("Add Faction Image…")
        self._btn_add_faction.setProperty("class", "primary")
        self._btn_add_faction.clicked.connect(lambda: self._add_image(FACTION_IMAGE_PREFIX))

        self._btn_remove = QPushButton("Remove")
        self._btn_remove.setProperty("class", "danger")
        self._btn_remove.setEnabled(False)
        self._btn_remove.clicked.connect(self._remove_image)

        for b in (self._btn_add_alert, self._btn_add_faction, self._btn_remove):
            btn_row.addWidget(b)
        btn_row.addStretch()
        left.addLayout(btn_row)

        # Right: preview
        right = QVBoxLayout()
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setMinimumSize(240, 240)
        self._preview.setMaximumSize(240, 240)
        self._preview.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._preview.setProperty("class", "card")
        self._caption = QLabel()
        self._caption.setProperty("class", "muted")
        self._caption.setAlignment(Qt.AlignCenter)
        right.addWidget(self._preview)
        right.addWidget(self._caption)
        right.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        right.addWidget(btn_close)

        root.addLayout(left, 1)
        root.addLayout(right)

    def _populate(self) -> None:
        self._list.clear()
        # Collect images from both bundled and user dirs
        from evealert.manager.alertmanager import _load_image_files  # noqa: PLC0415
        try:
            alert_files, faction_files = _load_image_files()
        except Exception:
            alert_files, faction_files = [], []
        user_dir = str(get_user_img_path())
        for path in alert_files + faction_files:
            item = QListWidgetItem()
            fname = path.replace("\\", "/").split("/")[-1]
            is_bundled = not path.startswith(user_dir)
            display = fname + (" (bundled)" if is_bundled else "")
            item.setText(display)
            item.setData(Qt.ItemDataRole.UserRole, {"path": path, "bundled": is_bundled})
            try:
                px = QPixmap(path).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                item.setIcon(QIcon(px))
            except Exception:
                pass
            self._list.addItem(item)

    def _on_select(self, current, _prev) -> None:
        if current is None:
            self._btn_remove.setEnabled(False)
            self._preview.clear()
            self._caption.clear()
            return
        data = current.data(Qt.ItemDataRole.UserRole)
        bundled = data["bundled"]
        self._btn_remove.setEnabled(not bundled)
        # Preview
        try:
            px = QPixmap(data["path"]).scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._preview.setPixmap(px)
            img = cv2.imread(data["path"])
            if img is not None:
                h, w = img.shape[:2]
                fname = data["path"].replace("\\", "/").split("/")[-1]
                self._caption.setText(f"{fname} — {w}×{h} px")
            else:
                self._caption.setText("")
        except Exception:
            self._preview.clear()
            self._caption.clear()

    def _add_image(self, prefix: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff)"
        )
        if not path:
            return
        # Validate with cv2
        img = cv2.imread(path)
        if img is None:
            QMessageBox.warning(self, "Invalid Image", "The selected file could not be read as an image.")
            return
        user_dir = get_user_img_path()
        user_dir.mkdir(parents=True, exist_ok=True)
        fname = path.replace("\\", "/").split("/")[-1]
        if not fname.startswith(prefix):
            fname = prefix + fname
        dest = user_dir / fname
        shutil.copy2(path, dest)
        # Trigger template reload on the running engine
        try:
            from evealert.manager.alertmanager import _load_image_files  # noqa: PLC0415
            alert_files, faction_files = _load_image_files()
            self._agent._alert_files = alert_files
            self._agent._faction_files = faction_files
            from evealert.tools.vision import Vision  # noqa: PLC0415
            self._agent.alert_vision = Vision(alert_files)
            self._agent.alert_vision_faction = Vision(faction_files)
        except Exception:
            pass
        self._populate()

    def _remove_image(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if data["bundled"]:
            return
        reply = QMessageBox.question(self, "Remove Image", f"Remove '{item.text()}'?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            import os  # noqa: PLC0415
            os.remove(data["path"])
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            return
        # Reload templates
        try:
            from evealert.manager.alertmanager import _load_image_files  # noqa: PLC0415
            alert_files, faction_files = _load_image_files()
            self._agent._alert_files = alert_files
            self._agent._faction_files = faction_files
            from evealert.tools.vision import Vision  # noqa: PLC0415
            self._agent.alert_vision = Vision(alert_files)
            self._agent.alert_vision_faction = Vision(faction_files)
        except Exception:
            pass
        self._populate()
