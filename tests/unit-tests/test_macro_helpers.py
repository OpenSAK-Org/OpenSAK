"""tests/unit-tests/test_macro_helpers.py — opensak.coords, .re, .text, .date,
version() and sleep(): the pure helpers of the Lua macro API."""

import threading
import time
from datetime import datetime, timezone

import pytest

from opensak import __version__
from opensak.geodesy import to_ch1903, to_utm
from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.macro import helpers
from opensak.macro.permissions import FolderPermission


class _Host:
    def __init__(self, approval=FolderApproval.DENY):
        self.approval = approval
        self.approvals: list[tuple[str, bool]] = []

    def approve_folder(self, target, folder, write, macro=None):
        self.approvals.append((str(folder), write))
        return self.approval

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def end_macro(self):
        pass


def _run(source, host=None, **kwargs):
    out: list[str] = []
    MacroRuntime(host or _Host(), output=out.append, **kwargs).run(  # type: ignore[arg-type]
        source)
    return out


# ── Geodesy ──────────────────────────────────────────────────────────────────

def test_utm_matches_reference():
    # Zürich; reference from Krüger's series to order n^4 (mm accuracy)
    zone, band, easting, northing = to_utm(47.36872, 8.54093)
    assert (zone, band) == (32, "T")
    assert easting == pytest.approx(465_339.80, abs=0.05)
    assert northing == pytest.approx(5_246_242.11, abs=0.05)


def test_utm_southern_hemisphere_and_norway_exception():
    zone, band, _, northing = to_utm(-33.8688, 151.2093)  # Sydney
    assert (zone, band) == (56, "H")
    assert northing == pytest.approx(6_250_948.35, abs=0.05)
    assert to_utm(60.39, 5.32)[0] == 32  # Bergen lies in 31 by longitude


def test_utm_rejects_polar_latitudes():
    with pytest.raises(ValueError):
        to_utm(85.0, 0.0)


def test_ch1903_matches_swisstopo_example():
    # swisstopo's worked example: 46° 2' 38.87", 8° 43' 49.79"
    lat = 46 + 2 / 60 + 38.87 / 3600
    lon = 8 + 43 / 60 + 49.79 / 3600
    east, north = to_ch1903(lat, lon)
    assert east == pytest.approx(700_000, abs=1)
    assert north == pytest.approx(100_000, abs=1)
    assert to_ch1903(lat, lon, lv95=True) == pytest.approx((2_700_000, 1_100_000), abs=1)


# ── opensak.coords ───────────────────────────────────────────────────────────

def test_coords_parse_returns_point_and_format_or_nil():
    out = _run('print(opensak.coords.parse("N47 22.123 E008 32.456"))\n'
               'print(opensak.coords.parse("nonsense"))')
    lat, lon, fmt = out[0].split("\t")
    assert float(lat) == pytest.approx(47 + 22.123 / 60)
    assert float(lon) == pytest.approx(8 + 32.456 / 60)
    assert fmt == "latlon"
    assert out[1] == "nil"


@pytest.mark.parametrize("text, fmt", [
    ("32T E 465340 N 5246242", "utm"),
    ("32T MT 65340 46241", "mgrs"),
    ("683259 / 247015", "ch1903"),
    ("2683259 / 1247015", "ch1903"),
    ("R 3465403 H 5247900", "gk"),
    ("8FVC9G9R+F9M", "olc"),
    ("u0qj6r5qf", "geohash"),
])
def test_coords_parse_detects_format(text, fmt):
    out = _run(f'print(opensak.coords.parse("{text}"))')
    lat, lon, detected = out[0].split("\t")
    assert detected == fmt
    assert float(lat) == pytest.approx(47 + 22.123 / 60, abs=2e-5)
    assert float(lon) == pytest.approx(8 + 32.456 / 60, abs=2e-5)


def test_coords_parse_with_explicit_format():
    # Numeric British grid positions are only read when asked for.
    out = _run('print(opensak.coords.parse("530268 179642", "osgb"))\n'
               'print(opensak.coords.parse("N47 22.123 E008 32.456", "utm"))')
    lat, lon, fmt = out[0].split("\t")
    assert float(lat) == pytest.approx(51.5007, abs=1e-4)
    assert float(lon) == pytest.approx(-0.1246, abs=1e-4)
    assert fmt == "osgb"
    assert out[1] == "nil"
    with pytest.raises(MacroError, match="unknown format"):
        _run('opensak.coords.parse("x", "nope")')


