"""Tests for wormhole / Eve-Scout parsing (issue #101)."""

import asyncio
import unittest

import respx
from httpx import Response

from evealert.tools import wormhole
from evealert.tools.wormhole import _infer_wh_class, get_thera_connections

_SIG_URL = "https://api.eve-scout.com/v2/public/signatures"

_SAMPLE = [
    {
        "id": "69438",
        "signature_type": "wormhole",
        "wh_type": "J377",
        "expires_at": "2026-07-12T19:04:09.000Z",
        "remaining_hours": 6,
        "out_system_id": 30002086,
        "out_system_name": "Turnur",
        "in_system_id": 31001614,
        "in_system_class": "c4",
        "in_system_name": "J154833",
    },
    {
        # Non-wormhole signatures must be ignored
        "id": "70000",
        "signature_type": "gas",
        "out_system_id": 31000005,
        "out_system_name": "Thera",
    },
]


class TheraParsingTests(unittest.TestCase):
    def test_parses_flat_v2_schema(self):
        with respx.mock:
            respx.get(_SIG_URL).mock(return_value=Response(200, json=_SAMPLE))
            conns = asyncio.run(get_thera_connections())
        self.assertEqual(len(conns), 1)  # only the wormhole entry
        c = conns[0]
        self.assertEqual(c.hub_system_name, "Turnur")
        self.assertEqual(c.system_name, "J154833")
        self.assertEqual(c.system_class, "c4")
        self.assertEqual(c.wh_type, "J377")
        self.assertEqual(c.remaining_hours, 6)
        self.assertEqual(c.system_id, 31001614)

    def test_empty_on_http_error(self):
        with respx.mock:
            respx.get(_SIG_URL).mock(return_value=Response(500))
            conns = asyncio.run(get_thera_connections())
        self.assertEqual(conns, [])


class WhClassTests(unittest.TestCase):
    def test_thera_recognized(self):
        self.assertEqual(_infer_wh_class(31000005), "Thera")

    def test_other_wh_is_unknown_not_fabricated(self):
        # Previously returned a fabricated "C1" band; must be honest now.
        self.assertEqual(_infer_wh_class(31000200), "Unknown")

    def test_kspace_is_not_wh(self):
        self.assertEqual(_infer_wh_class(30000142), "k-space")


if __name__ == "__main__":
    unittest.main()
