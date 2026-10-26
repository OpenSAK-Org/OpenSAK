"""tests/unit-tests/test_macro_charts.py — the SVG charts and maps of
opensak.chart.*, the boundary outlines of opensak.geo.* (no Vega, no
JavaScript), opensak.open(), which shows a generated report, and the
statistics example macros built on them."""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest

from opensak.db.database import session_for
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache, Log
from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.macro.permissions import temp_dir
from opensak.export.file_export import active_database_name
from opensak.stats import charts, tables
from opensak.stats import maps as report_maps

EXAMPLES = Path(__file__).resolve().parents[2] / "macros" / "examples"


class FakeHost:
    """Only folder approval and the database name are used here."""

    def end_macro(self):
        pass

    def approve_folder(self, target, folder, write, macro=None):
        return FolderApproval.DENY

    def database_name(self):
        return active_database_name()

    def __getattr__(self, name):
        # Bound into the opensak table at every run, never called here.
        return lambda *args: None


@pytest.fixture(autouse=True)
def own_temp_dir(tmp_path, monkeypatch):
    """opensak.temp_dir() inside the test's folder, so the reports these
    tests write never replace real ones in the system temp folder."""
    import tempfile

    folder = tmp_path / "systemp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.fixture(autouse=True)
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "install" / "macros"
    d.mkdir(parents=True)
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d


def _run(source, host=None, **kwargs):
    out: list[str] = []
    MacroRuntime(host or FakeHost(), output=out.append, **kwargs).run(source)
    return out


def _lua_path(path: Path) -> str:
    return str(path).replace("\\", "/")



# ── Charts ──────────────────────────────────────────────────────────────────

ROWS = [{"key": 2023, "count": 5}, {"key": 2024, "count": 12}, {"key": "<b>", "count": None}]


def _bars(svg: str) -> int:
    return len(re.findall(r'<path d="M', svg))


def test_bar_chart_is_svg_with_escaped_labels_and_tooltips():
    svg = charts.bar(ROWS, title='Finds & "more"')
    assert svg.startswith("<svg ") and svg.endswith("</svg>")
    assert 'aria-label="Finds &amp; &quot;more&quot;"' in svg
    assert "<b>" not in svg and "&lt;b&gt;" in svg
    assert "<title>2024: 12</title>" in svg and "<title>&lt;b&gt;: 0</title>" in svg
    assert _bars(svg) == 2      # the 0 bar draws nothing
    assert "var(--chart-color, #2a78d6)" in svg


def test_bar_chart_horizontal_grows_with_rows_and_truncates_labels():
    rows = [{"key": "x" * 40 + str(i), "count": i} for i in range(1, 21)]
    svg = charts.bar(rows, horizontal=True)
    height = int(re.search(r'height="(\d+)"', svg).group(1))
    assert height > charts.DEFAULT_HEIGHT
    assert "x" * 27 + "…" in svg and f"<title>{'x' * 40}20: 20</title>" in svg
    assert _bars(svg) == 20


def test_axis_ticks_are_round():
    assert charts._ticks(12) == [0, 2.5, 5.0, 7.5, 10.0, 12.5]
    assert charts._ticks(1000) == [0, 200, 400, 600, 800, 1000]
    assert charts._ticks(0) == [0.0, 1.0]


def test_line_chart_points_and_last_value():
    rows = [{"m": "2024-01", "t": 3}, {"m": "2024-02", "t": 3}, {"m": "2024-03", "t": 10}]
    svg = charts.line(rows, x="m", y="t", area=True)
    assert svg.count("<circle") == 3 and "<polygon" in svg and "<polyline" in svg
    assert '<title>2024-03: 10</title>' in svg
    assert re.search(r'font-weight="600">10</text>', svg)


def test_empty_rows_still_draw_an_axis():
    assert charts.bar([]).startswith("<svg")
    assert charts.line([]).startswith("<svg")


@pytest.mark.parametrize("kwargs, message", [
    ({"rows": [{"key": 1, "count": "x"}]}, "must be a number"),
    ({"rows": [{"key": 1, "count": float("nan")}]}, "must be a number"),
    ({"rows": [1]}, "not a table"),
    ({"rows": ROWS, "width": 10}, "width must be"),
    ({"rows": ROWS, "color": "red;x:url(a)"}, "color must be"),
])
def test_chart_errors(kwargs, message):
    with pytest.raises(charts.ChartError, match=message):
        charts.bar(**kwargs)


def test_chart_from_lua():
    out = _run("""
        local svg = opensak.chart.bar({ {year = "2024", finds = 3} }, { x = "year", y = "finds", title = "T" })
        print(svg:match("<svg") ~= nil, svg:find("<title>2024: 3</title>", 1, true) ~= nil)
        print(opensak.chart.line({}, { area = true }):match("<svg") ~= nil)
    """)
    assert out == ["true\ttrue", "true"]


@pytest.mark.parametrize("call, message", [
    ('opensak.chart.bar("x")', "expects a list of rows"),
    ('opensak.chart.bar({}, { area = true })', r"unknown option\(s\) \['area'\]"),
    ('opensak.chart.line({}, { horizontal = true })', "unknown option"),
    ('opensak.chart.bar({}, { x = 1 })', "x must be a string"),
    ('opensak.chart.bar({}, { labels = "yes" })', "labels must be true or false"),
    ('opensak.chart.bar({ {key = 1, count = "x"} })', "row 1: count must be a number"),
])
def test_chart_errors_from_lua(call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)


# ── Example macro find_statistics.lua ───────────────────────────────────────

