"""Tests for LogPane's clickable zkillboard/dotlan hyperlinks (#207) and
the #210 name-as-link marker convention shared with alertmanager.py.

Uses the offscreen Qt platform so no display is needed in CI.
"""

import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_pane():
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    QApplication.instance() or QApplication([])  # ensure app exists

    from evealert.ui.log_pane import LogPane  # noqa: PLC0415

    return LogPane()


class LinkifyRegexTests(unittest.TestCase):
    """_LINK_RE must match only known link hosts, never plain pilot/corp
    names that happen to contain a period (EVE allows '.' in names)."""

    def test_matches_zkillboard_character_link(self):
        from evealert.ui.log_pane import _LINK_RE  # noqa: PLC0415

        m = _LINK_RE.search("zkillboard.com/character/12345/")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("url"), "zkillboard.com/character/12345/")

    def test_matches_dotlan_system_link(self):
        from evealert.ui.log_pane import _LINK_RE  # noqa: PLC0415

        m = _LINK_RE.search("dotlan.net/system/Jita")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("url"), "dotlan.net/system/Jita")

    def test_matches_zkillboard_search_link_with_hash(self):
        from evealert.ui.log_pane import _LINK_RE  # noqa: PLC0415

        m = _LINK_RE.search("zkillboard.com/search/#Roger+Booth")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("url"), "zkillboard.com/search/#Roger+Booth")

    def test_does_not_match_name_with_period(self):
        from evealert.ui.log_pane import _LINK_RE  # noqa: PLC0415

        self.assertIsNone(_LINK_RE.search("Dr. Evil"))
        self.assertIsNone(_LINK_RE.search("Some.Guy"))

    def test_does_not_match_unrelated_domain(self):
        """Only the two hosts this app actually generates should linkify."""
        from evealert.ui.log_pane import _LINK_RE  # noqa: PLC0415

        self.assertIsNone(_LINK_RE.search("example.com/whatever"))


