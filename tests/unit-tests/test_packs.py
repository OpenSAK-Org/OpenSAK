# tests/unit-tests/test_packs.py — geo/packs.py: on-demand fetch and update check.

from __future__ import annotations

import hashlib
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


def _manifest(
    version: str = "2",
    pack_names: list[str] | None = None,
    baseline_names: list[str] | None = None,
) -> dict:
    pack_list = pack_names or ["aa.geojson", "bb.geojson"]
    return {
        "dataset_version": version,
        "baseline": {fn: {"version": version} for fn in (baseline_names or [])},
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

    def test_url_percent_encodes_special_characters(self, tmp_path: Path, monkeypatch):
        # Defense in depth: filenames should always be pre-sanitized by the
        # generator, but a raw space in a URL raises http.client.InvalidURL
        # (not an OSError/URLError) if it isn't encoded first.
        seen_urls: list[str] = []

        def _fake(url, **_k):
            seen_urls.append(url)
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        packs.fetch_pack("can_British Columbia.geojson", tmp_path)
        assert " " not in seen_urls[0]
        assert "British%20Columbia" in seen_urls[0]

    def test_invalid_url_error_returns_false_instead_of_crashing(self, tmp_path: Path, monkeypatch):
        # http.client.InvalidURL is not an OSError/URLError subclass — this is
        # exactly the exception that used to crash the whole QThread instead
        # of degrading gracefully to "this one file failed".
        import http.client

        def _raise(*_a, **_k):
            raise http.client.InvalidURL("URL can't contain control characters")

        monkeypatch.setattr("urllib.request.urlopen", _raise)
        result = packs.fetch_pack("bad name.geojson", tmp_path)
        assert result is False


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


# ── fetch_packs ───────────────────────────────────────────────────────────────

class TestFetchPacks:
    def test_downloads_missing_packs_in_parallel(self, tmp_path: Path, monkeypatch):
        def _fake(url, **_k):
            if "manifest.json" in url:
                return _json_resp(_manifest("1"))
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        dest = tmp_path / "counties"
        count = packs.fetch_packs(["aa.geojson", "bb.geojson"], dest)
        assert count == 2
        assert (dest / "aa.geojson").is_file()
        assert (dest / "bb.geojson").is_file()

    def test_skips_already_present_packs(self, tmp_path: Path, monkeypatch):
        dest = tmp_path / "counties"
        dest.mkdir()
        (dest / "aa.geojson").write_bytes(b"{}")
        calls: list[str] = []

        def _fake(url, **_k):
            calls.append(url)
            if "manifest.json" in url:
                return _json_resp(_manifest("1"))
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        count = packs.fetch_packs(["aa.geojson", "bb.geojson"], dest)
        assert count == 1
        assert not any("aa.geojson" in u for u in calls)
        assert any("bb.geojson" in u for u in calls)

    def test_empty_input_returns_zero_without_network_call(self, tmp_path: Path, monkeypatch):
        calls: list[str] = []

        def _fake(url, **_k):
            calls.append(url)
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        assert packs.fetch_packs([], tmp_path / "counties") == 0
        assert not calls

    def test_all_already_present_skips_reachability_probe(self, tmp_path: Path, monkeypatch):
        dest = tmp_path / "counties"
        dest.mkdir()
        (dest / "aa.geojson").write_bytes(b"{}")
        calls: list[str] = []

        def _fake(url, **_k):
            calls.append(url)
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        assert packs.fetch_packs(["aa.geojson"], dest) == 0
        assert not calls

    def test_unreachable_network_skips_whole_batch(self, tmp_path: Path, monkeypatch):
        # Issue #722, proposal 3: a fully blocked network must fail fast via
        # the short reachability probe rather than paying up to
        # DOWNLOAD_TIMEOUT seconds per pack for what would also fail.
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *_a, **_k: (_ for _ in ()).throw(URLError("blocked")),
        )
        dest = tmp_path / "counties"
        count = packs.fetch_packs(["aa.geojson", "bb.geojson"], dest)
        assert count == 0
        assert not (dest / "aa.geojson").is_file()
        assert not (dest / "bb.geojson").is_file()

    def test_probe_uses_short_timeout(self, tmp_path: Path, monkeypatch):
        seen_timeouts: list[int] = []

        def _fake(url, timeout=None, **_k):
            seen_timeouts.append(timeout)
            if "manifest.json" in url:
                return _json_resp(_manifest("1"))
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        packs.fetch_packs(["aa.geojson"], tmp_path / "counties")
        assert seen_timeouts[0] == packs.PROBE_TIMEOUT

    def test_progress_callback_reports_done_and_total(self, tmp_path: Path, monkeypatch):
        def _fake(url, **_k):
            if "manifest.json" in url:
                return _json_resp(_manifest("1"))
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        reported: list[tuple[int, int]] = []
        packs.fetch_packs(
            ["aa.geojson", "bb.geojson"], tmp_path / "counties",
            progress_cb=lambda d, t: reported.append((d, t)),
        )
        assert len(reported) == 2
        assert reported[-1] == (2, 2)
        assert all(t == 2 for _, t in reported)

    def test_progress_callback_called_once_on_probe_failure(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *_a, **_k: (_ for _ in ()).throw(URLError("down")),
        )
        reported: list[tuple[int, int]] = []
        packs.fetch_packs(
            ["aa.geojson", "bb.geojson"], tmp_path / "counties",
            progress_cb=lambda d, t: reported.append((d, t)),
        )
        assert reported == [(2, 2)]

    def test_deduplicates_filenames(self, tmp_path: Path, monkeypatch):
        calls: list[str] = []

        def _fake(url, **_k):
            calls.append(url)
            if "manifest.json" in url:
                return _json_resp(_manifest("1"))
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        count = packs.fetch_packs(["aa.geojson", "aa.geojson"], tmp_path / "counties")
        assert count == 1
        assert sum("aa.geojson" in u for u in calls) == 1  # manifest probe + one pack fetch

    def test_individual_pack_failure_does_not_fail_whole_batch(self, tmp_path: Path, monkeypatch):
        def _fake(url, **_k):
            if "manifest.json" in url:
                return _json_resp(_manifest("1"))
            if "bb.geojson" in url:
                raise URLError("no")
            return _FakeResp(b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake)
        dest = tmp_path / "counties"
        count = packs.fetch_packs(["aa.geojson", "bb.geojson"], dest)
        assert count == 1
        assert (dest / "aa.geojson").is_file()
        assert not (dest / "bb.geojson").is_file()


