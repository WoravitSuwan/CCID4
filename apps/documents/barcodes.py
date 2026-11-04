"""Envelope barcodes: a short Code 128 number per summons, drawn as a PNG without image libraries.

The number is 9 + the summons id (8 digits, e.g. 90000123): all digits, so Code 128 packs it
into a short, easy-to-scan symbol. Scanning it on the scan page finds the summons.
"""
from __future__ import annotations

import re
import struct
import zlib

PREFIX = "9"
CODE_RE = re.compile(r"^9(\d{7})$")


def summons_code(summons) -> str:
    return f"{PREFIX}{summons.pk:07d}"


def summons_id_from_code(code: str) -> int | None:
    m = CODE_RE.match(re.sub(r"\s", "", code or ""))
    return int(m.group(1)) if m else None


def code128_modules(value: str) -> str:
    """'1'/'0' per module (bar/space) including start, checksum and stop symbols."""
    from barcode import Code128

    return Code128(value).build()[0]


def code128_png(value: str, module_px: int = 4, height_px: int = 110, quiet_modules: int = 10) -> bytes:
    modules = "0" * quiet_modules + code128_modules(value) + "0" * quiet_modules
    row = b"".join((b"\x00" if m == "1" else b"\xff") * module_px for m in modules)
    raw = b"".join(b"\x00" + row for _ in range(height_px))  # filter byte 0 per scanline
    width = len(modules) * module_px

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height_px, 8, 0, 0, 0, 0)  # 8-bit greyscale
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
