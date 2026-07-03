# tests/unit-tests/test_geo.py — offline boundary engine (store + resolver).

from collections.abc import Iterator
from pathlib import Path

import pytest

from opensak.geo import BoundaryStore, GeoLocation, TerritoryResolver
from opensak.geo import store as geo_store
from tests.data import build_boundary_test_data


@pytest.fixture()
def store(tmp_path: Path) -> Iterator[BoundaryStore]:
    build_boundary_test_data(tmp_path)
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


class TestDefaultDataDir:
    def test_env_override_wins(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("OPENSAK_BOUNDARIES_DIR", str(tmp_path))
        assert geo_store.default_data_dir() == tmp_path

    def test_falls_back_to_app_data_dir(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("OPENSAK_BOUNDARIES_DIR", raising=False)
        monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
        assert geo_store.default_data_dir() == tmp_path / "boundaries"


class TestFrozenBundleDir:
    def test_none_when_not_frozen(self, monkeypatch):
        monkeypatch.delattr(geo_store.sys, "frozen", raising=False)
        assert geo_store._frozen_bundle_dir() is None

    def test_none_when_frozen_but_no_boundaries_db(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(geo_store.sys, "frozen", True, raising=False)
        monkeypatch.setattr(geo_store.sys, "_MEIPASS", str(tmp_path), raising=False)
        assert geo_store._frozen_bundle_dir() is None

    def test_returns_bundle_dir_when_present(self, tmp_path: Path, monkeypatch):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "boundaries.db").write_bytes(b"")
        monkeypatch.setattr(geo_store.sys, "frozen", True, raising=False)
        monkeypatch.setattr(geo_store.sys, "_MEIPASS", str(tmp_path), raising=False)
        assert geo_store._frozen_bundle_dir() == tmp_path / "data"


class TestEnsureBaselineSeeded:
    def test_noop_when_already_present(self, tmp_path: Path, monkeypatch):
        (tmp_path / "boundaries.db").write_bytes(b"existing")
        monkeypatch.setattr(geo_store, "_frozen_bundle_dir", lambda: (_ for _ in ()).throw(AssertionError))
        geo_store.ensure_baseline_seeded(tmp_path)  # must not touch either fallback
        assert (tmp_path / "boundaries.db").read_bytes() == b"existing"

    def test_copies_from_frozen_bundle_when_present(self, tmp_path: Path, monkeypatch):
        bundle = tmp_path / "bundle"
        (bundle / "countries").mkdir(parents=True)
        (bundle / "states").mkdir(parents=True)
        (bundle / "boundaries.db").write_bytes(b"db")
        (bundle / "countries" / "world.geojson").write_bytes(b"world")
        (bundle / "states" / "prt.geojson").write_bytes(b"prt")

        dest = tmp_path / "dest"
        monkeypatch.setattr(geo_store, "_frozen_bundle_dir", lambda: bundle)
        geo_store.ensure_baseline_seeded(dest)

        assert (dest / "boundaries.db").read_bytes() == b"db"
        assert (dest / "countries" / "world.geojson").read_bytes() == b"world"
        assert (dest / "states" / "prt.geojson").read_bytes() == b"prt"

    def test_falls_back_to_network_when_not_bundled(self, tmp_path: Path, monkeypatch):
        calls: list[Path] = []
        monkeypatch.setattr(geo_store, "_frozen_bundle_dir", lambda: None)

        def _fake_fetch_baseline(data_dir: Path, manifest: dict | None = None) -> bool:
            calls.append(data_dir)
            return True

        import opensak.geo.packs as packs
        monkeypatch.setattr(packs, "fetch_baseline", _fake_fetch_baseline)
        geo_store.ensure_baseline_seeded(tmp_path)
        assert calls == [tmp_path]


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
