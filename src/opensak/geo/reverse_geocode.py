"""
src/opensak/geo/reverse_geocode.py — offline reverse geocoding of caches.

Fills country / state / county from the boundary polygon engine
(TerritoryResolver) and writes the four provenance columns. Qt-free, so the
same code runs in the Update locations dialog, after a GPX import in the
GUI and after a headless import (#937): ReverseGeocodeWorker only runs
reverse_geocode() on a QThread and turns its callbacks into signals.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from opensak.lang import tr

log = logging.getLogger(__name__)


class CacheRow:
    """One cache to resolve: the coordinates used and which ones they are."""

    __slots__ = ("gc_code", "lat", "lon", "basis")

    def __init__(self, gc_code: str, lat: float, lon: float, basis: str = "posted") -> None:
        self.gc_code = gc_code
        self.lat = lat
        self.lon = lon
        self.basis = basis


@dataclass
class UpdateLocationResult:
    updated: int = 0
    skipped: int = 0
    errors:  int = 0
    error_msgs: list[str] = field(default_factory=list)
    cancelled: bool = False


def rows_missing_location(db_path: Optional[Path] = None) -> list[CacheRow]:
    """The caches of *db_path* (default: the active database) that lack a
    country, state or county — what is resolved after an import. Corrected
    coordinates are used where a cache has them."""
    from sqlalchemy.orm import joinedload

    from opensak.db.database import session_on
    from opensak.db.models import Cache

    rows: list[CacheRow] = []
    with session_on(db_path) as session:
        query = session.query(Cache).options(joinedload(Cache.user_note)).filter(
            (Cache.country == None) | (Cache.country == "") |
            (Cache.state   == None) | (Cache.state   == "") |
            (Cache.county  == None) | (Cache.county  == "")
        )
        for cache in query.all():
            lat, lon = cache.latitude, cache.longitude
            basis = "posted"
            if cache.user_note and cache.user_note.is_corrected:
                clat = cache.user_note.corrected_lat
                clon = cache.user_note.corrected_lon
                if clat is not None and clon is not None:
                    lat, lon = clat, clon
                    basis = "corrected"
            if lat is not None and lon is not None:
                rows.append(CacheRow(gc_code=cache.gc_code, lat=lat, lon=lon, basis=basis))
    return rows


def reverse_geocode(
    rows: list[CacheRow],
    db_path: Optional[Path] = None,
    *,
    on_row: Optional[Callable[[str, str], None]] = None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> UpdateLocationResult:
    """Resolve *rows* and write the result to *db_path* (default: the
    active database).

    *on_row(gc_code, log_line)* reports each resolved cache, and the
    county-pack download with an empty gc_code. *is_cancelled()* is checked
    between rows; a cancelled run writes nothing and returns with
    ``cancelled`` set.

    Two phases:
      1. Parallel resolve — ThreadPoolExecutor, one BoundaryStore per thread.
      2. Bulk write — single IN query loads all caches; one transaction commits all.
    """
    from opensak.db.database import session_on
    from opensak.db.models import Cache
    from opensak.geo.boundaries import TerritoryResolver
    from opensak.geo.store import BoundaryStore, ensure_baseline_seeded

    def emit(gc_code: str, line: str) -> None:
        if on_row is not None:
            on_row(gc_code, line)

    result = UpdateLocationResult()

    if is_cancelled():
        result.cancelled = True
        return result

    # First-run baseline seed (copy from the PyInstaller bundle, or download
    # from OpenSAK-Data if nothing's bundled) happens here rather than at app
    # startup — the callers run this off the GUI thread, per this feature's
    # own "long work never blocks the main thread" rule, and the fallback
    # download path can take a while over the network.
    ensure_baseline_seeded()

    check = BoundaryStore()
    if not check.available():
        result.errors += 1
        emit("", tr("update_loc_no_boundaries"))
        return result

    dataset = check.dataset_version()
    check.close()
    now = datetime.now(timezone.utc)

    # Phase 0 — pre-fetch phase (#722): collect every distinct county pack
    # this batch will need (via the same R-Tree bbox candidates the resolver
    # itself uses) and download them all in parallel, with real progress,
    # before the per-row resolve starts. Without this, a county pack missing
    # locally but needed by many caches in the batch would otherwise retry a
    # slow on-demand fetch (up to packs.DOWNLOAD_TIMEOUT seconds) inside
    # _resolve_layer for every single cache that needs it — the original
    # #722 hang. This is a best-effort optimization: any failure here just
    # falls back to the existing on-demand fetch in store.py, so it's wrapped
    # defensively rather than aborting the whole geocode run over it.
    try:
        from opensak.geo import packs as _packs_mod

        probe = BoundaryStore()
        needed_packs: set[str] = set()
        for row in rows:
            if is_cancelled():
                break
            for region_id in probe.candidates("county", row.lat, row.lon):
                region = probe.region("county", region_id)
                if region is not None:
                    needed_packs.add(region.pack)
        counties_dir = probe.data_dir / "counties"
        probe.close()

        if needed_packs and not is_cancelled():
            def _prefetch_progress(done: int, total: int) -> None:
                if total:
                    emit("", tr("update_loc_prefetch_progress", done=done, total=total))

            _packs_mod.fetch_packs(
                sorted(needed_packs), counties_dir, progress_cb=_prefetch_progress
            )
    except Exception as exc:
        log.debug("county pre-fetch phase failed, falling back to on-demand fetch: %s", exc)

    if is_cancelled():
        result.cancelled = True
        return result

    # Phase 1 — parallel resolve.
    # _shared_packs is injected into every thread's BoundaryStore so GeoJSON
    # packs are loaded from disk at most once across all threads. Each thread
    # still gets its own SQLite connection (not thread-safe to share). Dict
    # writes under the GIL are atomic; the only race is two threads loading
    # the same missing pack simultaneously — harmless, second write wins.
    _shared_packs: dict = {}
    _tls = threading.local()

    def _resolve_one(row: CacheRow):
        if is_cancelled():
            return None
        if not hasattr(_tls, "resolver"):
            s = BoundaryStore()
            s._packs = _shared_packs
            _tls.store = s
            _tls.resolver = TerritoryResolver(s)
        loc = _tls.resolver.resolve(row.lat, row.lon)
        return row, loc

    workers = min(4, os.cpu_count() or 1)
    resolved = []
    cancelled = False

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_resolve_one, row): row for row in rows}
        for future in as_completed(futures):
            if is_cancelled():
                cancelled = True
                break
            pair = future.result()
            if pair is None:
                continue
            row, loc = pair
            emit(row.gc_code, tr(
                "update_loc_row",
                gc_code=row.gc_code,
                country=loc.country or "–",
                state=loc.state or "–",
                county=loc.county or "–",
            ))
            resolved.append((row, loc))

    if cancelled or is_cancelled():
        result.cancelled = True
        return result

    # Phase 2 — bulk write in one transaction, no per-row SELECT. The IN query
    # is chunked to stay under SQLite's bound-parameter limit (large DBs).
    resolved_map = {row.gc_code: (row, loc) for row, loc in resolved}
    gc_codes = list(resolved_map)
    try:
        with session_on(db_path) as session:
            for i in range(0, len(gc_codes), 500):
                chunk = gc_codes[i:i + 500]
                caches = (
                    session.query(Cache)
                    .filter(Cache.gc_code.in_(chunk))
                    .all()
                )
                for cache in caches:
                    row, loc = resolved_map[cache.gc_code]
                    cache.country          = loc.country
                    cache.state            = loc.state
                    cache.county           = loc.county
                    cache.location_source  = "boundary"
                    cache.location_basis   = row.basis
                    cache.location_updated = now
                    cache.location_dataset = dataset
                    result.updated += 1
    except Exception as exc:
        result.errors += 1
        emit("", tr("update_loc_row_error", gc_code="batch", msg=str(exc)))

    if is_cancelled():
        result.cancelled = True
    return result
