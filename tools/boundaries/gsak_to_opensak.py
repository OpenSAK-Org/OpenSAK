#!/usr/bin/env python3
# tools/boundaries/gsak_to_opensak.py — convert GSAK boundary data to OpenSAK format.
#
# Run with the project's own venv (3.11+), not a bare system python3 — older
# zipfile implementations (e.g. 3.8) fall back to legacy CP437 decoding for
# zip entries that don't set the UTF-8 flag bit (several GSAK county zips
# don't), silently mangling non-ASCII filenames and dropping those rows with
# no warning:
#   .venv/bin/python3 tools/boundaries/gsak_to_opensak.py [--gsak-dir DATA] [--out-dir DATA]
#
# Reads:  <gsak-dir>/bb.db3
#          <gsak-dir>/country_v<N>.zip          (N from bb.db3 Version table)
#          <gsak-dir>/states/<cc>[_vN].zip
#          <gsak-dir>/counties/<cc>/<pack>[_vN].zip
#
# Writes: <out-dir>/boundaries.db              (OpenSAK schema)
#          <out-dir>/manifest.json              (dataset version; "baseline" = world.geojson
#                                                + state packs, fetched wholesale on first run;
#                                                "packs" = county packs, fetched on demand)
#          <out-dir>/countries/world.geojson         simplified baseline (--simplify-tolerance)
#          <out-dir>/states/<cc>.geojson             simplified baseline, one per country code
#          <out-dir>/counties/<cc>_<pack>.geojson    full-resolution, on-demand (one per pack, flat)
#
# After this script finishes, BoundaryStore(Path("<out-dir>")) resolves
# coordinates offline using the engine in src/opensak/geo/.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
import zipfile
from pathlib import Path

from shapely.geometry import mapping as _shp_mapping
from shapely.geometry import shape as _shp_shape

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA = _REPO_ROOT / "data"

# ── Output schema (matches BoundaryStore expectations) ────────────────────────

_SCHEMA = """\
CREATE VIRTUAL TABLE rtree_country USING rtree(id, min_lat, max_lat, min_lon, max_lon);
CREATE TABLE region_country (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    parent        TEXT,
    pack          TEXT NOT NULL,
    feature_index INTEGER NOT NULL,
    poly_version  INTEGER NOT NULL DEFAULT 1,
    is_bundled    INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE rtree_state USING rtree(id, min_lat, max_lat, min_lon, max_lon);
CREATE TABLE region_state (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    parent        TEXT,
    pack          TEXT NOT NULL,
    feature_index INTEGER NOT NULL,
    poly_version  INTEGER NOT NULL DEFAULT 1,
    is_bundled    INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE rtree_county USING rtree(id, min_lat, max_lat, min_lon, max_lon);
CREATE TABLE region_county (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    parent        TEXT,
    pack          TEXT NOT NULL,
    feature_index INTEGER NOT NULL,
    poly_version  INTEGER NOT NULL DEFAULT 1,
    is_bundled    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER)
"""

# ── GSAK polygon text parser ──────────────────────────────────────────────────

def _parse_gsak_txt(content: str) -> list[list[list[list[float]]]]:
    """Parse GSAK lat,lon polygon text into GeoJSON polygon coordinate groups.

    Returns a list of [outer_ring, *holes] lists, one per '# Inclusion area'
    section. A single element means Polygon; multiple means MultiPolygon.
    GeoJSON convention: coordinates are [lon, lat].
    """
    polygons: list[list[list[list[float]]]] = []
    poly_rings: list[list[list[float]]] = []  # rings for the current inclusion area
    cur_ring: list[list[float]] = []           # ring being accumulated

    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "Inclusion area" in line or "Exclusion area" in line:
                # Flush current ring into the current polygon group
                if cur_ring:
                    poly_rings.append(cur_ring)
                    cur_ring = []
                # On a new inclusion area, also flush the polygon group
                if "Inclusion area" in line and poly_rings:
                    polygons.append(poly_rings)
                    poly_rings = []
        else:
            try:
                # GSAK uses several formats: tab (county/USA), comma (most states/countries),
                # comma with trailing comma (France/Italy), space-separated (Great Britain).
                if "\t" in line:
                    parts = line.split("\t")
                elif "," in line:
                    parts = [p.strip() for p in line.split(",")]
                else:
                    parts = line.split()
                parts = [p for p in parts if p]
                cur_ring.append([float(parts[1]), float(parts[0])])  # [lon, lat]
            except (ValueError, IndexError):
                pass

    # Flush whatever is left after the last line
    if cur_ring:
        poly_rings.append(cur_ring)
    if poly_rings:
        polygons.append(poly_rings)

    return polygons