FINDS = [
    {"code": "GC1", "d": "2024-03-04", "type": "Traditional", "fav": 2, "dt": 1.5},
    {"code": "GC2", "d": "2024-03-04 10:00:00", "type": "Multi", "fav": 5, "dt": 2.0},
    {"code": "GC3", "d": "2024-05-01", "type": "Traditional", "fav": None, "dt": 1.5},
    {"code": "GC4", "d": "2023-12-31", "type": "Mystery", "fav": 1, "dt": None},
    {"code": "GC5", "d": None, "type": "Traditional"},
]




def test_heatmap_chart():
    m = tables.pivot(FINDS, "type", "dt")
    svg = charts.heatmap(m, title="T", row_title="Type", col_title="D")
    assert svg.startswith("<svg") and "<title>Traditional / 1.5: 2</title>" in svg
    assert "<title>Multi / 1.5: 0</title>" in svg
    assert "Type \\ D" in svg
    with pytest.raises(charts.ChartError, match="one line of cells"):
        charts.heatmap({"rows": [1], "cols": [1], "cells": []})


def test_rose_chart():
    rows = [{"key": d, "count": n} for d, n in zip("N E S W".split(), (9, 4, 0, 1))]
    svg = charts.rose(rows, title="R")
    assert svg.count("<path") == 3                         # S has no sector
    assert "<title>N: 9</title>" in svg and "<title>S: 0</title>" in svg
    # Area grows with the value: the radius with its square root.
    radii = [float(r) for r in re.findall(r"A([\d.]+),", svg)]
    assert radii[0] == pytest.approx(radii[1] * 1.5, rel=0.01)


def test_stacked_bar_chart():
    rows = [{"key": "2023", "a": 1, "b": 2}, {"key": "2024", "a": 3, "b": None}]
    svg = charts.bar(rows, y=["a", "b"], names=["Alpha", "Beta"])
    assert "<title>2023 · Beta: 2 (total 3)</title>" in svg
    assert "<title>2024 · Alpha: 3 (total 3)</title>" in svg
    assert ">Alpha</text>" in svg and ">Beta</text>" in svg          # legend
    assert "var(--chart-color-2, #eb6834)" in svg
    with pytest.raises(charts.ChartError, match="names must be"):
        charts.bar(rows, y=["a", "b"], names=["x"])
    with pytest.raises(charts.ChartError, match="1 to 8 field names"):
        charts.bar(rows, y=[f"f{i}" for i in range(9)])


def test_stacked_bar_from_a_matrix_in_lua():
    out = _run("""
        local rows = { {y = "2023", t = "Trad"}, {y = "2023", t = "Multi"}, {y = "2024", t = "Trad"} }
        local svg = opensak.chart.bar(opensak.stats.pivot(rows, "y", "t"), { title = "T" })
        print(svg:find("<title>2023 · Trad: 1 (total 2)</title>", 1, true) ~= nil,
              svg:find(">Multi</text>", 1, true) ~= nil)
        local two = opensak.chart.bar({ {key = 1, a = 1, b = 2} }, { y = { "a", "b" } })
        print(two:find("1 · b: 2", 1, true) ~= nil)
    """)
    assert out == ["true\ttrue", "true"]
    with pytest.raises(MacroError, match="x and y are taken from the matrix"):
        _run('opensak.chart.bar(opensak.stats.pivot({ {a = 1, b = 2} }, "a", "b"), { y = "c" })')



# ── Maps and outlines ─────────────────────────────────────────────────────────

