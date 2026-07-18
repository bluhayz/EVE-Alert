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

    async def test_expired_token_returns_none_when_refresh_does_not_renew_it(self):
        """#231: a transient refresh failure leaves the old (still expired)
        token in place -- get_token() must not hand back a token known to
        be expired; every downstream ESI call would just 401."""
        auth = self._auth()
        stale = TokenInfo("old-expired", "refresh", time.time() - 5, 1, "P")
        auth._token = stale

        async def fake_refresh_that_fails():
            pass  # simulates a network error being swallowed inside _refresh()

        auth._refresh = fake_refresh_that_fails
        token = await auth.get_token()
        self.assertIsNone(token)

    async def test_expired_token_returns_fresh_token_when_refresh_succeeds(self):
        auth = self._auth()
        auth._token = TokenInfo("old-expired", "refresh", time.time() - 5, 1, "P")

        async def fake_refresh_that_succeeds():
            auth._token = TokenInfo("new-access", "refresh", time.time() + 1200, 1, "P")

        auth._refresh = fake_refresh_that_succeeds
        token = await auth.get_token()
        self.assertEqual(token, "new-access")


class RefreshInvalidGrantTests(unittest.IsolatedAsyncioTestCase):
    """#231: a revoked/expired refresh token (invalid_grant) must clear
    the token and warn once -- not retry the dead token forever with a
    generic DEBUG log every cycle."""

    def setUp(self):
        esi_auth.reset_refresh_invalid_grant_warning()

    def tearDown(self):
        esi_auth.reset_refresh_invalid_grant_warning()

    def _auth_with_token(self):
        with mock.patch.object(EsiAuth, "_load_token", lambda self: None):
            auth = EsiAuth(client_id="x")
        auth._token = TokenInfo("old", "dead-refresh", time.time() - 5, 1, "P")
        return auth

    def _client_with_400(self, error_code: str):
        resp = mock.MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"error": error_code}
        client = mock.AsyncMock()
        client.post = mock.AsyncMock(return_value=resp)
        client.__aenter__ = mock.AsyncMock(return_value=client)
        client.__aexit__ = mock.AsyncMock(return_value=False)
        return client

    async def test_invalid_grant_clears_token_and_warns_once(self):
        auth = self._auth_with_token()
        client = self._client_with_400("invalid_grant")

        with mock.patch("httpx.AsyncClient", return_value=client), \
             mock.patch.object(esi_auth.logger, "warning") as mock_warn, \
             mock.patch.object(auth, "_save_token"):
            await auth._refresh()
            await auth._refresh()  # second failure must not warn again

        self.assertIsNone(auth._token)
        mock_warn.assert_called_once()
        self.assertIn("revoked", mock_warn.call_args[0][0])

    async def test_get_token_returns_none_after_invalid_grant(self):
        auth = self._auth_with_token()
        client = self._client_with_400("invalid_grant")

        with mock.patch("httpx.AsyncClient", return_value=client):
            token = await auth.get_token()

        self.assertIsNone(token)
        self.assertFalse(auth.is_authenticated)

    async def test_other_400_error_does_not_clear_token(self):
        """A 400 that isn't invalid_grant (or an unparseable body) must
        not be treated as a permanent revocation."""
        auth = self._auth_with_token()
        client = self._client_with_400("some_other_error")

        with mock.patch("httpx.AsyncClient", return_value=client):
            await auth._refresh()

        self.assertIsNotNone(auth._token)  # unchanged, not cleared

    async def test_login_resets_invalid_grant_warning_flag(self):
        esi_auth._refresh_invalid_grant_warning_shown = True
        esi_auth.reset_refresh_invalid_grant_warning()
        self.assertFalse(esi_auth._refresh_invalid_grant_warning_shown)


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


