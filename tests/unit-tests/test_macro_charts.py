"""tests/unit-tests/test_macro_charts.py — the SVG charts and maps of
opensak.chart.*, the boundary outlines of opensak.geo.* (no Vega, no
JavaScript) and opensak.open(), which shows a generated report."""

import re
from pathlib import Path
from typing import Optional

import pytest

from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.export.file_export import active_database_name
from opensak.stats import charts, tables
from opensak.stats import maps as report_maps

class FakeHost:
    """Only folder approval and the database name are used here."""

    def end_macro(self):
        pass

    def approve_folder(self, target, folder, write):
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