def _square(lon0, lat0, lon1, lat1):
    return {"type": "Polygon",
            "coordinates": [[[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]]}


def build_boundaries(folder: Path) -> None:
    """Two countries side by side: Alpha (lon 0-10, lat 40-50) with the
    states North A and South Ä, Beta (lon 10-20) with the state Beta One."""
    import json
    import sqlite3

    layers: dict[str, list[tuple[str, Optional[str], str, dict]]] = {
        "country": [("Alpha", None, "world.geojson", _square(0, 40, 10, 50)),
                    ("Beta", None, "world.geojson", _square(10, 40, 20, 50))],
        "state": [("North A", "alp", "alp.geojson", _square(0, 45, 10, 50)),
                  ("South Ä", "alp", "alp.geojson", _square(0, 40, 10, 45)),
                  ("Beta One", "bet", "bet.geojson", _square(10, 40, 20, 50))],
        "county": [],
    }
    dirs = {"country": "countries", "state": "states", "county": "counties"}
    db = sqlite3.connect(folder / "boundaries.db")
    db.execute("CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER)")
    for layer, regions in layers.items():
        db.execute(f"CREATE VIRTUAL TABLE rtree_{layer} USING rtree(id, min_lat, max_lat, min_lon, max_lon)")
        db.execute(f"CREATE TABLE region_{layer} (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                   "parent TEXT, pack TEXT NOT NULL, feature_index INTEGER NOT NULL, "
                   "poly_version INTEGER NOT NULL DEFAULT 1, is_bundled INTEGER NOT NULL DEFAULT 1)")
        packs: dict[str, list] = {}
        for i, (name, parent, pack, geom) in enumerate(regions, 1):
            features = packs.setdefault(pack, [])
            ring = geom["coordinates"][0]
            lons, lats = [p[0] for p in ring], [p[1] for p in ring]
            db.execute(f"INSERT INTO rtree_{layer} VALUES (?, ?, ?, ?, ?)",
                       (i, min(lats), max(lats), min(lons), max(lons)))
            db.execute(f"INSERT INTO region_{layer} VALUES (?, ?, ?, ?, ?, 1, 1)",
                       (i, name, parent, pack, len(features)))
            features.append({"type": "Feature", "properties": {"name": name, "parent": parent},
                             "geometry": geom})
        (folder / dirs[layer]).mkdir(parents=True, exist_ok=True)
        for pack, features in packs.items():
            (folder / dirs[layer] / pack).write_text(
                json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    db.commit()
    db.close()


@pytest.fixture
def boundaries(tmp_path, monkeypatch):
    folder = tmp_path / "boundaries"
    folder.mkdir()
    build_boundaries(folder)
    monkeypatch.setenv("OPENSAK_BOUNDARIES_DIR", str(folder))
    return folder


@pytest.fixture
def map_data(boundaries):
    from opensak.geo import BoundaryStore

    data = report_maps.MapData(BoundaryStore(boundaries))
    yield data
    data.close()


def test_regions_and_their_countries(map_data):
    assert report_maps.regions(map_data) == [{"name": "Alpha"}, {"name": "Beta"}]
    assert report_maps.regions(map_data, "state") == [
        {"name": "North A", "country": "Alpha"}, {"name": "South Ä", "country": "Alpha"},
        {"name": "Beta One", "country": "Beta"}]
    assert [r["name"] for r in report_maps.regions(map_data, "state", "alpha")] == \
        ["North A", "South Ä"]
    assert report_maps.regions(map_data, "county") == []
    with pytest.raises(report_maps.MapError, match="layer must be one of"):
        report_maps.regions(map_data, "city")


def test_normalize_matches_accents_case_and_punctuation():
    n = report_maps.normalize
    assert n("Graubünden") == n("graubunden") == n("GRAUBÜNDEN")
    assert n("Basel-Landschaft") == n("Basel Landschaft")
    assert n("Αθήνα") == "αθήνα"           # no Latin letters: kept as it is


def test_choropleth_colours_matched_regions_and_lists_the_others(map_data):
    svg = report_maps.choropleth(map_data, [
        {"key": "alpha", "count": 30}, {"key": "Atlantis", "count": 2}], title="T")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<title>Alpha: 30</title>" in svg and "<title>Beta</title>" in svg
    assert "Not on the map: Atlantis" in svg
    states = report_maps.choropleth(map_data, [
        {"key": "South A", "count": 3}, {"key": "North A", "count": 1}],
        layer="state", within="Alpha", extent="all")
    assert "<title>South Ä: 3</title>" in states and "<title>North A: 1</title>" in states
    assert "Beta One" not in states        # Beta is only drawn as a country
    assert "<title>Beta</title>" in states


def test_choropleth_zooms_to_the_data(map_data):
    def view(svg):
        return re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).groups()

    one = report_maps.choropleth(map_data, [{"key": "Alpha", "count": 1}], width=400)
    both = report_maps.choropleth(map_data, [{"key": "Alpha", "count": 1}, {"key": "Beta", "count": 1}],
                                  width=400)
    # Alpha alone is about square, both side by side are wider than high.
    assert int(view(one)[1]) > int(view(both)[1])


def test_class_edges():
    assert report_maps.class_edges([3, 24443, 900, 50]) == [3, 10, 100, 500, 2000]
    assert report_maps.class_edges([1, 2, 3, 4]) == [1, 2, 3, 4]
    assert report_maps.class_edges([0, 0]) == []


def test_points_map(map_data):
    rows = [{"lat": 45.0, "lon": 5.0, "name": "A<1>"}, {"lat": 45.0, "lon": 5.0, "name": "A2"},
            {"lat": 45.0, "lon": 15.0, "name": "B"}, {"lat": None, "lon": 1.0}]
    svg = report_maps.points(map_data, rows, label="name", layer="state")
    assert svg.count("<circle") == 2                      # same pixel drawn once
    assert "<title>A&lt;1&gt; (+1)</title>" in svg and "<title>B</title>" in svg
    with pytest.raises(report_maps.MapError, match="must be coordinates"):
        report_maps.points(map_data, [{"lat": 95.0, "lon": 1.0}])


def test_map_functions_from_lua(boundaries):
    out = _run("""
        local rows = { { key = "Alpha", count = 3 }, { key = "Beta", count = 1 } }
        local svg = opensak.chart.map(rows, { title = "Finds" })
        print(svg:match("<svg") ~= nil, svg:find("<title>Alpha: 3</title>", 1, true) ~= nil)
        local dots = opensak.chart.points({ { lat = 45, lon = 5, name = "x" } }, { label = "name" })
        print(dots:find("<title>x</title>", 1, true) ~= nil)
        local states = opensak.geo.regions("state", { within = "Alpha" })
        print(#states, states[2].name, states[2].country, #opensak.geo.regions())
    """)
    assert out == ["true\ttrue", "true", "2\tSouth Ä\tAlpha\t2"]


def test_map_functions_without_boundary_data():
    out = _run("""
        print(opensak.chart.map({}):find("not installed", 1, true) ~= nil)
        print(opensak.chart.points({}):find("not installed", 1, true) ~= nil)
        print(opensak.geo.regions("state"))
    """)
    assert out == ["true", "true", "nil"]


@pytest.mark.parametrize("call, message", [
    ('opensak.chart.map({}, { layer = "city" })', "layer must be one of"),
    ('opensak.chart.map({}, { extent = "some" })', "extent must be one of"),
    ('opensak.chart.map({}, { nope = 1 })', r"unknown option\(s\) \['nope'\]"),
    ('opensak.chart.map({ { key = "Alpha", count = "x" } })', "count must be a number"),
    ('opensak.chart.map({}, { within = 3 })', "within must be a country name"),
    ('opensak.chart.points({}, { radius = 50 })', "radius must be"),
    ('opensak.geo.regions("x")', "layer must be one of"),
])
def test_map_errors_from_lua(boundaries, call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)



def test_geo_outlines(map_data):
    collection, unmatched = report_maps.outlines(
        map_data, "country", rows=[{"key": "alpha", "count": 5}, {"key": "Atlantis", "count": 1}])
    assert collection["type"] == "FeatureCollection" and unmatched == ["Atlantis"]
    props = {f["properties"]["name"]: f["properties"] for f in collection["features"]}
    assert props["Alpha"]["count"] == 5 and "count" not in props["Beta"]
    # Zoomed to Alpha (with a margin); Beta is still in the area next to it.
    lon0, lat0, lon1, lat1 = collection["bbox"]
    assert -1 < lon0 < 0 and 10 < lon1 < 11 and 39 < lat0 < 40 and 50 < lat1 < 51
    # Outer rings clockwise (d3-geo): the shoelace sum is negative.
    ring = props and collection["features"][0]["geometry"]["coordinates"][0]
    area2 = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:]))
    assert area2 < 0
    states, _ = report_maps.outlines(map_data, "state", within="Alpha", extent="all")
    assert sorted(f["properties"]["name"] for f in states["features"]) == ["North A", "South Ä"]
    assert {f["properties"]["country"] for f in states["features"]} == {"Alpha"}
    near, _ = report_maps.outlines(map_data, "country", around=[{"lat": 45.0, "lon": 15.0}])
    assert [f["properties"]["name"] for f in near["features"]] == ["Beta"]
    boxed, _ = report_maps.outlines(map_data, "state", box=[11, 41, 19, 49])
    assert [f["properties"]["name"] for f in boxed["features"]] == ["Beta One"]
    assert boxed["bbox"] == [11, 41, 19, 49]
    with pytest.raises(report_maps.MapError, match="box must be"):
        report_maps.outlines(map_data, box=[1, 2])
    with pytest.raises(report_maps.MapError, match="detail must be"):
        report_maps.outlines(map_data, detail=5)



