"""
database.py — Database engine, session factory, and initialisation helpers.

Usage
-----
    from opensak.db.database import init_db, get_session

    init_db()                    # create tables if they don't exist
    with get_session() as session:
        caches = session.query(Cache).all()
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from opensak.db.models import Base


# ── Engine factory ────────────────────────────────────────────────────────────

def _make_engine(db_path: Path) -> Engine:
    """Create a SQLAlchemy engine for a SQLite file at *db_path*."""
    # Ensure parent directory exists (pathlib — cross-platform safe)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    url = f"sqlite:///{db_path}"
    engine = create_engine(
        url,
        echo=False,          # set True to log all SQL (useful for debugging)
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    return engine


@event.listens_for(Engine, "connect")
def _enable_wal_and_fk(dbapi_connection, _connection_record) -> None:
    """
    Enable WAL journal mode (better concurrency) and foreign key enforcement
    every time a new SQLite connection is opened.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# ── Module-level singletons (initialised lazily) ─────────────────────────────

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_migrated_paths: set = set()  # undgår at køre migrationer to gange på samme DB

# Schema version stored in the SQLite header (PRAGMA user_version). MUST be
# bumped to the highest migration number whenever a new migration is added
# below — _run_migrations() skips the whole block when the database already
# reports this version, so a stale constant means new migrations never run.
SCHEMA_VERSION = 12


def init_db(db_path: Path | None = None) -> Engine:
    """
    Initialise the database: create all tables if they don't exist.

    Parameters
    ----------
    db_path : Path, optional
        Override the default database location.  If omitted the path from
        ``opensak.config.get_db_path()`` is used.

    Returns
    -------
    Engine
        The SQLAlchemy engine (useful for tests that want to inspect it).
    """
    global _engine, _SessionLocal

    if db_path is None:
        # Brug aktiv database fra manager hvis tilgængelig,
        # ellers fald tilbage til standard stien (bruges af tests)
        try:
            from opensak.db.manager import get_db_manager
            manager = get_db_manager()
            if manager.active_path:
                db_path = manager.active_path
            else:
                from opensak.config import get_db_path
                db_path = get_db_path()
        except Exception:
            from opensak.config import get_db_path
            db_path = get_db_path()

    new_engine = _make_engine(db_path)
    new_session = sessionmaker(
        bind=new_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,  # keep objects usable after session closes
    )

    # Create all tables that don't exist yet (safe to call multiple times).
    # Do this before touching the globals — if the file is not a valid SQLite
    # database create_all() raises here and the current engine is unaffected.
    Base.metadata.create_all(new_engine)

    # Kør schema-migrationer for eksisterende databaser (kun én gang per DB-sti)
    if db_path not in _migrated_paths:
        _run_migrations(new_engine)
        _migrated_paths.add(db_path)

    # Only swap the global pointers after everything above succeeded.
    _engine = new_engine
    _SessionLocal = new_session
    return _engine


# ── Schema migrationer ────────────────────────────────────────────────────────

