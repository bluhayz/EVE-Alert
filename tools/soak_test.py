"""Long-session soak test driver (#177, v7.2).

Dev-only tool -- lives outside evealert/, so hatchling's build (which
only includes /evealert, see pyproject.toml [tool.hatch.build]) never
ships it. Requires: pip install ".[soak,dev]"

Drives AlertAgent's alarm-dispatch/dedup/cooldown machinery with
synthetic enemy sightings for a configurable duration, and separately
seeds+purges the universe/zKillboard/heatmap TTL caches, sampling RSS,
thread count, and asyncio task count to CSV every --interval seconds.

Network calls are avoided entirely (ESI/zKB/webhook/push/OCR toggles are
forced off in the synthetic settings) -- this exercises the same
in-memory state machinery (dedup dicts, cooldown timers, TTL caches,
statistics) a real multi-hour AFK session churns through, without
needing a live EVE client, a display, or external network access. It is
NOT a substitute for an actual 24h run against the real app -- see
COCO.md-linked issue #177 for that acceptance criterion; this script is
the automatable slice of it.

Usage:
    python tools/soak_test.py --duration 7200 --interval 60   # ~2h soak
    python tools/soak_test.py --duration 20 --interval 1      # smoke test
"""

import argparse
import asyncio
import csv
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import psutil
except ImportError:
    print('psutil required: pip install ".[soak]"', file=sys.stderr)
    sys.exit(1)


def _build_agent(tmp_dir: Path):
    from evealert.manager.alertmanager import AlertAgent  # noqa: PLC0415
    from evealert.settings.store import reset_settings_store  # noqa: PLC0415

    settings_path = tmp_dir / "settings.json"
    settings_path.write_text(json.dumps({
        "alert_region_1": {"x": 0, "y": 0},
        "alert_region_2": {"x": 100, "y": 100},
        "faction_region_1": {"x": 200, "y": 0},
        "faction_region_2": {"x": 300, "y": 100},
    }))
    reset_settings_store(settings_path)
    os.environ["EVEALERT_STATS_PATH"] = str(tmp_dir / "statistics.json")
    os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(tmp_dir / "pilot_history.db")

    mock_main = MagicMock()
    mock_main.write_message = MagicMock()
    with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
        agent = AlertAgent(mock_main)
    agent.load_settings()
    # Keep network-touching/side-effect subsystems off -- this soak
    # targets in-memory growth in the engine's own state, not external
    # API behavior (which is covered by mocked unit tests elsewhere).
    agent._zkillboard_enabled = False
    agent._esi_enabled = False
    agent._ocr_enabled = False
    agent._push_config = {}
    agent._automation_enabled = False
    agent.mute = True
    return agent


async def _synthetic_alarm_cycle(agent, rng: random.Random) -> None:
    """One lightweight synthetic 'poll cycle' through the same dedup/
    cooldown code path a real vision_thread()+run() cycle drives -- enough
    churn through _seen_enemies/cooldown_timers/alarm_trigger_counts to
    reveal a leak over many iterations, without a real screen capture."""
    agent._enemy_points = [(rng.randint(0, 500), rng.randint(0, 500))]
    if agent._should_alarm_enemy({}):
        await agent.alarm_detection("Enemy Appears!", "fake.wav", "Enemy")
    if not rng.getrandbits(3):  # occasionally simulate "enemy left system"
        await agent.reset_alarm("Enemy")


def _seed_and_purge_ttl_caches(rng: random.Random) -> dict:
    """Seed the universe/zKillboard/heatmap TTL caches with synthetic
    already-expired entries (as a long session accumulates for systems
    only ever looked up once) and purge them, to exercise and demonstrate
    the #177 purge fix each sample tick."""
    from evealert.tools import threat_heatmap  # noqa: PLC0415
    from evealert.tools.universe import get_universe_cache  # noqa: PLC0415
    from evealert.tools.zkillboard import get_client  # noqa: PLC0415

    stale_time = time.time() - 100_000  # long past any real TTL
    universe = get_universe_cache()
    zkb = get_client()

    fake_system_id = rng.randint(30_000_000, 30_999_999)
    universe._kill_count_cache[fake_system_id] = (stale_time, rng.randint(0, 5))
    zkb._cache[f"synthetic-{fake_system_id}"] = (stale_time, None)
    threat_heatmap._CACHE[(f"SYNTH-{fake_system_id}", 7)] = (stale_time, {})

    return {
        "universe_purged": universe.purge_expired_kill_counts(),
        "zkb_purged": zkb.purge_expired(),
        "heatmap_purged": threat_heatmap.purge_expired_cache(),
    }


async def run_soak(duration: float, interval: float, out_path: Path) -> int:
    """Returns the number of synthetic cycles run."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="evealert_soak_"))
    agent = _build_agent(tmp_dir)
    process = psutil.Process()
    rng = random.Random(42)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "elapsed_s", "rss_mb", "threads", "asyncio_tasks", "open_files",
            "seen_enemies", "cooldown_timers", "universe_kill_cache",
            "zkb_cache", "heatmap_cache",
        ])

        start = time.monotonic()
        next_sample = start
        cycle = 0
        while time.monotonic() - start < duration:
            await _synthetic_alarm_cycle(agent, rng)
            cycle += 1
            now = time.monotonic()
            if now >= next_sample:
                _seed_and_purge_ttl_caches(rng)

                rss_mb = process.memory_info().rss / (1024 * 1024)
                threads = process.num_threads()
                try:
                    tasks = len(asyncio.all_tasks())
                except RuntimeError:
                    tasks = 0
                try:
                    open_files = len(process.open_files())
                except Exception:
                    open_files = -1

                from evealert.tools import threat_heatmap  # noqa: PLC0415
                from evealert.tools.universe import get_universe_cache  # noqa: PLC0415
                from evealert.tools.zkillboard import get_client  # noqa: PLC0415

                writer.writerow([
                    f"{now - start:.1f}", f"{rss_mb:.2f}", threads, tasks, open_files,
                    len(agent._seen_enemies), len(agent.cooldown_timers),
                    len(get_universe_cache()._kill_count_cache),
                    len(get_client()._cache),
                    len(threat_heatmap._CACHE),
                ])
                f.flush()
                next_sample += interval
            await asyncio.sleep(0.05)  # roughly VISION_SLEEP_INTERVAL cadence

    return cycle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=7200,
                         help="Soak duration in seconds (default 7200 = 2h)")
    parser.add_argument("--interval", type=float, default=60,
                         help="Sample interval in seconds (default 60)")
    parser.add_argument("--out", default="soak_results.csv")
    args = parser.parse_args()

    out_path = Path(args.out)
    cycles = asyncio.run(run_soak(args.duration, args.interval, out_path))
    print(f"Soak complete: {cycles} cycles over {args.duration:.0f}s -> {out_path}")


if __name__ == "__main__":
    main()