def test_geo_outlines_from_lua(boundaries):
    out = _run("""
        local fc, unmatched = opensak.geo.outlines("country",
            { rows = { { key = "Beta", count = 2 }, { key = "Nowhere", count = 1 } } })
        print(fc.type, #fc.features, unmatched[1])
        for _, f in ipairs(fc.features) do
            if f.properties.count then print(f.properties.name, f.properties.count) end
        end
    """)
    assert out == ["FeatureCollection	2	Nowhere", "Beta	2"]


def test_geo_outlines_without_boundary_data():
    assert _run('print(opensak.geo.outlines("country"))') == ["nil"]



# ── opensak.open ────────────────────────────────────────────────────────────

class OpenHost(FakeHost):
    """Records the files opened; *allow* answers the confirmation."""

    def __init__(self, allow=False):
        self.allow = allow
        self.opened: list[Path] = []
        self.asked: list[str] = []

    def confirm(self, message):
        self.asked.append(message)
        return self.allow

    def open_file(self, path):
        self.opened.append(path)
        return True


def test_open_shows_a_written_report_without_asking(own_temp_dir):
    host = OpenHost()
    _run("""
        local path = opensak.write_text(opensak.temp_dir() .. "/report.html", "<h1>x</h1>")
        opensak.open(path)
    """, host)
    assert [p.name for p in host.opened] == ["report.html"] and host.asked == []


def test_open_a_file_in_the_temp_folder_without_asking(own_temp_dir):
    host = OpenHost()
    _run("""
        local path = opensak.write_text(opensak.temp_dir() .. "/a.txt", "x")
    """)
    _run('opensak.open(opensak.temp_dir() .. "/a.txt")', host)
    assert [p.name for p in host.opened] == ["a.txt"] and host.asked == []


@pytest.mark.parametrize("allow", [True, False])
def test_open_asks_for_any_other_file(tmp_path, allow):
    report = tmp_path / "elsewhere" / "old.html"
    report.parent.mkdir()
    report.write_text("<p>x</p>", encoding="utf-8")
    host = OpenHost(allow=allow)
    call = f'opensak.open("{_lua_path(report)}")'
    if allow:
        _run(call, host)
        assert host.opened == [report.resolve()]
    else:
        with pytest.raises(MacroError, match="did not allow"):
            _run(call, host)
        assert host.opened == []
    assert len(host.asked) == 1 and str(report.resolve()) in host.asked[0]


@pytest.mark.parametrize("path, message", [
    ("https://example.com/a.html", "not URLs"),
    ("file:///C:/a.html", "not URLs"),
    ("evil.bat", "only these file types"),
    ("macro.lua", "only these file types"),
    ("link.url", "only these file types"),
    ("missing.html", "file not found"),
])
def test_open_refuses(path, message):
    host = OpenHost(allow=True)
    with pytest.raises(MacroError, match=message):
        _run(f'opensak.open("{path}")', host)
    assert host.opened == []


def test_open_is_limited_per_run(own_temp_dir):
    host = OpenHost()
    with pytest.raises(MacroError, match="at most 5 files per run"):
        _run("""
            local path = opensak.write_text(opensak.temp_dir() .. "/r.html", "x")
            for i = 1, 6 do opensak.open(path) end
        """, host)
    assert len(host.opened) == 5


# ── caches{ all = true } ────────────────────────────────────────────────────

def _cache(s, code, found_date, cache_type="Traditional Cache", d=1.5, t=2.0, logs=()):
    cache = Cache(gc_code=code, name=f"Cache {code}", cache_type=cache_type,
                  latitude=47.0, longitude=8.0, difficulty=d, terrain=t,
                  found=found_date is not None, found_date=found_date,
                  url=f"https://coord.info/{code}")
    s.add(cache)
    s.flush()
    for log_type, when in logs:
        s.add(Log(cache_id=cache.id, log_type=log_type, log_date=when, finder="me"))
    return cache


