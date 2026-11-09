# tests/unit-tests/test_gpi.py — the Garmin POI (.gpi) writer.
#
# The files are read back with a small reader that checks every record
# size, so a wrong size anywhere in the nested records fails the test.

import struct

import pytest

from opensak.export.gpi import MAX_ICON_SIZE, GpiPoint, build_gpi, load_gpi_icon


def _records(data: bytes, start: int, end: int):
    """Yield (tag, main data, sub-record bytes) of the records in data[start:end]."""
    pos = start
    while pos < end:
        tag, size = struct.unpack_from("<II", data, pos)
        pos += 8
        if tag & 0x80000:
            (main_size,) = struct.unpack_from("<I", data, pos)
            pos += 4
            assert main_size <= size
            body = data[pos:pos + size]
            yield tag & 0xFFFF, body[:main_size], body[main_size:]
        else:
            yield tag, data[pos:pos + size], b""
        pos += size
        assert pos <= end, "record runs past its parent"
    assert pos == end


def _long_string(data: bytes, pos: int, codec: str) -> tuple[str, int]:
    (size,) = struct.unpack_from("<I", data, pos)
    assert data[pos + 4:pos + 6] == b"EN"
    (length,) = struct.unpack_from("<H", data, pos + 6)
    assert size == length + 4
    return data[pos + 8:pos + 8 + length].decode(codec), pos + 8 + length


def read_gpi(data: bytes) -> dict:
    """Parse *data*; returns the header fields, category, blocks and POIs."""
    records = list(_records(data, 0, len(data)))
    assert [r[0] for r in records] == [0x0, 0x1, 0x9, 0xFFFF]
    header, poi_header, category, _end = records
    assert header[1][:8] == b"GRMREC00"
    assert poi_header[1][:3] == b"POI"
    (codepage,) = struct.unpack_from("<H", poi_header[1], 8)
    codec = {1252: "cp1252", 65001: "utf-8"}[codepage]

    name, pos = _long_string(category[1], 0, codec)
    result = {"codepage": codepage, "category": name, "blocks": [], "pois": [], "icon": None}
    for tag, main, _ in _records(category[1], pos, len(category[1])):
        assert tag == 0x8 and len(main) == 23
    for tag, main, _ in _records(category[2], 0, len(category[2])):
        assert tag == 0x5
        result["icon"] = main

    for _, block_main, block_sub in _records(category[1], pos, len(category[1])):
        result["blocks"].append(len(list(_records(block_sub, 0, len(block_sub)))))
        for tag, main, sub in _records(block_sub, 0, len(block_sub)):
            assert tag == 0x2
            lat, lon = struct.unpack_from("<ii", main, 0)
            poi = {
                "lat": lat * 180 / 2 ** 31, "lon": lon * 180 / 2 ** 31,
                "name": _long_string(main, 11, codec)[0],
            }
            for stag, smain, ssub in _records(sub, 0, len(sub)):
                if stag == 0x3:
                    poi["proximity"] = struct.unpack_from("<H", smain)[0]
                elif stag == 0x4:
                    poi["icon"] = True
                elif stag == 0xA:
                    poi["description"] = _long_string(smain, 0, codec)[0]
                elif stag == 0xB:
                    assert smain == struct.pack("<H", 0x10)
                    poi["address"] = _long_string(ssub, 0, codec)[0]
                elif stag == 0xC:
                    (length,) = struct.unpack_from("<H", ssub)
                    poi["phone"] = ssub[2:2 + length].decode(codec)
            result["pois"].append(poi)
    return result


def test_writes_points_with_all_texts():
    data = build_gpi([
        GpiPoint(47.37, 8.54, "Zürich", "Tradi by me (1.5/2)", phone="under the stone"),
        GpiPoint(-33.9, 151.2, "Sydney", address="Some street", proximity_m=50),
    ], "My caches")
    gpi = read_gpi(data)
    assert gpi["category"] == "My caches"
    assert gpi["codepage"] == 1252
    zurich, sydney = sorted(gpi["pois"], key=lambda p: p["name"], reverse=True)
    assert zurich["name"] == "Zürich"
    assert zurich["lat"] == pytest.approx(47.37, abs=1e-6)
    assert zurich["lon"] == pytest.approx(8.54, abs=1e-6)
    assert zurich["description"] == "Tradi by me (1.5/2)"
    assert zurich["phone"] == "under the stone"
    assert "address" not in zurich and "proximity" not in zurich
    assert sydney["lat"] == pytest.approx(-33.9, abs=1e-6)
    assert sydney["address"] == "Some street"
    assert sydney["proximity"] == 50
    assert gpi["icon"] is None and "icon" not in sydney


def test_uses_utf8_only_when_needed():
    gpi = read_gpi(build_gpi([GpiPoint(50.0, 14.4, "Žluťoučký kůň")], "Praha"))
    assert gpi["codepage"] == 65001
    assert gpi["pois"][0]["name"] == "Žluťoučký kůň"


def test_many_points_are_split_into_blocks():
    points = [GpiPoint(47 + i / 1000, 8 + (i % 37) / 100, f"P{i:04d}") for i in range(1000)]
    gpi = read_gpi(build_gpi(points, "Many"))
    assert len(gpi["pois"]) == 1000
    assert {p["name"] for p in gpi["pois"]} == {p.name for p in points}
    assert len(gpi["blocks"]) > 1
    assert max(gpi["blocks"]) <= 128


def test_points_at_one_position_stay_in_one_block():
    gpi = read_gpi(build_gpi([GpiPoint(47.0, 8.0, f"P{i}") for i in range(200)], "Same"))
    assert gpi["blocks"] == [200]


def test_timestamp_is_garmin_time():
    data = build_gpi([GpiPoint(1, 2, "x")], "c", timestamp=631065600 + 1234)
    assert struct.unpack_from("<I", data, 16)[0] == 1234


def test_empty_category_is_rejected():
    with pytest.raises(ValueError):
        build_gpi([GpiPoint(1, 2, "x")], "  ")


def test_icon_is_scaled_and_referenced(tmp_path):
    from PySide6.QtGui import QColor, QImage

    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))          # transparent
    for x in range(32):
        for y in range(16, 32):
            image.setPixelColor(x, y, QColor(10, 20, 30))
    image.save(str(tmp_path / "icon.png"))

    icon = load_gpi_icon(tmp_path / "icon.png")
    height, width, line_size, bpp = struct.unpack_from("<HHHH", icon, 2)
    assert (width, height, bpp) == (MAX_ICON_SIZE, MAX_ICON_SIZE, 32)
    assert line_size == width * 4
    pixels = icon[36:]
    assert len(pixels) == width * height * 4
    assert pixels[:4] == bytes([0xFF, 0x00, 0xFF, 0])     # transparent → magenta
    assert pixels[-4:] == bytes([30, 20, 10, 0])          # B, G, R

    gpi = read_gpi(build_gpi([GpiPoint(1, 2, "x")], "c", icon))
    assert gpi["icon"] == icon
    assert gpi["pois"][0]["icon"] is True


def test_unreadable_icon_raises(tmp_path):
    (tmp_path / "x.bmp").write_text("no image")
    with pytest.raises(ValueError):
        load_gpi_icon(tmp_path / "x.bmp")
