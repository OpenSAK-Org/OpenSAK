#!/usr/bin/env python3
"""
tools/boundaries/generate.py — generate the OpenSAK boundary dataset from public geodata.

No dependencies beyond the project's existing requirements (shapely + stdlib).

Sources:
  Countries / States : Natural Earth 10m (public domain)
                       https://github.com/nvkelso/natural-earth-vector
  Counties           : GADM 4.1 level-2 (free for non-commercial use)
                       https://gadm.org

Output (mirrors BoundaryStore layout, see src/opensak/geo/store.py):

  <out-dir>/
    countries/world.geojson    one FeatureCollection, all countries
    states/<cc>.geojson        one FeatureCollection per country (ISO alpha-2 lowercase)
    counties/<cc>.geojson      one FeatureCollection per country
    boundaries.db              SQLite R-Trees + region metadata
    manifest.json              dataset version + per-pack versions

Usage:
  # full global dataset (slow — downloads ~70 MB + GADM per country)
  python tools/boundaries/generate.py

  # subset (fast — good for local testing)
  python tools/boundaries/generate.py --countries PT,ES,DK --out-dir /tmp/bd

  # skip counties entirely
  python tools/boundaries/generate.py --no-counties

  # skip downloads, rebuild boundaries.db + manifest.json from existing GeoJSONs
  python tools/boundaries/generate.py --skip-download
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.error import URLError

try:
    from shapely.geometry import shape as _shp
    _HAS_SHAPELY = True
except ImportError:
    _HAS_SHAPELY = False

# ── Source URLs ───────────────────────────────────────────────────────────────

_NE_TAG   = "v5.1.2"
_NE_BASE  = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{_NE_TAG}/geojson"
_NE_COUNTRIES_URL = f"{_NE_BASE}/ne_10m_admin_0_countries.geojson"
_NE_STATES_URL    = f"{_NE_BASE}/ne_10m_admin_1_states_provinces.geojson"
_GADM_URL         = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{iso3}_2.json"

_REPO_ROOT   = Path(__file__).resolve().parents[2]
_DEFAULT_OUT = _REPO_ROOT / "data"
_TIMEOUT     = 120


# ── Download helpers ──────────────────────────────────────────────────────────

def _download(url: str, label: str = "", retries: int = 3) -> bytes:
    tag = label or url.rsplit("/", 1)[-1]
    print(f"  ↓ {tag}", end=" ", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "OpenSAK-boundary-generator/1.0"})
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = resp.read()
            print(f"({len(data) // 1024} KB)")
            return data
        except URLError as exc:
            last_exc = exc
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"retry in {wait}s ...", end=" ", flush=True)
                time.sleep(wait)
    print(f"FAILED ({last_exc})")
    raise last_exc


def _fetch_json(url: str, label: str = "") -> dict[str, Any]:
    return json.loads(_download(url, label))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── GeoJSON helpers ───────────────────────────────────────────────────────────

def _make_feature(
    layer: str,
    name: str,
    parent: str | None,
    geometry: dict[str, Any],
    source: str,
    version: int = 1,
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "layer": layer,
            "name": name,
            "parent": parent,
            "version": version,
            "source": source,
            "licence": "public_domain" if source == "natural_earth" else "CC-BY-4.0",
        },
        "geometry": geometry,
    }


def _write_pack(path: Path, features: list[dict[str, Any]]) -> None:
    fc = json.dumps(
        {"type": "FeatureCollection", "features": features},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    _atomic_write(path, fc)
    try:
        label = path.relative_to(_REPO_ROOT)
    except ValueError:
        label = path
    print(f"    → {label} ({len(features)} features, {len(fc) // 1024} KB)")


def _bbox(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    """Return (min_lat, max_lat, min_lon, max_lon) for R-Tree insertion."""
    if not _HAS_SHAPELY:
        raise RuntimeError("shapely is required; install it with: pip install shapely")
    min_lon, min_lat, max_lon, max_lat = _shp(geometry).bounds
    return min_lat, max_lat, min_lon, max_lon


# ── Countries (Natural Earth admin-0) ─────────────────────────────────────────

def build_countries(
    out_dir: Path,
    include: set[str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Download + write countries/world.geojson.

    Returns:
      iso2_to_name : {iso2_lower → country display name}
      iso3_to_iso2 : {ISO3_UPPER → iso2_lower}  (used when building counties)
    """
    print("\n[countries] Natural Earth admin-0")
    raw = _fetch_json(_NE_COUNTRIES_URL, "ne_10m_admin_0_countries.geojson")

    features_out: list[dict[str, Any]] = []
    iso2_to_name: dict[str, str] = {}
    iso3_to_iso2: dict[str, str] = {}

    for f in raw.get("features", []):
        props = f.get("properties") or {}

        # ISO_A2 is -99 for disputed / special territories in Natural Earth
        iso2 = (props.get("ISO_A2") or "").strip()
        if iso2 == "-99":
            iso2 = (props.get("ISO_A2_EH") or "").strip()
        if not iso2 or iso2 == "-99":
            continue

        iso3 = (props.get("ISO_A3") or "").strip()
        if iso3 == "-99":
            iso3 = (props.get("ISO_A3_EH") or "").strip()

        name = (props.get("ADMIN") or props.get("NAME") or iso2).strip()
        cc   = iso2.lower()

        if include and iso2.upper() not in include:
            continue

        iso2_to_name[cc] = name
        if iso3 and iso3 != "-99":
            iso3_to_iso2[iso3.upper()] = cc

        features_out.append(_make_feature("country", name, None, f["geometry"], "natural_earth"))

    _write_pack(out_dir / "countries" / "world.geojson", features_out)
    return iso2_to_name, iso3_to_iso2


