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

import errno
import logging
import re
import sqlite3
import time
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Generator, Iterable, Iterator, cast

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from opensak.backup.premigration import backup_before_migration, pending_schema_version
from opensak.db import companion, custom_fields, maintenance
from opensak.db.models import Base
from opensak.utils import flags

logger = logging.getLogger(__name__)


# ── Engine factory ────────────────────────────────────────────────────────────

def _make_engine(db_path: Path) -> Engine:
    """Create a SQLAlchemy engine for a SQLite file at *db_path*.

    The folder must already exist. Issue #1045: this used to create it with
    mkdir(parents=True), so opening a database on a removed USB drive walked
    up the missing path and failed on the mount point with PermissionError
    (or, on a writable path, silently created an empty database in a phantom
    folder). Creating folders belongs to the "new database" flows, which do
    it themselves before calling init_db().
    """
    if not db_path.parent.is_dir():
        raise FileNotFoundError(
            errno.ENOENT, "Database folder not found", str(db_path.parent)
        )

    url = f"sqlite:///{db_path}"
    engine = create_engine(
        url,
        echo=False,          # set True to log all SQL (useful for debugging)
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    _attach_companion_on_checkout(engine, db_path)
    return engine


def _attach_companion_on_checkout(engine: Engine, db_path: Path) -> None:
    """
    Attach *db_path*'s companion database (db/companion.py) to each of
    *engine*'s connections, as ``companion``, once it is usable. Checked on
    checkout rather than on connect, so a companion created while the
    engine is open is attached by the next session without reopening the
    database. A connection never has a transaction open at checkout, which
    ATTACH needs.

    It also keeps the ``caches_all`` TEMP view (custom fields, #1028) of
    each connection up to date (db/custom_fields.py), once _open_engine()
    is done with the schema: a TEMP view on ``caches`` makes SQLite refuse
    the table rebuilds of the migrations.
    """
    if not flags.companion_db:
        return
    key = companion.status_key(db_path)

    @event.listens_for(engine, "checkout")
    def _attach(dbapi_connection, connection_record, _proxy) -> None:
        info = connection_record.info
        if (not info.get("companion_attached")
                and companion.status_for_key(key) is companion.Status.OK
                and companion.attach(dbapi_connection, db_path)):
            info["companion_attached"] = True
        if engine in _schema_ready:
            custom_fields.ensure_view(
                dbapi_connection, db_path, info, bool(info.get("companion_attached")))


# Engines whose schema _open_engine() has brought up to date.
_schema_ready: "weakref.WeakSet[Engine]" = weakref.WeakSet()


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
SCHEMA_VERSION = 28

# Steps of opening a database that can take a while, for set_progress_callback().
PROGRESS_MIGRATING = "migrating"
PROGRESS_COMPACTING = "compacting"

_progress_callback: Callable[[str], None] | None = None


def set_progress_callback(callback: Callable[[str], None] | None) -> None:
    """
    Tell *callback* when opening a database starts a slow step:
    PROGRESS_MIGRATING (the schema is being updated) or PROGRESS_COMPACTING
    (VACUUM after a migration that rebuilt tables). The GUI shows it on the
    splash screen or in a progress dialog. None removes the callback.
    """
    global _progress_callback
    _progress_callback = callback


def _report_progress(step: str) -> None:
    if _progress_callback is None:
        return
    try:
        _progress_callback(step)
    except Exception:
        logger.debug("progress callback failed", exc_info=True)


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

    t0 = time.monotonic()
    # Opening (create_all + migrations) happens before touching the globals —
    # if the file is not a valid SQLite database _open_engine() raises and the
    # current engine is unaffected.
    new_engine = _open_engine(db_path)

    # Only swap the global pointers after everything above succeeded. The
    # replaced engine is disposed so repeated init_db() calls don't leave one
    # connection pool per call behind. Sessions still holding a connection
    # from it keep working; the connection is closed when they return it.
    old_engine = _engine
    _engine = new_engine
    _SessionLocal = _session_factory(new_engine)
    if old_engine is not None and old_engine is not new_engine:
        optimize_database(_engine_path(old_engine))
        old_engine.dispose()
    # #1048: statistics for the query planner, created the first time.
    optimize_database(db_path)
    logger.info("init_db: ready (+%.2fs total)", time.monotonic() - t0)
    return _engine


def _session_factory(engine: Engine | Connection) -> sessionmaker:
    """Session factory with the settings every OpenSAK session uses."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,  # keep objects usable after session closes
    )


def _open_engine(db_path: Path) -> Engine:
    """
    Create an engine for *db_path* and bring its schema up to date
    (create_all + migrations), without touching the module globals.

    Shared by init_db() (the app's active database) and session_for()
    (a private engine for a background job on another database).
    """
    new_engine = _make_engine(db_path)
    try:
        # Issue #549: if opening this database is about to migrate its schema,
        # save a one-time copy of it first. This must happen before
        # create_all(), which already adds any new tables to an old database.
        # Best-effort: backup_before_migration() never raises and never
        # blocks the migration.
        if db_path not in _migrated_paths:
            backup_before_migration(db_path, SCHEMA_VERSION)

        # The companion database (#1034): check it and update its own
        # schema, so the connections can attach it. Never raises.
        if flags.companion_db:
            companion.prepare(db_path)
            # Custom fields (#1028): add or rename columns as the
            # definitions say. Never raises.
            custom_fields.on_open(db_path)

        # Create all tables that don't exist yet (safe to call multiple times).
        logger.info("open_db: opening %s", db_path)
        t0 = time.monotonic()
        Base.metadata.create_all(new_engine)
        logger.info("open_db: create_all() done (+%.2fs)", time.monotonic() - t0)

        # Kør schema-migrationer for eksisterende databaser (kun én gang per DB-sti)
        if db_path not in _migrated_paths:
            _run_migrations(new_engine)
            _migrated_paths.add(db_path)
        _schema_ready.add(new_engine)
    except Exception:
        new_engine.dispose()
        raise
    return new_engine


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
            logger.info(
                "migrations: db already at schema %s, skipping", current_version
            )
            return

        logger.info(
            "migrations: starting, current_version=%s target=%s",
            current_version, SCHEMA_VERSION,
        )
        _report_progress(PROGRESS_MIGRATING)
        _mig_t0 = time.monotonic()

        # ── Migration 1: Tilføj is_corrected til user_notes ──────────────────
        logger.debug("[migrations] entering migration 1 (+%.2fs)", time.monotonic() - _mig_t0)
        existing_notes = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(user_notes)")).fetchall()
        ]
        if "is_corrected" not in existing_notes:
            conn.execute(text(
                "ALTER TABLE user_notes ADD COLUMN is_corrected BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()
            logger.info("Migration: added user_notes.is_corrected")

        # ── Migration 2: Udvid waypoints unique constraint ────────────────────
        logger.debug("[migrations] entering migration 2 (+%.2fs)", time.monotonic() - _mig_t0)
        # Den gamle constraint (cache_id, prefix) fejler når GSAK eksporterer
        # flere waypoints med samme prefix (f.eks. "WP") for samme cache.
        # Ny constraint: (cache_id, prefix, name) — tillader flere WP-waypoints
        # så længe de har forskellige navne.
        idx_rows = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='waypoints'"
        )).fetchall()
        idx_names = [r[0] for r in idx_rows]

        # Bug fundet 14/8-2026 (issue #723-opfølgning, Mikes 405MB database):
        # migration 22 nedenfor erstatter SENERE dette constraint med et nyt,
        # bedre ét — (cache_id, wp_code) i stedet for (cache_id, prefix, name),
        # fordi prefix+name legitimt KAN gentages på samme cache (se Waypoint-
        # modellens docstring, issue #536). For enhver database der allerede
        # har kørt migration 22, hedder indexet "uq_waypoint_cache_wp_code" —
        # "uq_waypoint_cache_prefix_name" findes ikke længere. Uden dette tjek
        # troede migration 2 fejlagtigt den aldrig var kørt og forsøgte at
        # genskabe det gamle, for strenge constraint — hvilket fejlede med en
        # UNIQUE constraint-fejl på ægte data med legitime prefix+name-
        # dubletter. Fejlen var ufanget hele vejen op gennem opstartskæden, så
        # appen crashede stille uden fejlbesked — det så ud som et hang.
        if (
            "uq_waypoint_cache_prefix_name" not in idx_names
            and "uq_waypoint_cache_wp_code" not in idx_names
        ):
            logger.debug("[migrations] 2: waypoints table rebuild starting")
            _step_t0 = time.monotonic()
            # SQLite understøtter ikke DROP CONSTRAINT — vi recreater tabellen.
            # Unik-constraint laves som et NAVNGIVET index (ikke inline UNIQUE,
            # der ville få et auto-genereret sqlite_autoindex-navn) så gaten
            # ovenfor faktisk genkender den og matcher modellens constraint-navn.
            # NB: this PRAGMA is a no-op inside an open transaction, and none
            # is committed first. It only works because waypoints is a child
            # table — don't copy this for a parent table (see migration 26).
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
            logger.debug(
                "[migrations] 2: waypoints table rebuild done (+%.2fs)",
                time.monotonic() - _step_t0,
            )
            logger.info("Migration: updated waypoints unique constraint to (cache_id, prefix, name)")

        # ── Migration 3: Tilføj county til caches ────────────────────────────
        logger.debug("[migrations] entering migration 3 (+%.2fs)", time.monotonic() - _mig_t0)
        existing_caches = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "county" not in existing_caches:
            conn.execute(text("ALTER TABLE caches ADD COLUMN county VARCHAR(64)"))
            conn.commit()
            logger.info("Migration: added caches.county")

        # ── Migration 4: GSAK field parity (issue #33) ───────────────────────
        logger.debug("[migrations] entering migration 4 (+%.2fs)", time.monotonic() - _mig_t0)
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
            logger.info(f"Migration: added GSAK fields to caches: {', '.join(added)}")

        # ── Migration 5: Normaliser GPS Adventures cache_type varianter ─────
        logger.debug("[migrations] entering migration 5 (+%.2fs)", time.monotonic() - _mig_t0)
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
            logger.info(f"Migration: normalised {result.rowcount} GPS Adventures cache_type values")

        # ── Migration 6: indexes on logs table (issue #723) ──────────────────
        logger.debug("[migrations] entering migration 6 (+%.2fs)", time.monotonic() - _mig_t0)
        # Migrations 7, 23 and 24 below run correlated subqueries against the
        # logs table (COUNT/MAX per cache_id, and ordering by log_date) to
        # backfill cached columns on `caches`. Without an index on
        # logs.cache_id these are effectively a full table scan per row in
        # `caches`, which is what made startup appear to hang on large
        # existing databases (confirmed by benchmark on issue #723). This
        # migration is placed before all three so the index is guaranteed to
        # exist by the time those heavier queries run. CREATE INDEX IF NOT
        # EXISTS is idempotent and cheap once the index exists, so it is safe
        # to run on every startup.
        existing_log_idx = {
            row[0]
            for row in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='logs'"
            )).fetchall()
        }
        log_index_specs = [
            ("ix_logs_cache_id", "cache_id"),
            ("ix_logs_log_date", "log_date"),
        ]
        created_log_idx = []
        for idx_name, cols in log_index_specs:
            if idx_name not in existing_log_idx:
                logger.debug("[migrations] 6: creating %s starting", idx_name)
                _step_t0 = time.monotonic()
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON logs ({cols})"
                ))
                logger.debug(
                    "[migrations] 6: creating %s done (+%.2fs)",
                    idx_name, time.monotonic() - _step_t0,
                )
                created_log_idx.append(idx_name)
        if created_log_idx:
            conn.commit()
            logger.info(f"Migration: created {len(created_log_idx)} indexes on logs ({', '.join(created_log_idx)})")

        # ── Migration 7: log_count kolonne (issue #87) ───────────────────────
        logger.debug("[migrations] entering migration 7 (+%.2fs)", time.monotonic() - _mig_t0)
        # log_count caches the number of logs per cache so the UI can display
        # it without loading the logs relationship. apply_filters() does not
        # load Cache.logs for performance, which made cache.logs always
        # return an empty list — and len(cache.logs) was therefore always 0.
        # (Since #898 such access raises instead of returning [].)
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
            logger.info(f"Migration: added caches.log_count and updated {result.rowcount} caches")


        # ── Migration 8: Nano → Micro (data normalisation) ───────────────────
        logger.debug("[migrations] entering migration 8 (+%.2fs)", time.monotonic() - _mig_t0)
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
            logger.info(f"Migration: converted {result.rowcount} 'Nano' container values to 'Micro'")


        # ── Migration 9: parent_gc_code (issue #141) ─────────────────────────
        logger.debug("[migrations] entering migration 9 (+%.2fs)", time.monotonic() - _mig_t0)
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
            logger.info("Migration: added caches.parent_gc_code")

        # ── Migration 10: owner_name (issue #158) ─────────────────────────────
        logger.debug("[migrations] entering migration 10 (+%.2fs)", time.monotonic() - _mig_t0)
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
            logger.info("Migration: added caches.owner_name")

        # ── Migration 11: last_log_date (issue #186) ─────────────────────────
        logger.debug("[migrations] entering migration 11 (+%.2fs)", time.monotonic() - _mig_t0)
        # Caches the date of the most recent log so the "Latest Log" column can
        # display it without loading the (unloaded) logs relationship.
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
            logger.info(f"Migration: added caches.last_log_date and updated {result.rowcount} caches")

        # ── Migration 12: indexes for filter/sort columns (#214 phase 3) ─────
        logger.debug("[migrations] entering migration 12 (+%.2fs)", time.monotonic() - _mig_t0)
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
                logger.debug("[migrations] 12: creating %s starting", idx_name)
                _step_t0 = time.monotonic()
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON caches ({cols})"
                ))
                logger.debug(
                    "[migrations] 12: creating %s done (+%.2fs)",
                    idx_name, time.monotonic() - _step_t0,
                )
                created_idx.append(idx_name)
        if created_idx:
            conn.commit()
            logger.info(f"Migration: created {len(created_idx)} indexes on caches ({', '.join(created_idx)})")

        # ── Migration 13: location provenance columns (issue #60 phase 3) ──────
        logger.debug("[migrations] entering migration 13 (+%.2fs)", time.monotonic() - _mig_t0)
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
            logger.info(f"Migration: added provenance columns to caches: {', '.join(added)}")

        # ── Migration 14: parent_gc_code on waypoints (issue #376) ───────────
        logger.debug("[migrations] entering migration 14 (+%.2fs)", time.monotonic() - _mig_t0)
        # Stores the parent cache's GC code directly on the waypoint row —
        # mirrors cParent in GSAK and enables JOIN-free filter queries.
        existing_wpts = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(waypoints)")).fetchall()
        ]
        if "parent_gc_code" not in existing_wpts:
            conn.execute(text(
                "ALTER TABLE waypoints ADD COLUMN parent_gc_code VARCHAR(16)"
            ))
            # Back-fill from the caches table via the existing FK.
            conn.execute(text("""
                UPDATE waypoints
                SET parent_gc_code = (
                    SELECT gc_code FROM caches WHERE caches.id = waypoints.cache_id
                )
            """))
            conn.commit()
            logger.info("Migration: added waypoints.parent_gc_code")

        # ── Migration 15: waypoint_count on caches (issue #377) ──────────────
        logger.debug("[migrations] entering migration 15 (+%.2fs)", time.monotonic() - _mig_t0)
        # Cached count of child waypoints so the grid can show a visual cue
        # without loading the (unloaded) waypoints relationship.
        existing_caches_14 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "waypoint_count" not in existing_caches_14:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN waypoint_count INTEGER NOT NULL DEFAULT 0"
            ))
            conn.execute(text("""
                UPDATE caches
                SET waypoint_count = (
                    SELECT COUNT(*) FROM waypoints WHERE waypoints.cache_id = caches.id
                )
            """))
            conn.commit()
            logger.info("Migration: added caches.waypoint_count")

        # ── Migration 16: locked flag on caches (issue #202) ─────────────────
        logger.debug("[migrations] entering migration 16 (+%.2fs)", time.monotonic() - _mig_t0)
        # When set, _upsert_cache() skips overwriting scalar GPX-sourced
        # fields on re-import, so a manually corrected/locked cache survives
        # later PQ/GPX imports unchanged.
        existing_caches_15 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "locked" not in existing_caches_15:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN locked BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()
            logger.info("Migration: added caches.locked")

        # ── Migration 17: trackables table (issue #491) ───────────────────────
        logger.debug("[migrations] entering migration 17 (+%.2fs)", time.monotonic() - _mig_t0)
        # The Trackable model (travel bugs / geocoins seen in a cache) has
        # shipped since v1.14.0 with no corresponding migration to create its
        # table. Any existing database crashes with "no such table:
        # trackables" the instant anything queries Cache.trackables — e.g.
        # the already-shipped "Has trackables" filter in the Filter dialog,
        # or the importer's re-import cleanup step.
        existing_tables = {
            row[0]
            for row in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )).fetchall()
        }
        if "trackables" not in existing_tables:
            conn.execute(text("""
                CREATE TABLE trackables (
                    id INTEGER NOT NULL,
                    cache_id INTEGER NOT NULL,
                    tracking_code VARCHAR(64),
                    name VARCHAR(256),
                    ref VARCHAR(64),
                    PRIMARY KEY (id),
                    FOREIGN KEY(cache_id) REFERENCES caches (id)
                )
            """))
            conn.execute(text(
                "CREATE INDEX ix_trackables_cache_id ON trackables (cache_id)"
            ))
            conn.commit()
            logger.info("Migration: created the trackables table (missing since v1.14.0)")

        # ── Migration 18: trackable_count kolonne (issue #489/#491) ──────────
        logger.debug("[migrations] entering migration 18 (+%.2fs)", time.monotonic() - _mig_t0)
        # Mirrors log_count (Migration 7): cache the trackable count on the
        # caches row so the new "Trackables" table column can display it
        # without loading the trackables relationship for every row. Depends
        # on Migration 17 above having created the trackables table.
        existing_caches_17 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "trackable_count" not in existing_caches_17:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN trackable_count INTEGER NOT NULL DEFAULT 0"
            ))
            result = conn.execute(text("""
                UPDATE caches
                SET trackable_count = (
                    SELECT COUNT(*)
                    FROM trackables
                    WHERE trackables.cache_id = caches.id
                )
            """))
            conn.commit()
            logger.info(f"Migration: added caches.trackable_count and updated {result.rowcount} caches")

        # ── Migration 19: GSAK database import schema additions (issue #469) ─
        logger.debug("[migrations] entering migration 19 (+%.2fs)", time.monotonic() - _mig_t0)
        # Adds fields identified during the #469 field-by-field comparison
        # against real GSAK databases, ahead of building the importer itself
        # (session 1), so the mapping code can target the final schema
        # directly instead of retrofitting columns afterwards.
        existing_caches_18 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        gsak_import_cache_columns = [
            ("gc_note",     "TEXT"),
            ("url",         "VARCHAR(512)"),
            ("elevation",   "FLOAT"),
            ("color",       "VARCHAR(16)"),
            ("guid",        "VARCHAR(64)"),
            ("watch",       "BOOLEAN NOT NULL DEFAULT 0"),
            ("gc_cache_id", "VARCHAR(32)"),
        ]
        added = []
        for col_name, col_def in gsak_import_cache_columns:
            if col_name not in existing_caches_18:
                conn.execute(text(f"ALTER TABLE caches ADD COLUMN {col_name} {col_def}"))
                added.append(col_name)
        if added:
            conn.commit()
            logger.info(f"Migration: added GSAK import fields to caches: {', '.join(added)}")

        existing_wpts_18 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(waypoints)")).fetchall()
        ]
        gsak_import_waypoint_columns = [
            ("wp_code",         "VARCHAR(16)"),
            ("url",             "VARCHAR(512)"),
            ("wp_date",         "DATETIME"),
            ("created_by_user", "BOOLEAN NOT NULL DEFAULT 0"),
            ("wp_flag",         "BOOLEAN NOT NULL DEFAULT 0"),
        ]
        added = []
        for col_name, col_def in gsak_import_waypoint_columns:
            if col_name not in existing_wpts_18:
                conn.execute(text(f"ALTER TABLE waypoints ADD COLUMN {col_name} {col_def}"))
                added.append(col_name)
        if added:
            conn.commit()
            logger.info(f"Migration: added GSAK import fields to waypoints: {', '.join(added)}")

        existing_logs_18 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(logs)")).fetchall()
        ]
        gsak_import_log_columns = [
            ("latitude",        "FLOAT"),
            ("longitude",       "FLOAT"),
            ("logged_by_owner", "BOOLEAN NOT NULL DEFAULT 0"),
        ]
        added = []
        for col_name, col_def in gsak_import_log_columns:
            if col_name not in existing_logs_18:
                conn.execute(text(f"ALTER TABLE logs ADD COLUMN {col_name} {col_def}"))
                added.append(col_name)
        if added:
            conn.commit()
            logger.info(f"Migration: added GSAK import fields to logs: {', '.join(added)}")

        # ── Migration 20: find_count on caches (issue #517) ──────────────────
        logger.debug("[migrations] entering migration 20 (+%.2fs)", time.monotonic() - _mig_t0)
        # GC API field prep. Also populated straight away by the GSAK
        # importer from Caches.FoundCount (see #469 gsak_importer.py).
        existing_caches_19 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "find_count" not in existing_caches_19:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN find_count INTEGER"
            ))
            conn.commit()
            logger.info("Migration: added caches.find_count")

        # ── Migration 21: remove leftover favorite_point column (issue #530) ─
        logger.debug("[migrations] entering migration 21 (+%.2fs)", time.monotonic() - _mig_t0)
        # `favorite_point` (singular, Boolean NOT NULL) was briefly introduced
        # in v1.14.0 without a migration to add it to already-existing
        # databases, which surfaced as "no such column" errors (#488). The fix
        # for #488 removed the field from the model, filters, and UI entirely
        # — but never added a migration to drop the physical column from
        # databases that had already been created with it present during that
        # window. On those databases, every insert into `caches` fails with
        # `NOT NULL constraint failed: caches.favorite_point`, since current
        # code has no knowledge of the column and never supplies a value for
        # it. Reported by community tester Bob Long while testing the GSAK
        # Database Import feature (#469).
        existing_caches_20 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "favorite_point" in existing_caches_20:
            # DROP COLUMN requires SQLite 3.35.0+ (March 2021). A full
            # table-rebuild fallback is deliberately not attempted here: the
            # caches table has ~50 columns and a dozen indexes, and hand-
            # rewriting that whole schema to remove one leftover column would
            # carry far more risk of a mistake than the near-zero chance of a
            # bundled SQLite that old on a machine running Python 3.12.
            if sqlite3.sqlite_version_info >= (3, 35, 0):
                conn.execute(text("ALTER TABLE caches DROP COLUMN favorite_point"))
                conn.commit()
                logger.info("Migration: removed leftover caches.favorite_point column")
            else:
                logger.warning(
                    "Migration: could not remove leftover caches.favorite_point "
                    f"column — SQLite {sqlite3.sqlite_version} does not support "
                    "DROP COLUMN (needs 3.35+). Contact hello@opensak.com."
                )

        # ── Migration 22: waypoints unique constraint on wp_code, not name (#536) ─
        logger.debug("[migrations] entering migration 22 (+%.2fs)", time.monotonic() - _mig_t0)
        # GSAK's own uniqueness for a waypoint is per-cache by its own code
        # (cCode, stored here as wp_code) — not by prefix+name. The
        # (cache_id, prefix, name) constraint added in Migration 2 was too
        # strict: real GSAK databases can legitimately have two distinct
        # waypoints on one cache sharing the same prefix+name (e.g. several
        # stages a user happened to name identically) but with different
        # cCode, and those were being silently dropped as "duplicates" on
        # import (reported by nagisml, #536).
        #
        # wp_code is NULL for every GPX-imported waypoint (only the GSAK
        # importer populates it) — SQLite/SQL treats each NULL as distinct
        # for UNIQUE purposes, so this constraint imposes no restriction at
        # all on GPX-sourced waypoints, only on GSAK-sourced ones where
        # wp_code is genuinely GSAK's own per-cache unique waypoint code.
        idx_rows_21 = conn.execute(text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name='waypoints' AND name NOT LIKE 'sqlite_%'"
        )).fetchall()
        idx_names_21 = [r[0] for r in idx_rows_21]

        if "uq_waypoint_cache_wp_code" not in idx_names_21:
            # Preserve every existing index except the old, too-strict unique
            # constraint we're replacing — rather than hardcoding an assumed
            # index list, so this is safe even if a database has picked up
            # extra indexes some other way.
            keep_indexes = [
                (name, sql) for name, sql in idx_rows_21
                if name != "uq_waypoint_cache_prefix_name" and sql
            ]

            # NB: no-op inside the open transaction (see migration 2); only
            # harmless because waypoints is a child table. Migration 26
            # shows the pattern for a parent table.
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(text("""
                CREATE TABLE waypoints_new (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_id         INTEGER NOT NULL REFERENCES caches(id),
                    prefix           TEXT,
                    wp_type          TEXT,
                    name             TEXT,
                    description      TEXT,
                    comment          TEXT,
                    latitude         REAL,
                    longitude        REAL,
                    parent_gc_code   VARCHAR(16),
                    wp_code          VARCHAR(16),
                    url              VARCHAR(512),
                    wp_date          DATETIME,
                    created_by_user  BOOLEAN NOT NULL DEFAULT 0,
                    wp_flag          BOOLEAN NOT NULL DEFAULT 0
                )
            """))
            conn.execute(text(
                "INSERT INTO waypoints_new "
                "SELECT id, cache_id, prefix, wp_type, name, description, comment, "
                "latitude, longitude, parent_gc_code, wp_code, url, wp_date, "
                "created_by_user, wp_flag FROM waypoints"
            ))
            conn.execute(text("DROP TABLE waypoints"))
            conn.execute(text("ALTER TABLE waypoints_new RENAME TO waypoints"))
            for name, sql in keep_indexes:
                conn.execute(text(sql))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_waypoint_cache_wp_code "
                "ON waypoints (cache_id, wp_code)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_waypoints_cache_id ON waypoints (cache_id)"
            ))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
            logger.info("Migration: updated waypoints unique constraint to (cache_id, wp_code)")

        # ── Migration 23: found_log_count kolonne (issue #552) ───────────────
        logger.debug("[migrations] entering migration 23 (+%.2fs)", time.monotonic() - _mig_t0)
        # Cache.found is a boolean ("have I found this cache at least once"),
        # which undercounts relocatable/multi-visit caches where the same
        # user can legitimately log a found-type entry more than once —
        # geocaching.com and GSAK both count found LOGS in the footer total,
        # not found CACHES. We add the column AND backfill it from existing
        # logs so users don't have to re-import for the count to appear.
        #
        # Backfill matches the same way found_date/FTF are derived elsewhere
        # (see count_own_found_logs() in utils/utils.py): numeric finder_id
        # first, falling back to a case-insensitive username comparison
        # (SQL TRIM only strips leading/trailing whitespace here — the fuller
        # normalize_geocacher_name() whitespace/GSAK-stats-suffix handling
        # only applies at import time, not in this one-off backfill). Only
        # caches already marked found=1 are touched, since that's the only place found_log_count is read (mainwindow.py falls
        # back to counting the cache itself if found_log_count is 0 but
        # found=1 — e.g. no username configured, or the matching log simply
        # isn't present in this database).
        existing_caches_22 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        if "found_log_count" not in existing_caches_22:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN found_log_count "
                "INTEGER NOT NULL DEFAULT 0"
            ))
            conn.commit()

            from opensak.gui.settings import get_settings
            _sett = get_settings()
            gc_finder_id = (_sett.gc_finder_id or "").strip()
            gc_username = (_sett.gc_username or "").strip().lower()

            if gc_finder_id or gc_username:
                result = conn.execute(text("""
                    UPDATE caches
                    SET found_log_count = (
                        SELECT COUNT(*) FROM logs
                        WHERE logs.cache_id = caches.id
                          AND logs.log_type IN ('Found it', 'Attended', 'Webcam Photo Taken')
                          AND (
                              (:gc_finder_id != '' AND logs.finder_id = :gc_finder_id)
                              OR (:gc_username != ''
                                  AND LOWER(TRIM(logs.finder)) = :gc_username)
                          )
                    )
                    WHERE caches.found = 1
                """), {"gc_finder_id": gc_finder_id, "gc_username": gc_username})
                conn.commit()
                logger.info(
                    "Migration: added caches.found_log_count and "
                    f"updated {result.rowcount} caches"
                )
            else:
                logger.info(
                    "Migration: added caches.found_log_count "
                    "(no gc_username/gc_finder_id set — skipping backfill)"
                )

        # ── Migration 24: last_found_date, last_gpx_update, last_four_logs
        # (issue #716) ─────────────────────────────────────────────────────
        logger.debug("[migrations] entering migration 24 (+%.2fs)", time.monotonic() - _mig_t0)
        # Follow-up to #658's GSAK-compatible columns. Unlike those (which
        # just exposed existing data), these 3 are genuinely new derived
        # data, so a fresh column needs a one-off backfill from what's
        # already in the database — new values are populated on every
        # import from here on (see _upsert_cache() in importer/__init__.py
        # and gsak_importer.py).
        existing_caches_23 = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(caches)")).fetchall()
        ]
        added_23 = []

        if "last_found_date" not in existing_caches_23:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN last_found_date DATETIME"
            ))
            logger.debug("[migrations] 24: last_found_date backfill starting")
            _step_t0 = time.monotonic()
            # Most recent "Found it"-type log by ANY finder — same
            # FOUND_LOG_TYPES set used everywhere else in the codebase
            # (see opensak.utils.constants.FOUND_LOG_TYPES).
            conn.execute(text("""
                UPDATE caches
                SET last_found_date = (
                    SELECT MAX(log_date)
                    FROM logs
                    WHERE logs.cache_id = caches.id
                      AND logs.log_type IN ('Found it', 'Attended', 'Webcam Photo Taken')
                )
            """))
            logger.debug(
                "[migrations] 24: last_found_date backfill done (+%.2fs)",
                time.monotonic() - _step_t0,
            )
            added_23.append("last_found_date")

        if "last_gpx_update" not in existing_caches_23:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN last_gpx_update DATETIME"
            ))
            logger.debug("[migrations] 24: last_gpx_update backfill starting")
            _step_t0 = time.monotonic()
            # No historical import-timestamp data exists yet — imported_at
            # (set once, on first creation) is the best available proxy
            # until the next real import sets this properly.
            conn.execute(text(
                "UPDATE caches SET last_gpx_update = imported_at"
            ))
            logger.debug(
                "[migrations] 24: last_gpx_update backfill done (+%.2fs)",
                time.monotonic() - _step_t0,
            )
            added_23.append("last_gpx_update")

        if "last_four_logs" not in existing_caches_23:
            conn.execute(text(
                "ALTER TABLE caches ADD COLUMN last_four_logs TEXT"
            ))
            logger.debug("[migrations] 24: last_four_logs backfill starting")
            _step_t0 = time.monotonic()
            # One line per log ("ISO date<TAB>log type<TAB>finder"), most
            # recent first, matching the format _upsert_cache() writes on
            # import (see importer/__init__.py). The inner derived table's
            # ORDER BY + LIMIT 4 fixes the row order group_concat sees —
            # SQLite doesn't reshuffle rows for an aggregate with no GROUP
            # BY, so this reliably comes out newest-first without needing
            # window functions.
            conn.execute(text("""
                UPDATE caches
                SET last_four_logs = (
                    SELECT group_concat(line, char(10))
                    FROM (
                        SELECT (
                            strftime('%Y-%m-%dT%H:%M:%S', log_date) || char(9) ||
                            log_type || char(9) || COALESCE(finder, '')
                        ) AS line
                        FROM logs
                        WHERE logs.cache_id = caches.id
                          AND log_date IS NOT NULL
                        ORDER BY log_date DESC
                        LIMIT 4
                    )
                )
                WHERE EXISTS (SELECT 1 FROM logs WHERE logs.cache_id = caches.id)
            """))
            logger.debug(
                "[migrations] 24: last_four_logs backfill done (+%.2fs)",
                time.monotonic() - _step_t0,
            )
            added_23.append("last_four_logs")

        if added_23:
            conn.commit()
            logger.info(f"Migration: added caches.{', caches.'.join(added_23)}")

        # ── Migration 25: partial index on caches missing a distance ─────────
        logger.debug("[migrations] entering migration 25 (+%.2fs)", time.monotonic() - _mig_t0)
        # distances_up_to_date() looks for a cache without a distance on
        # every startup and database switch. On an up-to-date database that
        # is a full table scan (~1 s at 180k caches); this index holds only
        # the rows still missing one, so the lookup is instant. Rows leave it
        # as recalculate_distances() fills them, so its upkeep is negligible.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_caches_distance_missing "
            "ON caches (id) WHERE distance IS NULL"
        ))
        conn.commit()

        # ── Migration 26: database defaults for NOT NULL columns (#1033) ─────
        logger.debug("[migrations] entering migration 26 (+%.2fs)", time.monotonic() - _mig_t0)
        # Columns that had a default only in the model need one in the
        # database too, so a plain SQL INSERT only has to list the real data.
        # SQLite cannot change a column's default, so each affected table is
        # rebuilt — only where a default is still missing, which also makes
        # this a no-op on databases created by create_all() from now on.
        _step_t0 = time.monotonic()
        rebuilt = _add_column_defaults(conn, _COLUMN_DEFAULTS_26)
        if rebuilt:
            logger.info(
                "[migrations] 26: rebuilt %s with column defaults (+%.2fs)",
                ", ".join(rebuilt), time.monotonic() - _step_t0,
            )
            logger.info(f"Migration: added database defaults to {', '.join(rebuilt)}")

        # ── Migration 27: GSAK's small columns (#1139, #1028 step 2) ─────────
        logger.debug("[migrations] entering migration 27 (+%.2fs)", time.monotonic() - _mig_t0)
        # Plain ADD COLUMNs: SQLite allows NOT NULL here because each one has
        # a constant default, so no table rebuild is needed. No backfill —
        # the values only exist in GSAK (or come with the next import), and
        # existing corrections keep corrected_source NULL ("unknown").
        added_27 = _add_missing_columns(conn, _NEW_COLUMNS_27)
        if added_27:
            conn.commit()
            logger.info(f"Migration: added {', '.join(added_27)}")

        # ── Migration 28: last_user_update triggers + is_owner (#1140) ───────
        logger.debug("[migrations] entering migration 28 (+%.2fs)", time.monotonic() - _mig_t0)
        # The database stamps caches.last_user_update itself whenever a user
        # field changes, whatever wrote it (GUI, macros, raw SQL). IF NOT
        # EXISTS makes this safe to run again, and also creates them on new
        # databases. NB: a migration that rebuilds caches or user_notes
        # (DROP TABLE) drops their triggers too and must call this again.
        create_user_update_triggers(conn)
        # is_owner from the account in Settings, for existing databases; from
        # now on the GPX import and a change of account keep it current. Safe
        # to run again: it only follows the current account (and does nothing
        # without one, keeping what the GSAK import brought in).
        from opensak.db.ownership import recalculate_is_owner
        from opensak.gui.settings import get_settings
        _sett_28 = get_settings()
        changed_28 = recalculate_is_owner(
            conn,
            getattr(_sett_28, "gc_finder_id", ""),
            getattr(_sett_28, "gc_username", ""),
        )
        if changed_28:
            logger.info(f"Migration: set caches.is_owner on {changed_28} caches")
        conn.commit()

        # ── Stamp the schema version so the next launch skips the probes ─────
        # PRAGMA does not accept bind parameters; SCHEMA_VERSION is a trusted
        # int constant, so inlining it is safe.
        conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
        conn.commit()
        logger.info(
            "migrations: done, now at schema %s (+%.2fs total)",
            SCHEMA_VERSION, time.monotonic() - _mig_t0,
        )

        # ── Compact after migration 26 (#1033) ───────────────────────────────
        # The rebuild left the old tables' pages on the free list, so the
        # file grew by about the size of the rebuilt tables. VACUUM gives
        # that back — after the commit, with foreign keys on again. It only
        # runs when it pays off and the disk has room, and never raises: a
        # database that could not be compacted is still valid, just larger.
        if rebuilt:
            maintenance.vacuum(
                cast(sqlite3.Connection, conn.connection.driver_connection),
                Path(engine.url.database or ""),
                progress=lambda: _report_progress(PROGRESS_COMPACTING),
            )

        # #1048: a table rebuild drops the planner's statistics, and new
        # columns and indexes have none yet. Never raises.
        try:
            maintenance.optimize(cast(sqlite3.Connection, conn.connection.driver_connection))
        except sqlite3.Error as exc:
            logger.warning("migrations: optimize failed: %s", exc)


# Migration 26 (#1033): the defaults models.py has as server_default, frozen
# here so later model changes don't alter what this migration does.
_SQL_NOW_26 = "(strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')"
_COLUMN_DEFAULTS_26: dict[str, dict[str, str]] = {
    "caches": {
        "available": "1", "archived": "0", "premium_only": "0",
        "short_desc_html": "0", "long_desc_html": "0", "found": "0", "dnf": "0",
        "first_to_find": "0", "user_flag": "0", "watch": "0", "locked": "0",
        "log_count": "0", "trackable_count": "0", "found_log_count": "0",
        "waypoint_count": "0", "imported_at": _SQL_NOW_26,
    },
    "user_notes": {"is_corrected": "0", "updated_at": _SQL_NOW_26},
    "logs": {"text_encoded": "0", "logged_by_owner": "0"},
    "attributes": {"is_on": "1"},
    # Migration 22 already rebuilt waypoints with DEFAULT 0 on every
    # database; listed so the result does not depend on that.
    "waypoints": {"created_by_user": "0", "wp_flag": "0"},
}

# Migration 27 (#1139): table -> [(column, SQL definition)], frozen here so
# later model changes don't alter what this migration does.
_NEW_COLUMNS_27: dict[str, list[tuple[str, str]]] = {
    "caches": [
        ("last_user_update", "DATETIME"),
        ("macro_flag", "BOOLEAN NOT NULL DEFAULT 0"),
        ("macro_sort", "TEXT"),
        ("is_owner", "BOOLEAN NOT NULL DEFAULT 0"),
        ("symbol", "VARCHAR(64)"),
    ],
    "user_notes": [
        ("corrected_source", "VARCHAR(16)"),
    ],
}


def _add_missing_columns(conn: Connection, columns_by_table: dict[str, list[tuple[str, str]]]) -> list[str]:
    """
    ADD COLUMN every listed column its table doesn't have yet, so this can
    run again safely (and is a no-op on a database create_all() just made).
    Returns the added columns as "table.column".
    """
    added: list[str] = []
    for table, columns in columns_by_table.items():
        existing = {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table}")'))}
        for column, definition in columns:
            if column not in existing:
                # Table/column names and definitions are trusted constants.
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'))
                added.append(f"{table}.{column}")
    return added


# ── last_user_update triggers (#1140) ────────────────────────────────────────
# Current UTC time in SQLAlchemy's DateTime storage format (as models._SQL_NOW).
_TRIGGER_NOW = "(strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')"
# The user's own fields on caches. Text fields compare with NULL and '' as
# equal, so a dialog that saves an untouched empty field doesn't count.
_USER_FIELDS_TEXT = ("user_data_1", "user_data_2", "user_data_3", "user_data_4")
_USER_FIELDS_VALUE = ("user_flag", "user_sort", "locked")


def _changed(cols_text: tuple[str, ...], cols_value: tuple[str, ...]) -> str:
    parts = [f"IFNULL(NEW.{c}, '') IS NOT IFNULL(OLD.{c}, '')" for c in cols_text]
    parts += [f"NEW.{c} IS NOT OLD.{c}" for c in cols_value]
    return " OR ".join(parts)


_USER_UPDATE_TRIGGERS: dict[str, str] = {
    # A user field really changed, and the statement didn't set
    # last_user_update itself (an import bringing its own date, e.g. GSAK's
    # LastUserDate, wins). Updating last_user_update doesn't fire this again:
    # it is not in the OF list.
    "trg_caches_last_user_update": (
        "AFTER UPDATE OF " + ", ".join(_USER_FIELDS_VALUE + _USER_FIELDS_TEXT)
        + " ON caches FOR EACH ROW WHEN NEW.last_user_update IS OLD.last_user_update AND ("
        + _changed(_USER_FIELDS_TEXT, _USER_FIELDS_VALUE) + ") "
        f"BEGIN UPDATE caches SET last_user_update = {_TRIGGER_NOW} WHERE id = NEW.id; END"
    ),
    # A personal note or a correction was added …
    "trg_user_notes_insert_last_user_update": (
        "AFTER INSERT ON user_notes FOR EACH ROW "
        "WHEN IFNULL(NEW.note, '') <> '' OR NEW.is_corrected "
        f"BEGIN UPDATE caches SET last_user_update = {_TRIGGER_NOW} WHERE id = NEW.cache_id; END"
    ),
    # … or changed. Deleting a note row is not covered: OpenSAK clears a note
    # or a correction by updating the row, and only deletes it along with
    # its cache — where a trigger touching the cache row being deleted would
    # be undefined behaviour in SQLite.
    "trg_user_notes_update_last_user_update": (
        "AFTER UPDATE OF note, corrected_lat, corrected_lon, is_corrected ON user_notes "
        "FOR EACH ROW WHEN "
        + _changed(("note",), ("corrected_lat", "corrected_lon", "is_corrected")) + " "
        f"BEGIN UPDATE caches SET last_user_update = {_TRIGGER_NOW} WHERE id = NEW.cache_id; END"
    ),
}

# Their names; opensak.sql_write() lets them write last_user_update, which a
# macro cannot.
USER_UPDATE_TRIGGER_NAMES = frozenset(_USER_UPDATE_TRIGGERS)


def create_user_update_triggers(conn: Connection) -> None:
    """Create the last_user_update triggers (#1140) where they are missing."""
    for name, body in _USER_UPDATE_TRIGGERS.items():
        conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS {name} {body}"))


# A column type as SQLAlchemy writes it: BOOLEAN, VARCHAR(16), NUMERIC(10, 2).
_SQL_TYPE = r"\w+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?"


def _add_column_defaults(conn: Connection, defaults_by_table: dict[str, dict[str, str]]) -> list[str]:
    """
    Give existing columns a database default by rebuilding their tables.

    Only columns that exist and have no default yet are changed, so this can
    run again safely. Returns the names of the tables it rebuilt.
    """
    plans: dict[str, dict[str, str]] = {}
    for table, defaults in defaults_by_table.items():
        info = {r[1]: r for r in conn.execute(text(f'PRAGMA table_info("{table}")'))}
        missing = {c: d for c, d in defaults.items() if c in info and info[c][4] is None}
        if missing:
            plans[table] = missing
    if not plans:
        return []

    # caches is the parent of every other table, so DROP TABLE caches fails
    # while foreign keys are on. PRAGMA foreign_keys does nothing inside an
    # open transaction: commit first and check that it took effect.
    conn.commit()
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    if conn.execute(text("PRAGMA foreign_keys")).scalar() != 0:
        raise RuntimeError("migration 26: could not switch off foreign keys")
    try:
        # Rows that already broke a foreign key are not this migration's
        # business; it only must not add any.
        fk_before = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
        # One transaction for all tables, so a failure leaves the old schema.
        conn.exec_driver_sql("BEGIN")
        # Triggers (#1140) go first and come back afterwards: DROP TABLE takes
        # a table's own triggers with it, and the rename back to the old name
        # fails while another table's trigger refers to the dropped table.
        triggers = conn.execute(text(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND sql IS NOT NULL"
        )).fetchall()
        for name, _sql in triggers:
            conn.exec_driver_sql(f'DROP TRIGGER "{name}"')
        for table, missing in plans.items():
            _rebuild_with_defaults(conn, table, missing)
        for _name, sql in triggers:
            conn.exec_driver_sql(sql)
        fk_new = set(conn.execute(text("PRAGMA foreign_key_check")).fetchall()) - set(fk_before)
        if fk_new:
            raise RuntimeError(f"migration 26: foreign key check failed: {sorted(fk_new)[:5]}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(text("PRAGMA foreign_keys=ON"))
    if fk_before:
        logger.warning(
            "[migrations] 26: database already had %s foreign key violations",
            len(fk_before),
        )
    return list(plans)


def _rebuild_with_defaults(conn: Connection, table: str, missing: dict[str, str]) -> None:
    """
    Rebuild *table* with ` DEFAULT x` added to the columns in *missing*.

    The new table is built from the table's own CREATE TABLE text, so its
    constraints and any columns older migrations appended stay as they are.
    Column order does not change, so SELECT * copies the data.
    """
    create_sql = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"), {"t": table}
    ).scalar()
    if create_sql is None:
        raise RuntimeError(f"migration 26: no CREATE TABLE for {table}")
    for col, default in missing.items():
        # The column's definition right after "(" or ",": name (quoted or
        # not), type and an optional NOT NULL; the default goes after that,
        # before the next column or constraint.
        create_sql, n = re.subn(
            rf'([(,]\s*"?{re.escape(col)}"?\s+{_SQL_TYPE})(\s+NOT\s+NULL)?'
            r'(?=\s*[,)]|\s+(?:PRIMARY|UNIQUE|CHECK|REFERENCES|COLLATE|CONSTRAINT)\b)',
            lambda m: f"{m.group(1)}{m.group(2) or ''} DEFAULT {default}",
            create_sql, count=1, flags=re.IGNORECASE,
        )
        if n != 1:
            raise RuntimeError(f"migration 26: cannot find {table}.{col} in its CREATE TABLE")
    # A rebuilt table's name is stored quoted ("caches") after the RENAME.
    create_sql, n = re.subn(
        rf'^(\s*CREATE\s+TABLE\s+)"?{re.escape(table)}"?(?=\s*\()',
        lambda m: f"{m.group(1)}{table}_new", create_sql, count=1, flags=re.IGNORECASE,
    )
    if n != 1:
        raise RuntimeError(f"migration 26: unexpected CREATE TABLE for {table}")

    # Indexes go with the dropped table; autoindexes of UNIQUE constraints
    # (sql IS NULL) come back with the CREATE TABLE text.
    indexes = conn.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=:t "
        "AND sql IS NOT NULL"
    ), {"t": table}).scalars().all()

    # A TEMP view on the table (caches_all, custom fields) makes SQLite
    # refuse the RENAME below; it is made again on the next checkout.
    custom_fields.drop_view(conn)

    # Raw SQL from sqlite_master: exec_driver_sql, so nothing in it is taken
    # for a bind parameter.
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}_new")
    conn.exec_driver_sql(create_sql)
    conn.exec_driver_sql(f"INSERT INTO {table}_new SELECT * FROM {table}")
    conn.exec_driver_sql(f"DROP TABLE {table}")
    conn.exec_driver_sql(f"ALTER TABLE {table}_new RENAME TO {table}")
    for index_sql in indexes:
        conn.exec_driver_sql(index_sql)


