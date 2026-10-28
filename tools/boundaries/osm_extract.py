#!/usr/bin/env python3
# tools/boundaries/osm_extract.py — build OpenSAK state/county packs from OpenStreetMap.
#
# Part of epic #968 (OSM as the preferred source for new and replacement
# boundary data). Reads a Geofabrik country extract (.osm.pbf), takes the
# administrative boundaries at the admin_level configured per country in
# osm_countries.toml, and writes them in the same pack format as
# gsak_to_opensak.py:
#
#   <out-dir>/states/<iso3>.geojson                     simplified (--state-tolerance)
#   <out-dir>/counties/<iso3>_<county_pack>.geojson     full resolution by default
#   <out-dir>/osm_extract_<iso3>.json                   provenance: source file, OSM date
#   <out-dir>/osm_extract_<iso3>_report.md              summary + comparison report
#
# It does not touch boundaries.db or manifest.json: merging the packs into a
# release is the pipeline's job (#968 Phase 1, contributions override
# GSAK-derived data per country and layer).
#
# Usage (needs pyosmium from the dev dependencies: pip install -e ".[dev]"):
#
#   .venv/bin/python3 tools/boundaries/osm_extract.py CH
#   .venv/bin/python3 tools/boundaries/osm_extract.py CH --compare /path/to/CHE \
#       --check-points location_gaps.csv
#
#   --pbf FILE         use a local .osm.pbf (or .osm) instead of downloading
#   --compare DIR      compare with existing packs (DIR/states/<iso3>.geojson,
#                      DIR/counties/<iso3>_<pack>.geojson), e.g. the GSAK-derived ones
#   --check-points CSV compare with names known from caches: the CSV written by the
#                      "Fill missing location" macro (lat, lon, level, known_value,
#                      known_from) — rows from Pocket Queries carry geocaching.com's names
#
# The download is cached in --cache-dir and reused for a day (--refresh to force).
# Geofabrik extracts are updated daily; please don't download more often than needed.

from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import json
import re
import sys
import tomllib
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import shapely
from shapely.prepared import prep
from shapely.geometry import mapping as shp_mapping
from shapely.geometry.base import BaseGeometry

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
DEFAULT_CONFIG = _HERE / "osm_countries.toml"
DEFAULT_OUT = _REPO_ROOT / "data" / "osm"
DEFAULT_CACHE = _REPO_ROOT / "data" / "osm" / "cache"
GEOFABRIK_URL = "https://download.geofabrik.de/{path}-latest.osm.pbf"
USER_AGENT = "OpenSAK-boundary-tools (+https://github.com/OpenSAK-Org/OpenSAK)"

# Same baseline tolerance as gsak_to_opensak.py (~55 m at the equator).
DEFAULT_STATE_TOLERANCE = 0.0005
# An outer ring narrower than this survives as a sliver, not a region.
MIN_AREA = 1e-9


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class CountryConfig:
    code: str                       # ISO 3166-1 alpha-2, e.g. "CH"
    iso3: str                       # OpenSAK country code, e.g. "che"
    state_level: int
    county_level: int | None = None
    geofabrik: str | None = None
    county_pack: str = ""
    name_tags: list[str] = field(default_factory=lambda: ["name"])
    county_name_tags: list[str] | None = None      # default: name_tags
    county_strip_prefixes: list[str] = field(default_factory=list)
    state_name_split: str | None = None
    county_name_split: str | None = None
    county_name_join: str | None = None
    names: dict[str, str] = field(default_factory=dict)
    county_exclude: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.county_pack:
            self.county_pack = f"all_{self.iso3}"

    @property
    def state_pack_name(self) -> str:
        return f"{self.iso3}.geojson"

    @property
    def county_pack_name(self) -> str:
        return f"{self.iso3}_{self.county_pack}.geojson"


def load_config(path: Path, code: str) -> CountryConfig:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    code = code.upper()
    if code not in data:
        known = ", ".join(sorted(data)) or "none"
        raise SystemExit(f"{code} is not in {path} (configured: {known})")
    entry: dict[str, Any] = dict(data[code])
    try:
        return CountryConfig(code=code, **entry)
    except TypeError as exc:   # unknown or missing key
        raise SystemExit(f"{path} [{code}]: {exc}") from exc


# ── Names ─────────────────────────────────────────────────────────────────────

