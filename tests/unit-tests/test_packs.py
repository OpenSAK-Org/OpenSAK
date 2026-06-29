# tests/unit-tests/test_packs.py — geo/packs.py: on-demand fetch and update check.

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from urllib.error import URLError

import pytest

import opensak.geo.packs as packs
from opensak.geo import BoundaryStore, GeoLocation, TerritoryResolver


# ── Helpers ───────────────────────────────────────────────────────────────────

class _FakeResp:
    # Minimal response that urllib.request.urlopen returns as a context manager.

    def __init__(self, payload: bytes) -> None:
        self._data = BytesIO(payload)

    def read(self) -> bytes:
        return self._data.read()

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_a: object) -> None:
        return


def _json_resp(obj: object) -> _FakeResp:
    return _FakeResp(json.dumps(obj).encode())


def _manifest(version: str = "2", pack_names: list[str] | None = None) -> dict:
    pack_list = pack_names or ["aa.geojson", "bb.geojson"]
    return {
        "dataset_version": version,
        "packs": {fn: {"version": version} for fn in pack_list},
    }


def _make_geojson(version: str = "1") -> bytes:
    fc = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"version": version}, "geometry": None}],
    }
    return json.dumps(fc).encode()


def _make_boundaries_db(data_dir: Path, version: str = "1") -> None:
    db = sqlite3.connect(data_dir / "boundaries.db")
    db.execute("CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER)")
    db.execute("INSERT INTO file_version VALUES ('dataset', NULL, NULL, ?)", (version,))
    db.commit()
    db.close()


# ── fetch_manifest ────────────────────────────────────────────────────────────

class TestFetchManifest:
    def test_success(self, monkeypatch):
        payload = _manifest("42")
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _json_resp(payload))
        result = packs.fetch_manifest()
        assert result == payload

    def test_network_error(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: (_ for _ in ()).throw(URLError("down")))
        assert packs.fetch_manifest() is None

    def test_bad_json(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(b"not json"))
        assert packs.fetch_manifest() is None


# ── fetch_pack ────────────────────────────────────────────────────────────────

class TestFetchPack:
    def test_writes_file_atomically(self, tmp_path: Path, monkeypatch):
        data = _make_geojson()
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(data))
        dest = tmp_path / "counties"
        assert packs.fetch_pack("prt.geojson", dest)
        assert (dest / "prt.geojson").read_bytes() == data

    def test_creates_missing_dest_dir(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(b"{}"))
        dest = tmp_path / "a" / "b" / "counties"
        packs.fetch_pack("x.geojson", dest)
        assert dest.is_dir()

    def test_network_error_returns_false(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: (_ for _ in ()).throw(URLError("no")))
        result = packs.fetch_pack("prt.geojson", tmp_path)
        assert result is False
        assert not (tmp_path / "prt.geojson").is_file()

    def test_url_contains_filename(self, tmp_path: Path, monkeypatch):
        seen_urls: list[str] = []

        def _fake(url, **_k):
            seen_urls.append(url)
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        packs.fetch_pack("prt.geojson", tmp_path)
        assert "prt.geojson" in seen_urls[0]


# ── fetch_all ─────────────────────────────────────────────────────────────────

class TestFetchAll:
    def test_downloads_missing_packs(self, tmp_path: Path, monkeypatch):
        calls: list[str] = []

        def _fake(url, **_k):
            calls.append(url)
            return _FakeResp(b"{}")

        manifest = _manifest("1", ["aa.geojson", "bb.geojson"])
        # aa.geojson already present, bb.geojson missing
        (tmp_path / "counties").mkdir()
        (tmp_path / "counties" / "aa.geojson").write_bytes(b"{}")
        monkeypatch.setattr("urllib.request.urlopen", _fake)
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: manifest)
        count = packs.fetch_all(tmp_path)
        assert count == 1
        assert any("bb.geojson" in u for u in calls)
        assert not any("aa.geojson" in u for u in calls)

    def test_progress_callback(self, tmp_path: Path, monkeypatch):
        reported: list[tuple[int, int]] = []
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(b"{}"))
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: _manifest("1", ["x.geojson"]))
        packs.fetch_all(tmp_path, progress_cb=lambda d, t: reported.append((d, t)))
        assert reported == [(1, 1)]

    def test_no_manifest_returns_zero(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: None)
        assert packs.fetch_all(tmp_path) == 0


