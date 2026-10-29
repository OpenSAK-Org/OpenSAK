"""
src/opensak/importer/gsak_import_service.py — GSAK import without questions.

What the GSAK import dialog does, as one call that never asks anything: list
the GSAK databases a source holds, pick the OpenSAK database each one goes
into, decide up front what happens to a target that exists already, import,
bring the distances up to date, and migrate GSAK's user locations and saved
filters (gsak.db3). It returns a report of what was done instead.

Used by opensak.import_gsak() in Lua macros, and meant as the shared import
service of the headless command-line import (#937). The dialog shares the
target and distance helpers below. Qt-free: nothing here needs a
QApplication, and nothing here may open a dialog.

Sources (list_gsak_source):

* a GSAK backup .zip — every ``<name>/sqlite.db3`` in it, plus gsak.db3
* a GSAK folder — an install / data location, its ``data`` folder, or one
  database's folder (see list_gsak_folder)
* a single cache database file (``sqlite.db3`` or a renamed copy) — just
  that database; the gsak.db3 of the install it belongs to is not used
* gsak.db3 itself — only the user locations and saved filters
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, Optional

from opensak.importer.gsak_importer import (
    GSAK_SETTINGS_DB_NAME,
    GsakBackupContents,
    GsakBackupDatabase,
    GsakImportResult,
    extract_gsak_member,
    import_gsak_db,
    list_gsak_backup,
    list_gsak_folder,
)
from opensak.importer.gsak_replace import import_gsak_db_replacing, is_empty

if TYPE_CHECKING:
    from opensak.importer.gsak_filter_importer import GsakFilterImportResult
    from opensak.importer.gsak_location_importer import LocationImportResult

logger = logging.getLogger(__name__)

# What a source is (list_gsak_source)
SOURCE_BACKUP = "backup"
SOURCE_FOLDER = "folder"
SOURCE_FILE = "file"
SOURCE_SETTINGS = "settings"

# What happens to a target OpenSAK database that exists already — the three
# buttons of the dialog's question, "skip" being its default button.
IF_EXISTS = ("skip", "merge", "overwrite")
# What happens to a user location / saved filter whose name exists already
SETTINGS_IF_EXISTS = ("skip", "overwrite")


def settings_mode(if_exists: str) -> str:
    """The SETTINGS_IF_EXISTS value that goes with a database *if_exists*:
    a location or filter is replaced by name, so merging and overwriting
    are the same thing for it."""
    return "skip" if if_exists == "skip" else "overwrite"

# What a proposed OpenSAK database name resolves to (see target_state)
TARGET_NEW = "new"            # nothing there yet — will be created
TARGET_EXISTING = "existing"  # a database in OpenSAK's list
TARGET_ORPHAN = "orphan"      # a valid database file that isn't in the list
TARGET_BLOCKED = "blocked"    # some other file sits on the path the name maps to

# DatabaseReport.action
ACTION_CREATED = "created"          # new OpenSAK database
ACTION_MERGED = "merged"            # into an existing one
ACTION_OVERWRITTEN = "overwritten"  # existing one emptied first
ACTION_SKIPPED = "skipped"
ACTION_FAILED = "failed"
ACTION_CANCELLED = "cancelled"


class GsakImportError(Exception):
    """The import cannot start (missing source, nothing to import, unknown
    database name, bad option). Nothing has been changed."""


class _Cancelled(Exception):
    pass


# ── Target databases (shared with the dialog) ───────────────────────────────

def find_existing(name: str):
    """The known OpenSAK database called *name* (case-insensitive — the
    file name is derived from it, and Windows file names ignore case)."""
    from opensak.db.manager import get_db_manager

    wanted = name.lower()
    for db in get_db_manager().databases:
        if db.name.lower() == wanted:
            return db
    return None


def target_state(name: str):
    """Resolve an OpenSAK database name: ``(TARGET_*, DatabaseInfo | Path)``.

    A name that isn't in the database list can still collide with a file
    on disk (new_database() derives the path from the name and refuses
    to reuse a file) — e.g. a stale ``Default.db`` left behind. A valid
    database file is offered as an existing target; anything else blocks
    the name.
    """
    from opensak.db.manager import get_db_manager

    existing = find_existing(name)
    if existing is not None:
        return TARGET_EXISTING, existing
    manager = get_db_manager()
    path = manager.default_path_for(name)
    for db in manager.databases:
        if db.path == path:
            return TARGET_EXISTING, db
    if path.exists():
        if manager.is_valid_database_file(path):
            return TARGET_ORPHAN, path
        return TARGET_BLOCKED, path
    return TARGET_NEW, path


def _database_at(path: Path):
    """The listed database at *path*, or an unlisted one named after the
    file."""
    from opensak.db.manager import DatabaseInfo, get_db_manager

    for db in get_db_manager().databases:
        if db.path == path:
            return db
    return DatabaseInfo(path.stem, path)


def free_name(name: str) -> str:
    """*name*, or ``name-2``, ``name-3``, … when a stray file blocks it."""
    candidate, n = name, 2
    while target_state(candidate)[0] == TARGET_BLOCKED:
        candidate = f"{name}-{n}"
        n += 1
    return candidate


# ── Distances after an import (shared with the dialog's worker) ─────────────

def update_distances(db_path: Optional[Path]) -> None:
    """Bring the imported database's distances up to date, so neither the
    refresh after the import nor a later switch to that database has to
    recalculate them on the GUI thread (both still check, as a safety net).
    Works on *db_path* through its own session when that isn't the active
    database, and its home point and the centre used are read from and
    stored in that file, not in the active database's settings. A failure
    here only means that check recalculates later — the import itself
    succeeded.
    """
    if db_path is None:
        return
    try:
        from opensak.db.database import distances_up_to_date, recalculate_distances
        from opensak.gui.settings import get_settings
        lat, lon = get_settings().home_for_db_file(db_path)
        if lat and lon and not distances_up_to_date(lat, lon, db_path=db_path):
            recalculate_distances(lat, lon, db_path=db_path)
    except Exception:
        logger.warning("GSAK import: could not update distances for %s",
                       db_path, exc_info=True)


def invalidate_distances(db_path: Optional[Path]) -> None:
    """Forget the centre *db_path*'s distances were calculated for, so
    distances_up_to_date() reports them stale. The target's last job
    then recalculates; should it never run (skipped, failed, cancelled),
    the check after the import or on switching to that database does.
    """
    if db_path is None:
        return
    try:
        from opensak.db import db_settings
        db_settings.write_file(db_path, {
            "dist_calc_lat": None,
            "dist_calc_lon": None,
            "dist_calc_method": None,
        })
    except Exception:
        logger.warning("GSAK import: could not invalidate distances for %s",
                       db_path, exc_info=True)


# ── Sources ──────────────────────────────────────────────────────────────────

def list_gsak_source(path: Path) -> tuple[str, GsakBackupContents]:
    """``(SOURCE_*, contents)`` of a backup .zip, GSAK folder, cache
    database or gsak.db3. Raises GsakImportError when *path* is missing,
    unreadable or holds nothing to import."""
    path = Path(path)
    if not path.exists():
        raise GsakImportError(f"{path} does not exist")
    try:
        if path.is_dir():
            kind, contents = SOURCE_FOLDER, list_gsak_folder(path)
        else:
            contents = list_gsak_backup(path)
            if contents.is_zip:
                kind = SOURCE_BACKUP
            elif contents.databases:
                kind = SOURCE_FILE
                # "Just this database": not the filters / locations of the
                # GSAK install the file happens to sit in.
                contents.settings_path = None
            else:
                kind = SOURCE_SETTINGS
    except GsakImportError:
        raise
    except Exception as exc:   # not a zip, no access, …
        raise GsakImportError(f"cannot read {path}: {exc}") from None
    if not contents.databases and not contents.has_settings_db:
        raise GsakImportError(f"no GSAK database found in {path}")
    return kind, contents


# ── Options and report ──────────────────────────────────────────────────────

@dataclass
class GsakImportOptions:
    """What to import, and what to do with what exists already."""

    # GSAK database names to import (case-insensitive); None = all of them
    databases: Optional[list[str]] = None
    # GSAK database names to leave out
    exclude: list[str] = field(default_factory=list)
    # One OpenSAK database for all of them; None = each into the OpenSAK
    # database named after it
    target: Optional[str] = None
    # GSAK name → OpenSAK database, ahead of *target*
    targets: dict[str, str] = field(default_factory=dict)
    # One existing database file for all of them, ahead of *target* and
    # *targets* — also one that isn't in OpenSAK's list (it is not added)
    target_path: Optional[Path] = None
    # An existing target database: one of IF_EXISTS
    if_exists: str = "skip"
    # Create a missing target database (False: that database fails)
    create: bool = True
    # gsak.db3: None = not imported, else one of SETTINGS_IF_EXISTS
    filters: Optional[str] = None
    locations: Optional[str] = None


@dataclass
class DatabaseReport:
    """What happened to one GSAK database."""

    gsak_name: str
    target: str
    action: str = ACTION_SKIPPED
    reason: str = ""                 # why skipped / failed / cancelled
    result: Optional[GsakImportResult] = None
    cleared: int = 0                 # caches removed before an overwrite
    target_path: Optional[Path] = None
    active: bool = False             # the target is the active database


@dataclass
class GsakImportReport:
    source: Path
    kind: str
    databases: list[DatabaseReport] = field(default_factory=list)
    # OpenSAK databases created or added to the list by this run
    new_databases: list[str] = field(default_factory=list)
    filters: Optional[GsakFilterImportResult] = None
    filters_error: str = ""
    locations: Optional[LocationImportResult] = None
    locations_invalid: int = 0
    locations_error: str = ""
    notes: list[str] = field(default_factory=list)
    cancelled: bool = False

    def total(self, attr: str) -> int:
        return sum(getattr(d.result, attr) for d in self.databases if d.result)

    @property
    def errors(self) -> int:
        """Caches that failed, databases that failed, failed settings imports."""
        count = sum(len(d.result.errors) for d in self.databases if d.result)
        count += sum(1 for d in self.databases if d.action == ACTION_FAILED)
        if self.filters is not None:
            count += self.filters.failed
        return count + bool(self.filters_error) + bool(self.locations_error)

    @property
    def ok(self) -> bool:
        return not self.cancelled and self.errors == 0

    def summary_lines(self) -> list[str]:
        lines = [f"GSAK import from {self.source} ({self.kind})"]
        for d in self.databases:
            head = f"  {d.gsak_name} → {d.target}: {d.action}"
            if d.reason:
                head += f" ({d.reason})"
            lines.append(head)
            if d.cleared:
                lines.append(f"    {d.cleared} caches removed first")
            r = d.result
            if r is not None:
                lines.append(
                    f"    {r.created} new, {r.updated} updated, {r.skipped} skipped, "
                    f"{r.waypoints} waypoints, {r.logs} logs, {len(r.errors)} errors"
                )
                cf = r.custom_fields
                if cf is not None and cf.fields:
                    lines.append(f"    {cf.fields} custom fields ({len(cf.created)} new, "
                                 f"{len(cf.global_fields)} global), values for {cf.values} caches")
                if r.note_images_replaced:
                    lines.append(f"    {r.note_images_replaced} embedded note images "
                                 "replaced by placeholders")
        if self.locations is not None:
            loc = self.locations
            lines.append(f"  User locations: {len(loc.added)} added, {len(loc.updated)} "
                         f"updated, {len(loc.skipped)} skipped, "
                         f"{self.locations_invalid} invalid")
        elif self.locations_error:
            lines.append(f"  User locations failed: {self.locations_error}")
        if self.filters is not None:
            f = self.filters
            lines.append(f"  Saved filters: {f.written} imported, {f.skipped} skipped, "
                         f"{f.failed} failed")
        elif self.filters_error:
            lines.append(f"  Saved filters failed: {self.filters_error}")
        lines.extend(f"  Note: {n}" for n in self.notes)
        if self.cancelled:
            lines.append("  Cancelled")
        return lines


# ── Planning ────────────────────────────────────────────────────────────────

@dataclass
class _Job:
    entry: GsakBackupDatabase
    report: DatabaseReport
    create: bool = False             # target to be created first
    register: bool = False           # orphan file: add it to the list first
    replace: bool = False            # empty the target first
    db3_path: Optional[Path] = None
    unpacked: bool = False
    started: bool = False            # the import itself has begun


def _by_name(names: list[str], available: Mapping[str, GsakBackupDatabase],
             what: str) -> list[str]:
    """*names* as the GSAK database names they match (case-insensitive)."""
    found: list[str] = []
    unknown: list[str] = []
    for name in names:
        key = name.lower()
        (found if key in available else unknown).append(key)
    if unknown:
        listed = ", ".join(sorted(e.name for e in available.values())) or "none"
        raise GsakImportError(f"{what}: no GSAK database named "
                              f"{', '.join(repr(n) for n in unknown)} (found: {listed})")
    return found


def _plan(contents: GsakBackupContents, opts: GsakImportOptions) -> list[_Job]:
    available = {e.name.lower(): e for e in contents.databases}
    if opts.databases is not None:
        wanted = set(_by_name(opts.databases, available, "databases"))
    else:
        wanted = set(available)
    wanted -= {n.lower() for n in opts.exclude}
    targets = {k.lower(): v for k, v in opts.targets.items()}
    _by_name(list(opts.targets), available, "target")

    jobs: list[_Job] = []
    replaced: set[str] = set()
    created: set[str] = set()
    for entry in contents.databases:
        if entry.name.lower() not in wanted:
            continue
        if opts.target_path is not None:
            state, where = TARGET_EXISTING, _database_at(opts.target_path)
            name = where.name
        else:
            name = (targets.get(entry.name.lower()) or opts.target or "").strip()
            if not name:
                name = free_name(entry.name)
            state, where = target_state(name)
        rep = DatabaseReport(entry.name, name)
        job = _Job(entry, rep)
        jobs.append(job)
        if name.lower() in created:
            # An earlier job of this run creates it: this one adds to it.
            rep.action = ACTION_MERGED
            continue
        if state == TARGET_BLOCKED:
            rep.action, rep.reason = ACTION_FAILED, f"another file is in the way: {where}"
        elif state == TARGET_NEW:
            if opts.create:
                job.create = True
                created.add(name.lower())
                rep.action = ACTION_CREATED
            else:
                rep.action, rep.reason = ACTION_FAILED, "no OpenSAK database with that name"
        else:
            if state == TARGET_EXISTING:
                rep.target, rep.target_path = where.name, where.path
            else:
                rep.target_path, job.register = where, True
            if opts.if_exists == "skip":
                rep.action, rep.reason = ACTION_SKIPPED, "the OpenSAK database exists already"
            elif opts.if_exists == "overwrite":
                rep.action = ACTION_OVERWRITTEN
                # Several GSAK databases may go into the same target: only
                # the first one empties it.
                job.replace = rep.target.lower() not in replaced
                replaced.add(rep.target.lower())
                if not job.replace:
                    rep.action = ACTION_MERGED
            else:
                rep.action = ACTION_MERGED
    return jobs


# ── Running ─────────────────────────────────────────────────────────────────

class _SettingsDb:
    """gsak.db3 of a run, unpacked (or checked) once when first needed: the
    databases need it for their global custom fields, the settings step for
    locations and filters."""

    def __init__(self, contents: GsakBackupContents, temp_dir: Optional[Path],
                 check_file: Optional[Callable[[Path], None]]):
        self._contents = contents
        self._temp_dir = temp_dir
        self._check_file = check_file
        self._path: Optional[Path] = None

    @property
    def available(self) -> bool:
        return self._contents.has_settings_db

    def path(self, progress: Callable) -> Path:
        """The file on disk. Raises if it can't be unpacked or read."""
        if self._path is None:
            if self._contents.settings_member is not None:
                assert self._temp_dir is not None
                progress(f"{GSAK_SETTINGS_DB_NAME}: unpacking")
                self._path = extract_gsak_member(self._contents.source,
                                                 self._contents.settings_member,
                                                 self._temp_dir / "settings")
            else:
                assert self._contents.settings_path is not None
                if self._check_file is not None:
                    self._check_file(self._contents.settings_path)
                self._path = self._contents.settings_path
        return self._path