# ── States (Natural Earth admin-1) ────────────────────────────────────────────

def build_states(
    out_dir: Path,
    iso2_to_name: dict[str, str],
    include: set[str] | None,
) -> None:
    """Download + write states/<cc>.geojson (one pack per country)."""
    print("\n[states] Natural Earth admin-1")
    raw = _fetch_json(_NE_STATES_URL, "ne_10m_admin_1_states_provinces.geojson")

    by_country: dict[str, list[dict[str, Any]]] = {}

    for f in raw.get("features", []):
        props = f.get("properties") or {}

        iso2 = (props.get("iso_a2") or props.get("adm0_a3", "")[:2]).strip().lower()
        name = (props.get("name") or props.get("NAME") or "").strip()

        if not iso2 or iso2 == "-9" or not name:
            continue
        if iso2 not in iso2_to_name:
            continue
        if include and iso2.upper() not in include:
            continue

        parent = iso2.upper()
        by_country.setdefault(iso2, []).append(
            _make_feature("state", name, parent, f["geometry"], "natural_earth")
        )

    for cc, features in sorted(by_country.items()):
        _write_pack(out_dir / "states" / f"{cc}.geojson", features)


# ── Counties (GADM 4.1 level-2) ───────────────────────────────────────────────

def build_counties(
    out_dir: Path,
    iso3_to_iso2: dict[str, str],
    include: set[str] | None,
) -> None:
    """Download GADM 4.1 level-2 per country → counties/<cc>.geojson."""
    print("\n[counties] GADM 4.1 level-2")

    # Invert map: iso2_lower → ISO3_UPPER
    iso2_to_iso3 = {v: k for k, v in iso3_to_iso2.items()}
    targets = sorted(iso2_to_iso3.keys())
    if include:
        targets = [cc for cc in targets if cc.upper() in include]

    ok = skipped = 0
    for cc in targets:
        iso3 = iso2_to_iso3[cc]
        url  = _GADM_URL.format(iso3=iso3)
        out_path = out_dir / "counties" / f"{cc}.geojson"

        try:
            raw = _fetch_json(url, f"GADM {iso3} level-2")
        except (URLError, OSError) as exc:
            print(f"    SKIP {cc} ({iso3}): {exc}")
            skipped += 1
            continue

        features_out: list[dict[str, Any]] = []
        for f in raw.get("features", []):
            props     = f.get("properties") or {}
            state_nm  = (props.get("NAME_1") or "").strip()
            county_nm = (props.get("NAME_2") or "").strip()
            if not county_nm:
                continue
            parent = f"{cc.upper()}/{state_nm}" if state_nm else cc.upper()
            features_out.append(
                _make_feature("county", county_nm, parent, f["geometry"], "gadm")
            )

        if features_out:
            _write_pack(out_path, features_out)
            ok += 1
        else:
            print(f"    SKIP {cc}: no features")
            skipped += 1

    print(f"  counties done: {ok} written, {skipped} skipped")


# ── boundaries.db ─────────────────────────────────────────────────────────────

_DB_SCHEMA = """\
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
CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER);\
"""

_LAYER_DIR = {"country": "countries", "state": "states", "county": "counties"}