# ── fetch_baseline ────────────────────────────────────────────────────────────

class TestFetchBaseline:
    def test_creates_dest_dir_when_it_does_not_exist_yet(self, tmp_path: Path, monkeypatch):
        # Real first-run scenario: the app-data boundaries/ dir doesn't exist
        # at all yet. _fetch_file_atomic used to assume its caller already
        # created dest_dir (true for apply_update, called on an
        # already-initialized dir) — fetch_baseline is the first caller that
        # can hit a genuinely nonexistent directory.
        fresh_dir = tmp_path / "does" / "not" / "exist" / "yet"
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(b"{}"))
        manifest = _manifest("1", baseline_names=["world.geojson"])
        assert packs.fetch_baseline(fresh_dir, manifest=manifest) is True
        assert (fresh_dir / "boundaries.db").is_file()

    def test_downloads_boundaries_db_and_baseline_packs(self, tmp_path: Path, monkeypatch):
        calls: list[str] = []

        def _fake(url, **_k):
            calls.append(url)
            return _FakeResp(b"{}")

        manifest = _manifest("1", baseline_names=["world.geojson", "prt.geojson"])
        monkeypatch.setattr("urllib.request.urlopen", _fake)
        result = packs.fetch_baseline(tmp_path, manifest=manifest)

        assert result is True
        assert (tmp_path / "boundaries.db").is_file()
        assert (tmp_path / "countries" / "world.geojson").is_file()
        assert (tmp_path / "states" / "prt.geojson").is_file()
        assert any("boundaries.db" in u for u in calls)
        assert any("world.geojson" in u for u in calls)
        assert any("prt.geojson" in u for u in calls)

    def test_fetches_manifest_when_not_given(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResp(b"{}"))
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: _manifest("1", baseline_names=["world.geojson"]))
        assert packs.fetch_baseline(tmp_path) is True

    def test_no_manifest_returns_false(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(packs, "fetch_manifest", lambda **_k: None)
        assert packs.fetch_baseline(tmp_path) is False
        assert not (tmp_path / "boundaries.db").is_file()

    def test_boundaries_db_failure_returns_false_without_fetching_packs(self, tmp_path: Path, monkeypatch):
        calls: list[str] = []

        def _fake(url, **_k):
            calls.append(url)
            if "boundaries.db" in url:
                raise URLError("no")
            return _FakeResp(b"{}")

        manifest = _manifest("1", baseline_names=["world.geojson"])
        monkeypatch.setattr("urllib.request.urlopen", _fake)
        assert packs.fetch_baseline(tmp_path, manifest=manifest) is False
        assert not any("world.geojson" in u for u in calls)

    def test_state_pack_failure_does_not_fail_whole_call(self, tmp_path: Path, monkeypatch):
        def _fake(url, **_k):
            if "prt.geojson" in url:
                raise URLError("no")
            return _FakeResp(b"{}")

        manifest = _manifest("1", baseline_names=["world.geojson", "prt.geojson"])
        monkeypatch.setattr("urllib.request.urlopen", _fake)
        result = packs.fetch_baseline(tmp_path, manifest=manifest)

        assert result is True
        assert (tmp_path / "countries" / "world.geojson").is_file()
        assert not (tmp_path / "states" / "prt.geojson").is_file()


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

    def test_fetch_attempted_once_across_candidates_sharing_a_pack(self, tmp_path: Path, monkeypatch):
        # Issue #722: AlphaCounty and BetaCounty (candidates 1 and 2) both
        # reference missing.geojson — a single resolve() call must only
        # attempt the network fetch once, not once per candidate.
        _build_minimal_db(tmp_path)
        calls: list[str] = []

        def _fake_fetch(filename: str, dest_dir: Path, **_k: object) -> bool:
            calls.append(filename)
            return False

        monkeypatch.setattr(packs, "fetch_pack", _fake_fetch)
        s = BoundaryStore(tmp_path)
        resolver = TerritoryResolver(s)
        loc = resolver.resolve(0.5, 0.5)
        s.close()
        assert calls == ["missing.geojson"]
        assert loc.county is None

    def test_fetch_attempted_once_across_separate_resolve_calls(self, tmp_path: Path, monkeypatch):
        # Issue #722: a batch resolving many caches in the same missing
        # county must only try the network once for the whole batch, not
        # once per cache — this is the actual reported hang.
        _build_minimal_db(tmp_path)
        calls: list[str] = []

        def _fake_fetch(filename: str, dest_dir: Path, **_k: object) -> bool:
            calls.append(filename)
            return False

        monkeypatch.setattr(packs, "fetch_pack", _fake_fetch)
        s = BoundaryStore(tmp_path)
        resolver = TerritoryResolver(s)
        resolver.resolve(0.5, 0.5)
        resolver.resolve(0.5, 0.5)
        resolver.resolve(0.5, 0.5)
        s.close()
        assert calls == ["missing.geojson"]

    def test_fresh_store_retries_after_previous_store_failed(self, tmp_path: Path, monkeypatch):
        # Negative caching is scoped to a single BoundaryStore/run, not
        # persisted to disk — a later import batch (fresh store) should
        # still retry, in case the pack has since become available.
        _build_minimal_db(tmp_path)
        calls: list[str] = []

        def _fake_fetch(filename: str, dest_dir: Path, **_k: object) -> bool:
            calls.append(filename)
            return False

        monkeypatch.setattr(packs, "fetch_pack", _fake_fetch)
        s1 = BoundaryStore(tmp_path)
        TerritoryResolver(s1).resolve(0.5, 0.5)
        s1.close()
        s2 = BoundaryStore(tmp_path)
        TerritoryResolver(s2).resolve(0.5, 0.5)
        s2.close()
        assert calls == ["missing.geojson", "missing.geojson"]

    def test_corrupt_pack_after_successful_fetch_degrades_gracefully(self, tmp_path: Path, monkeypatch):
        # fetch_pack() can return True but the write is still bad in some
        # edge case (e.g. an HTML error page saved as if it were JSON) —
        # this must degrade to county=None on read, not crash, and must
        # also be cached negatively so it isn't re-read every time.
        _build_minimal_db(tmp_path)
        calls: list[str] = []

        def _fake_fetch(filename: str, dest_dir: Path, **_k: object) -> bool:
            calls.append(filename)
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / filename).write_bytes(b"not valid json")
            return True

        monkeypatch.setattr(packs, "fetch_pack", _fake_fetch)
        s = BoundaryStore(tmp_path)
        resolver = TerritoryResolver(s)
        loc = resolver.resolve(0.5, 0.5)
        s.close()
        assert loc.county is None
        assert calls == ["missing.geojson"]  # only attempted once (2 candidates share it)