# ── check_update ──────────────────────────────────────────────────────────────

class TestCheckUpdate:
    def test_newer_available(self, tmp_path: Path, monkeypatch):
        _make_boundaries_db(tmp_path, version="1")
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: _manifest("2"))
        newer, m = packs.check_update(tmp_path, force=True)
        assert newer is True
        assert m is not None

    def test_same_version_not_newer(self, tmp_path: Path, monkeypatch):
        _make_boundaries_db(tmp_path, version="2")
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: _manifest("2"))
        newer, _ = packs.check_update(tmp_path, force=True)
        assert newer is False

    def test_network_error(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: None)
        newer, m = packs.check_update(tmp_path, force=True)
        assert newer is False
        assert m is None

    def test_throttled_skips_check(self, tmp_path: Path, monkeypatch):
        # write a recent manifest (mtime = now)
        mf = tmp_path / packs.MANIFEST_FILENAME
        mf.write_text("{}", encoding="utf-8")
        fetched: list[bool] = []

        def _mark_fetched(**_k: object) -> dict:
            fetched.append(True)
            return _manifest("99")

        monkeypatch.setattr(packs, "fetch_manifest", _mark_fetched)
        newer, _ = packs.check_update(tmp_path, force=False)
        assert newer is False
        assert not fetched  # throttled — fetch never called

    def test_force_bypasses_throttle(self, tmp_path: Path, monkeypatch):
        mf = tmp_path / packs.MANIFEST_FILENAME
        mf.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: _manifest("99"))
        _make_boundaries_db(tmp_path, version="1")
        newer, _ = packs.check_update(tmp_path, force=True)
        assert newer is True

    def test_stale_manifest_triggers_check(self, tmp_path: Path, monkeypatch):
        mf = tmp_path / packs.MANIFEST_FILENAME
        mf.write_text("{}", encoding="utf-8")
        old_time = time.time() - packs.THROTTLE_SECONDS - 10
        import os; os.utime(mf, (old_time, old_time))
        _make_boundaries_db(tmp_path, version="1")
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: _manifest("2"))
        newer, _ = packs.check_update(tmp_path, force=False)
        assert newer is True


# ── apply_update ──────────────────────────────────────────────────────────────

