# EVE Alert — External Integrations Reference (v6.1)

Every network call the app makes, by module. All HTTP goes through `httpx`
(optional import — every module degrades gracefully to a no-op if httpx is
missing) with the shared `User-Agent` from `evealert/tools/http_common.py`
(`DEFAULT_HEADERS` — always pass it; merge for auth headers). All calls are
wrapped in broad `except Exception` and log at DEBUG, so **integration
failures are silent by design** — check `logs/` with `log_level: DEBUG` when
an intel feature "does nothing".

## ESI (EVE Swagger Interface) — public, no auth

Base: `https://esi.evetech.net`

| Endpoint | Used by | Purpose |
|---|---|---|
| `POST /latest/universe/ids/` | `universe.resolve_ids()` — the ONLY name→ID resolver (#110); also `threat_heatmap.py` | System/character/corp/alliance name → ID (exact match) |
| `/v4/universe/systems/{id}/`, `/latest/universe/systems/{id}/` | `universe.py`, `wormhole.py`, `threat_heatmap.py` | System detail, stargates, constellation ID |
| `/latest/universe/constellations/{id}/` | `threat_heatmap.py` | Constellation → system list |
| `/v2/universe/stargates/{id}/` | `universe.py` | Stargate → destination system |
| `/v1/sovereignty/map/` | `universe.py` | Bulk sov map (cached 5 min) |
| `/v4|v5/alliances/{id}/`, `/v4|v5/corporations/{id}/` | `universe.py`, `esi_standings.py` | Entity names |
| `/v5/characters/{id}/` | `esi_standings.py`, `esi_auth.py` | Char detail (birthday, sec status, corp) |
| `/v2/characters/{id}/corporationhistory/` | `esi_standings.py` | Corp count heuristic |
| `/latest/killmails/{id}/{hash}/` | `zkillboard.py` | Killmail detail |
| `/latest/universe/types/{id}/`, `/v4/universe/types/{id}/` | `zkillboard.py`, `fleet_context.py` | Ship type names |
| `/latest/characters/{id}/` | `zkillboard.py` | Victim name |

All endpoint versions live-verified 2026-07-12. The removed `/search/` family
is fully migrated off (#110).

## ESI — authenticated (EVE SSO OAuth2)

`esi_auth.py`. Authorize `https://login.eveonline.com/v2/oauth/authorize`,
token `.../v2/oauth/token`. **PKCE (S256) + per-login random `state`,
validated on callback** (#104/#105). Character identity decoded from the JWT
access token. Callback server: `http://localhost:8888/callback` (one-shot
asyncio server, 120 s timeout). Token persisted with 0600 perms at
`user_config_dir("evealert")/esi_token.json`; auto-refresh 30 s before expiry.

**There is no built-in client ID.** Users must register a free EVE developer
application (type *Authentication & API Access*, callback exactly
`http://localhost:8888/callback`) and paste the 32-hex client ID into
Settings; `login()` validates the format before opening a browser (#136).

Scopes: `esi-characters.read_standings.v1`, `esi-fleets.read_fleet.v1`,
`esi-assets.read_assets.v1`, `esi-corporations.read_structures.v1`,
`publicData`.

| Endpoint | Purpose |
|---|---|
| `/v2/characters/{id}/standings/` | Personal standings (5-min poll; drives ally filter #147) |
| `/v1/characters/{id}/fleet/` | Fleet membership (404 = not in fleet) |
| `/v3/corporations/{id}/structures/` | Structure fuel expiry (< 7 days warning) |

## zKillboard

Base: `https://zkillboard.com/api`. Shared UA from `http_common.py`.

**Two response quirks every consumer must handle** (live-verified):
1. Empty result sets return `[null]`, not `[]` — normalize via
   `zkillboard.clean_zkb_entries()` (#133).
2. List responses cap at ~200 entries per page; no pagination is currently
   implemented (#163 tracks adding `/page/N/` for the heatmap).
3. The `limit/{n}/` modifier was **revoked** by zKB — never use it; slice
   client-side (#132).

| Endpoint | Used by |
|---|---|
| `/kills/solarSystemID/{id}/` | `zkillboard.py` (recent kills on alarm; client-side slice) |
| `/kills/solarSystemID/{id}/pastSeconds/3600/` | `universe.py` (route threat) |
| `/kills/solarSystemID/{id}/pastSeconds/900/` | `neighbor_monitor.py` (adjacent activity) |
| `/kills/solarSystemID/{id}/pastSeconds/604800/` | `threat_heatmap.py` (7-day histograms, 1 h cache) |
| `/stats/characterID/{id}/` | `esi_standings.py` (kill profile; `topLists` type is `shipType`, counts are all-time — #134) |
| `/kills/characterID/{id}/` | `fleet_context.py` (fleet composition; entries embed full killmails incl. `attackers`) |
| `/kills|losses/characterID/{id}/pastSeconds/300/` | `fleet_context.py` (killmail monitor, 60 s poll) |

Rate-limit posture: TTL caches (120 s kills, 600 s profiles, 3600 s heatmap),
per-system alert cooldowns (10 min), poll intervals ≥ 60 s,
`fleet_context._ZKB_SEMAPHORE` serializes its calls. `neighbor_monitor` still
fans out one request per nearby system per cycle — keep `max_jumps` small.
Future: v7.0 plans a RedisQ push stream to replace most polling (#169).

## Other services

| Service | Module | Endpoint |
|---|---|---|
| Eve-Scout (Thera/Turnur) | `wormhole.py` | `https://api.eve-scout.com/v2/public/signatures` (15-min poll; flat schema, live-verified) |
| CVA KOS (**offline — disabled by default**, #135) | `kos_checker.py` | `https://kos.cva-eve.com/api/` — dead-source quarantine skips it after first connection failure |
| Custom KOS APIs | `kos_checker.py` | user-configured URLs (SSRF-guarded via `net_safety.py`), CVA JSON shape assumed |
| Discord | `alertmanager.py` via `dhooks_lite` | user webhook URLs (all-events + per-type with min-count gates). ⚠ currently sync inside the event loop — #160 |
| Telegram | `push_notifier.py` | `api.telegram.org/bot{token}/sendMessage` |
| Pushover | `push_notifier.py` | `api.pushover.net/1/messages.json` (priority 1) |
| ntfy | `push_notifier.py` | user-configured URL (POST, Priority: high) |
| **Automation bridge (outbound)** | `alertmanager._post_automation_webhook` (#153) | user-configured URL — POSTs `{type, text, timestamp}` on every alarm (for AutoHotkey/PyAutoGUI listeners) |
| GitHub | `update_checker.py` | `api.github.com/repos/bluhayz/EVE-Alert/releases/latest` |

## Inbound/local surfaces (no external network)

| Surface | Module | Notes |
|---|---|---|
| Web dashboard | `web_server.py` | localhost HTTP: `/`, `/api/status`, `/api/log`, `/api/alarm/latest` (#153) |
| EVE chat logs | `intel_watcher.py` (+ `intel_parser.py`) | `~/Documents/EVE/logs/Chatlogs/` tail |
| EVE D-scan logs | `dscan_watcher.py` | `~/Documents/EVE/logs/Dscan/` (1.5 s poll; UTF-16 w/ UTF-8 fallback) |
| User plugins | `plugin_loader.py` | `user_config_dir("evealert")/plugins/*.py` |
| Tesseract OCR | `ocr_local.py` | local binary via pytesseract (optional) |
| TTS | `tts.py` | pyttsx3 / Windows SAPI5 (optional extra `[tts]`) |

## Timeouts and failure behavior

Per-module HTTP timeouts: 5 s (update check), 6 s (KOS, push), 8 s (universe,
ESI lookups, fleet, wormhole), 10 s (zkillboard, ESI auth, heatmap). One
`httpx.AsyncClient` is created **per request** throughout (no connection
pooling) — acceptable at current call rates; revisit if RedisQ (#169) or
heatmap pagination (#163) raises volumes.
