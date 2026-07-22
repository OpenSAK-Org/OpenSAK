#!/usr/bin/env python3
"""
scripts/benchmark_large_db.py — Large-database performance benchmark harness.

Part of #628 (large-database performance meta-issue #627).

Why this exists: @nagisml's manual benchmark on #579 showed that the
distance-recalculation fix only accounts for a small slice of total load
time on a large database — apply_filters() (~9.5s) and map load (~8.6s)
dominate. Every follow-up optimization in #627 (icon caching, map bulk
loading, skipped redundant filtering, the lightweight query path) needs to
be measured against the same baseline, on the same synthetic data, or we're
guessing instead of measuring.

This script:
  1. Generates a synthetic OpenSAK database at a configurable scale (default
     250,000 caches, each with a random number of logs/attributes/
     trackables, scattered both near and far from a home point so
     distance-filtering scenarios are meaningful).
  2. Measures the same steps as @nagisml's benchmark comment:
       - distance recalc (cold, full)
       - distance spot-check (warm — confirms #579's skip path is taken)
       - distance recalc (invalidated — home point changed, fallback path)
       - DB query / apply_filters, for three scenarios (None, exclude
         archived, distance-filtered)
       - map load (Python-side payload build — JSON + pin-icon generation)
       - table load (CacheTableModel)
       - info-bar update
  3. Prints a table in the same format as the #579 benchmark comment, so
     results can be pasted directly into issue comments for before/after
     comparisons.

Safety: this script NEVER touches your real OpenSAK settings or databases.
It isolates the settings store to a throwaway temp directory and only ever
opens the synthetic database file you point it at.

Usage:
    source .venv/bin/activate
    python scripts/benchmark_large_db.py
    python scripts/benchmark_large_db.py --cache-count 250000 --keep
    python scripts/benchmark_large_db.py --db-path /tmp/bench.sqlite --skip-generate

Steps 5 and 6 (map load, table load) need PySide6 with
QT_QPA_PLATFORM=offscreen; if that's not available they're skipped with a
warning and the rest of the benchmark still runs:

    QT_QPA_PLATFORM=offscreen python scripts/benchmark_large_db.py

Note on scope: the map-load step measures the real Python-side production
code path (get_map_pin_html() via map_widget._cache_pin_html(), JSON
building, template-literal escaping) — exactly what #629 and the Python
side of #630 touch. It does NOT measure actual Leaflet/JS marker-clustering
time inside the browser (that's #630's chunkedLoading/addLayers() work) —
QtWebEngine's JS execution isn't something this headless script can time
synchronously. Verify that part manually in the running app.
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from sqlalchemy import text  # noqa: E402

# ── Synthetic data pools ─────────────────────────────────────────────────────

HOME_LAT, HOME_LON = 55.6761, 12.5683  # Copenhagen — matches settings.py default

CACHE_TYPES = [
    "Traditional Cache", "Multi-cache", "Unknown Cache", "Earthcache",
    "Letterbox Hybrid", "Wherigo Cache", "Virtual Cache", "Event Cache",
]
CONTAINERS = ["Nano", "Micro", "Small", "Regular", "Large", "Not chosen", "Other"]
RATINGS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
LOG_TYPES = ["Found it", "Didn't find it", "Write note", "Owner Maintenance"]
COUNTRIES = ["Denmark", "Sweden", "Germany", "Norway", "Netherlands"]
ATTRIBUTE_POOL = [
    (1, "Dogs allowed"), (2, "Bicycles"), (4, "Kids friendly"),
    (7, "Wheelchair accessible"), (13, "Available 24-7"), (24, "Night cache"),
    (32, "Poison plants"), (43, "Field puzzle"),
]


@dataclass
class GenConfig:
    cache_count: int
    seed: int
    near_fraction: float = 0.7   # fraction of caches scattered near the home point
    near_radius_km: float = 100.0
    logs_low_max: int = 10       # most caches: 0-10 logs
    logs_low_prob: float = 0.70
    logs_mid_max: int = 40       # some caches: 10-40 logs
    logs_mid_prob: float = 0.25
    logs_high_max: int = 150     # a few "power" caches: 40-150 logs
    chunk_size: int = 5000


def _rand_latlon_near(rng: random.Random, lat0: float, lon0: float, radius_km: float) -> tuple[float, float]:
    """Uniform-ish random point within *radius_km* of (lat0, lon0)."""
    import math
    r = radius_km * math.sqrt(rng.random()) / 111.0  # ~111 km per degree latitude
    theta = rng.random() * 2 * math.pi
    dlat = r * math.cos(theta)
    dlon = r * math.sin(theta) / max(math.cos(math.radians(lat0)), 0.01)
    return lat0 + dlat, lon0 + dlon


def _rand_latlon_anywhere(rng: random.Random) -> tuple[float, float]:
    return rng.uniform(-60.0, 70.0), rng.uniform(-170.0, 170.0)


def _rand_log_count(rng: random.Random, cfg: GenConfig) -> int:
    roll = rng.random()
    if roll < cfg.logs_low_prob:
        return rng.randint(0, cfg.logs_low_max)
    elif roll < cfg.logs_low_prob + cfg.logs_mid_prob:
        return rng.randint(cfg.logs_low_max, cfg.logs_mid_max)
    return rng.randint(cfg.logs_mid_max, cfg.logs_high_max)


def generate_database(db_path: Path, cfg: GenConfig) -> None:
    """Build a synthetic OpenSAK database at *db_path* with *cfg.cache_count* caches."""
    from datetime import datetime, timedelta, timezone

    from opensak.db.database import init_db
    from opensak.db.models import Attribute, Cache, Log, Trackable

    if db_path.exists():
        db_path.unlink()
    for suffix in ("-shm", "-wal"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()

    engine = init_db(db_path)
    rng = random.Random(cfg.seed)

    with engine.begin() as conn:
        # Throwaway synthetic DB — full durability is not needed while generating.
        conn.execute(text("PRAGMA synchronous=OFF"))
        conn.execute(text("PRAGMA cache_size=-131072"))  # ~128 MB page cache

    cache_rows: list[dict] = []
    log_rows: list[dict] = []
    attr_rows: list[dict] = []
    tb_rows: list[dict] = []

    log_id_seq = 1
    now = datetime.now(timezone.utc)

    from sqlalchemy import insert as _sa_insert

    def _flush(conn) -> None:
        nonlocal cache_rows, log_rows, attr_rows, tb_rows
        if cache_rows:
            conn.execute(_sa_insert(Cache), cache_rows)
            cache_rows = []
        if log_rows:
            conn.execute(_sa_insert(Log), log_rows)
            log_rows = []
        if attr_rows:
            conn.execute(_sa_insert(Attribute), attr_rows)
            attr_rows = []
        if tb_rows:
            conn.execute(_sa_insert(Trackable), tb_rows)
            tb_rows = []

    with engine.begin() as conn:
        for i in range(1, cfg.cache_count + 1):
            cache_id = i
            near = rng.random() < cfg.near_fraction
            lat, lon = (
                _rand_latlon_near(rng, HOME_LAT, HOME_LON, cfg.near_radius_km)
                if near else _rand_latlon_anywhere(rng)
            )
            archived = rng.random() < 0.05
            available = archived or rng.random() > 0.03
            found = rng.random() < 0.30
            n_logs = _rand_log_count(rng, cfg)

            cache_rows.append(dict(
                id=cache_id,
                gc_code=f"GC{cache_id:06X}",
                name=f"Benchmark Cache {cache_id}",
                cache_type=rng.choice(CACHE_TYPES),
                container=rng.choice(CONTAINERS),
                latitude=lat,
                longitude=lon,
                difficulty=rng.choice(RATINGS),
                terrain=rng.choice(RATINGS),
                placed_by=f"Owner{cache_id % 500}",
                owner_name=f"Owner{cache_id % 500}",
                owner_id=str(cache_id % 500),
                hidden_date=now - timedelta(days=rng.randint(30, 4000)),
                last_updated=now,
                available=available,
                archived=archived,
                premium_only=rng.random() < 0.05,
                short_description="A synthetic benchmark cache.",
                short_desc_html=False,
                long_description="Placeholder long description text for benchmarking.",
                long_desc_html=False,
                encoded_hints=None,
                country=rng.choice(COUNTRIES),
                state=None,
                county=None,
                found=found,
                found_date=(now - timedelta(days=rng.randint(0, 1000))) if found else None,
                dnf=(not found) and rng.random() < 0.1,
                dnf_date=None,
                first_to_find=found and rng.random() < 0.02,
                user_flag=rng.random() < 0.05,
                user_sort=None,
                user_data_1=None, user_data_2=None, user_data_3=None, user_data_4=None,
                distance=None,
                bearing=None,
                log_count=n_logs,
                trackable_count=0,
                found_log_count=1 if found else 0,
                waypoint_count=0,
                last_log_date=(now - timedelta(days=rng.randint(0, 60))) if n_logs else None,
                source_file="benchmark_large_db.py",
                locked=False,
            ))

            for j in range(n_logs):
                log_rows.append(dict(
                    id=log_id_seq,
                    cache_id=cache_id,
                    log_id=f"bench_{cache_id}_{j}",
                    log_type=rng.choice(LOG_TYPES),
                    log_date=now - timedelta(days=rng.randint(0, 2000)),
                    finder=f"Finder{rng.randint(0, 5000)}",
                    finder_id=str(rng.randint(0, 5000)),
                    text="Great cache, thanks for the hide!",
                    text_encoded=False,
                    latitude=None,
                    longitude=None,
                    logged_by_owner=False,
                ))
                log_id_seq += 1

            for attr_id, attr_name in rng.sample(ATTRIBUTE_POOL, k=rng.randint(0, 4)):
                attr_rows.append(dict(
                    cache_id=cache_id,
                    attribute_id=attr_id,
                    name=attr_name,
                    is_on=rng.random() < 0.8,
                ))

            if rng.random() < 0.10:
                tb_rows.append(dict(
                    cache_id=cache_id,
                    ref=f"TB{cache_id}A",
                    name=f"Travel Bug {cache_id}",
                ))

            if i % cfg.chunk_size == 0:
                _flush(conn)
        _flush(conn)

    with engine.begin() as conn:
        conn.execute(text("PRAGMA synchronous=FULL"))
        conn.execute(text("PRAGMA cache_size=-2000"))
        conn.execute(text("ANALYZE"))


# ── Settings isolation (never touch the real user's config) ─────────────────

def _isolate_settings(tmp_dir: Path) -> None:
    from opensak import settings_store as ss
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_dir / "opensak.json"
    ss._store = fresh

    import opensak.gui.settings as smod
    smod._settings = None

    import opensak.db.manager as mgr
    mgr._manager = None


# ── Benchmark steps ───────────────────────────────────────────────────────────

@dataclass
class StepResult:
    label: str
    seconds: float | None
    detail: str = ""


_T = TypeVar("_T")


def _timed(label: str, fn: Callable[..., _T], *args, **kwargs) -> tuple[StepResult, _T]:
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    dt = time.perf_counter() - t0
    return StepResult(label, dt), result


def bench_distance_recalc() -> list[StepResult]:
    from opensak.db.database import distances_up_to_date, recalculate_distances

    results = []

    r, n = _timed("Distance recalc (cold, full)", recalculate_distances, HOME_LAT, HOME_LON)
    r.detail = f"{n} caches updated"
    results.append(r)

    r, up_to_date = _timed("Distance spot-check (warm)", distances_up_to_date, HOME_LAT, HOME_LON)
    r.detail = "up to date, skipped" if up_to_date else "WARNING: expected up-to-date"
    results.append(r)

    # Simulate a database synced from elsewhere with a different home point —
    # distances_up_to_date() should report False, forcing the fallback path.
    other_lat, other_lon = HOME_LAT + 5.0, HOME_LON + 5.0
    r, invalidated = _timed("Distance spot-check (invalidated)", distances_up_to_date, other_lat, other_lon)
    r.detail = "correctly detected stale" if not invalidated else "WARNING: expected stale"
    results.append(r)

    r2, n2 = _timed("Distance recalc (after invalidation, full)", recalculate_distances, other_lat, other_lon)
    r2.detail = f"{n2} caches updated"
    results.append(r2)

    # Restore the original home point's persisted values for later steps.
    recalculate_distances(HOME_LAT, HOME_LON)

    return results


def bench_apply_filters() -> tuple[list[StepResult], list]:
    from opensak.db.database import get_session
    from opensak.filters.engine import ArchivedFilter, DistanceFilter, FilterSet, WhereClauseFilter, apply_filters

    results = []

    with get_session() as session:
        r, caches = _timed("apply_filters — no filter", apply_filters, session, None, None)
        r.detail = f"{len(caches)} caches"
        results.append(r)

    with get_session() as session:
        fs = FilterSet(mode="AND")
        fs.add(WhereClauseFilter("archived = 0"))
        r, caches = _timed("apply_filters — exclude archived", apply_filters, session, fs, None)
        r.detail = f"{len(caches)} caches"
        results.append(r)

    with get_session() as session:
        fs = FilterSet(mode="AND")
        fs.add(DistanceFilter(HOME_LAT, HOME_LON, max_km=50.0))
        r, caches = _timed("apply_filters — within 50km", apply_filters, session, fs, None)
        r.detail = f"{len(caches)} caches"
        results.append(r)

    return results, caches  # last (smallest) result set reused by later steps


def bench_map_load(caches: list) -> list[StepResult]:
    """Measure the Python-side map payload build (JSON + pin icons).

    Reuses the exact production functions map_widget._do_load_caches() calls,
    without needing a live QWebEngineView. Does NOT measure JS-side Leaflet
    clustering time — see module docstring.
    """
    try:
        import json as _json

        from opensak.gps.garmin import _effective_coords
        from opensak.gui.map_widget import _cache_pin_html
    except ImportError as exc:
        return [StepResult("Map load (Python-side payload)", None, f"skipped — {exc}")]

    def _build_payload(caches):
        data = []
        for c in caches:
            if c.latitude is None or c.longitude is None:
                continue
            note = getattr(c, "user_note", None)
            has_corrected = bool(note and getattr(note, "is_corrected", False))
            eff_lat, eff_lon = _effective_coords(c)
            data.append({
                "gc_code": c.gc_code, "name": c.name or "", "cache_type": c.cache_type or "",
                "difficulty": c.difficulty or 0, "terrain": c.terrain or 0,
                "lat": c.latitude, "lon": c.longitude, "clat": eff_lat, "clon": eff_lon,
                "corrected": has_corrected, "corrected_label": "Corrected",
                "pin_html": _cache_pin_html(c.cache_type or "", bool(c.found), bool(c.dnf)),
                "found": c.found,
            })
        json_str = _json.dumps(data, ensure_ascii=False)
        json_str = json_str.replace("\\", "\\\\").replace("`", "\\`")
        return json_str

    r, payload = _timed("Map load (Python-side payload)", _build_payload, caches)
    r.detail = f"{len(caches)} caches, {len(payload) / 1024:.0f} KB JSON"
    return [r]


def bench_table_load(caches: list) -> list[StepResult]:
    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from opensak.gui.cache_table import CacheTableModel
    except ImportError as exc:
        return [StepResult("Table load (CacheTableModel)", None, f"skipped — {exc}")]

    app = QApplication.instance() or QApplication([])
    model = CacheTableModel()
    r, _ = _timed("Table load (CacheTableModel)", model.load, caches)
    r.detail = f"{len(caches)} caches"
    return [r]


def bench_info_bar(caches: list) -> list[StepResult]:
    """Replicates mainwindow._update_info_bar()'s per-cache aggregate cost
    without needing a full MainWindow/GUI instance."""
    from opensak.db.database import get_session
    from opensak.db.models import Cache

    def _run() -> tuple[int, int, int, int]:
        with get_session() as session:
            total_in_db = session.query(Cache).count()
        found = sum(max(c.found_log_count, 1) for c in caches if c.found)
        flagged = sum(1 for c in caches if c.user_flag)
        inactive = sum(1 for c in caches if c.archived or not c.available)
        return total_in_db, found, flagged, inactive

    r, (total_in_db, found, flagged, inactive) = _timed("Info-bar update", _run)
    r.detail = f"{total_in_db} in DB, {found} found, {flagged} flagged, {inactive} inactive"
    return [r]


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_results(all_results: list[StepResult]) -> None:
    label_w = max(len(r.label) for r in all_results) + 2
    print()
    print(f"{'Step':<{label_w}}{'Time':>10}   Detail")
    print("-" * (label_w + 10 + 3 + 40))
    total = 0.0
    for r in all_results:
        time_str = f"{r.seconds:.4f}s" if r.seconds is not None else "—"
        print(f"{r.label:<{label_w}}{time_str:>10}   {r.detail}")
        if r.seconds is not None:
            total += r.seconds
    print("-" * (label_w + 10 + 3 + 40))
    print(f"{'Total':<{label_w}}{total:.2f}s")
    print()


def print_markdown_table(all_results: list[StepResult]) -> None:
    print("| Step | Time | Detail |")
    print("|---|---|---|")
    total = 0.0
    for r in all_results:
        time_str = f"{r.seconds:.4f}s" if r.seconds is not None else "—"
        print(f"| {r.label} | {time_str} | {r.detail} |")
        if r.seconds is not None:
            total += r.seconds
    print(f"| **Total** | **{total:.2f}s** | |")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-count", type=int, default=250_000, help="Number of synthetic caches to generate (default: 250000)")
    parser.add_argument("--db-path", type=Path, default=None, help="Path for the synthetic database (default: a temp file)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible generation")
    parser.add_argument("--keep", action="store_true", help="Don't delete the generated database afterwards")
    parser.add_argument("--skip-generate", action="store_true", help="Reuse an existing database at --db-path instead of generating a new one")
    parser.add_argument("--markdown", action="store_true", help="Also print results as a markdown table (for pasting into GitHub issues)")
    args = parser.parse_args()

    db_path = args.db_path or Path(tempfile.gettempdir()) / "opensak_benchmark.sqlite"
    tmp_settings_dir = Path(tempfile.mkdtemp(prefix="opensak_benchmark_settings_"))
    _isolate_settings(tmp_settings_dir)

    if not args.skip_generate:
        print(f"Generating {args.cache_count:,} synthetic caches at {db_path} (seed={args.seed})...")
        t0 = time.perf_counter()
        generate_database(db_path, GenConfig(cache_count=args.cache_count, seed=args.seed))
        print(f"  done in {time.perf_counter() - t0:.1f}s")
    else:
        from opensak.db.database import init_db
        init_db(db_path)
        print(f"Reusing existing database at {db_path}")

    from opensak.gui.settings import get_settings
    s = get_settings()
    s.home_lat = HOME_LAT
    s.home_lon = HOME_LON

    all_results: list[StepResult] = []
    all_results += bench_distance_recalc()

    filter_results, smallest_caches = bench_apply_filters()
    all_results += filter_results

    # map/table/info-bar steps run against the largest (unfiltered) result set,
    # matching @nagisml's "Total to caches shown" methodology.
    from opensak.db.database import get_session
    from opensak.filters.engine import apply_filters
    with get_session() as session:
        all_caches = apply_filters(session, None, None)

    all_results += bench_map_load(all_caches)
    all_results += bench_table_load(all_caches)
    all_results += bench_info_bar(all_caches)

    print_results(all_results)
    if args.markdown:
        print_markdown_table(all_results)

    if not args.keep and not args.skip_generate:
        db_path.unlink(missing_ok=True)
        for suffix in ("-shm", "-wal"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        print(f"(deleted {db_path} — pass --keep to retain it)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