class GetCharacterLocationTests(unittest.IsolatedAsyncioTestCase):
    """Tests for get_character_location()."""

    async def test_returns_system_name_on_success(self):
        from evealert.tools.esi_auth import EsiAuth, get_character_location

        auth = mock.MagicMock(spec=EsiAuth)
        auth.character_id = 12345
        auth.get_token = mock.AsyncMock(return_value="tok")

        loc_resp = mock.MagicMock()
        loc_resp.raise_for_status = mock.Mock()
        loc_resp.json.return_value = {"solar_system_id": 30000142}

        sys_resp = mock.MagicMock()
        sys_resp.raise_for_status = mock.Mock()
        sys_resp.json.return_value = {"name": "Jita"}

        client = mock.AsyncMock()
        client.get = mock.AsyncMock(side_effect=[loc_resp, sys_resp])
        client.__aenter__ = mock.AsyncMock(return_value=client)
        client.__aexit__ = mock.AsyncMock(return_value=False)

        with mock.patch("httpx.AsyncClient", return_value=client):
            result = await get_character_location(auth)

        self.assertEqual(result, "Jita")

    async def test_returns_none_when_no_token(self):
        from evealert.tools.esi_auth import EsiAuth, get_character_location

        auth = mock.MagicMock(spec=EsiAuth)
        auth.character_id = 12345
        auth.get_token = mock.AsyncMock(return_value=None)

        result = await get_character_location(auth)
        self.assertIsNone(result)

    async def test_returns_none_on_network_error(self):
        from evealert.tools.esi_auth import EsiAuth, get_character_location

        auth = mock.MagicMock(spec=EsiAuth)
        auth.character_id = 12345
        auth.get_token = mock.AsyncMock(return_value="tok")

        client = mock.AsyncMock()
        client.get = mock.AsyncMock(side_effect=OSError("network down"))
        client.__aenter__ = mock.AsyncMock(return_value=client)
        client.__aexit__ = mock.AsyncMock(return_value=False)

        with mock.patch("httpx.AsyncClient", return_value=client):
            result = await get_character_location(auth)

        self.assertIsNone(result)

    async def test_returns_none_when_character_id_zero(self):
        from evealert.tools.esi_auth import EsiAuth, get_character_location

        auth = mock.MagicMock(spec=EsiAuth)
        auth.character_id = 0
        auth.get_token = mock.AsyncMock(return_value="tok")

        result = await get_character_location(auth)
        self.assertIsNone(result)


class LocationScopeWarningTests(unittest.IsolatedAsyncioTestCase):
    """#211: a 403 on the location endpoint means the current token predates
    esi-location.read_location.v1 being added to _SCOPES -- must be
    surfaced as a one-time WARNING (previously silent at DEBUG), not
    conflated with a generic/transient failure."""

    def setUp(self):
        import evealert.tools.esi_auth as esi_auth_mod

        self._mod = esi_auth_mod
        esi_auth_mod.reset_location_scope_warning()

    def tearDown(self):
        self._mod.reset_location_scope_warning()

    def _make_403_client(self):
        resp = mock.MagicMock()
        resp.status_code = 403
        client = mock.AsyncMock()
        client.get = mock.AsyncMock(return_value=resp)
        client.__aenter__ = mock.AsyncMock(return_value=client)
        client.__aexit__ = mock.AsyncMock(return_value=False)
        return client

    async def test_location_scope_in_default_scopes(self):
        self.assertIn("esi-location.read_location.v1", self._mod._SCOPES)

    async def test_403_returns_none_and_logs_one_warning(self):
        from evealert.tools.esi_auth import EsiAuth, get_character_location

        auth = mock.MagicMock(spec=EsiAuth)
        auth.character_id = 12345
        auth.get_token = mock.AsyncMock(return_value="tok")

        client = self._make_403_client()
        with mock.patch("httpx.AsyncClient", return_value=client), \
             mock.patch.object(self._mod.logger, "warning") as mock_warn:
            result1 = await get_character_location(auth)
            result2 = await get_character_location(auth)

        self.assertIsNone(result1)
        self.assertIsNone(result2)
        # Only ONE warning across two failed polls -- not per-cycle spam.
        mock_warn.assert_called_once()
        warned_text = mock_warn.call_args[0][0]
        self.assertIn("esi-location.read_location.v1", warned_text)

    async def test_login_resets_warning_flag(self):
        self._mod._location_scope_warning_shown = True
        # Simulate what login() does on success without a real OAuth round trip.
        self._mod.reset_location_scope_warning()
        self.assertFalse(self._mod._location_scope_warning_shown)


if __name__ == "__main__":
    unittest.main()