@pytest.fixture
def finds_db():
    manager = get_db_manager()
    db = manager.new_database("Finds")
    manager.switch_to(db)
    with session_for(db.path) as s:
        day = datetime(2024, 3, 4)
        # Same day: GCB's find log was imported first, so it is find no. 2
        # although GCA sorts first by code (and by found time).
        _cache(s, "GCB", day.replace(hour=12), logs=[("Found it", day)])
        _cache(s, "GCA", day.replace(hour=9), "Multi-cache", 3.0, 4.5,
               logs=[("Found it", day)])
        _cache(s, "GCZ", datetime(2023, 12, 31), logs=[("Found it", datetime(2023, 12, 31))])
        _cache(s, "GCN", datetime(2024, 5, 1), "Unknown Cache", 5.0, 5.0)
        _cache(s, "GCX", None)                          # not found
    return db


def _describe(db, updates):
    with session_for(db.path) as s:
        for code, values in updates.items():
            cache = s.query(Cache).filter_by(gc_code=code).one()
            for key, value in values.items():
                setattr(cache, key, value)


class EmptyFilterHost(OpenHost):
    """The active filter shows no cache at all."""

    def filtered_caches(self):
        return []


def test_caches_all_ignores_the_active_filter(finds_db):
    out = _run("""
        local n, all = 0, 0
        for _ in opensak.caches() do n = n + 1 end
        for c in opensak.caches{ all = true, fields = { "found" } } do all = all + 1 end
        print(n, all)
    """, EmptyFilterHost())
    assert out == ["0\t5"]
    with pytest.raises(MacroError, match="cannot be combined with filter keys"):
        _run("for _ in opensak.caches{ all = true, found = true } do end", EmptyFilterHost())
    with pytest.raises(MacroError, match="all must be true or false"):
        _run('for _ in opensak.caches{ all = "yes" } do end', EmptyFilterHost())


# ── Example macros ──────────────────────────────────────────────────────────

def _tile(page, label):
    m = re.search(r'<div class="value">([^<]*)</div><div class="label">' + re.escape(label), page)
    return m and m.group(1)


def _example(name):
    return (EXAMPLES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["find_statistics.lua", "database_statistics.lua",
                                  "region_coverage.lua", "owned_statistics.lua"])
def test_statistics_examples_state_their_limitations(name):
    head = _example(name).split("\nlocal ", 1)[0]
    assert "-- Known limitations:" in head
    assert "template macro" in head and "geocaching.com" in head
    assert "boundary data" in head and "not yet stable" in head


def test_find_statistics_example(finds_db, boundaries):
    _describe(finds_db, {
        "GCA": {"latitude": 47.0, "longitude": 5.0, "country": "Alpha", "state": "North A"},
        "GCB": {"latitude": 46.0, "longitude": 6.0, "country": "Alpha", "state": "North A"},
        "GCN": {"latitude": 42.0, "longitude": 5.0, "country": "Alpha", "state": "South A"},
        "GCZ": {"latitude": 45.0, "longitude": 15.0, "country": "Beta", "state": "Beta One"},
    })
    source = _example("find_statistics.lua").replace(
        "local MILESTONE_STEP = nil", "local MILESTONE_STEP = 2")
    host = OpenHost()
    out = _run(source, host)
    target = temp_dir() / "find_statistics.html"
    assert out == [f"4 finds → {target}"]
    assert host.opened == [target] and host.asked == []
    page = target.read_text(encoding="utf-8")

    assert _tile(page, "finds") == "4"
    assert _tile(page, "cache days") == "3"
    assert _tile(page, "finds per cache day") == "1.33"
    assert _tile(page, "D/T combinations") == "3 / 81"
    # 2023-12-31, 2024-03-04, 2024-05-01: no two days in a row; the longest
    # break is 1 January to 3 March 2024 (leap year).
    assert _tile(page, "longest streak (days)") == "1"
    assert _tile(page, "longest break (days)") == "63"
    assert _tile(page, "countries") == "2 / 2"
    assert _tile(page, "states in Alpha") == "2 / 2"
    assert "2023-12-31 to 2024-05-01" in page
    # Per year, stacked by cache type; the running total ends at 4 in May 2024.
    assert "<title>2024 · Traditional Cache: 1 (total 3)</title>" in page
    assert "<title>2024-04: 3</title>" in page and "<title>2024-05: 4</title>" in page
    for weekday in ("Mon: 2", "Tue: 0", "Wed: 1", "Sun: 1"):
        assert f"<title>{weekday}</title>" in page
    assert "<title>Traditional Cache: 2</title>" in page
    # Maps: countries and the states of the home country ("South A" is the
    # outline "South Ä").
    assert "<title>Alpha: 3</title>" in page and "<title>Beta: 1</title>" in page
    assert "<title>North A: 2</title>" in page and "<title>South Ä: 1</title>" in page
    assert page.count("<circle") >= 4                    # dots + rose rings
    assert "<script" not in page
    # Milestones 1, 2 and the last one (4); 2 is GCB, found on the same day
    # as GCA but with the earlier imported find log.
    rows = re.findall(r'<tr><td class="num">(\d+)</td><td>([\d-]+)</td><td class="num">(\d*)</td>'
                      r'<td><a href="https://coord.info/(GC\w+)">GC\w+</a></td>', page)
    assert rows == [("1", "2023-12-31", "", "GCZ"), ("2", "2024-03-04", "64", "GCB"),
                    ("4", "2024-05-01", "58", "GCN")]


def test_find_statistics_example_without_boundary_data(finds_db):
    out = _run(_example("find_statistics.lua"), OpenHost())
    assert out[0].startswith("4 finds → ")
    page = (temp_dir() / "find_statistics.html").read_text(encoding="utf-8")
    assert "Map not available: the boundary data is not installed." in page
    assert _tile(page, "countries") == "0"


