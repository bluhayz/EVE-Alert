"""EVE SSO OAuth2 authentication for EVE Alert.

Implements the EVE Online SSO OAuth2 authorization code flow:
  1. Open browser to ESI auth URL (with PKCE optional, or implicit)
  2. User logs in and authorises scopes
  3. EVE redirects to http://localhost:8888/callback with ?code=...
  4. Exchange code for access + refresh tokens
  5. Store refresh token in platformdirs config

Required scopes for v4.0 features:
  esi-characters.read_standings.v1    — personal standings (#95)
  esi-fleets.read_fleet.v1            — fleet membership (#96)
  esi-assets.read_assets.v1           — asset monitoring (#97)
  esi-corporations.read_structures.v1 — structure fuel (#97)
  publicData                          — ESI character info (no auth needed)

Usage:
  auth = EsiAuth()
  if not auth.is_authenticated:
      await auth.login()  # opens browser, waits for callback
  token = await auth.get_token()  # auto-refreshes if expired
"""

import asyncio
import json
import logging
import time
import webbrowser
from pathlib import Path
from typing import NamedTuple

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from platformdirs import user_config_dir

logger = logging.getLogger("alert.esi_auth")

_ESI_AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
_ESI_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
_ESI_VERIFY_URL = "https://esi.evetech.net/verify/"
_HTTP_TIMEOUT = 10.0
_REDIRECT_PORT = 8888
_REDIRECT_URI = f"http://localhost:{_REDIRECT_PORT}/callback"

# EVE Alert registered client ID (public, for installed apps — no secret)
# Users can override via settings if they have their own dev app.
_DEFAULT_CLIENT_ID = "evealert_public_client"

_SCOPES = " ".join(
    [
        "esi-characters.read_standings.v1",
        "esi-fleets.read_fleet.v1",
        "esi-assets.read_assets.v1",
        "publicData",
    ]
)


def _token_path() -> Path:
    config_dir = Path(user_config_dir("evealert"))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "esi_token.json"


class TokenInfo(NamedTuple):
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds
    character_id: int
    character_name: str


