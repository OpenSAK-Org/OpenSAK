# tests/unit-tests/test_geo.py — offline boundary engine (store + resolver).

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from opensak.geo import BoundaryStore, GeoLocation, TerritoryResolver

# Synthetic dataset (lon/lat squares & triangles), mirroring the real layout:
#   county layer — two same-bbox triangles (force Stage-2 PIP) + one far square
#                  (single bbox hit) ; state/country — one big covering square.
_TRI_LOWER = [[[0, 0], [2, 0], [0, 2], [0, 0]]]          # x + y <= 2
_TRI_UPPER = [[[2, 2], [0, 2], [2, 0], [2, 2]]]          # x + y >= 2
_FAR_SQUARE = [[[10, 10], [12, 10], [12, 12], [10, 12], [10, 10]]]
_BIG_SQUARE = [[[-1, -1], [13, -1], [13, 13], [-1, 13], [-1, -1]]]
# Isolated triangle: bbox [20,20,22,22] but polygon only covers the lower-left
# half. A point at (lat=21.5, lon=21.5) is inside the bbox but outside the
# triangle — the border-crossing bug. lon+lat=43>42 puts it above the hypotenuse.
_ISOLATED_TRI = [[[20, 20], [22, 20], [20, 22], [20, 20]]]


def _feature(layer: str, name: str, parent: str | None, polygon: Any, bbox: list[int]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"layer": layer, "name": name, "parent": parent,
                       "version": 1, "source": "test", "licence": "ODbL"},
        "bbox": bbox,
        "geometry": {"type": "Polygon", "coordinates": polygon},
    }