def _run_migrations(engine: Engine) -> None:
    """
    Kør inkrementelle schema-migrationer på en eksisterende database.

    Hver migration tjekker om kolonnen/tabellen allerede findes og springer
    over hvis ja — så er det sikkert at kalde ved hver opstart.
    """
    with engine.connect() as conn:
        # ── Gate: skip all probes when the DB is already at the current schema ─
        # Every migration below is idempotent but still runs ~10 PRAGMA
        # table_info probes on each launch. Once a database reports the current
        # SCHEMA_VERSION we can skip the whole block and only pay a single
        # PRAGMA user_version read. Existing databases default to user_version=0
        # and run the (idempotent) migrations once, after which the version is
        # stamped and subsequent launches short-circuit here.
        current_version = conn.execute(text("PRAGMA user_version")).scalar() or 0
        if current_version >= SCHEMA_VERSION:
            return

        # ── Migration 1: Tilføj is_corrected til user_notes ──────────────────
        existing_notes = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(user_notes)")).fetchall()
        ]
        if "is_corrected" not in existing_notes:
            conn.execute(text(
                "ALTER TABLE user_notes ADD COLUMN is_corrected BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()
            print("Migration: tilføjede user_notes.is_corrected")

        # ── Migration 2: Udvid waypoints unique constraint ────────────────────
        # Den gamle constraint (cache_id, prefix) fejler når GSAK eksporterer
        # flere waypoints med samme prefix (f.eks. "WP") for samme cache.
        # Ny constraint: (cache_id, prefix, name) — tillader flere WP-waypoints
        # så længe de har forskellige navne.
        idx_rows = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='waypoints'"
        )).fetchall()
        idx_names = [r[0] for r in idx_rows]

        if "uq_waypoint_cache_prefix_name" not in idx_names:
            # SQLite understøtter ikke DROP CONSTRAINT — vi recreater tabellen.
            # Unik-constraint laves som et NAVNGIVET index (ikke inline UNIQUE,
            # der ville få et auto-genereret sqlite_autoindex-navn) så gaten
            # ovenfor faktisk genkender den og matcher modellens constraint-navn.
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(text("""
                CREATE TABLE waypoints_new (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_id    INTEGER NOT NULL REFERENCES caches(id),
                    prefix      TEXT,
                    wp_type     TEXT,
                    name        TEXT,
                    description TEXT,
                    comment     TEXT,
                    latitude    REAL,
                    longitude   REAL
                )
            """))
            conn.execute(text(
                "INSERT INTO waypoints_new "
                "SELECT id, cache_id, prefix, wp_type, name, description, comment, latitude, longitude "
                "FROM waypoints"
            ))
            conn.execute(text("DROP TABLE waypoints"))
            conn.execute(text("ALTER TABLE waypoints_new RENAME TO waypoints"))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_waypoint_cache_prefix_name "
                "ON waypoints (cache_id, prefix, name)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_waypoints_cache_id ON waypoints (cache_id)"
            ))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
            print("Migration: opdaterede waypoints unique constraint til (cache_id, prefix, name)")

        # ── Migration 3: Tilføj county til caches ────────────────────────────
        existing_caches = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "county" not in existing_caches:
            conn.execute(text("ALTER TABLE caches ADD COLUMN county VARCHAR(64)"))
            conn.commit()
            print("Migration: tilføjede caches.county")

        # ── Migration 4: GSAK field parity (issue #33) ───────────────────────
        # Re-læs eksisterende kolonner efter migration 3 kan have tilføjet county
        existing_caches = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]

        gsak_columns = [
            ("dnf_date",        "DATETIME"),
            ("first_to_find",   "BOOLEAN DEFAULT 0"),
            ("user_flag",       "BOOLEAN DEFAULT 0"),
            ("user_sort",       "INTEGER"),
            ("user_data_1",     "TEXT"),
            ("user_data_2",     "TEXT"),
            ("user_data_3",     "TEXT"),
            ("user_data_4",     "TEXT"),
            ("distance",        "FLOAT"),
            ("bearing",         "FLOAT"),
            ("favorite_points", "INTEGER"),
        ]

        added = []
        for col_name, col_def in gsak_columns:
            if col_name not in existing_caches:
                conn.execute(text(f"ALTER TABLE caches ADD COLUMN {col_name} {col_def}"))
                added.append(col_name)

        if added:
            conn.commit()
            print(f"Migration: tilføjede GSAK-felter til caches: {', '.join(added)}")

        # ── Migration 5: Normaliser GPS Adventures cache_type varianter ─────
        result = conn.execute(text("""
            UPDATE caches
            SET cache_type = 'GPS Adventures Maze'
            WHERE LOWER(cache_type) IN (
                'gps adventures exhibit',
                'gps adventures maze exhibit'
            )
        """))
        if result.rowcount:
            conn.commit()
            print(f"Migration: normaliserede {result.rowcount} GPS Adventures cache_type værdier")

        # ── Migration 6: log_count kolonne (issue #87) ───────────────────────
        # log_count caches the number of logs per cache so the UI can display
        # it without loading the logs relationship. apply_filters() uses
        # noload(Cache.logs) for performance, which made cache.logs always
        # return an empty list — and len(cache.logs) was therefore always 0.
        # We add the column AND populate it from existing data so users don't
        # have to re-import their databases for the count to appear.
        existing_caches = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "log_count" not in existing_caches:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN log_count INTEGER NOT NULL DEFAULT 0"
            ))
            # Populate from existing logs table — one UPDATE for all caches
            result = conn.execute(text("""
                UPDATE caches
                SET log_count = (
                    SELECT COUNT(*)
                    FROM logs
                    WHERE logs.cache_id = caches.id
                )
            """))
            conn.commit()
            print(f"Migration: tilføjede caches.log_count og opdaterede {result.rowcount} caches")


        # ── Migration 7: Nano → Micro (data normalisation) ───────────────────
        # "Nano" is not an official Geocaching.com container size — it is an
        # informal community term for very small Micro caches (< 10 ml).
        # Geocaching.com exports these as "Micro" in GPX/PQ files. Only GSAK
        # databases may contain "Nano". We convert silently so the filter
        # dialog and sort order work correctly without re-importing.
        result = conn.execute(text(
            "UPDATE caches SET container = 'Micro' WHERE container = 'Nano'"
        ))
        if result.rowcount:
            conn.commit()
            print(f"Migration: konverterede {result.rowcount} 'Nano' container værdier til 'Micro'")


        # ── Migration 8: parent_gc_code (issue #141) ─────────────────────────
        # Custom waypoints (CW...) can optionally reference a parent geocache.
        # NULL for all real geocaches — added silently, no data backfill needed.
        existing_caches = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "parent_gc_code" not in existing_caches:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN parent_gc_code VARCHAR(16)"
            ))
            conn.commit()
            print("Migration: tilføjede caches.parent_gc_code")

        # ── Migration 9: owner_name (issue #158) ─────────────────────────────
        # Stores the gs:owner display name separately from placed_by, which may
        # differ when a cache is adopted or placed under a pseudonym.
        existing_caches = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "owner_name" not in existing_caches:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN owner_name VARCHAR(128)"
            ))
            conn.commit()
            print("Migration: tilføjede caches.owner_name")

        # ── Migration 10: last_log_date (issue #186) ─────────────────────────
        # Caches the date of the most recent log so the "Latest Log" column can
        # display it without loading the noload'ed logs relationship.
        # Populated from existing log data so users don't need to re-import.
        existing_caches = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "last_log_date" not in existing_caches:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN last_log_date DATETIME"
            ))
            result = conn.execute(text("""
                UPDATE caches
                SET last_log_date = (
                    SELECT MAX(log_date)
                    FROM logs
                    WHERE logs.cache_id = caches.id
                )
            """))
            conn.commit()
            print(f"Migration: tilføjede caches.last_log_date og opdaterede {result.rowcount} caches")

        # ── Migration 11: indexes for filter/sort columns (#214 phase 3) ─────
        # Phase 2 pushed the common filters into the SQL WHERE clause; these
        # indexes let SQLite satisfy those predicates (and ORDER BY) without a
        # full table scan on large databases. CREATE INDEX IF NOT EXISTS is
        # idempotent and cheap once the index exists, so it is safe to run on
        # every startup. Index names follow SQLAlchemy's ix_<table>_<col>
        # convention, so a future index=True on the model would reuse the same
        # name rather than create a duplicate.
        # Note: country/state/county are intentionally NOT indexed — their
        # filters use LIKE '%text%' (substring), which a B-tree index cannot
        # satisfy, so an index there would be pure write overhead. cache_type
        # (exact IN), the difficulty/terrain ranges, the date columns used by
        # ORDER BY, and the availability composite are all sargable.
        index_specs = [
            ("ix_caches_cache_type",    "cache_type"),
            ("ix_caches_difficulty",    "difficulty"),
            ("ix_caches_terrain",       "terrain"),
            ("ix_caches_hidden_date",   "hidden_date"),
            ("ix_caches_found_date",    "found_date"),
            ("ix_caches_last_log_date", "last_log_date"),
            ("ix_caches_found",         "found"),
            # Composite for the availability quick-filter (archived + available)
            ("ix_caches_archived_available", "archived, available"),
            # Composite for DistanceFilter's lat/lon bounding-box pre-narrow
            # (#214 phase 4). Leading latitude column serves the latitude
            # BETWEEN range; longitude refines.
            ("ix_caches_lat_lon", "latitude, longitude"),
        ]
        existing_idx = {
            row[0]
            for row in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='caches'"
            )).fetchall()
        }
        created_idx = []
        for idx_name, cols in index_specs:
            if idx_name not in existing_idx:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON caches ({cols})"
                ))
                created_idx.append(idx_name)
        if created_idx:
            conn.commit()
            print(f"Migration: oprettede {len(created_idx)} indexes på caches ({', '.join(created_idx)})")

        # ── Migration 12: location provenance columns (issue #60 phase 3) ──────
        # Four nullable columns record where territory values came from and which
        # dataset version produced them — needed by the Phase 4 GUI and the
        # stale-indicator logic. All default to NULL ("unknown / imported").
        existing_caches = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        provenance_columns = [
            ("location_source",  "VARCHAR(16)"),
            ("location_basis",   "VARCHAR(16)"),
            ("location_updated", "DATETIME"),
            ("location_dataset", "VARCHAR(64)"),
        ]
        added = []
        for col_name, col_def in provenance_columns:
            if col_name not in existing_caches:
                conn.execute(text(f"ALTER TABLE caches ADD COLUMN {col_name} {col_def}"))
                added.append(col_name)
        if added:
            conn.commit()
            print(f"Migration: tilføjede provenance-kolonner til caches: {', '.join(added)}")

        # ── Stamp the schema version so the next launch skips the probes ─────
        # PRAGMA does not accept bind parameters; SCHEMA_VERSION is a trusted
        # int constant, so inlining it is safe.
        conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
        conn.commit()