def pick_name(tags: dict[str, str], cfg: CountryConfig, layer: str) -> str | None:
    """The OpenSAK name for a boundary: first of name_tags, then split/join, then renames."""
    keys = cfg.county_name_tags if layer == "county" and cfg.county_name_tags else cfg.name_tags
    raw = next((tags[t].strip() for t in keys if tags.get(t, "").strip()), None)
    if raw is None:
        return None
    name = raw
    if layer == "county":
        for prefix in cfg.county_strip_prefixes:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix):].strip()
                break
    split = cfg.state_name_split if layer == "state" else cfg.county_name_split
    join = cfg.county_name_join if layer == "county" else None
    if split and split in name:
        parts = [p.strip() for p in name.split(split)]
        name = join.join(parts) if join is not None else parts[0]
    # A rename may be keyed by the raw OSM name or by the name after splitting.
    return cfg.names.get(raw, cfg.names.get(name, name))


def normalize(name: str) -> str:
    """Case, accents and punctuation ignored — like the macro API's region matching."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-z]+", "", text.casefold())


# ── Reading OSM ───────────────────────────────────────────────────────────────

@dataclass
class Boundary:
    osm_id: str                      # "r51701"
    level: int
    tags: dict[str, str]
    geom: BaseGeometry


def read_boundaries(pbf: Path, levels: set[int]) -> tuple[list[Boundary], int]:
    """All boundary=administrative relations at one of `levels` (2 is always included),
    and the number of closed ways with those tags that were left out.

    Administrative boundaries are relations in OSM. A closed way tagged
    boundary=administrative is nearly always a mistagged building or site
    (in Switzerland: "Gemeindehaus", "Statistisches Amt", ...)."""
    import osmium  # optional dependency, only needed here

    wanted = {str(level) for level in levels | {2}}
    factory = osmium.geom.WKBFactory()
    result: list[Boundary] = []
    skipped_ways = 0
    processor = (
        osmium.FileProcessor(str(pbf))
        .with_areas(osmium.filter.TagFilter(("boundary", "administrative")))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
    )
    for area in processor:
        if not isinstance(area, osmium.osm.Area):
            continue
        level = area.tags.get("admin_level", "")
        if level not in wanted:
            continue
        if area.from_way():
            skipped_ways += 1
            continue
        try:
            geom = shapely.from_wkb(bytes.fromhex(factory.create_multipolygon(area)))
        except RuntimeError:
            # An incomplete relation: at the edge of the extract, or broken in OSM.
            continue
        result.append(Boundary(f"r{area.orig_id()}", int(level),
                               {t.k: t.v for t in area.tags}, geom))
    return result, skipped_ways


def osm_timestamp(pbf: Path) -> str | None:
    """The replication timestamp in the file header (when the extract was cut)."""
    try:
        import osmium
        reader = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING)
        try:
            stamp = reader.header().get("osmosis_replication_timestamp")
        finally:
            reader.close()
        return stamp or None
    except Exception:  # noqa: BLE001 — provenance is nice to have, never fatal
        return None


def find_country(boundaries: list[Boundary], code: str) -> Boundary:
    for b in boundaries:
        if b.level == 2 and code in (b.tags.get("ISO3166-1:alpha2"), b.tags.get("ISO3166-1")):
            return b
    raise SystemExit(f"No admin_level=2 boundary with ISO3166-1 = {code} in the extract")


# ── Building the packs ────────────────────────────────────────────────────────

@dataclass
class Region:
    name: str
    osm_ids: list[str]
    geom: BaseGeometry
    code: str | None = None          # "AG" from ISO3166-2 = "CH-AG" (states)


def collect_layer(boundaries: list[Boundary], country: BaseGeometry, level: int,
                  cfg: CountryConfig, layer: str, warnings: list[str],
                  states: list[Region] | None = None) -> list[Region]:
    """The regions of one admin_level inside the country.

    Two regions with the same name are different places (Reinach AG and
    Reinach BL), never merged: each gets the code of its state appended,
    "Reinach (AG)", the way Swiss official names do it."""
    prepared = prep(country)
    regions: list[Region] = []
    excluded: list[str] = []
    for b in boundaries:
        if b.level != level:
            continue
        # Extracts carry pieces of neighbouring countries; keep what lies inside.
        if not prepared.contains(b.geom.representative_point()):
            continue
        name = pick_name(b.tags, cfg, layer)
        if name is None:
            warnings.append(f"{layer} {b.osm_id} has no name — skipped")
            continue
        if layer == "county" and name in cfg.county_exclude:
            excluded.append(name)
            continue
        iso = b.tags.get("ISO3166-2", "")
        code = iso.split("-", 1)[1] if "-" in iso else None
        regions.append(Region(name, [b.osm_id], b.geom, code))
    if excluded:
        warnings.append(f"{layer}: left out as configured (county_exclude): "
                        + ", ".join(sorted(excluded)))

    by_name: dict[str, list[Region]] = {}
    for r in regions:
        by_name.setdefault(r.name, []).append(r)
    for name, same in by_name.items():
        if len(same) < 2:
            continue
        codes = [_state_code(r.geom, states) for r in same]
        if None in codes or len(set(codes)) < len(codes):   # no state, or the same state
            codes = [r.osm_ids[0] for r in same]
        for r, code in zip(same, codes):
            r.name = f"{name} ({code})"
        warnings.append(f"{layer} '{name}' is the name of {len(same)} regions — renamed to "
                        + ", ".join(r.name for r in same))
    return sorted(regions, key=lambda r: normalize(r.name))


def _state_code(geom: BaseGeometry, states: list[Region] | None) -> str | None:
    point = geom.representative_point()
    for s in states or []:
        if s.code and s.geom.contains(point):
            return s.code
    return None


def _repair(geom: BaseGeometry) -> BaseGeometry:
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    # make_valid can return a collection with lines/points; keep the polygons.
    if isinstance(geom, shapely.GeometryCollection):
        geom = shapely.union_all([g for g in geom.geoms
                                  if g.geom_type in ("Polygon", "MultiPolygon")])
    return geom


def simplify_layer(regions: list[Region], tolerance: float, warnings: list[str]) -> None:
    """Simplify as a coverage, so neighbours keep sharing their borders (no gaps, #782)."""
    if tolerance > 0 and regions:
        geoms = [r.geom for r in regions]
        simplified: list[BaseGeometry]
        try:
            simplified = list(shapely.coverage_simplify(geoms, tolerance))
        except Exception as exc:  # noqa: BLE001 — not a clean coverage: fall back
            warnings.append(f"coverage simplification failed ({exc}); "
                            "simplified each region on its own")
            simplified = [g.simplify(tolerance, preserve_topology=True) for g in geoms]
        for region, geom in zip(regions, simplified):
            region.geom = geom
    for region in regions:
        region.geom = _repair(region.geom)
        if region.geom.is_empty or region.geom.area < MIN_AREA:
            warnings.append(f"'{region.name}' is empty after simplification")


def to_feature(region: Region, cfg: CountryConfig, version: int, layer: str) -> dict[str, Any]:
    geom = region.geom
    if isinstance(geom, shapely.Polygon):
        geom = shapely.MultiPolygon([geom])
    minx, miny, maxx, maxy = geom.bounds
    if maxx - minx > 180:
        raise SystemExit(f"'{region.name}' crosses the antimeridian — not supported yet")
    return {
        "type": "Feature",
        "properties": {
            "name": region.name,
            "parent": cfg.iso3,
            "version": version,
            "source": "osm",
            "licence": "ODbL",
            "layer": layer,
            "osm_id": ",".join(region.osm_ids),
        },
        "bbox": [round(minx, 7), round(miny, 7), round(maxx, 7), round(maxy, 7)],
        "geometry": shp_mapping(shapely.set_precision(geom, 1e-7)),
    }


def write_pack(path: Path, features: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


# ── Comparison with existing packs and with names from caches ─────────────────

def _load_pack(path: Path) -> list[tuple[str, BaseGeometry]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(f["properties"]["name"], _repair(shapely.geometry.shape(f["geometry"])))
            for f in data["features"]]


class Resolver:
    """Point → region name, for one layer."""

    def __init__(self, regions: list[tuple[str, BaseGeometry]]) -> None:
        self.names = [n for n, _ in regions]
        self.geoms = [g for _, g in regions]
        self.tree = shapely.STRtree(self.geoms)

    def names_at(self, point: BaseGeometry) -> list[str]:
        idx = self.tree.query(point, predicate="intersects")
        return sorted(self.names[i] for i in idx)


def _iou(a: BaseGeometry, b: BaseGeometry) -> float:
    union = shapely.union(a, b).area
    return shapely.intersection(a, b).area / union if union else 0.0


def _rename_hint(geom: BaseGeometry, name: str, only_ref: list[str],
                 ref_by: dict[str, BaseGeometry]) -> str:
    """Which reference region this OSM-only region probably is: same area, else similar name."""
    best, best_iou = None, 0.5
    overlapping: dict[str, str] = {}     # a renamed region is still in the same place
    for ref_name in only_ref:
        other = ref_by[ref_name]
        if not geom.intersects(other) or shapely.intersection(geom, other).area <= 0:
            continue                     # elsewhere, or a neighbour that only touches
        overlapping[normalize(ref_name)] = ref_name
        iou = _iou(geom, other)
        if iou > best_iou:
            best, best_iou = ref_name, iou
    if best is not None:
        return f" — same area as the reference's '{best}' ({best_iou:.0%} overlap)"
    close = difflib.get_close_matches(normalize(name), list(overlapping), n=1, cutoff=0.85)
    return f" — similar name in the reference: '{overlapping[close[0]]}'" if close else ""


def match_names(osm_names: list[str], ref_names: list[str]) -> dict[str, str]:
    """Reference name -> OSM name: equal names first, then names that differ only in
    case, accents or punctuation ("Basel Stadt" / "Basel-Stadt") when that is unambiguous.
    Equal names come first because real regions can differ by an accent only
    (Brugg and Brügg are two Swiss municipalities)."""
    pairs = {n: n for n in set(ref_names) & set(osm_names)}
    rest_osm: dict[str, list[str]] = {}
    for n in set(osm_names) - set(pairs.values()):
        rest_osm.setdefault(normalize(n), []).append(n)
    rest_ref: dict[str, list[str]] = {}
    for n in set(ref_names) - set(pairs):
        rest_ref.setdefault(normalize(n), []).append(n)
    for key, refs in rest_ref.items():
        if len(refs) == 1 and len(rest_osm.get(key, [])) == 1:
            pairs[refs[0]] = rest_osm[key][0]
    return pairs


def compare_layer(layer: str, osm: list[Region], ref: list[tuple[str, BaseGeometry]]) -> list[str]:
    """Markdown lines comparing the OSM regions of one layer with a reference pack."""
    lines = [f"### {layer.capitalize()}: OSM {len(osm)} vs reference {len(ref)}", ""]
    osm_by = {r.name: r.geom for r in osm}
    ref_by = dict(ref)
    pairs = match_names(list(osm_by), list(ref_by))

    only_osm = sorted(set(osm_by) - set(pairs.values()), key=normalize)
    only_ref = sorted(set(ref_by) - set(pairs), key=normalize)
    if only_osm:
        lines.append(f"**Only in OSM ({len(only_osm)}):**")
        for n in only_osm:
            lines.append(f"- {n}{_rename_hint(osm_by[n], n, only_ref, ref_by)}")
        lines.append("")
    if only_ref:
        lines.append(f"**Only in the reference ({len(only_ref)}):** " + ", ".join(only_ref))
        lines.append("")
    spelled = sorted((r, o) for r, o in pairs.items() if r != o)
    if spelled:
        lines.append(f"**Spelled differently ({len(spelled)}), reference → OSM:** "
                     + ", ".join(f"{r} → {o}" for r, o in spelled))
        lines.append("")

    # Same region, different shape (intersection over union). When one polygon
    # lies almost entirely inside the other, the larger one has an extra piece
    # (often lake area: the GSAK-derived municipalities reach into the lakes).
    differ: list[tuple[float, str]] = []
    ref_larger: list[str] = []
    osm_larger: list[str] = []
    for r, o in pairs.items():
        a, b = osm_by[o], ref_by[r]
        iou = _iou(a, b)
        if iou >= 0.9:
            continue
        shared = shapely.intersection(a, b).area
        if a.area and shared / a.area >= 0.95:
            ref_larger.append(o)
        elif b.area and shared / b.area >= 0.95:
            osm_larger.append(o)
        else:
            differ.append((iou, o))
    n_shape = len(differ) + len(ref_larger) + len(osm_larger)
    lines.append(f"Matched: {len(pairs)}; of those {n_shape} differ in shape (overlap below 90 %).")
    lines.append("")
    if ref_larger:
        lines.append(f"**The reference is larger ({len(ref_larger)})** — OSM's polygon lies "
                     "inside it; often lake area: " + ", ".join(sorted(ref_larger, key=normalize)))
        lines.append("")
    if osm_larger:
        lines.append(f"**OSM is larger ({len(osm_larger)})** — the reference lies inside it; "
                     "often a merger: " + ", ".join(sorted(osm_larger, key=normalize)))
        lines.append("")
    if differ:
        lines.append(f"**Different shape ({len(differ)}):**")
        for iou, name in sorted(differ)[:40]:
            lines.append(f"- {name}: {iou:.0%} overlap")
        if len(differ) > 40:
            lines.append(f"- … and {len(differ) - 40} more")
        lines.append("")

    # Where would a cache in the middle of the reference region end up?
    resolver = Resolver([(r.name, r.geom) for r in osm])
    moved: list[str] = []
    for name, geom in ref:
        found = resolver.names_at(geom.representative_point())
        if pairs.get(name, name) not in found:
            moved.append(f"- {name} → {', '.join(found) or 'nothing'}")
    lines.append(f"A point inside each reference {layer} resolves to another name in OSM: "
                 f"{len(moved)} of {len(ref)}" + (":" if moved else "."))
    lines.extend(moved[:40])
    if len(moved) > 40:
        lines.append(f"- … and {len(moved) - 40} more")
    lines.append("")
    return lines


def check_points(csv_path: Path, country: BaseGeometry,
                 layers: dict[str, list[Region]]) -> list[str]:
    """Compare names from the gap-report CSV (macro 'Fill missing location') with OSM."""
    resolvers = {layer: Resolver([(r.name, r.geom) for r in regions])
                 for layer, regions in layers.items() if regions}
    prepared = prep(country)
    stats: dict[tuple[str, str], list[int]] = {}
    mismatches: dict[tuple[str, str, str], int] = {}
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            layer, known = row.get("level", ""), (row.get("known_value") or "").strip()
            source = row.get("known_from") or "?"
            if layer not in resolvers or not known:
                continue
            point = shapely.Point(float(row["lon"]), float(row["lat"]))
            if not prepared.contains(point):
                continue
            found = resolvers[layer].names_at(point)
            ok = any(normalize(f) == normalize(known) for f in found)
            s = stats.setdefault((layer, source), [0, 0])
            s[0 if ok else 1] += 1
            if not ok:
                key = (layer, known, ", ".join(found) or "nothing")
                mismatches[key] = mismatches.get(key, 0) + 1
    lines = [f"### Names known from caches ({csv_path.name})", ""]
    if not stats:
        return lines + ["No rows with a known name inside the country.", ""]
    lines.append("| Level | Known from | Same name | Other name |")
    lines.append("|---|---|---|---|")
    for (level, source), (same, other) in sorted(stats.items()):
        lines.append(f"| {level} | {source} | {same} | {other} |")
    lines.append("")
    if mismatches:
        lines.append("Different names (known → OSM, number of caches):")
        for (level, name, osm_names), n in sorted(mismatches.items(), key=lambda kv: -kv[1])[:40]:
            lines.append(f"- {level}: {name} → {osm_names} ({n})")
        lines.append("")
    return lines


# ── Download ──────────────────────────────────────────────────────────────────

def download(cfg: CountryConfig, cache_dir: Path, refresh: bool) -> Path:
    if not cfg.geofabrik:
        raise SystemExit(f"[{cfg.code}] has no 'geofabrik' path — pass --pbf")
    dest = cache_dir / (cfg.geofabrik.replace("/", "_") + "-latest.osm.pbf")
    if dest.exists() and not refresh:
        age = dt.datetime.now() - dt.datetime.fromtimestamp(dest.stat().st_mtime)
        if age < dt.timedelta(days=1):
            print(f"Using cached {dest} ({age.seconds // 3600} h old)")
            return dest
    url = GEOFABRIK_URL.format(path=cfg.geofabrik)
    print(f"Downloading {url} …")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as resp, tmp.open("wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    tmp.replace(dest)
    print(f"  {dest.stat().st_size / 1e6:.0f} MB")
    return dest


# ── Main ──────────────────────────────────────────────────────────────────────

def run(cfg: CountryConfig, pbf: Path, out_dir: Path, *, version: int,
        state_tolerance: float, county_tolerance: float,
        compare_dir: Path | None = None, points_csv: Path | None = None,
        source_url: str | None = None) -> dict[str, Any]:
    levels = {cfg.state_level} | ({cfg.county_level} if cfg.county_level else set())
    print(f"Reading administrative boundaries from {pbf.name} …")
    boundaries, skipped_ways = read_boundaries(pbf, levels)
    country = find_country(boundaries, cfg.code)
    warnings: list[str] = []
    if skipped_ways:
        warnings.append(f"{skipped_ways} closed ways tagged boundary=administrative left out "
                        "(administrative boundaries are relations; these are mostly buildings)")

    layers: dict[str, list[Region]] = {
        "state": collect_layer(boundaries, country.geom, cfg.state_level, cfg, "state", warnings),
    }
    if cfg.county_level:
        layers["county"] = collect_layer(boundaries, country.geom, cfg.county_level,
                                         cfg, "county", warnings, states=layers["state"])
    simplify_layer(layers["state"], state_tolerance, warnings)
    if "county" in layers:
        simplify_layer(layers["county"], county_tolerance, warnings)

    files: dict[str, str] = {}
    pack_names = {"state": cfg.state_pack_name, "county": cfg.county_pack_name}
    dirs = {"state": "states", "county": "counties"}
    for layer, regions in layers.items():
        if not regions:
            warnings.append(f"no {layer} boundaries at admin_level "
                            f"{cfg.state_level if layer == 'state' else cfg.county_level}")
            continue
        path = out_dir / dirs[layer] / pack_names[layer]
        write_pack(path, [to_feature(r, cfg, version, layer) for r in regions])
        files[layer] = str(path.relative_to(out_dir))

    timestamp = osm_timestamp(pbf)
    meta: dict[str, Any] = {
        "country": cfg.code, "iso3": cfg.iso3,
        "source": "OpenStreetMap", "licence": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
        "extract": source_url or pbf.name, "osm_timestamp": timestamp,
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "admin_levels": {"state": cfg.state_level, "county": cfg.county_level},
        "counts": {layer: len(regions) for layer, regions in layers.items()},
        "files": files, "version": version,
    }
    (out_dir / f"osm_extract_{cfg.iso3}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    report = [f"# OSM boundaries for {cfg.code} ({cfg.iso3})", "",
              f"- Extract: {meta['extract']}", f"- OSM data as of: {timestamp or 'unknown'}",
              f"- States (admin_level {cfg.state_level}): {len(layers['state'])}"]
    if "county" in layers:
        report.append(f"- Counties (admin_level {cfg.county_level}): {len(layers['county'])}")
    report.append("")
    if warnings:
        report += ["## Warnings", ""] + [f"- {w}" for w in warnings] + [""]
    if compare_dir is not None:
        report += ["## Comparison with the reference packs", "", f"Reference: `{compare_dir}`", ""]
        for layer, regions in layers.items():
            ref_path = compare_dir / dirs[layer] / pack_names[layer]
            if ref_path.exists():
                report += compare_layer(layer, regions, _load_pack(ref_path))
            else:
                report += [f"### {layer.capitalize()}: no reference file {ref_path.name}", ""]
    if points_csv is not None:
        report += ["## Comparison with names from caches", ""]
        report += check_points(points_csv, country.geom, layers)
    report_path = out_dir / f"osm_extract_{cfg.iso3}_report.md"
    report_path.write_text("\n".join(report), encoding="utf-8")
    meta["report"] = str(report_path)
    meta["warnings"] = warnings
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("country", help="ISO 3166-1 alpha-2 code configured in the config file, e.g. CH")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pbf", type=Path, help="local .osm.pbf/.osm instead of downloading")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--refresh", action="store_true", help="download even if a cached copy is fresh")
    parser.add_argument("--compare", type=Path, metavar="DIR", help="existing packs to compare with")
    parser.add_argument("--check-points", type=Path, metavar="CSV",
                        help="gap-report CSV from the 'Fill missing location' macro")
    parser.add_argument("--version", type=int, default=1, help="polygon version written to each feature")
    parser.add_argument("--state-tolerance", type=float, default=DEFAULT_STATE_TOLERANCE,
                        metavar="DEGREES", help="simplification of the state layer (default: %(default)s)")
    parser.add_argument("--county-tolerance", type=float, default=0.0,
                        metavar="DEGREES", help="simplification of the county layer (default: none)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config, args.country)
    source_url = None
    if args.pbf:
        pbf = args.pbf
    else:
        pbf = download(cfg, args.cache_dir, args.refresh)
        source_url = GEOFABRIK_URL.format(path=cfg.geofabrik)
    meta = run(cfg, pbf, args.out_dir / cfg.iso3, version=args.version,
               state_tolerance=args.state_tolerance, county_tolerance=args.county_tolerance,
               compare_dir=args.compare, points_csv=args.check_points, source_url=source_url)
    for layer, count in meta["counts"].items():
        print(f"{layer}: {count}")
    for w in meta["warnings"]:
        print(f"warning: {w}")
    print(f"Packs and report in {args.out_dir / cfg.iso3}")
    print(f"Report: {meta['report']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
