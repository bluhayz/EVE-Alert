"""Hostile Watchlist Manager dialog (#240, v7.3).

Edits settings["watchlist"] (pilots/corporations/alliances name lists),
mirroring the standings-manager list UX (#173): a simple add/remove list
per category, read-merge-write against the shared SettingsStore.
"""

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from evealert.settings.store import SettingsStore


class WatchlistManagerDialog(QDialog):
    """View/edit the hostile pilot/corporation/alliance watchlist (#240).

    Three independent lists, each backed by settings["watchlist"][key].
    IDs are resolved from these names once per engine session (see
    AlertAgent._resolve_watchlist_ids()) -- this dialog only edits names.
    """

    def __init__(self, parent, store: SettingsStore):
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Watchlist Manager")
        self.setMinimumSize(420, 520)
        self._store = store
        self._lists: dict[str, QListWidget] = {}
        self._entries: dict[str, QLineEdit] = {}
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Pilots, corporations, and alliances here get flagged "
            "[WATCHLIST] on Enemy alarms, add a point to the threat score, "
            "and (when the live killmail feed is enabled) are tracked "
            "anywhere in New Eden, not just nearby systems."
        ))
        help_label = root.itemAt(root.count() - 1).widget()
        help_label.setWordWrap(True)
        help_label.setProperty("class", "muted")

        for key, label in (
            ("pilots", "Pilots"),
            ("corporations", "Corporations"),
            ("alliances", "Alliances"),
        ):
            root.addWidget(QLabel(f"<b>{label}</b>"))
            list_widget = QListWidget()
            list_widget.setMaximumHeight(120)
            self._lists[key] = list_widget
            root.addWidget(list_widget)

            row = QHBoxLayout()
            entry = QLineEdit()
            entry.setPlaceholderText(f"Add a {label[:-1].lower()} name…")
            entry.returnPressed.connect(lambda k=key: self._add_entry(k))
            self._entries[key] = entry
            add_btn = QPushButton("Add")
            add_btn.clicked.connect(lambda _checked=False, k=key: self._add_entry(k))
            remove_btn = QPushButton("Remove Selected")
            remove_btn.clicked.connect(lambda _checked=False, k=key: self._remove_selected(k))
            row.addWidget(entry, 1)
            row.addWidget(add_btn)
            row.addWidget(remove_btn)
            root.addLayout(row)

        footer = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.setProperty("class", "primary")
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(save_btn)
        footer.addStretch()
        footer.addWidget(close_btn)
        root.addLayout(footer)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _load(self) -> None:
        settings = self._store.load()
        watchlist = settings.get("watchlist", {})
        for key, list_widget in self._lists.items():
            list_widget.clear()
            list_widget.addItems(sorted(watchlist.get(key, [])))

    def _add_entry(self, key: str) -> None:
        entry = self._entries[key]
        name = entry.text().strip()
        if not name:
            return
        list_widget = self._lists[key]
        existing = {list_widget.item(i).text().lower() for i in range(list_widget.count())}
        if name.lower() not in existing:
            list_widget.addItem(name)
        entry.clear()

    def _remove_selected(self, key: str) -> None:
        list_widget = self._lists[key]
        for item in list_widget.selectedItems():
            list_widget.takeItem(list_widget.row(item))

    def _collect(self) -> dict:
        return {
            key: [list_widget.item(i).text() for i in range(list_widget.count())]
            for key, list_widget in self._lists.items()
        }

    def _save(self) -> None:
        try:
            settings = self._store.load()
            settings["watchlist"] = self._collect()
            self._store.save(settings)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