def build_db(out_dir: Path) -> None:
    """Scan all GeoJSON packs in out_dir and build boundaries.db from scratch."""
    print("\n[db] building boundaries.db")
    db_path = out_dir / "boundaries.db"
    if db_path.exists():
        db_path.unlink()

    db = sqlite3.connect(db_path)
    for stmt in _DB_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            db.execute(stmt)

    version = time.strftime("%Y%m%d")
    db.execute("INSERT INTO file_version VALUES ('dataset', NULL, NULL, ?)", (version,))

    rid: dict[str, int] = {"country": 0, "state": 0, "county": 0}

    for layer in ("country", "state", "county"):
        layer_dir = out_dir / _LAYER_DIR[layer]
        if not layer_dir.exists():
            continue

        packs = sorted(layer_dir.glob("*.geojson"))
        inserted = 0

        for pack_path in packs:
            fc = json.loads(pack_path.read_text(encoding="utf-8"))
            pack_name = pack_path.name

            for idx, feature in enumerate(fc.get("features", [])):
                props  = feature.get("properties") or {}
                name   = str(props.get("name") or "")
                parent = props.get("parent")
                geom   = feature.get("geometry") or {}

                if not geom or not name:
                    continue

                try:
                    min_lat, max_lat, min_lon, max_lon = _bbox(geom)
                except Exception:
                    continue

                rid[layer] += 1
                r = rid[layer]
                db.execute(
                    f"INSERT INTO rtree_{layer} VALUES (?,?,?,?,?)",
                    (r, min_lat, max_lat, min_lon, max_lon),
                )
                db.execute(
                    f"INSERT INTO region_{layer} VALUES (?,?,?,?,?,?,?)",
                    (r, name, parent, pack_name, idx, 1, 0 if layer == "county" else 1),
                )
                inserted += 1

        print(f"  {layer}: {len(packs)} pack(s), {inserted} regions")

    db.commit()
    db.close()
    size_kb = db_path.stat().st_size // 1024
    print(f"  boundaries.db written ({size_kb} KB)")


# ── manifest.json ─────────────────────────────────────────────────────────────

def build_manifest(out_dir: Path) -> None:
    """Generate manifest.json from the current boundaries.db and county packs."""
    print("\n[manifest] building manifest.json")

    dataset_version = "unknown"
    db_path = out_dir / "boundaries.db"
    if db_path.exists():
        con = sqlite3.connect(str(db_path))
        row = con.execute(
            "SELECT version FROM file_version WHERE layer = 'dataset'"
        ).fetchone()
        con.close()
        if row:
            dataset_version = str(row[0])

    packs: dict[str, dict[str, str]] = {}
    counties_dir = out_dir / "counties"
    if counties_dir.exists():
        for p in sorted(counties_dir.glob("*.geojson")):
            try:
                fc       = json.loads(p.read_text(encoding="utf-8"))
                features = fc.get("features", [])
                ver      = str(features[0]["properties"].get("version", 1)) if features else "1"
            except Exception:
                ver = "1"
            packs[p.name] = {"version": ver}

    manifest = {"dataset_version": dataset_version, "packs": packs}
    _atomic_write(
        out_dir / "manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
    )
    print(f"  manifest.json written ({len(packs)} county packs, dataset={dataset_version})")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--out-dir", default=str(_DEFAULT_OUT),
        help="output directory (default: data/)",
    )
    ap.add_argument(
        "--countries",
        help="comma-separated ISO 3166-1 alpha-2 codes to limit the run "
             "(e.g. PT,ES,DK). Applies to all three layers.",
    )
    ap.add_argument(
        "--no-counties", action="store_true",
        help="skip county download (faster, useful when testing countries/states only)",
    )
    ap.add_argument(
        "--no-states", action="store_true",
        help="skip state download",
    )
    ap.add_argument(
        "--skip-download", action="store_true",
        help="do not download anything — rebuild boundaries.db and manifest.json "
             "from the GeoJSON files already present in <out-dir>",
    )
    args = ap.parse_args()

    if not _HAS_SHAPELY:
        sys.exit("shapely is required: pip install shapely")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    include: set[str] | None = None
    if args.countries:
        include = {c.strip().upper() for c in args.countries.split(",") if c.strip()}
        print(f"Limiting to: {', '.join(sorted(include))}")

    if not args.skip_download:
        iso2_to_name, iso3_to_iso2 = build_countries(out_dir, include)

        if not args.no_states:
            build_states(out_dir, iso2_to_name, include)

        if not args.no_counties:
            build_counties(out_dir, iso3_to_iso2, include)

    build_db(out_dir)
    build_manifest(out_dir)

    print(f"\nDone. Dataset written to: {out_dir}")


if __name__ == "__main__":
    main()
