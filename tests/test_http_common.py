"""Tests for evealert.tools.http_common (#137)."""

import unittest

from evealert import __version__
from evealert.tools.http_common import DEFAULT_HEADERS, USER_AGENT


class UserAgentTests(unittest.TestCase):
    def test_contains_version(self):
        self.assertIn(__version__, USER_AGENT)

    def test_contains_repo_url(self):
        self.assertIn("github.com/bluhayz/EVE-Alert", USER_AGENT)

    def test_default_headers_has_user_agent(self):
        self.assertIn("User-Agent", DEFAULT_HEADERS)
        self.assertEqual(DEFAULT_HEADERS["User-Agent"], USER_AGENT)

    def test_default_headers_has_accept_encoding(self):
        self.assertIn("Accept-Encoding", DEFAULT_HEADERS)


if __name__ == "__main__":
    unittest.main()
