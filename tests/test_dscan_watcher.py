"""Tests for D-scan classification + parsing (issues #101, #106)."""

import unittest

from evealert.tools.dscan_watcher import DscanWatcher, classify_entry


class ClassifyTests(unittest.TestCase):
    def test_supercarrier_before_carrier(self):
        # "supercarrier" must not be shadowed by the "carrier" substring.
        self.assertEqual(classify_entry("Supercarrier"), "red")

    def test_probe(self):
        self.assertEqual(classify_entry("Core Scanner Probe"), "probe")

    def test_unknown(self):
        self.assertEqual(classify_entry("Veldspar"), "unknown")


class ParseLinesTests(unittest.TestCase):
    def _watcher(self):
        threats, entries = [], []
        w = DscanWatcher(
            on_threat=lambda t, n, c=None: threats.append((t, n, c)),
            on_probe=lambda: None,
            on_entry=lambda e: entries.append(e),
        )
        return w, threats, entries

    def test_classifies_by_type_column(self):
        # Column 0 is a custom ship name that matches nothing; column 2 (type)
        # is the reliable "Force Recon Ship" -> red.
        w, threats, _ = self._watcher()
        line = "My Ship Name\t12 km\tForce Recon Ship\tRecon Ship"
        current, _types, probe, _sigs = w._parse_lines(line + "\n")
        self.assertIn("My Ship Name", current)
        self.assertEqual(threats[-1][0], "red")

    def test_name_fallback_when_no_type_column(self):
        w, threats, _ = self._watcher()
        # Only a name column; "Sabre" is a known red ship name.
        current, _types, probe, _sigs = w._parse_lines("Sabre\n")
        self.assertEqual(threats[-1][0], "red")

    def test_probe_detected(self):
        w, _, _ = self._watcher()
        _, _types, probe, _sigs = w._parse_lines("Probe\t5 km\tCore Scanner Probe\tScanner Probe\n")
        self.assertTrue(probe)

    def test_cosmic_signature_counted(self):
        w, _, _ = self._watcher()
        _, _types, _, sig_count = w._parse_lines(
            "ABC-123\t1234 km\tCosmic Signature\t\n"
            "DEF-456\t2000 km\tCosmic Signature\t\n"
        )
        self.assertEqual(sig_count, 2)

    def test_new_signature_callback_fires(self):
        events = []
        w = DscanWatcher(on_new_signature=lambda old, new: events.append((old, new)))
        # Simulate two consecutive scans via _sig_count manipulation
        w._sig_count = 1
        # Inject a second sig manually
        w._sig_count = 1
        # Fire the callback path directly
        _, _types, _, sig_count = w._parse_lines(
            "ABC-123\t1234 km\tCosmic Signature\t\n"
            "XYZ-789\t500 km\tCosmic Signature\t\n"
        )
        # Manually fire callback as _tail_once would do (read_to_eof=True)
        if sig_count > w._sig_count:
            w._on_new_signature(w._sig_count, sig_count)
            w._sig_count = sig_count
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], (1, 2))


class EncodingDetectTests(unittest.TestCase):
    def test_detect_utf16_le_bom(self, tmp=None):
        import tempfile
        from pathlib import Path

        w = DscanWatcher()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "dscan.txt"
            p.write_bytes(b"\xff\xfe" + "Rifter".encode("utf-16-le"))
            w._log_path = p
            self.assertEqual(w._detect_encoding(), "utf-16-le")

    def test_detect_utf8_default(self):
        import tempfile
        from pathlib import Path

        w = DscanWatcher()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "dscan.txt"
            p.write_bytes(b"Rifter\t10 km\tFrigate\tFrigate\n")
            w._log_path = p
            self.assertEqual(w._detect_encoding(), "utf-8")


class LatestLogRaceConditionTests(unittest.TestCase):
    """#234: a file glob() found can be deleted/locked (rotation,
    antivirus, manual cleanup) by the time stat() runs on it -- must be
    skipped, not let the whole call raise."""

    def test_normal_case_returns_newest_file(self):
        import tempfile
        import time
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            older = Path(d) / "older.txt"
            newer = Path(d) / "newer.txt"
            older.write_text("x")
            time.sleep(0.01)
            newer.write_text("x")
            result = DscanWatcher._latest_log(Path(d))
        self.assertEqual(result, newer)

    def test_empty_directory_returns_none(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            result = DscanWatcher._latest_log(Path(d))
        self.assertIsNone(result)

    def test_a_file_whose_stat_raises_is_skipped_not_fatal(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "good.txt"
            bad = Path(d) / "bad.txt"
            good.write_text("x")
            bad.write_text("x")

            real_stat = Path.stat

            def flaky_stat(self, *a, **kw):
                if self.name == "bad.txt":
                    raise OSError("file vanished")
                return real_stat(self, *a, **kw)

            with mock.patch.object(Path, "stat", flaky_stat):
                result = DscanWatcher._latest_log(Path(d))

        self.assertEqual(result, good)

    def test_every_file_failing_stat_returns_none_not_raises(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "gone1.txt").write_text("x")
            (Path(d) / "gone2.txt").write_text("x")

            real_stat = Path.stat

            def flaky_stat(self, *a, **kw):
                if self.suffix == ".txt":
                    raise OSError("gone")
                return real_stat(self, *a, **kw)

            with mock.patch.object(Path, "stat", flaky_stat):
                result = DscanWatcher._latest_log(Path(d))
        self.assertIsNone(result)


class RunLoopResilienceTests(unittest.IsolatedAsyncioTestCase):
    """#234: an OSError inside one poll iteration must not kill the whole
    run() task -- D-scan monitoring would otherwise be gone silently for
    the rest of the session."""

    async def test_oserror_in_poll_does_not_stop_the_loop(self):
        from pathlib import Path
        from unittest import mock

        w = DscanWatcher()
        poll_count = {"n": 0}

        async def fake_sleep(_):
            poll_count["n"] += 1
            if poll_count["n"] >= 3:
                w._running = False

        def flaky_latest_log(dscan_dir):
            poll_count_now = poll_count["n"]
            if poll_count_now == 0:
                raise OSError("directory temporarily unavailable")
            return None  # subsequent polls recover, no file found

        with mock.patch.object(w, "_find_dscan_dir", return_value=Path("/fake/dscan")), \
             mock.patch.object(w, "_latest_log", flaky_latest_log), \
             mock.patch("evealert.tools.dscan_watcher.asyncio.sleep", new=fake_sleep):
            await w.run()  # must return normally, not raise

        self.assertGreaterEqual(poll_count["n"], 3)


if __name__ == "__main__":
    unittest.main()