def run_gsak_import(
    source: Path,
    options: Optional[GsakImportOptions] = None,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    check_file: Optional[Callable[[Path], None]] = None,
) -> GsakImportReport:
    """Import *source* as *options* say, without asking anything.

    *progress_cb(text, done, total)* reports progress (total 0 = no known
    end); *cancel_cb()* returning True stops the run after the current
    batch of caches. *check_file(path)* is called before any file on disk
    is read and may raise to refuse it (the macro folder permissions); a
    refused database fails, the others go on.

    Raises GsakImportError before anything is changed when the source or the
    options are unusable. Everything after that ends up in the report:
    one failing database is reported and the run goes on with the next.
    """
    opts = options or GsakImportOptions()
    if opts.if_exists not in IF_EXISTS:
        raise GsakImportError(f"if_exists must be one of {', '.join(IF_EXISTS)}")
    for key in ("filters", "locations"):
        value = getattr(opts, key)
        if value is not None and value not in SETTINGS_IF_EXISTS:
            raise GsakImportError(f"{key} must be one of {', '.join(SETTINGS_IF_EXISTS)}")

    source = Path(source)
    kind, contents = list_gsak_source(source)
    report = GsakImportReport(source, kind)
    if kind == SOURCE_SETTINGS and opts.filters is None and opts.locations is None:
        # gsak.db3 alone offers nothing else (the dialog ticks both then).
        mode = settings_mode(opts.if_exists)
        opts = dataclasses.replace(opts, filters=mode, locations=mode)
    jobs = _plan(contents, opts)
    report.databases = [j.report for j in jobs]

    def progress(text: str, done: int = 0, total: int = 0) -> None:
        if cancel_cb is not None and cancel_cb():
            raise _Cancelled()
        if progress_cb is not None:
            progress_cb(text, done, total)

    temp_dir: Optional[Path] = None
    try:
        if contents.is_zip:
            temp_dir = Path(tempfile.mkdtemp(prefix="gsak_import_"))
        settings = _SettingsDb(contents, temp_dir, check_file)
        runnable = [j for j in jobs if j.report.action not in (ACTION_SKIPPED, ACTION_FAILED)]
        for index, job in enumerate(runnable):
            if report.cancelled:
                job.report.action, job.report.reason = ACTION_SKIPPED, "cancelled"
                continue
            later = runnable[index + 1:]
            try:
                _run_job(job, index, contents, temp_dir, later, report, progress, check_file,
                         settings)
            except _Cancelled:
                report.cancelled = True
                if job.report.result is None:   # else only its distances are missing
                    job.report.action = ACTION_CANCELLED
                    job.report.reason = ("cancelled during the import, partly imported"
                                         if job.started else "cancelled")
                invalidate_distances(job.report.target_path)
            except Exception as exc:
                logger.warning("GSAK import of %s failed", job.entry.name, exc_info=True)
                job.report.action, job.report.reason = ACTION_FAILED, str(exc) or type(exc).__name__
            finally:
                _discard_unpacked(job)
        if not report.cancelled:
            try:
                _run_settings(kind, opts, report, progress, settings)
            except _Cancelled:
                report.cancelled = True
    finally:
        if temp_dir is not None:
            import gc
            gc.collect()   # drop stray sqlite handles so Windows lets go of the files
            shutil.rmtree(temp_dir, ignore_errors=True)

    for line in report.summary_lines():
        logger.info(line)
    return report