def dispose_engine(db_path: Path | None = None) -> None:
    """
    Luk og frigiv SQLAlchemy connection pool for en given database-sti.

    Nødvendigt på Windows før sletning af .db/.db-shm/.db-wal filer —
    SQLite WAL-mode holder filerne låst (WinError 32) så længe pool er åben.
    Kaldes automatisk af DatabaseManager.delete_database() og remove_from_list().

    Hvis db_path er None, disposes den aktive engine uanset sti.
    """
    global _engine, _SessionLocal
    if _engine is None:
        return  # Intet at frigive

    if db_path is not None:
        # Sammenlign stier — normaliser separatorer for Windows-kompatibilitet
        engine_url = str(_engine.url).replace("sqlite:///", "")
        engine_path = Path(engine_url).resolve()
        try:
            target_path = db_path.resolve()
        except OSError:
            target_path = db_path

        if engine_path != target_path:
            return  # Denne engine peger ikke på den ønskede database

    _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_engine() -> Engine:
    """Return the current engine, raising if init_db() hasn't been called."""
    if _engine is None:
        raise RuntimeError("Database not initialised — call init_db() first.")
    return _engine


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context-manager that yields a SQLAlchemy Session and handles
    commit / rollback automatically.

    Example
    -------
        with get_session() as session:
            session.add(some_object)
    """
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() first.")

    session: Session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def make_session():
    """Return a bare Session — caller handles commit/rollback/close."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() first.")
    return _SessionLocal()


