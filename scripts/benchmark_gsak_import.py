#!/usr/bin/env python3
"""
scripts/benchmark_gsak_import.py — where the time of a GSAK database import goes.

Imports a real GSAK ``sqlite.db3`` (or backup .zip) into a throwaway OpenSAK
database and reports, per scenario:

  - wall time of each import phase (loading the GSAK tables, mapping rows,
    writing batches, commits, custom fields, statistics, distances);
  - SQL time and statement counts per verb and table, measured on the
    target connection;
  - Python garbage-collector pauses;
  - peak memory of the process (the highest so far in this run).

Scenarios, run in this order on the same scratch database:

  fresh      import into a new, empty database (a new OpenSAK database)
  merge      the same import again — every cache already exists (re-import)
  overwrite  clear the database first, then import (the dialog's "overwrite")

Phase times are inclusive and nest: ``flush_batch`` contains ``upsert_cache``,
which contains the ORM ``flush`` calls and most of the SQL time. The row
``(cursor + rest)`` is what is left of the import loop: reading the GSAK
cursor (including the lenient text decoding) and loop overhead.

Safety: the GSAK database is opened read-only, the OpenSAK settings store is
isolated to a temp directory, and only the scratch database is written. Your
GC username / finder id and home point are *read* from the real settings so
the per-cache found-log count and the distance step do real work.

Usage:
    python scripts/benchmark_gsak_import.py %APPDATA%/gsak/data/MyCaches/sqlite.db3
    python scripts/benchmark_gsak_import.py MyCaches/sqlite.db3 --sample 20000
    python scripts/benchmark_gsak_import.py MyCaches/sqlite.db3 --scenarios fresh \\
        --profile fresh.prof
    python scripts/benchmark_gsak_import.py MyCaches/sqlite.db3 --json baseline.json

``--sample N`` first copies the first N caches (by code) and everything that
hangs off them into a temp GSAK database, for quick iterations.
"""

from __future__ import annotations

import argparse
import contextlib
import cProfile
import gc
import json
import pstats
import re
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

SCENARIOS = ("fresh", "merge", "overwrite")


# ── Measuring ────────────────────────────────────────────────────────────────

class Recorder:
    """Inclusive wall time and call count per phase name."""

    def __init__(self) -> None:
        self.time: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self.order: list[str] = []

    @contextlib.contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if name not in self.order:
            self.order.append(name)
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.time[name] += time.perf_counter() - t0
            self.calls[name] += 1

    def wrap(self, name: str, fn: Callable) -> Callable:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with self.phase(name):
                return fn(*args, **kwargs)
        wrapper.__wrapped__ = fn   # type: ignore[attr-defined]
        return wrapper


_SQL_TABLE_RE = re.compile(
    r"^\s*(INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|SELECT\b.*?\bFROM|PRAGMA|BEGIN|COMMIT|"
    r"RELEASE|SAVEPOINT|ROLLBACK)\s*([\w\"]*)",
    re.I | re.S,
)


def _sql_key(statement: str) -> str:
    m = _SQL_TABLE_RE.match(statement)
    if m is None:
        return statement.split(None, 1)[0].upper() if statement.strip() else "?"
    verb = m.group(1).split()[0].upper()
    table = m.group(2).strip('"').lower()
    return f"{verb} {table}".strip()