class EsiAuth:
    """Manages EVE SSO OAuth2 token lifecycle."""

    def __init__(self, client_id: str = _DEFAULT_CLIENT_ID) -> None:
        self._client_id = client_id
        self._token: TokenInfo | None = None
        self._load_token()

    @property
    def is_authenticated(self) -> bool:
        return self._token is not None

    @property
    def character_name(self) -> str:
        return self._token.character_name if self._token else ""

    @property
    def character_id(self) -> int:
        return self._token.character_id if self._token else 0

    async def login(self) -> bool:
        """Open browser and wait for OAuth callback. Returns True on success."""
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx not available — ESI auth disabled.")
            return False

        import urllib.parse  # pylint: disable=import-outside-toplevel

        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": _REDIRECT_URI,
            "scope": _SCOPES,
            "state": "evealert",
        }
        auth_url = f"{_ESI_AUTH_URL}?{urllib.parse.urlencode(params)}"
        webbrowser.open(auth_url)

        # Start a minimal HTTP server to capture the callback
        code = await self._await_callback()
        if not code:
            return False

        token = await self._exchange_code(code)
        if token:
            self._token = token
            self._save_token()
            return True
        return False

    async def get_token(self) -> str | None:
        """Return a valid access token, refreshing if necessary."""
        if self._token is None:
            return None
        if time.time() > self._token.expires_at - 30:
            await self._refresh()
        return self._token.access_token if self._token else None

    def logout(self) -> None:
        self._token = None
        p = _token_path()
        if p.exists():
            p.unlink()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _await_callback(self, timeout: float = 120.0) -> str | None:
        """Start a one-shot HTTP server and wait for the callback code."""
        code_holder: list[str] = []

        async def handle(reader, writer):
            try:
                data = await asyncio.wait_for(reader.read(2048), timeout=5.0)
                request = data.decode("utf-8", errors="replace")
                first_line = request.split("\r\n")[0]
                path = first_line.split(" ")[1] if " " in first_line else ""
                if "code=" in path:
                    import urllib.parse  # pylint: disable=import-outside-toplevel

                    qs = urllib.parse.parse_qs(path.split("?", 1)[-1])
                    code_holder.extend(qs.get("code", []))
                body = b"<html><body><h2>EVE Alert authorised. Return to the app.</h2></body></html>"
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + body
                )
                await writer.drain()
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", _REDIRECT_PORT)
        try:
            deadline = time.time() + timeout
            async with server:
                while not code_holder and time.time() < deadline:
                    await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug("OAuth callback server error: %s", exc)
        return code_holder[0] if code_holder else None

    async def _exchange_code(self, code: str) -> TokenInfo | None:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "redirect_uri": _REDIRECT_URI,
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(_ESI_TOKEN_URL, data=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug("Token exchange failed: %s", exc)
            return None
        return await self._build_token_info(data)

    async def _refresh(self) -> None:
        if not self._token:
            return
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._token.refresh_token,
            "client_id": self._client_id,
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(_ESI_TOKEN_URL, data=payload)
                resp.raise_for_status()
                data = resp.json()
            new_token = await self._build_token_info(data, self._token)
            if new_token:
                self._token = new_token
                self._save_token()
        except Exception as exc:
            logger.debug("Token refresh failed: %s", exc)

    async def _build_token_info(
        self, data: dict, existing: TokenInfo | None = None
    ) -> TokenInfo | None:
        access = data.get("access_token", "")
        refresh = data.get("refresh_token", existing.refresh_token if existing else "")
        expires_in = int(data.get("expires_in", 1199))
        expires_at = time.time() + expires_in

        # Verify token to get character info
        char_id = existing.character_id if existing else 0
        char_name = existing.character_name if existing else ""
        if not char_id and access:
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                    resp = await client.get(
                        _ESI_VERIFY_URL,
                        headers={"Authorization": f"Bearer {access}"},
                    )
                    resp.raise_for_status()
                    verify = resp.json()
                    char_id = verify.get("CharacterID", 0)
                    char_name = verify.get("CharacterName", "")
            except Exception:
                pass

        return TokenInfo(
            access_token=access,
            refresh_token=refresh,
            expires_at=expires_at,
            character_id=char_id,
            character_name=char_name,
        )

    def _save_token(self) -> None:
        if not self._token:
            return
        try:
            with open(_token_path(), "w", encoding="utf-8") as fh:
                json.dump(self._token._asdict(), fh, indent=2)
        except OSError as exc:
            logger.debug("Failed to save token: %s", exc)

    def _load_token(self) -> None:
        p = _token_path()
        if not p.exists():
            return
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            self._token = TokenInfo(**data)
        except Exception as exc:
            logger.debug("Failed to load token: %s", exc)


# ------------------------------------------------------------------
# v4.0 ESI private-data helpers (require authenticated token)
# ------------------------------------------------------------------


async def get_personal_standings(auth: EsiAuth) -> list[dict]:
    """Fetch the authenticated character's standings list."""
    if not _HTTPX_AVAILABLE:
        return []
    token = await auth.get_token()
    if not token:
        return []
    char_id = auth.character_id
    url = f"https://esi.evetech.net/v2/characters/{char_id}/standings/"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.debug("ESI standings fetch failed: %s", exc)
        return []


async def get_fleet_membership(auth: EsiAuth) -> dict | None:
    """Return current fleet info or None if not in fleet."""
    if not _HTTPX_AVAILABLE:
        return None
    token = await auth.get_token()
    if not token:
        return None
    char_id = auth.character_id
    url = f"https://esi.evetech.net/v1/characters/{char_id}/fleet/"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.debug("ESI fleet check failed: %s", exc)
        return None


async def get_structure_fuel_warnings(auth: EsiAuth) -> list[dict]:
    """Return structures with fuel expiry < 7 days for the authenticated character's corp."""
    if not _HTTPX_AVAILABLE:
        return []
    token = await auth.get_token()
    if not token:
        return []
    # First get corporation ID
    char_id = auth.character_id
    url_char = f"https://esi.evetech.net/v5/characters/{char_id}/"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url_char)
            resp.raise_for_status()
            corp_id = resp.json().get("corporation_id", 0)
        if not corp_id:
            return []
        url_structs = f"https://esi.evetech.net/v3/corporations/{corp_id}/structures/"
        resp = await client.get(
            url_structs, headers={"Authorization": f"Bearer {token}"}
        )
        resp.raise_for_status()
        structures = resp.json()
    except Exception as exc:
        logger.debug("ESI structure fetch failed: %s", exc)
        return []

    warnings = []
    now = time.time()
    for struct in structures if isinstance(structures, list) else []:
        fuel_expires = struct.get("fuel_expires")
        if fuel_expires:
            try:
                from datetime import (  # pylint: disable=import-outside-toplevel
                    datetime,
                    timezone,
                )

                expiry = datetime.fromisoformat(fuel_expires.replace("Z", "+00:00"))
                days_left = (expiry.timestamp() - now) / 86400
                if days_left < 7:
                    warnings.append(
                        {
                            "structure_id": struct.get("structure_id"),
                            "name": struct.get("name", "Unknown"),
                            "days_left": round(days_left, 1),
                        }
                    )
            except Exception:
                pass
    return warnings


# Module-level singleton
_auth: EsiAuth | None = None


def get_esi_auth(client_id: str = _DEFAULT_CLIENT_ID) -> EsiAuth:
    global _auth
    if _auth is None:
        _auth = EsiAuth(client_id=client_id)
    return _auth
