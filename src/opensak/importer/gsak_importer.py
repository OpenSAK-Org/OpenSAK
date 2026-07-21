"""
src/opensak/importer/gsak_importer.py — GSAK direct database importer (issue #469).

Reads a GSAK ``sqlite.db3`` file directly (confirmed genuine SQLite 3,
``PRAGMA user_version = 27`` on two independent real-world installations —
see #469 investigation) and upserts caches straight into OpenSAK's own
SQLAlchemy models, without going via GPX.

── Session 1 (core cache data) ───────────────────────────────────────────────
    - Caches + CacheMemo   -> Cache (scalar fields, incl. the #469 schema
      additions: gc_note, url, elevation, color, guid, watch, gc_cache_id)
    - Corrected            -> UserNote.corrected_lat/lon/is_corrected
    - Waypoints + WayMemo  -> Waypoint (incl. wp_code, url, wp_date,
      created_by_user, wp_flag)
    - Attributes           -> Attribute (names resolved via OpenSAK's own
      Groundspeak attribute table + English strings, since GSAK's Attributes
      table only stores aId/aInc, unlike GPX's inline <gs:attribute> text)

── Session 2 (this addition) ─────────────────────────────────────────────────
    - Logs + LogMemo -> Log (full history, not capped like GPX/PQ exports).
      ``log_id`` is built as ``f"{lParent}_{lLogId}"`` rather than using
      GSAK's ``lLogId`` alone: verified against a real 1.1M-row database that
      the same ``lLogId`` can appear on up to 45 *different* caches (e.g. a
      single day's "Team Position Police" power-trail run) — GSAK's log ID
      is not globally unique the way a real GC.com log GUID is, but
      ``(lParent, lLogId)`` together always is (verified 1,123,992 rows =
      1,123,992 distinct pairs). ``lEncoded`` maps straight to
      ``text_encoded`` (same concept as GPX's ``<gs:text encoded="..">``).
      ``lIsowner`` -> ``logged_by_owner`` (#469 schema addition).
      ``log_count``/``last_log_date`` on Cache are (re)computed from the
      imported logs, mirroring GPX import (#87/#186).

── Session 3 (this addition, issue #472) ─────────────────────────────────────
    - CacheMemo.UserNote -> UserNote.note (personal note text). Embedded
      local ``file:///`` image references (from GSAK's own "GrabImages"
      macro) are replaced with ``[image: filename.ext]`` placeholders —
      verified against a real 12,600-cache database: 154 notes / 312 <img>
      tags, all matching one consistent pattern. ``scan_gsak_notes_for_
      embedded_images()`` lets the (future, session 4) import dialog show a
      one-time count before the user commits to importing.
      UserNote is now created/updated whenever there is corrected coords
      OR note text (previously only on corrected coords — a real GSAK
      database showed 2,348 of 2,446 notes have text but no corrected
      coords, so the narrower condition silently dropped almost all real
      note text). Like Waypoints/Attributes/Logs, the note is unconditionally
      overwritten on every import — no "protect existing note" merge logic,
      per Allan's call that this is mostly a one-time GSAK-to-OpenSAK
      migration rather than a repeated two-way sync.

── Session 4 (this addition, issue #538) ─────────────────────────────────────
    - CacheMemo.TravelBugs -> Trackable (name, ref, tracking_code). GSAK
      stores one line per trackable in a single text field, format
      ``<Name> (id = <TrackableID>, ref = <Trackable Reference>)`` (one
      trackable per line — confirmed by #538 reporter's example:
      ``Best TB ever (id = 1234567, ref = TBAB12CD)``). Parsed with
      ``_parse_gsak_trackables()``; a line that doesn't match the pattern is
      skipped rather than aborting the whole cache (GSAK's own free-text
      "Custom" trackable entries, if any, would not match and are silently
      dropped rather than imported as garbage).
      Like Waypoints/Attributes/Logs, Trackables are now unconditionally
      rebuilt (delete + recreate) on every import of a given cache — this is
      a deliberate change from the previous "leave Trackables completely
      untouched" behaviour (see git history), made consistent with the rest
      of the importer's one-time-migration philosophy (#472). One
      consequence worth knowing: re-importing a GSAK database now overwrites
      any Trackables a cache already had from an earlier *GPX* import with
      whatever GSAK currently has for that cache.

Deliberately NOT imported yet (later sessions per #469 plan):
    - Custom/CustomLocal, Ignore (out of scope / #473)
    - Cache.find_count (issue #517 prep column) — GSAK's own FoundCount
      turned out (verified against a real 12,600-cache database) to be
      identical to Found (0/1, "found by me"), not a true community find
      count, so there is no honest GSAK source for it yet. A count of
      "Found it" logs per cache (now available, see session 2 above) is
      the closest approximation — still deliberately not wired up to
      find_count, since it would not match the real GC.com number for
      caches with partial/capped log history, and it's better to make that
      a conscious choice with Allan than a silent approximation.

This is still a *partial*-scope import (Custom fields / Ignore list remain
out of scope, see #473) — but as of session 4, Waypoints, Attributes, Logs
and Trackables are all rebuilt (delete + recreate) on every re-import here,
same as the GPX importer's ``_upsert_cache``.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from opensak.db.models import Attribute, Cache, Log, Trackable, UserNote, Waypoint
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
#
# Issue #532: the 13 codes above don't cover GSAK's full $d_CacheType list
# (gsak.net/help/hs21040.htm), which also documents A/D/F/H/J/P/Q/X/Y/Z.
# Any code missing from this map falls through to the "Unknown Cache"
# default in _row_to_cache_data() — which silently turned every imported
# Adventure Lab cache (code Q) into a Mystery cache, reported independently
# by two users (Véé X Péé on Facebook, and visible in C3GPS's #530 GSAK
# import log: LB-prefixed Lab caches showing cache_type "Unknown Cache").
#
# Added below: six more codes with an unambiguous, already-existing
# OpenSAK CACHE_TYPES equivalent (utils/constants.py). Deliberately NOT
# added: D ("Groundspeak Lost and Found Celebration") and F ("Lost and
# Found Event") — two distinct legacy event variants with no clear 1:1
# match to our single "Community Celebration Event" entry — and Y
# (Waymark), which has no OpenSAK equivalent at all. These three keep
# falling back to "Unknown Cache" rather than risk a wrong mapping.
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
    "Q": "Lab Cache",
    "A": "Project A.P.E. Cache",
    "H": "Geocaching HQ Cache",
    "J": "Giga-Event Cache",
    "P": "Geocaching HQ Block Party",
    "X": "GPS Adventures Maze",
    "Z": "Mega-Event Cache",
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

# ── Issue #472: embedded local image references in UserNote ─────────────────
# GSAK's own "GrabImages" macro downloads images referenced in a cache's
# online content and embeds them into CacheMemo.UserNote as HTML <img> tags
# pointing at a local file:/// path on the GSAK user's *old* computer, e.g.:
#   <img src="file:///C:\Users\Bob\...\GeocacheImages\GC3KMD2-null.jpg"
#        width=600 alt="null" title="null">
# These paths are meaningless on a different machine (or even the same
# machine, once GSAK itself is uninstalled), and OpenSAK's Notes tab is a
# plain QPlainTextEdit — it can't render HTML/images either way. Verified
# against a real 12,600-cache database: 154 notes / 312 individual <img>
# tags, all following this exact pattern (0 anomalies against the regex
# below). Each tag is replaced with a plain-text placeholder holding just
# the image's base filename — not the full local path, which could reveal
# the source computer's username/folder structure.
_EMBEDDED_IMG_PATTERN = re.compile(r'<img src="file:///([^"]+)"[^>]*>')


# ── Issue #538: CacheMemo.TravelBugs (one line per trackable) ───────────────
# Format confirmed by the #538 reporter (nagisml): one trackable per line,
# ``<Name> (id = <TrackableID>, ref = <Trackable Reference>)``, e.g.
# ``Best TB ever (id = 1234567, ref = TBAB12CD)``. The name itself may
# legitimately contain parentheses, so the pattern anchors on the specific
# ``(id = ..., ref = ...)`` suffix rather than a bare ``(...)`` group, and
# takes everything before it as the name.
_TRAVELBUG_LINE_PATTERN = re.compile(
    r"^(?P<name>.*?)\s*\(id\s*=\s*(?P<id>\d+)\s*,\s*ref\s*=\s*(?P<ref>[A-Za-z0-9]+)\)\s*$"
)


def _parse_gsak_trackables(raw: Optional[str]) -> list[dict]:
    """Parse GSAK's ``CacheMemo.TravelBugs`` free-text field into a list of
    ``{"name", "ref", "tracking_code"}`` dicts, one per matched line.

    A line that doesn't match the expected pattern is skipped rather than
    aborting the whole cache import — better to import the trackables we
    can parse than to drop them all over one unexpected line.
    """
    if not raw:
        return []
    trackables: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TRAVELBUG_LINE_PATTERN.match(line)
        if m is None:
            continue
        trackables.append({
            "name":           _s(m.group("name")),
            "ref":            _s(m.group("ref")),
            "tracking_code":  _s(m.group("id")),
        })
    return trackables


def _replace_embedded_images_with_placeholders(note_text: str) -> tuple[str, int]:
    """Replace every ``<img src="file:///...">`` tag in *note_text* with a
    ``[image: filename.ext]`` placeholder. Returns (new_text, images_replaced).

    Everything else in the note (captions, GSAK's ``~~GeocacheImages~~``
    section markers, etc.) is left exactly as-is — this only targets the
    specific un-renderable image tags, not a general HTML/text cleanup.
    """
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        local_path = m.group(1)
        filename = local_path.replace("\\", "/").rsplit("/", 1)[-1]
        return f"[image: {filename}]"

    return _EMBEDDED_IMG_PATTERN.sub(_sub, note_text), count


def scan_gsak_notes_for_embedded_images(db_path: Path) -> dict:
    """
    Pre-scan a GSAK database for personal notes containing embedded local
    images, *without* importing anything — for the import dialog (session 4)
    to show a one-time warning before the user commits to importing.

    Returns ``{"affected_notes": int, "total_images": int}``.
    """
    conn = _open_readonly(Path(db_path))
    try:
        affected_notes = 0
        total_images = 0
        cur = conn.execute("SELECT UserNote FROM CacheMemo WHERE UserNote LIKE '%file:///%'")
        for row in cur.fetchall():
            n = len(_EMBEDDED_IMG_PATTERN.findall(row["UserNote"] or ""))
            if n:
                affected_notes += 1
                total_images += n
        return {"affected_notes": affected_notes, "total_images": total_images}
    finally:
        conn.close()


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
    """ImportResult plus GSAK-specific counters (attributes, corrected coords, logs, notes)."""

    def __init__(self):
        super().__init__()
        self.attributes: int = 0
        self.corrected: int = 0
        self.logs: int = 0
        self.notes: int = 0
        self.note_images_replaced: int = 0
        self.trackables: int = 0
        self.encoding_fallbacks: int = 0

    def __str__(self) -> str:
        base = super().__str__()
        return (base + f"\n  Attributes     : {self.attributes}"
                f"\n  Corrected coords: {self.corrected}"
                f"\n  Logs           : {self.logs}"
                f"\n  Notes          : {self.notes}"
                f"\n  Note images -> placeholders: {self.note_images_replaced}"
                f"\n  Trackables     : {self.trackables}"
                f"\n  Encoding fallbacks (non-UTF-8 fields): {self.encoding_fallbacks}")


def find_gsak_db3_in_zip(path: Path) -> Path:
    """If *path* is a .zip, extract it to a temp dir and locate the
    ``sqlite.db3`` inside (GSAK backups store it in a named subdirectory,
    not at the zip root — e.g. ``Sommerhus/sqlite.db3``). If *path* is
    already a ``.db3``/other file, it's returned unchanged.

    Raises ``ValueError`` if a .zip is given but contains no sqlite.db3.
    Shared by ``scripts/import_gsak.py`` and the GSAK import dialog so both
    behave identically.
    """
    path = Path(path)
    if path.suffix.lower() != ".zip":
        return path

    import tempfile
    import zipfile

    extract_dir = Path(tempfile.mkdtemp(prefix="gsak_extract_"))
    with zipfile.ZipFile(path) as zf:
        zf.extractall(extract_dir)

    matches = list(extract_dir.rglob("sqlite.db3"))
    if not matches:
        raise ValueError(f"No sqlite.db3 file found inside {path.name}")
    return matches[0]


def _open_readonly(
    db_path: Path, fallback_counter: Optional[list[int]] = None
) -> sqlite3.Connection:
    """Open the GSAK database strictly read-only (we never write to it).

    GSAK databases are Windows desktop files and aren't guaranteed to store
    text as UTF-8 — older or rarely-touched fields (e.g. ``SmartName``, part
    of GSAK's "Smart Names" macro feature) may still hold whatever system
    codepage the row was written under, commonly Windows-1252 for Western
    European installs. Python's sqlite3 default text_factory assumes strict
    UTF-8 and raises ``OperationalError`` the moment it hits a byte sequence
    that isn't valid UTF-8 — aborting the *entire* import over a single
    unrelated field OpenSAK doesn't even use, swept in by ``SELECT c.*``
    (issue #529 follow-up, reported by Thomas Bang Christensen).

    We try UTF-8 first (the common, correct case), fall back to cp1252, and
    finally fall back to a replacement-character decode so one oddly-encoded
    legacy field can never abort an otherwise-healthy import.
    """
    def _lenient_text_factory(data: bytes) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            if fallback_counter is not None:
                fallback_counter[0] += 1
            try:
                return data.decode("cp1252")
            except UnicodeDecodeError:
                return data.decode("utf-8", errors="replace")

    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.text_factory = _lenient_text_factory
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


def _combine_date_time(date_val, time_val) -> Optional[datetime]:
    """Combine GSAK's separate lDate ('YYYY-MM-DD') and lTime ('HH:MM:SS')
    columns into one datetime. Falls back to midnight if lTime is missing."""
    base = _d(date_val)
    if base is None:
        return None
    raw_time = _s(time_val)
    if raw_time is None:
        return base
    try:
        h, m, sec = (int(p) for p in raw_time.split(":"))
        return base + timedelta(hours=h, minutes=m, seconds=sec)
    except (ValueError, TypeError):
        return base


def _load_logs_by_parent(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Return ``{parent_gc_code: [log dict, ...]}`` for every log entry.

    LEFT JOINs LogMemo since the two tables can drift by a row or two in
    practice (seen on the 12,600-cache sample: 1,123,992 Logs vs 1,123,991
    LogMemo rows) — a missing LogMemo row must not drop the log itself.
    """
    cur = conn.execute("""
        SELECT l.lParent, l.lLogId, l.lType, l.lBy, l.lDate, l.lTime,
               l.lLat, l.lLon, l.lEncoded, l.lownerid, l.lIsowner,
               m.lText
        FROM Logs l
        LEFT JOIN LogMemo m ON l.lParent = m.lParent AND l.lLogId = m.lLogId
    """)
    by_parent: dict[str, list[dict]] = {}
    for row in cur.fetchall():
        parent = row["lParent"]
        # GSAK's lLogId is only unique *per cache*, not globally (verified:
        # the same lLogId can appear on dozens of different caches — see
        # module docstring). (lParent, lLogId) together is always unique,
        # so that's what we build our globally-unique log_id from.
        by_parent.setdefault(parent, []).append({
            "log_id":       f"{parent}_{row['lLogId']}",
            "log_type":     _s(row["lType"]) or "Note",
            "log_date":     _combine_date_time(row["lDate"], row["lTime"]),
            "finder":       _s(row["lBy"]),
            "finder_id":    _s(row["lownerid"]),
            "latitude":     _f(row["lLat"]),
            "longitude":    _f(row["lLon"]),
            "text_encoded": _b(row["lEncoded"]),
            "logged_by_owner": _b(row["lIsowner"]),
            "text":         _s(row["lText"]),
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
    """Return ``{gc_code: {corrected_lat, corrected_lon, original_lat, original_lon}}``
    for solved caches.

    Issue #614: GSAK's own ``Caches.Latitude``/``Longitude`` reflect the
    *corrected* position once a cache has been solved — the true original/
    posted coordinates are only preserved in the ``Corrected`` table's
    ``kBeforeLat``/``kBeforeLon`` columns. We deliberately do NOT use
    ``Caches.LatOriginal``/``LonOriginal`` for this even though GSAK's own
    docs describe them as kept "in sync" with kBefore*: a real GSAK test
    database (two solved caches) showed LatOriginal/LonOriginal correctly
    in sync for one cache but left equal to the corrected Latitude/Longitude
    for the other. kBeforeLat/kBeforeLon were correct in both cases (and
    matched the cache's own GSAK-exported GPX for the same cache), so the
    Corrected table is the more reliable source.
    """
    cur = conn.execute("""
        SELECT kCode, kBeforeLat, kBeforeLon, kAfterLat, kAfterLon FROM Corrected
    """)
    by_code: dict[str, dict] = {}
    for row in cur.fetchall():
        after_lat, after_lon = _f(row["kAfterLat"]), _f(row["kAfterLon"])
        if after_lat is None or after_lon is None:
            continue
        entry = {"corrected_lat": after_lat, "corrected_lon": after_lon}
        before_lat, before_lon = _f(row["kBeforeLat"]), _f(row["kBeforeLon"])
        if before_lat is not None and before_lon is not None:
            entry["original_lat"] = before_lat
            entry["original_lon"] = before_lon
        by_code[row["kCode"]] = entry
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

    # ── Issue #472: personal note text, with embedded-image placeholders ────
    raw_note = _s(row["UserNote"])
    note_text: Optional[str] = None
    note_images_replaced = 0
    if raw_note is not None:
        note_text, note_images_replaced = _replace_embedded_images_with_placeholders(raw_note)

    # ── Issue #538: trackables ────────────────────────────────────────────
    trackables = _parse_gsak_trackables(row["TravelBugs"])

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
        # Issue #541: GSAK's own $d_IsPremium column ("Geocaching.com member
        # only cache status") was never read here — the GPX importer already
        # handled the equivalent gsak:IsPremium extension element, but the
        # direct-DB path silently dropped it, so every GSAK-DB-imported
        # cache came in as non-premium regardless of the source data.
        "premium_only":   _b(row["IsPremium"]),
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

        # ── Issue #472 ────────────────────────────────────────────────────
        "note_text":            note_text,
        "note_images_replaced": note_images_replaced,

        # ── Issue #538 ────────────────────────────────────────────────────
        "trackables": trackables,
    }


_CACHE_JOIN_SQL = """
    SELECT c.*, m.LongDescription, m.ShortDescription, m.Url, m.Hints, m.UserNote,
           m.TravelBugs
    FROM Caches c
    LEFT JOIN CacheMemo m ON c.Code = m.Code
"""


def _upsert_cache_from_gsak(
    session: Session,
    data: dict,
    attr_rows: list[dict],
    wpt_rows: list[dict],
    log_rows: list[dict],
    corrected: Optional[dict],
    attr_names: dict[int, str],
    source_file: str,
    existing_ids: dict[str, int],
    warnings: Optional[list[str]] = None,
) -> tuple[Cache, bool]:
    """
    Insert or update a Cache row from parsed GSAK data.

    Same as the GPX importer's ``_upsert_cache``: Waypoints, Attributes,
    Logs and (as of #538) Trackables are all rebuilt (delete + recreate) on
    every re-import of a given cache.
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
        session.query(Log).filter_by(cache_id=cache.id).delete(synchronize_session=False)
        session.query(Trackable).filter_by(cache_id=cache.id).delete(synchronize_session=False)
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

        # Issue #614: Caches.Latitude/Longitude reflect the *corrected*
        # position for a solved cache — replace with the true original/
        # posted coordinates from the Corrected table so the primary
        # cache.latitude/longitude always matches the cache's listed
        # location, mirroring how the GPX importer handles GSAK's own
        # LatBeforeCorrect/LonBeforeCorrect extension fields.
        if corrected is not None:
            orig_lat = corrected.get("original_lat")
            orig_lon = corrected.get("original_lon")
            if orig_lat is not None and orig_lon is not None:
                cache.latitude = orig_lat
                cache.longitude = orig_lon

    # Personal/status fields are not subject to the lock — mirrors GPX
    # import behaviour, where a re-import can still bring in a newer
    # found/DNF/favourite-points state without unlocking the listing data.
    for field in (
        "found", "found_date", "dnf", "dnf_date", "first_to_find",
        "user_flag", "user_sort", "premium_only",
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
    # Issue #536: dedup by wp_code (GSAK's own per-cache waypoint code,
    # cCode), matching the uq_waypoint_cache_wp_code constraint — not by
    # prefix+name, which real GSAK databases can legitimately repeat across
    # distinct waypoints on the same cache (e.g. several stages a user
    # happened to name identically). A waypoint with no wp_code is never
    # treated as a duplicate of another (mirrors SQL's own NULL-is-distinct
    # behaviour for the unique index) — only a genuine repeated non-empty
    # wp_code is dropped here (query is ORDER BY cCode, so this is
    # deterministic) rather than failing the whole cache's import.
    seen_wpt_codes: set[str] = set()
    added_wpt_count = 0
    for w in wpt_rows:
        code = w["wp_code"]
        if code is not None:
            if code in seen_wpt_codes:
                if warnings is not None:
                    warnings.append(
                        f"{gc_code}: dropped duplicate waypoint {code!r} "
                        f"(wp_code already used by another waypoint on this cache)"
                    )
                continue
            seen_wpt_codes.add(code)
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
        added_wpt_count += 1
    cache.waypoint_count = added_wpt_count

    # ── Issue #538: Trackables ──────────────────────────────────────────────
    trackable_rows = data.get("trackables", [])
    for t in trackable_rows:
        session.add(Trackable(
            cache=cache,
            name=t["name"],
            ref=t["ref"],
            tracking_code=t["tracking_code"],
        ))
    cache.trackable_count = len(trackable_rows)

    # bulk_insert_mappings below needs a real cache.id — guaranteed for
    # existing caches, but a brand-new cache only gets one on flush.
    if cache.id is None:
        session.flush()

    # Logs
    # log_id is built as f"{gc_code}_{lLogId}" in _load_logs_by_parent, which
    # is verified unique per (parent, lLogId) pair — but guard with a seen-set
    # anyway (defence in depth, e.g. if a future GSAK version's data doesn't
    # hold that invariant) so a collision drops the duplicate instead of
    # crashing the whole cache via the logs.log_id UNIQUE constraint.
    #
    # Uses bulk_insert_mappings rather than per-row session.add(Log(...)) —
    # on the 1.1M-log real database, individual ORM object creation took
    # ~185s total; bulk_insert_mappings bypasses per-object unit-of-work
    # tracking for a large, uniform, insert-only batch like this.
    seen_log_ids: set[str] = set()
    log_dates: list[datetime] = []
    log_mappings: list[dict] = []
    for lg in log_rows:
        log_id = lg["log_id"]
        if log_id in seen_log_ids:
            if warnings is not None:
                warnings.append(f"{gc_code}: dropped duplicate log id {log_id!r}")
            continue
        seen_log_ids.add(log_id)
        log_mappings.append({
            "cache_id":        cache.id,
            "log_id":          log_id,
            "log_type":        lg["log_type"],
            "log_date":        lg["log_date"],
            "finder":          lg["finder"],
            "finder_id":       lg["finder_id"],
            "text":            lg["text"],
            "text_encoded":    lg["text_encoded"],
            "latitude":        lg["latitude"],
            "longitude":       lg["longitude"],
            "logged_by_owner": lg["logged_by_owner"],
        })
        if lg["log_date"] is not None:
            log_dates.append(lg["log_date"])

    if log_mappings:
        session.bulk_insert_mappings(inspect(Log), log_mappings)

    # ── Issue #87/#186: cached log_count / last_log_date for fast UI display
    cache.log_count = len(seen_log_ids)
    cache.last_log_date = max(log_dates) if log_dates else None

    # ── Issue #552: cached count of the user's own found-type logs ──────────
    # GSAK databases hold the full log history (unlike a PQ's 5-log window),
    # so this is the most reliable source for the "found N times" count on
    # relocatable/multi-visit caches. See count_own_found_logs() for the
    # finder_id/username matching rules (mirrors found_date/FTF derivation).
    from opensak.gui.settings import get_settings
    from opensak.utils.utils import count_own_found_logs
    _sett = get_settings()
    cache.found_log_count = count_own_found_logs(
        log_rows, _sett.gc_finder_id, _sett.gc_username
    )

    # UserNote: corrected coordinates + personal note text (#469 + #472).
    # Always overwritten on import — same rebuild-every-time policy as
    # Waypoints/Attributes/Logs, per Allan's call that a merge/protect-
    # existing-note strategy isn't worth the complexity for what's mostly a
    # one-time GSAK-to-OpenSAK migration, not a repeated two-way sync.
    note_text = data.get("note_text")
    if corrected is not None or note_text is not None:
        note = cache.user_note
        if note is None:
            note = UserNote(cache=cache)
            session.add(note)
        if corrected is not None:
            note.corrected_lat = corrected["corrected_lat"]
            note.corrected_lon = corrected["corrected_lon"]
            note.is_corrected = True
        if note_text is not None:
            note.note = note_text
            if data.get("note_images_replaced"):
                if warnings is not None:
                    warnings.append(
                        f"{gc_code}: replaced {data['note_images_replaced']} embedded "
                        f"image reference(s) in the personal note with placeholders"
                    )

    return cache, created


def _flush_gsak_batch(
    session: Session,
    batch: list[tuple[dict, list[dict], list[dict], list[dict], Optional[dict]]],
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
        pending: list[tuple[Cache, bool, int, bool, bool, int]] = []
        for data, attr_rows, wpt_rows, log_rows, corrected in batch:
            cache, created = _upsert_cache_from_gsak(
                session, data, attr_rows, wpt_rows, log_rows, corrected,
                attr_names, source, existing_ids, result.warnings,
            )
            pending.append((
                cache, created, len(attr_rows), corrected is not None,
                data.get("note_text") is not None, data.get("note_images_replaced", 0),
            ))
        sp.commit()
    except Exception:
        sp.rollback()
        _flush_gsak_batch_isolated(session, batch, attr_names, source, existing_ids, result)
        return

    for cache, created, n_attrs, had_corrected, had_note, n_images in pending:
        if created:
            result.created += 1
            existing_ids[cache.gc_code] = cache.id
        else:
            result.updated += 1
        result.waypoints += cache.waypoint_count
        result.attributes += n_attrs
        result.logs += cache.log_count
        result.trackables += cache.trackable_count
        if had_corrected:
            result.corrected += 1
        if had_note:
            result.notes += 1
        result.note_images_replaced += n_images


def _flush_gsak_batch_isolated(
    session: Session,
    batch: list[tuple[dict, list[dict], list[dict], list[dict], Optional[dict]]],
    attr_names: dict[int, str],
    source: str,
    existing_ids: dict[str, int],
    result: GsakImportResult,
) -> None:
    """Replay a batch one cache at a time so a single bad cache only skips itself."""
    for data, attr_rows, wpt_rows, log_rows, corrected in batch:
        cell = session.begin_nested()
        try:
            cache, created = _upsert_cache_from_gsak(
                session, data, attr_rows, wpt_rows, log_rows, corrected,
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
            result.logs += cache.log_count
            result.trackables += cache.trackable_count
            if corrected is not None:
                result.corrected += 1
            if data.get("note_text") is not None:
                result.notes += 1
            result.note_images_replaced += data.get("note_images_replaced", 0)
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
    Import a GSAK ``sqlite.db3`` file directly into OpenSAK (scope:
    Caches/CacheMemo/Corrected/Waypoints/WayMemo/Attributes/Logs/Trackables).

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
        fallback_counter = [0]
        conn = _open_readonly(db_path, fallback_counter)
    except sqlite3.Error as e:
        result.errors.append(f"Could not open GSAK database: {e}")
        return result

    try:
        total = conn.execute("SELECT COUNT(*) FROM Caches").fetchone()[0]
        wpts_by_parent = _load_waypoints_by_parent(conn)
        attrs_by_code = _load_attributes_by_code(conn)
        corrected_by_code = _load_corrected_by_code(conn)
        logs_by_parent = _load_logs_by_parent(conn)
        attr_names = _attribute_name_lookup()

        existing_ids = _load_existing_gc_map(session)
        _enter_bulk_import_pragmas(session)

        batch: list[tuple[dict, list[dict], list[dict], list[dict], Optional[dict]]] = []
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
                    logs_by_parent.get(gc_code, []),
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

    result.encoding_fallbacks = fallback_counter[0]
    return result