def _split_antimeridian(ring: list[list[float]]) -> list[list[list[float]]]:
    """Split a ring that crosses the antimeridian via teleportation edges (|Δlon| > 180°).

    GSAK stores antimeridian-spanning countries (e.g. Russia) as one ring with synthetic
    "jump" edges that skip ≥180° of longitude.  Those edges also create a figure-8 topology
    where the ring revisits ring[0] in the middle, making the implicit closing edge synthetic
    too.  We detect both kinds and emit valid, independently-closed sub-rings so that
    standard ray-casting PIP gives correct results everywhere.
    """
    n = len(ring)
    split_after: set[int] = set()

    for i in range(n):
        if abs(ring[i][0] - ring[(i + 1) % n][0]) > 180:
            split_after.add(i)

    if not split_after:
        return [ring]

    # If ring[0] recurs mid-ring the implicit closing edge ring[n-1]→ring[0] is also synthetic.
    v0 = ring[0]
    for i in range(1, n - 1):
        if ring[i][0] == v0[0] and ring[i][1] == v0[1]:
            split_after.add(n - 1)
            break

    sorted_splits = sorted(split_after)
    sub_rings: list[list[list[float]]] = []
    for k, sp in enumerate(sorted_splits):
        start = sp + 1
        end = sorted_splits[(k + 1) % len(sorted_splits)]
        seg: list[list[float]] = (
            list(ring[start : end + 1]) if start <= end
            else list(ring[start:]) + list(ring[: end + 1])
        )
        if seg and seg[0] != seg[-1]:
            seg.append(seg[0])
        if len(seg) >= 4:
            sub_rings.append(seg)

    return sub_rings or [ring]


def _geometry(polygons: list[list[list[list[float]]]]) -> dict[str, object]:
    # Split outer rings that span the antimeridian into valid sub-polygons.
    all_polys: list[list[list[list[float]]]] = []
    for rings in polygons:
        holes = rings[1:]
        for split_ring in _split_antimeridian(rings[0]):
            all_polys.append([split_ring] + holes)
    if not all_polys:
        return {"type": "Polygon", "coordinates": []}
    if len(all_polys) == 1:
        return {"type": "Polygon", "coordinates": all_polys[0]}
    return {"type": "MultiPolygon", "coordinates": all_polys}


def _simplify(geom: dict[str, object], tolerance: float) -> dict[str, object]:
    # Douglas-Peucker (degrees-based tolerance) for the baseline layers
    # (country/state) only — counties stay full-resolution since they're
    # fetched on demand, not bundled. Called with tolerance=0 for counties to
    # skip simplification but still get the validity repair below: the raw
    # GSAK-derived rings are self-intersecting for ~6% of real counties (not
    # something simplification introduces), and shapely's .contains() doesn't
    # raise on an invalid geometry — it silently returns wrong answers.
    if not geom.get("coordinates"):
        return geom
    shp = _shp_shape(geom)
    if tolerance > 0:
        shp = shp.simplify(tolerance, preserve_topology=True)
    if not shp.is_valid:
        # preserve_topology doesn't fully guarantee validity on complex multi-ring
        # geometries (nearby-but-separate rings can end up crossing) — buffer(0)
        # is the standard GEOS trick to repair self-intersections.
        shp = shp.buffer(0)
    if shp.is_empty:
        return geom
    return _shp_mapping(shp)