def test_find_statistics_example_without_finds():
    manager = get_db_manager()
    manager.switch_to(manager.new_database("Empty"))
    host = OpenHost()
    assert _run(_example("find_statistics.lua"), host) == [
        "No found caches with a found date in Empty."]
    assert host.opened == []


def test_database_statistics_example(finds_db, boundaries):
    _describe(finds_db, {
        "GCA": {"hidden_date": datetime(2020, 5, 1), "country": "Alpha", "state": "North A",
                "owner_name": "Alice", "favorite_points": 7, "container": "Small",
                "latitude": 47.0, "longitude": 5.0},
        "GCB": {"hidden_date": datetime(2022, 1, 1), "country": "Alpha", "state": "North A",
                "owner_name": "Alice", "favorite_points": 3, "container": "Micro",
                "latitude": 46.0, "longitude": 6.0},
        "GCZ": {"hidden_date": datetime(2022, 1, 2), "country": "Beta", "state": "Beta One",
                "owner_name": "Bob", "favorite_points": 12, "archived": True,
                "latitude": 45.0, "longitude": 15.0},
        "GCX": {"hidden_date": datetime(2023, 3, 3), "country": "Alpha", "state": "South A",
                "owner_name": "<Eve>", "favorite_points": 0, "latitude": 42.0, "longitude": 5.0},
    })
    host = EmptyFilterHost()
    out = _run(_example("database_statistics.lua"), host)
    target = temp_dir() / "database_statistics.html"
    assert out == [f"5 caches → {target}"]
    assert host.opened == [target]
    page = target.read_text(encoding="utf-8")

    assert _tile(page, "caches") == "5"
    assert _tile(page, "found by you (80 %)") == "4"
    assert _tile(page, "still to find (active)") == "1"        # GCX
    assert (_tile(page, "active"), _tile(page, "disabled"), _tile(page, "archived")) == ("4", "0", "1")
    assert _tile(page, "owners") == "3"
    assert _tile(page, "countries") == "2 / 2"
    assert _tile(page, "states in Alpha") == "2 / 2"
    assert _tile(page, "favourite points") == "22"
    assert _tile(page, "most days in a row with a new cache") == "2"   # 1 and 2 January 2022
    assert "placed 2020-05-01 to 2023-03-03" in page
    # Per type with the found share, and placed per year stacked by status.
    assert ('<td>Traditional Cache</td><td class="num">3</td>' in page
            and "<td>67 %</td>" in page)
    assert "<title>2022 · active: 1 (total 2)</title>" in page
    assert "<title>2022 · archived: 1 (total 2)</title>" in page
    # Maps: caches per country; still to find per state of Alpha.
    assert "<title>Alpha: 3</title>" in page and "<title>Beta: 1</title>" in page
    assert "<title>South Ä: 1</title>" in page and "<title>North A</title>" in page
    assert "&lt;Eve&gt;" in page and "<Eve>" not in page
    # Most favourite points first, linked to the listing.
    first = re.search(r'<td class="num">(\d+)</td><td><a href="https://coord.info/(GC\w+)">', page)
    assert first is not None and first.groups() == ("12", "GCZ")


def _coverage_db(db):
    _describe(db, {
        "GCA": {"country": "Alpha", "state": "North A", "latitude": 47.0, "longitude": 5.0},
        # Case and punctuation of country and state do not matter.
        "GCB": {"country": "alpha", "state": "north-a", "latitude": 46.0, "longitude": 6.0},
        # No state of the boundary data: not counted, listed.
        "GCN": {"country": "Alpha", "state": "Atlantis", "latitude": 44.0, "longitude": 5.0},
        "GCZ": {"country": "Beta", "state": "Beta One", "latitude": 45.0, "longitude": 15.0},
        # Not found; without the accent of the outline "South Ä", which it
        # still matches.
        "GCX": {"country": "Alpha", "state": "South A", "latitude": 42.0, "longitude": 5.0},
    })


def test_region_coverage_example_states(finds_db, boundaries):
    _coverage_db(finds_db)
    host = EmptyFilterHost()
    out = _run(_example("region_coverage.lua"), host)
    target = temp_dir() / "region_coverage.html"
    assert out == [f"1 of 2 states → {target}"]
    assert host.opened == [target]
    page = target.read_text(encoding="utf-8")
    assert "<title>Region coverage: states in Alpha</title>" in page
    assert _tile(page, "states found") == "1 / 2"
    assert _tile(page, "coverage") == "50 %"
    assert _tile(page, "missing with a cache to find") == "1"
    assert _tile(page, "finds in Alpha") == "2"
    # The missing state with its nearest cache still to find, linked.
    assert re.search(r'<td>South Ä</td><td class="num">\d+</td>'
                     r'<td><a href="https://coord.info/GCX">GCX</a></td>', page)
    # North A was got with GCA (found at 9:00, before GCB).
    assert re.search(r'<td>North A</td><td><a href="https://coord.info/GCA">GCA</a></td>', page)
    assert "Names that match no region" in page and "<td>Atlantis</td>" in page
    assert "<title>North A: 2</title>" in page


def test_region_coverage_example_country_setting(finds_db, boundaries):
    _coverage_db(finds_db)
    source = _example("region_coverage.lua").replace("local COUNTRY = nil", 'local COUNTRY = "BETA"')
    out = _run(source, EmptyFilterHost())
    assert out[0].startswith("1 of 1 states → ")
    page = (temp_dir() / "region_coverage.html").read_text(encoding="utf-8")
    assert "<title>Region coverage: states in Beta</title>" in page
    source = _example("region_coverage.lua").replace("local COUNTRY = nil", 'local COUNTRY = "Gamma"')
    assert _run(source, EmptyFilterHost()) == ["The boundary data has no country Gamma: set COUNTRY."]


