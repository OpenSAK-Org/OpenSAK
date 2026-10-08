"""
src/opensak/export/gpi.py — write Garmin POI files (.gpi).

GPI is Garmin's binary custom-POI database (header signature "GRMREC00"),
read by every Garmin outdoor and car device from its Garmin/POI folder.
Garmin does not document it; the layout written here follows the records
that Garmin's own POI Loader and GPSBabel produce:

    record 0      file header ("GRMREC00", timestamp, file name)
    record 1      "POI" header (code page)
    record 0x09   category: name, the POI blocks, the category icon
      record 0x08 block: bounding box + up to 128 POIs (a flat list of
                  leaf blocks — the points are split into quadrants until
                  every block is small enough)
        record 0x02  POI: position, name and optional sub-records
          0x03       proximity alert
          0x04       icon reference
          0x0a       description
          0x0b       address
          0x0c       phone number
      record 0x05 icon bitmap
    record 0xffff end of file

Every record is a little-endian int32 tag and an int32 size of what
follows. Tags with bit 0x80000 set carry a second int32 — the size of the
record's own data, which is followed by its sub-records.

Pure Python (only the icon loader uses Qt to decode the image), so the
export dialog and Lua macros share it.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

# Garmin time counts from 1989-12-31 00:00 UTC.
_GARMIN_EPOCH = 631065600
_POINTS_PER_BLOCK = 128
_HAS_SUBRECORDS = 0x80000

_TAG_HEADER = 0x0
_TAG_POI_HEADER = 0x1
_TAG_POI = 0x2
_TAG_ALERT = 0x3
_TAG_ICON_REF = 0x4
_TAG_ICON = 0x5
_TAG_BLOCK = 0x8
_TAG_CATEGORY = 0x9
_TAG_DESCRIPTION = 0xA
_TAG_ADDRESS = 0xB
_TAG_PHONE = 0xC
_TAG_END = 0xFFFF

_ADDRESS_STREET = 0x10    # address mask bit for the free-text address line

# The largest icon Garmin devices show; bigger images are scaled down.
MAX_ICON_SIZE = 24
# Magenta pixels are drawn transparent.
_TRANSPARENT = 0xFF00FF

# Code pages a GPI may declare. Windows-1252 is what POI Loader writes and
# what every device reads; UTF-8 is only used when a text needs it.
_CP1252 = 1252
_UTF8 = 65001


@dataclass
class GpiPoint:
    """One POI. Empty texts are left out of the file."""

    lat: float
    lon: float
    name: str
    description: str = ""
    address: str = ""
    phone: str = ""
    proximity_m: int = 0      # proximity alert radius in meters (0 = none)


def _semicircles(deg: float) -> int:
    value = int(round(deg * (2 ** 31) / 180.0))
    return max(-(2 ** 31), min(2 ** 31 - 1, value))


class _Encoder:
    def __init__(self, codepage: int):
        self.codepage = codepage
        self._codec = "cp1252" if codepage == _CP1252 else "utf-8"

    def encode(self, text: str) -> bytes:
        return text.encode(self._codec, errors="replace")

    def long_string(self, text: str) -> bytes:
        """Language-tagged string: size, "EN", length, bytes."""
        raw = self.encode(text)
        return struct.pack("<I", len(raw) + 4) + b"EN" + struct.pack("<H", len(raw)) + raw

    def short_string(self, text: str) -> bytes:
        raw = self.encode(text)
        return struct.pack("<H", len(raw)) + raw


def _record(tag: int, data: bytes) -> bytes:
    return struct.pack("<II", tag, len(data)) + data


def _record_with_subrecords(tag: int, data: bytes, subrecords: bytes = b"") -> bytes:
    return (struct.pack("<III", tag | _HAS_SUBRECORDS,
                        len(data) + len(subrecords), len(data))
            + data + subrecords)


def _choose_codepage(points: Sequence[GpiPoint], category: str) -> int:
    texts = [category]
    for p in points:
        texts += (p.name, p.description, p.address, p.phone)
    try:
        for text in texts:
            text.encode("cp1252")
    except UnicodeEncodeError:
        return _UTF8
    return _CP1252


def _split_blocks(points: list[GpiPoint]) -> list[list[GpiPoint]]:
    """Split *points* into quadrants until each holds at most
    _POINTS_PER_BLOCK points (or only points at the same position)."""
    if len(points) <= _POINTS_PER_BLOCK:
        return [points]
    lats = [p.lat for p in points]
    lons = [p.lon for p in points]
    if min(lats) >= max(lats) and min(lons) >= max(lons):
        return [points]
    center_lat = sum(lats) / len(points)
    center_lon = sum(lons) / len(points)
    quadrants: list[list[GpiPoint]] = [[], [], [], []]
    for p in points:
        quadrants[(p.lat < center_lat) * 2 + (p.lon < center_lon)].append(p)
    if max(len(q) for q in quadrants) == len(points):
        return [points]   # cannot be split further (e.g. a line of points)
    blocks: list[list[GpiPoint]] = []
    for q in quadrants:
        if q:
            blocks += _split_blocks(q)
    return blocks


def _alert_record(proximity_m: int) -> bytes:
    data = struct.pack(
        "<HHIBBBB",
        min(proximity_m, 0xFFFF),   # proximity in meters
        0,                          # speed in 1/100 m/s (not used)
        0x100100, 1, 1,
        4,                          # 4 = proximity alert
        0x10,
    )
    return _record(_TAG_ALERT, data)


def _poi_record(p: GpiPoint, enc: _Encoder, alerts: bool, with_icon: bool) -> bytes:
    data = (struct.pack("<iiHB", _semicircles(p.lat), _semicircles(p.lon), 1, int(alerts))
            + enc.long_string(p.name))
    sub = b""
    if p.proximity_m > 0:
        sub += _alert_record(p.proximity_m)
    if with_icon:
        sub += _record(_TAG_ICON_REF, struct.pack("<H", 0))
    if p.description:
        sub += _record(_TAG_DESCRIPTION, enc.long_string(p.description))
    if p.address:
        sub += _record_with_subrecords(
            _TAG_ADDRESS, struct.pack("<H", _ADDRESS_STREET),
            enc.long_string(p.address),
        )
    if p.phone:
        sub += _record_with_subrecords(
            _TAG_PHONE, struct.pack("<H", 1), enc.short_string(p.phone),
        )
    return _record_with_subrecords(_TAG_POI, data, sub)


def _block_record(points: list[GpiPoint], enc: _Encoder, with_icon: bool) -> bytes:
    points = sorted(points, key=lambda p: p.name)
    alerts = any(p.proximity_m > 0 for p in points)
    data = struct.pack(
        "<iiiiIHB",
        _semicircles(max(p.lat for p in points)),
        _semicircles(max(p.lon for p in points)),
        _semicircles(min(p.lat for p in points)),
        _semicircles(min(p.lon for p in points)),
        0, 1, int(alerts),
    )
    pois = b"".join(_poi_record(p, enc, alerts, with_icon) for p in points)
    return _record_with_subrecords(_TAG_BLOCK, data, pois)


def build_gpi(
    points: Sequence[GpiPoint],
    category: str,
    icon: Optional[bytes] = None,
    timestamp: Optional[float] = None,
) -> bytes:
    """Return the GPI file content for *points* in one *category*.

    *icon* is a bitmap from load_gpi_icon() shown for every point (None =
    the device's default POI icon). *timestamp* (Unix time, default now)
    is the file's creation date — devices use it to tell versions apart.
    """
    if not category.strip():
        raise ValueError("a GPI category must not be empty")
    points = list(points)
    enc = _Encoder(_choose_codepage(points, category))
    if timestamp is None:
        timestamp = time.time()
    created = max(0, int(timestamp) - _GARMIN_EPOCH)

    out = bytearray()
    out += _record(_TAG_HEADER, b"GRMREC00" + struct.pack("<IHH", created, 0, 6) + b"my.gpi")
    out += _record(_TAG_POI_HEADER, b"POI\0\0\0" + b"00" + struct.pack("<HH", enc.codepage, 0))

    with_icon = bool(icon)
    blocks = b"".join(
        _block_record(block, enc, with_icon) for block in _split_blocks(points) if block
    )
    icon_record = _record(_TAG_ICON, icon) if icon else b""
    out += _record_with_subrecords(
        _TAG_CATEGORY, enc.long_string(category) + blocks, icon_record,
    )
    out += _record(_TAG_END, b"")
    return bytes(out)


def write_gpi(
    path: Path,
    points: Sequence[GpiPoint],
    category: str,
    icon: Optional[bytes] = None,
) -> None:
    """Write *points* to the GPI file *path* (see build_gpi)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_gpi(points, category, icon))


