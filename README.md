# EVE Alert

EVE Alert monitors your EVE Online Local chat window for enemy and neutral player icons using OpenCV template matching, then fires audio alarms and optional Discord webhook notifications — giving you a heads-up without breaking your focus.

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
- Platform-aware settings storage: `%APPDATA%\evealert` on Windows, `~/Library/Application Support/evealert` on macOS
- Rotating log files (5 MB x 3 backups) with configurable log level
- Support for multiple UI scaling variants of template images (e.g. `image_1_90%.png`, `image_1_100%.png`)
- Pre-built releases: Windows `.exe` and macOS `.dmg` — no Python required for end users

---

## Requirements

| Requirement | Detail |
|---|---|
| Python | 3.10, 3.11, or 3.12 |
| OS | Windows 10/11 (primary), macOS 12+ (supported) |
| Display scaling | Must be set to **100%** in OS Display Settings; other values cause region misalignment |
| Audio | A default audio output device must be present |
| macOS only | PortAudio must be installed separately (`brew install portaudio`) |

---

## Installation

### Option A — Pre-built release (recommended for non-developers)

1. Go to the [Releases page](https://github.com/bluhayz/EVE-Alert/releases).
2. Download the latest `EVE-Alert.exe` (Windows) or `EVE-Alert.dmg` (macOS).
3. Run it — no Python installation required.

> **macOS note:** Right-click and choose "Open" the first time to bypass Gatekeeper, and grant Accessibility permissions for the global hotkeys (see [macOS Setup](#macos-setup)).

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

## macOS Setup

**PortAudio (required for audio):**

```bash
brew install portaudio
```

Without PortAudio, `sounddevice` cannot initialize and EVE Alert will show a warning and run without audio.

**Accessibility permission (required for F1/F2 hotkeys):**

1. Open **System Settings > Privacy & Security > Accessibility**.
2. Add the EVE Alert app (or your terminal if running from source).

Without this permission, the Config Mode hotkeys will not work.

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

**macOS:**

```bash
pip install ".[build-macos]"
make build-macos
# → EVE-Alert-macOS.dmg
```

---

## Project Structure

```
EVE-Alert/
├── main.py                         # Entry point
├── pyproject.toml                  # Package metadata, dependencies, build config
├── COCO.md                         # AI agent architecture context document
├── CHANGELOG.md
│
├── evealert/
│   ├── __init__.py                 # Version string (__version__)
│   ├── constants.py                # All magic numbers and tuneable defaults
│   ├── exceptions.py               # Custom exception types
│   ├── hotkeys.py                  # Hotkey parsing and matching helpers
│   ├── statistics.py               # Detection counter / statistics model
│   ├── tray.py                     # System-tray integration (pystray)
│   │
│   ├── manager/
│   │   └── alertmanager.py         # Core detection loop, audio, webhook, Zkillboard dispatch
│   │
│   ├── menu/
│   │   ├── main.py                 # Main window and button layout
│   │   ├── config.py               # Config Mode overlay and region selection
│   │   ├── image_manager.py        # Add/remove/preview custom template images
│   │   ├── setting.py              # Settings dialog
│   │   ├── statistics.py           # Statistics window (live stats + sessions tab)
│   │   └── threshold_editor.py     # Per-image threshold editor
│   │
│   ├── settings/
│   │   ├── helper.py               # Resource path resolution (dev vs. PyInstaller)
│   │   ├── logger.py               # Rotating file + console logger setup
│   │   ├── stats_store.py          # Persistent lifetime stats + session reports
│   │   └── validator.py            # Settings schema validation
│   │
│   ├── tools/
│   │   ├── intel_watcher.py        # EVE chat log tail watcher
│   │   ├── overlay.py              # Bounding-box overlay window
│   │   ├── vision.py               # OpenCV template matching engine
│   │   ├── window_finder.py        # Cross-platform EVE window detection
│   │   ├── windowscapture.py       # mss-based screen region capture
│   │   └── zkillboard.py           # Zkillboard + ESI kill lookup
│   │
│   ├── img/                        # Bundled template and UI images
│   └── sound/                      # Bundled audio files
│
└── tests/
    ├── test_alertmanager.py
    ├── test_vision.py
    └── test_validator.py
```

---

## Contributing

Pull requests and bug reports are welcome. Open an issue describing the problem or feature before submitting a large PR.

---

## License

EVE Alert is released under the [GNU General Public License v3.0](LICENSE).

---

> [!CAUTION]
> This is an open-source project provided without any warranty. Ensure your usage complies with EVE Online's Terms of Service. Screen-reading overlays are generally permitted, but you are solely responsible for verifying compliance with current CCP policies.