def _feature(name: str, parent: str | None, geom: dict[str, object], version: int) -> dict[str, object]:
    return {
        "type": "Feature",
        "properties": {
            "name": name,
            "parent": parent,
            "version": version,
            "source": "gsak",
            "licence": "ODbL",
        },
        "geometry": geom,
    }


# ── Zip helpers ───────────────────────────────────────────────────────────────

def _find_zip(parent: Path, name: str, version: int) -> Path | None:
    """Locate the zip for a given pack name + version.

    GSAK names the current version as <name>.zip (version 1) or
    <name>_v<N>.zip / <name>V<N>.zip (version N > 1), keeping older copies
    with the version suffix. Falls back to the base <name>.zip.
    """
    if version <= 1:
        candidates = [parent / f"{name}.zip"]
    else:
        candidates = [
            parent / f"{name}_v{version}.zip",
            parent / f"{name}V{version}.zip",   # ArizonaV3.zip style
            parent / f"{name}.zip",              # fallback
        ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _read_zip_entry(zf: zipfile.ZipFile, entry: str) -> str | None:
    try:
        raw = zf.read(entry)
    except KeyError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")  # GSAK files pre-2020 often use Latin-1


def _sanitize_filename_part(name: str) -> str:
    # GSAK state/pack names can contain spaces and accents (e.g. Canada's
    # "British Columbia", "Québec") — GitHub Release assets silently rewrite
    # such characters on upload (spaces become dots), which would otherwise
    # make manifest.json permanently diverge from the real asset names.
    ascii_only = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_only).strip("_")


# ── Version table ─────────────────────────────────────────────────────────────

def _load_versions(bb: sqlite3.Connection) -> dict[tuple[str, str, str], int]:
    """Read all rows from the GSAK Version table."""
    cur = bb.execute("SELECT Type, Country, State, Version FROM Version")
    return {
        (str(r[0]), str(r[1] or ""), str(r[2] or "")): int(r[3])
        for r in cur.fetchall()
    }


# ── Conversion passes ─────────────────────────────────────────────────────────

def _convert_countries(
    bb: sqlite3.Connection,
    gsak_dir: Path,
    out_dir: Path,
    conn: sqlite3.Connection,
    versions: dict[tuple[str, str, str], int],
    simplify_tolerance: float = 0.0,
) -> None:
    version = versions.get(("c", "", ""), 46)
    zip_path = gsak_dir / f"country_v{version}.zip"
    if not zip_path.exists():
        print(f"  ! country zip not found: {zip_path.name}")
        return

    print(f"  {zip_path.name}")
    features: list[dict[str, object]] = []
    skipped = 0

    with zipfile.ZipFile(zip_path) as zf:
        rows = bb.execute(
            "SELECT rowid, File, Country, MaxLat, MinLat, MaxLon, MinLon FROM bb_country"
        ).fetchall()

        for rowid, file_name, country_name, max_lat, min_lat, max_lon, min_lon in rows:
            content = _read_zip_entry(zf, file_name)
            if content is None:
                skipped += 1
                continue

            geom = _simplify(_geometry(_parse_gsak_txt(content)), simplify_tolerance)
            feature_index = len(features)
            features.append(_feature(country_name, None, geom, version))

            conn.execute(
                "INSERT INTO rtree_country VALUES (?, ?, ?, ?, ?)",
                (rowid, min_lat, max_lat, min_lon, max_lon),
            )
            conn.execute(
                "INSERT INTO region_country VALUES (?, ?, NULL, 'world.geojson', ?, 1, 1)",
                (rowid, country_name, feature_index),
            )

    out_path = out_dir / "countries" / "world.geojson"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  → {len(features)} countries, {skipped} skipped")


