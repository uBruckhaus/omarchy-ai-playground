"""How large a picture says it is, read from its header before anything decodes it (a small,
highly compressed file can declare a huge canvas: a decompression bomb). Pictures from outside
(picked files, screenshots, video frames) are decoded only when this passes. PNG, JPEG, GIF and
WebP: the formats the Vision tab takes."""

from __future__ import annotations

import os
import struct

MAX_SIDE = 16384
MAX_PIXELS = 64 * 1024 * 1024          # width × height × frames
MAX_FILE = 64 * 1024 * 1024
HEAD = 1024 * 1024                     # bytes read to find the size (JPEG's SOF can come after big EXIF)


def _png(data: bytes):
    if data[12:16] != b"IHDR" or len(data) < 24:
        return None
    w, h = struct.unpack(">II", data[16:24])
    frames = 1
    at = data.find(b"acTL")
    if at > 0 and len(data) >= at + 8:
        frames = max(1, struct.unpack(">I", data[at + 4:at + 8])[0])
    return w, h, frames


def _jpeg(data: bytes):
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if length < 2:
            return None
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 > len(data):
                return None
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h, 1
        i += 2 + length
    return None


def _gif(data: bytes):
    if len(data) < 10:
        return None
    w, h = struct.unpack("<HH", data[6:10])
    return w, h, max(1, data.count(b"\x00\x2c"))           # image descriptors, roughly the frames


def _webp(data: bytes):
    if len(data) < 30:
        return None
    kind = data[12:16]
    if kind == b"VP8X":
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
        return w, h, max(1, data.count(b"ANMF"))
    if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF, 1
    if kind == b"VP8L" and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF), 1
    return None


def declared_size(path) -> tuple | None:
    """(width, height, frames) the file's header declares; None for anything else or malformed."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(HEAD)
    except OSError:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png(data)
    if data.startswith(b"\xff\xd8"):
        return _jpeg(data)
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return _gif(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp(data)
    return None


def acceptable(path) -> bool:
    """Small enough to decode: a known header, at most MAX_SIDE per side and MAX_PIXELS in all."""
    try:
        if os.path.getsize(path) > MAX_FILE:
            return False
    except OSError:
        return False
    size = declared_size(path)
    if size is None:
        return False
    w, h, frames = size
    return 0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE and w * h * frames <= MAX_PIXELS