def test_region_coverage_example_countries(finds_db, boundaries):
    _coverage_db(finds_db)
    source = _example("region_coverage.lua").replace('local LAYER = "state"', 'local LAYER = "country"')
    out = _run(source, EmptyFilterHost())
    assert out[0].startswith("2 of 2 countries → ")
    page = (temp_dir() / "region_coverage.html").read_text(encoding="utf-8")
    assert _tile(page, "countries found") == "2 / 2"
    assert "None — you have them all." in page
    assert "Names that match no region" not in page


def test_region_coverage_example_without_boundary_data(finds_db):
    host = OpenHost()
    assert _run(_example("region_coverage.lua"), host) == [
        "The boundary data is not installed (see Update location)."]
    assert host.opened == []


# ── geo.regions{ rows = }: counting regions like the maps match them ───────

def test_match_regions_like_the_map(map_data):
    found, unmatched = report_maps.match(map_data, [
        {"key": "south a", "count": 2}, {"key": "South-Ä", "count": 1},
        {"key": "Atlantis", "count": 5}, {"key": None, "count": 9}], "state", within="Alpha")
    assert found == [{"name": "North A", "country": "Alpha"},
                     {"name": "South Ä", "country": "Alpha", "count": 3}]
    assert unmatched == ["Atlantis"]
    assert report_maps.match(map_data, None)[0] == report_maps.regions(map_data)


def test_geo_regions_with_rows_from_lua(boundaries):
    out = _run("""
        local all, unmatched = opensak.geo.regions("state",
            { within = "Alpha", rows = { { st = "North A", n = 2 }, { st = "Nowhere" } },
              key = "st", value = "n" })
        print(#all, all[1].name, all[1].n, all[2].n, #unmatched, unmatched[1])
        print(select("#", opensak.geo.regions("state")))
    """)
    assert out == ["2\tNorth A\t2\tnil\t1\tNowhere", "1"]
    with pytest.raises(MacroError, match=r"unknown option\(s\) \['around'\]"):
        _run('opensak.geo.regions("state", { around = {} })')


def test_find_statistics_counts_only_states_on_the_map(finds_db, boundaries):
    # Four spellings, two of them of the same state, and one that is no
    # state of Alpha at all: still 2 of 2 states.
    _describe(finds_db, {
        "GCA": {"latitude": 47.0, "longitude": 5.0, "country": "Alpha", "state": "North A"},
        "GCB": {"latitude": 46.0, "longitude": 6.0, "country": "alpha", "state": "North-A"},
        "GCN": {"latitude": 42.0, "longitude": 5.0, "country": "Alpha", "state": "South Ä"},
        "GCZ": {"latitude": 45.0, "longitude": 15.0, "country": "Alpha", "state": "Atlantis"},
    })
    _run(_example("find_statistics.lua"), OpenHost())
    page = (temp_dir() / "find_statistics.html").read_text(encoding="utf-8")
    assert _tile(page, "states in Alpha") == "2 / 2"
    assert _tile(page, "countries") == "1 / 2"
    assert "Names that match no region" in page
    assert re.search(r"<td>state</td><td>Atlantis</td><td class=\"num\">1</td>", page)


def test_database_statistics_country_setting(finds_db, boundaries):
    _describe(finds_db, {
        "GCA": {"country": "Alpha", "state": "North A"},
        "GCB": {"country": "Alpha", "state": "North A"},
        "GCZ": {"country": "Beta", "state": "Beta One"},
    })
    source = _example("database_statistics.lua").replace(
        "local COUNTRY = nil", 'local COUNTRY = "Beta"')
    _run(source, EmptyFilterHost())
    page = (temp_dir() / "database_statistics.html").read_text(encoding="utf-8")
    assert _tile(page, "states in Beta") == "1 / 1"
    assert "Still to find in Beta" in page
    assert "Names that match no region" not in page



def test_examples_measure_from_the_active_centre(finds_db, boundaries):
    from opensak.gui.settings import get_settings

    _coverage_db(finds_db)
    settings = get_settings()
    settings.home_lat, settings.home_lon = 42.0, 5.0      # where GCX is
    _run(_example("region_coverage.lua"), EmptyFilterHost())
    page = (temp_dir() / "region_coverage.html").read_text(encoding="utf-8")
    assert re.search(r'<td>South Ä</td><td class="num">0</td>', page)


# ── Example macro owned_statistics.lua ──────────────────────────────────────

def _hide(s, code, hidden, cache_type="Traditional Cache", logs=(), **values):
    """A cache of the user's (unless *values* say otherwise) with its logs:
    (type, date, finder, finder_id, text[, by_owner])."""
    values = {"latitude": 47.0, "longitude": 8.0, "is_owner": True, "owner_name": "Vyrembi",
              **values}
    cache = Cache(gc_code=code, name=f"Hide {code}", cache_type=cache_type,
                  hidden_date=hidden, url=f"https://coord.info/{code}", **values)
    s.add(cache)
    s.flush()
    for log_type, when, finder, finder_id, log_text, *by_owner in logs:
        s.add(Log(cache_id=cache.id, log_type=log_type, log_date=when, finder=finder,
                  finder_id=finder_id, text=log_text, logged_by_owner=bool(by_owner)))


