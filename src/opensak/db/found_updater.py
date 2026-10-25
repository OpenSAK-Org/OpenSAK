"""
src/opensak/db/found_updater.py — Opdater 'fundet' status i aktiv database
baseret på en reference database (f.eks. 'Mine Fund').

Workflow:
1. Brugeren har en 'Mine Fund' database med alle fundne caches
2. Brugeren kører opdatering mod en anden database (f.eks. 'Sjælland')
3. Alle caches der findes i begge databaser markeres som fundet
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from sqlalchemy import Table, bindparam, create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.lang import tr
from opensak.utils.constants import FOUND_LOG_TYPES
from opensak.utils.types import GcCode


# GC codes per SELECT/UPDATE round (#1095). Well below SQLite's limit on
# bound parameters, large enough to keep the number of round trips small.
_CHUNK_SIZE = 500


@dataclass
class UpdateResult:
    """Resultat af en fund-opdatering."""
    updated:   int = 0       # caches markeret som fundet
    already:   int = 0       # var allerede markeret som fundet
    not_found: int = 0       # i reference DB men ikke i aktiv DB
    errors:    list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"  Marked as found : {self.updated}\n"
            f"  Already found   : {self.already}\n"
            f"  Not in this DB  : {self.not_found}"
        )


def get_found_gc_codes(reference_db_path: Path) -> dict[GcCode, datetime | None]:
    """
    Hent alle GC koder fra reference databasen med den tilhørende fund-dato.

    Fund-datoen er datoen på den ældste "found"-log (se FOUND_LOG_TYPES i
    opensak.utils.constants — dækker "Found it", "Attended" og "Webcam Photo
    Taken") i reference-databasen (My Finds PQ indeholder typisk ét log per
    cache — brugerens eget fund-log).

    Returnerer et dict {gc_code: found_date} — found_date kan være None
    hvis ingen "found"-log findes for cachen.
    """
    url = f"sqlite:///{reference_db_path}"
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    try:
        with engine.connect() as conn:
            # Tjek om logs-tabellen findes i reference-databasen
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
            has_logs = "logs" in tables

            # Hent alle fundne GC koder
            rows = conn.execute(text("SELECT id, gc_code FROM caches")).fetchall()
            id_to_gc: dict[int, str] = {row[0]: row[1] for row in rows if row[1]}

            if not id_to_gc:
                return {}

            found_dates: dict[str, datetime | None] = {gc: None for gc in id_to_gc.values()}

            if has_logs:
                # Hent ældste "found"-log per cache (FOUND_LOG_TYPES: "Found it",
                # "Attended", "Webcam Photo Taken" — se opensak.utils.constants).
                # log_type varierer mellem geocaching.com eksporter i case
                # ("Found it", "Found It", "found it") — brug LOWER() for sikkerhed.
                # My Finds PQ har typisk kun ét log per cache (brugerens eget).
                #
                # Bug #457: forespørgslen matchede tidligere kun 'found it',
                # så webcam-caches og events aldrig fik en found_date herfra.
                found_types_lower = [t.lower() for t in FOUND_LOG_TYPES]
                query = text("""
                    SELECT cache_id, MIN(log_date)
                    FROM logs
                    WHERE LOWER(log_type) IN :found_types
                    GROUP BY cache_id
                """).bindparams(bindparam("found_types", expanding=True))
                log_rows = conn.execute(query, {"found_types": found_types_lower}).fetchall()

                for cache_id, log_date_raw in log_rows:
                    gc = id_to_gc.get(cache_id)
                    if gc and log_date_raw:
                        # Parse dato — SQLite returnerer strenge som
                        # '2009-08-12 19:00:00.000000' (mellemrum, ikke T, med µs)
                        # datetime.fromisoformat() håndterer alle varianter i Python 3.7+
                        try:
                            raw = str(log_date_raw).strip().removesuffix("Z").replace("T", " ")
                            # Fjern overskydende mikrosekunder hvis nødvendigt
                            found_dates[gc] = datetime.fromisoformat(raw).replace(
                                tzinfo=timezone.utc
                            )
                        except ValueError:
                            pass

            return found_dates
    except Exception as e:
        raise RuntimeError(f"Could not read reference database: {e}") from e
    finally:
        engine.dispose()


def update_found_from_reference(reference_db_path: Path) -> UpdateResult:
    """
    Opdater 'found' status i den aktive database baseret på
    GC koder fra reference databasen.

    Parameters
    ----------
    reference_db_path : Path til reference databasen (f.eks. Mine Fund)

    Returns
    -------
    UpdateResult med statistik over opdateringen
    """
    result = UpdateResult()

    # Hent alle GC koder + fund-datoer fra reference databasen
    try:
        found_map = get_found_gc_codes(reference_db_path)
    except RuntimeError as e:
        result.errors.append(tr("found_err_read_ref", error=e.__cause__ or e))
        return result

    if not found_map:
        result.errors.append(tr("found_err_no_caches"))
        return result

    # Opdater aktiv database.
    #
    # Issue #1095: this used to load every cache in the active database as
    # an ORM object and change them one by one. Now it works on the
    # reference DB's GC codes in chunks: one SELECT per chunk tells which
    # codes exist here and which are already found, then two bulk UPDATEs
    # write the changes without loading any rows. Semantics are unchanged:
    #   - not yet found        → found = 1, found_date = ref date (may be
    #                            NULL), counted as `updated`
    #   - already found        → found_date overwritten only when the ref
    #                            DB has a date, counted as `already`
    #   - in ref, not here     → counted as `not_found`
    # Counts come from the SELECT, not from rowcount (unreliable for
    # executemany across drivers). Everything still runs in one transaction.
    # Declarative __table__ is typed as FromClause; update() needs a Table.
    caches = cast(Table, Cache.__table__)
    found_date_type = caches.c.found_date.type
    mark_found = (
        update(caches)
        .where(caches.c.gc_code == bindparam("gc"))
        .values(found=True, found_date=bindparam("d", type_=found_date_type))
    )
    set_found_date = (
        update(caches)
        .where(caches.c.gc_code == bindparam("gc"))
        .values(found_date=bindparam("d", type_=found_date_type))
    )

    codes = list(found_map)
    with get_session() as session:
        for start in range(0, len(codes), _CHUNK_SIZE):
            chunk = codes[start:start + _CHUNK_SIZE]
            rows = session.execute(
                select(caches.c.gc_code, caches.c.found)
                .where(caches.c.gc_code.in_(chunk))
            ).all()

            not_yet: list[str] = []
            already: list[str] = []
            for gc_code, found in rows:
                (already if found else not_yet).append(gc_code)

            result.updated += len(not_yet)
            result.already += len(already)
            result.not_found += len(chunk) - len(rows)

            if not_yet:
                session.execute(
                    mark_found,
                    [{"gc": gc, "d": found_map[gc]} for gc in not_yet],
                )
            dated = [gc for gc in already if found_map[gc] is not None]
            if dated:
                session.execute(
                    set_found_date,
                    [{"gc": gc, "d": found_map[gc]} for gc in dated],
                )

    return result
