"""Plugin Manager dialog (#181, v8.0).

Lists every plugin discovered in the user plugins directory (name,
version, hook count, status) with per-plugin enable/disable and a
"Reset Quarantine" action for a plugin that tripped the 3-consecutive-
failure quarantine (see evealert.tools.plugin_loader).

State here is the live PluginManager singleton's in-memory state, not
settings.json -- enable/disable and quarantine resets take effect
immediately for the next hook call, but do not persist across an app
restart (a restart reloads every plugin file fresh and clears
quarantine, matching load_plugins()'s own "fresh start on reload"
behavior).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from evealert.tools.plugin_loader import get_plugin_manager


class PluginManagerDialog(QDialog):
    """View/enable/disable loaded plugins and reset quarantine."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Plugin Manager")
        self.setMinimumSize(560, 380)
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Plugins are .py files in your plugins folder -- see docs/PLUGINS.md "
            "for the hook reference. A plugin disabled here (or auto-quarantined "
            "after 3 consecutive errors on the same hook) stops receiving calls "
            "until re-enabled or the app restarts."
        ))
        help_label = root.itemAt(root.count() - 1).widget()
        help_label.setWordWrap(True)
        help_label.setProperty("class", "muted")

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Plugin", "Version", "Hooks", "Status"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        root.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        self._btn_toggle = QPushButton("Enable / Disable Selected")
        self._btn_toggle.clicked.connect(self._toggle_selected)
        self._btn_reset = QPushButton("Reset Quarantine")
        self._btn_reset.clicked.connect(self._reset_selected)
        btn_open_folder = QPushButton("Open Plugins Folder")
        btn_open_folder.clicked.connect(self._open_folder)
        btn_reload = QPushButton("Reload All")
        btn_reload.clicked.connect(self._reload_all)
        for b in (self._btn_toggle, self._btn_reset, btn_open_folder, btn_reload):
            btn_row.addWidget(b)
        btn_row.addStretch()
        root.addLayout(btn_row)

        footer = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addStretch()
        footer.addWidget(close_btn)
        root.addLayout(footer)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        records = get_plugin_manager().list_plugins()
        self._table.setRowCount(len(records))
        if not records:
            self._table.setRowCount(1)
            item = QTableWidgetItem(
                "No plugins found -- drop a .py file into your plugins folder."
            )
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(0, 0, item)
            self._table.setSpan(0, 0, 1, 4)
            return

        for row, record in enumerate(records):
            values = [record.name, record.version or "", ", ".join(record.hook_names), record.status]
            for col, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, record.name)
                self._table.setItem(row, col, item)

    def _selected_plugin_name(self) -> str | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _toggle_selected(self) -> None:
        name = self._selected_plugin_name()
        if not name:
            return
        pm = get_plugin_manager()
        record = pm.get_plugin(name)
        if record is None:
            return
        pm.set_enabled(name, not record.enabled)
        self._refresh()

    def _reset_selected(self) -> None:
        name = self._selected_plugin_name()
        if not name:
            return
        get_plugin_manager().reset_quarantine(name)
        self._refresh()

    def _reload_all(self) -> None:
        from evealert.settings.helper import get_user_plugins_path  # noqa: PLC0415

        get_plugin_manager().load_plugins(get_user_plugins_path())
        self._refresh()

    def _open_folder(self) -> None:
        from evealert.settings.helper import get_user_plugins_path  # noqa: PLC0415

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_user_plugins_path())))
