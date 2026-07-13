"""Profile manager dialog for EVE Alert (#166).

Provides an explicit, safe UI for viewing and editing settings profiles.
Replaces the implicit Save/New/Load/Delete bar in SettingsDialog.

Features:
  - Profile list (user profiles + read-only built-in space profiles)
  - Diff table showing which keys a profile overrides vs the current base
  - Actions: New, Duplicate, Rename, Delete, Set Active, Remove override
  - "Save current as profile" with checkbox selection of which keys to include
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from evealert.tools.space_profiles import PROFILES as _BUILTIN_PROFILES

_BUILTIN_LABEL = "(built-in)"


class ProfileManagerDialog(QDialog):
    """Manage user profiles and view built-in space profiles."""

    def __init__(self, parent, store) -> None:
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Profile Manager")
        self.setMinimumSize(700, 480)
        self._store = store
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        # Left: profile list
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Profiles</b>"))
        self._list = QListWidget()
        self._list.setMinimumWidth(180)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        left.addWidget(self._list, 1)

        # Action buttons
        actions = [
            ("New",       self._new_profile),
            ("Duplicate", self._duplicate_profile),
            ("Rename",    self._rename_profile),
            ("Delete",    self._delete_profile),
            ("Set Active",self._set_active),
        ]
        for label, slot in actions:
            btn = QPushButton(label)
            if label == "Delete":
                btn.setProperty("class", "danger")
            btn.clicked.connect(slot)
            left.addWidget(btn)
            setattr(self, f"_btn_{label.lower().replace(' ', '_')}", btn)

        save_btn = QPushButton("Save current settings as profile…")
        save_btn.clicked.connect(self._save_current_as_profile)
        left.addWidget(save_btn)

        root.addLayout(left)

        # Right: diff table
        right = QVBoxLayout()
        self._diff_label = QLabel("Select a profile to see its overrides.")
        self._diff_label.setProperty("class", "muted")
        right.addWidget(self._diff_label)

        self._diff_table = QTableWidget(0, 3)
        self._diff_table.setHorizontalHeaderLabels(["Setting key", "Base value", "Profile value"])
        self._diff_table.horizontalHeader().setStretchLastSection(True)
        self._diff_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._diff_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        right.addWidget(self._diff_table, 1)

        remove_btn = QPushButton("Remove selected override")
        remove_btn.clicked.connect(self._remove_override)
        self._btn_remove_override = remove_btn
        right.addWidget(remove_btn)

        root.addLayout(right, 2)

        # Close
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.accept)
        outer = QVBoxLayout()
        outer.addLayout(root, 1)
        outer.addWidget(btns)
        # Replace root as the dialog layout
        container = QWidget()
        container.setLayout(root)
        dialog_root = QVBoxLayout(self)
        dialog_root.addWidget(container, 1)
        dialog_root.addWidget(btns)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Reload the profile list from the store."""
        self._list.clear()
        settings = self._store.load_raw()
        active = settings.get("active_profile", "Default")

        # User profiles (Default is always present)
        user_profiles = list(settings.get("profiles", {}).keys())
        if "Default" not in user_profiles:
            user_profiles.insert(0, "Default")
        for name in user_profiles:
            item = QListWidgetItem(name + (" ✓" if name == active else ""))
            item.setData(Qt.ItemDataRole.UserRole, ("user", name))
            self._list.addItem(item)

        # Built-in space profiles (read-only)
        for key, profile in _BUILTIN_PROFILES.items():
            label = profile.get("label", key)
            item = QListWidgetItem(f"{label} {_BUILTIN_LABEL}")
            item.setData(Qt.ItemDataRole.UserRole, ("builtin", key))
            item.setForeground(Qt.GlobalColor.gray)
            self._list.addItem(item)

        self._update_button_states(None)

    def _current_kind_name(self) -> tuple[str, str] | tuple[None, None]:
        item = self._list.currentItem()
        if item is None:
            return None, None
        return item.data(Qt.ItemDataRole.UserRole)

    def _is_user_profile(self) -> bool:
        kind, _ = self._current_kind_name()
        return kind == "user"

    # ------------------------------------------------------------------
    # Diff table
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        kind, name = self._current_kind_name()
        if kind is None:
            self._diff_table.setRowCount(0)
            self._diff_label.setText("Select a profile to see its overrides.")
            self._update_button_states(kind)
            return

        base = self._store.load_raw()

        if kind == "user":
            overrides = base.get("profiles", {}).get(name, {})
        else:
            overrides = {k: v for k, v in _BUILTIN_PROFILES[name].items()
                         if k not in ("label", "description")}

        self._populate_diff(base, overrides)
        self._diff_label.setText(
            f"Profile <b>{name}</b> — {len(overrides)} override(s)"
            + (" (read-only)" if kind == "builtin" else "")
        )
        self._update_button_states(kind)

    def _populate_diff(self, base: dict, overrides: dict) -> None:
        self._diff_table.setRowCount(0)
        for row, (key, profile_val) in enumerate(overrides.items()):
            self._diff_table.insertRow(row)
            # Resolve base value via dotted path
            parts = key.split(".")
            base_val = base
            for p in parts:
                if isinstance(base_val, dict) and p in base_val:
                    base_val = base_val[p]
                else:
                    base_val = "(not set)"
                    break
            for col, text in enumerate([key, str(base_val), str(profile_val)]):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._diff_table.setItem(row, col, item)
        self._diff_table.resizeColumnsToContents()

    def _update_button_states(self, kind: str | None) -> None:
        is_user = kind == "user"
        for name in ("duplicate", "rename", "delete", "set_active", "remove_override"):
            btn = getattr(self, f"_btn_{name}", None)
            if btn:
                btn.setEnabled(is_user)

    # ------------------------------------------------------------------
    # Profile actions
    # ------------------------------------------------------------------

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        settings = self._store.load_raw()
        settings.setdefault("profiles", {})[name] = {}
        self._store.save(settings)
        self._refresh()

    def _duplicate_profile(self) -> None:
        kind, name = self._current_kind_name()
        if kind != "user":
            return
        new_name, ok = QInputDialog.getText(self, "Duplicate Profile", "New name:", text=f"{name} copy")
        if not ok or not new_name.strip():
            return
        settings = self._store.load_raw()
        original = settings.get("profiles", {}).get(name, {})
        settings.setdefault("profiles", {})[new_name.strip()] = copy.deepcopy(original)
        self._store.save(settings)
        self._refresh()

    def _rename_profile(self) -> None:
        kind, name = self._current_kind_name()
        if kind != "user" or name == "Default":
            return
        new_name, ok = QInputDialog.getText(self, "Rename Profile", "New name:", text=name)
        if not ok or not new_name.strip() or new_name.strip() == name:
            return
        settings = self._store.load_raw()
        profiles = settings.setdefault("profiles", {})
        profiles[new_name.strip()] = profiles.pop(name, {})
        if settings.get("active_profile") == name:
            settings["active_profile"] = new_name.strip()
        self._store.save(settings)
        self._refresh()

    def _delete_profile(self) -> None:
        kind, name = self._current_kind_name()
        if kind != "user" or name == "Default":
            QMessageBox.warning(self, "Cannot delete", "The Default profile cannot be deleted.")
            return
        reply = QMessageBox.question(self, "Delete Profile", f"Delete profile '{name}'?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        settings = self._store.load_raw()
        settings.get("profiles", {}).pop(name, None)
        if settings.get("active_profile") == name:
            settings["active_profile"] = "Default"
        self._store.save(settings)
        self._refresh()

    def _set_active(self) -> None:
        kind, name = self._current_kind_name()
        if kind != "user":
            return
        settings = self._store.load_raw()
        settings["active_profile"] = name
        self._store.save(settings)
        self._refresh()

    def _remove_override(self) -> None:
        kind, name = self._current_kind_name()
        if kind != "user":
            return
        rows = self._diff_table.selectedItems()
        if not rows:
            return
        row = self._diff_table.row(rows[0])
        key = self._diff_table.item(row, 0).text()
        settings = self._store.load_raw()
        profile = settings.get("profiles", {}).get(name, {})
        profile.pop(key, None)
        self._store.save(settings)
        self._on_selection_changed()

    def _save_current_as_profile(self) -> None:
        """Snapshot selected changed-vs-base keys into a new (or existing) profile."""
        from evealert.settings.fields import FIELDS  # noqa: PLC0415

        base = self._store.load_raw()
        # Build a flat list of (path, current_value) pairs from all FIELDS
        changed: list[tuple[str, object]] = []
        for spec in FIELDS:
            parts = spec.path.split(".")
            node = base
            val = None
            ok = True
            for p in parts:
                if isinstance(node, dict) and p in node:
                    node = node[p]
                else:
                    ok = False
                    break
            if ok:
                val = node
            if val != spec.default:
                changed.append((spec.path, val))

        if not changed:
            QMessageBox.information(self, "Save as Profile",
                                    "No settings differ from defaults — nothing to save.")
            return

        name, ok = QInputDialog.getText(self, "Save as Profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()

        # Checkbox-list dialog: let user choose which keys to include
        from PySide6.QtWidgets import QCheckBox, QScrollArea, QDialogButtonBox as _DBB  # noqa: PLC0415
        dlg = QDialog(self)
        dlg.setWindowTitle("Choose keys to include")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Select which changed settings to include in the profile:"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        checkboxes = []
        for path, val in changed:
            cb = QCheckBox(f"{path} = {val!r}")
            cb.setChecked(True)
            cb.setProperty("_path", path)
            cb.setProperty("_val", val)
            inner_layout.addWidget(cb)
            checkboxes.append(cb)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)
        btns = _DBB(_DBB.StandardButton.Ok | _DBB.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        overrides: dict[str, object] = {}
        for cb in checkboxes:
            if cb.isChecked():
                overrides[cb.property("_path")] = cb.property("_val")

        if not overrides:
            return
        base.setdefault("profiles", {})[name] = overrides
        self._store.save(base)
        self._refresh()
