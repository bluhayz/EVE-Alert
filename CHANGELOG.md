# Changelog

## [7.4.0] 2026-07-18

### Added — Enemy Analytics & Situational Awareness (#241-#244)

Turns the v7.3 data foundation into in-the-moment situational awareness:
per-pilot combat dossiers, hunting-ground/danger-window analytics,
dossier-enriched alarms and threat scoring, and an Intel Analytics UI
with export. Completes the Enemy Combat Analytics epic.

- **Pilot combat dossier engine (#241)**: new `pilot_dossier.py` answers
  "who is this pilot operationally" in one call — ships flown (with
  frequencies), hunting grounds, active hours/prime window, gang
  size/solo %, and inferred fleetmates (pilots sharing 3+ killmails).
  Prefers the cached v7.3 rollup for full-history aggregates and falls
  back to a bounded recent-activity read, so it's safe to call from the
  alarm path.
- **Hunting-ground analytics (#242)**: new `hunting_grounds.py` —
  `group_activity()` ranks a corp/alliance's top systems, pilots, gang
  size, and 7d-vs-30d trend; `system_danger_windows()` aggregates a
  system plus its neighbors within 2 jumps into an hourly kill histogram
  and a `danger_now` flag, entirely from local data (zero network calls
  in this module). Wired into the engine as a "Danger window:" log line
  that fires once per transition into a historically hot hour, gated
  behind the existing peak-hours-warning setting.
- **Dossier-enriched alarms and threat scoring (#243)**: Enemy alarms now
  show a `Dossier:` line when one exists; the composite threat score
  gains two optional signals — a dossier top-ship class of tackle/dictor,
  and a normally-gang-flying pilot showing up alone in Local (advance-
  scout pattern). Both default to no-op, so pre-#243 scores are
  unaffected. The webhook template gains an optional `{dossier}`
  variable (empty by default).
- **Intel Analytics UI (#244)**: new "Intel Analytics" window — a pilot
  search/dossier browser (ships, hunting grounds, fleetmates, 24h
  activity, recent sightings), a top-hostiles board ranked by a
  recency-weighted encounter score, and a group view over
  `group_activity()`, with CSV/JSON export. All store reads run on a
  background thread, delivered back to the UI via Qt signal.

## [7.3.0] 2026-07-18

### Added — Combat Intelligence Data Foundation (#237-#240)

Builds the data layer for enemy-pilot analytics: what a hostile flies,
where they hunt, and who's worth watching. First half of the Enemy Combat
Analytics epic — v7.4 builds the dossier/hunting-ground analytics on top
of this.

- **Combat activity store (#237)**: new `combat_activity_store.py`
  captures killmail-derived per-pilot activity (ship flown, system,
  gang size, role) via two paths — the R2Z2 live-kill stream for any
  currently-tracked pilot, and a one-time zKillboard backfill on a
  pilot's first Enemy alarm each session so a dossier isn't empty on
  first encounter.
- **Sighting enrichment (#238)**: `pilot_history_store` migrates to
  schema v2 (nullable `character_id`, composite indexes) in place;
  local sightings now prefer the ship actually visible on D-scan over
  zKB's historical guess, and intel-sourced sightings validate their
  parsed system against the universe cache before recording.
- **Analytics rollup layer (#239)**: new `intel_rollups.py`
  precomputes per-pilot and per-system dossier data so a future alarm-
  path read is a single small-row lookup, not a full history scan — with
  a genuinely non-blocking read path and a maintenance-task sweep that
  only touches pilots with new activity.
- **Hostile watchlists (#240)**: named pilot/corporation/alliance lists
  (new Watchlist Manager dialog) that get tracked anywhere in New Eden
  via the live-kill feed, tag Enemy alarms `[WATCHLIST]`, and add a
  threat-score signal.

## [7.2.3] 2026-07-18

### Fixed — 11 bugs from the full-codebase code review (#226-#236)

- **#226**: `UniverseCache.get_route()` didn't exist at all -- two production
  call sites (jump-distance display, #217 pathing plausibility check)
  silently no-op'd/soft-failed on `AttributeError`, masked by tests that
  mocked the method into existence. Added a real implementation.
- **#227**: `_fetch_neighbors()` truncated to the first 8 stargates instead
  of bounding concurrency, silently corrupting the jump graph for any
  system with more gates (trade hubs); a transient ESI failure was also
  cached as a permanent empty neighbor list. Now resolves all stargates
  with bounded concurrency and never caches a failed fetch.
- **#228**: the web status dashboard rendered log lines -- including raw
  intel-channel chat text -- without HTML-escaping (stored XSS). Fixed.
- **#229**: the #177 cache-maintenance sweep missed `EsiLookup` and
  `KosChecker`, the two largest per-pilot TTL caches. Added purge methods
  and wired them in; also fixed a casing bug in `KosChecker.reconfigure()`.
- **#230**: the intel-channel parser upper-cased entire messages before
  system-name detection, so ordinary words became indistinguishable from
  real systems (`"hostile sabre heading north"` resolved to system
  `"HOSTILE"`). Fixed to match on original casing.
- **#231**: `EsiAuth.get_token()` returned a known-expired access token
  whenever a refresh attempt failed. Now returns `None` on refresh
  failure; a revoked/expired refresh token clears the session and warns
  once instead of retrying forever.
- **#232**: a failed sovereignty-map fetch was cached as a successful
  "no sov anywhere" result for the full 5-minute TTL. Now serves stale
  data and retries after a short backoff instead.
- **#233**: the adjacent-system kill monitor fired one zKillboard request
  per nearby system with no concurrency bound -- up to 150+ simultaneous
  requests per poll cycle. Bounded via semaphore.
- **#234**: an unhandled `OSError` from a deleted/locked D-scan log file
  could silently kill the D-scan watcher for the rest of the session.
  Hardened against the race.
- **#235**: the `kos_list` setting was documented but never actually read
  anywhere -- entries a user added did nothing. Wired in.
- **#236**: the test suite wrote real session-report files into the
  user's actual config directory instead of the test temp dir.

## [7.2.2] 2026-07-18

### Fixed — OCR misreads fired repeat alarms and fragmented pilot history (#224)

- The same on-screen enemy icon, read slightly differently between polls
  (classic `l`/`t`/`I`/`1` confusion in condensed UI fonts, e.g.
  `lilbitofgoop` → `litbitofgoop` → `titbitofgoop`), was treated as a
  brand-new pilot on every misread — retriggering the alarm, redundant
  ESI/zKillboard lookups, and splitting one pilot's sighting history
  across N near-duplicate records.
- `_stabilize_enemy_identities()` (`alertmanager.py`) now debounces OCR
  reads per on-screen position: the first read is trusted immediately (no
  delay on new threats), a near-miss (length-scaled Levenshtein
  edit-distance) is absorbed into the existing anchor, and a genuinely
  different name only takes over after repeating for 2 consecutive polls
  — so a real pilot swap at the same screen slot still works. Feeds into
  dedup, the alarm headline, ESI hint names, and pilot-history writes.

### Fixed — intel channel discovery never found real EVE chatlog files (#225)

- `discover_channels()` (`intel_watcher.py`) anchored its filename regex
  on `_YYYYMMDD_HHMMSS.txt`, but real EVE clients append a further
  `_<ownerID>` segment before `.txt` (e.g.
  `I. Ftn Intel_20260718_120036_620084186.txt`). The regex never matched
  real logs, so the Settings dialog's channel auto-discovery list came
  back empty on every real installation. The date/time suffix now allows
  an optional trailing `_<digits>` owner-ID segment.

## [7.2.1] 2026-07-18

### Fixed — duplicate System/Sov info logged on startup (#223)

- `_location_monitor()`'s first-detection branch fired the same
  `create_task(self._display_system_info())` as its else branch, so the
  one-shot `System: ... | Type: ...` / `Sov: ...` log lines appeared
  twice within seconds of startup — once from `start()`'s own
  unconditional call, once again on the first ESI-detected system.
  Now only a genuine *later* system change re-triggers the display; the
  lighter "System: auto-detected → X" line and the `server.system`
  settings update still fire on every detection, including the first.

## [7.2.0] 2026-07-18

### Added — v7.2: Multiboxing & Performance (#174, #175, #176, #177)

Cuts steady-state vision CPU by ~94% on a static workload, adds an optional
Desktop Duplication capture backend, ships an MVP of multi-client support for
multiboxers, and closes out the long-session memory-growth audit.

- **Vision pipeline performance pass (#175)**: `evealert/tools/vision.py`
  gained a frame-change short-circuit (skip re-matching entirely when the
  captured region is byte-identical to the previous poll — the common case
  for a static Local roster), needle-normalization caching (dtype-cast +
  `cv.normalize()` no longer redone ~10x/sec per template), and an optional
  `detection.downscale` factor. Benchmark harness at `tools/bench_vision.py`
  measured **3.49ms → 0.19ms per frame (94.5% reduction)** on a static
  workload — comfortably past the ≥50% target. Deliberately did *not*
  implement literal cross-template "stop at first match": it would silently
  drop points from a second template matching the same frame, which the
  existing per-enemy dedup (#100/#213) relies on for multi-hostile tracking.
- **dxcam capture backend (#176)**: `windowscapture.py` gained a
  `CaptureBackend` protocol with `MssBackend` (unchanged default) and
  `DxcamBackend` (Windows Desktop Duplication API, optional `[capture-dx]`
  extra) implementations, hot-swappable via `detection.capture_backend`
  (`mss`/`auto`/`dxcam`, defaulting to `mss` — an unproven-in-this-app native
  capture path shouldn't become every existing user's default overnight).
  Clean fallback with a one-time log line when dxcam isn't installed.
- **Multi-client support, MVP (#174)**: new `clients: [...]` settings list
  (legacy single-region keys keep working with zero user action — a
  non-empty list's first entry becomes the primary client, one-way
  migration). Each additional client gets its own `WindowCapture`/`Vision`
  pair and fully independent dedup/cooldown state, so one client's alarm
  cooldown can never suppress another's. Alarms from extra clients are
  prefixed (`[ClientName] Enemy Appears!`). Threat score/ESI/Discord
  webhook-template/push notifications/OCR identity resolution stay
  primary-client/global-only in this pass — no Config Mode client-selector
  UI or status-chip row yet (follow-up). 3-client benchmark: aggregate cost
  scales linearly (~3x for 3x clients), no cross-client interference.
- **Long-session soak reliability (#177)**: audited and fixed the
  named unbounded-growth candidates — `_seen_enemies` was already correctly
  bounded (verified, no fix needed); added `purge_expired()`/
  `purge_expired_kill_counts()`/`purge_expired_cache()` to the zKillboard,
  universe kill-count, and constellation-heatmap TTL caches (previously:
  TTL-checked on read but never evicted, so an entry looked up once and
  never revisited sat in memory for the life of the process); added a size
  guard to the permanent `_neighbors` identity cache. A new periodic
  cache-maintenance task purges all three every 15 minutes. New
  `tools/soak_test.py` drives the alarm-dispatch/dedup machinery with
  synthetic sightings and samples RSS/thread/task counts to CSV — the
  automatable slice of this issue; a genuine multi-hour RSS-slope
  measurement still needs a human to run it for real.

## [7.1.1] 2026-07-18

### Fixed — flaky R2Z2 stale-sequence test under Python 3.12 (CI)

- `test_r2z2.py::ConsumerRunLoopTests::test_stale_sequence_resyncs_to_live_after_threshold`
  simulated elapsed time by patching the global `time.time()` with a
  finite, call-count-indexed list. That's fragile: other library
  internals (httpx/anyio) also read the globally patched clock, and the
  number of incidental reads differs between Python 3.12 and 3.13 —
  passed consistently in local dev (3.13) but failed on CI's
  `windows-latest` / Python 3.12 runner, blocking the v7.1.0 release
  build (`build-windows`/`release` jobs never ran since they depend on
  the test job passing). Rewritten to advance a live, unboundedly-
  re-readable clock value as HTTP calls arrive rather than an
  exact-count-indexed list, so incidental extra reads no longer desync
  the simulated timeline. No application code changed — v7.1.1 exists
  solely to get a green CI run publishing the Windows build; see 7.1.0
  below for the actual feature set.

## [7.1.0] 2026-07-17

### Added — v7.1: Real-Time Intel Platform (#169, #170, #171, #172, #173, #191)

Replaces the old 60s+ zKillboard polling loop with a push-style live-kill
feed, adds gate-camp detection and a route-avoidance advisor on top of it,
and rounds out the intel pipeline with multi-channel dedup and a standings
manager.

- **Live kill feed (#169)**: new `evealert/tools/r2z2.py`. zKillboard's
  RedisQ (this issue's original spec) was sunset 2026-05-31 before
  implementation — verified against the live API and built against its
  documented replacement, R2Z2, instead (sequence-based HTTP polling: GET
  `sequence.json` once, then `{sequence}.json` repeatedly; 200 = kill +
  advance, 404 = wait and retry the same sequence). Filtered to kills
  within `r2z2.watch_jumps` of the configured system or matching
  `r2z2.alliance_watchlist` before buffering, so the rolling kill buffer
  never grows with every kill in New Eden. Live kills log as
  `LIVE KILL: <ship> destroyed in <system> (Nj away) — <N> attackers`,
  trigger the alarm sound within `r2z2.alarm_jumps`, and feed the
  composite threat score's `adjacent_kills` signal. Supersedes the
  Adjacent System Monitor's polling loop when enabled (`r2z2.enabled`);
  the last processed sequence persists across restarts. 24 new tests in
  `test_r2z2.py`.
- **Gate-camp detection (#170)**: new `evealert/tools/gatecamp.py`, fed by
  the R2Z2 kill buffer. Clusters kills by (system, gate/station/structure)
  within a rolling 30-minute window; ≥3 kills with ≥2 distinct victim
  corporations and ≥60% repeating-attacker overlap is a confirmed camp,
  2 kills with overlap is a possible camp. Confirmed camps within
  `adjacent.max_jumps` log a `GATE CAMP: ...` warning (once per camp per
  hour). `universe.route_threat()` now marks legs with an active camp as
  danger regardless of the raw zKB kill count, and the F4 status readout
  mentions active camps. 13 new tests in `test_gatecamp.py`.
- **Route-avoidance advisor (#172)**: `universe.py` gains
  `suggest_safer_route()`, a weighted-Dijkstra alternative to the existing
  shortest-path `route_threat()` — edge weight is 1 + penalty from recent
  kills (now cached, 5-min TTL), active gate camps, and low/null-sec
  status, capped at 30 hops and 50 zKB probes per suggestion. Returns both
  the shortest and suggested routes so the log can show
  `Shortest: 8j (2 dangerous) — Suggested: 10j (0 dangerous)`. Wired into
  a new "Route Check" section in Settings (origin/destination fields,
  results post to the main log). 20 new tests across
  `test_route_avoidance.py` and `test_route_threat_gatecamp.py`.
- **Multi-channel intel watcher (#171, #191)**: `intelligence.intel_channels`
  (list) replaces the single `intel_log_channel` string (old configs
  migrate transparently); one `IntelWatcher` runs per channel, tagging
  each log line (`[NC-INT] ...`) and de-duplicating the same paste landing
  in multiple channels within 30s. Settings gains a "Scan for Channels"
  button that discovers channels from the EVE chatlog directory
  automatically, with a checkbox list and manual-add fallback.
- **Standings manager (#173)**: new `evealert/ui/standings_manager.py` —
  a dedicated dialog for manual ally/hostile overrides (add/edit/remove,
  JSON import/export) alongside a read-only view of ESI-synced personal
  standings ("Sync Now"), replacing ad-hoc threat-tier editing.

## [7.0.0] 2026-07-17

### Added — v7.0: Pilot Intelligence & Persistence (#214, #215, #216, #217, #218)

Every pilot sighting — Local enemy detections and intel-channel mentions — is
now recorded to a durable local history, and that history feeds back into
Enemy alarms as it accumulates.

- **Persistent sighting store (#214)**: new `evealert/tools/pilot_history_store.py`,
  a local SQLite database (`pilot_history.db`, stdlib `sqlite3`, no new
  dependency) recording pilot name, system, ship, source (local/intel),
  corp/alliance, and timestamp for every sighting. A configurable retention
  window (`intelligence.pilot_history_retention_days`, default 180 days,
  `0` = keep forever) is pruned once per app start.
- **Ingestion (#215)**: Enemy-alarm pilots and intel-channel `mentioned_pilots`
  are now recorded automatically. The reporting pilot in an intel message is
  deliberately excluded — only who they *mention* counts as a sighting. Gated
  behind `intelligence.pilot_history_enabled` (default on).
- **History summary on alarms (#216)**: pilots with 3+ recorded sightings get
  an extra log line — `History: 14 sightings over 45d — mostly in J5A-IX
  (9x), 1DQ1-A (3x); usually flies Loki; most active 19:00-22:00` — computed
  in `evealert/tools/pilot_history_analytics.py`.
- **Pathing inference (#217)**: sightings are grouped into sessions (a >4h
  gap starts a new session) to infer a pilot's home system and their most
  common system-to-system transitions, cross-checked against the jump graph
  for plausibility. Requires a transition to repeat at least 3 times before
  reporting it — no result is shown rather than a low-confidence guess.
  Appears as a trailing segment on the History line, e.g. `home J5A-IX;
  often moves J5A-IX -> 1DQ1-A`.
- **Historical threat scoring (#218)**: `evealert/tools/threat_score.py`'s
  composite scorer now accepts `history_frequency` and
  `history_is_regular_route` as additive signals — a pilot frequently seen
  in the current system, or known to pass through on their regular route,
  scores measurably higher. Both default to "no history," so the score is
  byte-for-byte unchanged for a pilot with none (verified against the full
  pre-#218 test suite run unmodified). A short behavioral label ("frequent
  resident", "occasional visitor", "single sighting", "known to pass
  through") is appended to the rendered threat line, separate from the
  numeric score.
- 63 new tests across `test_pilot_history_store.py`, `test_pilot_history_analytics.py`,
  `test_threat_score.py`, and `test_alertmanager.py`.

## [6.3.34] 2026-07-17

### Changed — log pane now shows newest-first (#222)

- New log entries appear at the top instead of the bottom, so watching live
  activity no longer requires scrolling down.
- `LogPane._insert_entry()` prepends each entry above the previous top entry.
  `setMaximumBlockCount` was removed in favor of a manual
  `_trim_to_max_blocks()` that trims from the end of the document, since the
  oldest entry now lives at the bottom rather than the top — Qt's built-in
  cap always trims from the structural start, which would otherwise have
  silently deleted the newest lines instead of the oldest. Auto-scroll now
  follows the top of the view (and only when the user was already there),
  the mirror of the old bottom-follow behavior.

### Fixed — OCR [alarm] messages repeated for an unchanged result (#222)

- `OCR [alarm]: identified pilot(s): ...` re-printed on every fresh
  (non-throttled) OCR resolve even when the result was identical to last
  time — a single stationary pilot got re-announced every
  ~1.5–2.5 seconds for as long as they stayed, drowning out everything
  else in the log.
- Added `AlertAgent._log_ocr_message()`, which all five `OCR [alarm]: ...`
  log sites in `_resolve_enemy_identities()` now route through. It tracks
  the last message actually logged and only emits again when the text
  changes. The dedup state resets in `reset_alarm("Enemy")` alongside the
  other #213 per-engagement state, so a later, genuinely new engagement
  with the same pilot still logs normally.
- New regression tests in `test_log_pane.py` (ordering, rerender,
  trim-from-bottom) and `test_alertmanager.py` (message dedup, re-trigger
  on change, reset behavior).

## [6.3.33] 2026-07-17

### Fixed — stationary enemy pilot re-triggered the full alarm every cooldown_timer_enemy seconds (#221)

- A pilot who stayed continuously in system re-fired the entire Enemy alarm
  pipeline — log line, ESI query, sound, webhook — every `cooldown_timer_enemy`
  seconds, indefinitely, even though they never left. Reported log showed the
  same pilot re-alarming roughly once a minute for 15+ minutes straight.
- `AlertAgent._should_alarm_enemy()` had a cooldown-elapsed branch that
  re-triggered on a still-present, unchanged identity purely because time had
  passed, independent of whether the pilot had actually left and returned.
  This conflicted with `docs/FEATURES.md`'s own documented design, which
  already separated "per-enemy dedup + re-alert" (`rearm_minutes`) from
  "per-type sound cooldown" (`cooldown_timer_enemy/_faction`, scoped to
  `play_sound()`) as two distinct mechanisms.
- Removed the cooldown-elapsed auto-refire. An identity that has already
  alarmed now only fires again when it drops out of `_seen_enemies` because
  the pilot actually left (`reset_alarm`, unchanged, #100), or when
  `rearm_minutes > 0` and they've been continuously present for that long
  (#144) — the existing, opt-in lever for periodic reminders on a sustained
  threat, off by default. `cooldown_timer_enemy`/`cooldown_timer_faction`
  are unaffected for their other purpose, the alarm-type-level sound-spam
  throttle in `play_sound()`.
- Updated/added regression tests in `test_alarm_dedup.py` covering both the
  no-more-auto-refire case and that `rearm_minutes` still works as before.

## [6.3.32] 2026-07-17

### Fixed — OCR alarm headline/ESI query reported the entire Local roster as "the enemy" (#220, regression from #213)

- v6.3.31's #213 fix set `AlertAgent._last_ocr_names` to `match_names_to_targets()`'s
  `all_names` — every distinct name OCR found anywhere in the captured region — instead
  of only the names actually matched to a detected enemy icon's row. Since
  `_last_ocr_names` feeds both the Enemy alarm headline and the ESI query's
  `hint_names`, a single enemy icon on screen could produce an alarm listing (and
  ESI-querying) the entire visible Local roster, including the player's own name and
  corp/fleet mates, whenever the OCR region spanned more than the enemy's own row.
- `_last_ocr_names` is now built strictly from the icon-matched identities. When no
  icon matches any OCR'd row, it's left empty (bare `"Enemy Appears!"` headline, no
  ESI query) rather than falling back to the full roster. The `OCR [alarm]:` diagnostic
  log line was split into three honest cases — names matched to an enemy icon,
  names found but none matched a row (a region/tolerance-misalignment warning, not
  reported as hostiles), and no names found — so region tuning still has useful
  diagnostic output without implying everyone visible is the enemy.
- 2 new regression tests in `test_alertmanager.py` reproduce the exact reported
  scenario (a matched name buried in a larger `all_names` roster dump, and the
  no-match case) and fail against the pre-fix code.

## [6.3.31] 2026-07-17

### Fixed — OCR emitting the same pilot name twice, with icon-glyph prefixes leaking through (#209)

- `parse_eve_names()` tried the icon-glyph-stripped candidate (e.g. `"S Naveia"`
  -> `"Naveia"`) **and** the untouched full line as a fallback, unconditionally
  — so a name like `"g MickFun"` or `"IS Scarlet Police"` emitted both the
  stripped name and the original glyph-prefixed line as two separate results.
  The fallback now only fires when the stripped candidate fails validation,
  eliminating the duplicate while still catching genuinely ambiguous lines
  (e.g. `"AB 123"`, where the "stripped" remainder has no letters). Tradeoff:
  a legitimate short first name like `"Al Capone"` now yields only `"Capone"`
  — accepted, since icon-glyph noise is far more common in practice.
- 5 new/updated regression tests in `test_ocr_local.py`, including the exact
  `"g Mick Lun"` / `"IS Scarlet Police"` / `"g MickFun"` cases from the report.

### Fixed — pilot/system names now the clickable link, not a separate visible URL (#210)

- Pilot intel lines, intel-channel reporter/hostile links, and dotlan system
  links previously showed the name **and** a full raw URL after it
  (`Mick Lun ... | zkillboard.com/character/.../`). The name itself is now
  the clickable link; the raw URL is never shown as separate text.
- New `evealert/tools/link_markers.py`: a small, Qt-free `make_link()` /
  `MARKER_RE` contract that lets `alertmanager.py` (deliberately Qt-free, for
  headless testability) hand link text off to `LogPane` for rendering without
  ever putting a raw URL in the log's plain-text buffer. `LogPane` renders
  markers as real `<a>` tags in the widget and as readable `"name (url)"`
  text in the bug-reporter's plain-text export; the existing bare-URL
  linkify pass (#207) still runs as a fallback for any un-marked text.
- 10 new tests across `test_log_pane.py` and existing suites confirm the
  anchor renders on the name, copy/paste stays clean, and legacy bare-URL
  linkifying still works alongside the new marker.

### Fixed — current system not detected despite being authenticated with ESI (#211)

- The ESI token used for auto-detecting the player's current system was
  missing the `esi-location.read_location.v1` scope, so
  `get_character_location()` silently 403'd and the app kept falling back to
  the placeholder `"Enter a System Name"`. Scopes are fixed at token-issuance
  time, so already-authenticated users' tokens couldn't gain the new scope
  retroactively — the fix detects the live 403 and logs a one-time warning
  telling the user to log out and back in via Settings -> Intel & ESI -> EVE
  SSO to re-grant permissions, rather than failing silently.
- 3 new tests in `test_esi_auth.py` cover the scope list, the one-warning
  behavior across repeated 403s, and the warning flag resetting on a fresh
  login.

### Changed — per-icon pilot identity now drives Enemy alarm dedup, not screen position alone (#213)

- A Local roster re-sort (which shifts every pilot's row/Y-position without
  anyone actually leaving) could make a still-present hostile look like a
  brand-new sighting and re-trigger the alarm mid-cooldown. Dedup identity is
  now the OCR-resolved pilot name when available, falling back to the
  quantized screen position when OCR can't identify a given icon (unchanged
  from before this fix).
- New `match_names_to_targets()` in `ocr_local.py` replaces
  `read_local_names_near_rows()`: one OCR pass now serves both the alarm
  headline (all names found) and per-icon identity (name matched to each
  icon's row) instead of needing two separate capture passes.
- OCR identity resolution is throttled (`_IDENTITY_RESOLVE_MIN_INTERVAL` =
  1.5s) so it doesn't re-run on every 0.1-0.2s poll cycle, while still
  resolving immediately whenever the *set* of detected icon positions
  changes (a genuinely new arrival is never delayed).
- 10 new tests across `test_ocr_local.py`, `test_alarm_dedup.py`, and
  `test_alertmanager.py` cover the roster-resort-doesn't-realarm scenario,
  a different pilot landing on an old position not inheriting its cooldown,
  and the OCR throttling behavior.

### Added — correlate Enemy alarms with recent intel-channel reports (#212)

- When a pilot resolves for an Enemy alarm, EVE Alert now checks the last
  10 minutes of intel-channel reports for a mention of that same pilot
  (case-insensitive match against the report's mentioned-pilot list or the
  reporting pilot themselves) and, if found, shows the report's
  system/message inline — e.g. `Intel (2m ago, reported by bluhayz):
  "maybe shuttle" in J5A-IX` — surfacing real-time ship/position
  corroboration that the ESI/zKillboard pipeline alone can't provide (a
  zKillboard "flies X" stat is historical, not current).
- A capped rolling buffer (`deque`, 50 entries) of recent `IntelReport`s is
  kept on `AlertAgent`, populated from the existing `_on_intel_report()`
  callback — no re-reading of the chat log needed. New setting
  `intelligence.correlate_intel_reports` (default on) disables the feature
  entirely when off; no match means no extra line and no behavior change.
- 10 new tests in `test_alertmanager.py` cover matching, no-match, a report
  aging out of the recency window, and the settings toggle.

## [6.3.30] 2026-07-16

### Fixed — zkillboard character links 404 for pilots zkillboard has never indexed (#208)

- **Root cause**: zkillboard's stats API (`/api/stats/characterID/<id>/`)
  returns **HTTP 200** with `{"error": "Invalid type or id"}` — not a 4xx —
  for any character it has never seen in a killmail (e.g. a brand-new pilot
  who has never been killed or scored a kill). Because the status code is
  200, `raise_for_status()` never fires, and the error body was being parsed
  as if it were real data: `shipsDestroyed`/`shipsLost` default to 0, so the
  character was treated as "a real profile with zero kills/losses" instead
  of "no profile at all." The same condition — zero killmail history — is
  exactly what makes that character's `zkillboard.com/character/<id>/` web
  page 404, so the link shown in pilot intel lines pointed at a page that
  doesn't exist.
- `_fetch_zkb_profile` now detects the `{"error": ...}` response shape and
  returns `None` (not a zero-stat profile). The pilot intel line's
  zkillboard character link is now shown **only when zkillboard actually
  has a record for that pilot** — the zkb profile is fetched once per pilot
  and reused for both the link decision and the existing kill/loss stats
  line (no extra network round-trip).
- Verified against the exact character from the reported 404
  (`2124449072`, "Oveim Hrild Beldrulf"): confirmed live that zkillboard's
  API returns the error body for this ID, and that the fixed code now
  correctly omits the link while still showing corp/age/KOS/threat-tier
  intel for the pilot. A known-active character continues to get its link
  as before.
- 3 new regression tests (`NeverIndexedCharacterTests` in
  `test_esi_standings.py`, plus updated/added `test_alertmanager.py` cases)
  covering both the error-body-as-None parsing and the link-suppression
  behavior, including a check that a *genuine* zero-stats character (no
  `"error"` key) still parses normally.

## [6.3.29] 2026-07-16

### Added — clickable zkillboard/dotlan links in the log pane (#207)

- zkillboard and dotlan links in the log (pilot intel lines, kill reports,
  intel-channel summaries) were plain text — you had to select and paste the
  URL yourself. They're now real hyperlinks: **click to open in your default
  browser**, rendered in a distinct blue (`#58A6FF`) with an underline so
  they stand out from the surrounding severity color (red/yellow/cyan/etc.)
  on the same line.
- `LogPane`'s display widget changed from `QPlainTextEdit` to `QTextBrowser`
  (the Qt class built for exactly this: read-only rich text with automatic
  external-link opening). Copy/paste and the right-click "Copy line"/"Copy
  all visible" actions are unaffected — they still yield clean plain text
  with the full URL, not markup.
- Link detection is scoped to the two hosts this app actually generates
  (`zkillboard.com`, `dotlan.net`) rather than a generic URL pattern, so a
  pilot or corp name containing a period (EVE allows them, e.g. "Dr. Evil")
  is never mistakenly linkified.
- Fixed a stray trailing colon after the dotlan link in the "Recent kills"
  log line (cosmetic, left over from #205).
- 14 new tests (`tests/test_log_pane.py`) covering the link regex, HTML
  escaping, href scheme normalization, and that filtering/search still work
  against the underlying plain-text buffer.

## [6.3.28] 2026-07-16

### Fixed — alarm intel now targets only the alerting pilot, not the whole Local roster (#206)

- **OCR fed every visible pilot name to the intel pipeline on every Enemy
  alarm**, not just the hostile who actually triggered it — including the
  player's own character and every neutral/blue in the room. Root cause:
  vision-based detection only ever knew "an enemy icon matched somewhere in
  the region," never which row; OCR then read every name currently visible
  and sent the entire list downstream.
- Correlated the two systems by screen position: `Vision.find()` already
  returns the pixel center of each detected enemy icon
  (`AlertAgent._enemy_points`); WinRT's `OcrWord.bounding_rect` (previously
  discarded) gives the pixel position of each recognized name. Both are
  translated to absolute screen coordinates and matched within a tolerance
  derived from the icon template's own height — only the OCR row(s) that
  line up with a detected enemy icon are now sent to ESI/KOS/zKillboard.
  Implemented for both OCR backends (WinRT and the Tesseract fallback, via
  `image_to_data`'s per-word line grouping) so the fix isn't Windows-only.
- **Degrades safely, never silently**: if no OCR row lines up with any
  detected icon (e.g. an OCR region that isn't row-aligned with the Alert
  Region — a configuration issue, not a crash), the full unfiltered name
  list is used instead of returning nothing, so a misconfigured setup falls
  back to the old (noisier but working) behavior rather than going dark.
- Verified end-to-end through the real `_build_enemy_alarm_text()` code
  path against a live 12-pilot Local roster with a synthetic enemy-icon
  position: correctly isolated the single targeted pilot instead of
  reporting all twelve.
- 5 new regression tests for the row-correlation logic
  (`ReadLocalNamesNearRowsTests`), plus updated existing OCR tests for the
  new position-preserving internals.

## [6.3.27] 2026-07-16

### Fixed — alarm-time OCR finally feeds the intel pipeline (#205)

- **Alarm-time OCR returned zero names on every alarm** while the Settings
  "Test OCR on Region" button worked perfectly — because
  `_build_enemy_alarm_text()` executes on the engine's asyncio loop thread
  while that loop is running, and `_ocr_with_winrt`'s
  `loop.run_until_complete(...)` raises `RuntimeError: Cannot run the event
  loop while another loop is running` in that context (the test button runs
  on a plain worker thread, where the same call is legal). The error was
  swallowed at DEBUG level, leaving only the misleading "check your OCR
  region" message. `_ocr_with_winrt` is now loop-aware: with a running loop
  present it executes recognition on a short-lived worker thread with its
  own event loop (15 s timeout); the plain-thread path is unchanged. This
  was the last blocker in the OCR chain (#193/#194/#199 fixed real bugs
  that sat in front of it). Verified live: simulated alarm context against
  the real screen captured 6 pilots and produced the full ESI/KOS/zKB/threat
  intel block.
- **zkillboard character links on pilot intel lines** — every ESI-resolved
  pilot in the alarm intel output now ends with
  `| zkillboard.com/character/<id>/` (the v6.3.25 links only covered
  intel-channel messages, not the alarm output).
- **dotlan system links on kill reports** — "Recent kills in X" / "No recent
  kills found for X" lines now append `— dotlan.net/system/X`.

## [6.3.26] 2026-07-15

### Fixed — OCR/ESI/KOS intel pipeline completion (#201, #202, #203, #204)

- **#203 — KOS check no longer gated behind ESI success.** `_augment_with_esi`
  previously returned immediately when `client.lookup_many()` resolved zero
  characters (network issue, ESI 5xx, a misread/nonexistent name), skipping
  the KOS check entirely for every pilot in that alarm — including any that
  genuinely were on a KOS list. KOS checks (CVA/custom APIs) only need a bare
  name, not ESI data. The per-pilot loop now iterates over the OCR/log name
  list directly (not the ESI results list), enriching each with ESI data when
  available and falling back to a name-only KOS check (empty corp/alliance)
  when it isn't. Verified against real OCR capture data with an injected
  unresolvable name: it correctly gets an "ESI lookup unavailable" line and a
  KOS check attempt, while resolvable names in the same batch keep their full
  corp/alliance/age/zKillboard enrichment.
- **#201 — "Test OCR on Region" now runs the real intel pipeline.** The
  Settings dialog's OCR test found pilot names and displayed them, but never
  fed them into ESI/KOS/zKillboard — confirming OCR worked told you nothing
  about whether your intel setup (KOS lists, threat tiers) would catch
  anything. Added `AlertAgent.run_intel_check()`, a public wrapper around
  `_augment_with_esi`, and wired the Settings dialog to call it automatically
  whenever the test finds ≥1 name — results stream into the main window log,
  identical to a real alarm.
- **#204 — OCR test success now warns when the feature isn't actually
  enabled.** Confirming OCR works in the test didn't mean OCR would run
  during real alarms — that also requires checking "Read pilot names from
  Local on alarm" and saving. The test result now shows an inline warning
  when that checkbox is currently unchecked.
- **#202 — removed a redundant, blocking second OCR capture; honest fallback
  messaging.** When alarm-time OCR found nothing, `_augment_with_esi` retried
  the *exact same* screen region synchronously inside itself moments later —
  never had a real chance of succeeding where the first attempt just failed,
  and blocked the engine's event loop for a full OCR pass while it tried.
  Removed. The Local-log "joined the channel" fallback is kept (it correctly
  catches a hostile who just jumped/warped in — the common alarm-trigger
  case) but the "no names found" message now explains *why*, distinguishing
  OCR-enabled-but-empty ("hostile may already have been in-system — check
  your OCR region") from ESI-only/no-OCR mode ("enable OCR for coverage of
  already-present pilots") instead of one generic dead-end message.

## [6.3.24] 2026-07-15

### Fixed — OCR name detection root cause (#199)

- **WinRT OCR line structure.** `OcrResult.text` flattens the entire
  recognition result into ONE space-joined string with no newlines, so
  `parse_eve_names` (which splits on newlines) saw a single 120+-char token,
  failed the 3–37-char name regex, and returned zero names on every capture —
  while the OCR engine was reading every pilot perfectly. This was the actual
  cause behind the v6.3.6–v6.3.22 miss reports; the pixel-format fixes were
  necessary but not sufficient. `_winrt_recognize_async` now builds its output
  from `result.lines` (one pilot per line). Verified end-to-end on a real
  Windows machine against two live Local captures: 10/10 lines extracted,
  9 pilots resolved through ESI, full intel/threat analysis produced.
- **Icon glyphs misread as letter tokens.** EVE's standing icons frequently
  OCR as short letter tokens (`"S Naveia"`, `"(S DamonR"`), which the
  non-alphanumeric strip can't remove. `parse_eve_names` now also emits the
  remainder after a leading 1–2-char token; ESI exact-match resolution picks
  the right candidate and drops the wrong one silently.
- **Preprocessing retuned with measured data.** Accuracy matrix on a real
  capture: 3× upscale with no contrast boost scores 8/10 exact names vs 6/10
  for the previous 2×+invert+contrast pipeline — the contrast×2.0 step was
  actively harmful (5/10 at 3–4×), and inversion is a no-op for WinRT. New
  pipeline: grayscale → 3× LANCZOS → RGBA.
- **Python 3.13+ support.** `winsdk` stopped publishing wheels after
  Python 3.12 (source build fails without a C compiler). `ocr_local` now
  falls back to the maintained `winrt-*` namespace packages (same OS engine),
  declared in `pyproject.toml` for `python_version >= '3.13'`.

## [6.3.6] 2026-07-14

### Changed — Bundled OCR (Windows.Media.Ocr)

- **OCR no longer requires an external Tesseract installation.** On Windows 10
  1607+ (all modern Windows), the OCR engine is already part of the OS.
  `winsdk` (a small ~2 MB Microsoft-maintained package) is now a base
  dependency on Windows (`sys_platform == "win32"`) that provides Python
  bindings to `Windows.Media.Ocr`.

- `evealert/tools/ocr_local.py` rearchitected with two independent backends:
  1. **Windows.Media.Ocr** (priority) — `is_winrt_ocr_available()`,
     `_ocr_with_winrt(pil_img)`.  Runs recognition via an asyncio event loop
     isolated from the alert daemon loop to avoid conflicts.
  2. **pytesseract + Tesseract** (fallback) — `is_tesseract_available()`.
     Unchanged behaviour; requires `pip install ".[ocr]"` plus the Tesseract
     binary for users on non-Windows platforms or older Windows builds.

- Settings dialog **"Check Tesseract"** button renamed to **"Check OCR"**.
  Status messages now distinguish the active backend:
  - `✓ OCR ready (Windows.Media.Ocr — built-in)` — no install needed
  - `✓ OCR ready (Tesseract 5.x.y)` — Tesseract fallback active
  - `✗ OCR unavailable` — neither backend found

- `build-windows` extra no longer includes `pytesseract` (not needed in the
  bundled .exe).

## [6.3.0] 2026-07-13

### Added — UX & Onboarding (Epic v6.3)

- **#167 Log pane ergonomics** — `LogPane(QWidget)` replaces the bare
  `QPlainTextEdit`. New toolbar: All / Alarms / Intel / System category
  filter buttons (driven by message color tags), free-text search, and a
  Pause toggle (ring buffer of 2 000 entries keeps filling while paused; view
  re-renders on resume). Right-click context menu: Copy line / Copy all visible.
  `MainWindow.append_log` delegates to the pane; web-server mirroring unchanged.

- **#168 Tray icon state** — Three icon variants generated at runtime (grey =
  stopped, green = running, red = alarm flash) by compositing a status dot onto
  the base icon with `QPainter`. `QtBridge.alarm_fired` signal (new) emitted on
  every red log message; connected to `AppTray.on_alarm()` which flashes red for
  10 s then reverts. Tooltip updated: `EVE Alert — Running · last alarm HH:MM:SS`.
  New "Mute alarms" checkable tray menu item writes `server.mute` via
  `load_raw()/save()` and hot-reloads the engine.

- **#165 HotkeyEdit capture widget** — `HotkeyEdit(QPushButton)` enters capture
  mode on click, grabs the keyboard, converts the pressed Qt key to a
  pynput-compatible string, and rejects conflicting bindings with a tooltip
  warning. Replaces the two free-text QLineEdit hotkey fields in Settings;
  F3 (profile cycle) and F4 (status readout) now exposed as remappable bindings.
  `DEFAULT_HOTKEYS` expanded; `reload_hotkeys()` reads all four bindings.

- **#166 Profile manager UI** — `ProfileManagerDialog(QDialog)` shows a profile
  list (user profiles + read-only built-in space profiles) and a diff table
  (key / base value / profile value). Actions: New, Duplicate, Rename, Delete,
  Set Active, Remove override. "Save current settings as profile" opens a
  checkbox-list dialog so profiles stay minimal overlays. Settings dialog profile
  bar simplified to combo + "Manage Profiles…" button.

- **#164 First-run onboarding wizard** — `OnboardingWizardDialog` auto-shown on
  first launch (when alert region is unconfigured and
  `ui.onboarding_completed` is false). Four pages: Welcome (EVE window detect),
  Alert Region (RegionOverlay + live mss thumbnail), Sound & volume (test alarm),
  Done (start detection checkbox). Persists region + volume; marks
  `ui.onboarding_completed = True` on Finish or Skip.
  Re-launchable via **Settings → Detection → "Run Setup Wizard…"**.

---

## [6.2.0] 2026-07-13

### Fixed

- **#154 F1/F2 hotkey hijack** — hotkeys only fire when Config Mode dialog is
  `isVisible()`; after the dialog closes F1/F2 are silently ignored so EVE
  in-game fire-weapons/functions work normally.

- **#155 Space profiles crash** — `SettingsStore` gains a `set(path, value)`
  method and `save()` now accepts an optional dict (defaults to the in-memory
  cache). Wrong KOS key `kos.cva_kos_enabled` corrected to `kos.cva_enabled`
  in all three built-in profiles.

- **#156 Profile overlay baked into base settings** — `load()` now applies the
  profile overlay to a *copy* only; the internal cache always holds raw base
  values. New `load_raw()` method exposed for callers (Settings dialog) that
  need to read without overlay. Save and Apply both call `load_raw()` so profile
  values are never permanently written to disk.

- **#157 ESI client-ID placeholder** — replaced misleading "Leave blank for
  built-in public client" text with "Required — register a free app at
  developers.eveonline.com"; added a muted help label with app type, callback
  URL, and format requirements.

- **#158 Heatmap Qt thread violations** — replaced cross-thread widget writes
  and `QTimer.singleShot` (which crashes from non-Qt threads) with a `Signal(object)`.
  Worker emits the result dict (or Exception) to `_on_heatmap_ready` which runs
  safely on the main Qt thread.

- **#159 Tests writing to real statistics.json** — `get_stats_path()` now
  respects `EVEALERT_STATS_PATH` env var; both test classes set it to a
  temp-dir path in setUp and unset it in tearDown.

- **#160 Blocking Discord webhook** — all three `dhooks_lite.execute()` calls
  wrapped in `loop.run_in_executor(None, ...)` so the asyncio detection loop
  is not stalled during HTTP round-trips.

- **#161 Hotkey remaps need restart** — bindings stored on `self._hotkey_alert_key` /
  `self._hotkey_faction_key`; listener closure reads them on every keypress. New
  `reload_hotkeys()` called from Settings Save and Apply so new bindings take
  effect immediately without restarting.

- **#162 Qt shell polish** — removed accumulating dead `accepted.connect` signal
  on every `_open_settings()` call; `_save_and_apply` now calls `self.hide()`
  (Save closes); `_apply_only` saves without closing; `_build_tray` wrapped in
  try/except with `None` fallback; all three `self._tray` call sites guarded.

- **#163 Heatmap zKB caps at 200 kills** — `_zkb_kills_for_system` now paginates
  up to 5 pages (≈ 1 000 kills) stopping early when a page returns fewer than
  200 entries; 500 ms polite pause between pages.

---

## [6.1.0] 2026-07-13

### Added

- **#145 Wormhole signature delta monitoring** — `DscanWatcher` now counts
  "Cosmic Signature" entries on each D-scan poll and fires `on_new_signature(old, new)`
  when the count increases. Alertmanager logs a red warning: *"NEW SIGNATURE
  DETECTED: 1 new cosmic sig(s) (2 → 3) — possible wormhole connection!"*.
  Configurable via `dscan.alert_new_signatures` (default True).

- **#150 Ship cross-reference via zKillboard** — After the zKillboard profile
  line, if the pilot's `top_ship` matches any type currently visible on D-scan,
  a red `⚠ MATCH: <pilot> typically flies <ship> — that type is on D-scan NOW`
  line is logged. `DscanWatcher` now also tracks type-column values in
  `_visible_types`; exposed via `current_visible_types` property.

- **#152 F4 status readout hotkey** — Press **F4** to have EVE Alert speak the
  current threat situation aloud: local hostile count, highest-urgency D-scan
  class, adjacent system kills, and composite threat score. Degrades gracefully
  when TTS is unavailable. Also logs the summary to the log pane.

- **#153 EVE automation bridge** — When `automation.enabled = true` and
  `automation.webhook_url` is set, EVE Alert POSTs `{type, text, timestamp}`
  JSON to the configured URL on every alarm. AutoHotkey / PyAutoGUI scripts can
  listen on localhost and trigger an in-game keypress (safe-spot warp, dock).
  The built-in web server also exposes `GET /api/alarm/latest` for polling-based
  consumers.

- **#148 Constellation threat heatmap** — New `evealert/tools/threat_heatmap.py`
  with `get_constellation_heatmap(system, days=7)`. Resolves the constellation
  via ESI, fetches per-system kill history from zKillboard, and bins kills into
  24-bucket UTC histograms. Results are session-cached for 1 hour. Accessible
  from the new **Threat Heatmap** tab in the Statistics window.

- **#151 Historical threat pattern alerts** — `_peak_hours_monitor()` async task
  runs hourly and checks the upcoming hour's kill rate against the 7-day average
  for the configured constellation. When the rate is ≥ `peak_threshold_multiplier`
  (default 1.5×), a yellow warning fires 15 minutes before the hour. Configurable
  via `intelligence.peak_hours_warning` and `intelligence.peak_threshold_multiplier`.

- **#149 Mobile notification setup wizard** — New `NotificationWizardDialog`:
  a 4-page guided QDialog that walks through Telegram, Pushover, or ntfy.sh
  setup with inline registration instructions and a live test step before saving.
  Accessible via **Settings → Alerts & Sound → "Setup Mobile Notifications…"**.

---

## [6.0.0] 2026-07-13

### Added — AFK Situational Awareness

- **#140 D-scan ship class classification** — `ShipThreatClass` enum (TACKLE /
  DICTOR / FORCE_RECON / COVERT_OPS / CYNO / COMBAT / INDUSTRIAL / UNKNOWN)
  with per-class urgency weights. `classify_ship()` maps ship names/types via an
  ordered `SHIP_CLASS_MAP` list. `DscanEntry` gains a `threat_class` field; D-scan
  log lines now include human-readable labels such as
  `D-SCAN RED: Sabre [DICTOR — bubble incoming]`.

- **#139 TTS voice alerts** — optional text-to-speech readout of alarm details
  using `pyttsx3` (Windows SAPI5, no extra install on Windows). `speak()` runs on
  a daemon thread so the detection loop is never blocked. Settings: **Alerts &
  Sound → Text-to-Speech** — enable toggle, speech rate (50–400 wpm), and Check /
  Test buttons. Install with `pip install "evealert[tts]"`.

- **#141 Composite threat score** — `compute_threat_score()` aggregates up to
  five signals (local hostile count, KOS status, zKillboard danger ratio, D-scan
  ship class, adjacent system kills) into a 1–10 score with a
  `CAUTION / HIGH / CRITICAL` label and reasoning list. Cynosural-field detection
  always returns 10 / CRITICAL. Logged after the ESI intel block on every Enemy
  alarm.

- **#144 Per-enemy re-alert after sustained presence** — a new
  `alerts.rearm_minutes` setting (0 = disabled) re-arms the alarm for a pilot
  who has been continuously present in local beyond the configured time window.
  Replaces the previous bare-float `_seen_enemies` dict with a `_EnemySighting`
  record that tracks `first_seen`, `last_alarm`, and `rearm_at`.

- **#143 Pre-configured space profiles + F3 hotkey** — three built-in profiles
  (Null-sec, Wormhole, High-sec) write a coordinated set of settings overrides in
  one call and reload the agent without restart. Press **F3** to cycle through
  profiles while the overlay is running; current profile is logged in cyan.
  Profiles tune D-scan alerts, escalation threshold, TTS, zKillboard, KOS, and
  re-alert interval.

- **#142 Intel channel improvements** — `intel_parser.py` parses free-text intel
  channel messages into `IntelReport` objects (system name, hostile count, clear
  signal, ship mentions). `IntelWatcher` gains an `on_intel(IntelReport)` callback
  that fires alongside the existing raw-line callback. Hostile reports are logged
  as `Intel: N hostile(s) in SYSTEM [ships]` in red; clear signals as
  `Intel: SYSTEM CLEAR` in green. When a home system is configured, an async ESI
  route lookup appends the jump count (`Intel: D7-ZAC is 3 jumps from 1DQ1-A`).

- **#146 Cynosural field detection** — when a cynosural field object or cyno ship
  appears on D-scan, an immediate CRITICAL alarm fires:
  `⚠ CYNO DETECTED: <name> — CAPITAL DROP IMMINENT — LEAVE NOW`. Bypasses the
  normal cooldown so each re-light triggers a fresh alarm. Also announces via TTS
  when TTS is enabled.

- **#147 Standings-aware local monitoring** — new
  `esi_oauth.standings_filter_blues` setting. When enabled alongside ESI OAuth,
  pilots with a personal standing ≥ +5.0 are labelled `[ALLY]` in green and
  excluded from KOS checks, threat-score counting, and hostile display — reducing
  noise in mixed-fleet space.

---

## [5.0.1] 2026-07-13

### Fixed

- **#132** — zKillboard revoked the `limit/` URL modifier; kills-on-alarm intel was completely broken. Removed the `/limit/{n}/` path segment; client-side slicing is unchanged.
- **#133** — zKillboard returns `[null]` for empty result sets. Added `clean_zkb_entries()` helper that normalises `[null]` → `[]`; applied at all 4 affected sites: `neighbor_monitor._kills_15min`, `universe._zkb_kills_last_hour`, `zkillboard._fetch_kills`, and `fleet_context._zkb_get`. Phantom adjacent-monitor alerts and false-positive route-threat warnings are gone.
- **#134** — zKillboard `topLists` category key is `"shipType"`, not `"ship"`. Top-ship intel line now resolves correctly. Kill/loss field names renamed from `kills_30d`/`losses_30d` to `kills_total`/`losses_total` (data is all-time, not 30-day); display strings updated to say `(all-time)`. `dangerRatio` read from zKB's own field when available.
- **#135** — CVA KOS domain (`kos.cva-eve.com`) is offline. `cva_enabled` now defaults to `False` in both `KosChecker` and `DEFAULT_SETTINGS`. A new `_dead_sources` set disables any KOS source that raises a connection error for the rest of the session (one warning log, no repeated attempts).
- **#136** — EVE SSO login would open the browser and hang 120 s with a fake placeholder client ID (`evealert_public_client`). `_DEFAULT_CLIENT_ID` is now `""`. `EsiAuth.login()` validates that the client ID is a 32-character lowercase hex string (the format issued by developers.eveonline.com) before opening the browser; blank or malformed IDs return `False` immediately with a descriptive log message.
- **#137** — HTTP User-Agent strings were stale, mismatched across modules, and missing entirely from several `httpx.AsyncClient` calls. Introduced `evealert/tools/http_common.py` with a canonical `USER_AGENT` and `DEFAULT_HEADERS`; applied to all `AsyncClient` constructions in `zkillboard`, `universe`, `esi_standings`, `fleet_context`, `wormhole`, `esi_auth`, and `neighbor_monitor`.
- **#138** — EVE SSO login in the Settings dialog crashed with `cannot import name 'ESIAuth'` (class is `EsiAuth`), then `load_token()` (method does not exist), then `start_oauth_flow()` (method does not exist, and `login()` is async). Fixed: uses `get_esi_auth()` factory, reads `auth.is_authenticated` / `auth.character_name` properties, and runs `asyncio.run(auth.login())` in a `_LoginThread(QThread)` so Qt's event loop is not blocked.

## [5.0.0] 2026-07-13

### Changed — UI completely rewritten (PySide6 migration, #123–#131)

- **Replaced customtkinter/Tkinter with PySide6 (Qt 6, LGPL)** — the entire
  presentation layer is rebuilt from scratch under `evealert/ui/`.  The detection
  engine (`evealert/manager/`, `evealert/tools/`) is unchanged.
- **New dark-themed UI** — 510-line QSS stylesheet; consistent color palette with
  primary/danger/warning button variants, stat cards, monospace log pane.
- **Main window** — status indicator (● Running / ● Stopped), Start/Stop/Exit row,
  Config Mode / Settings / Statistics buttons, region toggle buttons, scrollable
  colored log pane, system tray with Start/Stop/Show/Exit menu.
- **Settings dialog** — tabbed, scrollable, resizable (no more 1200 px fixed
  height overflow).  42 settings auto-generated from the field registry + all
  non-registry sections (regions, thresholds, sounds, webhooks, hotkeys, ESI
  OAuth).  All settings sections reachable on a 1080p display (#107).
- **Config mode** — fullscreen translucent QRubberBand drag-to-select overlay for
  Alert and Faction regions (replaces the previous guide-only dialog).  F1/F2
  hotkeys trigger the overlay directly; HiDPI scaling handled via
  `devicePixelRatio()`.
- **Statistics window** — 3×2 stat cards + sortable `QTableWidget` history; Sessions
  tab with View / Export CSV / Delete.
- **Image manager** — thumbnail list with 32×32 QIcon previews, 240×240 preview
  pane, Add (with cv2 validation) / Remove with live template reload.
- **Per-image threshold editor** — scrollable slider rows, per-image override or
  Clear to global.
- **System tray** — `QSystemTrayIcon` (replaces pystray daemon thread); double-click
  to restore, minimize-to-tray on close.
- **Engine/GUI decoupling** — `SettingsStore` owns all JSON persistence; `UIBridge`
  protocol routes all engine→UI calls through Qt signals (no `after(0, ...)`)
  (#124).

### Removed
- `customtkinter`, `pystray`, `screeninfo` dependencies removed from
  `pyproject.toml`.
- `evealert/menu/` package deleted (setting.py, main.py, config.py, statistics.py,
  image_manager.py, threshold_editor.py).
- `evealert/tray.py` (pystray) and `evealert/tools/overlay.py` (Tk marquee) deleted.

### Added
- `evealert/settings/store.py` — `SettingsStore` with atomic save, `changed` flag,
  dotted-path `get()`, and `DEFAULT_SETTINGS` (moved from setting.py).
- `evealert/settings/fields.py` — `FieldSpec` namedtuple, `FIELDS` registry (42
  entries), `TAB_ORDER`, `apply_registry_fields()` / `save_registry_fields()`.
- `evealert/bridge.py` — `UIBridge` protocol (toolkit-agnostic engine/GUI contract).
- `evealert/ui/` — Qt UI package: `app.py`, `theme.py` + `theme.qss`, `main_window.py`,
  `qt_bridge.py`, `tray.py`, `settings_dialog.py`, `config_dialog.py`,
  `region_overlay.py`, `statistics_window.py`, `image_manager.py`,
  `threshold_editor.py`.

## [4.2.0] 2026-07-12

### Added
- **Diagnostic mode** — new **Alerts & Sound → Diagnostics** settings section with:
  - "Enable diagnostic (verbose) logging" toggle: raises all app loggers to DEBUG for the duration of a session, capturing full call-path detail in the log files.
  - **Log Level** dropdown (Debug/Info/Warning/Error): surfaces the previously-hidden `log_level` setting.
  - **Export Diagnostics Bundle** button: packages all log files + a secrets-redacted copy of your settings + a system/environment info snapshot into a single `eve-alert-diagnostics-<timestamp>.zip` in the config directory, then reveals the file in your OS file manager.
  - Log path label showing where logs are stored.
- `EVEALERT_DEBUG=1` environment variable: enables verbose DEBUG logging before the UI loads (useful for diagnosing crashes at startup).
- `evealert/settings/diagnostics.py`: `gather_context()` (app version, OS, Python, monitors, EVE chatlog dir detection, OCR/Tesseract availability, feature flags), `_redact_settings()` (blanks push tokens, OAuth client ID, webhook URLs), `create_bundle()` (creates the export zip).

## [4.1.0] 2026-07-12

### Added
- **OCR pilot-name detection (#98)** — optionally reads pilot names from a configured Local-chat screen region on each Enemy alarm (via Tesseract/pytesseract) and merges them into the existing KOS / ESI / zKillboard intel pipeline. Off by default and import-guarded: degrades to a no-op with a log message when the Tesseract engine is not installed. New settings under **Intel & ESI → OCR Name Detection** (enable toggle + capture region x1/y1/x2/y2). Requires installing the Tesseract OCR engine separately.

### Fixed / Changed (post-4.0 hardening)
- Settings save no longer wipes saved profiles / per-image thresholds / active profile (#99, #108); settings UI writes now round-trip.
- Settings window rearchitected into a tabbed, scrollable layout with a persistent Save/Apply/Close footer and a declarative field registry (#107).
- ESI name→ID resolution migrated to `POST /universe/ids/` (removed public `/search/` endpoints) — restores zKillboard/pilot-intel/adjacent/route/sov features (#110).
- EVE SSO OAuth now works: PKCE, corp-structures scope, single-client structure fetch, JWT-based character identity, and per-login state validation (#104, #115, #105).
- External-API integrations fixed against real response shapes: Eve-Scout v2, embedded ZKB attackers, kills+losses feeds, honest WH class, KOS corp/alliance checks, D-scan UTF-16/type-column parsing (#101).
- Vision robustness: skips unreadable template images, correct debug window name, guarded error path (#111, #112, #113).
- Duplicate enemy alarms deduped by quantized position (#100); asyncio monitors cancelled cleanly on stop (#102); settings hot-reload no longer mutates Tk from the alert thread (#114); web dashboard HTML renders (#109); credential/SSRF hardening (#105); robustness polish incl. rate limiting and bounded caches (#106).
- Test suite expanded to 237 tests covering the v3.3–v4.1 modules (#103).

## [4.0.0] 2026-07-11

### Added
- **v3.3**: D-scan log watcher — tails EVE D-scan files, classifies ships into RED/ORANGE/YELLOW/GREEN threat tiers, fires probe detection alarm, maintains session timeline
- **v3.4**: KOS checker — auto-queries CVA KOS API and any configured custom KOS endpoints per pilot; local hostile list matching
- **v3.5**: Push notifications — Telegram Bot, Pushover, and ntfy.sh channels; auto-screenshot on alarm; alarm escalation counter
- **v3.6**: Wormhole awareness — Thera connection monitor (Eve-Scout API), WH static type inference, WH fleet drop heuristic
- **v3.7**: Fleet context — hostile fleet composition analysis, timezone activity profiling, killmail notifications for tracked characters
- **v4.0**: EVE SSO OAuth2 login — full authorization code flow via local callback server; access/refresh token lifecycle with auto-refresh; personal standings auto-classify in Local; fleet membership display on start; structure fuel-expiry warnings; standings-based color coding in pilot intel display



### Added
- **Neighboring system kill monitor** (#73) — Optional async background task polls Zkillboard every 2 minutes for kills in systems within a configurable jump radius (1–5). Per-system 10-minute cooldown prevents alert spam. Posts: `"Adjacent: N kill(s) in [System] (X jumps away)"`.
- **Route threat assessment** (#74) — "Check Route" button in Settings triggers a BFS path from the current system to a configured destination, checks each hop for kill activity (last hour via Zkillboard), and posts a summary with `[danger]`/`[caution]`/`safe` classification per hop.
- **Pipe/pocket detection** (#75) — On detection start, posts system type based on gate count: `"dead-end"` (1 gate), `"pipe"` (2 gates), `"crossroads"` (3+ gates). Helps assess whether incoming neutrals are through-traffic or specifically targeting you.
- **Sovereignty display** (#76) — On start, fetches the current system's sovereignty holder from the ESI bulk sov map and posts: `"Sov: Alliance [Ticker] — IHub: active | TCU: active"`. Re-polls every 5 minutes and posts a yellow `SOV CHANGE` alert if the controlling alliance changes.

### Changed
- `DEFAULT_SETTINGS` gains an `adjacent` block: `enabled`, `max_jumps`, `poll_interval`, `min_kills`, `destination_system`.
- Settings window height 1050 → 1200. New "Adjacent System Monitor" section with enable checkbox, max-jumps/min-kills/poll-interval entries, destination system field, and "Check Route" button.
- `AlertAgent.start()` now creates `_display_system_info()` (one-shot) and `_sov_monitor()` (background poll) tasks automatically.

### New files
- `evealert/tools/universe.py` — `UniverseCache` with BFS jump-graph, system ID/name resolution, gate counting, sovereignty lookup, route threat assessment; `SovInfo` and `RouteLeg` namedtuples
- `evealert/tools/neighbor_monitor.py` — `NeighborMonitor` async poll loop

## [3.1.0] 2026-07-11

### Added
- **Pilot background check** (#69) — ESI lookups now include character age (days since creation), total corps held (from corp history), and a cyno-alt heuristic: pilots < 30 days old trigger a "YOUNG PILOT — possible cyno/scout alt" warning.
- **Kill/death profile** (#70) — Zkillboard stats endpoint queried per pilot: 30-day kills, losses, danger ratio %, and top ship type posted below each pilot's corp/alliance line.
- **Alliance threat tier** (#71) — New "Threat Tiers" section in Settings. Add name/corp/alliance substrings mapped to red / orange / yellow tiers. Matched pilots are prefixed `⚠ [KOS-RED]`, `⚠ [HOSTILE]`, or `[CAUTION]`, and their log line is coloured accordingly.
- **Flashy security status alert** (#72) — New "Alert on flashy pilots (sec ≤ -5)" checkbox in Settings > ESI Augmentation. When enabled, pilots with security status ≤ -5.0 trigger a distinct red log line: "FLASHY: Name (sec: -7.2) — attackable in low-sec".

### Changed
- `CharacterInfo` NamedTuple extended with `age_days`, `security_status`, `corp_history_count`.
- `EsiLookup._fetch_character()` now makes an additional ESI call to `/v2/characters/{id}/corporationhistory/` to populate `corp_history_count`.
- `_augment_with_esi()` in `AlertAgent` fully rewritten to format all pilot intelligence into structured per-pilot log output.
- `DEFAULT_SETTINGS["esi"]` gains `alert_flashy: false`.
- `DEFAULT_SETTINGS` gains `threat_tiers: {}`.
- Settings window height 900 → 1050.

### New types/methods
- `KillProfile` NamedTuple: `kills_30d`, `losses_30d`, `top_ship`, `danger_ratio`
- `EsiLookup.get_zkillboard_profile(character_id)` — cached Zkillboard stats fetch
- `_compute_age_days(birthday_str)` — ISO-8601 → age in days helper

## [3.0.0] 2026-07-11

### Added
- **ESI augmentation** — When an Enemy alarm fires and ESI is enabled in Settings, a background task reads the Local chat log, extracts the names of recently joined characters, and looks them up via public ESI endpoints (no OAuth required). Corporation name and alliance name are posted to the log pane in cyan alongside the alarm. Configurable: separate toggles for show-corp and show-alliance. New `settings.json["esi"]` block.
- **Plugin system** — Drop any `.py` file into `~/.config/evealert/plugins/` to extend EVE Alert without modifying the core. Plugins may define `on_start()`, `on_stop()`, `on_enemy(system, timestamp)`, `on_faction(system, timestamp)`, and `on_intel(line)` hooks. Hooks run in a thread-pool executor so plugin errors are isolated. On startup the number of loaded plugins is shown in the log pane. New `settings.json["plugins"]` block.
- **Web status UI** — Optional local HTTP server (no extra dependencies) that serves a self-refreshing status dashboard at `http://127.0.0.1:<port>/`. Also exposes `GET /api/status` and `GET /api/log` JSON endpoints. Enabled via a new "Web Status UI" section in Settings (checkbox + port entry). New `settings.json["web_ui"]` block.
- `evealert/settings/helper.py`: `get_user_plugins_path()` — returns `~/.config/evealert/plugins/`, creating it on first use.

### Changed
- Settings window height increased from 720 to 900 to accommodate three new sections (ESI, Web UI).
- `DEFAULT_SETTINGS` gains: `esi`, `plugins`, `web_ui` blocks.
- `AlertAgent.stop()` now also stops the web server and calls `on_stop` plugin hook.
- `MainMenu.write_message()` mirrors every log line to the web server's in-memory circular buffer so the dashboard stays current.

### New files
- `evealert/tools/esi_standings.py` — `EsiLookup` async client + `CharacterInfo` namedtuple + `extract_joining_characters()` log parser
- `evealert/tools/plugin_loader.py` — `PluginManager` discovery/dispatch + `get_plugin_manager()` singleton
- `evealert/tools/web_server.py` — `WebStatusServer` async HTTP server + `append_to_log_buffer()`

## [2.6.0] 2026-07-11

### Added
- **Per-type sound cooldown** — separate cooldown timers for Enemy and Faction alarms. `cooldown_timer_enemy` and `cooldown_timer_faction` fields added to `settings.json`. Both default to 60 s. Configured via two new entry rows in Settings.
- **Custom webhook message template** — the Discord notification message is now a user-editable template stored in `settings.json["server"]["webhook_template"]`. Supported variables: `{alarm_type}`, `{system}`, `{time}`, `{count}`. Configurable via a new "Msg Template:" entry row in Settings.
- **Multiple webhook targets** — in addition to the existing "all events" webhook, users can now configure dedicated URLs for Enemy alarms and Faction alarms independently via new "Enemy Webhook / Faction Webhook" rows in Settings. Each target also supports a `min_count` threshold so the webhook only fires after a configurable number of session alarms of that type have occurred.
- **Startup version check** — on each detection start, an async background request to the GitHub Releases API compares the installed version against the latest release. If a newer version is available, a yellow message with the release URL is shown in the log pane. Completely non-blocking; silently suppressed if offline.

### Changed
- `DEFAULT_SETTINGS` gains: `cooldown_timer_enemy`, `cooldown_timer_faction`, `server.webhook_template`, `webhooks` block.
- Settings window height increased from 560 to 720 to accommodate the new rows.
- `AlertAgent.play_sound()` now uses per-type cooldown instead of a single shared value.
- `AlertAgent.send_webhook_message()` now formats the message using the template and dispatches to all configured targets (all-events + per-type), replacing the hardcoded `"Enemy Appears in {system}!"` string.
- Webhook reset message on alarm clear now uses the system name without the hardcoded "Alarm Reset:" prefix.

### New files
- `evealert/tools/update_checker.py` — `check_for_update()` async GitHub Releases version comparison

## [2.5.0] 2026-07-11

### Added
- **Stats persistence** — lifetime alarm totals (`total_alarms`, `total_by_type`) now survive application restarts. Stored atomically in the platformdirs config directory as `statistics.json`. Loaded back into `AlarmStatistics` on startup via `load_lifetime()`. Saved after every alarm and on clean stop.
- **Per-session reports** — each detection run is saved as `session_YYYYMMDD_HHMMSS.json` in a `sessions/` sub-directory alongside `settings.json`. Reports include start/end time, duration, alarm counts by type, and the full event history.
- **Statistics Sessions tab** — the Statistics window now has two tabs: "Live Stats" (the existing real-time view) and "Sessions" (a scrollable list of past session JSON files). Each session row has a View button (shows details in a text pane below the list) and a red Delete button. An "Open Folder" button opens the sessions directory in the OS file manager.
- **Zkillboard kill intelligence** — when "Enable Zkillboard lookup on alarm" is checked in Settings, the first Enemy alarm in a configurable cooldown window (default 5 min) triggers an async ESI + Zkillboard lookup for the configured system name. The top 3 recent kills (victim name, ship, ISK value, time) are posted to the log pane in yellow.
- **Intel channel log watcher** — when "Watch EVE intel chat log" is enabled in Settings, a background task tails the most-recently-modified EVE chat log whose filename contains the configured channel name (e.g. "Intel"). New chat lines are posted to the log pane in cyan in real-time as they appear.
- Intelligence section in the Settings window with two checkboxes (Zkillboard, Intel log) and an Intel Channel Name text field.
- `cyan` and `yellow` log colours registered in the main log textbox.

### Changed
- `DEFAULT_SETTINGS` gains an `intelligence` block: `zkillboard_enabled`, `zkillboard_cooldown`, `intel_log_enabled`, `intel_log_channel`.
- Statistics window geometry increased to 520×600 to accommodate the tabbed layout.
- `AlertAgent.stop()` now saves lifetime stats and a session report before shutting down.
- `AlertAgent.load_settings()` reads `intelligence` settings and sets internal flags.

### New files
- `evealert/settings/stats_store.py` — `load_lifetime_stats()`, `save_lifetime_stats()`, `save_session_report()`, `list_session_reports()`
- `evealert/tools/zkillboard.py` — `ZkillboardClient` with ESI system lookup + Zkillboard kill fetch; module-level `get_client()` singleton
- `evealert/tools/intel_watcher.py` — `IntelWatcher` async tail loop + `get_eve_chatlog_dir()` / `find_intel_log()` helpers

## [2.4.0] 2026-07-11

### Added
- **Named detection profiles** — save and load named snapshots of all detection settings (regions, thresholds, cooldown, webhook, hotkeys, sounds). Profile selector at the top of the Settings window with Save, New, Load, and Delete buttons. Profiles stored in `settings.json` under the `profiles` key.
- **Custom sound library** — browse for any WAV file to use as the enemy alarm or faction alarm. "Browse Alarm..." and "Browse Faction..." buttons in Settings. Custom sound paths stored in `settings.json["sounds"]`; automatically falls back to bundled sounds if the file is missing.
- **Per-image threshold control** — override the global detection threshold for individual template images. "Per-Image Thresholds..." button opens a modal editor with a toggle + slider per template. Stored in `settings.json["image_thresholds"]` as `{basename: int_or_null}`. `null` means use the global `detectionscale` value.
- **Image management UI** — "Image Manager" button in Config Mode. Add custom template images (copied to the platformdirs user `img/` directory), remove user-added images, reload the detection engine without restarting. Bundled images shown read-only.
- `get_user_img_path()` in `evealert/settings/helper.py` — returns the writable user image directory alongside `settings.json`; created automatically on first use.
- `_load_image_files()` now scans both the bundled `img/` directory and the user `img/` directory.

### Changed
- `DEFAULT_SETTINGS` gains: `active_profile`, `profiles`, `sounds`, `image_thresholds`
- `Vision.__init__` now stores `needle_paths` for per-image threshold filename lookup
- `Vision.vision_process()` accepts `per_image_thresholds` dict to override threshold per template
- `Vision.find()` and `find_faction()` accept `per_image_thresholds` and pass it through
- `AlertAgent.load_settings()` reads `sounds` and `image_thresholds` from settings
- `AlertAgent.run()` now passes `self.image_thresholds` to `find()` and `find_faction()`
- Settings window height increased from 400 to 560 to accommodate new rows
- `alertmanager.py` uses `self._alarm_sound` / `self._faction_sound` instance attributes instead of module-level constants for alarm playback

### New files
- `evealert/menu/image_manager.py` — `ImageManagerWindow`
- `evealert/menu/threshold_editor.py` — `ThresholdEditorWindow`

## [2.3.0] 2026-07-11

### Added
- **System tray** — EVE Alert now minimizes to the system tray instead of closing. The X button hides the window; the tray icon provides Show, Start Detection, Stop Detection, and Exit menu items. Requires `pystray>=0.19` (bundled in releases).
- **Auto-detect EVE window** — "Detect EVE Window" button in Config Mode finds the running EVE Online client and pre-fills both region coordinates with the full window bounds. Supported on Windows (`pygetwindow`) and macOS (`osascript`). Regions can still be refined with F1/F2.
- **Configurable hotkeys** — Alert Region and Faction Region keys are now configurable in Settings. Defaults remain F1/F2. Enter any key name (e.g. `f3`, `g`, `home`) and click Save. ESC remains hardcoded for aborting region selection.
- **Config popup screen clamping** — Config Mode and Settings windows no longer open partially off-screen when the main window is near the display edge.
- **Lazy window creation** — Config Mode and Settings windows are now created on first open rather than at startup, eliminating the macOS window flash on launch.

### Changed
- `pyproject.toml`: Added `pystray>=0.19` as a runtime dependency; added `[windows]` optional extra for `pygetwindow>=0.0.9`
- Release pipeline: Windows build now installs `.[build-windows,windows]` to bundle `pygetwindow`
- `DEFAULT_SETTINGS` now includes `hotkeys` section: `{"alert_region": "f1", "faction_region": "f2"}`

## [2.2.0] 2026-07-11

### Fixed (Critical)
- Thread safety: All Tkinter widget mutations from the alert daemon thread now go through `self._ui()` / `self.main.after(0, ...)` — prevents non-deterministic crashes on Windows and macOS
- OpenCV debug window (`cv.imshow`) calls moved out of the background thread path; `detection_image` variable is now always bound before the debug check
- Non-atomic settings write replaced with write-to-temp + `os.replace()` — crash mid-write no longer corrupts `settings.json`
- `os.listdir()` for template images moved from module import time into `AlertAgent.__init__()` with try/except — missing `img/` directory now shows a user-facing error instead of crashing before the UI opens
- `AlertAgent` coordinates (`x1`, `y1`, `x2`, `y2`) initialized to 0 in `__init__` — no more `AttributeError` if validation fails early
- `stop()` now guards against `self.loop is None` — safe to call before `start()`
- `vision_faction_thread` now resets `self.faction = False` on screenshot failure — prevents indefinite alarm loop

### Fixed (High)
- `run()` now catches `Exception` broadly — silent loop death on non-ValueError exceptions prevented
- Faction screenshot failure now resets stale `True` state
- Windows overlay region coordinates corrected: `_x_offset` cached at `create_overlay` time and applied consistently in `on_button_release`
- Vision detection threshold default changed from float `0.5` (→ 0.005 after /100 → clamped to 0.1) to int `50` (→ 0.50) — correct behavior
- `StatisticsWindow`: only one instance allowed at a time; re-clicking focuses existing window
- Double-start race: `self.alert.running = True` set before thread launch; Start button disabled on click
- `DEFAULT_COOLDOWN_TIMER` (constants.py) used as `cooldown_timer` default in `DEFAULT_SETTINGS` — single source of truth
- macOS release CI job now correctly installs `.[build-macos]` (was `.[build-windows]`)
- Release pipeline now requires `tests` workflow to pass before building binaries
- `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (deprecated in 3.10+)

### Fixed (Medium/Low)
- `load_settings()` exception catch broadened from `FileNotFoundError` to `OSError`
- `save_settings()` wrapped in `try/except OSError`; success message only shown on confirmed write
- `write_message()` calls in pynput keyboard listener thread marshalled via `self.after(0, ...)`
- Settings validator now validates `volume` (0–100) and `log_level` (known level names)
- German UI labels `X-Achse`/`Y-Achse` changed to `X-Axis`/`Y-Axis`
- `setup_mac.py` reads `__version__` dynamically from `evealert/__init__.py`
- Typos fixed: `factiom_vision_opened` → `faction_vision_opened`, `detection_treshhold` → `detection_threshold`, `vison_t` → `vision_t`
- Dead code removed: `enemy == "Error"` check (Vision never returns a string), stale `needle_img`/`needle_w`/`needle_h` class attrs, module-level `now = datetime.now()`
- Redundant `f"{alarm_text}"` replaced with `alarm_text`
- `on_mouse_drag` guarded against `self.rect is None` during concurrent `clean_up()`
- Overlay monitor cached at `create_overlay` time for correct multi-monitor coordinate math
- Haystack normalisation moved outside the per-template loop (computed once per frame)

## [2.1.1] 2026-07-11

### Fixed
- Resource files (`img/`, `sound/`) not found on Windows when running the PyInstaller `--onefile` build. Root cause: `get_resource_path()` was looking in `Path(sys.executable).parent` (the folder next to the `.exe`) instead of `sys._MEIPASS` (the temp directory where PyInstaller extracts bundled assets at runtime).
- Dead code in `helper.py`: the development-mode path fallback block was stranded after an unreachable `return`, causing `get_resource_path()` to return `None` in development mode.

## [2.1.0] 2026-07-11

### Added
- macOS support: platform-conditional window icon (`iconphoto` + PNG on macOS, `iconbitmap` + ICO on Windows)
- `eve.png` icon generated from `eve.ico` for macOS/Linux
- GitHub Actions release pipeline: automated Windows `.exe` (PyInstaller) and macOS `.dmg` (py2app) builds triggered on `v*` tags
- `setup_mac.py` py2app configuration for macOS app bundle
- Local CI runner via Makefile (`make check`, `make test`, `make lint`, `make build-windows`, `make build-macos`)
- Platform-appropriate settings storage via `platformdirs` (`%APPDATA%\evealert` on Windows, `~/Library/Application Support/evealert` on macOS)

### Fixed
- asyncio event loop created in background thread using `new_event_loop()` instead of `get_event_loop()` — prevents incorrect loop reuse on Python 3.10+
- Removed permanently-held asyncio lock in `run()` that would deadlock on any restart attempt
- Audio playback moved to `run_in_executor` so vision detection is no longer paused during alarm sounds
- Settings schema unified: `server.webhook` key used consistently everywhere (validator now correctly validates the webhook URL)
- Log level key unified: `log_level` used in both settings file and logger (changing log level in UI now takes effect)
- `load_settings()` no longer writes to disk on every read — only explicit saves write the file
- `iconbitmap()` crash on macOS replaced with platform-conditional `iconphoto()`
- Status icon garbage-collection bug: `check_status()` was storing `self.offline` instead of `self.online`
- Platform-conditional pixel offsets in `overlay.py`: `+30` Y and `-10` X corrections now only applied on Windows
- `sounddevice` import wrapped in `try/except OSError` with clear PortAudio install instructions for macOS users
- mss instance reused across screen captures (was opened/closed 20×/second)
- Alarm trigger latency reduced from 2–3 seconds to ~200 ms
- Log textbox capped at 200 lines to prevent unbounded growth and UI slowdown
- TOML section scoping bug in `pyproject.toml` that caused `pip install` to fail
- Two stale tests updated to reflect intentional code changes

### Changed
- Removed `pyautogui` dependency — replaced with `pynput.mouse.Controller` (already required)
- Removed `CTkMessagebox` dependency — replaced with stdlib `tkinter.messagebox`
- Moved `pyinstaller` from runtime to `[project.optional-dependencies].build-windows`
- Added `py2app` as `[project.optional-dependencies].build-macos`
- Added `platformdirs>=4.0` as a runtime dependency
- Pinned `dhooks-lite>=0.2`
- Removed duplicate `requirements.txt` — `pyproject.toml` is now the single source of truth
- Updated project URLs to `github.com/bluhayz/EVE-Alert`
- German log messages replaced with English
- Mouse position label text changed from "Mausposition" to "Mouse Position"

## [In Development] - Unreleased

<!--
Section Order:

### Added
### Fixed
### Changed
### Removed
-->

## [2.0.2] 2026-01-03

## Added
- Makefile System

### Changed
- removed negative error handler in some cases it needs to be negative see [#51](https://github.com/Geuthur/EVE-Alert/issues/51)

## [2.0.1] 2025-11-24

### Fixed

- **Resource loading:** Completely rewritten `get_resource_path()` — the application now always reads resources from the running executable. This ensures `img/` and `sound/` are consistently loaded in both development and distribution builds.
- **Sound handling:** Corrected `SOUND_FOLDER` in `evealert/constants.py` to `sound`, so custom WAV files like `alarm.wav` are properly located and played.

## [2.0.0] 2025-11-22

Big Thanks to [@Gotarr](https://github.com/Gotarr) for improving the whole EVE-Alert System with many QoL changes, fixes, optimations

### Added

- Codecov Report
- Discord Badge
- Release Badge
- Licence Badge
- Complete test infrastructure (pytest, pytest-asyncio, pytest-cov, pre-commit)
- 57 new unit and integration tests (test coverage: 65%)
- Real-time statistics and history system (alarm counter, session tracking, export to CSV/JSON)
- Configuration validation for all settings (regions, scales, timers, webhooks, audio)
- Central management of all constants (constants.py)
- Developer documentation and AI agent instructions (copilot-instructions.md)
- Sprint summaries and improvement plan in the docs folder

### Changed

- Updated Codecov action from `v4` to `v5`
- Refactoring: Exception hierarchy, import organization, code formatting (Black, isort)
- Type hints and docstrings for all modules
- Logging system with rotating file handler and module loggers
- Settings can now be changed at runtime (without restarting)
- Audio system: mono/stereo conversion, error handling, test buttons in the UI
- CI/CD: automated tests and coverage checks via GitHub Actions

### Fixed

- Various bugs in settings, vision, overlay, and audio
- Improved error and validation messages

### Removed

- Unnecessary and duplicate code sections
- Obsolete socket functions

## [1.0.1] 2025-06-12

### Fixed

- [#32](https://github.com/Geuthur/EVE-Alert-Opensource/issues/32)

## [1.0.0] 2025-03-30

### Changed

- Removed Socket System
  - The Socket System has been removed and we now use Discord Webhook to share Intel.
- Setting Loader
  - Missing Keys will be created.
- Update Dependencies
  - Open-CV Updated to 4.11.\*
  - ScreenInfo to 0.8.1
  - MSS to 10.0.0

### Added

- Discord System
  - All alarms are sent to a Discord webhook with the system name.
- dhooks-lite
  - Discord Webhook library

## [0.9.0] - 2025-01-08

### Changed

- Setting Manager
  - The settings manager has been refactored to improve the handling and organization of settings.
- Config Mode
  - The configuration mode has been optimized to respond more flexibly to changes.
- Refactor of the entire system
  - The system has been refactored to improve code readability and maintainability.
- Logger System
  - The logger system has been enhanced to capture more detailed information about errors, aiding in better troubleshooting.
- Alert Sound
  - The alert sound will not interrupt the program after an error occurs.

### Fixed

- EveLocal not closing with Window Close:
  - An issue was fixed where the EveLocal window would not properly close when the window's close button was clicked. This fix ensures that the window is now correctly closed when the user attempts to exit.
- Settings not reloaded if changed:
  - A bug where settings were not being reloaded after being modified has been resolved. Now, when changes are made to the settings, they will be properly reloaded, reflecting the new configuration.
- Vision not reloaded if changed:
  - A similar issue was addressed where vision settings (likely related to display or graphical configurations) were not reloaded after changes. This fix ensures that any changes to vision settings are immediately applied and reflected.
- Overlay Window not fitting exactly to the monitor resolution:
  - The overlay window was previously not aligning correctly with the monitor's resolution. This issue has been fixed, ensuring that the overlay window now correctly fits and scales to the screen size, providing a more accurate and consistent user interface.

### Added

- Propertys for each system
  - New properties have been added to manage and configure each system individually. This allows for more flexibility and control over system settings and their states.
- Socket System (Test)
  - A new socket system has been implemented for testing purposes. This enables communication between different components or systems, facilitating data exchange and interactions.
- Cleanup Functions
  - Cleanup functions have been introduced to improve resource management. These functions help remove unnecessary data, free up memory, and ensure the application remains efficient by handling cleanup tasks properly.
- Set changed flag in the menu:
  - The changed flag is now set to True whenever a modification is made to the menu settings. This helps track changes and triggers actions like warnings before exiting without saving.
- Buttons State
  - All buttons now have a state color to indicate when they are pressed. This visual cue helps users easily identify the current state of the buttons, improving the user interface and overall user experience by providing clearer feedback during interaction.
- Message Box System
  - Implemented a single-instance error message box to prevent multiple error windows from opening simultaneously.

## [0.5.0] - 2024-11-29

### Changed

- Settings for Regions now have a visual interface
- Setting Region now work per `Marquee Selection`
- Enemy & Faction now work seperately

### Fixed

- Faction Region can't be open if a error occurs [#15](https://github.com/Geuthur/EVE-Alert-Opensource/issues/15)
- In some Cases Multiple Monitors not work [#15](https://github.com/Geuthur/EVE-Alert-Opensource/issues/15) (Testing)
- Vision System not work if Detection Scale is Zero or below
- Images with Alpha Channel triggers Error [#15](https://github.com/Geuthur/EVE-Alert-Opensource/issues/15)
- It is not possible to switch Windows recognition off/on during sound playback
- Overlapping Overlay when F1 and F2 was pressed
- Background width is not correct

### Added

- Abort Option on Settings with `ESC`
- Faction Detection Scale
- Overlay System

### Removed

- Drop Color Mode Support
- Screenshot Mode

## [0.4.4] - 2024-11-23

### Changed

- print to logger in regionmanager
- Alarm Sound

### Fixed

- Faction Region can't be open if a error occurs [#15](https://github.com/Geuthur/EVE-Alert-Opensource/issues/15)

## [0.4.3] - 2024-10-18

### Added

- Dependency Requirment Info

### Changed

- Requirments
- Moved from PyAudio to sounddevice and soundfile
- Update Preview Video
- Update Window Installer

### Fixed

- Icon Bitmap Error on Linux [#9](https://github.com/Geuthur/EVE-Alert-Opensource/issues/9)

## [0.4.2] - 2024-10-18

### Added

- Window Installation Guide

### Fixed

- Window Installer not working correctly if executed as Admin

## [0.4.1b1] - 2024-05-24

### Added

- Cooldown Function with optional cooldowns
- Log Message Function
- Status Icon for Running Alert
- Log Textfield

### Fixed

- Program Blocking on some situations
- Alert Text appears after Error
- Performance Issues

### Changed

- Moved AlertAgent to Async
- AlertAgent now started via seperate Thread
- save_settings function moved to SettingsManager
- Moved Play Sound to one Function

### Removed

- Socket Functions, Maybe Later i will Implemend this..
- Log System Label
- Unused Code

## Full Changelog

[1.0.1]: https://github.com/Geuthur/eve-alert/compare/v1.0.0...v1.0.1 "1.0.1"
[2.0.0]: https://github.com/Geuthur/eve-alert/compare/v1.0.1...v2.0.0 "2.0.0"
[2.0.1]: https://github.com/Geuthur/eve-alert/compare/v2.0.0...v2.0.1 "2.0.1"
[2.0.2]: https://github.com/Geuthur/eve-alert/compare/v2.0.1...v2.0.2 "2.0.2"
[in development]: https://github.com/Geuthur/eve-alert/compare/v2.0.2...HEAD "In Development"
[report any issues]: https://github.com/Geuthur/eve-alert/issues "report any issues"