"""
src/opensak/gui/refresh_worker.py — background worker for cache-list refresh.

Issue #740: on large databases (200k+ caches), apply_filters_auto() plus the
map's nearest-N query took 6-9+ seconds and ran synchronously on the GUI
thread inside MainWindow._refresh_cache_list(). With no progress feedback,
Windows' "Not Responding" detector (or the user, via Task Manager) would
treat the app as frozen and force-close it during a database switch —
confirmed via a user-submitted opensak.log showing no exception and no
migration activity, just a long gap ending abruptly mid-refresh.

This worker moves only the read-only DB-query portion off the GUI thread.
GUI-only work (cache_table.load_caches(), map_widget.load_caches(), label
updates — none of which are thread-safe to call from here) stays on the GUI
thread, in MainWindow's slot connected to `result`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from opensak.db import custom_fields
from opensak.db.database import get_engine, get_session
from opensak.db.models import Cache
from opensak.filters.engine import FilterSet, SortSpec, apply_filters_auto
from opensak.gui.dialogs.column_dialog import custom_column_name


class RefreshWorker(QThread):
    """Runs apply_filters_auto() (table + map queries) on a background thread.

    `generation` is an opaque int the caller assigns and gets back unchanged
    in both signals. MainWindow uses it to discard results from a worker
    that's since been superseded by a newer refresh request (e.g. the user
    switched databases twice in quick succession) — simpler and safer than
    trying to cancel/terminate an in-flight QThread mid-query. See
    MainWindow._refresh_cache_list() / _on_refresh_result().
    """

    # (generation, table_caches, map_caches, custom values or None — see run())
    result = Signal(int, list, list, object)
    error  = Signal(int, str)          # (generation, error message)

    def __init__(
        self,
        generation: int,
        filterset: FilterSet,
        sort: SortSpec,
        columns: frozenset,
        fetch_map: bool,
        map_max_caches: int,
    ):
        super().__init__()
        self.generation = generation
        self.filterset = filterset
        self.sort = sort
        self.columns = columns
        self.fetch_map = fetch_map
        self.map_max_caches = map_max_caches

    def run(self) -> None:
        # Own session, own connection — never share a Session across threads.
        # Objects returned here are safe to hand to the GUI thread afterwards:
        # apply_filters_auto()/apply_filters() already joinedload() everything
        # the table/map need and raiseload() the rest (see filters/engine.py,
        # #898), so nothing triggers a lazy load once this session closes — the
        # exact same closed-session usage the previous synchronous code
        # already relied on (caches were read after `with get_session()`
        # had already exited).
        try:
            with get_session() as session:
                table_caches: list[Cache] = apply_filters_auto(
                    session, self.filterset, self.sort, columns=self.columns,
                )
                if not self.fetch_map:
                    map_caches: list[Cache] = []
                elif not self.map_max_caches:
                    map_caches = table_caches
                else:
                    map_caches = apply_filters_auto(
                        session, self.filterset,
                        SortSpec("distance", ascending=True),
                        limit=self.map_max_caches, push_limit=True,
                    )
                custom = self._custom_values(session)
        except Exception as exc:  # noqa: BLE001 — surfaced to the GUI thread, never re-raised here
            logger_msg = f"{type(exc).__name__}: {exc}"
            self.error.emit(self.generation, logger_msg)
            return
        self.result.emit(self.generation, table_caches, map_caches, custom)

    def _custom_values(self, session) -> Optional[custom_fields.Values]:
        """#1028: the values of the visible custom columns, read here so the
        table doesn't have to on the GUI thread; None without such columns."""
        names = [n for n in (custom_column_name(c) for c in self.columns) if n is not None]
        if not names:
            return None
        dbapi = session.connection().connection.dbapi_connection
        return custom_fields.load_values(dbapi, Path(get_engine().url.database or ""), names)
