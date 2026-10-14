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

    def approve_folder(self, target, folder, write):
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

def test_coords_parse_returns_two_values_or_nil():
    out = _run('print(opensak.coords.parse("N47 22.123 E008 32.456"))\n'
               'print(opensak.coords.parse("nonsense"))')
    lat, lon = map(float, out[0].split("\t"))
    assert lat == pytest.approx(47 + 22.123 / 60)
    assert lon == pytest.approx(8 + 32.456 / 60)
    assert out[1] == "nil"


@pytest.mark.parametrize("fmt, expected", [
    ("dmm", "N47 22.123  E008 32.456"),
    ("dd", "47.36872, 8.54093"),
    ("utm", "32T E 465340 N 5246242"),
    ("ch1903", "683259 / 247015"),
    ("ch1903+", "2683259 / 1247015"),
])
def test_coords_format(fmt, expected):
    out = _run(f'print(opensak.coords.format("N47 22.123 E008 32.456", "{fmt}"))\n'
               f'print(opensak.coords.format(47 + 22.123 / 60, 8 + 32.456 / 60, "{fmt}"))')
    assert out == [expected, expected]


def test_coords_format_rejects_unknown_format():
    with pytest.raises(MacroError, match="unknown format"):
        _run('opensak.coords.format(47, 8, "mgrs")')


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
    threading.Timer(0.1, rt.cancel).start()
    start = time.monotonic()
    with pytest.raises(MacroError, match="cancelled"):
        rt.run("opensak.sleep(5000)")
    assert time.monotonic() - start < 2
    rt.run("opensak.sleep(1)")  # the next run starts uncancelled
