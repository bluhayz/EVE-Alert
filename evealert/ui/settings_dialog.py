"""Settings dialog — registry-generated form + non-registry sections (Phase 3, #127).

Rule: save = store.load() → patch the keys this dialog owns → store.save().
Never build a settings dict from DEFAULT_SETTINGS (data-loss risk, see #108).
"""

import os

from PySide6.QtCore import Qt, QThread, Signal as _Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from evealert.settings.fields import FIELDS, TAB_ORDER
from evealert.settings.helper import get_settings_path
from evealert.settings.store import SettingsStore, _get_by_path, _set_by_path


class _LoginThread(QThread):
    """Runs EsiAuth.login() on a background thread so Qt's event loop is not blocked."""

    finished = _Signal(bool, str)  # ok, character_name_or_error

    def __init__(self, auth):
        super().__init__()
        self._auth = auth

    def run(self) -> None:
        import asyncio  # noqa: PLC0415
        try:
            ok = asyncio.run(self._auth.login())
            name = self._auth.character_name if ok else ""
            self.finished.emit(ok, name)
        except Exception as exc:
            self.finished.emit(False, str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _group(title: str, form: bool = True) -> tuple[QGroupBox, QFormLayout | QVBoxLayout]:
    box = QGroupBox(title)
    layout = QFormLayout(box) if form else QVBoxLayout(box)
    layout.setContentsMargins(10, 6, 10, 10)
    layout.setSpacing(6)
    return box, layout


def _scroll_tab() -> tuple[QScrollArea, QVBoxLayout]:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setAlignment(Qt.AlignTop)
    layout.setSpacing(8)
    layout.setContentsMargins(8, 8, 8, 8)
    area.setWidget(container)
    return area, layout


# ---------------------------------------------------------------------------
# SettingsDialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """Modeless settings dialog.  One instance owned by MainWindow.

    call show_dialog() to open/raise; it calls load() on each open so
    values always reflect the latest saved state.
    """

    def __init__(self, parent, store: SettingsStore):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("EVE Alert — Settings")
        self.setMinimumSize(700, 560)
        self.resize(760, 640)
        self._store = store
        # registry controls: dotted-path → QWidget
        self._controls: dict[str, QWidget] = {}
        # tab layout containers: tab_name → QVBoxLayout
        self._tab_layouts: dict[str, QVBoxLayout] = {}
        # section group boxes: "tab/section" → QGroupBox + its form layout
        self._sections: dict[str, tuple[QGroupBox, QFormLayout]] = {}

        self._build_ui()
        self.load()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # Profile bar
        root.addWidget(self._build_profile_bar())

        # Tab widget
        self._tabs = QTabWidget()
        for tab_name in TAB_ORDER:
            area, layout = _scroll_tab()
            self._tabs.addTab(area, tab_name)
            self._tab_layouts[tab_name] = layout
        root.addWidget(self._tabs, 1)

        # Non-registry sections first (appear at top of each tab)
        self._build_detection_sections()
        self._build_sound_sections()
        self._build_intel_sections()

        # Registry-driven sections (appended after non-registry)
        self._build_registry_controls()

        # Post-registry additions that inject into auto-created sections
        self._build_ocr_check_button()
        self._build_tts_check_button()

        # Footer buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Close
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("class", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save && Apply")
        buttons.accepted.connect(self._save_and_apply)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply_only)
        buttons.rejected.connect(self.hide)
        root.addWidget(buttons)

    def _build_profile_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Profile:"))
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(160)
        layout.addWidget(self._profile_combo)
        for text, slot in [
            ("Save", self._save_profile),
            ("New…", self._new_profile),
            ("Load", self._load_profile),
            ("Delete", self._delete_profile),
        ]:
            btn = QPushButton(text)
            if text == "Delete":
                btn.setProperty("class", "danger")
            layout.addWidget(btn)
            btn.clicked.connect(slot)
        layout.addStretch()
        return bar

    def _get_section(self, tab: str, section: str) -> QFormLayout:
        """Return (or create) the QFormLayout for a named section on a tab."""
        key = f"{tab}/{section}"
        if key not in self._sections:
            box, form = _group(section)
            self._tab_layouts[tab].addWidget(box)
            self._sections[key] = (box, form)
        return self._sections[key][1]

    # ── Non-registry: Detection tab ────────────────────────────────────

    def _build_detection_sections(self) -> None:
        det = self._tab_layouts["Detection"]

        # Alert region
        box, form = _group("Alert Region")
        self._alert_x1 = QSpinBox(); self._alert_x1.setRange(0, 9999)
        self._alert_y1 = QSpinBox(); self._alert_y1.setRange(0, 9999)
        self._alert_x2 = QSpinBox(); self._alert_x2.setRange(0, 9999)
        self._alert_y2 = QSpinBox(); self._alert_y2.setRange(0, 9999)
        form.addRow("X1:", self._alert_x1); form.addRow("Y1:", self._alert_y1)
        form.addRow("X2:", self._alert_x2); form.addRow("Y2:", self._alert_y2)
        det.addWidget(box)

        # Faction region
        box2, form2 = _group("Faction Region")
        self._faction_x1 = QSpinBox(); self._faction_x1.setRange(0, 9999)
        self._faction_y1 = QSpinBox(); self._faction_y1.setRange(0, 9999)
        self._faction_x2 = QSpinBox(); self._faction_x2.setRange(0, 9999)
        self._faction_y2 = QSpinBox(); self._faction_y2.setRange(0, 9999)
        form2.addRow("X1:", self._faction_x1); form2.addRow("Y1:", self._faction_y1)
        form2.addRow("X2:", self._faction_x2); form2.addRow("Y2:", self._faction_y2)
        det.addWidget(box2)

        # Detection thresholds
        box3, form3 = _group("Detection Thresholds")
        self._enemy_threshold = QSlider(Qt.Horizontal); self._enemy_threshold.setRange(1, 100)
        self._faction_threshold = QSlider(Qt.Horizontal); self._faction_threshold.setRange(1, 100)
        self._enemy_thresh_label = QLabel("90")
        self._faction_thresh_label = QLabel("90")
        self._enemy_threshold.valueChanged.connect(lambda v: self._enemy_thresh_label.setText(str(v)))
        self._faction_threshold.valueChanged.connect(lambda v: self._faction_thresh_label.setText(str(v)))
        enemy_row = QHBoxLayout()
        enemy_row.addWidget(self._enemy_threshold, 1)
        enemy_row.addWidget(self._enemy_thresh_label)
        per_img_btn = QPushButton("Per-Image Thresholds…")
        per_img_btn.clicked.connect(self._open_threshold_editor)
        enemy_row.addWidget(per_img_btn)
        faction_row = QHBoxLayout()
        faction_row.addWidget(self._faction_threshold, 1)
        faction_row.addWidget(self._faction_thresh_label)
        form3.addRow("Enemy:", enemy_row)
        form3.addRow("Faction:", faction_row)
        det.addWidget(box3)

        # Log level
        box4, form4 = _group("Logging")
        self._log_level = QComboBox()
        self._log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level.setCurrentText("INFO")
        form4.addRow("Log Level:", self._log_level)
        det.addWidget(box4)

    # ── Non-registry: Alerts & Sound tab ──────────────────────────────

    def _build_sound_sections(self) -> None:
        snd = self._tab_layouts["Alerts & Sound"]

        # System + mute
        box, form = _group("System")
        self._system_name = QLineEdit()
        self._system_name.setPlaceholderText("Enter a System Name")
        self._mute = QCheckBox("Mute alarm")
        form.addRow("System Name:", self._system_name)
        form.addRow("", self._mute)
        snd.addWidget(box)

        # Volume
        box2, form2 = _group("Volume")
        self._volume = QSlider(Qt.Horizontal); self._volume.setRange(0, 100); self._volume.setValue(100)
        self._volume_label = QLabel("100")
        self._volume.valueChanged.connect(lambda v: self._volume_label.setText(str(v)))
        vol_row = QHBoxLayout()
        vol_row.addWidget(self._volume, 1); vol_row.addWidget(self._volume_label)
        form2.addRow("Volume:", vol_row)
        snd.addWidget(box2)

        # Cooldowns
        box3, form3 = _group("Cooldown Timers (seconds)")
        self._cooldown = QSpinBox(); self._cooldown.setRange(0, 3600)
        self._cooldown_enemy = QSpinBox(); self._cooldown_enemy.setRange(0, 3600)
        self._cooldown_faction = QSpinBox(); self._cooldown_faction.setRange(0, 3600)
        form3.addRow("Global:", self._cooldown)
        form3.addRow("Enemy:", self._cooldown_enemy)
        form3.addRow("Faction:", self._cooldown_faction)
        snd.addWidget(box3)

        # Webhooks
        box4, form4 = _group("All-Events Webhook")
        self._webhook_url = QLineEdit(); self._webhook_url.setPlaceholderText("https://discord.com/api/webhooks/...")
        self._webhook_template = QLineEdit()
        form4.addRow("URL:", self._webhook_url)
        form4.addRow("Template:", self._webhook_template)
        snd.addWidget(box4)

        box5, form5 = _group("Per-Type Webhooks")
        self._enemy_webhook = QLineEdit(); self._enemy_webhook.setPlaceholderText("Enemy URL")
        self._enemy_webhook_min = QSpinBox(); self._enemy_webhook_min.setRange(0, 999)
        self._faction_webhook = QLineEdit(); self._faction_webhook.setPlaceholderText("Faction URL")
        self._faction_webhook_min = QSpinBox(); self._faction_webhook_min.setRange(0, 999)
        form5.addRow("Enemy URL:", self._enemy_webhook)
        form5.addRow("Enemy min count:", self._enemy_webhook_min)
        form5.addRow("Faction URL:", self._faction_webhook)
        form5.addRow("Faction min count:", self._faction_webhook_min)
        snd.addWidget(box5)

        # Custom sounds
        box6, form6 = _group("Custom Sounds")
        self._alarm_sound_path = ""
        self._faction_sound_path = ""
        self._alarm_sound_label = QLabel("(bundled default)")
        self._alarm_sound_label.setProperty("class", "muted")
        self._faction_sound_label = QLabel("(bundled default)")
        self._faction_sound_label.setProperty("class", "muted")

        alarm_row = QHBoxLayout()
        alarm_row.addWidget(self._alarm_sound_label, 1)
        alarm_browse = QPushButton("Browse…"); alarm_browse.clicked.connect(self._browse_alarm_sound)
        alarm_clear = QPushButton("Clear"); alarm_clear.clicked.connect(self._clear_alarm_sound)
        alarm_row.addWidget(alarm_browse); alarm_row.addWidget(alarm_clear)

        faction_row = QHBoxLayout()
        faction_row.addWidget(self._faction_sound_label, 1)
        faction_browse = QPushButton("Browse…"); faction_browse.clicked.connect(self._browse_faction_sound)
        faction_clear = QPushButton("Clear"); faction_clear.clicked.connect(self._clear_faction_sound)
        faction_row.addWidget(faction_browse); faction_row.addWidget(faction_clear)

        form6.addRow("Alarm:", alarm_row)
        form6.addRow("Faction:", faction_row)
        snd.addWidget(box6)

        # Hotkeys
        box7, form7 = _group("Hotkeys")
        self._hotkey_alert = QLineEdit(); self._hotkey_alert.setMaximumWidth(80)
        self._hotkey_faction = QLineEdit(); self._hotkey_faction.setMaximumWidth(80)
        form7.addRow("Alert Region:", self._hotkey_alert)
        form7.addRow("Faction Region:", self._hotkey_faction)
        snd.addWidget(box7)

    # ── Non-registry: Intel & ESI tab ─────────────────────────────────

    def _build_intel_sections(self) -> None:
        intel = self._tab_layouts["Intel & ESI"]

        # ESI OAuth (non-registry parts: client_id, login/logout, status)
        box, form = _group("EVE SSO / ESI OAuth")
        self._esi_client_id = QLineEdit(); self._esi_client_id.setPlaceholderText("Leave blank for built-in public client")
        self._esi_status_label = QLabel("Not authenticated"); self._esi_status_label.setProperty("class", "muted")
        btn_row = QHBoxLayout()
        self._esi_login_btn = QPushButton("Login with EVE SSO")
        self._esi_login_btn.setProperty("class", "primary")
        self._esi_login_btn.clicked.connect(self._esi_login)
        self._esi_logout_btn = QPushButton("Logout")
        self._esi_logout_btn.clicked.connect(self._esi_logout)
        btn_row.addWidget(self._esi_login_btn); btn_row.addWidget(self._esi_logout_btn); btn_row.addStretch()
        form.addRow("Client ID:", self._esi_client_id)
        form.addRow("Status:", self._esi_status_label)
        form.addRow("", btn_row)
        intel.addWidget(box)

        # KOS custom URLs (non-registry list stored as comma-separated)
        box2, form2 = _group("KOS Checker — Custom URLs")
        self._kos_custom_urls = QLineEdit(); self._kos_custom_urls.setPlaceholderText("https://url1, https://url2, …")
        form2.addRow("Custom URLs:", self._kos_custom_urls)
        intel.addWidget(box2)

        # Fleet tracked character IDs
        box3, form3 = _group("Fleet Context — Tracked Characters")
        self._fleet_char_ids = QLineEdit(); self._fleet_char_ids.setPlaceholderText("Character IDs, comma-separated")
        form3.addRow("Character IDs:", self._fleet_char_ids)
        intel.addWidget(box3)

    # ── Registry-driven controls ───────────────────────────────────────

    def _build_registry_controls(self) -> None:
        for spec in FIELDS:
            form = self._get_section(spec.tab, spec.section)
            if spec.kind == "bool":
                w: QWidget = QCheckBox(spec.label)
                form.addRow(w)
            elif spec.kind == "int":
                w = QSpinBox()
                w.setRange(0, 10_000_000)
                form.addRow(spec.label + ":", w)
            elif spec.kind == "float":
                w = QDoubleSpinBox()
                w.setRange(0, 10_000_000)
                w.setDecimals(2)
                form.addRow(spec.label + ":", w)
            else:  # str
                w = QLineEdit()
                w.setMinimumWidth(300)
                form.addRow(spec.label + ":", w)
            self._controls[spec.path] = w

    def _build_ocr_check_button(self) -> None:
        """Append a Tesseract health-check row to the OCR Name Detection section."""
        key = "Intel & ESI/OCR Name Detection"
        if key not in self._sections:
            return  # section not created yet (no OCR fields in FIELDS)
        form = self._sections[key][1]

        self._tesseract_status = QLabel("Not checked")
        self._tesseract_status.setProperty("class", "muted")

        btn = QPushButton("Check Tesseract")
        btn.clicked.connect(self._check_tesseract)

        row = QHBoxLayout()
        row.addWidget(btn)
        row.addWidget(self._tesseract_status, 1)
        form.addRow("Status:", row)

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Populate all widgets from the current on-disk settings."""
        settings = self._store.load()

        # Profiles
        profiles = settings.get("profiles", {})
        active = settings.get("active_profile", "Default")
        names = list(profiles.keys()) if profiles else ["Default"]
        if active not in names:
            names.insert(0, active)
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        self._profile_combo.addItems(names)
        self._profile_combo.setCurrentText(active)
        self._profile_combo.blockSignals(False)

        # Detection regions
        r1 = settings.get("alert_region_1", {}); r2 = settings.get("alert_region_2", {})
        self._alert_x1.setValue(int(r1.get("x", 0))); self._alert_y1.setValue(int(r1.get("y", 0)))
        self._alert_x2.setValue(int(r2.get("x", 0))); self._alert_y2.setValue(int(r2.get("y", 0)))
        f1 = settings.get("faction_region_1", {}); f2 = settings.get("faction_region_2", {})
        self._faction_x1.setValue(int(f1.get("x", 0))); self._faction_y1.setValue(int(f1.get("y", 0)))
        self._faction_x2.setValue(int(f2.get("x", 0))); self._faction_y2.setValue(int(f2.get("y", 0)))

        # Thresholds
        self._enemy_threshold.setValue(int(settings.get("detectionscale", {}).get("value", 90)))
        self._faction_threshold.setValue(int(settings.get("faction_scale", {}).get("value", 90)))

        # Log level
        self._log_level.setCurrentText(settings.get("log_level", "INFO"))

        # Alerts & Sound
        server = settings.get("server", {})
        self._system_name.setText(server.get("system", ""))
        self._mute.setChecked(bool(server.get("mute", False)))
        self._volume.setValue(int(settings.get("volume", {}).get("value", 100)))
        self._cooldown.setValue(int(settings.get("cooldown_timer", {}).get("value", 30)))
        self._cooldown_enemy.setValue(int(settings.get("cooldown_timer_enemy", {}).get("value", 30)))
        self._cooldown_faction.setValue(int(settings.get("cooldown_timer_faction", {}).get("value", 30)))
        self._webhook_url.setText(server.get("webhook", ""))
        self._webhook_template.setText(server.get("webhook_template", ""))
        wh = settings.get("webhooks", {})
        self._enemy_webhook.setText(wh.get("enemy", {}).get("url", ""))
        self._enemy_webhook_min.setValue(int(wh.get("enemy", {}).get("min_count", 0)))
        self._faction_webhook.setText(wh.get("faction", {}).get("url", ""))
        self._faction_webhook_min.setValue(int(wh.get("faction", {}).get("min_count", 0)))
        sounds = settings.get("sounds", {})
        self._alarm_sound_path = sounds.get("alarm", "")
        self._faction_sound_path = sounds.get("faction", "")
        self._alarm_sound_label.setText(os.path.basename(self._alarm_sound_path) if self._alarm_sound_path else "(bundled default)")
        self._faction_sound_label.setText(os.path.basename(self._faction_sound_path) if self._faction_sound_path else "(bundled default)")
        hk = settings.get("hotkeys", {})
        self._hotkey_alert.setText(hk.get("alert_region", "f1"))
        self._hotkey_faction.setText(hk.get("faction_region", "f2"))

        # Intel & ESI (non-registry)
        esi_oauth = settings.get("esi_oauth", {})
        self._esi_client_id.setText(esi_oauth.get("client_id", ""))
        self._refresh_esi_status()
        kos = settings.get("kos", {})
        self._kos_custom_urls.setText(", ".join(kos.get("custom_urls", [])))
        fleet = settings.get("fleet", {})
        self._fleet_char_ids.setText(", ".join(str(i) for i in fleet.get("tracked_character_ids", [])))

        # Registry controls
        for spec in FIELDS:
            w = self._controls.get(spec.path)
            if w is None:
                continue
            value = _get_by_path(settings, spec.path, spec.default)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(value if isinstance(value, (int, float)) else spec.default)
            elif isinstance(w, QLineEdit):
                w.setText(str(value))

    def _collect(self) -> dict:
        """Read all widgets into a patch dict (does NOT load from disk)."""
        patch: dict = {}

        # Detection regions
        patch["alert_region_1"] = {"x": self._alert_x1.value(), "y": self._alert_y1.value()}
        patch["alert_region_2"] = {"x": self._alert_x2.value(), "y": self._alert_y2.value()}
        patch["faction_region_1"] = {"x": self._faction_x1.value(), "y": self._faction_y1.value()}
        patch["faction_region_2"] = {"x": self._faction_x2.value(), "y": self._faction_y2.value()}
        patch["detectionscale"] = {"value": self._enemy_threshold.value()}
        patch["faction_scale"] = {"value": self._faction_threshold.value()}
        patch["log_level"] = self._log_level.currentText()

        # Alerts & Sound
        patch["server"] = {
            "system": self._system_name.text(),
            "mute": self._mute.isChecked(),
            "webhook": self._webhook_url.text().strip(),
            "webhook_template": self._webhook_template.text().strip()
                or "{alarm_type} detected in {system} at {time} (session #{count})",
        }
        patch["volume"] = {"value": self._volume.value()}
        patch["cooldown_timer"] = {"value": self._cooldown.value()}
        patch["cooldown_timer_enemy"] = {"value": self._cooldown_enemy.value()}
        patch["cooldown_timer_faction"] = {"value": self._cooldown_faction.value()}
        patch["webhooks"] = {
            "enemy": {"url": self._enemy_webhook.text().strip(), "min_count": self._enemy_webhook_min.value()},
            "faction": {"url": self._faction_webhook.text().strip(), "min_count": self._faction_webhook_min.value()},
        }
        patch["sounds"] = {"alarm": self._alarm_sound_path, "faction": self._faction_sound_path}
        patch["hotkeys"] = {
            "alert_region": self._hotkey_alert.text().strip().lower() or "f1",
            "faction_region": self._hotkey_faction.text().strip().lower() or "f2",
        }

        # Intel & ESI (non-registry)
        patch.setdefault("esi_oauth", {})["client_id"] = self._esi_client_id.text().strip()
        custom_urls = [u.strip() for u in self._kos_custom_urls.text().split(",") if u.strip()]
        patch.setdefault("kos", {})["custom_urls"] = custom_urls
        char_ids = [int(c.strip()) for c in self._fleet_char_ids.text().split(",") if c.strip().isdigit()]
        patch.setdefault("fleet", {})["tracked_character_ids"] = char_ids

        # Registry controls
        for spec in FIELDS:
            w = self._controls.get(spec.path)
            if w is None:
                continue
            if isinstance(w, QCheckBox):
                value = w.isChecked()
            elif isinstance(w, QSpinBox):
                value = w.value()
            elif isinstance(w, QDoubleSpinBox):
                value = w.value()
            else:
                value = w.text().strip()
            _set_by_path(patch, spec.path, value)

        return patch

    def _save_and_apply(self) -> None:
        try:
            settings = self._store.load()
            patch = self._collect()
            settings = _deep_merge(settings, patch)
            # Preserve active_profile from UI
            settings["active_profile"] = self._profile_combo.currentText()
            self._store.save(settings)
            # Tell MainWindow to refresh context line
            parent = self.parent()
            if hasattr(parent, "refresh_context_line"):
                parent.refresh_context_line()
        except Exception as e:
            QMessageBox.critical(self, "Settings Error", f"Could not save: {e}")

    def _apply_only(self) -> None:
        self._save_and_apply()

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def _save_profile(self) -> None:
        name = self._profile_combo.currentText()
        if not name or name == "Default":
            QMessageBox.warning(self, "Profile", "Cannot save to 'Default'. Create a named profile first.")
            return
        settings = self._store.load()
        settings.setdefault("profiles", {})[name] = self._collect()
        self._store.save(settings)

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        settings = self._store.load()
        settings.setdefault("profiles", {})[name] = {}
        settings["active_profile"] = name
        self._store.save(settings)
        self.load()

    def _load_profile(self) -> None:
        name = self._profile_combo.currentText()
        settings = self._store.load()
        settings["active_profile"] = name
        self._store.save(settings)
        self.load()

    def _delete_profile(self) -> None:
        name = self._profile_combo.currentText()
        if name == "Default":
            QMessageBox.warning(self, "Profile", "Cannot delete the Default profile.")
            return
        reply = QMessageBox.question(self, "Delete Profile", f"Delete profile '{name}'?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        settings = self._store.load()
        settings.get("profiles", {}).pop(name, None)
        settings["active_profile"] = "Default"
        self._store.save(settings)
        self.load()

    # ------------------------------------------------------------------
    # Sound browse
    # ------------------------------------------------------------------

    def _browse_alarm_sound(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Alarm Sound", "", "WAV files (*.wav)")
        if path:
            self._alarm_sound_path = path
            self._alarm_sound_label.setText(os.path.basename(path))

    def _clear_alarm_sound(self) -> None:
        self._alarm_sound_path = ""
        self._alarm_sound_label.setText("(bundled default)")

    def _browse_faction_sound(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Faction Sound", "", "WAV files (*.wav)")
        if path:
            self._faction_sound_path = path
            self._faction_sound_label.setText(os.path.basename(path))

    def _clear_faction_sound(self) -> None:
        self._faction_sound_path = ""
        self._faction_sound_label.setText("(bundled default)")

    # ------------------------------------------------------------------
    # ESI OAuth
    # ------------------------------------------------------------------

    def _refresh_esi_status(self) -> None:
        try:
            from evealert.tools.esi_auth import get_esi_auth  # noqa: PLC0415
            auth = get_esi_auth()
            if auth.is_authenticated:
                self._esi_status_label.setText(f"✓ {auth.character_name}")
            else:
                self._esi_status_label.setText("Not authenticated")
        except Exception:
            self._esi_status_label.setText("ESI unavailable")

    def _esi_login(self) -> None:
        client_id = self._esi_client_id.text().strip()
        if not client_id:
            self._esi_status_label.setText(
                "Enter Client ID first (developers.eveonline.com)"
            )
            return
        from evealert.tools.esi_auth import get_esi_auth  # noqa: PLC0415
        self._login_thread = _LoginThread(get_esi_auth(client_id))
        self._login_thread.finished.connect(self._on_login_done)
        self._esi_login_btn.setEnabled(False)
        self._esi_status_label.setText("Waiting for browser login\u2026")
        self._login_thread.start()

    def _on_login_done(self, ok: bool, name_or_err: str) -> None:
        self._esi_login_btn.setEnabled(True)
        if ok:
            self._esi_status_label.setText(f"\u2713 {name_or_err}")
        else:
            self._esi_status_label.setText("Not authenticated")
            if name_or_err:
                QMessageBox.warning(self, "EVE SSO", f"Login failed: {name_or_err}")

    def _esi_logout(self) -> None:
        try:
            from evealert.tools.esi_auth import get_esi_auth  # noqa: PLC0415
            get_esi_auth().logout()
            self._refresh_esi_status()
        except Exception as e:
            QMessageBox.warning(self, "EVE SSO", f"Logout error: {e}")

    # ------------------------------------------------------------------
    # Threshold editor (Phase 6)
    # ------------------------------------------------------------------

    def _open_threshold_editor(self) -> None:
        try:
            from evealert.ui.threshold_editor import ThresholdEditorDialog  # noqa: PLC0415
            dlg = ThresholdEditorDialog(self, self._store)
            dlg.exec()
        except Exception as e:
            QMessageBox.information(self, "Threshold Editor", f"Coming in Phase 6: {e}")

    def _build_tts_check_button(self) -> None:
        """Append a TTS health-check row + Test button to the Text-to-Speech section."""
        key = "Alerts & Sound/Text-to-Speech"
        if key not in self._sections:
            return
        form = self._sections[key][1]

        self._tts_status = QLabel("Not checked")
        self._tts_status.setProperty("class", "muted")

        btn_check = QPushButton("Check TTS")
        btn_check.clicked.connect(self._check_tts)

        btn_test = QPushButton("Test")
        btn_test.clicked.connect(self._test_tts)

        row = QHBoxLayout()
        row.addWidget(btn_check)
        row.addWidget(btn_test)
        row.addWidget(self._tts_status, 1)
        form.addRow("Status:", row)

    # ------------------------------------------------------------------
    # TTS check
    # ------------------------------------------------------------------

    def _check_tts(self) -> None:
        """Test whether pyttsx3 is available and can initialise."""
        try:
            from evealert.tools.tts import is_tts_available  # noqa: PLC0415
            if is_tts_available():
                self._tts_status.setText("\u2713 TTS ready (pyttsx3)")
                self._tts_status.setStyleSheet("color: #3FB950;")
            else:
                self._tts_status.setText("\u2717 pyttsx3 not available  (pip install pyttsx3)")
                self._tts_status.setStyleSheet("color: #F85149;")
        except Exception as e:
            self._tts_status.setText(f"\u2717 {e}")
            self._tts_status.setStyleSheet("color: #F85149;")

    def _test_tts(self) -> None:
        """Speak a sample phrase to test TTS output."""
        try:
            from evealert.tools.tts import speak  # noqa: PLC0415
            speak("EVE Alert — text to speech test", rate=175)
            self._tts_status.setText("Speaking\u2026")
            self._tts_status.setStyleSheet("")
        except Exception as e:
            self._tts_status.setText(f"\u2717 {e}")
            self._tts_status.setStyleSheet("color: #F85149;")

    # ------------------------------------------------------------------
    # Tesseract / OCR check
    # ------------------------------------------------------------------

    def _check_tesseract(self) -> None:
        """Test whether Tesseract is installed and report the version."""
        try:
            import pytesseract  # noqa: PLC0415
            from evealert.tools.ocr_local import reset_availability_cache  # noqa: PLC0415

            reset_availability_cache()  # force a fresh probe (may have been installed since last check)
            version = pytesseract.get_tesseract_version()
            msg = f"✓ Tesseract {version} — OCR ready"
            self._tesseract_status.setText(msg)
            self._tesseract_status.setProperty("class", "")
            self._tesseract_status.setStyleSheet("color: #3FB950;")  # SUCCESS green
        except ImportError:
            self._tesseract_status.setText("✗ pytesseract not installed  (pip install pytesseract)")
            self._tesseract_status.setStyleSheet("color: #F85149;")
        except Exception as exc:
            msg = str(exc)
            if "tesseract" in msg.lower() or "not found" in msg.lower():
                self._tesseract_status.setText(
                    "✗ Tesseract engine not found — install from https://github.com/tesseract-ocr/tesseract"
                )
            else:
                self._tesseract_status.setText(f"✗ {msg}")
            self._tesseract_status.setStyleSheet("color: #F85149;")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_dialog(self) -> None:
        self.load()
        self.show()
        self.raise_()
        self.activateWindow()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge *patch* values into *base*, returning a new dict."""
    result = dict(base)
    for k, v in patch.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