def _run_job(job: _Job, index: int, contents: GsakBackupContents,
             temp_dir: Optional[Path], later: list[_Job], report: GsakImportReport,
             progress: Callable, check_file: Optional[Callable[[Path], None]],
             settings: Optional[_SettingsDb] = None) -> None:
    from opensak.db.database import get_session, optimize_database, session_for
    from opensak.utils import flags
    from opensak.db.manager import get_db_manager

    rep, entry = job.report, job.entry
    if entry.member is not None:
        assert temp_dir is not None
        progress(f"{entry.name}: unpacking")
        db3_path = extract_gsak_member(contents.source, entry.member,
                                       temp_dir / f"{index:02d}")
        job.db3_path, job.unpacked = db3_path, True
    else:
        assert entry.path is not None   # not in a zip: a file on disk
        db3_path = job.db3_path = entry.path
        if check_file is not None:
            check_file(db3_path)

    # Created only now, so nothing is created for a database that fails to
    # unpack or that the user may not read.
    manager = get_db_manager()
    if job.create:
        existing = find_existing(rep.target)
        info = existing or manager.new_database(rep.target)
        if existing is None:
            report.new_databases.append(info.name)
        rep.target, rep.target_path = info.name, info.path
    elif job.register:
        assert rep.target_path is not None
        info = manager.open_database(rep.target_path)
        rep.target = info.name
        report.new_databases.append(info.name)
    elif rep.target_path is None:
        # Merged into a database an earlier job of this run created.
        info = find_existing(rep.target)
        if info is None:
            raise GsakImportError(f"OpenSAK database {rep.target!r} was not created")
        rep.target_path = info.path

    # gsak.db3 holds the definitions of GSAK's global custom fields.
    settings_db: Optional[Path] = None
    if flags.companion_db and settings is not None and settings.available:
        try:
            settings_db = settings.path(progress)
        except _Cancelled:
            raise
        except Exception as exc:
            logger.warning("GSAK import: %s not readable", GSAK_SETTINGS_DB_NAME, exc_info=True)
            report.notes.append(f"{GSAK_SETTINGS_DB_NAME} could not be read ({exc}): "
                                "global custom fields imported as local fields")

    active_path = manager.active_path
    rep.active = rep.target_path == active_path
    label = f"{entry.name} → {rep.target}"
    progress(label)
    if job.replace or is_empty(rep.target_path):
        # #1231: built in a fresh file and swapped in; the target stays as it
        # was if the import fails. A target without caches (a new database)
        # has nothing to keep either, so it goes the same fast way.
        job.started = True
        rep.result = import_gsak_db_replacing(
            db3_path, rep.target_path,
            progress_cb=lambda done, total: progress(label, done, total),
            settings_db=settings_db,
        )
        rep.cleared = rep.result.cleared or 0
    else:
        with (get_session() if rep.active else session_for(rep.target_path)) as session:
            job.started = True
            rep.result = import_gsak_db(
                db3_path, session,
                progress_cb=lambda done, total: progress(label, done, total),
                settings_db=settings_db,
            )
    optimize_database(rep.target_path)   # #1048
    if all(j.report.target.lower() != rep.target.lower() for j in later):
        progress(f"{rep.target}: distances")
        update_distances(rep.target_path)
    else:
        invalidate_distances(rep.target_path)


