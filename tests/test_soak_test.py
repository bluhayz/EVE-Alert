"""CI-runnable smoke test for tools/soak_test.py (#177, v7.2).

Not a real soak (that needs a human running it for hours -- see the
module docstring) -- asserts the driver runs cleanly for a couple of
synthetic cycles, produces a well-formed CSV, and that the TTL caches it
seeds each sample tick are actually purged back down (the core mechanism
this issue is about), without needing a live EVE client or network
access.
"""

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import psutil  # noqa: F401
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


@unittest.skipUnless(_PSUTIL_AVAILABLE, "psutil not installed (pip install \".[soak]\")")
class SoakTestSmokeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)

    def tearDown(self):
        import shutil
        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_short_run_produces_a_well_formed_csv(self):
        from tools.soak_test import run_soak  # noqa: PLC0415

        out_path = Path(self.temp_dir) / "soak.csv"
        cycles = await run_soak(duration=0.5, interval=0.2, out_path=out_path)

        self.assertGreater(cycles, 0)
        self.assertTrue(out_path.exists())
        with open(out_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertGreaterEqual(len(rows), 2)  # header + at least one sample
        header = rows[0]
        self.assertIn("rss_mb", header)
        self.assertIn("threads", header)
        self.assertIn("asyncio_tasks", header)
        self.assertIn("seen_enemies", header)
        self.assertIn("universe_kill_cache", header)

    async def test_ttl_caches_are_purged_back_down_each_sample(self):
        """The core #177 mechanism: caches must not accumulate across
        samples -- each tick seeds one stale entry and immediately purges
        it, so the tracked column stays at 0 throughout."""
        from tools.soak_test import run_soak  # noqa: PLC0415

        out_path = Path(self.temp_dir) / "soak.csv"
        await run_soak(duration=0.6, interval=0.2, out_path=out_path)

        with open(out_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(int(row["universe_kill_cache"]), 0)
            self.assertEqual(int(row["zkb_cache"]), 0)
            self.assertEqual(int(row["heatmap_cache"]), 0)

    async def test_synthetic_alarm_cycle_does_not_raise(self):
        from tools.soak_test import _build_agent, _synthetic_alarm_cycle  # noqa: PLC0415
        import random  # noqa: PLC0415

        agent = _build_agent(Path(self.temp_dir))
        rng = random.Random(1)
        for _ in range(20):
            await _synthetic_alarm_cycle(agent, rng)  # must not raise
        self.assertIsInstance(agent._seen_enemies, dict)


if __name__ == "__main__":
    unittest.main()
