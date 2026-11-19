# tests/unit-tests/test_osm_extract.py — OSM boundary extraction (tools/boundaries/osm_extract.py).

import csv
import json
from pathlib import Path

import pytest

pytest.importorskip("osmium", reason="pyosmium is a dev dependency (pip install -e '.[dev]')")

from tools.boundaries import osm_extract as ox  # noqa: E402


# A tiny country "XX" (lon 0–10, lat 40–50) with two states and four counties,
# plus a state of a neighbouring country that the extract also contains.
#
#   state "Ouest/West" (XX-OU) = lon 0–5     "Est" (XX-ES) = lon 5–10     "Nachbar" = lon 10–15
#   county "Biel/Bienne" = 0–5, 40–45        "Doppel" = 0–5, 45–50
#   county "Doppel" = 5–10, 40–45            "See" = 5–10, 45–50 (excluded in the config)
#   a county without a name = 10–15 (outside XX anyway)
#   a closed way tagged as a state ("Amt") — a mistagged building, never a region
_RECTS = {
    # relation id: (min_lon, min_lat, max_lon, max_lat, tags)
    1: (0, 40, 10, 50, {"admin_level": "2", "ISO3166-1:alpha2": "XX", "name": "Xland"}),
    2: (0, 40, 5, 50, {"admin_level": "4", "name": "Ouest/West", "ISO3166-2": "XX-OU"}),
    3: (5, 40, 10, 50, {"admin_level": "4", "name": "Est", "ISO3166-2": "XX-ES"}),
    4: (10, 40, 15, 50, {"admin_level": "4", "name": "Nachbar"}),
    5: (0, 40, 5, 45, {"admin_level": "8", "name": "Biel/Bienne"}),
    6: (0, 45, 5, 50, {"admin_level": "8", "name": "Doppel"}),
    7: (5, 40, 10, 45, {"admin_level": "8", "name": "Doppel"}),
    8: (5, 45, 10, 50, {"admin_level": "8", "name": "See"}),
    9: (10, 40, 15, 50, {"admin_level": "8"}),
}
_WAY_AREA = (1, 41, 2, 42, {"admin_level": "4", "name": "Amt"})


def _write_osm(path: Path) -> None:
    nodes: dict[tuple[int, int], int] = {}
    node_xml, way_xml, rel_xml = [], [], []

    def node(lon: int, lat: int) -> int:
        if (lon, lat) not in nodes:
            nid = len(nodes) + 1
            nodes[(lon, lat)] = nid
            node_xml.append(f'<node id="{nid}" version="1" lat="{lat}" lon="{lon}"/>')
        return nodes[(lon, lat)]

    for rid, (x0, y0, x1, y1, tags) in _RECTS.items():
        ring = [node(x0, y0), node(x1, y0), node(x1, y1), node(x0, y1), node(x0, y0)]
        wid = 100 + rid
        way_xml.append(f'<way id="{wid}" version="1">'
                       + "".join(f'<nd ref="{n}"/>' for n in ring) + "</way>")
        all_tags = {"type": "boundary", "boundary": "administrative", **tags}
        rel_xml.append(f'<relation id="{rid}" version="1">'
                       f'<member type="way" ref="{wid}" role="outer"/>'
                       + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in all_tags.items())
                       + "</relation>")
    x0, y0, x1, y1, tags = _WAY_AREA
    ring = [node(x0, y0), node(x1, y0), node(x1, y1), node(x0, y1), node(x0, y0)]
    way_xml.append('<way id="999" version="1">' + "".join(f'<nd ref="{n}"/>' for n in ring)
                   + "".join(f'<tag k="{k}" v="{v}"/>'
                             for k, v in {"boundary": "administrative", **tags}.items())
                   + "</way>")
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="test">\n'
        + "\n".join(node_xml + way_xml + rel_xml) + "\n</osm>\n",
        encoding="utf-8",
    )


def _cfg(**overrides: object) -> ox.CountryConfig:
    values: dict = {"code": "XX", "iso3": "xxx", "state_level": 4, "county_level": 8,
                    "state_name_split": "/", "county_name_split": "/", "county_name_join": "-",
                    "county_exclude": ["See"]}
    values.update(overrides)
    return ox.CountryConfig(**values)


def _names(path: Path) -> list[str]:
    return [f["properties"]["name"] for f in json.loads(path.read_text("utf-8"))["features"]]


@pytest.fixture()
def osm_file(tmp_path: Path) -> Path:
    path = tmp_path / "xx.osm"
    _write_osm(path)
    return path


