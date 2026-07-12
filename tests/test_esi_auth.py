"""Tests for EVE SSO OAuth fixes (issues #104, #115)."""

import base64
import json
import unittest
from unittest import mock

import evealert.tools.esi_auth as esi_auth
from evealert.tools.esi_auth import (
    _DEFAULT_CLIENT_ID,
    _SCOPES,
    EsiAuth,
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
        auth = get_esi_auth(client_id="")  # blank must not clobber to placeholder
        self.assertEqual(auth._client_id, _DEFAULT_CLIENT_ID)


class PkceParamsTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_builds_pkce_challenge(self):
        esi_auth._auth = None
        with mock.patch.object(EsiAuth, "_load_token", lambda self: None):
            auth = EsiAuth(client_id="test-id")
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


if __name__ == "__main__":
    unittest.main()