class TestApplyUpdate:
    def test_updates_boundaries_db(self, tmp_path: Path, monkeypatch):
        _make_boundaries_db(tmp_path, version="1")
        new_db = b"new_db_content"
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(new_db))
        updated = packs.apply_update(tmp_path, _manifest("2"))
        assert "boundaries.db" in updated

    def test_updates_cached_pack_when_version_changed(self, tmp_path: Path, monkeypatch):
        counties = tmp_path / "counties"
        counties.mkdir()
        # old version 1 pack already cached
        (counties / "aa.geojson").write_bytes(_make_geojson(version="1"))
        manifest = {"dataset_version": "2", "packs": {"aa.geojson": {"version": "2"}}}
        new_content = _make_geojson(version="2")
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(new_content))
        updated = packs.apply_update(tmp_path, manifest)
        assert "aa.geojson" in updated
        # verify new content written
        fc = json.loads((counties / "aa.geojson").read_bytes())
        assert fc["features"][0]["properties"]["version"] == "2"

    def test_skips_uncached_pack(self, tmp_path: Path, monkeypatch):
        # bb.geojson is NOT cached locally — should not be downloaded
        calls: list[str] = []
        manifest = {"dataset_version": "2", "packs": {"bb.geojson": {"version": "2"}}}

        def _fake_open(url: str, **_k: object) -> _FakeResp:
            calls.append(url)
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake_open)
        packs.apply_update(tmp_path, manifest)
        assert not any("bb.geojson" in u for u in calls)

    def test_saves_manifest(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(b"{}"))
        manifest = _manifest("3")
        packs.apply_update(tmp_path, manifest)
        saved = json.loads((tmp_path / packs.MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert saved["dataset_version"] == "3"

    def test_progress_callback(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(b"{}"))
        reported: list[str] = []
        packs.apply_update(tmp_path, _manifest("2"), progress_cb=reported.append)
        assert "boundaries.db" in reported


# ── BoundaryStore on-demand fetch ─────────────────────────────────────────────

def _build_minimal_db(data_dir: Path) -> None:
    # Minimal boundaries.db with a single county that requires a pack.
    db = sqlite3.connect(data_dir / "boundaries.db")
    db.execute("CREATE VIRTUAL TABLE rtree_county USING rtree(id, min_lat, max_lat, min_lon, max_lon)")
    db.execute("CREATE TABLE region_county (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
               "parent TEXT, pack TEXT NOT NULL, feature_index INTEGER NOT NULL, "
               "poly_version INTEGER NOT NULL, is_bundled INTEGER NOT NULL)")
    # Two overlapping boxes → Stage-2 PIP needed → pack will be loaded
    db.executemany("INSERT INTO rtree_county VALUES (?, ?, ?, ?, ?)",
                   [(1, 0, 2, 0, 2), (2, 0, 2, 0, 2)])
    db.executemany("INSERT INTO region_county VALUES (?, ?, ?, ?, ?, ?, ?)", [
        (1, "AlphaCounty", None, "missing.geojson", 0, 1, 0),
        (2, "BetaCounty",  None, "missing.geojson", 1, 1, 0),
    ])
    for layer in ("state", "country"):
        db.execute(f"CREATE VIRTUAL TABLE rtree_{layer} USING rtree(id, min_lat, max_lat, min_lon, max_lon)")
        db.execute(f"CREATE TABLE region_{layer} (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                   "parent TEXT, pack TEXT NOT NULL, feature_index INTEGER NOT NULL, "
                   "poly_version INTEGER NOT NULL, is_bundled INTEGER NOT NULL)")
    db.execute("CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER)")
    db.commit()
    db.close()


_TRI_LOWER = [[[0.0, 0.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0]]]
_TRI_UPPER = [[[2.0, 2.0], [0.0, 2.0], [2.0, 0.0], [2.0, 2.0]]]


def _make_two_triangle_pack() -> bytes:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"version": "1"}, "geometry": {"type": "Polygon", "coordinates": _TRI_LOWER}},
            {"type": "Feature", "properties": {"version": "1"}, "geometry": {"type": "Polygon", "coordinates": _TRI_UPPER}},
        ],
    }
    return json.dumps(fc).encode()


@pytest.fixture()
def minimal_store(tmp_path: Path) -> Iterator[BoundaryStore]:
    _build_minimal_db(tmp_path)
    s = BoundaryStore(tmp_path)
    yield s
    s.close()


class TestOnDemandFetch:
    def test_fetch_called_when_pack_missing(self, tmp_path: Path, monkeypatch):
        _build_minimal_db(tmp_path)
        fetched: list[str] = []

        def _fake_fetch(filename: str, dest_dir: Path, **_k: object) -> bool:
            fetched.append(filename)
            pack = _make_two_triangle_pack()
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / filename).write_bytes(pack)
            return True

        monkeypatch.setattr(packs, "fetch_pack", _fake_fetch)
        s = BoundaryStore(tmp_path)
        resolver = TerritoryResolver(s)
        # (0.5, 0.5) is in the lower triangle → needs Stage-2 PIP → pack fetched
        loc = resolver.resolve(0.5, 0.5)
        s.close()
        assert "missing.geojson" in fetched
        assert loc.county == "AlphaCounty"

    def test_fetch_failure_degrades_gracefully(self, tmp_path: Path, monkeypatch):
        # When fetch fails, county = None; no exception raised.
        _build_minimal_db(tmp_path)
        monkeypatch.setattr(packs, "fetch_pack", lambda *_a, **_k: False)
        s = BoundaryStore(tmp_path)
        resolver = TerritoryResolver(s)
        loc = resolver.resolve(0.5, 0.5)
        s.close()
        assert loc.county is None  # coarser layers still resolved (state/country are empty here)
