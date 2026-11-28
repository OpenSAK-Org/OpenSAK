"""
src/opensak/db/manager.py — Database manager.

Håndterer flere lokale SQLite databaser.
Fra 1.14.0 (issue #209): gemmer liste over kendte databaser i opensak.json
via settings_store i stedet for QSettings.
"""

from __future__ import annotations

import gc
import logging
import os
import shutil
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryFile
from typing import TYPE_CHECKING, Optional

from opensak.backup.snapshot import SnapshotError, snapshot_database
from opensak.db import companion
from opensak.lang import tr
from opensak.settings_store import get_store
from opensak.utils.file_names import is_reserved_device_name

if TYPE_CHECKING:
    from opensak.db.lock import DatabaseLock

logger = logging.getLogger(__name__)

# Issue #870: detaljeret sporing af hvad der reelt indlæses fra settings.
# Issue #1046: logger kun databases.*-nøglerne, aldrig hele settings-store.
# Tavs medmindre "db_manager"-flaget er aktivt i debug_flags.py. De
# ovenstående logger.warning()-kald forbliver altid synlige uanset flag.
from opensak.logger import get_logger

_debug_log = get_logger("db_manager")

_SQLITE_MAGIC = b"SQLite format 3\x00"
# Leave room for extensions, companion files and SQLite sidecars.
_MAX_DATABASE_STEM_BYTES = 230


def _is_valid_sqlite_file(path: Path) -> bool:
    """
    Let, hurtig gyldigheds-tjek af en SQLite-database — læser kun de
    første 16 bytes (filens magic-header), åbner IKKE selve databasen.

    Issue #828: shutil.copy2() kopierer glad enhver fil videre uanset
    indhold. En tom, afkortet, eller på anden måde korrupt kilde-fil
    (fx fra en afbrudt tidligere session, eller — som fundet i praksis —
    en test-isolations-lækage, se #829) blev tidligere kopieret
    byte-for-byte, og brugeren ramte først sqlite3.DatabaseError næste
    gang appen prøvede at åbne filen, uden nogen kontekst om at filen
    allerede var i stykker FØR flytningen.

    Bevidst kun magic-bytes, ikke en fuld `sqlite3.connect()` +
    `PRAGMA integrity_check` — det er nok til at fange de konkrete
    fejlscenarier (tom fil, afkortet fil, forkert filtype) uden at åbne
    en ekstra databaseforbindelse for hver fil der skal flyttes.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(len(_SQLITE_MAGIC))
    except OSError:
        return False
    return header == _SQLITE_MAGIC


def _rename_database_files(old_path: Path, new_path: Path, folder: Path) -> None:
    """Rename database files in place, then move their folder if needed."""
    old_companion = companion.companion_path(old_path)
    new_companion = companion.companion_path(new_path)
    pairs = [(old_path, new_path)] + [
        (Path(str(old_path) + suffix), Path(str(new_path) + suffix))
        for suffix in ("-shm", "-wal", "-journal")
        if Path(str(old_path) + suffix).exists()
    ] + [
        (src, Path(str(new_companion) + src.name[len(old_companion.name):]))
        for src in companion.files(old_path)
    ]

    moves_folder = old_path.parent.name != folder.name
    reserved_folder = False
    renamed: list[tuple[Path, Path]] = []
    try:
        # POSIX rename can replace an empty directory. Claim the destination
        # first so simultaneous create/copy/restore calls cannot claim it.
        # Windows rename already refuses to replace an existing directory.
        if moves_folder and os.name != "nt" and not _same_name_ignoring_case(old_path.parent, folder):
            folder.mkdir()
            reserved_folder = True
        for source, target in pairs:
            if source.name != target.name:
                source.rename(target)
                renamed.append((source, target))
        if moves_folder:
            old_path.parent.rename(folder)
    except OSError:
        try:
            for source, target in reversed(renamed):
                target.rename(source)
        finally:
            if reserved_folder:
                _remove_empty_folder(folder)
        raise


def _same_name_ignoring_case(source: Path, target: Path) -> bool:
    """Allow a spelling-only rename on a case-insensitive filesystem."""
    return (
        source.name.casefold() == target.name.casefold()
        and not target.is_symlink()
        and target.exists()
        and source.exists()
        and source.samefile(target)
    )


def _remove_empty_folder(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        logger.warning("Could not remove database folder %s", path)


def _remove_with_sidecars(path: Path) -> None:
    """Best-effort removal of *path* and its SQLite sidecars."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink(missing_ok=True)
        except OSError:
            pass


class DatabaseInfo:
    """Metadata om en enkelt database."""

    def __init__(self, name: str, path: Path, *, managed_folder: bool | None = False):
        # None is an older settings entry with no ownership information.
        self.managed_folder = managed_folder
        self.name = name
        self.path = Path(path)

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def size_mb(self) -> float:
        if self.path.exists():
            return self.path.stat().st_size / (1024 * 1024)
        return 0.0

    @property
    def modified(self) -> Optional[datetime]:
        if self.path.exists():
            return datetime.fromtimestamp(self.path.stat().st_mtime)
        return None

    def to_dict(self) -> dict:
        entry: dict[str, str | bool] = {"name": self.name, "path": str(self.path)}
        if self.managed_folder is not None:
            entry["managed_folder"] = self.managed_folder
        return entry

    @classmethod
    def from_dict(cls, data: dict) -> "DatabaseInfo":
        managed = data.get("managed_folder")
        return cls(data["name"], Path(data["path"]),
                   managed_folder=managed if isinstance(managed, bool) else None)

    def __repr__(self) -> str:
        return f"<DatabaseInfo {self.name!r} @ {self.path}>"


