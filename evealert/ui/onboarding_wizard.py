"""First-run onboarding wizard for EVE Alert (#164).

Auto-shown when the alert region has never been configured (x1==y1==x2==y2==0).
Guides the user from app launch to a working alarm in ~2 minutes:

  Page 0 — Welcome: EVE window detection
  Page 1 — Alert region: RegionOverlay selection + live thumbnail
  Page 2 — Sound & volume: test the alarm sound
  Page 3 — Done: summary + start detection option

Persists ui.onboarding_completed=True so the wizard never auto-shows again.
Re-launchable via Settings → Detection → "Run Setup Wizard…".
"""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class OnboardingWizardDialog(QDialog):
    """4-page wizard that gets a new user to a working alarm configuration."""

    _thumbnail_ready = Signal(object)   # QPixmap or None

    def __init__(self, parent, store, alert=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Setup Wizard")
        self.setMinimumSize(520, 400)
        self._store = store
        self._alert = alert  # AlertAgent, for volume changes
        self._region: tuple[int, int, int, int] | None = None
        self._overlay = None

        self._thumbnail_ready.connect(self._on_thumbnail_ready)
        self._build_ui()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Page title
        self._page_title = QLabel("")
        self._page_title.setProperty("class", "heading")
        self._page_title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._page_title)

        # Stack
        self._stack = QStackedWidget()
        self._stack.addWidget(self._page_welcome())   # 0
        self._stack.addWidget(self._page_region())    # 1
        self._stack.addWidget(self._page_sound())     # 2
        self._stack.addWidget(self._page_done())      # 3
        root.addWidget(self._stack, 1)

        # Navigation
        nav = QHBoxLayout()
        self._btn_skip   = QPushButton("Skip wizard")
        self._btn_back   = QPushButton("\u2190 Back")
        self._btn_next   = QPushButton("Next \u2192")
        self._btn_finish = QPushButton("\u2713 Finish")
        self._btn_finish.setProperty("class", "primary")
        self._btn_skip.clicked.connect(self._skip)
        self._btn_back.clicked.connect(self._go_back)
        self._btn_next.clicked.connect(self._go_next)
        self._btn_finish.clicked.connect(self._finish)
        nav.addWidget(self._btn_skip)
        nav.addStretch()
        nav.addWidget(self._btn_back)
        nav.addWidget(self._btn_next)
        nav.addWidget(self._btn_finish)
        root.addLayout(nav)

        self._go_to(0)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def _page_welcome(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(
            "<b>Welcome to EVE Alert!</b><br><br>"
            "This wizard will help you configure EVE Alert in under 2 minutes.<br>"
            "You will select the screen region containing your Local chat list,<br>"
            "test the alarm sound, and optionally start detection right away.<br>"
            "<br>"
            "Make sure EVE Online is running and Local chat is visible."
        ))
        layout.addSpacing(12)

        self._eve_status = QLabel("")
        self._eve_status.setWordWrap(True)
        layout.addWidget(self._eve_status)

        btn_detect = QPushButton("Detect EVE Window")
        btn_detect.clicked.connect(self._detect_eve)
        layout.addWidget(btn_detect, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        return w

    def _page_region(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(
            "Press <b>Select Region</b>, then drag a rectangle over the<br>"
            "standing-icon column in your EVE Local chat list."
        ))
        layout.addSpacing(8)

        self._region_coords = QLabel("No region selected yet.")
        self._region_coords.setProperty("class", "muted")
        layout.addWidget(self._region_coords)

        btn_select = QPushButton("Select Region\u2026")
        btn_select.clicked.connect(self._start_region_select)
        layout.addWidget(btn_select, 0, Qt.AlignmentFlag.AlignLeft)

        self._thumbnail_label = QLabel()
        self._thumbnail_label.setFixedSize(240, 120)
        self._thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumbnail_label.setStyleSheet("border: 1px solid #333; background: #111;")
        self._thumbnail_label.setText("Region preview")
        layout.addWidget(self._thumbnail_label)
        layout.addStretch()
        return w

    def _page_sound(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(
            "Adjust the alarm volume and press <b>Test Sound</b><br>"
            "to hear what the alarm will sound like."
        ))
        layout.addSpacing(8)

        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("Volume:"))
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self._vol_slider)
        self._vol_label = QLabel("80%")
        vol_row.addWidget(self._vol_label)
        layout.addLayout(vol_row)

        btn_test = QPushButton("Test Sound")
        btn_test.clicked.connect(self._test_sound)
        layout.addWidget(btn_test, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        return w

    def _page_done(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(
            "<b>Setup complete!</b><br><br>"
            "Your alert region and volume are configured. You can start detection now,<br>"
            "or explore Settings to add Discord webhooks, ESI, push notifications, and more."
        ))
        layout.addSpacing(12)

        self._region_summary = QLabel("")
        self._region_summary.setProperty("class", "muted")
        layout.addWidget(self._region_summary)

        self._start_checkbox = QCheckBox("Start detection now")
        self._start_checkbox.setChecked(True)
        layout.addWidget(self._start_checkbox)

        btn_settings = QPushButton("Open Settings\u2026")
        btn_settings.clicked.connect(lambda: self.parent()._open_settings() if hasattr(self.parent(), "_open_settings") else None)
        layout.addWidget(btn_settings, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        return w

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    _TITLES = [
        "Step 1 of 4 \u2014 Welcome",
        "Step 2 of 4 \u2014 Alert Region",
        "Step 3 of 4 \u2014 Sound",
        "Step 4 of 4 \u2014 Done",
    ]

    def _go_to(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._page_title.setText(self._TITLES[index])
        self._btn_back.setEnabled(index > 0)
        is_last = index == 3
        self._btn_next.setVisible(not is_last)
        self._btn_finish.setVisible(is_last)
        if is_last:
            self._refresh_done_summary()

    def _go_back(self) -> None:
        self._go_to(max(0, self._stack.currentIndex() - 1))

    def _go_next(self) -> None:
        self._go_to(min(3, self._stack.currentIndex() + 1))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _detect_eve(self) -> None:
        try:
            from evealert.tools.window_finder import find_eve_window  # noqa: PLC0415
            bounds = find_eve_window()
            if bounds:
                self._region = (bounds.x, bounds.y,
                                bounds.x + bounds.width,
                                bounds.y + bounds.height)
                self._eve_status.setText(
                    f"\u2713 EVE window found at ({bounds.x},{bounds.y}) "
                    f"{bounds.width}\u00d7{bounds.height} — region pre-filled!"
                )
                self._eve_status.setStyleSheet("color: #3FB950;")
                self._update_region_ui()
            else:
                self._eve_status.setText(
                    "\u2717 EVE window not found. Make sure EVE is running, "
                    "or select the region manually on the next page."
                )
                self._eve_status.setStyleSheet("color: #F85149;")
        except Exception as exc:
            self._eve_status.setText(f"\u2717 {exc}")
            self._eve_status.setStyleSheet("color: #F85149;")

    def _start_region_select(self) -> None:
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415
        from evealert.ui.region_overlay import RegionOverlay, pick_screen_for_cursor  # noqa: PLC0415

        screen = pick_screen_for_cursor()
        self._overlay = RegionOverlay(screen)
        self._overlay.region_selected.connect(self._on_region_selected)
        self._overlay.cancelled.connect(lambda: None)
        self._overlay.show()

    def _on_region_selected(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self._region = (x1, y1, x2, y2)
        self._update_region_ui()
        self._grab_thumbnail()

    def _update_region_ui(self) -> None:
        if self._region:
            x1, y1, x2, y2 = self._region
            self._region_coords.setText(
                f"Region: ({x1}, {y1}) \u2192 ({x2}, {y2})"
                f"  [{x2-x1}\u00d7{y2-y1} px]"
            )

    def _grab_thumbnail(self) -> None:
        if not self._region:
            return
        x1, y1, x2, y2 = self._region
        sig = self._thumbnail_ready

        def _run():
            try:
                import mss  # noqa: PLC0415
                with mss.mss() as sct:
                    monitor = {"left": x1, "top": y1, "width": x2-x1, "height": y2-y1}
                    img = sct.grab(monitor)
                    from PIL import Image  # noqa: PLC0415
                    pil = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                    pil = pil.resize((240, 120))
                    qpix = QPixmap()
                    import io  # noqa: PLC0415
                    buf = io.BytesIO()
                    pil.save(buf, "PNG")
                    qpix.loadFromData(buf.getvalue())
                    sig.emit(qpix)
            except Exception:
                sig.emit(None)

        threading.Thread(target=_run, daemon=True).start()

    def _on_thumbnail_ready(self, pix) -> None:
        if pix and not pix.isNull():
            self._thumbnail_label.setPixmap(pix)
        else:
            self._thumbnail_label.setText("Preview unavailable")

    def _on_volume_changed(self, value: int) -> None:
        self._vol_label.setText(f"{value}%")

    def _test_sound(self) -> None:
        try:
            from evealert.constants import ALARM_SOUND_FILE, SOUND_FOLDER  # noqa: PLC0415
            from evealert.settings.helper import get_resource_path  # noqa: PLC0415
            import sounddevice as sd  # noqa: PLC0415
            import soundfile as sf  # noqa: PLC0415

            path = get_resource_path(f"{SOUND_FOLDER}/{ALARM_SOUND_FILE}")
            data, rate = sf.read(path, dtype="float32")
            vol = self._vol_slider.value() / 100.0
            sd.play(data * vol, rate)
        except Exception:
            pass

    def _refresh_done_summary(self) -> None:
        if self._region:
            x1, y1, x2, y2 = self._region
            self._region_summary.setText(
                f"Alert region: ({x1},{y1}) \u2192 ({x2},{y2})"
            )
        else:
            self._region_summary.setText("Alert region: not configured")

    # ------------------------------------------------------------------
    # Finish / skip
    # ------------------------------------------------------------------

    def _save_settings(self) -> None:
        """Persist region + volume to SettingsStore."""
        settings = self._store.load_raw()
        if self._region:
            x1, y1, x2, y2 = self._region
            settings["alert_region_1"] = {"x": x1, "y": y1}
            settings["alert_region_2"] = {"x": x2, "y": y2}
        vol = self._vol_slider.value()
        settings.setdefault("volume", {})["value"] = vol
        settings.setdefault("ui", {})["onboarding_completed"] = True
        self._store.save(settings)

    def _finish(self) -> None:
        self._save_settings()
        if self._start_checkbox.isChecked():
            parent = self.parent()
            if parent and hasattr(parent, "start_detection"):
                parent.start_detection()
        self.accept()

    def _skip(self) -> None:
        settings = self._store.load_raw()
        settings.setdefault("ui", {})["onboarding_completed"] = True
        self._store.save(settings)
        self.reject()