def _discard_unpacked(job: _Job) -> None:
    if not job.unpacked or job.db3_path is None:
        return
    import gc
    gc.collect()   # drop stray sqlite handles so Windows lets go of the file
    shutil.rmtree(job.db3_path.parent, ignore_errors=True)
    job.unpacked = False


def _run_settings(kind: str, opts: GsakImportOptions, report: GsakImportReport,
                  progress: Callable, settings: _SettingsDb) -> None:
    """gsak.db3: user locations, then saved filters — one copy serves both."""
    if opts.filters is None and opts.locations is None:
        return
    if not settings.available:
        if kind == SOURCE_FILE:
            report.notes.append("user locations and saved filters are only imported "
                                "from a backup, a GSAK folder or gsak.db3")
        else:
            report.notes.append(f"no {GSAK_SETTINGS_DB_NAME} found: user locations and "
                                "saved filters not imported")
        return
    try:
        db3 = settings.path(progress)
    except _Cancelled:
        raise
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        if opts.locations is not None:
            report.locations_error = message
        if opts.filters is not None:
            report.filters_error = message
        return

    if opts.locations is not None:
        progress("user locations")
        from opensak.importer.gsak_location_importer import (
            apply_locations, load_gsak_locations, parse_gsak_locations,
        )
        try:
            locations = parse_gsak_locations(load_gsak_locations(db3))
            report.locations_invalid = sum(1 for loc in locations if not loc.valid)
            report.locations = apply_locations(locations, opts.locations == "overwrite")
        except Exception as exc:
            logger.warning("GSAK import: user locations failed", exc_info=True)
            report.locations_error = str(exc) or type(exc).__name__

    if opts.filters is not None:
        progress("saved filters")
        from opensak.importer.gsak_filter_importer import import_gsak_filters
        try:
            report.filters = import_gsak_filters(db3, overwrite=opts.filters == "overwrite")
        except Exception as exc:
            logger.warning("GSAK import: saved filters failed", exc_info=True)
            report.filters_error = str(exc) or type(exc).__name__