def test_writes_packs_in_the_pipeline_format(osm_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    meta = ox.run(_cfg(), osm_file, out, version=3, state_tolerance=0.0005, county_tolerance=0.0)

    states = out / "states" / "xxx.geojson"
    counties = out / "counties" / "xxx_all_xxx.geojson"
    assert sorted(_names(states)) == ["Est", "Ouest"]   # split; neighbour and closed way left out
    assert sorted(_names(counties)) == ["Biel-Bienne", "Doppel (ES)", "Doppel (OU)"]

    feature = json.loads(counties.read_text("utf-8"))["features"][0]
    props = feature["properties"]
    assert props["parent"] == "xxx" and props["version"] == 3
    assert props["source"] == "osm" and props["licence"] == "ODbL"
    assert feature["geometry"]["type"] == "MultiPolygon"
    assert len(feature["bbox"]) == 4

    assert meta["counts"] == {"state": 2, "county": 3}
    assert any("1 closed ways" in w for w in meta["warnings"])
    assert any("county_exclude" in w and "See" in w for w in meta["warnings"])
    provenance = json.loads((out / "osm_extract_xxx.json").read_text("utf-8"))
    assert provenance["attribution"] == "© OpenStreetMap contributors"


def test_duplicate_names_in_the_same_state_get_the_osm_id(osm_file: Path, tmp_path: Path) -> None:
    # Without state codes the two "Doppel" can't be told apart by state: OSM ids.
    text = osm_file.read_text("utf-8").replace('<tag k="ISO3166-2" v="XX-OU"/>', "")
    osm_file.write_text(text, "utf-8")
    out = tmp_path / "out"
    ox.run(_cfg(), osm_file, out, version=1, state_tolerance=0, county_tolerance=0)
    names = _names(out / "counties" / "xxx_all_xxx.geojson")
    assert sorted(names) == ["Biel-Bienne", "Doppel (r6)", "Doppel (r7)"]


def test_names_split_join_and_rename() -> None:
    cfg = _cfg(names={"Est": "East", "Biel-Bienne": "Biel/Bienne (BE)"})
    assert ox.pick_name({"name": "Valais/Wallis"}, cfg, "state") == "Valais"
    assert ox.pick_name({"name": "Biel/Bienne"}, cfg, "county") == "Biel/Bienne (BE)"
    assert ox.pick_name({"name": "Est"}, cfg, "state") == "East"
    assert ox.pick_name({"name:de": "Genf"}, cfg, "state") is None
    assert ox.pick_name({"name:de": "Genf", "name": "Genève"},
                        _cfg(name_tags=["name:de", "name"]), "state") == "Genf"
    official = _cfg(county_name_tags=["official_name", "name"])
    tags = {"name": "Muri", "official_name": "Muri (AG)"}
    assert ox.pick_name(tags, official, "county") == "Muri (AG)"
    assert ox.pick_name(tags, official, "state") == "Muri"          # states keep name_tags
    assert ox.pick_name({"name": "Bissone"}, official, "county") == "Bissone"  # fallback
    stripped = _cfg(county_strip_prefixes=["Gemeinde "])
    assert ox.pick_name({"name": "Gemeinde Avers"}, stripped, "county") == "Avers"
    assert ox.pick_name({"name": "Gemeinde "}, stripped, "county") == "Gemeinde"
    assert ox.pick_name({"name": "Gemeinde Avers"}, stripped, "state") == "Gemeinde Avers"


def test_comparison_report(osm_file: Path, tmp_path: Path) -> None:
    # Reference: "Ouest" named "West", and "Nord" missing; a cache CSV with one
    # matching and one different county name.
    ref = tmp_path / "ref"
    square = {"type": "Polygon", "coordinates": [[[0, 40], [5, 40], [5, 50], [0, 50], [0, 40]]]}
    ox.write_pack(ref / "states" / "xxx.geojson", [
        {"type": "Feature", "properties": {"name": "West"}, "geometry": square}])
    ox.write_pack(ref / "counties" / "xxx_all_xxx.geojson", [
        {"type": "Feature", "properties": {"name": "Biel Bienne"},
         "geometry": {"type": "Polygon", "coordinates": [[[0, 40], [6.5, 40], [6.5, 45], [0, 45], [0, 40]]]}}])
    points = tmp_path / "location_gaps.csv"
    with points.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "lat", "lon", "country", "level", "known_value", "known_from"])
        w.writerow(["GC1", "42", "2", "Xland", "county", "Biel-Bienne", "cache"])
        w.writerow(["GC2", "48", "2", "Xland", "county", "Doppelheim", "cache"])
        w.writerow(["GC3", "48", "12", "Nachbarland", "county", "Elsewhere", "cache"])

    out = tmp_path / "out"
    meta = ox.run(_cfg(), osm_file, out, version=1, state_tolerance=0, county_tolerance=0,
                  compare_dir=ref, points_csv=points)
    report = Path(meta["report"]).read_text("utf-8")

    assert "- Ouest — same area as the reference's 'West' (100% overlap)" in report
    assert "- Est\n" in report                            # no false hint from the spelling
    assert "West → Ouest" in report                       # a point in "West" lands in "Ouest"
    assert "- Doppel (ES)\n" in report                    # county only in OSM
    assert "Spelled differently (1), reference → OSM:** Biel Bienne → Biel-Bienne" in report
    assert "Matched: 1;" in report
    assert "**The reference is larger (1)**" in report      # reaches 1.5° into the next county
    assert "| county | cache | 1 | 1 |" in report         # GC3 lies outside XX
    assert "county: Doppelheim → Doppel (OU) (1)" in report


def test_unknown_country_in_config(tmp_path: Path) -> None:
    config = tmp_path / "c.toml"
    config.write_text('[CH]\niso3 = "che"\nstate_level = 4\n', encoding="utf-8")
    assert ox.load_config(config, "ch").county_pack_name == "che_all_che.geojson"
    with pytest.raises(SystemExit, match="XX is not in"):
        ox.load_config(config, "XX")


def test_bundled_config_is_valid() -> None:
    cfg = ox.load_config(ox.DEFAULT_CONFIG, "CH")
    assert cfg.iso3 == "che" and cfg.state_level == 4 and cfg.county_level == 8
    assert cfg.county_pack_name == "che_all_che.geojson"


def test_match_names_keeps_accent_only_differences_apart() -> None:
    # Brugg and Brügg are two different Swiss municipalities.
    pairs = ox.match_names(["Brugg", "Brügg", "Basel-Stadt"], ["Brügg", "Brugg", "Basel Stadt"])
    assert pairs == {"Brugg": "Brugg", "Brügg": "Brügg", "Basel Stadt": "Basel-Stadt"}
    # Ambiguous after normalising: no guess.
    assert ox.match_names(["Brugg", "Brügg"], ["BRUGG"]) == {}