def test_coords_functions_take_any_format():
    out = _run('print(opensak.coords.distance("32T MT 65340 46241", "N47 22.123 E008 32.456"))')
    assert float(out[0]) < 0.002


@pytest.mark.parametrize("fmt, expected", [
    ("dmm", "N47 22.123  E008 32.456"),
    ("dd", "47.36872, 8.54093"),
    ("utm", "32T E 465340 N 5246242"),
    ("ch1903", "683259 / 247015"),
    ("ch1903+", "2683259 / 1247015"),
    ("lv95", "2683259 / 1247015"),
    ("mgrs", "32T MT 65340 46241"),
    ("gk", "R 3465403 H 5247900"),
    ("olc", "8FVC9G9R+F9M"),
    ("geohash", "u0qj6r5qf"),
    ("maidenhead", "JN47gi48"),
])
def test_coords_format(fmt, expected):
    out = _run(f'print(opensak.coords.format("N47 22.123 E008 32.456", "{fmt}"))\n'
               f'print(opensak.coords.format(47 + 22.123 / 60, 8 + 32.456 / 60, "{fmt}"))')
    assert out == [expected, expected]


def test_coords_format_rejects_unknown_format():
    with pytest.raises(MacroError, match="unknown format"):
        _run('opensak.coords.format(47, 8, "nope")')
    with pytest.raises(MacroError, match="outside the British National Grid"):
        _run('opensak.coords.format(47, 8, "osgb")')


def test_distance_bearing_project_midpoint_agree():
    out = _run(
        "local a, b = 'N47 22.123 E008 32.456', 'N47 23.000 E008 34.000'\n"
        "local d = opensak.coords.distance(a, b)\n"
        "local brg = opensak.coords.bearing(a, b)\n"
        "local lat, lon = opensak.coords.project(a, brg, d)\n"
        "print(opensak.coords.distance(lat, lon, b))\n"
        "local mlat, mlon = opensak.coords.midpoint(a, b)\n"
        "print(opensak.coords.distance(a, mlat, mlon) - d / 2)\n"
        "print(d, brg)"
    )
    assert float(out[0]) < 0.001  # projected point lands on b (< 1 m)
    assert abs(float(out[1])) < 0.001
    d, brg = map(float, out[2].split("\t"))
    assert d == pytest.approx(2.53, abs=0.01)
    assert 40 < brg < 60


def test_project_wraps_longitude():
    out = _run("print(opensak.coords.project(0, 179.9, 90, 50))")
    assert float(out[0].split("\t")[1]) < -179


def test_coords_functions_report_missing_points():
    with pytest.raises(MacroError, match="expects 2 point"):
        _run("opensak.coords.distance(47, 8)")
    with pytest.raises(MacroError, match="bearing in degrees"):
        _run("opensak.coords.project(47, 8, 90)")


def test_inside_with_table_of_points():
    out = _run(
        "local area = { 'N47 20 E008 30', {47.4167, 8.5}, {lat = 47.4167, lon = 8.6667},"
        " {47.3333, 8.6667} }\n"
        "print(opensak.coords.inside(47.37, 8.54, area))\n"
        "print(opensak.coords.inside('N46 00.000 E008 00.000', area))"
    )
    assert out == ["true", "false"]


def test_inside_reads_polygon_file_with_permission(tmp_path):
    (tmp_path / "area.txt").write_text(
        "N47 20 E008 30\nN47 25 E008 30\nN47 25 E008 40\nN47 20 E008 40\n",
        encoding="utf-8")
    perms = [FolderPermission(str(tmp_path), read=True, write=False)]
    path = (tmp_path / "area.txt").as_posix()
    out = _run(f'print(opensak.coords.inside(47.37, 8.54, "{path}"))',
               folder_permissions=perms)
    assert out == ["true"]
    # An unlisted folder is put to the user like for read_csv()
    host = _Host(FolderApproval.DENY)
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.coords.inside(47.37, 8.54, "{path}")', host=host,
             folder_permissions=[])
    assert host.approvals == [(str(tmp_path), False)]
    host = _Host(FolderApproval.ONCE)
    out = _run(f'print(opensak.coords.inside(47.37, 8.54, "{path}"))', host=host,
               folder_permissions=[])
    assert out == ["true"]


def test_inside_needs_three_points():
    with pytest.raises(MacroError, match="at least 3 points"):
        _run("opensak.coords.inside(47, 8, { {47, 8}, {48, 9} })")


def test_location_is_nil_without_boundary_data(monkeypatch, tmp_path):
    monkeypatch.setattr("opensak.geo.store.default_data_dir", lambda: tmp_path)
    assert _run("print(opensak.coords.location(47.37, 8.54))") == ["nil"]