def _convert_states(
    bb: sqlite3.Connection,
    gsak_dir: Path,
    out_dir: Path,
    conn: sqlite3.Connection,
    versions: dict[tuple[str, str, str], int],
    simplify_tolerance: float = 0.0,
) -> dict[str, int]:
    rows = bb.execute(
        "SELECT rowid, Country, File, MaxLat, MinLat, MaxLon, MinLon, Sname FROM bb_state"
    ).fetchall()

    # group by country code so we open each state zip once
    by_cc: dict[str, list] = {}
    for row in rows:
        by_cc.setdefault(str(row[1]), []).append(row)

    pack_versions: dict[str, int] = {}
    total = 0
    missing = 0
    for cc in sorted(by_cc):
        version = versions.get(("s", cc, ""), 1)
        zip_path = _find_zip(gsak_dir / "states", cc, version)
        if zip_path is None:
            missing += 1
            continue

        features: list[dict[str, object]] = []
        pack_name = f"{cc}.geojson"

        with zipfile.ZipFile(zip_path) as zf:
            for row in by_cc[cc]:
                rowid, _, file_id, max_lat, min_lat, max_lon, min_lon, sname = row
                content = _read_zip_entry(zf, f"{file_id}.txt")
                if content is None:
                    continue

                geom = _simplify(_geometry(_parse_gsak_txt(content)), simplify_tolerance)
                feature_index = len(features)
                features.append(_feature(sname, cc, geom, version))

                conn.execute(
                    "INSERT INTO rtree_state VALUES (?, ?, ?, ?, ?)",
                    (rowid, min_lat, max_lat, min_lon, max_lon),
                )
                conn.execute(
                    "INSERT INTO region_state VALUES (?, ?, ?, ?, ?, 1, 1)",
                    (rowid, sname, cc, pack_name, feature_index),
                )

        if features:
            out_path = out_dir / "states" / pack_name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
                encoding="utf-8",
            )
            total += len(features)
            pack_versions[pack_name] = version

    print(f"  → {total} states across {len(by_cc)} codes ({missing} zip(s) missing)")
    return pack_versions


def _convert_counties(
    bb: sqlite3.Connection,
    gsak_dir: Path,
    out_dir: Path,
    conn: sqlite3.Connection,
    versions: dict[tuple[str, str, str], int],
) -> dict[str, int]:
    rows = bb.execute(
        "SELECT rowid, Country, State, File, MaxLat, MinLat, MaxLon, MinLon, Cname FROM bb_county"
    ).fetchall()

    # group by (country, pack) so we open each county zip once, keeping
    # feature_index sequential within the per-country output GeoJSON
    by_cc_pack: dict[tuple[str, str], list] = {}
    for row in rows:
        key = (str(row[1]), str(row[2]))
        by_cc_pack.setdefault(key, []).append(row)

    pack_versions: dict[str, int] = {}
    total = 0
    missing = 0
    for (cc, pack_name) in sorted(by_cc_pack):
        version = versions.get(("y", cc, pack_name), 1)
        zip_path = _find_zip(gsak_dir / "counties" / cc, pack_name, version)
        if zip_path is None:
            missing += 1
            continue

        # Counties from the same pack are flattened into a single release-asset
        # filename (cc_pack.geojson) — GitHub Release assets can't hold subdirectories,
        # and packs.py fetches them by this exact flat name. pack_name is sanitized
        # here (not when locating the source zip above) since some GSAK state names
        # contain spaces/accents (e.g. Canada's "British Columbia", "Québec").
        pack_features: list[dict[str, object]] = []
        pack_region_rows: list[tuple] = []
        out_pack = f"{cc}_{_sanitize_filename_part(pack_name)}.geojson"

        with zipfile.ZipFile(zip_path) as zf:
            for row in by_cc_pack[(cc, pack_name)]:
                rowid, _, _, file_id, max_lat, min_lat, max_lon, min_lon, cname = row
                content = _read_zip_entry(zf, f"{file_id}.txt")
                if content is None:
                    continue

                # tolerance=0: no simplification, but still validity-checked/repaired.
                geom = _simplify(_geometry(_parse_gsak_txt(content)), 0.0)
                feature_index = len(pack_features)
                pack_features.append(_feature(cname, cc, geom, version))

                conn.execute(
                    "INSERT INTO rtree_county VALUES (?, ?, ?, ?, ?)",
                    (rowid, min_lat, max_lat, min_lon, max_lon),
                )
                conn.execute(
                    "INSERT INTO region_county VALUES (?, ?, ?, ?, ?, 1, 0)",
                    (rowid, cname, cc, out_pack, feature_index),
                )

        if pack_features:
            out_path = out_dir / "counties" / out_pack
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps({"type": "FeatureCollection", "features": pack_features}, ensure_ascii=False),
                encoding="utf-8",
            )
            total += len(pack_features)
            pack_versions[out_pack] = version

    print(f"  → {total} counties ({missing} zip(s) missing)")
    return pack_versions


