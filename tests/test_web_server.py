"""Tests for the web status server HTML rendering (issue #109)."""

import unittest

from evealert.tools.web_server import WebStatusServer, append_to_log_buffer


class _FakeStats:
    session_alarms = 3
    total_alarms = 42


class WebStatusHtmlTests(unittest.TestCase):
    def _server(self, running):
        return WebStatusServer(port=0, stats_ref=_FakeStats(), running_ref=[running])

    def test_html_page_renders_when_running(self):
        html = self._server(True)._html_page()
        self.assertIn('class="running"', html)
        self.assertIn("RUNNING", html)
        self.assertIn("EVE Alert v", html)

    def test_html_page_renders_when_stopped(self):
        html = self._server(False)._html_page()
        self.assertIn('class="stopped"', html)
        self.assertIn("STOPPED", html)

    def test_html_page_includes_log_lines(self):
        append_to_log_buffer("test log entry")
        html = self._server(True)._html_page()
        self.assertIn("test log entry", html)

    def test_html_page_does_not_raise_keyerror(self):
        # Regression: the template previously contained an f-string expression
        # rendered via str.format(), raising KeyError on every request.
        try:
            self._server(True)._html_page()
        except KeyError as exc:
            self.fail(f"_html_page raised KeyError: {exc}")


if __name__ == "__main__":
    unittest.main()