def optimize_database(db_path: Path | None = None) -> None:
    """
    Refresh the query planner's statistics (#1048) of *db_path* (default:
    the active database) and of its companion database. Cheap when nothing
    changed much; the first time it creates them from a sample of each index
    (about 0.2 s, at any database size).

    Called when a database is opened, closed or switched, and after imports,
    deletes and moves. Runs on its own connection, waits at most a few
    seconds for a writer, and never raises: without fresh statistics the
    queries are only slower.
    """
    try:
        if db_path is None:
            if _engine is None:
                return
            db_path = _engine_path(_engine)
        db_path = Path(db_path)
        if not db_path.is_file():
            return
        maintenance.optimize_file(db_path, timeout=_OPTIMIZE_TIMEOUT)
        if flags.companion_db and companion.exists(db_path):
            companion.optimize(db_path, timeout=_OPTIMIZE_TIMEOUT)
    except Exception:
        logger.warning("optimize %s failed", db_path, exc_info=True)


# Seconds optimize_database() waits for another connection's write.
_OPTIMIZE_TIMEOUT = 5


def _engine_path(engine: Engine) -> Path:
    return Path(engine.url.database or "")


def release_connections(db_path: Path) -> bool:
    """
    Close the pooled connections of the active engine if it points at
    *db_path* — and with them the companion attached to them — so a
    maintenance step can have the files to itself (VACUUM, moving a damaged
    companion aside, #1048). Unlike dispose_engine() the engine stays: the
    next session simply opens a new connection, so nothing on the GUI
    thread finds the database closed meanwhile. True if it was the active
    database.
    """
    if _engine is None:
        return False
    try:
        if _engine_path(_engine).resolve() != Path(db_path).resolve():
            return False
    except OSError:
        return False
    _engine.dispose()
    return True


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
        # Sammenlign stier — brug url.database (den ukodede sti), ikke
        # str(url): SQLAlchemy 2.1+ percent-encoder ':' og backslash (Windows-
        # stier) samt ikke-ASCII-tegn (æ/ø/å) i str(url), så stien aldrig
        # matchede og dispose stille blev sprunget over.
        engine_path = Path(_engine.url.database or "").resolve()
        try:
            target_path = db_path.resolve()
        except OSError:
            target_path = db_path

        if engine_path != target_path:
            return  # Denne engine peger ikke på den ønskede database

    # #1048: the database is closed (switch, exit, move, rename …).
    optimize_database(_engine_path(_engine))
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