class SqlStats:
    """Time and count of statements on an engine, by verb + table."""

    def __init__(self) -> None:
        self.time: dict[str, float] = defaultdict(float)
        self.statements: dict[str, int] = defaultdict(int)

    def attach(self, engine) -> Callable[[], None]:
        from sqlalchemy import event

        def before(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("_bench_t0", []).append(time.perf_counter())

        def after(conn, cursor, statement, parameters, context, executemany):
            t0 = conn.info["_bench_t0"].pop()
            key = _sql_key(statement)
            self.time[key] += time.perf_counter() - t0
            self.statements[key] += 1

        event.listen(engine, "before_cursor_execute", before)
        event.listen(engine, "after_cursor_execute", after)

        def detach() -> None:
            event.remove(engine, "before_cursor_execute", before)
            event.remove(engine, "after_cursor_execute", after)
        return detach


class GcStats:
    """Count and total duration of garbage-collector runs, per generation."""

    def __init__(self) -> None:
        self.time: dict[int, float] = defaultdict(float)
        self.count: dict[int, int] = defaultdict(int)
        self._t0 = 0.0

    def __call__(self, stage: str, info: dict) -> None:
        if stage == "start":
            self._t0 = time.perf_counter()
        else:
            gen = info.get("generation", -1)
            self.time[gen] += time.perf_counter() - self._t0
            self.count[gen] += 1


def _peak_memory_mb() -> Optional[float]:
    """Peak working set (Windows) / max RSS (Unix) of this process, in MB."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.PeakWorkingSetSize / 1e6
        return None
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1e6 if sys.platform == "darwin" else peak / 1e3
    except ImportError:
        return None


# ── Setup ────────────────────────────────────────────────────────────────────

def _real_user_values() -> dict[str, Any]:
    """GC username / finder id and home point from the real settings (read only)."""
    from opensak.settings_store import get_store
    store = get_store()
    return {
        "user.gc_username":   store.get("user.gc_username", ""),
        "user.gc_finder_id":  store.get("user.gc_finder_id", ""),
        "location.home_lat":  store.get("location.home_lat", None),
        "location.home_lon":  store.get("location.home_lon", None),
    }


def _isolate_settings(tmp_dir: Path, values: dict[str, Any]) -> None:
    """Same isolation as benchmark_large_db.py, seeded with *values*."""
    from opensak import settings_store as ss
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_dir / "opensak.json"
    ss._store = fresh
    for key, value in values.items():
        if value not in (None, ""):
            fresh.set(key, value)

    import opensak.gui.settings as smod
    smod._settings = None

    import opensak.db.manager as mgr
    mgr._manager = None


# Tables copied by --sample, with the column that holds the cache code.
_SAMPLE_TABLES = {
    "Caches": "Code", "CacheMemo": "Code", "Corrected": "kCode",
    "Logs": "lParent", "LogMemo": "lParent",
    "Waypoints": "cParent", "WayMemo": "cParent",
    "Attributes": "aCode", "Custom": "cCode",
}
# Small tables copied whole (custom field definitions).
_SAMPLE_WHOLE = ("CustomLocal", "CustomCheck")


def _make_sample(src: Path, n: int, dest_dir: Path) -> Path:
    """A GSAK database with the first *n* caches (by code) of *src*."""
    dest = dest_dir / "sqlite.db3"
    conn = sqlite3.connect(f"file:{dest.as_posix()}", uri=True)
    # GSAK's schema names its own collation; a case-insensitive stand-in is
    # enough to copy the tables (the importer never sorts by it).
    conn.create_collation("gsaknocase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
    conn.execute("ATTACH DATABASE ? AS src", (f"file:{src.as_posix()}?mode=ro",))
    existing = {name.lower(): (name, sql) for name, sql in conn.execute(
        "SELECT name, sql FROM src.sqlite_master WHERE type = 'table' AND sql IS NOT NULL")}
    conn.execute("CREATE TEMP TABLE pick AS SELECT Code FROM src.Caches ORDER BY Code LIMIT ?", (n,))
    for table, col in list(_SAMPLE_TABLES.items()) + [(t, None) for t in _SAMPLE_WHOLE]:
        if table.lower() not in existing:
            continue
        name, sql = existing[table.lower()]
        conn.execute(sql)
        where = f" WHERE {col} IN (SELECT Code FROM pick)" if col else ""
        conn.execute(f"INSERT INTO main.[{name}] SELECT * FROM src.[{name}]{where}")
    for (sql,) in conn.execute(
            "SELECT sql FROM src.sqlite_master WHERE type = 'index' AND sql IS NOT NULL").fetchall():
        tbl = re.search(r"\bon\s+(\w+)", sql, re.I)
        if tbl and tbl.group(1).lower() in {t.lower() for t in (*_SAMPLE_TABLES, *_SAMPLE_WHOLE)}:
            conn.execute(sql)
    conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.close()
    return dest


def _gsak_counts(db3: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db3.as_posix()}?mode=ro", uri=True)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                for t in ("Caches", "Logs", "Attributes", "Waypoints", "Corrected")}
    finally:
        conn.close()


# ── One scenario ─────────────────────────────────────────────────────────────

def run_scenario(name: str, db3: Path, target: Path, batch_size: int,
                 profile_path: Optional[Path]) -> dict[str, Any]:
    from opensak.db.database import optimize_database, session_for
    from opensak.importer import gsak_import_service as service
    from opensak.importer import gsak_importer as gi

    rec = Recorder()
    sql = SqlStats()
    gcs = GcStats()

    # Wrap the importer's building blocks where import_gsak_db looks them up
    # (only those this version of the importer has).
    patched = {
        (gi, "_load_waypoints_by_parent"): "load waypoints",
        (gi, "_load_attributes_by_code"):  "load attributes",
        (gi, "_load_corrected_by_code"):   "load corrected",
        (gi, "_load_logs_by_parent"):      "load logs",
        (gi, "_load_existing_gc_map"):     "existing gc map",
        (gi, "_row_to_cache_data"):        "row_to_cache_data",
        (gi, "_flush_gsak_batch"):         "flush_batch",
        (gi, "_flush_gsak_batch_isolated"): "  flush_batch_isolated (fallback!)",
        (gi, "_upsert_cache_from_gsak"):   "  upsert_cache",     # before #1231
        (gi, "_write_gsak_batch"):         "  write_batch",      # since #1231
        (gi, "_restore_last_user_update"): "  restore_last_user_update",
        (gi, "_import_custom_fields"):     "custom fields",
    }
    # Since #1231 an overwrite builds a fresh file and swaps it in.
    replace = _replace_module() if name == "overwrite" else None
    if replace is not None:
        patched.update({
            (replace, "import_gsak_db"):     "import_gsak_db (total)",
            (replace, "copy_database_data"): "copy database data",
            (replace, "_swap"):              "swap file",
            (replace, "_custom_fields_after_swap"): "custom fields",
        })
    patched = {key: label for key, label in patched.items() if hasattr(*key)}
    originals = {key: getattr(*key) for key in patched}
    for key, label in patched.items():
        setattr(key[0], key[1], rec.wrap(label, originals[key]))

    from sqlalchemy.orm import Session
    orig_flush, orig_commit = Session.flush, Session.commit
    Session.flush = rec.wrap("    session.flush", orig_flush)        # type: ignore[method-assign]
    Session.commit = rec.wrap("commit", orig_commit)                 # type: ignore[method-assign]

    profiler = cProfile.Profile() if profile_path else None
    result = None
    gc.collect()
    gc.callbacks.append(gcs)
    t_start = time.perf_counter()
    try:
        if replace is not None:
            from sqlalchemy.engine import Engine
            detach = sql.attach(Engine)   # it opens engines of its own
            try:
                commit_before = rec.time["commit"]
                if profiler:
                    profiler.enable()
                with rec.phase("overwrite: fresh file + swap (total)"):
                    result = replace.import_gsak_db_replacing(db3, target, batch_size=batch_size)
                if profiler:
                    profiler.disable()
                import_commits = rec.time["commit"] - commit_before
                print(f"  replaced {result.cleared} caches")
            finally:
                detach()
        else:
          with session_for(target) as session:
            detach = sql.attach(session.get_bind().engine)
            try:
                if name == "overwrite":
                    with rec.phase("clear (overwrite)"):
                        cleared = gi.clear_opensak_cache_data(session)
                    print(f"  cleared {cleared} caches")
                commit_before = rec.time["commit"]
                if profiler:
                    profiler.enable()
                with rec.phase("import_gsak_db (total)"):
                    result = gi.import_gsak_db(db3, session, batch_size=batch_size)
                if profiler:
                    profiler.disable()
                import_commits = rec.time["commit"] - commit_before
            finally:
                detach()
        with rec.phase("optimize_database"):
            optimize_database(target)
        # As the service does after the last job of a target: invalidate first so
        # every scenario pays the full recalculation, like a real import.
        service.invalidate_distances(target)
        with rec.phase("update_distances"):
            service.update_distances(target)
    finally:
        total = time.perf_counter() - t_start
        gc.callbacks.remove(gcs)
        for key, fn in originals.items():
            setattr(key[0], key[1], fn)
        Session.flush, Session.commit = orig_flush, orig_commit   # type: ignore[method-assign]

    if profiler and profile_path:
        profiler.dump_stats(profile_path)

    top_level = ["load waypoints", "load attributes", "load corrected", "load logs",
                 "existing gc map", "row_to_cache_data", "flush_batch"]
    if replace is None:   # since #1231 an overwrite imports them after the swap
        top_level.append("custom fields")
    import_total = rec.time["import_gsak_db (total)"]
    rest = import_total - import_commits - sum(rec.time[k] for k in top_level)

    return {
        "scenario": name,
        "total_s": total,
        "phases": [{"phase": k, "seconds": rec.time[k], "calls": rec.calls[k]} for k in rec.order]
                  + [{"phase": "(cursor + rest)", "seconds": rest, "calls": 0}],
        "sql": sorted(({"key": k, "seconds": sql.time[k], "statements": sql.statements[k]}
                       for k in sql.time),
                      key=lambda r: -r["seconds"]),
        "gc": {str(g): {"runs": gcs.count[g], "seconds": gcs.time[g]} for g in sorted(gcs.count)},
        "peak_memory_mb": _peak_memory_mb(),
        "result": {
            "created": result.created, "updated": result.updated, "skipped": result.skipped,
            "logs": result.logs, "attributes": result.attributes, "waypoints": result.waypoints,
            "corrected": result.corrected, "notes": result.notes, "trackables": result.trackables,
            "errors": len(result.errors), "warnings": len(result.warnings),
        } if result is not None else None,
        "target_size_mb": target.stat().st_size / 1e6 if target.exists() else None,
    }


def _replace_module():
    try:
        from opensak.importer import gsak_replace
    except ImportError:   # before #1231
        return None
    return gsak_replace


# ── Report ───────────────────────────────────────────────────────────────────

def print_report(rep: dict[str, Any], top_sql: int) -> None:
    total = rep["total_s"]
    print(f"\n### {rep['scenario']} — {total:.1f} s total"
          f" (peak memory {rep['peak_memory_mb'] or 0:.0f} MB,"
          f" target file {rep['target_size_mb'] or 0:.0f} MB)\n")
    print("| Phase | Seconds | % | Calls |")
    print("|---|---:|---:|---:|")
    for p in rep["phases"]:
        print(f"| {p['phase']} | {p['seconds']:.2f} | {100 * p['seconds'] / total:.1f} | {p['calls'] or ''} |")

    sql_total = sum(r["seconds"] for r in rep["sql"])
    print(f"\nSQL on the target: {sql_total:.1f} s in "
          f"{sum(r['statements'] for r in rep['sql']):,} statements\n")
    print("| Statement | Seconds | Statements |")
    print("|---|---:|---:|")
    for r in rep["sql"][:top_sql]:
        print(f"| {r['key']} | {r['seconds']:.2f} | {r['statements']:,} |")

    gc_line = ", ".join(f"gen{g}: {v['runs']} runs {v['seconds']:.2f} s" for g, v in rep["gc"].items())
    print(f"\nGC: {gc_line or 'none'}")
    if rep["result"]:
        print(f"Result: {rep['result']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("gsak_path", type=Path, help="GSAK sqlite.db3 or backup .zip")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS),
                        help=f"comma-separated, from {', '.join(SCENARIOS)} (default: all, in order)")
    parser.add_argument("--sample", type=int, metavar="N",
                        help="benchmark a copy holding only the first N caches")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--target", type=Path,
                        help="scratch OpenSAK database (default: new file in a temp dir)")
    parser.add_argument("--keep", action="store_true", help="keep the scratch files")
    parser.add_argument("--profile", type=Path, metavar="FILE",
                        help="cProfile import_gsak_db of each scenario to FILE (suffixed per "
                             "scenario) and print the top functions; makes the run slower")
    parser.add_argument("--json", type=Path, metavar="FILE", help="also write all numbers as JSON")
    parser.add_argument("--top-sql", type=int, default=15)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    unknown = set(scenarios) - set(SCENARIOS)
    if unknown:
        parser.error(f"unknown scenario(s): {', '.join(sorted(unknown))}")
    if "fresh" not in scenarios and args.target is None:
        print("Note: without 'fresh' the scratch database starts empty, so 'merge' "
              "behaves like a fresh import.")

    work = Path(tempfile.mkdtemp(prefix="opensak_gsak_bench_"))
    user_values = _real_user_values()
    _isolate_settings(work, user_values)

    from opensak.db.database import init_db
    from opensak.importer.gsak_importer import find_gsak_db3_in_zip
    from opensak.utils import flags

    db3 = find_gsak_db3_in_zip(args.gsak_path)
    if args.sample:
        print(f"Copying the first {args.sample} caches of {db3} ...")
        db3 = _make_sample(db3, args.sample, work)
    target = args.target or work / "bench.sqlite"
    init_db(db_path=target)

    print(f"Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version}, "
          f"companion_db={flags.companion_db}")
    print(f"GSAK source: {db3}  {_gsak_counts(db3)}")
    print(f"Target: {target}")
    print(f"GC user set: {bool(user_values['user.gc_username'] or user_values['user.gc_finder_id'])}, "
          f"batch size {args.batch_size}")

    reports = []
    for name in scenarios:
        print(f"\nRunning {name} ...", flush=True)
        prof = (args.profile.with_name(f"{args.profile.stem}_{name}{args.profile.suffix or '.prof'}")
                if args.profile else None)
        rep = run_scenario(name, db3, target, args.batch_size, prof)
        reports.append(rep)
        print_report(rep, args.top_sql)
        if prof:
            print(f"\nTop functions by own time ({prof}):")
            pstats.Stats(str(prof)).sort_stats("tottime").print_stats(25)

    if args.json:
        args.json.write_text(json.dumps({
            "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
            "source": str(args.gsak_path), "sample": args.sample, "batch_size": args.batch_size,
            "gsak_counts": _gsak_counts(db3), "scenarios": reports,
        }, indent=2), encoding="utf-8")
        print(f"\nNumbers written to {args.json}")

    # init_db() left the scratch database open as the active one; Windows
    # can't delete it (WAL files included) while that pool holds it.
    from opensak.db.database import dispose_engine
    dispose_engine()
    if args.keep:
        print(f"\nScratch files kept in {work}")
    else:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
        if work.exists():
            print(f"\nCould not remove all scratch files in {work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
