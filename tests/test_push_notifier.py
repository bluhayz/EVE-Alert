"""Tests for PushNotifier configuration + channel dispatch (issue #103)."""

import asyncio
import unittest
from unittest import mock

import respx
from httpx import Response

from evealert.tools.push_notifier import PushNotifier


class IsConfiguredTests(unittest.TestCase):
    def test_empty_is_not_configured(self):
        self.assertFalse(PushNotifier().is_configured())

    def test_telegram_needs_both_token_and_chat(self):
        self.assertFalse(PushNotifier(telegram_token="t").is_configured())
        self.assertFalse(PushNotifier(telegram_chat_id="c").is_configured())
        self.assertTrue(
            PushNotifier(telegram_token="t", telegram_chat_id="c").is_configured()
        )

    def test_pushover_needs_both_user_and_token(self):
        self.assertFalse(PushNotifier(pushover_user="u").is_configured())
        self.assertTrue(
            PushNotifier(pushover_user="u", pushover_token="t").is_configured()
        )

    def test_ntfy_url_alone_is_configured(self):
        self.assertTrue(PushNotifier(ntfy_url="https://ntfy.sh/x").is_configured())


class ChannelDispatchTests(unittest.TestCase):
    def test_send_dispatches_only_configured_channels(self):
        n = PushNotifier(ntfy_url="https://ntfy.sh/mytopic")
        with respx.mock:
            route = respx.post("https://ntfy.sh/mytopic").mock(
                return_value=Response(200)
            )
            asyncio.run(n.send("hello", "EVE Alert"))
        self.assertTrue(route.called)

    def test_ntfy_ssrf_url_is_refused(self):
        # A loopback ntfy URL must be skipped (no request made) (#105).
        n = PushNotifier(ntfy_url="https://127.0.0.1/topic")
        with respx.mock:
            route = respx.post("https://127.0.0.1/topic").mock(
                return_value=Response(200)
            )
            asyncio.run(n.send("hello"))
        self.assertFalse(route.called)

    def test_telegram_message_is_html_escaped(self):
        n = PushNotifier(telegram_token="tok", telegram_chat_id="42")
        captured = {}

        with respx.mock:

            def _capture(request):
                captured["content"] = request.content.decode()
                return Response(200, json={"ok": True})

            respx.post("https://api.telegram.org/bottok/sendMessage").mock(
                side_effect=_capture
            )
            asyncio.run(n.send("<script> & tags"))
        self.assertIn("&lt;script&gt;", captured["content"])
        self.assertNotIn("<script>", captured["content"])


if __name__ == "__main__":
    unittest.main()
