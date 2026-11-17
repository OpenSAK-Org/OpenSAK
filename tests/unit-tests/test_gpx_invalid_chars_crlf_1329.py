# tests/unit-tests/test_gpx_invalid_chars_crlf_1329.py — #1329: the GPX/GGZ
# export wrote text as stored in the database:
#   * control characters XML 1.0 doesn't allow (e.g. \x02 in Adventure Lab
#     descriptions imported from GSAK) made the whole file invalid XML;
#   * text with CR LF line breaks (GSAK, geocaching.com) came out as
#     CR CR LF in a text-mode file on Windows, which parsers read as two
#     line breaks, so every multi-line log/note/description got empty lines.

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensak.gps.garmin import generate_ggz, generate_gpx, generate_loc, write_export, write_gpx
from opensak.lang import load_language

GPX = "{http://www.topografix.com/GPX/1/0}"
GS = "{http://www.groundspeak.com/cache/1/0/1}"
GSAK = "{http://www.gsak.net/xmlv1/6}"

BAD = "\x02"


@pytest.fixture(autouse=True)
def _lang():
    load_language("en")


def _log(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        log_id="1393440501", log_date=datetime(2026, 11, 11, 12), log_type="Found it",
        finder="Finder", finder_id=123, text=text,
    )


def _cache(text: str = "", **kw) -> SimpleNamespace:
    """A cache with *text* in every text field the export writes."""
    values = dict(
        id=1, gc_code="GCBWND7", gc_cache_id=10572559, name=f"Name{text}",
        cache_type="Multi-cache", latitude=47.19, longitude=8.52,
        difficulty=1.0, terrain=1.5, placed_by=f"Owner{text}", owner_name="Owner",
        owner_id=6179840, available=True, archived=False, country="Switzerland",
        state="Zug", encoded_hints=f"Hint{text}", hidden_date=None,
        logs=[_log(f"Log{text}")],
        user_note=SimpleNamespace(note=f"Note{text}", is_corrected=False,
                                  corrected_lat=None, corrected_lon=None, updated_at=None),
        container="Other", short_description=f"Short{text}", short_desc_html=False,
        long_description=f"Long{text}", long_desc_html=False, attributes=[],
        waypoints=[SimpleNamespace(
            prefix="P0", wp_type="Parking Area", name=f"Parking{text}",
            description=f"Desc{text}", comment=f"Comment{text}",
            latitude=47.1, longitude=8.5, url=None, wp_date=None, wp_code="P0BWND7",
            wp_flag=False, created_by_user=False,
        )],
        trackables=[SimpleNamespace(tracking_code="7219256", ref="TB88415", name=f"TB{text}")],
        found=False, elevation=None,
        user_flag=False, locked=False, dnf=False, dnf_date=None, watch=False,
        user_data_1=f"User{text}", user_data_2=None, user_data_3=None, user_data_4=None,
        first_to_find=False, county=None, user_sort=None, last_gpx_update=None,
        premium_only=False, favorite_points=None, gc_note=None, guid=None,
    )
    values.update(kw)
    return SimpleNamespace(**values)