# ── opensak.re ───────────────────────────────────────────────────────────────

def test_re_find_returns_match_and_captures():
    out = _run(r'print(opensak.re.find("Final: N47 E008", [[N(\d+) E(\d+)( W)?]]))'
               "\n"
               'print(opensak.re.find("abc", "x"))')
    assert out == ["N47 E008\t47\t008\tnil", "nil"]


def test_re_match_findall_split():
    out = _run(
        r'print(opensak.re.match("Bonus cache", [[(?i)^bonus]]))'
        "\n"
        r'print(opensak.re.match("Bonus cache", [[^cache]]))'
        "\n"
        r'print(table.concat(opensak.re.findall("A=3, B=12", [[\d+]]), ","))'
        "\n"
        r'local pairs_ = opensak.re.findall("A=3, B=12", [[(\w)=(\d+)]])'
        "\n"
        'print(pairs_[2][1], pairs_[2][2])\n'
        r'print(table.concat(opensak.re.split("a, b;c", [[[,;]\s*]]), "|"))'
    )
    assert out == ["true", "false", "3,12", "B\t12", "a|b|c"]


def test_re_replace_with_string_count_and_function():
    out = _run(
        r'print(opensak.re.replace("A=3 B=12", [[(\w)=(\d+)]], [[\2:\1]]))'
        "\n"
        r'print(opensak.re.replace("1 2 3", [[\d]], "x", 2))'
        "\n"
        r'print(opensak.re.replace("A=3 B=12", [[\d+]], function(n) return n * 2 end))'
        "\n"
        r'print(opensak.re.replace("a b", [[\w]], function(c) if c == "a" then return "A" end end))'
    )
    assert out == ["3:A 12:B\t2", "x x 3\t2", "A=6 B=24\t2", "A b\t2"]  # counts like gsub


def test_re_invalid_pattern_is_a_macro_error():
    with pytest.raises(MacroError, match="invalid pattern"):
        _run('opensak.re.match("x", "(")')


def test_re_catastrophic_pattern_times_out(monkeypatch):
    monkeypatch.setattr(helpers, "REGEX_TIMEOUT_S", 0.2)
    start = time.monotonic()
    with pytest.raises(MacroError, match="longer than"):
        _run('opensak.re.match(string.rep("x", 5000), [[(x+x+)+y]])')
    assert time.monotonic() - start < 5


# ── opensak.text ─────────────────────────────────────────────────────────────

def test_html_to_text():
    html = ("<html><head><style>p{}</style></head><body>"
            "<p>Stage&nbsp;1:<br>N47   22.123</p><script>x()</script>"
            "<ul><li>one</li><li>two &amp; three</li></ul></body></html>")
    assert helpers.html_to_text(html) == "Stage 1:\nN47 22.123\n\none\n\ntwo & three"


def test_text_helpers_from_lua():
    out = _run(
        'print(opensak.text.rot13("haqre gur fgbar [Ubhfr]"))\n'
        "print(opensak.text.digit_sum(1987), opensak.text.digit_sum('N47 22.123'))\n"
        'print(opensak.text.word_value("Geocache"), opensak.text.word_value("Ärger!"))\n'
        'print(opensak.text.normalize_name("CH: Zürich / Nord"))'
    )
    assert out == [
        "under the stone [Ubhfr]",
        "25\t21",
        "47\t" + str(1 + 18 + 7 + 5 + 18),
        "CH_ Zürich _ Nord",
    ]


@pytest.mark.parametrize("name, expected", [
    ('a\\b/c:d*e?f"g<h>i|j', "a_b_c_d_e_f_g_h_i_j"),
    ("  ..hidden. ", "hidden"),
    ("CON", "_CON"),
    ("nul.txt", "_nul.txt"),
    ("a\tb\n  c", "a b c"),
    ("", "_"),
    ("x" * 300, "x" * helpers.MAX_NAME_LENGTH),
])
def test_normalize_name(name, expected):
    assert helpers.normalize_name(name) == expected


# ── opensak.date ─────────────────────────────────────────────────────────────