class EntryToHtmlTests(unittest.TestCase):
    def test_link_gets_https_scheme_and_distinct_color(self):
        from evealert.ui.log_pane import _entry_to_html  # noqa: PLC0415
        from evealert.ui import theme  # noqa: PLC0415

        html_out = _entry_to_html(
            "Bad Guy | zkillboard.com/character/999/", theme.LOG_COLORS["red"]
        )
        self.assertIn('href="https://zkillboard.com/character/999/"', html_out)
        self.assertIn(theme.LOG_LINK_COLOR, html_out)
        self.assertIn(theme.LOG_COLORS["red"], html_out)  # base line color preserved

    def test_existing_scheme_not_doubled(self):
        from evealert.ui.log_pane import _entry_to_html  # noqa: PLC0415
        from evealert.ui import theme  # noqa: PLC0415

        html_out = _entry_to_html(
            "see https://zkillboard.com/character/1/", theme.LOG_COLORS["cyan"]
        )
        self.assertIn('href="https://zkillboard.com/character/1/"', html_out)
        self.assertNotIn("https://https://", html_out)

    def test_html_special_chars_escaped(self):
        from evealert.ui.log_pane import _entry_to_html  # noqa: PLC0415
        from evealert.ui import theme  # noqa: PLC0415

        html_out = _entry_to_html("<script>alert(1)</script> & co", theme.LOG_COLORS["normal"])
        self.assertNotIn("<script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)

    def test_no_link_present_no_anchor_tag(self):
        from evealert.ui.log_pane import _entry_to_html  # noqa: PLC0415
        from evealert.ui import theme  # noqa: PLC0415

        html_out = _entry_to_html("Enemy Appears! — bluhayz", theme.LOG_COLORS["red"])
        self.assertNotIn("<a href", html_out)


class LinkMarkerTests(unittest.TestCase):
    """#210: make_link()/MARKER_RE round-trip, and _entry_to_html() renders
    the marker's display text as the anchor instead of showing the URL."""

    def test_make_link_round_trips_through_marker_re(self):
        from evealert.tools.link_markers import MARKER_RE, make_link  # noqa: PLC0415

        encoded = make_link("Bad Guy", "https://zkillboard.com/character/999/")
        m = MARKER_RE.search(encoded)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("text"), "Bad Guy")
        self.assertEqual(m.group("url"), "https://zkillboard.com/character/999/")

    def test_entry_to_html_renders_name_as_anchor_text_not_url(self):
        from evealert.tools.link_markers import make_link  # noqa: PLC0415
        from evealert.ui.log_pane import _entry_to_html  # noqa: PLC0415
        from evealert.ui import theme  # noqa: PLC0415

        line = f"  ⚠ [KOS-RED] {make_link('Bad Guy', 'https://zkillboard.com/character/999/')} — 5d old"
        html_out = _entry_to_html(line, theme.LOG_COLORS["red"])
        self.assertIn('href="https://zkillboard.com/character/999/"', html_out)
        self.assertIn(">Bad Guy</a>", html_out)
        # The raw URL must not appear as separate visible text outside the href.
        self.assertNotIn(">https://zkillboard.com/character/999/<", html_out)

    def test_entry_to_html_marker_and_bare_url_can_coexist(self):
        """A marker elsewhere in the line must not stop the legacy bare-URL
        fallback from linkifying a separate, un-marked URL, and vice versa —
        each is only substituted in its own (non-overlapping) span."""
        from evealert.tools.link_markers import make_link  # noqa: PLC0415
        from evealert.ui.log_pane import _entry_to_html  # noqa: PLC0415
        from evealert.ui import theme  # noqa: PLC0415

        line = (
            f"{make_link('Bad Guy', 'https://zkillboard.com/character/1/')} "
            "also see zkillboard.com/character/2/"
        )
        html_out = _entry_to_html(line, theme.LOG_COLORS["red"])
        self.assertIn('href="https://zkillboard.com/character/1/"', html_out)
        self.assertIn(">Bad Guy</a>", html_out)
        self.assertIn('href="https://zkillboard.com/character/2/"', html_out)
        self.assertIn(">zkillboard.com/character/2/</a>", html_out)

    def test_strip_markers_plain_renders_readable_name_and_url(self):
        from evealert.tools.link_markers import make_link  # noqa: PLC0415
        from evealert.ui.log_pane import _strip_markers_plain  # noqa: PLC0415

        line = f"  {make_link('Bad Guy', 'https://zkillboard.com/character/999/')} — 5d old"
        plain = _strip_markers_plain(line)
        self.assertEqual(plain, "  Bad Guy (https://zkillboard.com/character/999/) — 5d old")
        self.assertNotIn("\x02", plain)
        self.assertNotIn("\x1f", plain)
        self.assertNotIn("\x03", plain)