@contextmanager
def session_for(db_path: Path) -> Generator[Session, None, None]:
    """
    Like get_session(), but on a private engine for *db_path* — the active
    database (init_db()'s globals) is left untouched.

    Use this for background jobs that work on a database other than the
    active one (import into another DB, move/copy caches, PQ e-mail import).
    Swapping the globals with init_db() from a worker thread instead would
    let anything on the GUI thread that calls get_session() meanwhile
    (timers, queued signals) read from or write to the wrong database.

    The schema is brought up to date first, exactly as init_db() would, and
    the engine is disposed when the block exits.

    Example
    -------
        with session_for(other_db) as session:
            session.add(some_object)
    """
    engine = _open_engine(db_path)
    try:
        session: Session = _session_factory(engine)()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    finally:
        engine.dispose()


def needs_migration(db_path: Path) -> bool:
    """True if opening *db_path* would migrate its schema (an existing
    database from an older OpenSAK build)."""
    return pending_schema_version(Path(db_path), SCHEMA_VERSION) is not None


def migrate_databases(paths: Iterable[Path]) -> list[tuple[Path, Exception]]:
    """
    Bring each database in *paths* up to SCHEMA_VERSION — with the usual
    pre-migration backup (#549) — on a private engine, so the active
    database is left untouched ("Apply to all databases").

    Databases already up to date are skipped. A failure on one database is
    logged and does not stop the others; returns the (path, error) pairs
    of those that failed.
    """
    failed: list[tuple[Path, Exception]] = []
    for db_path in paths:
        db_path = Path(db_path)
        if not needs_migration(db_path):
            continue
        try:
            _open_engine(db_path).dispose()
        except Exception as e:
            logger.exception("migrate_databases: %s failed", db_path)
            failed.append((db_path, e))
    return failed


