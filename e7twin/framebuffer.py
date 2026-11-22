"""Framebuffer RGB565 -> PNG (zlib + struct seulement) et mire synthétique animée.

Un framebuffer est un dict ``{width, height, format: "RGB565", pitch, address, data: bytes}``
(contrat de `Session.read_framebuffer()`). ``pitch`` = octets par ligne (>= width*2).
"""
from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)


def to_png(fb: dict) -> bytes:
    """RGB565 petit-boutiste -> PNG RGB 8 bits. Les lignes/pixels manquants dans ``data`` sont noirs."""
    w, h = int(fb["width"]), int(fb["height"])
    if fb.get("format", "RGB565") != "RGB565":
        raise ValueError(f"format non géré : {fb.get('format')!r}")
    pitch = int(fb.get("pitch") or w * 2)
    if pitch < w * 2:
        raise ValueError(f"pitch {pitch} < largeur*2 ({w * 2})")
    data = bytes(fb["data"])
    raw = bytearray()
    for y in range(h):
        row = data[y * pitch: y * pitch + w * 2].ljust(w * 2, b"\0")
        raw.append(0)  # filtre « None »
        for i in range(0, w * 2, 2):
            v = row[i] | (row[i + 1] << 8)
            r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
            raw += bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + _chunk(b"IEND", b""))


_BARS = (0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000)  # blanc jaune cyan vert magenta rouge bleu noir


def test_pattern(width: int, height: int, t: float) -> dict:
    """Mire : 8 barres de couleur verticales + une barre blanche horizontale qui descend avec ``t`` (2 s/tour)."""
    w, h = int(width), int(height)
    bar_h = max(1, h // 20)
    y0 = int((t % 2.0) / 2.0 * h)
    rows = []
    cols = b"".join(struct.pack("<H", _BARS[x * len(_BARS) // w]) for x in range(w))
    white = struct.pack("<H", 0xFFFF) * w
    for y in range(h):
        rows.append(white if (y - y0) % h < bar_h else cols)
    return {"width": w, "height": h, "format": "RGB565", "pitch": w * 2, "address": 0, "data": b"".join(rows)}


test_pattern.__test__ = False  # pour pytest si importé dans un module de test