def _write_pack(path: Path, features: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")


def _build_boundaries(data_dir: Path) -> None:
    _write_pack(data_dir / "counties" / "test.geojson", [
        _feature("county", "Alpha County", "TL/TS", _TRI_LOWER, [0, 0, 2, 2]),
        _feature("county", "Beta County", "TL/TS", _TRI_UPPER, [0, 0, 2, 2]),
        _feature("county", "Gamma County", "TL/TS", _FAR_SQUARE, [10, 10, 12, 12]),
        _feature("county", "Border County", "TL/TS", _ISOLATED_TRI, [20, 20, 22, 22]),
    ])
    _write_pack(data_dir / "states" / "test.geojson",
                [_feature("state", "Teststate", "TL", _BIG_SQUARE, [-1, -1, 13, 13])])
    _write_pack(data_dir / "countries" / "test.geojson",
                [_feature("country", "Testland", None, _BIG_SQUARE, [-1, -1, 13, 13])])

    db = sqlite3.connect(data_dir / "boundaries.db")
    for layer in ("country", "state", "county"):
        db.execute(f"CREATE VIRTUAL TABLE rtree_{layer} USING rtree(id, min_lat, max_lat, min_lon, max_lon)")
        db.execute(f"CREATE TABLE region_{layer} (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                   "parent TEXT, pack TEXT NOT NULL, feature_index INTEGER NOT NULL, "
                   "poly_version INTEGER NOT NULL, is_bundled INTEGER NOT NULL)")
    db.execute("CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER)")

    # rtree rows are (id, min_lat, max_lat, min_lon, max_lon)
    db.executemany("INSERT INTO rtree_county VALUES (?, ?, ?, ?, ?)",
                   [(1, 0, 2, 0, 2), (2, 0, 2, 0, 2), (3, 10, 12, 10, 12), (4, 20, 22, 20, 22)])
    db.executemany("INSERT INTO region_county VALUES (?, ?, ?, ?, ?, ?, ?)", [
        (1, "Alpha County", "TL/TS", "test.geojson", 0, 1, 1),
        (2, "Beta County", "TL/TS", "test.geojson", 1, 1, 1),
        (3, "Gamma County", "TL/TS", "test.geojson", 2, 1, 1),
        (4, "Border County", "TL/TS", "test.geojson", 3, 1, 1),
    ])
    db.execute("INSERT INTO rtree_state VALUES (1, -1, 13, -1, 13)")
    db.execute("INSERT INTO region_state VALUES (1, 'Teststate', 'TL', 'test.geojson', 0, 1, 1)")
    db.execute("INSERT INTO rtree_country VALUES (1, -1, 13, -1, 13)")
    db.execute("INSERT INTO region_country VALUES (1, 'Testland', NULL, 'test.geojson', 0, 1, 1)")
    db.execute("INSERT INTO file_version VALUES ('dataset', NULL, NULL, 1)")
    db.commit()
    db.close()


@pytest.fixture()
def store(tmp_path: Path) -> Iterator[BoundaryStore]:
    _build_boundaries(tmp_path)
    s = BoundaryStore(tmp_path)
    yield s
    s.close()


# ── BoundaryStore ─────────────────────────────────────────────────────────────


class TestStore:
    def test_available(self, store: BoundaryStore):
        assert store.available() is True

    def test_available_false_when_missing(self, tmp_path: Path):
        assert BoundaryStore(tmp_path / "empty").available() is False

    def test_candidates_single_hit(self, store: BoundaryStore):
        assert store.candidates("county", 11, 11) == [3]

    def test_candidates_overlap(self, store: BoundaryStore):
        assert sorted(store.candidates("county", 0.5, 0.5)) == [1, 2]

    def test_candidates_none(self, store: BoundaryStore):
        assert store.candidates("county", 50, 50) == []

    def test_region_lookup(self, store: BoundaryStore):
        region = store.region("county", 1)
        assert region is not None
        assert region.name == "Alpha County"
        assert region.parent == "TL/TS"

    def test_region_missing(self, store: BoundaryStore):
        assert store.region("county", 999) is None

    def test_unknown_layer_rejected(self, store: BoundaryStore):
        with pytest.raises(ValueError):
            store.candidates("planet", 0, 0)

    def test_geometry_lazy_loaded(self, store: BoundaryStore):
        region = store.region("county", 3)
        assert region is not None
        geom = store.geometry("county", region)
        assert geom["type"] == "Polygon"


# ── TerritoryResolver ─────────────────────────────────────────────────────────


class TestResolver:
    def test_single_hit_fills_all_layers(self, store: BoundaryStore):
        loc = TerritoryResolver(store).resolve(11, 11)
        assert loc == GeoLocation("Testland", "Teststate", "Gamma County")

    def test_overlap_disambiguated_by_polygon_lower(self, store: BoundaryStore):
        # (0.5, 0.5) sits in both county bboxes but only inside the lower triangle.
        loc = TerritoryResolver(store).resolve(0.5, 0.5)
        assert loc.county == "Alpha County"

    def test_overlap_disambiguated_by_polygon_upper(self, store: BoundaryStore):
        loc = TerritoryResolver(store).resolve(1.5, 1.5)
        assert loc.county == "Beta County"

    def test_ocean_returns_empty(self, store: BoundaryStore):
        assert TerritoryResolver(store).resolve(50, 50) == GeoLocation(None, None, None)

    def test_state_country_without_county(self, store: BoundaryStore):
        # A point inside the big state/country square but outside any county bbox.
        loc = TerritoryResolver(store).resolve(8, 8)
        assert loc == GeoLocation("Testland", "Teststate", None)

    def test_single_bbox_hit_outside_polygon_returns_none(self, store: BoundaryStore):
        # Regression for border-crossing bug: the R-Tree bbox of Border County
        # covers (lat=21.5, lon=21.5) and it is the only candidate, but the point
        # sits above the triangle's hypotenuse (lon+lat=43>42) — outside the polygon.
        # Before the fix, the single-hit shortcut returned "Border County" without
        # checking the polygon; now it must return None.
        loc = TerritoryResolver(store).resolve(21.5, 21.5)
        assert loc.county is None

    def test_single_bbox_hit_inside_polygon_still_resolves(self, store: BoundaryStore):
        # Counterpart: a point truly inside Border County's triangle must still resolve.
        loc = TerritoryResolver(store).resolve(20.5, 20.5)
        assert loc.county == "Border County"