def test_date_parse_and_format_round_trip():
    local_midnight = int(datetime(2026, 10, 6).timestamp())
    out = _run(
        'print(opensak.date.parse("2026-10-06"))\n'
        'print(opensak.date.parse("06.10.2026"))\n'
        'print(opensak.date.parse("10/06/2026", "%m/%d/%Y"))\n'
        'print(opensak.date.parse("2026-10-06T12:00:00Z"))\n'
        'print(opensak.date.parse("not a date"))\n'
        'print(opensak.date.format(opensak.date.parse("2026-10-06 14:30")))\n'
        'print(opensak.date.format(opensak.date.parse("2026-10-06 14:30"), "%d.%m.%Y %H:%M"))'
    )
    noon_utc = int(datetime(2026, 10, 6, 12, tzinfo=timezone.utc).timestamp())
    assert out == [str(local_midnight)] * 3 + [
        str(noon_utc), "nil", "2026-10-06", "06.10.2026 14:30",
    ]


def test_date_format_matches_os_date():
    out = _run('local t = os.time()\n'
               'print(opensak.date.format(t, "%Y-%m-%d %H:%M:%S") == os.date("%Y-%m-%d %H:%M:%S", t))')
    assert out == ["true"]


def test_date_format_rejects_non_numbers():
    with pytest.raises(MacroError, match="expects a time"):
        _run('opensak.date.format("2026-10-06")')


# ── version / sleep ──────────────────────────────────────────────────────────

def test_version():
    assert _run("print(opensak.version())") == [__version__]


def test_sleep_waits_and_is_capped(monkeypatch):
    import opensak.macro.runtime as runtime

    start = time.monotonic()
    _run("opensak.sleep(50)")
    assert time.monotonic() - start >= 0.04

    monkeypatch.setattr(runtime, "MAX_SLEEP_MS", 20)
    start = time.monotonic()
    _run("opensak.sleep(100000)")
    assert time.monotonic() - start < 1


def test_sleep_budget(monkeypatch):
    import opensak.macro.runtime as runtime

    monkeypatch.setattr(runtime, "SLEEP_BUDGET_S", 0.05)
    with pytest.raises(MacroError, match="at most"):
        _run("for i = 1, 10 do opensak.sleep(10) end")


def test_cancel_interrupts_sleep():
    rt = MacroRuntime(_Host(), output=lambda s: None)  # type: ignore[arg-type]
    # run() clears any earlier cancel when it starts (so each run begins
    # uncancelled). A single cancel fired on a timer could therefore land
    # *before* that point on a slow CI runner and be wiped, and the sleep
    # would then run its full 5 s (seen on Windows CI). Keep cancelling
    # until the run has ended, so one cancel always arrives during it.
    done = threading.Event()

    def keep_cancelling() -> None:
        while not done.wait(0.05):
            rt.cancel()

    canceller = threading.Thread(target=keep_cancelling, daemon=True)
    canceller.start()
    start = time.monotonic()
    try:
        with pytest.raises(MacroError, match="cancelled"):
            rt.run("opensak.sleep(5000)")
    finally:
        done.set()
        canceller.join()
    assert time.monotonic() - start < 2
    rt.run("opensak.sleep(1)")  # the next run starts uncancelled


# ── opensak.text: Unicode case, characters and encodings (#1015) ─────────────

def test_upper_lower_change_every_letter():
    assert helpers.upper("zürich ångström éte") == "ZÜRICH ÅNGSTRÖM ÉTE"
    assert helpers.lower("ZÜRICH ÅNGSTRÖM") == "zürich ångström"
    assert helpers.upper("straße") == "STRASSE"


def test_upper_lower_from_lua_unlike_string_upper():
    out = _run('print(("zürich"):upper(), opensak.text.upper("zürich"), '
               'opensak.text.lower("ÖSTERREICH"))')
    assert out == ["ZüRICH\tZÜRICH\tösterreich"]


def test_trim_removes_unicode_spaces():
    assert helpers.trim(" \t Zürich\u00a0\n") == "Zürich"
    assert helpers.trim("") == ""


@pytest.mark.parametrize("args, expected", [
    ((1, 6), "Zürich"),
    ((-4,), "Nord"),
    ((2, 2), "ü"),
    ((0, 1), "Z"),
    ((8, 100), "Nord"),
    ((-100, 2), "Zü"),
    ((5, 3), ""),
    ((1, -6), "Zürich"),
    ((), "Zürich Nord"),
])
def test_sub_counts_characters_like_string_sub(args, expected):
    assert helpers.sub("Zürich Nord", *args) == expected


def test_sub_from_lua():
    out = _run('local s = "Zürich Nord"\n'
               'print(opensak.text.sub(s, 1, 2), opensak.text.sub(s, -4), #s:sub(1, 2))')
    # string.sub(s, 1, 2) is "Z" and half of the ü; text.sub counts characters.
    assert out == ["Zü\tNord\t2"]


def test_sub_rejects_fractional_positions():
    with pytest.raises(MacroError, match="i must be an integer"):
        helpers.sub("abc", 1.5)