def load_gpi_icon(path: Path) -> bytes:
    """Load an image (BMP, PNG, …) as a GPI icon bitmap.

    Images larger than MAX_ICON_SIZE are scaled down. Transparent pixels and
    magenta (#FF00FF) are drawn transparent on the device. Raises ValueError
    if the file is no readable image.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    image = QImage(str(path))
    if image.isNull():
        raise ValueError(f"{path} is not a readable image")
    if image.width() > MAX_ICON_SIZE or image.height() > MAX_ICON_SIZE:
        image = image.scaled(
            MAX_ICON_SIZE, MAX_ICON_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = image.width(), image.height()

    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            argb = image.pixel(x, y)
            rgb = argb & 0xFFFFFF if (argb >> 24) >= 0x80 else _TRANSPARENT
            pixels += struct.pack("<I", rgb)    # B, G, R, 0
    line_size = width * 4
    image_size = line_size * height
    header = struct.pack(
        "<HHHHHHIIIIII",
        0,              # icon index
        height, width, line_size,
        32,             # bits per pixel
        0,
        image_size,
        0x2C,
        0,              # palette size (none for 32 bpp)
        _TRANSPARENT,   # transparent colour
        1,              # transparency on
        image_size + 0x2C,
    )
    return header + bytes(pixels)
