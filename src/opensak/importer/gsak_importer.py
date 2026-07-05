"""
src/opensak/importer/gsak_importer.py — GSAK direct database importer (issue #469).

Reads a GSAK ``sqlite.db3`` file directly (confirmed genuine SQLite 3,
``PRAGMA user_version = 27`` on two independent real-world installations —
see #469 investigation) and upserts caches straight into OpenSAK's own
SQLAlchemy models, without going via GPX.

── Session 1 scope (this file) ───────────────────────────────────────────────
Core cache data only:
    - Caches + CacheMemo   -> Cache (scalar fields, incl. the #469 schema
      additions: gc_note, url, elevation, color, guid, watch, gc_cache_id)
    - Corrected            -> UserNote.corrected_lat/lon/is_corrected
    - Waypoints + WayMemo  -> Waypoint (incl. wp_code, url, wp_date,
      created_by_user, wp_flag)
    - Attributes           -> Attribute (names resolved via OpenSAK's own
      Groundspeak attribute table + English strings, since GSAK's Attributes
      table only stores aId/aInc, unlike GPX's inline <gs:attribute> text)

Deliberately NOT imported yet (later sessions per #469 plan):
    - Logs / LogMemo        (session 2 — full history, perf-tested against
      a 1.1M-row real database)
    - UserNote.note         (session 3 — personal note *text*, needs the
      file:/// embedded-image placeholder pre-scan agreed in #472)
    - CacheMemo.TravelBugs, Custom/CustomLocal, Ignore (out of scope / #473)
    - Cache.find_count (issue #517 prep column) — GSAK's own FoundCount
      turned out (verified against a real 12,600-cache database) to be
      identical to Found (0/1, "found by me"), not a true community find
      count, so there is no honest GSAK source for it yet. A count of
      "Found it" logs per cache is the closest approximation once Logs
      are imported in session 2 — revisit find_count then.

Because this is a *partial*-scope import, re-running it on a database that
already has logs/trackables (e.g. from an earlier GPX import) must NEVER
touch those tables — only Waypoints and Attributes are rebuilt on
re-import here, unlike the GPX importer's ``_upsert_cache`` which rebuilds
everything every time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from opensak.db.models import Attribute, Cache, UserNote, Waypoint
from opensak.importer import (
    ImportResult,
    _enter_bulk_import_pragmas,
    _exit_bulk_import_pragmas,
    _load_existing_gc_map,
)
from opensak.utils.constants import ATTRIBUTES


# ── GSAK CacheType single-letter code -> OpenSAK cache_type string ──────────
# Confirmed against GSAK's own documentation (gsak.net/help/hs10300.htm,
# %typ1 special tag: "T=traditional, M=multi, B=letterbox hybrid, C=CITO,
# E=event, L=locationless, V=virtual, W=webcam, O=Other, G=Benchmark,
# R=Earth, I=Wherigo and U=mystery/Unknown") and cross-checked against the
# real distribution in both test databases (Sommerhus.zip: T/U/R only;
# GSAK_Database_Backup.zip: all 12 codes below, matching real-world type
# frequency — e.g. T=9984, U=1575, M=512, R=111 out of 12,600 caches).
GSAK_CACHE_TYPE_MAP: dict[str, str] = {
    "T": "Traditional Cache",
    "M": "Multi-cache",
    "U": "Unknown Cache",
    "B": "Letterbox Hybrid",
    "W": "Webcam Cache",
    "V": "Virtual Cache",
    "E": "Event Cache",
    "C": "Cache In Trash Out Event",
    "R": "Earthcache",
    "I": "Wherigo Cache",
    "L": "Locationless (Reverse) Cache",
    "O": "Other",
    "G": "Benchmark",
}

# GSAK's Container field already matches OpenSAK's CONTAINER_SIZES strings
# verbatim (Micro/Small/Regular/Large/Other/Not chosen), except for two
# placeholder values GSAK uses on caches with no physical container
# (Virtual/Event/Earthcache types) — both map to OpenSAK's "Not chosen".
GSAK_CONTAINER_MAP: dict[str, str] = {
    "Unknown": "Not chosen",
    "Virtual": "Not chosen",
}

# GSAK Status: 'A' = Active, 'T' = Temporarily disabled, 'X' = Archived.
# Verified 1:1 against the Archived/TempDisabled columns on the 12,600-cache
# database (A,0,0 / T,0,1 / X,1,0 — no other combination occurs).
_STATUS_ARCHIVED = "X"
_STATUS_AVAILABLE = "A"


def _s(value) -> Optional[str]:
    """Normalise a GSAK text field: '' or None -> None, else stripped string."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _f(value) -> Optional[float]:
    """Parse a GSAK numeric-as-text field ('' or None -> None)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _b(value) -> bool:
    """GSAK booleans are stored as 0/1 integers (or None for old rows)."""
    return bool(value)


def _d(value) -> Optional[datetime]:
    """Parse a GSAK date string ('YYYY-MM-DD[ HH:MM:SS]' or '' -> None)."""
    raw = _s(value)
    if raw is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _attribute_name_lookup() -> dict[int, str]:
    """
    Build an ``{attribute_id: English display name}`` map.

    GSAK's ``Attributes`` table only stores ``aId``/``aInc`` — no name text
    (unlike GPX's inline ``<gs:attribute id="..">Name</gs:attribute>``). We
    reuse OpenSAK's own Groundspeak attribute table (``ATTRIBUTES`` in
    ``utils/constants.py``, already the single source of truth used by the
    filter dialog) plus the English language file, rather than hardcoding a
    second copy of the ~70-entry Groundspeak attribute list here.
    """
    from opensak.lang.en import STRINGS as _en_strings

    return {
        attr_id: _en_strings.get(attr_key, str(attr_id))
        for attr_id, attr_key in ATTRIBUTES
    }


class GsakImportResult(ImportResult):
    """ImportResult plus GSAK-specific counters (attributes, corrected coords)."""

    def __init__(self):
        super().__init__()
        self.attributes: int = 0
        self.corrected: int = 0

    def __str__(self) -> str:
        base = super().__str__()
        return base + f"\n  Attributes     : {self.attributes}\n  Corrected coords: {self.corrected}"


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the GSAK database strictly read-only (we never write to it)."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_waypoints_by_parent(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Return ``{parent_gc_code: [waypoint dict, ...]}`` for every child waypoint.

    LEFT JOINs WayMemo since the two tables can drift by a few rows in
    practice (seen on the 12,600-cache sample: 3592 Waypoints vs 3587
    WayMemo rows) — a missing WayMemo row must not drop the waypoint itself.
    """
    cur = conn.execute("""
        SELECT w.cParent, w.cCode, w.cPrefix, w.cName, w.cType,
               w.cLat, w.cLon, w.cByuser, w.cDate, w.cFlag,
               m.cComment, m.cUrl
        FROM Waypoints w
        LEFT JOIN WayMemo m ON w.cParent = m.cParent AND w.cCode = m.cCode
        ORDER BY w.cParent, w.cCode
    """)
    by_parent: dict[str, list[dict]] = {}
    for row in cur.fetchall():
        by_parent.setdefault(row["cParent"], []).append({
            "wp_code":         _s(row["cCode"]),
            "prefix":          _s(row["cPrefix"]) or "",
            "name":            _s(row["cName"]),
            "wp_type":         _s(row["cType"]) or "Waypoint",
            "latitude":        _f(row["cLat"]),
            "longitude":       _f(row["cLon"]),
            "created_by_user": _b(row["cByuser"]),
            "wp_date":         _d(row["cDate"]),
            "wp_flag":         _b(row["cFlag"]),
            "comment":         _s(row["cComment"]),
            "url":             _s(row["cUrl"]),
        })
    return by_parent