def reload_caches_full(caches: list) -> list:
    """Reload *caches* with everything an export needs eagerly loaded.

    Table-model caches come from apply_filters(), which defers the text blobs
    (hints/descriptions) and noloads logs/waypoints for speed. Once their
    session closes they are detached, so an export worker that reads those
    attributes raises DetachedInstanceError. Reloading here returns fully
    populated, detached-safe objects (logs, waypoints, attributes, user_note
    and the text blobs) in the original order.

    Objects without a matching DB row (e.g. test stand-ins) pass through
    unchanged.
    """
    from opensak.db.models import Cache
    from sqlalchemy.orm import joinedload, selectinload, undefer

    # Only persisted Cache rows can be reloaded; pass anything else through
    # (e.g. SimpleNamespace stand-ins in tests).
    ids = [c.id for c in caches if isinstance(c, Cache) and c.id is not None]
    if not ids:
        return list(caches)

    with get_session() as session:
        rows = (
            session.query(Cache)
            .options(
                selectinload(Cache.logs),
                selectinload(Cache.waypoints),
                selectinload(Cache.attributes),
                joinedload(Cache.user_note),
                undefer(Cache.short_description),
                undefer(Cache.long_description),
                undefer(Cache.encoded_hints),
            )
            .filter(Cache.id.in_(ids))
            .all()
        )

    by_id = {c.id: c for c in rows}
    return [by_id.get(c.id, c) if isinstance(c, Cache) else c for c in caches]


