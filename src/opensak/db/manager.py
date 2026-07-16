"""
src/opensak/db/manager.py — Database manager.

Håndterer flere lokale SQLite databaser.
Fra 1.14.0 (issue #209): gemmer liste over kendte databaser i opensak.json
via settings_store i stedet for QSettings.
"""

from __future__ import annotations

import gc
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from opensak.lang import tr
from opensak.settings_store import get_store

logger = logging.getLogger(__name__)


class DatabaseInfo:
    """Metadata om en enkelt database."""

    def __init__(self, name: str, path: Path):
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
        return {"name": self.name, "path": str(self.path)}

    @classmethod
    def from_dict(cls, data: dict) -> "DatabaseInfo":
        return cls(data["name"], Path(data["path"]))

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
            print(f"Migration: flyttede database {path.name} → opensak/")
        elif path.exists() and new_path.exists():
            # Begge eksisterer — brug den nye, ignorer den gamle
            pass

        return new_path

    def _load_from_settings(self) -> None:
        """Indlæs liste over kendte databaser fra opensak.json."""
        store = get_store()
        db_list = store.get("databases.list", [])
        if isinstance(db_list, list):
            for entry in db_list:
                if isinstance(entry, dict):
                    name = entry.get("name")
                    path = entry.get("path")
                    if name and path:
                        migrated = self._migrate_path(Path(path))
                        info = DatabaseInfo(name, migrated)
                        self._databases.append(info)

        # Aktiv database
        active_path = store.get("databases.active")
        if active_path:
            migrated_active = self._migrate_path(Path(active_path))
            found = self._find_by_path(migrated_active)
            if found:
                self._active = found

        # Gem migrerede stier tilbage (én gang)
        self._save_to_settings()

        # Hvis ingen databaser kendes, opret Default
        if not self._databases:
            default_path = self._default_db_path()
            default = DatabaseInfo("Default", default_path)
            self._databases.append(default)
            self._active = default
            self._save_to_settings()
        elif self._active is None:
            # Databaser kendes men ingen aktiv — brug den første
            self._active = self._databases[0]
            self._save_to_settings()

    def _save_to_settings(self) -> None:
        """Gem liste over kendte databaser til opensak.json."""
        store = get_store()
        store.set_many({
            "databases.list": [db.to_dict() for db in self._databases],
            "databases.active": str(self._active.path) if self._active else "",
        })

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
        return list(self._databases)

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
        """
        if self._active:
            from opensak.db.database import init_db
            init_db(db_path=self._active.path)

    def new_database(self, name: str, path: Optional[Path] = None) -> "DatabaseInfo":
        """Opret en ny tom database."""
        if self._find_by_name(name):
            raise ValueError(tr("db_err_name_exists", name=name))

        if path is None:
            from opensak.settings_store import get_db_dir
            safe_name = "".join(
                c if c.isalnum() or c in "-_ " else "_" for c in name
            ).strip()
            path = get_db_dir() / f"{safe_name}.db"

        path = Path(path)

        # Issue #539 (opfølgning): stien udledes deterministisk af navnet, så
        # uden dette tjek kunne "New database" stille genbruge en efterladt/
        # forældreløs fil på samme sti — fx en fil "remove_from_list()"
        # bevidst har ladet ligge, eller en rest fra en tidligere
        # rename/delete under test. init_db() bruger CREATE TABLE IF NOT
        # EXISTS, så en genbrugt fil ville dukke op med sit gamle indhold og
        # (via navnet) sine gamle kolonneindstillinger, selvom brugeren
        # forventer en tom database. Afvis eksplicit i stedet for at gætte.
        if self._find_by_path(path) or path.exists():
            raise ValueError(
                tr("db_err_target_path_exists", name=name, path=str(path))
            )

        # Sørg for at mappen eksisterer og er skrivbar
        parent = path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ValueError(
                tr("db_err_mkdir_failed", path=parent) + f"\n{e}"
            )

        if not parent.is_dir():
            raise ValueError(tr("db_err_dir_not_found", path=parent))

        # Tjek skriverettigheder ved at prøve at oprette en midlertidig fil
        test_file = parent / f".opensak_write_test_{name}"
        try:
            test_file.touch()
            test_file.unlink()
        except OSError:
            raise ValueError(tr("db_err_no_write_permission", path=parent))

        from opensak.db.database import init_db
        try:
            init_db(db_path=path)
        except Exception as e:
            raise ValueError(
                tr("db_err_create_failed") + f"\n{e}"
            )

        # Genaktiver den nuværende database bagefter
        if self._active:
            init_db(db_path=self._active.path)

        info = DatabaseInfo(name, path)
        self._databases.append(info)
        self._save_to_settings()
        return info

    def open_database(self, path: Path) -> "DatabaseInfo":
        """Åbn en eksisterende .db fil og tilføj til listen."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(tr("db_err_file_not_found", path=path))

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

    def switch_to(self, db_info: "DatabaseInfo") -> None:
        """Skift aktiv database og initialiser den."""
        from opensak.db.database import init_db
        init_db(db_path=db_info.path)  # raises if the file is not a valid DB
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

        safe_name = "".join(
            c if c.isalnum() or c in "-_ " else "_" for c in new_name
        ).strip()
        new_path = old_path.parent / f"{safe_name}.db"

        if new_path != old_path:
            if new_path.exists():
                raise ValueError(
                    tr("db_err_target_path_exists", name=new_name, path=str(new_path))
                )

            # Luk engine FØR filoperationer — undgår låste filer på Windows
            # (samme mønster som delete_database/move_databases_to).
            from opensak.db.database import dispose_engine, init_db
            was_active = (db_info == self._active)
            dispose_engine(old_path)
            gc.collect()
            time.sleep(0.05)

            try:
                old_path.rename(new_path)
                for suffix in ("-shm", "-wal"):
                    side = Path(str(old_path) + suffix)
                    if side.exists():
                        side.rename(Path(str(new_path) + suffix))
            except OSError as e:
                raise ValueError(
                    tr("db_err_rename_failed", name=old_name) + f"\n{e}"
                )

            db_info.path = new_path

            if was_active:
                # Genåbn på den nye sti så aktiv-tilstanden forbliver konsistent.
                init_db(db_path=new_path)

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
                "Kunne ikke migrere kolonneindstillinger ved rename %r -> %r",
                old_name, new_name, exc_info=True,
            )

        db_info.name = new_name
        self._save_to_settings()

    def copy_database(self, db_info: "DatabaseInfo", new_name: str,
                      new_path: Optional[Path] = None) -> "DatabaseInfo":
        """Lav en kopi af en database."""
        if self._find_by_name(new_name):
            raise ValueError(tr("db_err_name_exists", name=new_name))

        if new_path is None:
            from opensak.settings_store import get_db_dir
            safe_name = "".join(
                c if c.isalnum() or c in "-_ " else "_" for c in new_name
            ).strip()
            new_path = get_db_dir() / f"{safe_name}.db"

        shutil.copy2(db_info.path, new_path)
        info = DatabaseInfo(new_name, new_path)
        self._databases.append(info)
        self._save_to_settings()
        return info

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

        from opensak.db.database import dispose_engine

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
                updated_any = True
                continue
            if new_path.exists() and new_path != old_path:
                errors.append(
                    tr("db_err_move_target_exists", name=db_info.name, path=str(new_path))
                )
                continue

            # Luk engine FØR filoperationer — undgår låste filer på Windows
            if db_info == self._active:
                active_engine_disposed = True
            dispose_engine(old_path)
            gc.collect()
            time.sleep(0.05)

            try:
                shutil.copy2(old_path, new_path)
                # Sidecar-filer (SQLite WAL-mode) — kopiér hvis de findes
                for suffix in ("-shm", "-wal"):
                    side = Path(str(old_path) + suffix)
                    if side.exists():
                        shutil.copy2(side, Path(str(new_path) + suffix))
            except OSError as exc:
                errors.append(
                    tr("db_err_move_failed", name=db_info.name, error=str(exc))
                )
                continue

            if delete_originals:
                for suffix in ("", "-shm", "-wal"):
                    f = Path(str(old_path) + suffix)
                    try:
                        if f.exists():
                            f.unlink()
                    except OSError:
                        pass  # ikke kritisk — kopien findes allerede

            db_info.path = new_path
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

        # Slet hovedfilen + WAL/SHM sidekick-filer
        for suffix in ("", "-shm", "-wal"):
            f = Path(str(db_path) + suffix)
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