def _parse(data: str | bytes) -> ET.Element:
    """Parse the export like a strict reader (c:geo): any invalid character
    raises ParseError."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return ET.fromstring(data)


def _texts(root: ET.Element) -> list[str]:
    return [t for el in root.iter() for t in (el.text, *el.attrib.values()) if t]


# ── F1: characters XML 1.0 doesn't allow ─────────────────────────────────────

class TestInvalidCharacters:
    @pytest.mark.parametrize("char", ["\x00", "\x02", "\x08", "\x0b", "\x0c", "\x1f", "￾"])
    def test_gpx_is_well_formed(self, char):
        root = _parse(generate_gpx([_cache(char)]))
        assert not any(char in t for t in _texts(root))

    def test_text_is_kept_without_the_character(self):
        root = _parse(generate_gpx([_cache(BAD)]))
        cache_wpt, child_wpt = root.findall(f"{GPX}wpt")
        cache = cache_wpt.find(f"{GS}cache")
        assert cache is not None
        assert cache.findtext(f"{GS}name") == "Name"
        assert cache.findtext(f"{GS}placed_by") == "Owner"
        assert cache.findtext(f"{GS}short_description") == "Short"
        assert cache.findtext(f"{GS}long_description") == "Long"
        assert cache.findtext(f"{GS}encoded_hints") == "Hint"
        assert cache.findtext(f".//{GS}log/{GS}text") == "Log"
        assert cache.findtext(f".//{GS}travelbug/{GS}name") == "TB"
        assert cache_wpt.findtext(f".//{GSAK}UserData") == "User"
        assert cache_wpt.findtext(f".//{GSAK}UserNote") == "Note"
        assert child_wpt.findtext(f"{GPX}cmt") == "Comment"

    def test_valid_whitespace_is_kept(self):
        root = _parse(generate_gpx([_cache(long_description="a\tb\nc")]))
        assert root.findtext(f".//{GS}long_description") == "a\tb\nc"

    def test_user_note_log_is_well_formed(self):
        from opensak.export.gpx_content import GpxContent

        root = _parse(generate_gpx([_cache(BAD)], content=GpxContent(user_note_log=True)))
        texts = [t.text for t in root.iter(f"{GS}text")]
        assert texts == ["Note", "Log"]

    def test_ggz_is_well_formed_and_offsets_match(self):
        caches = [_cache(BAD), _cache(BAD, id=2, gc_code="GCBWND8", waypoints=[])]
        with zipfile.ZipFile(io.BytesIO(generate_ggz(caches, filename="g"))) as zf:
            gpx = zf.read("data/g.gpx")
            index = zf.read("index/com/garmin/geocaches/v0/index.xml")
        _parse(gpx)
        _parse(index)
        assert BAD.encode() not in gpx + index
        # The index points at each cache's <wpt> in the data file.
        positions = [int(p.split(b"<")[0]) for p in index.split(b"<file_pos>")[1:]]
        assert positions and all(gpx[p:p + 4] == b"<wpt" for p in positions)

    def test_loc_is_well_formed(self):
        root = _parse(generate_loc([_cache(BAD)]))
        assert BAD not in "".join(_texts(root))


# ── F2: CR LF in the text ────────────────────────────────────────────────────

def _crlf_cache() -> SimpleNamespace:
    return _cache(
        logs=[_log("line 1\r\nline 2")],
        user_note=SimpleNamespace(note="note 1\r\nnote 2", is_corrected=False,
                                  corrected_lat=None, corrected_lon=None, updated_at=None),
        long_description="desc 1\r\ndesc 2\rdesc 3",
    )


class TestLineBreaks:
    def test_windows_text_file_has_no_cr_cr_lf(self, tmp_path):
        # A text-mode file on Windows translates LF to CR LF; newline="\r\n"
        # does the same on every platform.
        out = tmp_path / "x.gpx"
        with open(out, "w", encoding="utf-8", newline="\r\n") as f:
            write_gpx(f, [_crlf_cache()])
        data = out.read_bytes()
        assert b"\r\r\n" not in data
        root = _parse(data)
        assert root.findtext(f".//{GS}log/{GS}text") == "line 1\nline 2"
        assert root.findtext(f".//{GSAK}UserNote") == "note 1\nnote 2"
        assert root.findtext(f".//{GS}long_description") == "desc 1\ndesc 2\ndesc 3"

    def test_export_file_has_no_cr_cr_lf(self, tmp_path):
        # The file the export dialog and Send to GPS write, with the
        # platform's newlines.
        out = tmp_path / "x.gpx"
        write_export(out, lambda f: write_gpx(f, [_crlf_cache()]))
        data = out.read_bytes()
        assert b"\r\r\n" not in data
        assert _parse(data).findtext(f".//{GS}log/{GS}text") == "line 1\nline 2"

    def test_gpx_text_has_only_lf(self):
        assert "\r" not in generate_gpx([_crlf_cache()])

    def test_ggz_has_only_lf(self):
        with zipfile.ZipFile(io.BytesIO(generate_ggz([_crlf_cache()], filename="g"))) as zf:
            assert b"\r" not in zf.read("data/g.gpx")

    def test_macro_export_file_has_no_cr_cr_lf(self, tmp_path):
        # Lua macros write through safe_write_stream() in text mode.
        from opensak.macro.permissions import FolderPermission, safe_write_stream

        out = tmp_path / "x.gpx"
        safe_write_stream(
            out, lambda f: write_gpx(f, [_crlf_cache()]),
            permissions=[FolderPermission(path=str(tmp_path), write=True)], binary=False,
        )
        assert b"\r\r\n" not in Path(out).read_bytes()
