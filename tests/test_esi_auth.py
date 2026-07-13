"""Tests for EVE SSO OAuth fixes (issues #104, #115)."""

import base64
import json
import time
import unittest
from unittest import mock

import evealert.tools.esi_auth as esi_auth
from evealert.tools.esi_auth import (
    _DEFAULT_CLIENT_ID,
    _SCOPES,
    EsiAuth,
    TokenInfo,
    _decode_character_from_jwt,
    get_esi_auth,
)


def _make_fake_jwt(sub: str, name: str) -> str:
    """Build a JWT with an unsigned but structurally valid payload segment."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": sub, "name": name}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{header}.{payload}.signature"


class JwtDecodeTests(unittest.TestCase):
    def test_decode_character_from_jwt(self):
        token = _make_fake_jwt("CHARACTER:EVE:2112625428", "CCP Bartender")
        char_id, name = _decode_character_from_jwt(token)
        self.assertEqual(char_id, 2112625428)
        self.assertEqual(name, "CCP Bartender")

    def test_decode_malformed_jwt_returns_zero(self):
        char_id, name = _decode_character_from_jwt("not-a-jwt")
        self.assertEqual(char_id, 0)
        self.assertEqual(name, "")


class ScopeTests(unittest.TestCase):
    def test_structures_scope_present(self):
        # #104: structure fuel endpoint needs this scope
        self.assertIn("esi-corporations.read_structures.v1", _SCOPES)


class SingletonClientIdTests(unittest.TestCase):
    def setUp(self):
        # Reset the module singleton and avoid touching disk tokens.
        esi_auth._auth = None
        self._patch = mock.patch.object(EsiAuth, "_load_token", lambda self: None)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        esi_auth._auth = None

    def test_blank_client_id_falls_back_to_default(self):
        auth = get_esi_auth(client_id="")
        self.assertEqual(auth._client_id, _DEFAULT_CLIENT_ID)

    def test_custom_client_id_reconfigures_existing_singleton(self):
        first = get_esi_auth()  # builds singleton with default
        self.assertEqual(first._client_id, _DEFAULT_CLIENT_ID)
        second = get_esi_auth(client_id="my-dev-app-id")
        # Same instance, but client_id now updated (#115)
        self.assertIs(second, first)
        self.assertEqual(second._client_id, "my-dev-app-id")

    def test_none_client_id_does_not_override_existing(self):
        get_esi_auth(client_id="my-dev-app-id")
        auth = get_esi_auth(client_id="")  # blank leaves client_id as-is (empty = unconfigured)
        self.assertEqual(auth._client_id, _DEFAULT_CLIENT_ID)


class PkceParamsTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_builds_pkce_challenge(self):
        esi_auth._auth = None
        with mock.patch.object(EsiAuth, "_load_token", lambda self: None):
            auth = EsiAuth(client_id="a" * 32)  # valid 32-hex client ID
        opened = {}

        def fake_open(url):
            opened["url"] = url

        with (
            mock.patch("evealert.tools.esi_auth.webbrowser.open", fake_open),
            mock.patch.object(auth, "_await_callback", return_value=None),
        ):
            await auth.login()

        self.assertIn("code_challenge=", opened["url"])
        self.assertIn("code_challenge_method=S256", opened["url"])
        # A verifier must have been generated for the token exchange
        self.assertTrue(auth._code_verifier)


class GetTokenExpiryTests(unittest.IsolatedAsyncioTestCase):
    def _auth(self):
        with mock.patch.object(EsiAuth, "_load_token", lambda self: None):
            return EsiAuth(client_id="x")

    async def test_no_token_returns_none(self):
        auth = self._auth()
        auth._token = None
        self.assertIsNone(await auth.get_token())

    async def test_valid_token_returned_without_refresh(self):
        auth = self._auth()
        auth._token = TokenInfo("access-123", "refresh", time.time() + 600, 1, "P")
        refreshed = {"called": False}

        async def fake_refresh():
            refreshed["called"] = True

        auth._refresh = fake_refresh
        token = await auth.get_token()
        self.assertEqual(token, "access-123")
        self.assertFalse(refreshed["called"])

    async def test_expired_token_triggers_refresh(self):
        auth = self._auth()
        auth._token = TokenInfo("old", "refresh", time.time() + 5, 1, "P")  # within 30s
        refreshed = {"called": False}

        async def fake_refresh():
            refreshed["called"] = True

        auth._refresh = fake_refresh
        await auth.get_token()
        self.assertTrue(refreshed["called"])


class TokenRoundTripTests(unittest.TestCase):
    def test_save_then_load(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "esi_token.json"
            with mock.patch("evealert.tools.esi_auth._token_path", return_value=path):
                with mock.patch.object(EsiAuth, "_load_token", lambda self: None):
                    a = EsiAuth(client_id="x")
                a._token = TokenInfo("acc", "ref", 9e18, 42, "Pilot")
                a._save_token()
                # Fresh instance loads it back
                b = EsiAuth(client_id="x")
        self.assertEqual(b.character_id, 42)
        self.assertEqual(b.character_name, "Pilot")


class ClientIdValidationTests(unittest.TestCase):
    """login() must reject blank / malformed client IDs without opening a browser (#136)."""

    def _login_sync(self, client_id):
        import asyncio
        from evealert.tools.esi_auth import EsiAuth
        auth = EsiAuth(client_id=client_id)
        return asyncio.run(auth.login())

    def test_empty_client_id_returns_false(self):
        with mock.patch("webbrowser.open") as browser:
            result = self._login_sync("")
        self.assertFalse(result)
        browser.assert_not_called()

    def test_placeholder_client_id_returns_false(self):
        with mock.patch("webbrowser.open") as browser:
            result = self._login_sync("evealert_public_client")
        self.assertFalse(result)
        browser.assert_not_called()

    def test_non_hex_client_id_returns_false(self):
        with mock.patch("webbrowser.open") as browser:
            result = self._login_sync("not-hex-at-all")
        self.assertFalse(result)
        browser.assert_not_called()

    def test_valid_32hex_client_id_opens_browser(self):
        """A 32-hex client ID should attempt to open the browser."""
        import asyncio
        from evealert.tools.esi_auth import EsiAuth

        fake_id = "a" * 32  # valid 32-char lowercase hex
        auth = EsiAuth(client_id=fake_id)

        browser_calls = []

        async def fake_await_callback(timeout=120.0):
            return None  # simulate no callback (timeout)

        with mock.patch("webbrowser.open", side_effect=lambda url: browser_calls.append(url)):
            with mock.patch.object(auth, "_await_callback", fake_await_callback):
                result = asyncio.run(auth.login())

        self.assertEqual(len(browser_calls), 1)
        self.assertIn("login.eveonline.com", browser_calls[0])
        self.assertFalse(result)  # no callback arrived


if __name__ == "__main__":
    unittest.main()
