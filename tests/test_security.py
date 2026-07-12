"""Tests for security hardening (issue #105)."""

import os
import stat
import unittest
from unittest import mock

from evealert.tools.net_safety import is_safe_public_url


class SsrfGuardTests(unittest.TestCase):
    def test_public_https_allowed(self):
        self.assertTrue(is_safe_public_url("https://ntfy.sh/mytopic"))
        self.assertTrue(is_safe_public_url("https://kos.example.com/api/"))

    def test_http_rejected(self):
        self.assertFalse(is_safe_public_url("http://ntfy.sh/mytopic"))

    def test_loopback_and_metadata_rejected(self):
        self.assertFalse(is_safe_public_url("https://127.0.0.1/"))
        self.assertFalse(is_safe_public_url("https://localhost/x"))
        self.assertFalse(is_safe_public_url("https://169.254.169.254/latest/"))

    def test_private_ranges_rejected(self):
        self.assertFalse(is_safe_public_url("https://10.0.0.5/"))
        self.assertFalse(is_safe_public_url("https://192.168.1.1/"))
        self.assertFalse(is_safe_public_url("https://172.16.5.5/"))

    def test_internal_suffixes_rejected(self):
        self.assertFalse(is_safe_public_url("https://kos.internal/api"))
        self.assertFalse(is_safe_public_url("https://box.local/"))

    def test_garbage_rejected(self):
        self.assertFalse(is_safe_public_url("not a url"))
        self.assertFalse(is_safe_public_url(""))


class TokenFilePermissionTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX permission bits only")
    def test_saved_token_is_owner_only(self):
        import tempfile
        from pathlib import Path

        from evealert.tools.esi_auth import EsiAuth, TokenInfo

        with tempfile.TemporaryDirectory() as d:
            token_path = Path(d) / "esi_token.json"
            with (
                mock.patch(
                    "evealert.tools.esi_auth._token_path", return_value=token_path
                ),
                mock.patch.object(EsiAuth, "_load_token", lambda self: None),
            ):
                auth = EsiAuth(client_id="x")
                auth._token = TokenInfo("a", "r", 9e18, 1, "Pilot")
                auth._save_token()
                mode = stat.S_IMODE(os.stat(token_path).st_mode)
        self.assertEqual(mode, 0o600)


class OAuthStateTests(unittest.TestCase):
    def test_login_generates_random_state(self):
        import asyncio

        from evealert.tools.esi_auth import EsiAuth

        with mock.patch.object(EsiAuth, "_load_token", lambda self: None):
            auth = EsiAuth(client_id="x")
        captured = {}

        with (
            mock.patch(
                "evealert.tools.esi_auth.webbrowser.open",
                lambda url: captured.setdefault("url", url),
            ),
            mock.patch.object(auth, "_await_callback", return_value=None),
        ):
            asyncio.run(auth.login())

        self.assertTrue(auth._state)  # a state was generated
        self.assertNotEqual(auth._state, "evealert")  # not the old static value
        self.assertIn(f"state={auth._state}", captured["url"])


if __name__ == "__main__":
    unittest.main()