@pytest.fixture
def hides_db():
    manager = get_db_manager()
    db = manager.new_database("Hides")
    manager.switch_to(db)
    with session_for(db.path) as s:
        _hide(s, "GCH1", datetime(2020, 1, 15), latitude=47.5, difficulty=1.5, terrain=2.0,
              container="Small", favorite_points=5, elevation=400.0, logs=[
                  ("Found it", datetime(2020, 1, 20), "Alice", "1", "TFTC"),
                  ("Found it", datetime(2020, 1, 20), "Bob", None, "Nice one, thanks"),
                  ("Found it", datetime(2021, 6, 1), "Carl", None, "one two three four five six"),
                  ("Write note", datetime(2021, 7, 1), "Alice", "1", "x"),
                  ("Owner Maintenance", datetime(2021, 7, 2), "Vyrembi", "9", "ok", True),
              ])
        # Renamed finder: the same id, counted once under the newer name.
        # The find count of the import (40) wins over the one find log.
        _hide(s, "GCH2", datetime(2021, 3, 1), "Multi-cache", latitude=46.9, longitude=8.5,
              difficulty=3.0, terrain=2.0, container="Micro", favorite_points=1, find_count=40,
              elevation=1500.0, archived=True, last_log_date=datetime(2022, 3, 1), logs=[
                  ("Found it", datetime(2021, 3, 2), "Alice2", "1", "TFTC"),
                  ("Archive", datetime(2022, 3, 1), "Vyrembi", "9", "bye", True),
              ])
        _hide(s, "GCE", datetime(2021, 3, 1), "Event Cache", latitude=47.2, longitude=9.0,
              difficulty=1.0, terrain=1.0, container="Other", logs=[
                  ("Attended", datetime(2021, 3, 1), "Bob", None, "Great event"),
                  ("Attended", datetime(2021, 3, 1), "Dora", None, "Fun"),
              ])
        # Not the user's: left out unless OWNER names its owner.
        _hide(s, "GCX", datetime(2019, 5, 5), is_owner=False, owner_name="Zürich Team",
              logs=[("Found it", datetime(2019, 6, 1), "Alice", "1", "TFTC")])
    return db


def _record(page, label):
    m = re.search(r"<tr><td>" + re.escape(label) + r"</td><td>([^<]*)</td>"
                  r'<td>(?:<a href="https://coord.info/(GC\w+)">)?', page)
    return m and m.groups()


def test_owned_statistics_example(hides_db):
    host = EmptyFilterHost()
    out = _run(_example("owned_statistics.lua"), host)
    target = temp_dir() / "owned_statistics.html"
    assert out == [f"3 caches, 45 finds → {target}"]
    assert host.opened == [target]
    page = target.read_text(encoding="utf-8")

    assert _tile(page, "caches") == "3"
    assert (_tile(page, "active"), _tile(page, "disabled"), _tile(page, "archived")) == ("2", "0", "1")
    assert _tile(page, "events hosted") == "1"
    assert _tile(page, "attendees of your events") == "2"
    assert _tile(page, "finds received") == "45"          # 3 + 40 + 2
    assert _tile(page, "different finders") == "4"        # Alice(2), Bob, Carl, Dora
    assert _tile(page, "favourite points") == "6"
    assert _tile(page, "FP h-index") == "1"
    assert _tile(page, "D/T combinations") == "3 / 81"
    assert _tile(page, "D or T 3 or more") == "1 (33.3 %)"
    assert "GCX" not in page and "Owner Maintenance" not in page
    # Per year from the find logs: 2020 two finds on one day, 2021 four on three.
    assert re.search(r'<td>2020</td><td class="num">2</td>.*?<td class="num">1</td><td>2.00</td>', page)
    assert re.search(r'<td>2021</td><td class="num">4</td>.*?<td class="num">3</td><td>1.33</td>', page)
    assert "<title>2021-06: 6</title>" in page
    assert "<title>2021 · Archived: 1 (total 2)</title>" in page

    assert _record(page, "Northernmost")[1] == "GCH1"
    assert _record(page, "Highest") == ("1500 m", "GCH2")
    assert _record(page, "Lowest") == ("400 m", "GCH1")
    assert _record(page, "Most finds") == ("40", "GCH2")
    assert _record(page, "Most finds on one day")[0] == "2 on 2021-03-01"
    assert _record(page, "Day with the most finds") == ("2 on 2020-01-20", None)
    assert _record(page, "Most favourite points") == ("5", "GCH1")
    assert _record(page, "Found most often") == ("every 9.1 days", "GCH2")   # 365 / 40
    assert _record(page, "Active cache found the longest ago")[1] == "GCH1"
    assert _record(page, "Event with the most attendees") == ("2", "GCE")
    assert _record(page, "Lifetime of archived caches") == (
        "365 days on average; longest 365 days", "GCH2")
    assert _record(page, "Longest find log") == ("6 words", "GCH1")
    # Top finders: by the number of caches, the renamed one under the new name.
    finders = re.findall(r'<tr><td class="num">(\d+)</td><td>([^<]+)</td><td class="num">(\d+)</td>',
                         page)
    assert finders[:4] == [("1", "Alice2", "2"), ("2", "Bob", "2"), ("3", "Carl", "1"),
                           ("4", "Dora", "1")]
    assert "2 of 366 days of the year" in page
    assert '<td>Found it</td><td class="num">4</td>' in page
    assert '<td>Attended</td><td class="num">2</td>' in page
    assert "<script" not in page


def test_owned_statistics_example_owner_setting(hides_db):
    source = _example("owned_statistics.lua").replace(
        "local OWNER = nil", 'local OWNER = "ZÜRICH TEAM "')
    out = _run(source, EmptyFilterHost())
    assert out[0].startswith("1 caches, 1 finds → ")
    page = (temp_dir() / "owned_statistics.html").read_text(encoding="utf-8")
    assert "GCX" in page and "GCH1" not in page
    assert "ZÜRICH TEAM  · Hides" in page


def test_owned_statistics_example_without_owned_caches(finds_db):
    host = OpenHost()
    assert _run(_example("owned_statistics.lua"), host) == [
        "No caches owned by you in Finds. Set your geocaching.com username in Settings, "
        "or OWNER in the macro."]
    assert host.opened == []
