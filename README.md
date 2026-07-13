# EVE Alert

EVE Alert monitors your EVE Online Local chat window for enemy and neutral player icons using OpenCV template matching, then fires audio alarms, Discord webhooks, and mobile push notifications — giving you a heads-up without breaking your focus. Beyond screen detection it layers in a full intel suite: pilot background checks via ESI, zKillboard kill activity, D-scan threat classification (with per-ship threat classes), KOS list checks, adjacent-system and wormhole awareness, EVE SSO login for personal standings, and (since v6.0) a composite threat score, TTS voice alerts, cyno detection, intel channel parsing with jump-distance lookups, space profiles, and standings-aware ally filtering.

[![Tests](https://github.com/bluhayz/EVE-Alert/actions/workflows/tests.yml/badge.svg)](https://github.com/bluhayz/EVE-Alert/actions/workflows/tests.yml)
[![Latest Release](https://img.shields.io/github/v/release/bluhayz/EVE-Alert)](https://github.com/bluhayz/EVE-Alert/releases)
[![Python Versions](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](pyproject.toml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

---

## What It Does

EVE Alert captures a configurable screen region (your Local chat list) every 100–200 ms and runs OpenCV template matching against a library of enemy and neutral standing icons. When a match exceeds the configured confidence threshold, it:

1. Plays `alarm.wav` through your default audio device (up to 3 times before entering a cooldown).
2. Optionally fires a Discord webhook to broadcast the alert to a channel or relay server.
3. Draws a live overlay on the monitored region so you can see exactly what triggered the alarm.

A second, independent region (Faction Region) watches for faction spawn or other custom icons and plays `faction.wav` when triggered.

---

## Screenshot

<!-- Add a screenshot here once the UI stabilises -->

---

## Features

- OpenCV template matching — no OCR, no game memory access; works entirely from screen pixels
- Dual detection channels: Alert Region (Local chat enemies/neutrals) and Faction Region (faction spawns or any custom target)
- Audio alarms with per-channel sound files that you can replace via Settings
- Discord webhook integration for anonymous shared intel
- Config Mode with configurable hotkeys (default F1/F2) to draw detection regions directly on screen
- Real-time overlay visualizing matched bounding boxes
- Detection threshold slider (0–100) maps to OpenCV confidence 0.1–1.0
- Per-image threshold control — override the global confidence threshold per individual template image
- Cooldown timer prevents alarm spam after repeated detections
- Mute-alarm option for Discord-only notification workflows
- **System tray** — X button minimizes to tray; background monitoring without a taskbar entry
- **Auto-detect EVE Online client window** to pre-fill both detection region coordinates
- **Configurable hotkeys** — Alert Region and Faction Region keys remappable in Settings
- **Custom alarm sounds** — browse for any WAV file in Settings; separate files for enemy and faction alarms
- **Named detection profiles** — save and quick-load complete configuration snapshots
- **Image Manager** — add/remove/preview custom template images from within the app; no restart required
- **Persistent lifetime statistics** — alarm totals survive restarts; stored in the platformdirs config directory
- **Per-session JSON reports** — each detection run saved; browsable, viewable, and deletable from the Statistics window
- **Zkillboard kill intelligence** — optional async lookup of recent kills in your configured system on each Enemy alarm
- **Intel channel log watcher** — optional real-time tail of your EVE chat log; new lines appear in the log pane
- Statistics window showing lifetime totals, current-session counts, history, and past-session report browser

### Intelligence & alerting (v3.0 – v4.0)

- **ESI pilot intel** — on each Enemy alarm, recent Local joiners are looked up via public ESI: corp/alliance, character age, corp-history count, security status, and a zKillboard kill/loss profile per pilot
- **Threat tiers** — map pilot/corp/alliance name fragments to red/orange/yellow tiers; matched pilots get `⚠ [KOS-RED]` / `⚠ [HOSTILE]` / `[CAUTION]` prefixes
- **Flashy & cyno-alt warnings** — distinct alerts for sec-status ≤ −5 pilots and characters younger than 30 days
- **KOS checker** — queries the CVA KOS API plus any custom KOS endpoints per pilot
- **D-scan monitor** — tails EVE's D-scan log files, classifies ships into RED/ORANGE/YELLOW/GREEN threat tiers, and fires a distinct probe-detection alert
- **Adjacent-system monitor** — polls zKillboard for kill activity within a configurable jump radius (BFS over the ESI stargate graph)
- **Route threat check** — one-click threat assessment of every hop from your system to a destination
- **System context on start** — pipe/pocket/crossroads classification, sovereignty holder, and a background sov-change monitor
- **Wormhole awareness** — Thera connection monitor (Eve-Scout) and a WH fleet-drop heuristic (rapid multi-pilot Local joins)
- **Fleet context** — hostile fleet composition analysis when 3+ hostiles appear; killmail notifications for tracked characters
- **Push notifications** — Telegram Bot, Pushover, and ntfy.sh channels; auto-screenshot on alarm; alarm escalation counter
- **Web status dashboard** — optional localhost HTTP server with a live status page and JSON API (`/api/status`, `/api/log`)
- **Plugin system** — drop `.py` files into the user plugins directory; hooks for `on_start`, `on_stop`, `on_enemy`, `on_faction`, `on_intel`
- **EVE SSO login (v4.0)** — OAuth2 authorization-code flow; personal standings auto-classification of Local pilots, fleet membership display, and structure fuel-expiry warnings (requires registering your own EVE developer application client ID)
- **OCR name detection (v4.1)** — optionally reads pilot names from a configured Local-chat screen region on each Enemy alarm and feeds them into the KOS / ESI / zKillboard pipeline. Off by default; requires the [Tesseract OCR engine](https://github.com/tesseract-ocr/tesseract) installed separately (`brew install tesseract` on macOS, or the UB-Mannheim installer on Windows). Configure the capture region in **Settings → Intel & ESI → OCR Name Detection**.
- Platform-aware settings storage: `%APPDATA%\evealert` on Windows, `~/Library/Application Support/evealert` on macOS
- Rotating log files (5 MB x 3 backups) with configurable log level
- Support for multiple UI scaling variants of template images (e.g. `image_1_90%.png`, `image_1_100%.png`)
- Pre-built releases: Windows `.exe` and macOS `.dmg` — no Python required for end users

### AFK situational awareness (v6.0)

- **D-scan ship class classification** — D-scan threat entries now include a fine-grained class label: TACKLE, DICTOR, FORCE_RECON, COVERT_OPS, CYNO, COMBAT, INDUSTRIAL. Labels appear inline in the log (`D-SCAN RED: Sabre [DICTOR — bubble incoming]`).
- **TTS voice alerts** — optional spoken alarm readout via `pyttsx3`. Configurable speech rate; Check and Test buttons in Settings. Install with `pip install "evealert[tts]"`.
- **Composite threat score** — combines local count, KOS status, zKillboard danger ratio, D-scan ship class, and adjacent kills into a 1–10 score (CAUTION / HIGH / CRITICAL) logged after each ESI intel block. Cyno detection forces 10/CRITICAL.
- **Per-enemy re-alert** — set `alerts.rearm_minutes` to re-alarm on a pilot who has been continuously present beyond the configured window (0 = disabled).
- **Space profiles (F3 hotkey)** — three one-click presets (Null-sec / Wormhole / High-sec) apply a coordinated set of settings overrides and reload the agent live. Press **F3** to cycle through profiles.
- **Intel channel improvements** — structured parsing of coalition intel messages: system name, hostile count, clear signals, ship mentions. Jump distance from your home system is looked up via ESI and appended to each report.
- **Cyno detection** — dedicated CRITICAL alarm when a cynosural field or cyno ship appears on D-scan. Bypasses the normal cooldown; also speaks via TTS.
- **Standings-aware ally filtering** — enable `standings_filter_blues` to suppress allied pilots (standing ≥ +5.0) from threat display, KOS checks, and the composite threat score.

### Further enhancements (v6.1)

- **WH signature delta** — `DscanWatcher` fires an alarm whenever the cosmic-signature count increases: possible wormhole connection incoming.
- **Ship cross-reference** — when a hostile's zKillboard top-ship matches a type currently on D-scan, an immediate red `⚠ MATCH` line is logged.
- **F4 status readout** — press **F4** to hear the current threat state spoken aloud (local count, D-scan class, adjacent kills, threat score).
- **EVE automation bridge** — POST alarm JSON to a localhost webhook URL on each alarm so AutoHotkey / PyAutoGUI scripts can trigger in-game keypresses.
- **Constellation threat heatmap** — 7-day per-system kill histogram accessible from the Statistics window **Threat Heatmap** tab.
- **Peak hours alerts** — hourly check warns 15 min before a historically high-activity hour in the constellation.
- **Mobile notification wizard** — guided 4-page setup for Telegram, Pushover, or ntfy.sh with a live test step.

---

## Requirements

| Requirement | Detail |
|---|---|
| Python | 3.10, 3.11, or 3.12 (source installs only — releases bundle Python) |
| OS | Windows 10/11. (macOS support ended with v5.0; running from source on macOS is untested and unsupported) |
| Display scaling | Must be set to **100%** in OS Display Settings; other values cause region misalignment |
| Audio | A default audio output device must be present |

---

## Installation

### Option A — Pre-built release (recommended for non-developers)

1. Go to the [Releases page](https://github.com/bluhayz/EVE-Alert/releases).
2. Download the latest `EVE-Alert.exe`.
3. Run it — no Python installation required.

### Option B — From source

```bash
git clone https://github.com/bluhayz/EVE-Alert.git
cd EVE-Alert
pip install .
python main.py
```

---

## Quick Start

1. Download and run `EVE-Alert.exe` from the [Releases page](https://github.com/bluhayz/EVE-Alert/releases).
2. Open EVE Online and navigate to a system with Local chat visible on screen.
3. In EVE Alert, click **Settings** and configure your Discord webhook and system name if desired.
4. Click **Config Mode** to enter region-selection mode.
5. Press **F1** and drag a rectangle around the standing-icon column in your Local chat list. This becomes the Alert Region.
6. Press **F2** and drag a rectangle around any area you want to watch for faction or custom icons. This becomes the Faction Region.
7. Click **Config Mode** again to exit selection mode.
8. Adjust the **Detection Threshold** and **Cooldown** sliders to taste.
9. Click **Start Script**. EVE Alert begins monitoring and alarms on any match.

---

## Configuration Guide

### Alert Region

The Alert Region is the area of your screen containing the standing icons in Local chat. EVE Alert captures this region repeatedly and compares it against every `image_*.png` template in `img/`.

**How to set it:** Enter Config Mode, press **F1**, then click-and-drag a rectangle that tightly covers the standing icon column.

### Faction Region

An independent second capture region for faction spawn detection or any other pixel pattern you want to watch. Triggers `faction.wav` when matched.

**How to set it:** Enter Config Mode, press **F2**, then drag a rectangle over the target area.

### Detection Threshold

A slider from 0 to 100 that controls OpenCV template-matching confidence. Internally this maps to the range 0.10–1.00.

- **Higher values** (e.g., 80–95) require a near-perfect pixel match — fewer false positives, may miss detections if UI anti-aliasing varies.
- **Lower values** (e.g., 40–60) are more permissive — catches more true positives but may fire on similar-looking icons.

Start around **70** and tune from there.

### Cooldown Timer

After the alarm has triggered **3 times** in quick succession, EVE Alert enters a cooldown period (default: **60 seconds**). Configure the duration in Settings.

### Discord Webhook

**How to get a webhook URL:**

1. In Discord, open **Server Settings > Integrations > Webhooks**.
2. Click **New Webhook**, choose a channel, and copy the URL.

The URL must start with `https://discord.com/api/webhooks/`. Set a **System Name** (e.g., `Jita 4-4`) that appears in the message body. Check **Mute Alarm** to suppress local audio and use Discord-only notifications.

### Volume

A 0–100 slider that controls playback volume for both `alarm.wav` and `faction.wav`.

---

## Template Images

Template images live in `evealert/img/` (bundled with the release):

| Pattern | Purpose |
|---|---|
| `image_*.png` | Enemy and neutral standing icons for the Alert Region |
| `faction_*.jpg` / `faction_*.png` | Faction spawn or custom icons for the Faction Region |

**Adding custom templates:**

Use the **Image Manager** button in Config Mode. Click "Add Image..." to copy any PNG/JPG file into your user template directory. The detection engine reloads immediately — no restart required.

**UI scaling variants:** The bundled images cover 100% and 90% UI scale (e.g., `image_1_100%.png`, `image_1_90%.png`). Add variants for other scales as needed.

---

## Sound Files

Sound files live in `evealert/sound/`:

| File | Trigger |
|---|---|
| `alarm.wav` | Enemy or neutral detected in the Alert Region |
| `faction.wav` | Faction or custom icon detected in the Faction Region |
| `error.wav` | Internal error condition |

To use a custom sound, open **Settings** and click **Browse Alarm...** or **Browse Faction...**. Select any WAV file on your system. The path is stored in `settings.json`; the bundled files are used as a fallback if the selected file is missing.

---

## Development

### Setup

```bash
git clone https://github.com/bluhayz/EVE-Alert.git
cd EVE-Alert
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install ".[dev]"
pre-commit install
```

### Running from source

```bash
python main.py
```

### Testing

```bash
make test
```

### Local CI (lint + tests)

```bash
make check
```

Run this before opening a pull request.

### Building releases

**Windows** (run in a Windows environment):

```bash
pip install ".[build-windows]"
make build-windows
# → dist/EVE-Alert.exe
```

> macOS builds were retired with the v5.0 PySide6 migration; the release
> pipeline is Windows-only.

---

## Project Structure

```
EVE-Alert/
├── main.py                         # Entry point (launches the PySide6 app)
├── pyproject.toml                  # Package metadata, dependencies, build config
├── COCO.md                         # AI agent architecture context document
├── CHANGELOG.md
├── docs/
│   ├── ARCHITECTURE.md             # Module inventory, threading model, data flow
│   ├── INTEGRATIONS.md             # Every external API used, timeouts, failure modes
│   └── FEATURES.md                 # Feature ↔ settings-key ↔ module lookup table
│
├── evealert/
│   ├── __init__.py                 # Version string (__version__)
│   ├── bridge.py                   # UIBridge protocol — engine's only view of the UI
│   ├── constants.py                # All magic numbers and tuneable defaults
│   ├── exceptions.py               # Custom exception types
│   ├── hotkeys.py                  # Hotkey parsing and matching helpers
│   ├── statistics.py               # Detection counter / statistics model
│   │
│   ├── data/
│   │   └── ship_classes.py         # D-scan ship threat classification map (v6.0)
│   │
│   ├── manager/
│   │   └── alertmanager.py         # Core engine: vision loops, alarms, all intel task wiring
│   │
│   ├── ui/                         # PySide6 interface (v5.0+)
│   │   ├── app.py                  # QApplication bootstrap + theme loading
│   │   ├── main_window.py          # Main window, hotkey routing, engine wiring
│   │   ├── qt_bridge.py            # UIBridge → Qt signals (thread-safe engine→UI)
│   │   ├── settings_dialog.py      # Registry-generated settings form + profiles + SSO
│   │   ├── config_dialog.py        # Config Mode / region selection guidance
│   │   ├── region_overlay.py       # Fullscreen drag-to-select overlay (QRubberBand)
│   │   ├── statistics_window.py    # Live stats, sessions, threat heatmap tabs
│   │   ├── image_manager.py        # Add/remove/preview custom template images
│   │   ├── threshold_editor.py     # Per-image threshold editor
│   │   ├── notification_wizard.py  # Guided Telegram/Pushover/ntfy setup
│   │   ├── tray.py                 # System tray (QSystemTrayIcon)
│   │   └── theme.py / theme.qss    # Design tokens + stylesheet
│   │
│   ├── settings/
│   │   ├── store.py                # SettingsStore + DEFAULT_SETTINGS (GUI-free)
│   │   ├── fields.py               # FieldSpec registry driving the settings form
│   │   ├── helper.py               # Resource path resolution (dev vs. PyInstaller)
│   │   ├── logger.py               # Rotating file + console logger setup
│   │   ├── stats_store.py          # Persistent lifetime stats + session reports
│   │   └── validator.py            # Settings schema validation
│   │
│   ├── tools/
│   │   ├── vision.py               # OpenCV template matching engine
│   │   ├── windowscapture.py       # mss-based screen region capture
│   │   ├── window_finder.py        # EVE client window detection
│   │   ├── http_common.py          # Shared User-Agent / headers for all external HTTP
│   │   ├── zkillboard.py           # zKillboard kill lookups (+ [null] normalization)
│   │   ├── esi_standings.py        # Public-ESI pilot intel + Local join parser
│   │   ├── esi_auth.py             # EVE SSO OAuth2 (PKCE) + authed ESI helpers
│   │   ├── universe.py             # System cache, jump-graph BFS, sov, route threat
│   │   ├── neighbor_monitor.py     # Adjacent-system kill polling
│   │   ├── dscan_watcher.py        # D-scan tail: tiers, ship classes, probes, cyno, sigs
│   │   ├── intel_watcher.py        # EVE chat log tail watcher
│   │   ├── intel_parser.py         # Free-text intel → structured reports (v6.0)
│   │   ├── kos_checker.py          # KOS API checks (CVA legacy) + local list
│   │   ├── push_notifier.py        # Telegram / Pushover / ntfy push channels
│   │   ├── wormhole.py             # Thera monitor (Eve-Scout), WH class, drop heuristic
│   │   ├── fleet_context.py        # Fleet composition, TZ profile, killmail monitor
│   │   ├── threat_score.py         # Composite 1–10 threat score (v6.0)
│   │   ├── threat_heatmap.py       # Constellation kill histograms (v6.1)
│   │   ├── space_profiles.py       # F3 space-type presets (v6.0)
│   │   ├── tts.py                  # Text-to-speech alerts (pyttsx3, v6.0)
│   │   ├── ocr_local.py            # Optional Tesseract pilot-name OCR (v4.1)
│   │   ├── web_server.py           # Localhost dashboard + JSON API (+ /api/alarm/latest)
│   │   ├── net_safety.py           # SSRF/localhost guards for user-supplied URLs
│   │   ├── plugin_loader.py        # User plugin discovery and hook dispatch
│   │   └── update_checker.py       # GitHub Releases version check
│   │
│   ├── img/                        # Bundled template and UI images
│   └── sound/                      # Bundled audio files
│
└── tests/                          # pytest suite (330+ tests across ~30 modules)
```

## Documentation

- [COCO.md](COCO.md) — canonical AI-agent context: thread-safety rules, conventions, release process
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module inventory and data flow
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) — external API surface and failure behavior
- [docs/FEATURES.md](docs/FEATURES.md) — feature-to-settings-to-module map

## Reporting a Problem / Diagnostics

EVE Alert includes a built-in **Diagnostic Mode** that produces verbose logs and a shareable bundle.

### Enabling verbose logging

1. Open **Settings → Alerts & Sound → Diagnostics**.
2. Check **Enable diagnostic (verbose) logging**.
3. Optionally set **Log Level** to `DEBUG` for maximum detail.
4. Click **Save** then **Apply**.

All app loggers now write at DEBUG level to the log files in the path shown in the Diagnostics section.

### Exporting a diagnostics bundle

After reproducing an issue:

1. Settings → Alerts & Sound → Diagnostics → click **Export Diagnostics Bundle**.
2. A `eve-alert-diagnostics-<timestamp>.zip` is created in the config directory (your OS file manager opens to its location automatically).
3. The zip contains all log files, a **secrets-redacted** copy of your settings, and a system/environment info file.
4. Share the zip when reporting a bug — it contains enough context to diagnose issues remotely.

> **Note:** Log files may contain EVE system names and character names from your session. Push notification tokens, OAuth credentials, and webhook URLs are **redacted** from the settings snapshot.

### EVEALERT_DEBUG environment variable

Set `EVEALERT_DEBUG=1` before launching to enable verbose logging from process start (before the UI is displayed). Useful for startup crashes or issues that occur before the settings UI is accessible:

- Windows: `set EVEALERT_DEBUG=1` then launch `EVEAlert.exe`
- macOS/Linux: `EVEALERT_DEBUG=1 ./EVEAlert`

---

## Known Issues & Limitations

- **EVE SSO login requires your own EVE developer application.** There is no built-in client ID, so ESI OAuth features (personal standings auto-classify, ally filter, fleet membership, structure fuel warnings) require registering a free application at the [EVE Developers portal](https://developers.eveonline.com/) (callback URL exactly `http://localhost:8888/callback`) and entering your client ID under **Settings → Intel & ESI → EVE SSO / ESI OAuth**.
- **OCR pilot-name detection requires Tesseract installed separately.** The feature is off by default and degrades to a no-op with a log message when the Tesseract engine is not present on the machine.
- **CVA KOS lookups are disabled by default** — the public CVA KOS API is offline; use custom KOS URLs or threat tiers instead.
- The July 2026 code audit identified defects tracked in the [v6.2 — Stabilization milestone](https://github.com/bluhayz/EVE-Alert/milestone/21) — notably: F1/F2 can trigger region selection outside Config Mode (#154), the F3 space-profile hotkey is broken (#155), and saving Settings while a named profile is active bakes the profile into your base config (#156). Review that milestone before relying on profiles or the F-key extras.

## Roadmap

Planned work is tracked as GitHub milestones with epic issues:

| Milestone | Theme |
|---|---|
| [v6.2 — Stabilization & Test Integrity](https://github.com/bluhayz/EVE-Alert/milestone/21) | Fix all audit findings; trustworthy test suite |
| [v6.3 — UX & Onboarding](https://github.com/bluhayz/EVE-Alert/milestone/22) | First-run wizard, hotkey capture, profile manager, log tooling |
| [v7.0 — Real-Time Intel Platform](https://github.com/bluhayz/EVE-Alert/milestone/23) | zKillboard RedisQ live killstream, gate-camp detection, route advisor, standings manager |
| [v7.1 — Multiboxing & Performance](https://github.com/bluhayz/EVE-Alert/milestone/24) | Multi-client support, vision performance pass, dxcam capture backend |
| [v8.0 — Distribution & Ecosystem](https://github.com/bluhayz/EVE-Alert/milestone/25) | Auto-updater, signed installer + winget, crash reporting, plugin API v2 |

Found a bug? Please [open an issue](https://github.com/bluhayz/EVE-Alert/issues) with a diagnostics bundle (**Settings → Alerts & Sound → Diagnostics → Export Diagnostics Bundle**).

---

## Contributing

Pull requests and bug reports are welcome. Open an issue describing the problem or feature before submitting a large PR.

---

## License

EVE Alert is released under the [GNU General Public License v3.0](LICENSE).

---

> [!CAUTION]
> This is an open-source project provided without any warranty. Ensure your usage complies with EVE Online's Terms of Service. Screen-reading overlays are generally permitted, but you are solely responsible for verifying compliance with current CCP policies.