class LogPaneRenderTests(unittest.TestCase):
    """Integration-level: append() -> real QTextBrowser document."""

    def test_appended_link_is_clickable_anchor_in_widget(self):
        pane = _make_pane()
        pane.append(
            "  ⚠ [KOS-RED] Bad Guy — 5d old | zkillboard.com/character/42/", "red"
        )
        html_out = pane._log.toHtml()
        self.assertIn('href="https://zkillboard.com/character/42/"', html_out)

    def test_plain_text_extraction_keeps_url_readable(self):
        """Copy/paste (toPlainText, used by the context menu + bug reporter)
        must still yield clean text with the full URL, not markup."""
        pane = _make_pane()
        pane.append("Intel: Recent kills in Jita (2) — dotlan.net/system/Jita", "yellow")
        plain = pane._log.toPlainText()
        self.assertIn("dotlan.net/system/Jita", plain)
        self.assertNotIn("<a ", plain)
        self.assertNotIn("href=", plain)

    def test_multiple_entries_render_as_separate_lines(self):
        pane = _make_pane()
        pane.append("First line — zkillboard.com/character/1/", "red")
        pane.append("Second line — zkillboard.com/character/2/", "red")
        plain = pane._log.toPlainText()
        lines = [l for l in plain.splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        # Newest-first: the most recently appended entry is on top.
        self.assertIn("zkillboard.com/character/2/", lines[0])
        self.assertIn("zkillboard.com/character/1/", lines[1])

    def test_many_entries_stay_newest_first(self):
        pane = _make_pane()
        for i in range(5):
            pane.append(f"line {i}", "normal")
        plain = pane._log.toPlainText()
        lines = [ln for ln in plain.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 5)
        # line 4 (most recently appended) is on top, line 0 is at the bottom.
        for i, line in enumerate(lines):
            self.assertIn(f"line {4 - i}", line)

    def test_rerender_preserves_newest_first_order(self):
        """Switching filters (which calls _rerender) must not flip the
        newest-first order back to chronological."""
        pane = _make_pane()
        pane.append("System: one", "green")
        pane.append("System: two", "green")
        pane._set_tag_filter("system")
        plain = pane._log.toPlainText()
        lines = [ln for ln in plain.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        self.assertIn("System: two", lines[0])
        self.assertIn("System: one", lines[1])

    def test_trim_keeps_newest_entries_when_exceeding_block_max(self):
        """When the widget exceeds _WIDGET_BLOCK_MAX, the OLDEST entries
        must be dropped -- not the newest, which now live at the top of
        the document instead of the bottom."""
        import evealert.ui.log_pane as log_pane_mod

        pane = _make_pane()
        with patch.object(log_pane_mod, "_WIDGET_BLOCK_MAX", 3):
            for i in range(6):
                pane.append(f"line {i}", "normal")
            plain = pane._log.toPlainText()
        lines = [ln for ln in plain.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 3)
        # The 3 most recent lines (3, 4, 5) survive, newest on top.
        self.assertIn("line 5", lines[0])
        self.assertIn("line 4", lines[1])
        self.assertIn("line 3", lines[2])
        self.assertNotIn("line 0", plain)
        self.assertNotIn("line 2", plain)

    def test_name_with_period_not_linkified_in_widget(self):
        pane = _make_pane()
        pane.append("Intel: 2 hostile(s) in Jita [Dr. Evil, Some.Guy]", "red")
        html_out = pane._log.toHtml()
        self.assertNotIn("<a href", html_out)

    def test_appended_marker_link_is_clickable_anchor_on_name(self):
        """#210: the pilot's name (not a separate URL) is the clickable
        anchor once it flows through append() -> the real widget."""
        from evealert.tools.link_markers import make_link  # noqa: PLC0415

        pane = _make_pane()
        pane.append(
            f"  ⚠ [KOS-RED] {make_link('Bad Guy', 'https://zkillboard.com/character/42/')} "
            "— 5d old, 3 corp(s)",
            "red",
        )
        html_out = pane._log.toHtml()
        self.assertIn('href="https://zkillboard.com/character/42/"', html_out)
        plain = pane._log.toPlainText()
        self.assertIn("Bad Guy", plain)
        self.assertNotIn("zkillboard.com", plain)  # no separate visible URL

    def test_get_log_text_renders_marker_as_readable_name_and_url(self):
        """The bug-reporter export path (get_log_text) reads the raw
        buffer directly, bypassing the widget -- markers must still come
        out as clean, readable text there too."""
        from evealert.tools.link_markers import make_link  # noqa: PLC0415

        pane = _make_pane()
        pane.append(
            f"  ⚠ [KOS-RED] {make_link('Bad Guy', 'https://zkillboard.com/character/42/')}",
            "red",
        )
        text = pane.get_log_text()
        self.assertIn("Bad Guy (https://zkillboard.com/character/42/)", text)
        self.assertNotIn("\x02", text)
        self.assertNotIn("\x1f", text)
        self.assertNotIn("\x03", text)

    def test_filter_and_search_still_work_with_html_content(self):
        """LogPane's existing filter/search machinery operates on the plain
        buffered text, not the rendered HTML -- must be unaffected."""
        pane = _make_pane()
        pane.append("Intel: kills near Jita — dotlan.net/system/Jita", "cyan")
        pane.append("System: EVE Alert started.", "green")
        pane._set_tag_filter("intel")
        plain = pane._log.toPlainText()
        self.assertIn("dotlan.net", plain)
        self.assertNotIn("EVE Alert started", plain)


if __name__ == "__main__":
    unittest.main()
