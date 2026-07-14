# tests/unit-tests/test_verify_release.py — tools/boundaries/verify_release.py checks.

import hashlib
import json
import sqlite3
from io import BytesIO
from pathlib import Path
from urllib.error import URLError
from urllib.parse import unquote

import pytest

from tools.boundaries import verify_release as vr

_SQUARE = [[[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]]
# Self-intersecting "bowtie" ring — shapely reports it as an invalid geometry.
_BOWTIE = [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]

_INSIDE = (1.0, 1.0, "Testland")


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._data = BytesIO(payload)

    def read(self) -> bytes:
        return self._data.read()

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_a: object) -> None:
        return


def _feature(layer: str, name: str, parent: str | None, coords: list = _SQUARE) -> dict:
    return {
        "type": "Feature",
        "properties": {"layer": layer, "name": name, "parent": parent, "version": 1,
                       "source": "test", "licence": "ODbL"},
        "bbox": [0, 0, 2, 2],
        "geometry": {"type": "Polygon", "coordinates": coords},
    }


def _write_fc(path: Path, features: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")


def _digest(path: Path) -> dict:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _build_release(root: Path, county_coords: list = _SQUARE) -> dict:
    """A tiny, manifest-shaped release: one country/state/county, resolvable at (1, 1)."""
    _write_fc(root / "countries" / "world.geojson", [_feature("country", "Testland", None)])
    _write_fc(root / "states" / "usa.geojson", [_feature("state", "Teststate", "TL")])
    _write_fc(root / "counties" / "usa_county.geojson",
              [_feature("county", "Testcounty", "TL/TS", county_coords)])

    db = sqlite3.connect(root / "boundaries.db")
    for layer in ("country", "state", "county"):
        db.execute(f"CREATE VIRTUAL TABLE rtree_{layer} USING rtree(id, min_lat, max_lat, min_lon, max_lon)")
        db.execute(f"CREATE TABLE region_{layer} (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                   "parent TEXT, pack TEXT NOT NULL, feature_index INTEGER NOT NULL, "
                   "poly_version INTEGER NOT NULL, is_bundled INTEGER NOT NULL)")
    db.execute("CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER)")
    db.execute("INSERT INTO rtree_country VALUES (1, 0, 2, 0, 2)")
    db.execute("INSERT INTO region_country VALUES (1, 'Testland', NULL, 'world.geojson', 0, 1, 1)")
    db.execute("INSERT INTO rtree_state VALUES (1, 0, 2, 0, 2)")
    db.execute("INSERT INTO region_state VALUES (1, 'Teststate', 'TL', 'usa.geojson', 0, 1, 1)")
    db.execute("INSERT INTO rtree_county VALUES (1, 0, 2, 0, 2)")
    db.execute("INSERT INTO region_county VALUES (1, 'Testcounty', 'TL/TS', 'usa_county.geojson', 0, 1, 1)")
    db.execute("INSERT INTO file_version VALUES ('dataset', NULL, NULL, 1)")
    db.commit()
    db.close()

    manifest = {
        "dataset_version": "1",
        "boundaries_db": _digest(root / "boundaries.db"),
        "baseline": {
            "world.geojson": {"version": "1", **_digest(root / "countries" / "world.geojson")},
            "usa.geojson": {"version": "1", **_digest(root / "states" / "usa.geojson")},
        },
        "packs": {
            "usa_county.geojson": {"version": "1", **_digest(root / "counties" / "usa_county.geojson")},
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


@pytest.fixture(autouse=True)
def _smoke_inside_fixture(monkeypatch):
    # The real script checks real-world coordinates; point it at our synthetic
    # square instead so tests don't depend on network-fetched production data.
    monkeypatch.setattr(vr, "_SMOKE_COORDS", (_INSIDE,))


# ── --data-dir (local) ──────────────────────────────────────────────────────

class TestLocalDataDir:
    def test_all_green(self, tmp_path: Path) -> None:
        _build_release(tmp_path)
        assert vr.verify(tmp_path) == []

    def test_missing_manifest(self, tmp_path: Path) -> None:
        errors = vr.verify(tmp_path)
        assert len(errors) == 1
        assert "manifest.json not found" in errors[0]

    def test_missing_dataset_version(self, tmp_path: Path) -> None:
        _build_release(tmp_path)
        manifest_path = tmp_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        del manifest["dataset_version"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        errors = vr.verify(tmp_path)
        assert any("dataset_version" in e for e in errors)

    def test_checksum_mismatch_same_size(self, tmp_path: Path) -> None:
        _build_release(tmp_path)
        path = tmp_path / "states" / "usa.geojson"
        # Same length, still valid JSON/UTF-8 — isolates the checksum check
        # from the JSON/geometry checks, which have their own tests.
        path.write_bytes(path.read_bytes().replace(b"Teststate", b"TESTSTATE"))
        errors = vr.verify(tmp_path)
        assert any("usa.geojson" in e and "sha256 mismatch" in e for e in errors)

    def test_checksum_mismatch_size_changed(self, tmp_path: Path) -> None:
        _build_release(tmp_path)
        (tmp_path / "states" / "usa.geojson").write_bytes(b"tampered")
        errors = vr.verify(tmp_path)
        assert any("usa.geojson" in e and "size mismatch" in e for e in errors)

    def test_missing_asset(self, tmp_path: Path) -> None:
        _build_release(tmp_path)
        (tmp_path / "counties" / "usa_county.geojson").unlink()
        errors = vr.verify(tmp_path)
        assert any("usa_county.geojson" in e and "not found" in e for e in errors)

    def test_feature_index_out_of_bounds(self, tmp_path: Path) -> None:
        _build_release(tmp_path)
        db = sqlite3.connect(tmp_path / "boundaries.db")
        db.execute("UPDATE region_county SET feature_index = 5")
        db.commit()
        db.close()
        errors = vr.verify(tmp_path)
        assert any("feature_index" in e and "out of bounds" in e for e in errors)

    def test_row_count_mismatch(self, tmp_path: Path) -> None:
        _build_release(tmp_path)
        db = sqlite3.connect(tmp_path / "boundaries.db")
        db.execute("INSERT INTO rtree_country VALUES (2, 10, 12, 10, 12)")
        db.commit()
        db.close()
        errors = vr.verify(tmp_path)
        assert any("rtree_country" in e and "region_country" in e for e in errors)

    def test_invalid_geometry(self, tmp_path: Path) -> None:
        _build_release(tmp_path, county_coords=_BOWTIE)
        errors = vr.verify(tmp_path)
        assert any("invalid geometry" in e for e in errors)

    def test_resolver_returns_none_when_smoke_coords_miss(self, tmp_path: Path, monkeypatch) -> None:
        _build_release(tmp_path)
        monkeypatch.setattr(vr, "_SMOKE_COORDS", ((99.0, 99.0, "Nowhere"),))
        errors = vr.verify(tmp_path)
        assert len(errors) == 1
        assert "resolver returned no country" in errors[0]


# ── remote (network-mocked) ─────────────────────────────────────────────────

def _fake_urlopen_factory(src_root: Path, manifest: dict):
    def _fake(url, *_a, **_k):
        name = unquote(url.rsplit("/", 1)[-1])
        if name == "manifest.json":
            return _FakeResp(json.dumps(manifest).encode())
        for subdir in ("", "countries", "states", "counties"):
            candidate = (src_root / subdir / name) if subdir else (src_root / name)
            if candidate.is_file():
                return _FakeResp(candidate.read_bytes())
        raise URLError(f"no such asset: {name}")
    return _fake


class TestRemoteRelease:
    def test_all_green_over_network(self, tmp_path: Path, monkeypatch) -> None:
        manifest = _build_release(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_factory(tmp_path, manifest))
        assert vr.verify(None) == []

    def test_manifest_fetch_failure(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *_a, **_k: (_ for _ in ()).throw(URLError("down")),
        )
        errors = vr.verify(None)
        assert len(errors) == 1
        assert "failed to fetch manifest" in errors[0]


# ── CLI ──────────────────────────────────────────────────────────────────────

class TestMain:
    def test_exits_nonzero_on_failure(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr("sys.argv", ["verify_release.py", "--data-dir", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            vr.main()
        assert exc.value.code == 1
        assert "FAILED" in capsys.readouterr().out

    def test_exits_zero_on_success(self, tmp_path: Path, monkeypatch, capsys) -> None:
        _build_release(tmp_path)
        monkeypatch.setattr("sys.argv", ["verify_release.py", "--data-dir", str(tmp_path)])
        vr.main()
        assert "OK" in capsys.readouterr().out