def _load_attributes_by_code(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Return ``{gc_code: [{attribute_id, is_on}, ...]}`` for every cache."""
    cur = conn.execute("SELECT aCode, aId, aInc FROM Attributes")
    by_code: dict[str, list[dict]] = {}
    for row in cur.fetchall():
        by_code.setdefault(row["aCode"], []).append({
            "attribute_id": row["aId"],
            "is_on":        _b(row["aInc"]),
        })
    return by_code


def _load_corrected_by_code(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return ``{gc_code: {corrected_lat, corrected_lon}}`` for solved caches."""
    cur = conn.execute("""
        SELECT kCode, kAfterLat, kAfterLon FROM Corrected
    """)
    by_code: dict[str, dict] = {}
    for row in cur.fetchall():
        lat, lon = _f(row["kAfterLat"]), _f(row["kAfterLon"])
        if lat is None or lon is None:
            continue
        by_code[row["kCode"]] = {"corrected_lat": lat, "corrected_lon": lon}
    return by_code


def _row_to_cache_data(row: sqlite3.Row) -> Optional[dict]:
    """Map one joined Caches+CacheMemo row to a Cache-model-shaped dict."""
    gc_code = _s(row["Code"])
    lat, lon = _f(row["Latitude"]), _f(row["Longitude"])
    if not gc_code or lat is None or lon is None:
        return None

    status = _s(row["Status"])
    container = _s(row["Container"]) or "Not chosen"
    container = GSAK_CONTAINER_MAP.get(container, container)

    elevation = _f(row["Elevation"])
    # 0.0 is GSAK's default/placeholder before an elevation macro has run —
    # treat it the same as "not yet computed" (None), consistent with our
    # own convention (see #469 schema PR). A real 0m (sea-level) cache is
    # rare enough that this is the safer default; flag to Allan if this
    # needs revisiting once Fabio's elevation pass runs on imported caches.
    if elevation == 0.0:
        elevation = None

    raw_type = _s(row["CacheType"]) or ""
    return {
        "gc_code":     gc_code,
        "name":        _s(row["Name"]) or gc_code,
        "cache_type":  GSAK_CACHE_TYPE_MAP.get(raw_type, "Unknown Cache"),
        "container":   container,
        "latitude":    lat,
        "longitude":   lon,
        "difficulty":  _f(row["Difficulty"]),
        "terrain":     _f(row["Terrain"]),
        "placed_by":   _s(row["PlacedBy"]),
        "owner_name":  _s(row["OwnerName"]),
        "owner_id":    _s(row["OwnerId"]),
        "hidden_date": _d(row["PlacedDate"]),
        "last_updated": _d(row["Changed"]),
        "available":   status == _STATUS_AVAILABLE,
        "archived":    status == _STATUS_ARCHIVED or _b(row["Archived"]),
        "country":     _s(row["Country"]),
        "state":       _s(row["State"]),
        "county":      _s(row["County"]),
        "short_description": _s(row["ShortDescription"]),
        "short_desc_html":   "<" in (_s(row["ShortDescription"]) or ""),
        "long_description":  _s(row["LongDescription"]),
        "long_desc_html":    "<" in (_s(row["LongDescription"]) or ""),
        # GSAK stores hints already decoded (plain text) — passed straight
        # through. OpenSAK's split_hint() heuristic already auto-detects
        # plain-vs-ROT13 for display, so no re-encoding is needed here.
        "encoded_hints": _s(row["Hints"]),
        "url":           _s(row["Url"]),

        # Personal / status fields GSAK tracks directly on Caches, so no
        # log history is needed to derive them (unlike GPX import).
        "found":          _b(row["Found"]),
        "found_date":     _d(row["FoundByMeDate"]),
        "dnf":            _b(row["DNF"]),
        "dnf_date":       _d(row["DNFDate"]),
        "first_to_find":  _b(row["FTF"]),
        "user_flag":      _b(row["UserFlag"]),
        "user_sort":      row["UserSort"] if row["UserSort"] not in (None, "") else None,
        "user_data_1":    _s(row["UserData"]),
        "user_data_2":    _s(row["User2"]),
        "user_data_3":    _s(row["User3"]),
        "user_data_4":    _s(row["User4"]),
        "favorite_points": row["FavPoints"] if row["FavPoints"] is not None else None,

        # ── Issue #469 schema additions ──────────────────────────────────
        "gc_note":     _s(row["GcNote"]),
        "elevation":   elevation,
        "color":       _s(row["Color"]),
        "guid":        _s(row["Guid"]),
        "watch":       _b(row["Watch"]),
        "gc_cache_id": _s(row["CacheId"]),

        # GSAK's own "Lock" flag — same concept as our own `locked` (skip
        # overwriting scalar fields on re-import). Only honoured for
        # brand-new caches; an existing OpenSAK-locked cache keeps its own
        # lock regardless of what the source GSAK database says.
        "_gsak_lock": _b(row["Lock"]),
    }


_CACHE_JOIN_SQL = """
    SELECT c.*, m.LongDescription, m.ShortDescription, m.Url, m.Hints
    FROM Caches c
    LEFT JOIN CacheMemo m ON c.Code = m.Code
"""


def _upsert_cache_from_gsak(
    session: Session,
    data: dict,
    attr_rows: list[dict],
    wpt_rows: list[dict],
    corrected: Optional[dict],
    attr_names: dict[int, str],
    source_file: str,
    existing_ids: dict[str, int],
    warnings: Optional[list[str]] = None,
) -> tuple[Cache, bool]:
    """
    Insert or update a Cache row from parsed GSAK data (session 1 scope).

    Unlike the GPX importer's ``_upsert_cache``, this only rebuilds
    Waypoints and Attributes on re-import — Logs and Trackables are left
    completely untouched, since they are out of scope for this session and
    may already hold real data from an earlier GPX import.
    """
    gc_code = data["gc_code"]
    cid = existing_ids.get(gc_code)
    existing = session.get(Cache, cid) if cid is not None else None
    created = existing is None

    if existing is None:
        cache = Cache(gc_code=gc_code)
        session.add(cache)
        # New cache: honour GSAK's own Lock flag as our `locked`.
        cache.locked = data["_gsak_lock"]
    else:
        cache = existing
        session.query(Waypoint).filter_by(cache_id=cache.id).delete(synchronize_session=False)
        session.query(Attribute).filter_by(cache_id=cache.id).delete(synchronize_session=False)
        cache.waypoint_count = 0
        session.flush()

    # ── Issue #202: Lock a cache ──────────────────────────────────────────
    # A locked cache (set in OpenSAK itself, or inherited from GSAK's own
    # Lock flag on first import) keeps its scalar fields untouched.
    if existing is None or not cache.locked:
        for field in (
            "name", "cache_type", "container", "latitude", "longitude",
            "difficulty", "terrain", "placed_by", "owner_name", "owner_id",
            "hidden_date", "available", "archived",
            "country", "state", "county",
            "short_description", "short_desc_html",
            "long_description", "long_desc_html",
            "encoded_hints", "url",
            "gc_note", "elevation", "color", "guid", "watch", "gc_cache_id",
        ):
            setattr(cache, field, data.get(field))

    # Personal/status fields are not subject to the lock — mirrors GPX
    # import behaviour, where a re-import can still bring in a newer
    # found/DNF/favourite-points state without unlocking the listing data.
    for field in (
        "found", "found_date", "dnf", "dnf_date", "first_to_find",
        "user_flag", "user_sort",
        "user_data_1", "user_data_2", "user_data_3", "user_data_4",
        "favorite_points",
    ):
        setattr(cache, field, data.get(field))

    cache.source_file = source_file

    # Attributes
    for a in attr_rows:
        session.add(Attribute(
            cache=cache,
            attribute_id=a["attribute_id"],
            name=attr_names.get(a["attribute_id"]),
            is_on=a["is_on"],
        ))

    # Waypoints
    # GSAK data can (rarely) contain two waypoints under one cache sharing
    # the same prefix+name but a distinct cCode (seen once in 12,600 caches
    # during #469 testing — two "RP"/"Right turn" reference points). Our
    # uq_waypoint_cache_prefix_name constraint is (cache_id, prefix, name),
    # so the second one is dropped here (query is ORDER BY cCode, so this
    # is deterministic) rather than failing the whole cache's import.
    seen_wpt_keys: set[tuple[str, Optional[str]]] = set()
    for w in wpt_rows:
        key = (w["prefix"], w["name"])
        if key in seen_wpt_keys:
            if warnings is not None:
                warnings.append(
                    f"{gc_code}: dropped duplicate waypoint {w['wp_code']!r} "
                    f"(prefix+name already used by another waypoint on this cache)"
                )
            continue
        seen_wpt_keys.add(key)
        session.add(Waypoint(
            cache=cache,
            prefix=w["prefix"],
            wp_type=w["wp_type"],
            name=w["name"],
            comment=w["comment"],
            latitude=w["latitude"],
            longitude=w["longitude"],
            parent_gc_code=gc_code,
            wp_code=w["wp_code"],
            url=w["url"],
            wp_date=w["wp_date"],
            created_by_user=w["created_by_user"],
            wp_flag=w["wp_flag"],
        ))
    cache.waypoint_count = len(seen_wpt_keys)

    # Corrected coordinates -> UserNote (note text itself is session 3 scope;
    # an existing note's text is left untouched here either way).
    if corrected is not None:
        note = cache.user_note
        if note is None:
            note = UserNote(cache=cache)
            session.add(note)
        note.corrected_lat = corrected["corrected_lat"]
        note.corrected_lon = corrected["corrected_lon"]
        note.is_corrected = True

    return cache, created


def _flush_gsak_batch(
    session: Session,
    batch: list[tuple[dict, list[dict], list[dict], Optional[dict]]],
    attr_names: dict[int, str],
    source: str,
    existing_ids: dict[str, int],
    result: GsakImportResult,
) -> None:
    """Persist a batch under one SAVEPOINT, falling back to per-cache isolation
    on failure — mirrors the GPX importer's ``_flush_cache_batch``."""
    if not batch:
        return

    sp = session.begin_nested()
    try:
        pending: list[tuple[Cache, bool, int, bool]] = []
        for data, attr_rows, wpt_rows, corrected in batch:
            cache, created = _upsert_cache_from_gsak(
                session, data, attr_rows, wpt_rows, corrected,
                attr_names, source, existing_ids, result.warnings,
            )
            pending.append((cache, created, len(attr_rows), corrected is not None))
        sp.commit()
    except Exception:
        sp.rollback()
        _flush_gsak_batch_isolated(session, batch, attr_names, source, existing_ids, result)
        return

    for cache, created, n_attrs, had_corrected in pending:
        if created:
            result.created += 1
            existing_ids[cache.gc_code] = cache.id
        else:
            result.updated += 1
        result.waypoints += cache.waypoint_count
        result.attributes += n_attrs
        if had_corrected:
            result.corrected += 1


def _flush_gsak_batch_isolated(
    session: Session,
    batch: list[tuple[dict, list[dict], list[dict], Optional[dict]]],
    attr_names: dict[int, str],
    source: str,
    existing_ids: dict[str, int],
    result: GsakImportResult,
) -> None:
    """Replay a batch one cache at a time so a single bad cache only skips itself."""
    for data, attr_rows, wpt_rows, corrected in batch:
        cell = session.begin_nested()
        try:
            cache, created = _upsert_cache_from_gsak(
                session, data, attr_rows, wpt_rows, corrected,
                attr_names, source, existing_ids, result.warnings,
            )
            cell.commit()
            if created:
                result.created += 1
                existing_ids[cache.gc_code] = cache.id
            else:
                result.updated += 1
            result.waypoints += cache.waypoint_count
            result.attributes += len(attr_rows)
            if corrected is not None:
                result.corrected += 1
        except Exception as e:
            cell.rollback()
            result.errors.append(f"DB error for {data.get('gc_code', '?')} in {source}: {e}")
            result.skipped += 1


def import_gsak_db(
    db_path: Path,
    session: Session,
    progress_cb=None,
    batch_size: int = 200,
) -> GsakImportResult:
    """
    Import a GSAK ``sqlite.db3`` file directly into OpenSAK (session 1 scope:
    Caches/CacheMemo/Corrected/Waypoints/WayMemo/Attributes only).

    Parameters
    ----------
    db_path : Path
        Path to the GSAK ``sqlite.db3`` file (opened strictly read-only).
    session : Session
        OpenSAK's own SQLAlchemy session to import into.
    progress_cb : callable, optional
        Called as ``progress_cb(done, total)`` after each batch.
    batch_size : int
        Caches per SAVEPOINT batch (see :func:`_flush_gsak_batch`).
    """
    result = GsakImportResult()
    db_path = Path(db_path)

    if not db_path.exists():
        result.errors.append(f"GSAK database not found: {db_path}")
        return result

    try:
        conn = _open_readonly(db_path)
    except sqlite3.Error as e:
        result.errors.append(f"Could not open GSAK database: {e}")
        return result

    try:
        total = conn.execute("SELECT COUNT(*) FROM Caches").fetchone()[0]
        wpts_by_parent = _load_waypoints_by_parent(conn)
        attrs_by_code = _load_attributes_by_code(conn)
        corrected_by_code = _load_corrected_by_code(conn)
        attr_names = _attribute_name_lookup()

        existing_ids = _load_existing_gc_map(session)
        _enter_bulk_import_pragmas(session)

        batch: list[tuple[dict, list[dict], list[dict], Optional[dict]]] = []
        done = 0
        source = str(db_path)

        cur = conn.execute(_CACHE_JOIN_SQL)
        for row in cur:
            data = _row_to_cache_data(row)
            done += 1
            if data is None:
                result.skipped += 1
                result.warnings.append(f"Skipped row with missing code/coords near row {done}")
            else:
                gc_code = data["gc_code"]
                batch.append((
                    data,
                    attrs_by_code.get(gc_code, []),
                    wpts_by_parent.get(gc_code, []),
                    corrected_by_code.get(gc_code),
                ))
                if len(batch) >= batch_size:
                    _flush_gsak_batch(session, batch, attr_names, source, existing_ids, result)
                    session.commit()
                    batch = []

            if progress_cb is not None and (done % 50 == 0 or done == total):
                progress_cb(done, total)

        if batch:
            _flush_gsak_batch(session, batch, attr_names, source, existing_ids, result)
            session.commit()

    finally:
        _exit_bulk_import_pragmas(session)
        conn.close()

    return result
