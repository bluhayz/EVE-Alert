"""CI-runnable smoke test for tools/bench_vision.py (#175, v7.2).

Not a strict perf gate (CI runners vary too much for a hardcoded ms/frame
threshold) -- asserts the harness runs cleanly against the committed
fixtures and that a static (repeated-frame) workload is meaningfully
faster than a changing one, which is a real regression signal if someone
breaks the frame-change short-circuit without needing to pin an absolute
timing number.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.bench_vision import (  # noqa: E402
    _default_fixtures_dir,
    load_frames,
    run_benchmark,
    run_multi_client_benchmark,
)


class BenchVisionHarnessTests(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = _default_fixtures_dir()
        self.needle = os.path.join(self.fixtures_dir, "needle.png")

    def test_fixtures_present_and_loadable(self):
        frames = load_frames(self.fixtures_dir)
        self.assertGreaterEqual(len(frames), 2)

    def test_run_benchmark_completes_and_reports_matches(self):
        result = run_benchmark(
            self.fixtures_dir, self.needle, iterations=2, repeat=1
        )
        self.assertEqual(result["frame_count"], 3)
        self.assertGreater(result["total_matches"], 0)  # frame_02/03 have needle pasted in
        self.assertGreater(result["ms_per_frame"], 0.0)
        # Generous smoke bound -- not a strict perf gate (see module docstring).
        self.assertLess(result["ms_per_frame"], 500.0)

    def test_static_workload_is_faster_than_changing_workload(self):
        """Regression guard for the frame-change short-circuit: repeating
        the SAME frame many times in a row must be substantially cheaper
        per-frame than cycling through different frames every call."""
        static_result = run_benchmark(
            self.fixtures_dir, self.needle, iterations=5, repeat=20
        )
        changing_result = run_benchmark(
            self.fixtures_dir, self.needle, iterations=20, repeat=1
        )
        self.assertLess(
            static_result["ms_per_frame"], changing_result["ms_per_frame"],
            "Static (repeated-frame) workload should be faster per-frame "
            "than a constantly-changing one -- did the frame-cache break?",
        )


class MultiClientBenchmarkTests(unittest.TestCase):
    """#174 acceptance criterion: 3-client CPU benchmark. Not a strict
    perf gate -- asserts N independent clients scale roughly linearly
    (not exponentially) in aggregate cost, which is the real regression
    signal for "does adding clients cause a CPU blowup"."""

    def setUp(self):
        self.fixtures_dir = _default_fixtures_dir()
        self.needle = os.path.join(self.fixtures_dir, "needle.png")

    def test_three_clients_completes_and_reports_aggregate_stats(self):
        result = run_multi_client_benchmark(
            self.fixtures_dir, self.needle, n_clients=3, iterations=2, repeat=1
        )
        self.assertEqual(result["n_clients"], 3)
        self.assertGreater(result["total_matches"], 0)
        self.assertGreater(result["ms_per_frame"], 0.0)

    def test_three_clients_scales_roughly_linearly_not_exponentially(self):
        one = run_multi_client_benchmark(
            self.fixtures_dir, self.needle, n_clients=1, iterations=10, repeat=5
        )
        three = run_multi_client_benchmark(
            self.fixtures_dir, self.needle, n_clients=3, iterations=10, repeat=5
        )
        # Generous upper bound (5x, not a tight 3x) -- CI timing noise, not
        # a strict perf gate. The real regression this catches is
        # accidental O(n^2)-or-worse cross-client interference (e.g. a
        # shared, unscoped cache serving wrong results and forcing
        # constant re-matching).
        self.assertLess(three["elapsed_seconds"], one["elapsed_seconds"] * 5)


if __name__ == "__main__":
    unittest.main()
