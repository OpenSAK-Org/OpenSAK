# tests/unit-tests/test_gsak_to_opensak.py — GSAK boundary converter output contract.

import json
import sqlite3
import zipfile
from pathlib import Path

from tools.boundaries import gsak_to_opensak as conv

_SQUARE = "1.0,1.0\n1.0,2.0\n2.0,2.0\n2.0,1.0\n1.0,1.0\n"


def _write_bb_db3(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE Version (Type, Country, State, Version integer);
        CREATE TABLE bb_country (File, Country, MaxLat real, MinLat real, MaxLon real, MinLon real);
        CREATE TABLE bb_state (Country, File, MaxLat real, MinLat real, MaxLon real, MinLon real, Sname);
        CREATE TABLE bb_county (Country, State, File, MaxLat real, MinLat real, MaxLon real, MinLon real, Cname);
        """
    )
    db.execute("INSERT INTO Version VALUES ('c', '', '', 1)")
    db.execute("INSERT INTO Version VALUES ('s', 'usa', '', 2)")
    db.execute("INSERT INTO Version VALUES ('y', 'usa', 'california', 3)")
    db.execute("INSERT INTO Version VALUES ('y', 'usa', 'texas', 5)")
    db.execute("INSERT INTO bb_country VALUES ('1', 'United States', 2.0, 1.0, 2.0, 1.0)")
    db.execute(
        "INSERT INTO bb_state VALUES ('usa', '1', 2.0, 1.0, 2.0, 1.0, 'California')"
    )
    db.execute(
        "INSERT INTO bb_county VALUES ('usa', 'california', '1', 2.0, 1.0, 2.0, 1.0, 'Alpha County')"
    )
    db.execute(
        "INSERT INTO bb_county VALUES ('usa', 'texas', '2', 2.0, 1.0, 2.0, 1.0, 'Beta County')"
    )
    db.commit()
    db.close()


def _write_county_zip(path: Path, file_id: str, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{file_id}.txt", f"# GsakName={name}\n{_SQUARE}")


def _write_country_zip(path: Path, file_id: str, name: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(file_id, f"# GsakName={name}\n{_SQUARE}")


def _write_state_zip(path: Path, file_id: str, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{file_id}.txt", f"# GsakName={name}\n{_SQUARE}")


def test_county_packs_are_flat_and_manifest_matches_real_versions(tmp_path: Path) -> None:
    bb_path = tmp_path / "bb.db3"
    _write_bb_db3(bb_path)
    _write_country_zip(tmp_path / "country_v1.zip", "1", "United States")
    _write_state_zip(tmp_path / "states" / "usa_v2.zip", "1", "California")
    _write_county_zip(tmp_path / "counties" / "usa" / "california_v3.zip", "1", "Alpha County")
    _write_county_zip(tmp_path / "counties" / "usa" / "texas_v5.zip", "2", "Beta County")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _run(bb_path, tmp_path, out_dir)

    counties_dir = out_dir / "counties"
    packs = sorted(p.name for p in counties_dir.iterdir())
    assert packs == ["usa_california.geojson", "usa_texas.geojson"]
    # No nested per-country subdirectory should be created.
    assert all(p.is_file() for p in counties_dir.iterdir())

    db = sqlite3.connect(out_dir / "boundaries.db")
    db.row_factory = sqlite3.Row
    rows = {r["name"]: r["pack"] for r in db.execute("SELECT name, pack FROM region_county")}
    assert rows == {"Alpha County": "usa_california.geojson", "Beta County": "usa_texas.geojson"}
    assert all("/" not in pack for pack in rows.values())

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["dataset_version"] == "1"
    assert manifest["baseline"]["world.geojson"]["version"] == "1"
    assert manifest["baseline"]["usa.geojson"]["version"] == "2"
    assert manifest["packs"]["usa_california.geojson"]["version"] == "3"
    assert manifest["packs"]["usa_texas.geojson"]["version"] == "5"


def test_simplify_preserves_shape_when_tolerance_is_zero() -> None:
    # tolerance=0 skips Douglas-Peucker, but the result still round-trips through
    # shapely for the validity check (counties rely on this — see the comment
    # on _simplify), so compare geometrically rather than by raw dict equality.
    from shapely.geometry import shape

    geom: dict[str, object] = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    result = conv._simplify(geom, 0.0)
    assert shape(result).equals(shape(geom))


def test_simplify_repairs_self_intersection_from_preserve_topology(monkeypatch) -> None:
    # preserve_topology=True doesn't fully guarantee validity on complex
    # multi-ring geometries (see gsak_to_opensak.py's _simplify comment) — force
    # that failure mode with a bowtie (classic self-intersecting polygon) to
    # confirm the buffer(0) repair kicks in and always yields valid output.
    from shapely.geometry import Polygon

    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
    assert not bowtie.is_valid

    class _FakeShape:
        def simplify(self, tolerance: float, preserve_topology: bool) -> Polygon:
            return bowtie

    monkeypatch.setattr(conv, "_shp_shape", lambda geom: _FakeShape())

    fake_geom: dict[str, object] = {"type": "Polygon", "coordinates": [[[0, 0]]]}
    result = conv._simplify(fake_geom, 1.0)
    from shapely.geometry import shape

    assert shape(result).is_valid


def test_county_output_is_validity_repaired(tmp_path: Path) -> None:
    # Real GSAK county rings are self-intersecting for ~6% of real counties —
    # not something simplification introduces, present in the raw parsed data
    # itself. Counties are never Douglas-Peucker simplified, but must still go
    # through the same validity repair as the baseline layers (see _simplify).
    from shapely.geometry import shape

    bb_path = tmp_path / "bb.db3"
    _write_bb_db3(bb_path)
    _write_country_zip(tmp_path / "country_v1.zip", "1", "United States")
    # Bowtie ring (lat,lon lines): (0,0),(10,10),(10,0),(0,10),(0,0) in [lon,lat].
    bowtie_txt = "0,0\n10,10\n0,10\n10,0\n0,0\n"
    zip_path = tmp_path / "counties" / "usa" / "california_v3.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("1.txt", f"# GsakName=Bowtie County\n{bowtie_txt}")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _run(bb_path, tmp_path, out_dir)

    fc = json.loads((out_dir / "counties" / "usa_california.geojson").read_text())
    assert shape(fc["features"][0]["geometry"]).is_valid


def test_sanitize_filename_part_strips_spaces_and_accents() -> None:
    # GitHub Release assets silently rewrite spaces to dots on upload — a raw
    # GSAK state name in the output filename would permanently diverge from
    # the real asset name (found via Canada's "British Columbia", "Québec").
    assert conv._sanitize_filename_part("British Columbia") == "British_Columbia"
    assert conv._sanitize_filename_part("Québec") == "Quebec"
    assert conv._sanitize_filename_part("Newfoundland and Labrador") == "Newfoundland_and_Labrador"
    assert conv._sanitize_filename_part("Alberta") == "Alberta"


def test_county_pack_filename_is_sanitized(tmp_path: Path) -> None:
    bb_path = tmp_path / "bb.db3"
    _write_bb_db3(bb_path)
    _write_country_zip(tmp_path / "country_v1.zip", "1", "United States")
    _write_county_zip(tmp_path / "counties" / "usa" / "california_v3.zip", "1", "Alpha County")
    _write_county_zip(tmp_path / "counties" / "usa" / "texas_v5.zip", "2", "Beta County")

    # Rename the "california" pack to something with a space, mirroring a
    # real GSAK state name — bb.db3/Version rows drive the version lookup,
    # the zip filename drives which file gets opened, so both need updating.
    db = sqlite3.connect(bb_path)
    db.execute("UPDATE bb_county SET State = 'New Brunswick' WHERE State = 'california'")
    db.execute("UPDATE Version SET State = 'New Brunswick' WHERE State = 'california'")
    db.commit()
    db.close()
    (tmp_path / "counties" / "usa" / "california_v3.zip").rename(
        tmp_path / "counties" / "usa" / "New Brunswick_v3.zip"
    )

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _run(bb_path, tmp_path, out_dir)

    packs = sorted(p.name for p in (out_dir / "counties").iterdir())
    assert packs == ["usa_New_Brunswick.geojson", "usa_texas.geojson"]

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert "usa_New_Brunswick.geojson" in manifest["packs"]
    assert " " not in "".join(manifest["packs"].keys())


def _run(bb_path: Path, gsak_dir: Path, out_dir: Path) -> None:
    import sys

    argv = sys.argv
    sys.argv = [
        "gsak_to_opensak.py",
        "--bb-path", str(bb_path),
        "--gsak-dir", str(gsak_dir),
        "--out-dir", str(out_dir),
    ]
    try:
        conv.main()
    finally:
        sys.argv = argv
