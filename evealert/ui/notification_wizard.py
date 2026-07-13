"""Mobile notification setup wizard for EVE Alert (#149).

A guided QDialog that walks through configuring one of the three
supported push-notification providers (Telegram / Pushover / ntfy.sh)
with a live test step before saving.

Usage (from settings_dialog or a standalone button)::

    dlg = NotificationWizardDialog(parent, store)
    dlg.exec()
"""

from __future__ import annotations

import asyncio
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class NotificationWizardDialog(QDialog):
    """4-page guided wizard for setting up mobile push notifications."""

    _test_done = Signal(bool, str)  # (success, message)

    def __init__(self, parent, store) -> None:
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Notification Setup Wizard")
        self.setMinimumSize(480, 360)
        self._store = store
        self._provider: str = ""  # "telegram" | "pushover" | "ntfy"

        self._test_done.connect(self._on_test_result)
        self._build_ui()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # Navigation buttons
        nav = QHBoxLayout()
        self._btn_back = QPushButton("← Back")
        self._btn_next = QPushButton("Next →")
        self._btn_finish = QPushButton("Save & Close")
        self._btn_finish.setProperty("class", "primary")
        self._btn_back.clicked.connect(self._go_back)
        self._btn_next.clicked.connect(self._go_next)
        self._btn_finish.clicked.connect(self._finish)
        nav.addWidget(self._btn_back)
        nav.addStretch()
        nav.addWidget(self._btn_next)
        nav.addWidget(self._btn_finish)
        root.addLayout(nav)

        self._stack.addWidget(self._page_choose())    # 0
        self._stack.addWidget(self._page_telegram())  # 1
        self._stack.addWidget(self._page_pushover())  # 2
        self._stack.addWidget(self._page_ntfy())      # 3
        self._stack.addWidget(self._page_test())      # 4

        self._go_to(0)

    def _page_choose(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("<b>Step 1 — Choose a provider</b>"))
        layout.addWidget(QLabel(
            "EVE Alert can send push notifications to your phone when an "
            "alarm fires. Pick the provider you want to set up:"
        ))
        layout.addSpacing(12)

        for label, key, description in [
            ("Telegram", "telegram",
             "Free. Create a bot at t.me/BotFather. Best for personal alerts."),
            ("Pushover", "pushover",
             "One-time $5 app purchase. Reliable delivery, rich notifications."),
            ("ntfy.sh", "ntfy",
             "Free, open-source. Works with self-hosted servers too."),
        ]:
            btn = QPushButton(f"{label}  —  {description}")
            btn.setCheckable(False)
            btn.clicked.connect(lambda checked=False, k=key: self._select_provider(k))
            layout.addWidget(btn)

        layout.addStretch()
        return w

    def _page_telegram(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("<b>Step 2 — Telegram credentials</b>"))
        layout.addWidget(QLabel(
            "1. Open Telegram and message <b>@BotFather</b><br>"
            "2. Send <code>/newbot</code> and follow the instructions<br>"
            "3. Copy the bot token (looks like <code>123456:ABC-DEF...</code>)<br>"
            "4. Start a chat with your new bot, then message it once<br>"
            "5. Get your chat ID at: <code>https://api.telegram.org/bot&lt;token&gt;/getUpdates</code>"
        ))
        layout.addSpacing(8)

        self._tg_token = QLineEdit()
        self._tg_token.setPlaceholderText("Bot token, e.g. 123456:ABC-DEF1234ghIkl-zyx...")
        self._tg_chat = QLineEdit()
        self._tg_chat.setPlaceholderText("Chat ID, e.g. 987654321")
        layout.addWidget(QLabel("Bot Token:"))
        layout.addWidget(self._tg_token)
        layout.addWidget(QLabel("Chat ID:"))
        layout.addWidget(self._tg_chat)
        layout.addStretch()
        return w

    def _page_pushover(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("<b>Step 2 — Pushover credentials</b>"))
        layout.addWidget(QLabel(
            "1. Create an account at <b>pushover.net</b> and install the app ($5 one-time)<br>"
            "2. Your <b>User Key</b> is on your dashboard<br>"
            "3. Register a new application to get an <b>API Token</b>"
        ))
        layout.addSpacing(8)

        self._po_user = QLineEdit()
        self._po_user.setPlaceholderText("User Key")
        self._po_token = QLineEdit()
        self._po_token.setPlaceholderText("API Token")
        layout.addWidget(QLabel("User Key:"))
        layout.addWidget(self._po_user)
        layout.addWidget(QLabel("API Token:"))
        layout.addWidget(self._po_token)
        layout.addStretch()
        return w

    def _page_ntfy(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("<b>Step 2 — ntfy.sh setup</b>"))
        layout.addWidget(QLabel(
            "1. Install the ntfy app on your phone (iOS/Android)<br>"
            "2. Subscribe to a topic, e.g. <code>eve-alert-myname</code><br>"
            "3. Enter the full topic URL below:<br>"
            "   <code>https://ntfy.sh/eve-alert-myname</code><br>"
            "   (or your self-hosted server URL)"
        ))
        layout.addSpacing(8)

        self._ntfy_url = QLineEdit()
        self._ntfy_url.setPlaceholderText("https://ntfy.sh/your-topic")
        layout.addWidget(QLabel("Topic URL:"))
        layout.addWidget(self._ntfy_url)
        layout.addStretch()
        return w

    def _page_test(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("<b>Step 3 — Test your notification</b>"))
        self._test_status = QLabel("Press 'Send Test' to verify the connection.")
        self._test_status.setWordWrap(True)
        layout.addWidget(self._test_status)
        layout.addSpacing(8)

        btn_test = QPushButton("Send Test Notification")
        btn_test.clicked.connect(self._run_test)
        layout.addWidget(btn_test)
        layout.addStretch()
        return w

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_to(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._btn_back.setEnabled(index > 0)
        is_last = index == 4
        self._btn_next.setVisible(not is_last)
        self._btn_finish.setVisible(is_last)

    def _go_back(self) -> None:
        cur = self._stack.currentIndex()
        self._go_to(max(cur - 1, 0))

    def _go_next(self) -> None:
        cur = self._stack.currentIndex()
        if cur == 0:
            if not self._provider:
                QMessageBox.information(self, "Wizard", "Please choose a provider first.")
                return
            page_map = {"telegram": 1, "pushover": 2, "ntfy": 3}
            self._go_to(page_map[self._provider])
        elif cur in (1, 2, 3):
            self._go_to(4)
        else:
            self._go_to(cur + 1)

    def _select_provider(self, key: str) -> None:
        self._provider = key
        self._go_next()

    # ------------------------------------------------------------------
    # Test
    # ------------------------------------------------------------------

    def _run_test(self) -> None:
        self._test_status.setText("Sending test notification…")

        notifier = self._build_notifier()
        if not notifier:
            self._test_status.setText("No credentials entered.")
            return

        def _go() -> None:
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(notifier.send("EVE Alert — test notification 🚀"))
                loop.close()
                self._test_done.emit(True, "✓ Notification sent successfully!")
            except Exception as exc:
                self._test_done.emit(False, f"✗ Failed: {exc}")

        threading.Thread(target=_go, daemon=True, name="eve-alert-notif-test").start()

    def _on_test_result(self, success: bool, message: str) -> None:
        self._test_status.setText(message)
        self._test_status.setStyleSheet(
            "color: #3FB950;" if success else "color: #F85149;"
        )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _build_notifier(self):
        """Build a PushNotifier from current wizard state (or None if empty)."""
        try:
            from evealert.tools.push_notifier import PushNotifier  # noqa: PLC0415
            if self._provider == "telegram":
                return PushNotifier(
                    telegram_token=self._tg_token.text().strip(),
                    telegram_chat_id=self._tg_chat.text().strip(),
                )
            if self._provider == "pushover":
                return PushNotifier(
                    pushover_user=self._po_user.text().strip(),
                    pushover_token=self._po_token.text().strip(),
                )
            if self._provider == "ntfy":
                return PushNotifier(ntfy_url=self._ntfy_url.text().strip())
        except Exception:
            pass
        return None

    def _finish(self) -> None:
        """Write credentials to SettingsStore and close."""
        settings = self._store.load()
        push = settings.setdefault("push", {})

        if self._provider == "telegram":
            push["telegram_token"] = self._tg_token.text().strip()
            push["telegram_chat_id"] = self._tg_chat.text().strip()
        elif self._provider == "pushover":
            push["pushover_user_key"] = self._po_user.text().strip()
            push["pushover_api_token"] = self._po_token.text().strip()
        elif self._provider == "ntfy":
            push["ntfy_url"] = self._ntfy_url.text().strip()

        self._store.save()
        self.accept()