# ── apply_update: baseline and all-or-nothing (#1226) ─────────────────────────


_SQ_A = [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]
_SQ_B = [[[5.0, 5.0], [6.0, 5.0], [6.0, 6.0], [5.0, 6.0], [5.0, 5.0]]]
_SQ_C = [[[9.0, 9.0], [9.5, 9.0], [9.5, 9.5], [9.0, 9.5], [9.0, 9.0]]]


def _pack(version: str, *squares: list) -> bytes:
    return json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"version": version},
             "geometry": {"type": "Polygon", "coordinates": sq}}
            for sq in squares
        ],
    }).encode()


def _state_db(path: Path, version: str, states: list[tuple[int, str, str, int]]) -> bytes:
    """A boundaries.db with *states* (id, name, pack, feature_index); returns its bytes."""
    db = sqlite3.connect(path)
    for layer in ("country", "state", "county"):
        db.execute(f"CREATE VIRTUAL TABLE rtree_{layer} USING rtree(id, min_lat, max_lat, min_lon, max_lon)")
        db.execute(f"CREATE TABLE region_{layer} (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                   "parent TEXT, pack TEXT NOT NULL, feature_index INTEGER NOT NULL, "
                   "poly_version INTEGER NOT NULL, is_bundled INTEGER NOT NULL)")
    db.executemany("INSERT INTO region_state VALUES (?, ?, NULL, ?, ?, 1, 1)", states)
    db.execute("CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER)")
    db.execute("INSERT INTO file_version VALUES ('dataset', NULL, NULL, ?)", (version,))
    db.commit()
    db.close()
    return path.read_bytes()


