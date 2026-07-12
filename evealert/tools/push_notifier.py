"""Push notification dispatcher for EVE Alert.

Supports Telegram Bot API and Pushover/ntfy.sh for mobile push alerts.
All channels use httpx (already a dependency) — no new packages required.

v3.5 #84: Telegram push notifications
v3.5 #85: Pushover / ntfy.sh mobile push
"""

import logging

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.push")

_HTTP_TIMEOUT = 6.0


class PushNotifier:
    """Send alarm messages to Telegram and/or Pushover/ntfy."""

    def __init__(
        self,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        pushover_user: str = "",
        pushover_token: str = "",
        ntfy_url: str = "",
    ) -> None:
        self._telegram_token = telegram_token.strip()
        self._telegram_chat_id = telegram_chat_id.strip()
        self._pushover_user = pushover_user.strip()
        self._pushover_token = pushover_token.strip()
        self._ntfy_url = ntfy_url.strip()

    def is_configured(self) -> bool:
        return bool(
            (self._telegram_token and self._telegram_chat_id)
            or (self._pushover_user and self._pushover_token)
            or self._ntfy_url
        )

    async def send(self, message: str, title: str = "EVE Alert") -> None:
        """Send *message* to all configured push channels (fire-and-forget)."""
        if not _HTTPX_AVAILABLE:
            return
        tasks = []
        if self._telegram_token and self._telegram_chat_id:
            tasks.append(self._send_telegram(message))
        if self._pushover_user and self._pushover_token:
            tasks.append(self._send_pushover(message, title))
        if self._ntfy_url:
            tasks.append(self._send_ntfy(message, title))

        import asyncio  # pylint: disable=import-outside-toplevel

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.debug("Push notification error: %s", r)

    async def _send_telegram(self, message: str) -> None:
        url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
        # HTML-escape the body: EVE names can contain <, >, & which would
        # otherwise make Telegram reject the message with HTTP 400 (#106).
        import html  # pylint: disable=import-outside-toplevel

        payload = {
            "chat_id": self._telegram_chat_id,
            "text": html.escape(message),
            "parse_mode": "HTML",
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            # Sanitize errors: the bot token is in the URL path, and an
            # httpx error's string includes the URL. Raise a token-free
            # error so it never reaches the logger in send() (#105).
            if resp.status_code >= 400:
                raise RuntimeError(f"Telegram send failed: HTTP {resp.status_code}")

    async def _send_pushover(self, message: str, title: str) -> None:
        url = "https://api.pushover.net/1/messages.json"
        payload = {
            "token": self._pushover_token,
            "user": self._pushover_user,
            "title": title,
            "message": message,
            "priority": 1,  # high priority
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, data=payload)
            resp.raise_for_status()

    async def _send_ntfy(self, message: str, title: str) -> None:
        # Vet the user-supplied URL to avoid SSRF to loopback/metadata/private
        # hosts (#105).
        from evealert.tools.net_safety import (  # pylint: disable=import-outside-toplevel
            is_safe_public_url,
        )

        if not is_safe_public_url(self._ntfy_url):
            logger.warning("Refusing ntfy URL (must be https + public host).")
            return
        headers = {"Title": title, "Priority": "high", "Tags": "warning"}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(self._ntfy_url, content=message, headers=headers)
            resp.raise_for_status()


_notifier: PushNotifier | None = None


def get_push_notifier(**kwargs) -> PushNotifier:
    global _notifier
    if _notifier is None or kwargs:
        _notifier = PushNotifier(**kwargs)
    return _notifier
