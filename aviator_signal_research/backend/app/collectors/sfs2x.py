"""Minimal decoder for SmartFoxServer 2X binary messages (SFSObject format).

Used to read round-state messages that a game page receives over its own
WebSocket. Only decoding; nothing is sent. Format (public SFS2X protocol):
header byte (0x80 binary | 0x40 encrypted | 0x20 compressed | 0x08 big-size),
2- or 4-byte length, then an SFSObject (type 0x12).
"""
from __future__ import annotations

import struct
import zlib


class DecodeError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.b = data
        self.i = 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.b):
            raise DecodeError("truncated message")
        out = self.b[self.i:self.i + n]
        self.i += n
        return out

    def u8(self):
        return self.take(1)[0]

    def i16(self):
        return struct.unpack(">h", self.take(2))[0]

    def u16(self):
        return struct.unpack(">H", self.take(2))[0]

    def i32(self):
        return struct.unpack(">i", self.take(4))[0]

    def utf(self):
        return self.take(self.u16()).decode("utf-8", "replace")

    def value(self):
        t = self.u8()
        if t == 0:
            return None
        if t == 1:
            return self.u8() != 0
        if t == 2:
            return struct.unpack(">b", self.take(1))[0]
        if t == 3:
            return self.i16()
        if t == 4:
            return self.i32()
        if t == 5:
            return struct.unpack(">q", self.take(8))[0]
        if t == 6:
            return struct.unpack(">f", self.take(4))[0]
        if t == 7:
            return struct.unpack(">d", self.take(8))[0]
        if t == 8:
            return self.utf()
        if t == 9:
            return [self.u8() != 0 for _ in range(self.u16())]
        if t == 10:
            return self.take(self.i32())
        if t in (11, 12, 13, 14, 15):
            fmt, size = {11: (">h", 2), 12: (">i", 4), 13: (">q", 8), 14: (">f", 4), 15: (">d", 8)}[t]
            return [struct.unpack(fmt, self.take(size))[0] for _ in range(self.u16())]
        if t == 16:
            return [self.utf() for _ in range(self.u16())]
        if t == 17:
            return [self.value() for _ in range(self.u16())]
        if t == 18:
            return {self.utf(): self.value() for _ in range(self.u16())}
        if t == 20:
            return self.take(self.i32()).decode("utf-8", "replace")
        raise DecodeError(f"unsupported type {t}")


def decode_message(frame: bytes) -> dict:
    if not frame:
        raise DecodeError("empty frame")
    head = frame[0]
    if not head & 0x80:
        raise DecodeError("not a binary SFS2X message")
    if head & 0x40:
        raise DecodeError("encrypted message")
    size_len = 4 if head & 0x08 else 2
    body = frame[1 + size_len:]
    if head & 0x20:
        body = zlib.decompress(body)
    obj = _Reader(body).value()
    if not isinstance(obj, dict):
        raise DecodeError("payload is not an SFSObject")
    return obj


def extension_event(msg: dict) -> tuple[str | None, dict]:
    """Return (command, params) for an extension response ({c, a, p:{c, r, p}})."""
    p = msg.get("p") or {}
    if isinstance(p, dict) and "c" in p:
        return p.get("c"), (p.get("p") or {})
    return None, p if isinstance(p, dict) else {}