def _entry(data: bytes, version: str = "1") -> dict:
    return {"version": version, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _serve(monkeypatch, files: dict[str, bytes]) -> list[str]:
    """urlopen serving *files* by name (anything else fails); returns the
    list of requested names."""
    requested: list[str] = []

    def _fake(url, **_k):
        name = url.rsplit("/", 1)[1]
        requested.append(name)
        if name not in files:
            raise URLError("not found")
        return _FakeResp(files[name])

    monkeypatch.setattr("urllib.request.urlopen", _fake)
    return requested


@pytest.fixture()
def old_install(tmp_path: Path) -> Path:
    """An install with dataset 1: state "Old" in aaa.geojson, plus world.geojson."""
    data = tmp_path / "boundaries"
    data.mkdir()
    _state_db(data / "boundaries.db", "1", [(1, "Old", "aaa.geojson", 0)])
    (data / "states").mkdir()
    (data / "states" / "aaa.geojson").write_bytes(_pack("1", _SQ_A))
    (data / "countries").mkdir()
    (data / "countries" / "world.geojson").write_bytes(_pack("1", _SQ_C))
    return data


def _new_release(tmp_path: Path) -> tuple[dict[str, bytes], dict]:
    """Dataset 2: aaa.geojson changed (a feature added in front), bbb.geojson new."""
    files = {
        "boundaries.db": _state_db(tmp_path / "remote.db", "2", [
            (1, "Alpha", "aaa.geojson", 1), (2, "Beta", "bbb.geojson", 0)]),
        "aaa.geojson": _pack("2", _SQ_C, _SQ_A),
        "bbb.geojson": _pack("1", _SQ_B),
        "world.geojson": _pack("1", _SQ_C),
    }
    manifest = {
        "dataset_version": "2",
        "baseline": {n: _entry(files[n], "2" if n == "aaa.geojson" else "1")
                     for n in ("world.geojson", "aaa.geojson", "bbb.geojson")},
        "packs": {},
    }
    return files, manifest


class TestApplyUpdateBaseline:
    def test_new_and_changed_state_packs_arrive_with_the_db(self, tmp_path, old_install, monkeypatch):
        files, manifest = _new_release(tmp_path)
        requested = _serve(monkeypatch, files)

        updated = packs.apply_update(old_install, manifest)

        assert set(updated) == {"boundaries.db", "aaa.geojson", "bbb.geojson"}
        assert "world.geojson" not in requested  # unchanged: same sha256
        store = BoundaryStore(old_install)
        try:
            alpha, beta = store.region("state", 1), store.region("state", 2)
            assert alpha is not None and beta is not None
            assert (alpha.name, beta.name) == ("Alpha", "Beta")
            assert store.geometry("state", alpha)["coordinates"] == _SQ_A
            assert store.geometry("state", beta)["coordinates"] == _SQ_B
        finally:
            store.close()

    def test_missing_baseline_file_is_fetched(self, tmp_path, old_install, monkeypatch):
        files, manifest = _new_release(tmp_path)
        (old_install / "countries" / "world.geojson").unlink()
        _serve(monkeypatch, files)
        assert "world.geojson" in packs.apply_update(old_install, manifest)
        assert (old_install / "countries" / "world.geojson").read_bytes() == files["world.geojson"]

    def test_version_compared_when_manifest_has_no_sha256(self, tmp_path, old_install, monkeypatch):
        manifest = {"dataset_version": "2", "packs": {},
                    "baseline": {"aaa.geojson": {"version": "1"}, "world.geojson": {"version": "2"}}}
        requested = _serve(monkeypatch, {"boundaries.db": b"db", "world.geojson": _pack("2", _SQ_C)})
        updated = packs.apply_update(old_install, manifest)
        assert "aaa.geojson" not in requested  # same version
        assert "world.geojson" in updated

    def test_failed_baseline_file_leaves_everything_as_it_was(self, tmp_path, old_install, monkeypatch):
        files, manifest = _new_release(tmp_path)
        del files["bbb.geojson"]
        _serve(monkeypatch, files)
        before = {p: p.read_bytes() for p in old_install.rglob("*") if p.is_file()}

        assert packs.apply_update(old_install, manifest) == []

        after = {p: p.read_bytes() for p in old_install.rglob("*") if p.is_file()}
        assert after == before  # no file changed, no temp file or manifest left
        assert packs._local_dataset_version(old_install) == "1"  # offered again

    def test_sha256_mismatch_counts_as_failure(self, tmp_path, old_install, monkeypatch):
        files, manifest = _new_release(tmp_path)
        files["aaa.geojson"] = _pack("2", _SQ_B)  # not what the manifest says
        _serve(monkeypatch, files)
        assert packs.apply_update(old_install, manifest) == []
        assert (old_install / "states" / "aaa.geojson").read_bytes() == _pack("1", _SQ_A)

    def test_failed_db_download_changes_nothing(self, tmp_path, old_install, monkeypatch):
        files, manifest = _new_release(tmp_path)
        del files["boundaries.db"]
        _serve(monkeypatch, files)
        assert packs.apply_update(old_install, manifest) == []
        assert not (old_install / "states" / "bbb.geojson").exists()

    def test_failed_cached_county_pack_is_removed(self, tmp_path, old_install, monkeypatch):
        files, manifest = _new_release(tmp_path)
        counties = old_install / "counties"
        counties.mkdir()
        (counties / "aaa_counties.geojson").write_bytes(_pack("1", _SQ_A))
        manifest["packs"] = {"aaa_counties.geojson": {"version": "2"}}
        _serve(monkeypatch, files)  # the county pack isn't served

        updated = packs.apply_update(old_install, manifest)

        assert "boundaries.db" in updated
        assert not (counties / "aaa_counties.geojson").exists()  # fetched on demand later

    def test_boundaries_db_is_moved_into_place_last(self, tmp_path, old_install, monkeypatch):
        files, manifest = _new_release(tmp_path)
        _serve(monkeypatch, files)
        replaced: list[str] = []
        real_replace = packs.os.replace

        def _recording_replace(src, dst):
            replaced.append(Path(dst).name)
            real_replace(src, dst)

        monkeypatch.setattr(packs.os, "replace", _recording_replace)
        packs.apply_update(old_install, manifest)
        assert replaced[-2:] == ["boundaries.db", packs.MANIFEST_FILENAME]

    def test_progress_reported_from_the_calling_thread(self, tmp_path, old_install, monkeypatch):
        import threading

        files, manifest = _new_release(tmp_path)
        _serve(monkeypatch, files)
        threads: set[int] = set()
        packs.apply_update(old_install, manifest,
                           progress_cb=lambda _f: threads.add(threading.get_ident()))
        assert threads == {threading.get_ident()}


class TestStatePackFallbackFetch:
    def test_missing_state_pack_is_fetched_on_demand(self, tmp_path, monkeypatch):
        _state_db(tmp_path / "boundaries.db", "2", [(1, "Beta", "bbb.geojson", 0)])
        fetched: list[str] = []

        def _fake_fetch(filename: str, dest_dir: Path, **_k: object) -> bool:
            fetched.append(f"{dest_dir.name}/{filename}")
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / filename).write_bytes(_pack("1", _SQ_B))
            return True

        monkeypatch.setattr(packs, "fetch_pack", _fake_fetch)
        store = BoundaryStore(tmp_path)
        try:
            region = store.region("state", 1)
            assert region is not None
            assert store.geometry("state", region)["coordinates"] == _SQ_B
        finally:
            store.close()
        assert fetched == ["states/bbb.geojson"]
