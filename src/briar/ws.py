"""Minimal RFC 6455 WebSocket client (stdlib socket + ssl).

Why hand-rolled: stdlib has no WebSocket module, and pulling in
`websockets` or `websocket-client` would defeat the "drop-in script"
design. This implementation handles the HTTP/1.1 upgrade handshake,
masked outbound frames, ping/pong, and the clean-close handshake. Text
frames only — no binary, no extensions, no compression.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import ssl
import urllib.parse
from typing import Iterator, Optional, Tuple

from briar.errors import CliError
from briar.settings import WS_GUID


def xor_mask(payload: bytes, mask: bytes) -> bytes:
    """RFC 6455 §5.3 client-to-server masking. Involutive."""
    return bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def _header_value(headers_bytes: bytes, name_lower: bytes) -> str:
    for line in headers_bytes.split(b"\r\n"):
        if not line or b":" not in line:
            continue
        key, _, val = line.partition(b":")
        if key.strip().lower() == name_lower:
            return val.strip().decode("ascii", errors="replace")
    return ""


class WebSocketClient:
    """Stdlib-only WebSocket client. Text frames only.

    Use as:
        ws = WebSocketClient(url)
        ws.connect()
        try:
            for opcode, payload in ws.frames():
                if opcode == WebSocketClient.OP_TEXT:
                    print(payload.decode())
        finally:
            ws.close()
    """

    OP_CONT = 0x0
    OP_TEXT = 0x1
    OP_BIN = 0x2
    OP_CLOSE = 0x8
    OP_PING = 0x9
    OP_PONG = 0xA

    def __init__(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"ws", "wss"}:
            raise CliError(f"unsupported ws scheme: {scheme}")
        self._tls = scheme == "wss"
        self._host = parsed.hostname or ""
        self._port = parsed.port or (443 if self._tls else 80)
        self._path = parsed.path or "/"
        if parsed.query:
            self._path = f"{self._path}?{parsed.query}"
        self._sock: Optional[socket.socket] = None
        self._buffer: bytes = b""

    # ---- public API ------------------------------------------------------

    def connect(self) -> None:
        raw = socket.create_connection((self._host, self._port), timeout=30)
        sock: socket.socket = raw
        if self._tls:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw, server_hostname=self._host)
        self._sock = sock

        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        self._send_handshake(key)
        self._validate_handshake(key)

    def frames(self) -> Iterator[Tuple[int, bytes]]:
        """Yield (opcode, payload). Pings auto-pong'd; CLOSE terminates."""
        while True:
            opcode, payload = self._read_frame()
            if opcode == self.OP_PING:
                self._write_frame(self.OP_PONG, payload)
                continue
            if opcode == self.OP_CLOSE:
                self._write_frame(self.OP_CLOSE, payload)
                return
            yield opcode, payload

    def send_text(self, text: str) -> None:
        self._write_frame(self.OP_TEXT, text.encode("utf-8"))

    def close(self) -> None:
        sock = self._sock
        if sock is None:
            return
        try:
            self._write_frame(self.OP_CLOSE, b"")
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
        self._sock = None

    # ---- handshake -------------------------------------------------------

    def _send_handshake(self, key: str) -> None:
        sock = self._require_sock()
        req = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {self._host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"User-Agent: briar-cli/0.3\r\n"
            f"\r\n"
        )
        sock.sendall(req.encode("ascii"))

    def _validate_handshake(self, key: str) -> None:
        sock = self._require_sock()
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = sock.recv(4096)
            if not chunk:
                raise CliError("ws handshake: server closed connection")
            header += chunk

        status_line, _, rest = header.partition(b"\r\n")
        parts = status_line.split(b" ", 2)
        if len(parts) < 2 or parts[1] != b"101":
            raise CliError(
                f"ws handshake failed: "
                f"{status_line.decode(errors='replace')}"
            )

        expected = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if _header_value(rest, b"sec-websocket-accept") != expected:
            raise CliError("ws handshake: bad Sec-WebSocket-Accept")

        # Anything past the header start is the first chunk of frame data.
        idx = header.find(b"\r\n\r\n") + 4
        self._buffer = header[idx:]

    # ---- frame I/O -------------------------------------------------------

    def _require_sock(self) -> socket.socket:
        sock = self._sock
        if sock is None:
            raise CliError("ws: socket not connected")
        return sock

    def _read_exact(self, n: int) -> bytes:
        sock = self._require_sock()
        while len(self._buffer) < n:
            chunk = sock.recv(65536)
            if not chunk:
                raise CliError("ws: peer closed during read")
            self._buffer += chunk
        out = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return out

    def _read_frame(self) -> Tuple[int, bytes]:
        hdr = self._read_exact(2)
        b0, b1 = hdr[0], hdr[1]
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            length = int.from_bytes(self._read_exact(2), "big")
        if length == 127:
            length = int.from_bytes(self._read_exact(8), "big")
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked and mask:
            payload = xor_mask(payload, mask)
        return opcode, payload

    def _write_frame(self, opcode: int, payload: bytes) -> None:
        sock = self._require_sock()
        header = bytearray()
        header.append(0x80 | opcode)  # FIN + opcode
        length = len(payload)
        mask_bit = 0x80
        if length < 126:
            header.append(mask_bit | length)
        elif length < (1 << 16):
            header.append(mask_bit | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append(mask_bit | 127)
            header += length.to_bytes(8, "big")
        mask = secrets.token_bytes(4)
        header += mask
        sock.sendall(bytes(header) + xor_mask(payload, mask))
