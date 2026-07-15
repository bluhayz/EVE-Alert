"""Tests for evealert.tools.intel_parser (#142, #197)."""

import unittest

from evealert.tools.intel_parser import (
    IntelReport,
    _find_mentioned_pilots,
    parse_line,
)


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
        self.assertEqual(r.mentioned_pilots, [])

    def test_clear_report_has_no_mentioned_pilots(self):
        """Clear intel messages must not produce mentioned_pilots."""
        r = parse_line("[ 2024.05.01 15:30:22 ] Fenrir Haddig > YZS5-4 clear")
        self.assertIsNotNone(r)
        self.assertTrue(r.is_clear)
        self.assertEqual(r.mentioned_pilots, [])


class MentionedPilotsTests(unittest.TestCase):
    """Unit tests for _find_mentioned_pilots (#197)."""

    def test_extracts_two_word_pilot_name(self):
        # "YZS5-4 Roger Booth Proteus" — system + pilot + ship
        names = _find_mentioned_pilots("YZS5-4 Roger Booth Proteus", "YZS5-4", ["proteus"])
        self.assertIn("Roger Booth", names)
        # The ship should not appear as a pilot name
        self.assertNotIn("Proteus", names)

    def test_extracts_single_word_pilot(self):
        # "G-UTHL Naari nv" — system + pilot + shorthand
        names = _find_mentioned_pilots("BorC Naari G-UTHL nv", "BORC", [])
        self.assertIn("Naari", names)

    def test_shorthands_not_extracted(self):
        names = _find_mentioned_pilots("D7-ZAC nv clr clear xx", "D7-ZAC", [])
        self.assertEqual(names, [])

    def test_all_caps_tokens_skipped(self):
        # All-caps tokens look like system names / abbreviations, not pilot names
        names = _find_mentioned_pilots("D7-ZAC BLOPS SABRE", "D7-ZAC", [])
        self.assertEqual(names, [])

    def test_empty_message_returns_empty(self):
        self.assertEqual(_find_mentioned_pilots("", None, []), [])

    def test_system_name_not_extracted_as_pilot(self):
        names = _find_mentioned_pilots("YZS5-4 Roger Booth", "YZS5-4", [])
        self.assertNotIn("YZS5-4", names)
        self.assertIn("Roger Booth", names)

    def test_known_ship_excluded(self):
        # "Tengu" is in _KNOWN_SHIPS — should not appear as a pilot name
        names = _find_mentioned_pilots("D7-ZAC Tengu 2", "D7-ZAC", ["tengu"])
        self.assertNotIn("Tengu", names)

    def test_parse_line_populates_mentioned_pilots(self):
        line = "[ 2024.05.01 15:30:22 ] Fenrir Haddig > YZS5-4 Roger Booth Proteus"
        r = parse_line(line)
        self.assertIsNotNone(r)
        self.assertIn("Roger Booth", r.mentioned_pilots)

    def test_parse_line_mentioned_pilots_empty_on_clear(self):
        line = "[ 2024.05.01 15:30:22 ] Fenrir Haddig > YZS5-4 clr"
        r = parse_line(line)
        self.assertIsNotNone(r)
        self.assertEqual(r.mentioned_pilots, [])


if __name__ == "__main__":
    unittest.main()