def session_on(db_path: Path | None):
    """get_session() when *db_path* is None or the database the active
    engine already points at, otherwise session_for(db_path) — so a helper
    given another database's path never reads or writes the active one."""
    if db_path is None or (
        _engine is not None and _engine.url.database
        and Path(_engine.url.database).resolve() == Path(db_path).resolve()
    ):
        return get_session()
    return session_for(db_path)


def make_session(engine: Engine | Connection | None = None):
    """
    Return a bare Session — caller handles commit/rollback/close.

    Bound to the active database, or to *engine* when given (e.g. the bind
    of a session_for() session, so a helper opens its own session on the
    same database as the session it was handed).
    """
    if engine is not None:
        return _session_factory(engine)()
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() first.")
    return _SessionLocal()


# Cache ids per reload query in reload_caches_full().
RELOAD_CHUNK_SIZE = 500


def reload_caches_full(caches: list) -> list:
    """Reload *caches* with everything an export needs eagerly loaded.

    Table-model caches come from apply_filters_auto() — either real Cache
    ORM objects or, since #627 beta.9-11, LightweightCache rows (which defer
    the text blobs and never carry logs/waypoints/attributes at all).
    Either way, an export worker that reads those attributes needs the full
    picture: a real Cache would raise DetachedInstanceError once its
    session closes, and a LightweightCache raises AttributeError by design
    (see its docstring) for exactly these fields. Reloading here returns
    fully populated, detached-safe Cache objects (logs, waypoints,
    attributes, user_note and the text blobs) in the original order,
    regardless of which of the two types the caller is currently holding.

    Objects without a matching DB row (e.g. test stand-ins) pass through
    unchanged.

    This holds every fully loaded cache in memory at once; exports of a
    whole large database use iter_caches_full() instead (#1075).
    """
    return list(iter_caches_full(caches))


