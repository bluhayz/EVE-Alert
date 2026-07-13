"""Tests for evealert.tools.intel_parser (#142)."""

import unittest

from evealert.tools.intel_parser import parse_line, IntelReport


class ParseLineTests(unittest.TestCase):
    def _line(self, pilot, message):
        return f"[ 2024.05.01 15:30:22 ] {pilot} > {message}"

    def test_returns_none_for_non_chat(self):
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("-------"))
        self.assertIsNone(parse_line("2024.05.01 15:30:22  System Message"))

    def test_parses_pilot_and_message(self):
        r = parse_line(self._line("Foo Bar", "D7-ZAC sabre 2"))
        self.assertIsNotNone(r)
        self.assertEqual(r.pilot, "Foo Bar")

    def test_detects_system_name(self):
        r = parse_line(self._line("Pilot", "D7-ZAC 2 hostiles"))
        self.assertIsNotNone(r)
        self.assertIsNotNone(r.system)
        self.assertIn("D7", r.system)

    def test_hostile_count(self):
        r = parse_line(self._line("Pilot", "D7-ZAC 3 scimitars"))
        self.assertIsNotNone(r)
        self.assertEqual(r.hostile_count, 3)

    def test_clear_signal(self):
        r = parse_line(self._line("Pilot", "D7-ZAC clear"))
        self.assertIsNotNone(r)
        self.assertTrue(r.is_clear)
        self.assertEqual(r.hostile_count, 0)

    def test_clr_abbreviation(self):
        r = parse_line(self._line("Pilot", "3V8-LK clr"))
        self.assertIsNotNone(r)
        self.assertTrue(r.is_clear)

    def test_ship_detection(self):
        r = parse_line(self._line("Pilot", "1DQ1-A sabre 1"))
        self.assertIsNotNone(r)
        self.assertIn("sabre", r.ships)

    def test_no_ships_when_none_mentioned(self):
        r = parse_line(self._line("Pilot", "D7-ZAC 2"))
        self.assertIsNotNone(r)
        self.assertEqual(r.ships, [])

    def test_default_count_1_when_no_number(self):
        r = parse_line(self._line("Pilot", "Y-MPWL hostile"))
        self.assertIsNotNone(r)
        self.assertEqual(r.hostile_count, 1)

    def test_raw_line_preserved(self):
        line = self._line("Pilot", "D7-ZAC clear")
        r = parse_line(line)
        self.assertEqual(r.raw_line, line)

    def test_eve_system_pilot_ignored(self):
        r = parse_line(self._line("EVE System", "some system message"))
        self.assertIsNone(r)


class IntelReportTests(unittest.TestCase):
    def test_report_defaults(self):
        r = IntelReport(pilot="P", raw_line="", system="D7-ZAC",
                        hostile_count=1, is_clear=False)
        self.assertEqual(r.jump_distance, None)
        self.assertEqual(r.ships, [])


if __name__ == "__main__":
    unittest.main()
