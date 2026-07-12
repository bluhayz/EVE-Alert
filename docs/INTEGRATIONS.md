# EVE Alert — External Integrations Reference

Every network call the app makes, by module. All HTTP goes through `httpx`
(optional import — every module degrades gracefully to a no-op if httpx is
missing). All calls are wrapped in broad `except Exception` and log at DEBUG,
so **integration failures are silent by design** — check
`logs/` with `log_level: DEBUG` when an intel feature "does nothing".

## ESI (EVE Swagger Interface) — public, no auth

Base: `https://esi.evetech.net`

| Endpoint | Used by | Purpose |
|---|---|---|
| `/latest/search/` ⚠ | `zkillboard.py` | System name → ID |
| `/v2/search/` ⚠ | `universe.py` | System name → ID |
| `/v2/characters/search/` ⚠ | `esi_standings.py` | Character name → ID |
| `/v4/universe/systems/{id}/` | `universe.py`, `wormhole.py` | System detail, stargate list |
| `/v2/universe/stargates/{id}/` | `universe.py` | Stargate → destination system |
| `/v1/sovereignty/map/` | `universe.py` | Bulk sov map (cached 5 min) |
| `/v4/alliances/{id}/`, `/v4/corporations/{id}/` | `universe.py` | Entity names |
| `/v5/characters/{id}/` | `esi_standings.py`, `esi_auth.py` | Char detail (birthday, sec status, corp) |
| `/v2/characters/{id}/corporationhistory/` | `esi_standings.py` | Corp count heuristic |
| `/v5/corporations/{id}/`, `/v5/alliances/{id}/` | `esi_standings.py` | Names |
| `/latest/killmails/{id}/{hash}/` | `zkillboard.py` | Killmail detail |
| `/latest/universe/types/{id}/`, `/v4/universe/types/{id}/` | `zkillboard.py`, `fleet_context.py` | Ship type names |

> ⚠ **Known issue:** the public ESI `/search/` endpoint family was removed by
> CCP (only the authenticated `/characters/{id}/search/` remains), and
> `/v2/characters/search/` never existed in that form. Name→ID resolution
> should migrate to `POST /universe/ids/`. Until then, every feature that
> resolves a system or character by name fails silently. Tracked in issues.

## ESI — authenticated (v4.0, EVE SSO OAuth2)

`esi_auth.py`. Login URL `https://login.eveonline.com/v2/oauth/authorize`,
token URL `.../v2/oauth/token`, verify `https://esi.evetech.net/verify/`.
Callback server: `http://localhost:8888/callback` (one-shot asyncio server,
120 s timeout). Token persisted as plain JSON at
`user_config_dir("evealert")/esi_token.json`; auto-refresh 30 s before expiry.

Scopes: `esi-characters.read_standings.v1`, `esi-fleets.read_fleet.v1`,
`esi-assets.read_assets.v1`, `publicData`.

| Endpoint | Purpose |
|---|---|
| `/v2/characters/{id}/standings/` | Personal standings (5-min poll → `_esi_standings_cache`) |
| `/v1/characters/{id}/fleet/` | Fleet membership (404 = not in fleet) |
| `/v3/corporations/{id}/structures/` | Structure fuel expiry (< 7 days warning) |

Notes: the flow does **not** use PKCE and ships a placeholder
`_DEFAULT_CLIENT_ID = "evealert_public_client"` — users must register their own
EVE developer application and paste the client ID into Settings for login to
work. The `state` parameter is static (`"evealert"`), not validated.

## zKillboard

Base: `https://zkillboard.com/api`. Custom User-Agent per module
(e.g. `EVEAlert/3.2`).

| Endpoint | Used by |
|---|---|
| `/kills/solarSystemID/{id}/limit/{n}/` | `zkillboard.py` (recent kills on alarm) |
| `/kills/solarSystemID/{id}/pastSeconds/3600/` | `universe.py` (route threat) |
| `/kills/solarSystemID/{id}/pastSeconds/900/` | `neighbor_monitor.py` (adjacent activity) |
| `/stats/characterID/{id}/` | `esi_standings.py` (kill profile) |
| `/kills/characterID/{id}/limit/5/` | `fleet_context.py` (fleet composition) |
| `/kills/characterID/{id}/pastSeconds/120/` | `fleet_context.py` (killmail monitor, 60 s poll) |

Rate-limit posture: TTL caches (120 s kills, 600 s profiles), per-system alert
cooldowns (10 min), poll intervals ≥ 60 s. `neighbor_monitor` fans out one
request per system within the jump radius per cycle — with `max_jumps: 5` in a
dense region this can be dozens of concurrent requests; keep radius small.

## Other services

| Service | Module | Endpoint |
|---|---|---|
| Eve-Scout (Thera) | `wormhole.py` | `https://www.eve-scout.com/api/wormholes` (15-min poll) |
| CVA KOS | `kos_checker.py` | `https://kos.cva-eve.com/api/` (`?c=json&q=<pilot>&type=unit`) |
| Custom KOS APIs | `kos_checker.py` | user-configured URLs, CVA JSON shape assumed |
| Discord | `alertmanager.py` via `dhooks_lite` | user webhook URLs (all-events + per-type with min-count gates) |
| Telegram | `push_notifier.py` | `api.telegram.org/bot{token}/sendMessage` |
| Pushover | `push_notifier.py` | `api.pushover.net/1/messages.json` (priority 1) |
| ntfy | `push_notifier.py` | user-configured URL (POST, Priority: high) |
| GitHub | `update_checker.py` | `api.github.com/repos/bluhayz/EVE-Alert/releases/latest` |

## Local file integrations (no network)

| Source | Module | Location |
|---|---|---|
| EVE chat logs | `intel_watcher.py`, `alertmanager._augment_with_esi` | `~/Documents/EVE/logs/Chatlogs/` (tail; picks newest file matching channel substring) |
| EVE D-scan logs | `dscan_watcher.py` | `~/Documents/EVE/logs/Dscan/` (1.5 s poll; UTF-16 with UTF-8 fallback) |
| User plugins | `plugin_loader.py` | `user_config_dir("evealert")/plugins/*.py` |

## Timeouts and failure behavior

Per-module HTTP timeouts: 5 s (update check), 6 s (KOS, push), 8 s (universe,
ESI lookups, fleet, wormhole), 10 s (zkillboard client, ESI auth). One
`httpx.AsyncClient` is created **per request** throughout (no connection
pooling) — acceptable at current call rates, but batch operations
(`lookup_many`, neighbor polls) pay the handshake cost repeatedly.