def iter_caches_full(caches: list) -> Iterator:
    """Yield *caches* reloaded as by reload_caches_full(), in the original
    order, reloading RELOAD_CHUNK_SIZE of them at a time.

    #1075: an export of 100,000+ caches ran out of memory holding all of
    them (with their logs and descriptions) at once. As long as the caller
    does not keep the yielded caches, only one chunk is in memory at a time.
    """
    from opensak.db.models import Cache
    from opensak.filters.engine import LightweightCache
    from sqlalchemy.orm import joinedload, selectinload, undefer

    # Both Cache (full ORM) and LightweightCache (#627 beta.9-11) carry a
    # real .id — anything else (e.g. SimpleNamespace stand-ins in tests)
    # passes through unchanged.
    reloadable = (Cache, LightweightCache)

    # #1041: the IN query is chunked to stay under SQLite's bound-parameter
    # limit — exporting a whole large database failed with "too many SQL
    # variables".
    for start in range(0, len(caches), RELOAD_CHUNK_SIZE):
        chunk = caches[start:start + RELOAD_CHUNK_SIZE]
        ids = [c.id for c in chunk if isinstance(c, reloadable) and c.id is not None]
        by_id: dict[int, Cache] = {}
        if ids:
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
                by_id.update((c.id, c) for c in rows)
        for c in chunk:
            yield by_id.get(c.id, c) if isinstance(c, reloadable) else c


