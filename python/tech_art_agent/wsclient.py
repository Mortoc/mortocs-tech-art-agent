"""A minimal RFC 6455 WebSocket client in the standard library.

Blender and Houdini ship no `websockets` package and we may not install into their Pythons, so the
connector carries its own. Text frames only, which is all the core speaks.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(ConnectionError):
    pass


class WebSocket:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._send_lock = threading.Lock()
        self.closed = False

    @classmethod
    def connect(cls, url: str, timeout: float = 5.0) -> WebSocket:
        parts = urlsplit(url)
        if parts.scheme != "ws":
            raise WebSocketError(f"only ws:// is supported (the core is loopback-only): {url}")
        sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        request = (
            f"GET {target or '/'} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during the handshake")
            head += chunk
        header, _, rest = head.partition(b"\r\n\r\n")
        lines = header.decode("latin1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise WebSocketError(f"the core refused the connection: {lines[0]}")
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if not any(line.lower().startswith("sec-websocket-accept:") and line.split(":", 1)[1].strip() == accept for line in lines[1:]):
            raise WebSocketError("bad Sec-WebSocket-Accept")
        sock.settimeout(None)
        ws = cls(sock)
        ws._buffer = rest
        return ws

    _buffer = b""

    def _read(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WebSocketError("connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def _frame(self, opcode: int, payload: bytes) -> bytes:
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return head + mask + masked

    def send(self, text: str) -> None:
        with self._send_lock:
            self._sock.sendall(self._frame(0x1, text.encode()))

    def recv(self) -> str:
        """The next text message; answers pings and returns on close by raising WebSocketError."""
        message = b""
        while True:
            b1, b2 = self._read(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)  # server frames are unmasked
            if opcode == 0x8:
                self.closed = True
                raise WebSocketError("closed by the core")
            if opcode == 0x9:
                with self._send_lock:
                    self._sock.sendall(self._frame(0xA, payload))
                continue
            if opcode == 0xA:
                continue
            message += payload
            if fin:
                return message.decode()

    def close(self) -> None:
        self.closed = True
        try:
            with self._send_lock:
                self._sock.sendall(self._frame(0x8, b""))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
