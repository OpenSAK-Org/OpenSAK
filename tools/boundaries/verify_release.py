#!/usr/bin/env python3
# tools/boundaries/verify_release.py — validate a boundary dataset release.
#
# Checks a published OpenSAK-Data release (or a local pre-publish directory)
# end to end: manifest well-formed, every asset's sha256/size matches,
# boundaries.db opens with row counts consistent with its GeoJSON packs,
# every pack is valid GeoJSON with valid geometries, and a resolver smoke
# test against known real-world coordinates returns a result.
#
#   tools/boundaries/verify_release.py                # fetch + verify the published release
#   tools/boundaries/verify_release.py --data-dir DIR  # verify a local pre-publish directory
#
# Exits non-zero and prints every issue found if anything is wrong.

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from shapely.geometry import shape as _shp_shape

from opensak.geo import packs
from opensak.geo.boundaries import TerritoryResolver
from opensak.geo.store import BoundaryStore

_LAYER_DIRS = {"country": "countries", "state": "states", "county": "counties"}

# Fixed, well-inside-a-country coordinates. Only the *presence* of a country
# result is asserted, never the exact name — state/county names are real
# content that legitimately changes between releases.
_SMOKE_COORDS: tuple[tuple[float, float, str], ...] = (
    (38.7223, -9.1393, "Lisbon, Portugal"),
    (38.9072, -77.0369, "Washington DC, USA"),
)


def _asset_entries(manifest: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    entries = [("boundaries.db", "", manifest.get("boundaries_db", {}))]
    for name, info in manifest.get("baseline", {}).items():
        subdir = "countries" if name == "world.geojson" else "states"
        entries.append((name, subdir, info))
    for name, info in manifest.get("packs", {}).items():
        entries.append((name, "counties", info))
    return entries


def _download_all(manifest: dict[str, Any], dest_root: Path) -> list[str]:
    errors = []
    for name, subdir, _expected in _asset_entries(manifest):
        dest_dir = dest_root / subdir if subdir else dest_root
        if not packs.fetch_pack(name, dest_dir):
            errors.append(f"{name}: failed to download")
    return errors


def _verify_checksums(manifest: dict[str, Any], root: Path) -> list[str]:
    errors = []
    for name, subdir, expected in _asset_entries(manifest):
        sha = expected.get("sha256")
        size = expected.get("size")
        if sha is None or size is None:
            errors.append(f"{name}: manifest missing sha256/size")
            continue
        path = (root / subdir / name) if subdir else (root / name)
        if not path.is_file():
            errors.append(f"{name}: not found at {path}")
            continue
        data = path.read_bytes()
        if len(data) != size:
            errors.append(f"{name}: size mismatch (expected {size}, got {len(data)})")
            continue
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != sha:
            errors.append(f"{name}: sha256 mismatch (expected {sha}, got {actual_sha})")
    return errors


def _load_geojson(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    # A corrupted release asset can fail in several ways (bad encoding, bad
    # JSON, truncated file) — none of them should crash the checker itself.
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{path}: unreadable ({exc})"


def _verify_boundaries_db(root: Path) -> list[str]:
    db_path = root / "boundaries.db"
    if not db_path.is_file():
        return [f"boundaries.db not found at {db_path}"]

    errors: list[str] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as exc:
        return [f"{db_path}: not a valid SQLite database ({exc})"]
    try:
        pack_feature_counts: dict[str, int] = {}
        for layer, dirname in _LAYER_DIRS.items():
            rtree_table, region_table = f"rtree_{layer}", f"region_{layer}"
            if rtree_table not in tables or region_table not in tables:
                errors.append(f"{layer}: missing table {rtree_table} or {region_table}")
                continue

            rtree_count = conn.execute(f"SELECT COUNT(*) FROM {rtree_table}").fetchone()[0]
            region_rows = conn.execute(
                f"SELECT id, pack, feature_index FROM {region_table}"
            ).fetchall()
            if rtree_count != len(region_rows):
                errors.append(
                    f"{layer}: {rtree_table} has {rtree_count} row(s) but "
                    f"{region_table} has {len(region_rows)}"
                )

            for row in region_rows:
                pack_path = root / dirname / row["pack"]
                count = pack_feature_counts.get(str(pack_path))
                if count is None:
                    if not pack_path.is_file():
                        errors.append(f"{layer} region {row['id']}: pack file missing: {pack_path}")
                        continue
                    fc, load_error = _load_geojson(pack_path)
                    if load_error:
                        errors.append(load_error)
                        continue
                    assert fc is not None
                    count = len(fc.get("features", []))
                    pack_feature_counts[str(pack_path)] = count
                if not (0 <= row["feature_index"] < count):
                    errors.append(
                        f"{layer} region {row['id']}: feature_index {row['feature_index']} "
                        f"out of bounds for {row['pack']} ({count} features)"
                    )
    finally:
        conn.close()
    return errors


def _verify_pack_geometries(root: Path) -> list[str]:
    errors = []
    for dirname in _LAYER_DIRS.values():
        d = root / dirname
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.geojson")):
            fc, load_error = _load_geojson(path)
            if load_error:
                errors.append(load_error)
                continue
            assert fc is not None
            for i, feature in enumerate(fc.get("features", [])):
                geom = feature.get("geometry")
                if geom is None:
                    continue
                try:
                    shp = _shp_shape(geom)
                except Exception as exc:  # noqa: BLE001 — any shapely/geometry error is a data defect
                    errors.append(f"{path} feature {i}: unparsable geometry ({exc})")
                    continue
                if not shp.is_valid:
                    errors.append(f"{path} feature {i}: invalid geometry")
    return errors


def _verify_resolver(root: Path) -> list[str]:
    store = BoundaryStore(root)
    if not store.available():
        return ["boundaries.db not available for resolver smoke test"]
    resolver = TerritoryResolver(store)
    errors = []
    for lat, lon, label in _SMOKE_COORDS:
        location = resolver.resolve(lat, lon)
        if not location.country:
            errors.append(f"resolver returned no country for {label} ({lat}, {lon})")
    store.close()
    return errors


def _run_checks(manifest: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    if not manifest.get("dataset_version"):
        errors.append("manifest missing dataset_version")
    errors += _verify_checksums(manifest, root)
    errors += _verify_boundaries_db(root)
    errors += _verify_pack_geometries(root)
    if not errors:  # skip the (slower) resolver smoke test if the data is already known-bad
        errors += _verify_resolver(root)
    return errors


def verify(data_dir: Path | None) -> list[str]:
    if data_dir is not None:
        manifest_path = data_dir / "manifest.json"
        if not manifest_path.is_file():
            return [f"manifest.json not found at {manifest_path}"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return _run_checks(manifest, data_dir)

    manifest = packs.fetch_manifest()
    if manifest is None:
        return ["failed to fetch manifest.json from OpenSAK-Data"]
    with tempfile.TemporaryDirectory(prefix="opensak-verify-") as tmp:
        root = Path(tmp)
        errors = _download_all(manifest, root)
        errors += _run_checks(manifest, root)
        return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a published (or local pre-publish) OpenSAK-Data boundary release."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Validate a local pre-publish directory instead of downloading the published release",
    )
    args = parser.parse_args()

    errors = verify(args.data_dir)
    if errors:
        print(f"FAILED — {len(errors)} issue(s) found:")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)
    print("OK — release data verified.")


if __name__ == "__main__":
    main()