# ── Distance recalculation ────────────────────────────────────────────────────

def recalculate_distances(lat: float, lon: float, db_path: Path | None = None) -> int:
    """Recompute distance and bearing for every cache and persist to the DB.

    Called once whenever the active centre point changes (not on every table
    refresh). Uses distance_km_batch() which dispatches to Haversine or
    Vincenty depending on the user's distance_method setting.

    *db_path* is the database to work on when that may not be the active
    one — e.g. GsakImportWorker, which imports into each target database in
    turn. Its caches are then read and written through a private session
    (see session_for()) and the centre used is stored in that file's own
    settings (see distances_up_to_date()); without it both go to the active
    database.

    Returns the number of caches updated.
    """
    from opensak.filters.engine import distance_km_batch

    logger.info("recalculate_distances: starting for center (%s, %s)", lat, lon)
    _t0 = time.monotonic()

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

    with session_on(db_path) as session:
        rows = session.execute(
            text("SELECT id, latitude, longitude FROM caches WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
        ).fetchall()

        if not rows:
            logger.info("recalculate_distances: no rows with coordinates, skipping")
            return 0

        logger.info(
            "recalculate_distances: fetched %s rows (+%.2fs)",
            len(rows), time.monotonic() - _t0,
        )

        ids  = [r[0] for r in rows]
        lats = [r[1] for r in rows]
        lons = [r[2] for r in rows]

        dists = distance_km_batch(lat, lon, lats, lons)
        bears = _bearing_batch(lat, lon, lats, lons)
        logger.info(
            "recalculate_distances: distance/bearing computed (+%.2fs)",
            time.monotonic() - _t0,
        )

        session.execute(
            text("UPDATE caches SET distance = :d, bearing = :b WHERE id = :id"),
            [{"d": float(dists[i]), "b": float(bears[i]), "id": ids[i]} for i in range(len(ids))],
        )
        logger.info(
            "recalculate_distances: UPDATE done (+%.2fs)", time.monotonic() - _t0,
        )

    # Persist the center + method this recalculation used, so
    # distances_up_to_date() can skip a redundant full recalc on next
    # startup if nothing has changed (issue #579).
    from opensak.gui.settings import get_settings
    s = get_settings()
    if db_path is not None:
        from opensak.db import db_settings
        db_settings.write_file(db_path, {
            "dist_calc_lat": lat,
            "dist_calc_lon": lon,
            "dist_calc_method": s.distance_method,
        })
    else:
        s.dist_calc_lat = lat
        s.dist_calc_lon = lon
        s.dist_calc_method = s.distance_method

    logger.info(
        "recalculate_distances: done, %s caches updated (+%.2fs total)",
        len(ids), time.monotonic() - _t0,
    )
    return len(ids)


# Tolerance for comparing a freshly-calculated centre point / distance to a
# previously persisted one. Anything above this is treated as "changed" and
# triggers a full recalculation.
_COORD_EPSILON = 1e-9
_DISTANCE_EPSILON_KM = 0.01  # 10 m — generous enough to absorb float rounding


def distances_up_to_date(lat: float, lon: float, db_path: Path | None = None) -> bool:
    """Check whether the persisted Cache.distance/bearing values are still
    valid for the given centre point, so a full recalculate_distances() call
    can be skipped on startup (issue #579).

    Two checks must both pass:
    1. The centre point and distance_method match what was used for the last
       recalculation (persisted in settings, see recalculate_distances()).
    2. A cheap spot-check: recompute the distance for a single cache and
       compare it against the persisted value, to catch a database that was
       modified outside this OpenSAK install (e.g. synced from another
       machine with a different home point, or edited by another tool)
       without a matching recalculation ever having run here.
    3. No cache with coordinates is missing its distance — e.g. caches a
       merge import added after the last recalculation, which the single
       spot-check row can't see.

    *db_path*: as for recalculate_distances().

    Returns True if it's safe to skip the full recalculation.
    """
    from opensak.gui.settings import get_settings
    from opensak.filters.engine import distance_km

    # Deterministic spot-check row: lowest id with coordinates, regardless of
    # whether it currently has a persisted distance. Checked first (and
    # unconditionally) so an empty/coordinate-less database is always
    # considered up to date — there is nothing to recalculate either way.
    with session_on(db_path) as session:
        row = session.execute(
            text(
                "SELECT latitude, longitude, distance FROM caches "
                "WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
                "ORDER BY id LIMIT 1"
            )
        ).fetchone()

    if row is None:
        return True

    s = get_settings()
    calc_lat: float | None
    calc_lon: float | None
    calc_method: str | None
    if db_path is not None:
        from opensak.db import db_settings
        values = {
            k: db_settings.peek_value(db_path, k)
            for k in ("dist_calc_lat", "dist_calc_lon", "dist_calc_method")
        }
        try:
            calc_lat = float(values["dist_calc_lat"])
            calc_lon = float(values["dist_calc_lon"])
        except (TypeError, ValueError):
            calc_lat = calc_lon = None
        calc_method = values["dist_calc_method"]
    else:
        calc_lat = s.dist_calc_lat
        calc_lon = s.dist_calc_lon
        calc_method = s.dist_calc_method

    if calc_lat is None or calc_lon is None or calc_method is None:
        return False
    if calc_method != s.distance_method:
        return False
    if abs(calc_lat - lat) > _COORD_EPSILON or abs(calc_lon - lon) > _COORD_EPSILON:
        return False

    row_lat, row_lon, stored_distance = row
    if stored_distance is None:
        return False
    fresh_distance = distance_km(lat, lon, row_lat, row_lon)
    if abs(fresh_distance - stored_distance) > _DISTANCE_EPSILON_KM:
        return False

    # Served by the partial index ix_caches_distance_missing (migration 25),
    # so this stays cheap on large databases.
    with session_on(db_path) as session:
        missing = session.execute(
            text(
                "SELECT 1 FROM caches WHERE distance IS NULL "
                "AND latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 1"
            )
        ).fetchone()
    return missing is None


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