def _file_digest(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _write_manifest(
    out_dir: Path,
    dataset_version: int,
    baseline_versions: dict[str, int],
    pack_versions: dict[str, int],
) -> None:
    # "baseline" (world.geojson + state packs) is bundled in every install and
    # fetched wholesale on first run when not bundled (see geo/packs.py
    # fetch_baseline). "packs" (county-level) stays on-demand, per-country.
    # sha256/size let verify_release.py detect a corrupted or truncated
    # release asset without needing to reprocess the whole dataset.
    manifest = {
        "dataset_version": str(dataset_version),
        "boundaries_db": _file_digest(out_dir / "boundaries.db"),
        "baseline": {
            name: {"version": str(v), **_file_digest(
                out_dir / ("countries" if name == "world.geojson" else "states") / name
            )}
            for name, v in sorted(baseline_versions.items())
        },
        "packs": {
            name: {"version": str(v), **_file_digest(out_dir / "counties" / name)}
            for name, v in sorted(pack_versions.items())
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert GSAK boundary data (bb.db3 + polygon zips) to OpenSAK format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "After running, point BoundaryStore at <out-dir> or set\n"
            "OPENSAK_BOUNDARIES_DIR=<out-dir> to use the converted data."
        ),
    )
    parser.add_argument(
        "--gsak-dir",
        type=Path,
        default=_DEFAULT_DATA,
        metavar="DIR",
        help="Directory containing country_v*.zip, states/, counties/ "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "--bb-path",
        type=Path,
        default=_REPO_ROOT / "bb.db3",
        metavar="FILE",
        help="Path to GSAK bb.db3 (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_DATA,
        metavar="DIR",
        help="Output directory for boundaries.db and GeoJSON packs "
             "(default: same as --gsak-dir)",
    )
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        default=0.0005,
        metavar="DEGREES",
        help="Douglas-Peucker tolerance applied to the country/state baseline "
             "only (shipped in every install) — counties stay full-resolution "
             "since they're fetched on demand. 0 disables simplification "
             "(default: %(default)s, ~55m at the equator)",
    )
    args = parser.parse_args()
    gsak_dir: Path = args.gsak_dir
    out_dir: Path = args.out_dir
    simplify_tolerance: float = args.simplify_tolerance

    bb_path: Path = args.bb_path
    if not bb_path.exists():
        raise SystemExit(f"bb.db3 not found: {bb_path}")

    out_db = out_dir / "boundaries.db"
    out_db.unlink(missing_ok=True)
    conn = sqlite3.connect(out_db)
    for stmt in _SCHEMA.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()

    bb = sqlite3.connect(f"file:{bb_path}?mode=ro", uri=True)
    versions = _load_versions(bb)
    country_version = versions.get(("c", "", ""), 0)

    print("Converting countries…")
    _convert_countries(bb, gsak_dir, out_dir, conn, versions, simplify_tolerance)
    conn.commit()

    print("Converting states…")
    state_versions = _convert_states(bb, gsak_dir, out_dir, conn, versions, simplify_tolerance)
    conn.commit()
    baseline_versions = {"world.geojson": country_version, **state_versions}

    print("Converting counties…")
    pack_versions = _convert_counties(bb, gsak_dir, out_dir, conn, versions)
    conn.commit()

    conn.execute(
        "INSERT INTO file_version VALUES ('dataset', NULL, NULL, ?)",
        (country_version,),
    )
    conn.commit()

    bb.close()
    conn.close()

    _write_manifest(out_dir, country_version, baseline_versions, pack_versions)

    print(f"\nDone → {out_dir}")
    print(f"  boundaries.db   {out_db.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