class DatabaseManager:
    """
    Håndterer liste over kendte databaser og aktiv database.

    Databaser gemmes som separate .db filer i app data mappen.
    Listen over kendte databaser gemmes i opensak.json via settings_store.
    """

    def __init__(self):
        self._databases: list[DatabaseInfo] = []
        self._active: Optional[DatabaseInfo] = None
        # Issue #1045: (unavailable database, database used instead) when
        # ensure_active_initialised() had to fall back at startup.
        self._startup_fallback: Optional[tuple[DatabaseInfo, DatabaseInfo]] = None
        # #937: the lock of the active database, while the GUI holds it
        # (hold_active_lock); a headless run never does.
        self._hold_lock = False
        self._active_lock: Optional["DatabaseLock"] = None
        # #1203: the active database stored in the settings, while a headless
        # run works on another one (activate_for_run)
        self._stored_active: Optional[DatabaseInfo] = None
        self._run_only = False
        self._load_from_settings()

    # ── Interne helpers ───────────────────────────────────────────────────────

    def _default_db_path(self) -> Path:
        """Returner stien til standard databasen."""
        from opensak.settings_store import get_db_dir
        return get_db_dir() / "Default.db"

    @staticmethod
    def _migrate_path(path: Path) -> Path:
        """
        Flyt databaser fra den gamle 'geocacher'-mappe til 'opensak'-mappen.
        Kaldes automatisk ved indlæsning af QSettings.
        Selve .db-filen flyttes fysisk hvis den gamle sti stadig eksisterer.
        """
        from opensak.config import get_app_data_dir
        str_path = str(path)

        # Tjek om stien indeholder den gamle app-mappe
        old_markers = ["/geocacher/", "\\geocacher\\", "/geocacher\\", "\\geocacher/"]
        if not any(m in str_path for m in old_markers):
            return path  # allerede korrekt

        app_dir = get_app_data_dir()  # ~/.local/share/opensak
        new_path = app_dir / path.name

        # Flyt filen hvis den gamle eksisterer og den nye ikke gør
        if path.exists() and not new_path.exists():
            import shutil
            shutil.move(str(path), str(new_path))
            # #1094: the logger, not print() — stdout may not exist in a
            # windowed build, and the log file is where we ask users to look.
            logger.info("Migration: moved database %s → opensak/", path.name)
        elif path.exists() and new_path.exists():
            # Begge eksisterer — brug den nye, ignorer den gamle
            pass

        return new_path

    def _load_from_settings(self) -> None:
        """Indlæs liste over kendte databaser fra opensak.json."""
        store = get_store()
        db_list = store.get("databases.list", [])
        raw_active_path = store.get("databases.active")
        # Issue #878: id(store) logges for at afgøre om det er samme
        # SettingsStore-singleton der ses tom (reel læsnings-fejl) eller en
        # anden instans (singleton/cache-fejl). Issue #1046: kun antallet af
        # nøgler, ikke hele store'et — det indeholder personlige data.
        _debug_log.debug(
            "_load_from_settings: id(store)=%s, key_count=%d",
            id(store), len(getattr(store, "_data", None) or {}),
        )
        _debug_log.debug(
            "_load_from_settings: databases.list=%r, databases.active=%r, "
            "databases.dir=%r",
            db_list, raw_active_path, store.get("databases.dir"),
        )
        if isinstance(db_list, list):
            for entry in db_list:
                if isinstance(entry, dict):
                    name = entry.get("name")
                    path = entry.get("path")
                    if name and path:
                        original_path = Path(path)
                        migrated = (
                            original_path if entry.get("managed_folder") is True
                            else self._migrate_path(original_path)
                        )
                        _debug_log.debug(
                            "_load_from_settings: database %r -> %s "
                            "(original path %r, exists on disk: %s)",
                            name, migrated, path, migrated.exists(),
                        )
                        info = DatabaseInfo.from_dict({**entry, "path": migrated})
                        self._databases.append(info)

        # Aktiv database
        active_path = raw_active_path
        if active_path:
            migrated_active = Path(active_path)
            if not self._find_by_path(migrated_active):
                migrated_active = self._migrate_path(migrated_active)
            found = self._find_by_path(migrated_active)
            _debug_log.debug(
                "_load_from_settings: active path %r -> %s, matched a known "
                "database: %s", active_path, migrated_active, found is not None,
            )
            if found:
                self._active = found

        # Hvis ingen databaser kendes, opret Default
        if not self._databases:
            default_path = self._default_db_path()
            logger.warning(
                "No known databases found in settings (databases.list was "
                "%r, databases.active was %r) — creating a fresh, empty "
                "'Default' database at %s. If the user expected an "
                "existing database, it is most likely still untouched "
                "somewhere else on disk (see issue #870).",
                db_list, raw_active_path, default_path,
            )
            default = DatabaseInfo("Default", default_path)
            self._databases.append(default)
            self._active = default
        elif self._active is None:
            # Databaser kendes men ingen aktiv — brug den første
            _debug_log.debug(
                "_load_from_settings: %d database(s) known, but none active "
                "— using the first: %r", len(self._databases),
                self._databases[0].name,
            )
            self._active = self._databases[0]

        # Issue #878: dette kald er flyttet hertil (efter al fallback-logik
        # ovenfor) fra sin oprindelige placering lige efter parsing af
        # databases.list/.active. Kaldet var der ubetinget og kunne dermed
        # skrive en tom/delvis mellemtilstand til disk, FØR fallback-logikken
        # nåede at rette den op — hvis self._databases var tom på det
        # tidspunkt (root cause endnu ukendt, kun set i den pakkede/signerede
        # macOS-app, ikke reproduceret isoleret). Ved kun at gemme én gang,
        # med den endelige tilstand, kan et sådant tomt mellemresultat aldrig
        # nå at blive persisteret.
        self._save_to_settings()

    def _save_to_settings(self) -> None:
        """Gem liste over kendte databaser til opensak.json."""
        store = get_store()
        active = self._stored_active if self._run_only else self._active
        store.set_many({
            "databases.list": [db.to_dict() for db in self._databases],
            "databases.active": str(active.path) if active else "",
        })
        # Every change of the active database (switch, rename, move) passes here.
        self._sync_active_lock()

    def activate_for_run(self, db_info: "DatabaseInfo") -> None:
        """#1203: make *db_info* (already opened with init_db) the active
        database of this headless run only: the settings keep the active
        database, so the GUI still opens the same one afterwards. *db_info*
        need not be in the list."""
        if not self._run_only:
            self._stored_active = self._active
            self._run_only = True
        self._active = db_info

    def end_run(self) -> None:
        """Undo activate_for_run()."""
        if self._run_only:
            self._active = self._stored_active
            self._stored_active = None
            self._run_only = False

    def hold_active_lock(self) -> None:
        """#937: from now on hold the lock of the active database, and move
        it along when the active database changes — so a headless
        `opensak import` refuses to write to the database open in the GUI.
        Called by the GUI at startup."""
        self._hold_lock = True
        self._sync_active_lock()

    def release_active_lock(self) -> None:
        """Let go of the active database's lock (on exit)."""
        self._hold_lock = False
        self._sync_active_lock()

    def _sync_active_lock(self) -> None:
        path = self._active.path if (self._hold_lock and self._active) else None
        if self._active_lock is not None and self._active_lock.db_path == path:
            return
        if self._active_lock is not None:
            self._active_lock.release()
            self._active_lock = None
        if path is None:
            return
        from opensak.db.lock import DatabaseLock
        lock = DatabaseLock(path)
        try:
            acquired = lock.acquire()
        except OSError:
            logger.warning("database lock: could not lock %s", path, exc_info=True)
            return
        if acquired:
            self._active_lock = lock
        else:
            # Never keep the user out of their database: a headless run
            # (or a second OpenSAK) has it — say so in the log and go on.
            logger.warning("database lock: %s is in use by %s", path,
                           lock.holder() or "another process")

    def _find_by_path(self, path: Path) -> Optional[DatabaseInfo]:
        for db in self._databases:
            if db.path == path:
                return db
        return None

    def _find_by_name(self, name: str) -> Optional[DatabaseInfo]:
        for db in self._databases:
            if db.name == name:
                return db
        return None

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def databases(self) -> list[DatabaseInfo]:
        """Kendte databaser, alfabetisk sorteret efter navn (case-insensitive).

        Bemærk: rækkefølgen i selve `self._databases` (og dermed i
        opensak.json) er stadig indsættelsesrækkefølgen — kun visningen
        her sorteres. Se issue #531 / #601.
        """
        return sorted(self._databases, key=lambda db: db.name.lower())

    @property
    def active(self) -> Optional[DatabaseInfo]:
        return self._active

    @property
    def active_path(self) -> Optional[Path]:
        return self._active.path if self._active else None

    def ensure_active_initialised(self) -> None:
        """
        Sørg for at den aktive database er initialiseret.
        Kaldes ved opstart — åbner den samme DB som sidst.

        Issue #1045: if the last active database file is gone (typically a
        removed USB drive), fall back to another database instead of failing
        startup. The missing entry stays in the list — the drive usually comes
        back. take_startup_fallback() tells the GUI what happened.
        """
        if not self._active:
            return
        if not self._active.exists:
            self._fall_back_from_missing_active()
        from opensak.db.database import init_db
        init_db(db_path=self._active.path)

    def _fall_back_from_missing_active(self) -> None:
        """
        Issue #1045: replace a missing active database with the first known
        database that exists on disk, or else with Default.db in the database
        folder (created if needed).
        """
        missing = self._active
        assert missing is not None
        fallback = next(
            (db for db in self.databases if db is not missing and db.exists),
            None,
        )
        if fallback is None:
            default_path = self._writable_default_path()
            fallback = self._find_by_path(default_path)
            if fallback is None:
                name = "Default"
                counter = 2
                while self._find_by_name(name):
                    name = f"Default ({counter})"
                    counter += 1
                fallback = DatabaseInfo(name, default_path)
                self._databases.append(fallback)

        if fallback is not missing:
            logger.warning(
                "Active database %r is unavailable (%s not found) — "
                "using %r (%s) instead",
                missing.name, missing.path, fallback.name, fallback.path,
            )
            self._startup_fallback = (missing, fallback)
        self._active = fallback
        self._save_to_settings()

    def _writable_default_path(self) -> Path:
        """
        Default.db in the database folder, creating the folder if needed.
        If that folder can't be created either (it may itself live on the
        removed drive), use the install folder instead.
        """
        try:
            path = self._default_db_path()  # get_db_dir() creates the folder
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            from opensak.settings_store import get_install_dir
            logger.warning(
                "Database folder is unavailable — using the install folder "
                "for Default.db", exc_info=True,
            )
            install_dir = get_install_dir()
            install_dir.mkdir(parents=True, exist_ok=True)
            return install_dir / "Default.db"

    def take_startup_fallback(self) -> Optional[tuple[DatabaseInfo, DatabaseInfo]]:
        """
        Issue #1045: (unavailable database, database used instead) if startup
        had to fall back, else None. Cleared once read, so the notice is
        shown once.
        """
        result, self._startup_fallback = self._startup_fallback, None
        return result

    @staticmethod
    def default_path_for(name: str) -> Path:
        """Return the path for a new database under the configured directory."""
        from opensak.settings_store import get_db_dir
        return DatabaseManager.new_database_path(name, get_db_dir())

    @staticmethod
    def new_database_path(name: str, directory: Path) -> Path:
        """Build a portable folder and SQLite filename for a new database."""
        stem = DatabaseManager.file_name_for(name).removesuffix(".db") or "_"
        if is_reserved_device_name(stem):
            stem = f"_{stem}"
        encoded = stem.encode("utf-8")
        if len(encoded) > _MAX_DATABASE_STEM_BYTES:
            suffix = "-" + sha256(encoded).hexdigest()[:12]
            prefix = encoded[:_MAX_DATABASE_STEM_BYTES - len(suffix)]
            stem = prefix.decode("utf-8", errors="ignore").rstrip() + suffix
        return directory / stem / f"{stem}.db"

    @staticmethod
    def _create_destination_folder(parent: Path, *, exclusive: bool) -> None:
        try:
            parent.mkdir(parents=True, exist_ok=not exclusive)
        except FileExistsError as exc:
            if exclusive:
                raise ValueError(tr("db_err_new_folder_exists", path=parent)) from exc
            raise ValueError(tr("db_err_mkdir_failed", path=parent) + f"\n{exc}") from exc
        except OSError as exc:
            raise ValueError(tr("db_err_mkdir_failed", path=parent) + f"\n{exc}") from exc

    @staticmethod
    def is_valid_database_file(path: Path) -> bool:
        """True hvis *path* er en SQLite-fil (magic-header, se _is_valid_sqlite_file)."""
        return _is_valid_sqlite_file(Path(path))

    def new_database(
        self, name: str, path: Optional[Path] = None, *, directory: Optional[Path] = None
    ) -> "DatabaseInfo":
        """Create an empty database in its own folder, or at an explicit file path."""
        if self._find_by_name(name):
            raise ValueError(tr("db_err_name_exists", name=name))

        create_folder = path is None
        if path is None:
            path = (self.default_path_for(name) if directory is None
                    else self.new_database_path(name, directory))
        else:
            path = Path(path)

        # #539: init_db() would reopen an existing file and expose its data
        # under the name of a supposedly empty database.
        if self._find_by_path(path) or (not create_folder and path.exists()):
            raise ValueError(
                tr("db_err_target_path_exists", name=name, path=str(path))
            )

        parent = path.parent
        self._create_destination_folder(parent, exclusive=create_folder)

        from opensak.db.database import init_db, session_for

        try:
            if not parent.is_dir():
                raise ValueError(tr("db_err_dir_not_found", path=parent))
            try:
                with TemporaryFile(dir=parent):
                    pass
            except OSError as e:
                raise ValueError(tr("db_err_no_write_permission", path=parent)) from e
            try:
                if create_folder:
                    # An import worker can create a database while the UI
                    # continues to query the active one.
                    with session_for(path):
                        pass
                else:
                    init_db(db_path=path)
                    if self._active:
                        init_db(db_path=self._active.path)
            except Exception as e:
                raise ValueError(tr("db_err_create_failed") + f"\n{e}") from e
        except ValueError:
            if create_folder:
                companion.forget(path)
                _remove_with_sidecars(path)
                _remove_with_sidecars(companion.companion_path(path))
                _remove_empty_folder(parent)
            raise

        info = DatabaseInfo(name, path, managed_folder=create_folder)
        # Its db_uuid right away, not only when it is first opened: a
        # database filled in the background (GSAK import into a new
        # database) has an id before its companion file is made (#1034).
        self._seed_db_settings(info)
        self._databases.append(info)
        self._save_to_settings()
        return info

    def open_database(self, path: Path) -> "DatabaseInfo":
        """Åbn en eksisterende .db fil og tilføj til listen."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(tr("db_err_file_not_found", path=path))
        # #1034: opening a companion database as a database would give it
        # OpenSAK's whole schema.
        if companion.is_companion_file(path):
            main = path.name[: -len(companion.SUFFIX)] + ".db"
            raise ValueError(tr("db_err_companion_file", name=main, path=path))

        existing = self._find_by_path(path)
        if existing:
            return existing

        name = path.stem
        base_name = name
        counter = 2
        while self._find_by_name(name):
            name = f"{base_name} ({counter})"
            counter += 1

        info = DatabaseInfo(name, path)
        self._databases.append(info)
        self._save_to_settings()
        return info

    def add_existing(self, path: Path, name: str, *, managed_folder: bool = False) -> "DatabaseInfo":
        """
        Add the existing database file *path* to the list under the display
        name *name* (#954: a restored database). Unlike open_database(), the
        name is given rather than taken from the file name.

        Raises ValueError if the name or the path is already in the list.
        """
        path = Path(path)
        if self._find_by_name(name):
            raise ValueError(tr("db_err_name_exists", name=name))
        if self._find_by_path(path):
            raise ValueError(tr("db_err_target_path_exists", name=name, path=str(path)))
        info = DatabaseInfo(name, path, managed_folder=managed_folder)
        self._databases.append(info)
        self._save_to_settings()
        return info

    def switch_to(self, db_info: "DatabaseInfo") -> None:
        """Skift aktiv database og initialiser den."""
        from opensak.db.database import init_db
        # Issue #1045: never let switching create an empty database in place
        # of a missing one (e.g. on a removed USB drive).
        if not db_info.exists:
            raise FileNotFoundError(tr("db_err_file_not_found", path=db_info.path))
        try:
            init_db(db_path=db_info.path)  # raises if the file is not a valid DB
        except Exception:
            # Issue #738: this used to be uncaught all the way up through
            # whichever GUI call site triggered it (toolbar dropdown,
            # database dialog's Switch button, or the auto-switch after
            # creating a new database) — in a PyInstaller-bundled build
            # with no console window, that traceback went nowhere and the
            # app just looked frozen or was left in a half-switched state.
            # Logged here once, at the source, so it's captured regardless
            # of which caller triggered it (and now preserved across a
            # restart via #737's log rotation) — then re-raised so each
            # caller can still show its own error dialog.
            logger.exception("switch_to(%s) failed", db_info.path)
            raise
        self._active = db_info          # only update state after successful init
        self._save_to_settings()

    def rename(self, db_info: "DatabaseInfo", new_name: str) -> None:
        """
        Omdøb en database — både label og den fysiske .db-fil (+ evt.
        -shm/-wal sidecar-filer).

        Issue #539: rename() opdaterede tidligere KUN db_info.name (label'en
        i listen) — den fysiske fil blev aldrig flyttet/omdøbt. Det gav to
        sammenhængende fejl: (a) kolonneopsætning, som gemmes pr. database-
        navn (#199), pegede pludselig på en tom nøgle og så ud til at være
        nulstillet, og (b) new_database() udleder filstien deterministisk
        fra navnet — så en efterfølgende "Ny database" med det gamle (nu
        "ledige") navn genbrugte uden varsel den samme fysiske fil, som
        aldrig var blevet flyttet, og "genopstod" med alt det gamle indhold
        (caches, kolonneopsætning) intakt.
        """
        if new_name == db_info.name:
            return  # ingen ændring
        if self._find_by_name(new_name):
            raise ValueError(tr("db_err_name_exists", name=new_name))

        old_name = db_info.name
        old_path = db_info.path

        # Issue #659: the path- and name-keyed legacy settings in
        # opensak.json no longer match once the file is renamed — import
        # them into the database file first, so they travel with it.
        self._seed_db_settings(db_info)

        old_folder = old_path.parent
        managed_folder = db_info.managed_folder
        if managed_folder is None:
            managed_folder = self._is_legacy_database_folder(db_info)
        new_path = self.new_database_path(new_name, old_folder.parent)
        if not managed_folder:
            new_path = old_folder / new_path.name
        if new_path.name != old_path.name or new_path.parent.name != old_folder.name:
            self._rename_database(db_info, new_path, new_name)

        # Flyt evt. gemte kolonneindstillinger (#199) til det nye navn, så
        # brugeren ikke mister sin kolonneopsætning ved omdøbning. Best-
        # effort — en fejl her må ikke forhindre selve omdøbningen, men skal
        # ikke fejle helt lydløst (#539: gjorde det svært at diagnosticere
        # om kolonner "gik tabt" pga. denne fejlende, eller slet ikke blev
        # forsøgt migreret).
        try:
            from opensak.gui.dialogs.column_dialog import migrate_column_settings_for_rename
            migrate_column_settings_for_rename(old_name, new_name)
        except Exception:
            logger.warning(
                "Could not migrate column settings on rename %r -> %r",
                old_name, new_name, exc_info=True,
            )

        db_info.name = new_name
        db_info.managed_folder = managed_folder
        self._save_to_settings()

    def _is_legacy_database_folder(self, db_info: DatabaseInfo) -> bool:
        """Recognize folders created before the settings recorded ownership."""
        from opensak.settings_store import get_db_dir

        path = db_info.path
        folder = path.parent
        expected = self.new_database_path(db_info.name, folder.parent)
        if path.name != expected.name or folder.is_symlink() or not path.is_file():
            return False

        database_files = {path, companion.companion_path(path)}
        for file in tuple(database_files):
            database_files.update(Path(str(file) + suffix)
                                  for suffix in ("-journal", "-wal", "-shm", ".lock"))
        try:
            # The original PR also allowed a custom parent. Only adopt its
            # exact layout when the folder contains just this database's files.
            if folder.parent.resolve() != get_db_dir().resolve():
                return path == expected and all(child in database_files for child in folder.iterdir())

            # Under the configured database directory, also recognize runs
            # of the initial rename code that already renamed just the file.
            return all(
                child in database_files
                or (not child.is_dir()
                    and child.suffix.lower() not in {".db", ".db3", ".sqlite", ".sqlite3"})
                for child in folder.iterdir()
            )
        except OSError:
            return False

    def _rename_database(self, db_info: DatabaseInfo, new_path: Path, new_name: str) -> None:
        """Move an owned directory with its contents, or rename files in place."""
        from opensak.db.database import dispose_engine, init_db

        old_path = db_info.path
        old_folder, new_folder = old_path.parent, new_path.parent
        in_place = old_path.with_name(new_path.name)
        if old_folder.name != new_folder.name:
            if (new_folder.exists() or new_folder.is_symlink()) and not _same_name_ignoring_case(
                old_folder, new_folder
            ):
                raise ValueError(tr("db_err_new_folder_exists", path=new_folder))
            if old_folder.is_symlink():
                raise ValueError(tr("db_err_target_path_exists", name=db_info.name,
                                    path=str(old_folder)))
            absolute_folder = old_folder.absolute()
            resolved_folder = old_folder.resolve()
            for other in self._databases:
                if other is db_info:
                    continue
                if (other.path.absolute().is_relative_to(absolute_folder)
                        or other.path.resolve().is_relative_to(resolved_folder)):
                    raise ValueError(tr("db_err_target_path_exists", name=other.name,
                                        path=str(other.path)))

        for source, target in (
            (old_path, in_place),
            (companion.companion_path(old_path), companion.companion_path(in_place)),
        ):
            for suffix in ("", "-shm", "-wal", "-journal"):
                src = Path(str(source) + suffix)
                dst = Path(str(target) + suffix)
                if (src.name != dst.name and (dst.exists() or dst.is_symlink())
                        and not _same_name_ignoring_case(src, dst)):
                    raise ValueError(
                        tr("db_err_target_path_exists", name=new_name, path=str(dst))
                    )

        was_active = db_info is self._active
        dispose_engine(old_path)
        gc.collect()
        time.sleep(0.05)
        if self._active_lock is not None and self._active_lock.db_path == old_path:
            self._active_lock.release()
            self._active_lock = None
        try:
            _rename_database_files(old_path, in_place, new_folder)
            db_info.path = new_path
            companion.forget(old_path)
        except OSError as exc:
            raise ValueError(tr("db_err_rename_failed", name=db_info.name, error=exc)) from exc
        finally:
            if was_active and db_info.exists:
                try:
                    init_db(db_path=db_info.path)
                except Exception:
                    logger.exception("Could not reopen database %s after rename", db_info.path)
                self._sync_active_lock()

    @staticmethod
    def _seed_db_settings(db_info: "DatabaseInfo") -> None:
        """Import a database's legacy opensak.json settings into it (#659).

        Best-effort: a failure here must never block a rename or copy.
        """
        try:
            from opensak.db.db_settings import ensure_seeded
            ensure_seeded(db_info.path, db_info.name)
        except Exception:
            logger.warning(
                "Could not import the settings of %r into the database file",
                db_info.name, exc_info=True,
            )

    def copy_database(
        self, db_info: DatabaseInfo, new_name: str, new_path: Optional[Path] = None,
        *, directory: Optional[Path] = None,
    ) -> DatabaseInfo:
        """Copy a database into its own folder, or to an explicit file path."""
        if self._find_by_name(new_name):
            raise ValueError(tr("db_err_name_exists", name=new_name))

        create_folder = new_path is None
        if new_path is None:
            new_path = (self.default_path_for(new_name) if directory is None
                        else self.new_database_path(new_name, directory))
        new_path = Path(new_path)

        # Issue #1047: the destination can now be any folder. Never replace
        # a file that is already there (snapshot_database() would), and
        # never copy a database whose file is missing.
        if not db_info.exists:
            raise FileNotFoundError(tr("db_err_file_not_found", path=db_info.path))
        if self._find_by_path(new_path) or new_path.exists() or new_path.is_symlink():
            raise ValueError(
                tr("db_err_target_path_exists", name=new_name, path=str(new_path))
            )
        new_companion = companion.companion_path(new_path)
        if new_companion.exists() or new_companion.is_symlink():
            raise ValueError(
                tr("db_err_target_path_exists", name=new_name, path=str(new_companion))
            )

        # Issue #659: make sure the source's settings are inside the file
        # (imported from opensak.json if it hasn't been opened since), so
        # the copy gets them too.
        self._seed_db_settings(db_info)

        # Issue #943: a WAL-safe snapshot instead of shutil.copy2(). Copying
        # only the main .db file could miss committed changes that still
        # live in the -wal sidecar — notably when copying the active,
        # open database — and could produce an inconsistent copy if a
        # write happened mid-copy.
        if create_folder:
            self._create_destination_folder(new_path.parent, exclusive=True)
        try:
            snapshot_database(db_info.path, new_path)
            # #1034: the companion database is part of the copy.
            if companion.exists(db_info.path):
                snapshot_database(companion.companion_path(db_info.path), new_companion)
        except SnapshotError as exc:
            _remove_with_sidecars(new_path)
            _remove_with_sidecars(new_companion)
            if create_folder:
                _remove_empty_folder(new_path.parent)
            raise SnapshotError(
                tr("db_err_copy_failed", name=db_info.name, error=str(exc))
            ) from exc

        # Issue #659: a copy is a database of its own from now on, so it
        # gets its own identity. Best-effort — the copy itself succeeded.
        try:
            from opensak.db.db_settings import assign_new_uuid
            new_uuid = assign_new_uuid(new_path)
            # Its companion belongs to the copy, not to the original.
            companion.set_main_uuid(new_path, new_uuid)
        except Exception:
            logger.warning(
                "Could not give the copy %s a new database id", new_path,
                exc_info=True,
            )
        info = DatabaseInfo(new_name, new_path, managed_folder=create_folder)
        self._databases.append(info)
        self._save_to_settings()
        return info

    @staticmethod
    def file_name_for(name: str) -> str:
        """Return the sanitized .db filename for *name*."""
        safe_name = "".join(
            c if c.isalnum() or c in "-_ " else "_" for c in name
        ).strip()
        return f"{safe_name}.db"

    @staticmethod
    def move_target_path(db_info: DatabaseInfo, directory: Path, create_subfolder: bool) -> Path:
        """Resolve the path displayed by the move dialog and used by the move."""
        if create_subfolder:
            return DatabaseManager.new_database_path(db_info.name, directory)
        return directory / db_info.path.name

    def move_database(
        self, db_info: DatabaseInfo, new_dir: Path, *, create_subfolder: bool = False,
    ) -> Optional[str]:
        """
        Issue #1047: move one database (with its -wal/-shm sidecars) to the
        folder *new_dir*, optionally in a new managed subfolder, keeping its
        display name and database id.

        The original is deleted only after the copy is verified. Returns a
        warning message if the copy is in place and in use but the original
        could not be deleted (e.g. read-only media), else None.

        Raises FileNotFoundError if the database file is missing, and
        ValueError if the move is refused or fails — in that case nothing
        has changed: the database stays where it was, at its old path.
        """
        new_dir = Path(new_dir)
        old_path = db_info.path
        if not db_info.exists:
            raise FileNotFoundError(tr("db_err_file_not_found", path=old_path))
        new_path = self.move_target_path(db_info, new_dir, create_subfolder)
        if new_path == old_path:
            raise ValueError(tr("db_err_move_same_folder", name=db_info.name))
        self._create_destination_folder(new_path.parent, exclusive=create_subfolder)

        was_active = db_info == self._active
        try:
            original_removed = self._relocate(db_info, new_path, delete_original=True)
            db_info.managed_folder = create_subfolder
        except ValueError:
            if create_subfolder:
                _remove_empty_folder(new_path.parent)
            raise
        finally:
            if was_active:
                # The active engine was disposed to release the file. Reopen
                # it at whichever path is current now — the new one after a
                # move, the old one if the move was refused or failed — so
                # the app keeps working without a restart.
                try:
                    self.ensure_active_initialised()
                except Exception:
                    logger.warning(
                        "Could not reopen the active database after moving "
                        "it — a restart will be required.", exc_info=True,
                    )
        self._save_to_settings()

        if not original_removed:
            logger.warning(
                "Moved %r to %s, but could not delete the original %s",
                db_info.name, new_path, old_path,
            )
            return tr("db_move_original_left", name=db_info.name, path=str(old_path))
        return None

    def _relocate(self, db_info: "DatabaseInfo", new_path: Path,
                  delete_original: bool) -> bool:
        """
        Copy *db_info*'s file and its -wal/-shm sidecars to *new_path*,
        verify the copy, optionally delete the original, and point *db_info*
        at *new_path*. Shared by move_database() and move_databases_to().

        Returns False if *delete_original* was asked for but the original
        file is still there afterwards, else True. Does not save the list or
        reopen the active engine — the callers do that once.

        Raises ValueError if the move is refused or the copy fails; any
        partial copy is removed again and *db_info* is unchanged.
        """
        from opensak.db.database import dispose_engine

        old_path = db_info.path
        if self._find_by_path(new_path) or new_path.exists() or new_path.is_symlink():
            raise ValueError(
                tr("db_err_move_target_exists", name=db_info.name, path=str(new_path))
            )
        new_companion = companion.companion_path(new_path)
        if new_companion.exists() or new_companion.is_symlink():
            raise ValueError(
                tr("db_err_move_target_exists", name=db_info.name, path=str(new_companion))
            )

        # Issue #828: validate BEFORE touching the engine — a corrupt
        # source must never be copied on, and there is no reason to close
        # the active engine for a file that is skipped anyway.
        if not _is_valid_sqlite_file(old_path):
            raise ValueError(tr("db_err_corrupt_source", name=db_info.name))

        # Issue #961: the path-keyed legacy settings in opensak.json
        # (#659) no longer match once the file has a new path — import
        # them into the database file before it is copied, so home
        # location, sort order and filter profile travel with it. Same
        # pattern as rename() and copy_database().
        self._seed_db_settings(db_info)

        # Close the engine before file operations — avoids locked files on
        # Windows.
        dispose_engine(old_path)
        gc.collect()
        time.sleep(0.05)

        # #1034: the companion database (and its sidecars) moves along.
        old_companion = companion.companion_path(old_path)
        pairs = [
            (Path(str(old_path) + suffix), Path(str(new_path) + suffix))
            for suffix in ("", "-shm", "-wal")
        ] + [
            (src, Path(str(new_companion) + src.name[len(old_companion.name):]))
            for src in companion.files(old_path)
        ]
        copied: list[Path] = []
        try:
            for src, dst in pairs:
                if src != old_path and not src.exists():
                    continue  # SQLite WAL sidecars only exist while in use
                copied.append(dst)  # before copying: a failed copy can leave a partial file
                shutil.copy2(src, dst)
                if dst.stat().st_size != src.stat().st_size:
                    raise OSError(f"size mismatch after copying {src.name}")
            if not _is_valid_sqlite_file(new_path):
                raise OSError("the copy is not a valid SQLite database")
            if new_companion in copied and not _is_valid_sqlite_file(new_companion):
                raise OSError("the copy of the companion database is not a valid SQLite database")
        except OSError as exc:
            for f in copied:
                try:
                    f.unlink()
                except OSError:
                    pass
            raise ValueError(
                tr("db_err_move_failed", name=db_info.name, error=str(exc))
            ) from exc

        if delete_original:
            for f, _dst in pairs:
                try:
                    if f.exists():
                        f.unlink()
                except OSError:
                    pass  # reported below via old_path.exists()

        db_info.path = new_path
        db_info.managed_folder = False
        companion.forget(old_path)
        return not (delete_original and (old_path.exists() or old_companion.exists()))

    def move_databases_to(
        self, new_dir: Path, delete_originals: bool
    ) -> list[str]:
        """
        Overfør alle kendte databaser (inkl. -shm/-wal sidecar-filer) til
        en ny mappe og opdater deres stier i listen.

        Bruges når brugeren ændrer database-mappen i Settings → Advanced
        og vælger at flytte sine eksisterende databaser med (i stedet for
        kun at lade nye databaser blive oprettet i den nye mappe).

        Args:
            new_dir: destinationsmappen — oprettes hvis den ikke findes.
            delete_originals: hvis True slettes kilde-filerne efter
                kopiering ("Flyt og slet"); hvis False bevares de
                ("Flyt og behold").

        Returnerer en liste af fejlbeskeder for databaser der ikke kunne
        flyttes (fx fordi destinationen allerede har en fil med samme
        navn) — tom liste hvis alt gik godt. Databaser der fejler bliver
        IKKE rørt og forbliver på deres oprindelige sti.

        Den aktive database håndteres sidst og kræver at dens engine er
        disposed før filen kan flyttes/kopieres sikkert (samme mønster
        som delete_database, for at undgå låste filer på Windows).
        """
        new_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        updated_any = False
        active_engine_disposed = False

        for db_info in list(self._databases):
            old_path = db_info.path
            if old_path.parent == new_dir:
                continue  # allerede i destinationen — intet at gøre

            new_path = new_dir / old_path.name

            # Issue #609: en "kendt" database har ikke nødvendigvis en
            # fysisk .db-fil endnu — fx det auto-oprettede "Default"-metadata-
            # objekt for en helt frisk installation, hvor SQLite-filen først
            # bliver skabt når appen rent faktisk åbner den (init_db()),
            # hvilket sker EFTER velkomst-wizarden. shutil.copy2() ville her
            # fejle med "No such file or directory", selvom der reelt ikke
            # er noget at flytte. Opdatér blot stien i metadata og fortsæt —
            # ikke en fejl, bare intet arbejde at udføre.
            if not old_path.exists():
                db_info.path = new_path
                db_info.managed_folder = False
                updated_any = True
                continue

            # Issue #1047: the copy/verify/delete steps are shared with
            # move_database(). A failure leaves this database untouched at
            # its old path; the remaining databases are still moved.
            if db_info == self._active:
                active_engine_disposed = True
            try:
                self._relocate(db_info, new_path, delete_original=delete_originals)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            updated_any = True

        if updated_any:
            self._save_to_settings()

        if active_engine_disposed:
            # The loop above disposes the active database's engine before
            # copying its file (needed to release the file handle, notably
            # on Windows), but never reopened it — leaving get_session() in
            # a broken "Database not initialised" state until the app is
            # restarted. Any code that touches the database before that
            # restart (e.g. mainwindow re-syncing distances right after the
            # Settings dialog closes) would crash. Reopen it immediately at
            # its new path so the app stays usable without a restart.
            #
            # Best-effort: the file move itself already succeeded at this
            # point regardless of what happens here, so a failure to reopen
            # (e.g. a file handle not yet released) shouldn't be raised as
            # if the move failed — the caller already shows a "restart
            # required" notice that covers this case too.
            try:
                self.ensure_active_initialised()
            except Exception:
                logger.warning(
                    "Could not reopen the active database engine after "
                    "moving it — a restart will be required.", exc_info=True,
                )

        return errors

    def remove_from_list(self, db_info: "DatabaseInfo") -> None:
        """Fjern database fra listen uden at slette filen."""
        if db_info == self._active:
            raise ValueError(tr("db_err_remove_active"))
        # Luk engine så SQLite WAL-filer frigives korrekt på Windows
        from opensak.db.database import dispose_engine
        dispose_engine(db_info.path)
        self._databases.remove(db_info)
        self._save_to_settings()

    def delete_database(self, db_info: "DatabaseInfo") -> Optional[Path]:
        """Slet database permanent (inkl. -shm og -wal filer).

        Returnerer:
            Path til forældremappe hvis den er tom efter sletning og
            indeholder ingen andre filer — så dialogen kan tilbyde at
            slette den.  Returnerer None hvis mappen ikke er tom.
        """
        if db_info == self._active:
            raise ValueError(
                tr("db_err_delete_active")
            )

        # Luk SQLAlchemy engine for denne database FØR sletning.
        # På Windows holder WAL-mode (.db-shm / .db-wal) filerne låst
        # så længe connection pool er åben → WinError 32.
        from opensak.db.database import dispose_engine
        dispose_engine(db_info.path)
        gc.collect()       # tving garbage collection af evt. resterende refs
        time.sleep(0.1)    # giv Windows tid til at frigive file handles

        errors: list[str] = []
        db_path = db_info.path
        folder = db_path.parent

        # Slet hovedfilen + WAL/SHM sidekick-filer, and the companion
        # database (#1034) with its own.
        companion.forget(db_path)
        targets = [Path(str(db_path) + suffix) for suffix in ("", "-shm", "-wal")]
        for f in [*targets, *companion.files(db_path)]:
            if f.exists():
                try:
                    f.unlink()
                except OSError as e:
                    errors.append(f"{f.name}: {e}")

        if errors:
            # Fjern alligevel fra listen, men fortæl brugeren
            self._databases.remove(db_info)
            self._save_to_settings()
            raise OSError(
                tr("db_err_delete_partial") + "\n" + "\n".join(errors)
            )

        self._databases.remove(db_info)
        self._save_to_settings()

        # Tjek om mappen er tom efter sletning — returner stien så
        # dialogen kan spørge brugeren om den også skal slettes.
        try:
            remaining = list(folder.iterdir())
            if not remaining:
                return folder
        except OSError:
            pass
        return None

    def delete_folder(self, folder: Path) -> None:
        """Slet en tom mappe (kaldes fra dialog efter bruger-bekræftelse)."""
        try:
            folder.rmdir()
        except OSError as e:
            raise OSError(tr("db_err_delete_folder", path=folder) + f"\n{e}")


# ── Module-level singleton ────────────────────────────────────────────────────

_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    global _manager
    if _manager is None:
        _manager = DatabaseManager()
    return _manager
