"""
tests/unit-tests/test_macro_sql_1398.py — long SQL statements keep the
windows alive and can be cancelled (#1398).

A macro runs on the GUI thread. opensak.sql(), sql_each() and sql_write()
ran each statement as one blocking SQLite call, so a slow statement froze
the windows, and cancelling the macro only took effect after it (or after
QUERY_TIMEOUT_S). The connections' progress handler now also calls a tick
(sql.Tick) that keeps the windows alive and stops the statement once the
macro is cancelled.
"""

from __future__ import annotations

import threading
import time

import pytest

from opensak.db.database import get_engine, get_session
from opensak.db.models import Cache
from opensak.macro import MacroError, MacroRuntime, WriteApproval
from opensak.macro import db_access, sql
from opensak.utils import flags


def counting(n: str = "5000000") -> str:
    """A query that takes a while (about 0.6 s for 5 million) and returns
    {c = n}; without a limit it runs until it times out."""
    where = f" WHERE i < {n}" if n else ""
    return (f"WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n{where}) "
            "SELECT COUNT(*) AS c FROM n")


ENDLESS = counting("")


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        for code in ("GCSQ1", "GCSQ2", "GCSQ3"):
            s.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                        latitude=47.0, longitude=8.0, user_sort=0))


@pytest.fixture(autouse=True)
def _write(monkeypatch):
    monkeypatch.setattr(flags, "lua_macro_write", True)
    db_access.reset_session()
    yield
    db_access.reset_session()


class Window:
    def __init__(self):
        self.updates: list[tuple] = []

    def update(self, value, text):
        self.updates.append((value, text))
        return True

    def close(self):
        pass


class Host:
    """A MacroHost counting keep_alive() calls; it lets the macro write."""

    def __init__(self):
        self.kept_alive = 0
        self.windows: list[Window] = []

    def keep_alive(self):
        self.kept_alive += 1

    def progress(self, title, total):
        self.windows.append(Window())
        return self.windows[-1]

    def approve_macro_write(self, macro, change=None, editing=False):
        return WriteApproval.SESSION

    def caches_changed(self, codes, added):
        pass

    def caches_removed(self):
        pass

    def clear_filter(self):
        pass

    def cache_count(self):
        return 3

    def end_macro(self):
        pass


def run(host, source: str, out: list | None = None) -> list[str]:
    out = [] if out is None else out
    MacroRuntime(host, output=out.append).run(source)
    return out


def cancel_during(rt: MacroRuntime, source: str) -> float:
    """Run *source*, cancelling it until the run ends (a single cancel
    could land before run() resets the flag, as in test_macro_helpers);
    the seconds it took."""
    done = threading.Event()

    def keep_cancelling() -> None:
        while not done.wait(0.05):
            rt.cancel()

    canceller = threading.Thread(target=keep_cancelling, daemon=True)
    canceller.start()
    started = time.monotonic()
    try:
        with pytest.raises(MacroError, match="macro cancelled"):
            rt.run(source)
    finally:
        done.set()
        canceller.join()
    return time.monotonic() - started


# ── Keeping the windows alive ───────────────────────────────────────────────


def test_a_long_query_keeps_the_windows_alive():
    host = Host()
    out = run(host, f'print(opensak.sql("{counting()}")[1].c)')
    assert out == ["5000000"]                       # the result is unchanged
    assert host.kept_alive >= 5


def test_sql_each_keeps_the_windows_alive():
    host = Host()
    out = run(host, f'for row in opensak.sql_each("{counting()}") do print(row.c) end')
    assert out == ["5000000"] and host.kept_alive >= 5


def test_a_long_write_keeps_the_windows_alive():
    host = Host()
    run(host, 'opensak.sql_write("UPDATE caches SET user_sort = '
              f'({counting("2000000")}) WHERE gc_code LIKE \'GCSQ%\'")')
    with get_session() as s:
        assert {c.user_sort for c in s.query(Cache).filter(Cache.gc_code.like("GCSQ%"))} \
            == {2000000}
    assert host.kept_alive >= 5


def test_an_open_progress_window_is_repainted_instead():
    host = Host()
    run(host, f'local p = opensak.progress("Working", 10)\np:update(3, "three")\n'
              f'opensak.sql("{counting()}")\np:close()')
    [window] = host.windows
    assert window.updates[0] == (3, "three")
    repaints = window.updates[1:]
    assert len(repaints) >= 5 and set(repaints) == {(None, "")}   # value and text kept
    assert host.kept_alive == 0


# ── Cancelling ──────────────────────────────────────────────────────────────


def test_cancel_stops_a_query():
    rt = MacroRuntime(Host(), output=lambda _t: None)
    assert cancel_during(rt, f'opensak.sql("{ENDLESS}")') < 2   # times out after 60 s


def test_cancel_stops_sql_each():
    rt = MacroRuntime(Host(), output=lambda _t: None)
    assert cancel_during(rt, f'for row in opensak.sql_each("{ENDLESS}") do end') < 2


def _user_sorts() -> dict[str, int | None]:
    with get_session() as s:
        return {c.gc_code: c.user_sort for c in s.query(Cache).filter(Cache.gc_code.like("GCSQ%"))}


def test_cancel_stops_a_write_and_rolls_it_back():
    before = _user_sorts()
    rt = MacroRuntime(Host(), output=lambda _t: None)
    # GCSQ2 is written first; the endless subquery for GCSQ3 is then cancelled.
    took = cancel_during(rt, 'opensak.sql_write("UPDATE caches SET user_sort = CASE gc_code '
                             f'WHEN \'GCSQ2\' THEN -5 ELSE ({ENDLESS}) END '
                             'WHERE gc_code IN (\'GCSQ2\', \'GCSQ3\')")')
    assert took < 2
    assert _user_sorts() == before                  # nothing of the statement is kept
    rt.run('opensak.sql_write("UPDATE caches SET user_sort = 7 WHERE gc_code = \'GCSQ1\'")')
    with get_session() as s:                        # the next run writes as before
        assert s.query(Cache).filter_by(gc_code="GCSQ1").one().user_sort == 7


def test_pcall_cannot_catch_the_cancel():
    out: list[str] = []
    rt = MacroRuntime(Host(), output=out.append)
    cancel_during(rt, f'print(pcall(opensak.sql, "{ENDLESS}"))\nprint("went on")')
    assert out == []


# ── sql.py on its own ───────────────────────────────────────────────────────


def test_a_tick_returning_true_stops_the_statement_once():
    stop = [True]
    db = sql.ReadOnlyDatabase(get_engine().url.database, tick=lambda: stop[0])
    try:
        with pytest.raises(sql.SqlError, match="stopped: the macro was cancelled"):
            db.query(counting())
        stop[0] = False
        assert db.query(counting("1000")) == [{"c": 1000}]
    finally:
        db.close()


def test_the_timeout_still_applies_with_a_tick():
    db = sql.ReadOnlyDatabase(get_engine().url.database, timeout_s=0.05, tick=lambda: False)
    try:
        with pytest.raises(sql.SqlError, match="took longer than 0.05 s"):
            db.query(ENDLESS)
    finally:
        db.close()