# ── Distance recalculation ────────────────────────────────────────────────────

def recalculate_distances(lat: float, lon: float) -> int:
    """Recompute distance and bearing for every cache and persist to the DB.

    Called once whenever the active centre point changes (not on every table
    refresh). Uses distance_km_batch() which dispatches to Haversine or
    Vincenty depending on the user's distance_method setting.

    Returns the number of caches updated.
    """
    from opensak.filters.engine import distance_km_batch

    def _bearing_batch(lat0: float, lon0: float, lats: list, lons: list) -> list:
        # Bearing computation — vectorised with numpy when available.
        import math
        try:
            import numpy as np
            r = math.pi / 180
            la0 = lat0 * r
            la = np.asarray(lats, dtype=float) * r
            dlon = (np.asarray(lons, dtype=float) - lon0) * r
            x = np.sin(dlon) * np.cos(la)
            y = math.cos(la0) * np.sin(la) - math.sin(la0) * np.cos(la) * np.cos(dlon)
            return list((np.degrees(np.arctan2(x, y)) + 360) % 360)
        except ImportError:
            def _scalar(la2: float, lo2: float) -> float:
                r2 = math.pi / 180
                dlon2 = (lo2 - lon0) * r2
                la02 = lat0 * r2
                la22 = la2 * r2
                x2 = math.sin(dlon2) * math.cos(la22)
                y2 = math.cos(la02) * math.sin(la22) - math.sin(la02) * math.cos(la22) * math.cos(dlon2)
                return (math.degrees(math.atan2(x2, y2)) + 360) % 360
            return [_scalar(la, lo) for la, lo in zip(lats, lons)]

    with get_session() as session:
        rows = session.execute(
            text("SELECT id, latitude, longitude FROM caches WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
        ).fetchall()

        if not rows:
            return 0

        ids  = [r[0] for r in rows]
        lats = [r[1] for r in rows]
        lons = [r[2] for r in rows]

        dists = distance_km_batch(lat, lon, lats, lons)
        bears = _bearing_batch(lat, lon, lats, lons)

        session.execute(
            text("UPDATE caches SET distance = :d, bearing = :b WHERE id = :id"),
            [{"d": float(dists[i]), "b": float(bears[i]), "id": ids[i]} for i in range(len(ids))],
        )

    return len(ids)


# ── Health-check helper ───────────────────────────────────────────────────────

def db_health_check() -> dict:
    """
    Return a dict with basic stats about the current database.
    Useful for the startup banner and diagnostics.
    """
    from opensak.db.models import Cache, Log, Waypoint

    with get_session() as s:
        return {
            "caches": s.query(Cache).count(),
            "logs": s.query(Log).count(),
            "waypoints": s.query(Waypoint).count(),
        }
