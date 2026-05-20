"""WebSocket helper tests (no real connection)."""

from __future__ import annotations

import unittest

from briar.errors import CliError
from briar.ws import WebSocketClient, xor_mask


class XorMaskTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        mask = b"\x01\x02\x03\x04"
        original = b"hello world"
        self.assertEqual(xor_mask(xor_mask(original, mask), mask), original)


class UrlParsingTests(unittest.TestCase):
    def test_wss_default_port(self) -> None:
        ws = WebSocketClient("wss://api.example.com/ws/x/?token=t")
        self.assertEqual(ws._host, "api.example.com")
        self.assertEqual(ws._port, 443)
        self.assertEqual(ws._path, "/ws/x/?token=t")
        self.assertTrue(ws._tls)

    def test_ws_default_port(self) -> None:
        ws = WebSocketClient("ws://localhost:8000/ws/x/")
        self.assertEqual(ws._host, "localhost")
        self.assertEqual(ws._port, 8000)
        self.assertFalse(ws._tls)

    def test_bad_scheme_rejected(self) -> None:
        with self.assertRaises(CliError):
            WebSocketClient("http://x/")


if __name__ == "__main__":
    unittest.main()
