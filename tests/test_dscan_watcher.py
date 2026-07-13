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
        current, probe = w._parse_lines(line + "\n")
        self.assertIn("My Ship Name", current)
        self.assertEqual(threats[-1][0], "red")

    def test_name_fallback_when_no_type_column(self):
        w, threats, _ = self._watcher()
        # Only a name column; "Sabre" is a known red ship name.
        current, probe = w._parse_lines("Sabre\n")
        self.assertEqual(threats[-1][0], "red")

    def test_probe_detected(self):
        w, _, _ = self._watcher()
        _, probe = w._parse_lines("Probe\t5 km\tCore Scanner Probe\tScanner Probe\n")
        self.assertTrue(probe)


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


if __name__ == "__main__":
    unittest.main()