def test_html_decode_keeps_tags():
    assert helpers.html_decode("<b>Z&uuml;rich</b> &amp; &#228;&#x00e9;") == "<b>Zürich</b> & äé"


@pytest.mark.parametrize("line, sep, expected", [
    ('GC12345,"Bonus, final",N47 22.123', ",", ["GC12345", "Bonus, final", "N47 22.123"]),
    ('a;"say ""hi""";', ";", ["a", 'say "hi"', ""]),
    ("", ",", []),
    ("one", ",", ["one"]),
])
def test_csv_split(line, sep, expected):
    assert helpers.csv_split(line, sep) == expected


def test_csv_split_from_lua_and_bad_separator():
    out = _run('local f = opensak.text.csv_split([[GC1,"x, y",z]]) print(#f, f[2])')
    assert out == ["3\tx, y"]
    with pytest.raises(MacroError, match="sep must be one character"):
        _run('opensak.text.csv_split("a,b", ", ")')


def test_url_encode_decode():
    assert helpers.url_encode("Zürich & Bern/1") == "Z%C3%BCrich%20%26%20Bern%2F1"
    assert helpers.url_decode("Z%C3%BCrich+Nord") == "Zürich Nord"
    assert helpers.url_decode(helpers.url_encode("a+b c")) == "a+b c"


def test_base64_round_trip():
    assert helpers.base64_encode("Zürich") == "WsO8cmljaA=="
    assert helpers.base64_decode("WsO8\ncmljaA==") == "Zürich"
    assert helpers.base64_decode(helpers.base64_encode(b"\xff\x00")) == b"\xff\x00"
    with pytest.raises(MacroError, match="not valid Base64"):
        helpers.base64_decode("not base64!")


def test_base64_from_lua():
    out = _run('print(opensak.text.base64_decode(opensak.text.base64_encode("Zürich")))')
    assert out == ["Zürich"]


def test_re_escape_matches_literally():
    out = _run('local p = "^" .. opensak.re.escape("Bonus (1/3) *") .. "$"\n'
               'print(opensak.re.match("Bonus (1/3) *", p), opensak.re.match("Bonus 1/3", p))')
    assert out == ["true\tfalse"]


@pytest.mark.parametrize("func", ["upper", "lower", "trim", "sub", "html_decode",
                                  "csv_split", "url_encode", "url_decode",
                                  "base64_encode", "base64_decode"])
def test_text_functions_need_a_string(func):
    with pytest.raises(MacroError, match=f"opensak.text.{func}"):
        _run(f"opensak.text.{func}(12)")


# ── opensak.text.gc_to_id / id_to_gc (GSAK's GsCodeToNum / NumToGsCode) ──────

@pytest.mark.parametrize("code, number", [
    ("GC0", 0), ("GC1", 1), ("GCFFFF", 65535), ("GCG000", 65536), ("GCG001", 65537),
    ("GC12345", 574995), ("GC7XDGP", 6900686),
])
def test_gc_codes_and_numbers(code, number):
    assert helpers.gc_to_id(code) == number
    assert helpers.id_to_gc(number) == code


@pytest.mark.parametrize("code", ["GC", "GCOOO", "GC-1", "1234", "GC1 2", "GCG", "GC0G00"])
def test_gc_to_id_is_nil_for_other_text(code):
    assert helpers.gc_to_id(code) is None


def test_gc_to_id_any_case_and_other_prefixes():
    assert helpers.gc_to_id(" gcg000 ") == 65536
    assert helpers.gc_to_id("TB1") == 1
    assert helpers.id_to_gc(65536, "tb") == "TBG000"


def test_gc_round_trip():
    for n in range(60_000, 72_000, 7):
        assert helpers.gc_to_id(helpers.id_to_gc(n)) == n


def test_gc_functions_from_lua():
    out = _run('print(opensak.text.gc_to_id("GC12345"), opensak.text.id_to_gc(574995), '
               'opensak.text.gc_to_id("nope"))')
    assert out == ["574995\tGC12345\tnil"]


@pytest.mark.parametrize("call, message", [
    ("opensak.text.id_to_gc(-1)", "whole number >= 0"),
    ("opensak.text.id_to_gc(1.5)", "whole number >= 0"),
    ('opensak.text.id_to_gc("1")', "whole number >= 0"),
    ('opensak.text.id_to_gc(1, "T")', "prefix must be two letters"),
    ("opensak.text.gc_to_id(12)", "opensak.text.gc_to_id"),
])
def test_gc_bad_arguments(call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)
