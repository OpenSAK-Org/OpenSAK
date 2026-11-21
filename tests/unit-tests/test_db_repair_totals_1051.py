"""
tests/unit-tests/test_db_repair_totals_1051.py — database repair, phase 3
(#1051): the counts and dates derived from a cache's logs, waypoints and
trackables, and the found mark from the user's own found logs.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from opensak.db import companion, repair, repair_totals
from opensak.db.database import dispose_engine, get_session, init_db
from opensak.db.models import Cache, Log, Trackable, Waypoint
from opensak.db.repair_dates import Detail
from opensak.gui.dialogs import database_repair_dialog as drd
from opensak.lang import load_language, tr
from opensak.utils import flags

ME = ("4711", "Me")
D1 = datetime(2021, 3, 4, 12, 0, 0)      # whole hours: a fixed time, calendar date as is
D2 = datetime(2023, 6, 10, 12, 0, 0)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", False)
    companion.reset()
    yield
    dispose_engine()
    companion.reset()


def _database(path: Path) -> Path:
    """GC1: two logs (one my own find), a waypoint and a trackable, with the
    totals as the importer sets them; GC2: nothing, found by hand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path=path)
    with get_session() as s:
        gc1 = Cache(gc_code="GC1", name="One", cache_type="Traditional Cache",
                    latitude=47.0, longitude=8.0, found=True, found_date=datetime(2021, 3, 4),
                    log_count=2, found_log_count=1, last_log_date=D2, last_found_date=D2,
                    last_four_logs="2023-06-10T12:00:00\tFound it\tOther\n"
                                   "2021-03-04T12:00:00\tFound it\tMe",
                    waypoint_count=1, trackable_count=1, find_count=99)
        gc2 = Cache(gc_code="GC2", name="Lab", cache_type="Lab Cache",
                    latitude=47.1, longitude=8.1, found=True)
        s.add_all([gc1, gc2])
        s.flush()
        s.add_all([
            Log(cache_id=gc1.id, log_id="1", log_type="Found it", finder="Me",
                finder_id="4711", log_date=D1),
            Log(cache_id=gc1.id, log_id="2", log_type="Found it", finder="Other",
                finder_id="9", log_date=D2),
            Waypoint(cache_id=gc1.id, parent_gc_code="GC1", prefix="PK", name="Parking"),
            Trackable(cache_id=gc1.id, name="TB"),
        ])
    dispose_engine(path)
    return path


def _sql(path: Path, *statements: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        with conn:
            for statement in statements:
                conn.execute(statement)
    finally:
        conn.close()


def _row(path: Path, sql: str):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchone()
    finally:
        conn.close()


def _finding(result: repair.ScanResult, check: str) -> repair.Finding:
    return next(f for f in result.findings if f.check == check)


class TestScan:
    def test_matching_totals_are_not_reported(self, tmp_path):
        assert repair.scan(_database(tmp_path / "Ok.db"), account=ME).problems == ()

    def test_every_derived_column(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET log_count = 12, last_log_date = NULL, "
                 "last_found_date = '2020-01-01 00:00:00.000000', last_four_logs = NULL, "
                 "waypoint_count = 0, trackable_count = 5 WHERE gc_code = 'GC1'")
        finding = _finding(repair.scan(db, account=ME), repair.TOTALS)
        assert finding.count == 1 and finding.fixable
        assert finding.details == (
            Detail("GC1", "last_found_date",
                   "2020-01-01 00:00:00.000000 → 2023-06-10 12:00:00.000000"),
            Detail("GC1", "last_four_logs", "NULL → 2023-06-10T12:00:00 Found it Other | "
                                            "2021-03-04T12:00:00 Found it Me"),
            Detail("GC1", "last_log_date", "NULL → 2023-06-10 12:00:00.000000"),
            Detail("GC1", "log_count", "12 → 2"),
            Detail("GC1", "trackable_count", "5 → 1"),
            Detail("GC1", "waypoint_count", "0 → 1"),
        )

    def test_logs_with_the_same_date_in_another_order_are_fine(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE logs SET log_date = '2023-06-10 12:00:00.000000'",
             "UPDATE caches SET last_found_date = '2023-06-10 12:00:00.000000', "
             "last_four_logs = '2023-06-10T12:00:00' || char(9) || 'Found it' || char(9) || 'Me'"
             " || char(10) || '2023-06-10T12:00:00' || char(9) || 'Found it' || char(9) || "
             "'Other' WHERE gc_code = 'GC1'")
        assert repair.scan(db, account=ME).problems == ()

    def test_own_found_log_count_is_raised_never_lowered(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET found_log_count = 0 WHERE gc_code = 'GC1'")
        finding = _finding(repair.scan(db, account=ME), repair.TOTALS)
        assert finding.details == (Detail("GC1", "found_log_count", "0 → 1"),)
        # More finds than logs (a Pocket Query brings only some): kept.
        _sql(db, "UPDATE caches SET found_log_count = 3 WHERE gc_code = 'GC1'")
        assert repair.scan(db, account=ME).problems == ()

    def test_own_log_matched_by_name_too(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET found_log_count = 0 WHERE gc_code = 'GC1'")
        finding = _finding(repair.scan(db, account=(None, "me")), repair.TOTALS)
        assert finding.details == (Detail("GC1", "found_log_count", "0 → 1"),)

    def test_found_mark(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET found = 0, found_date = NULL WHERE gc_code = 'GC1'")
        finding = _finding(repair.scan(db, account=ME), repair.FOUND_MARK)
        assert finding.fixable
        assert finding.details == (Detail("GC1", "found", "0 → 1, found_date 2021-03-04"),)

    def test_without_an_account_own_logs_are_not_checked(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET found = 0, found_log_count = 0 WHERE gc_code = 'GC1'")
        assert repair.scan(db, account=None).problems == ()
        assert repair.scan(db, account=("", " ")).problems == ()

    def test_found_without_an_own_log_is_kept(self, tmp_path):
        # GC2 is a Lab marked found by hand: no log behind it, never cleared.
        result = repair.scan(_database(tmp_path / "T.db"), account=ME)
        assert result.problems == ()

    def test_logs_without_a_cache_are_skipped(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "PRAGMA foreign_keys=OFF",
             "INSERT INTO logs (cache_id, log_id, log_type) VALUES (0, 'x', 'Note')",
             "INSERT INTO logs (cache_id, log_id, log_type) VALUES (99999, 'y', 'Note')")
        totals = _finding(repair.scan(db, account=ME), repair.TOTALS)
        assert totals.count == 0


class TestRepair:
    def test_recalculates_and_a_second_run_finds_nothing(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET log_count = 12, last_log_date = NULL, last_four_logs = NULL, "
                 "waypoint_count = 0, trackable_count = 5, found_log_count = 0 "
                 "WHERE gc_code = 'GC1'")
        assert repair.repair(db, {repair.TOTALS}, account=ME).fixed == {repair.TOTALS: 1}
        assert _row(db, "SELECT log_count, last_log_date, waypoint_count, trackable_count, "
                        "found_log_count, last_four_logs, find_count FROM caches "
                        "WHERE gc_code = 'GC1'") == (
            2, "2023-06-10 12:00:00.000000", 1, 1, 1,
            "2023-06-10T12:00:00\tFound it\tOther\n2021-03-04T12:00:00\tFound it\tMe", 99)
        assert repair.scan(db, account=ME).problems == ()

    def test_recalculated_values_load(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET last_log_date = NULL WHERE gc_code = 'GC1'")
        repair.repair(db, {repair.TOTALS}, account=ME)
        init_db(db_path=db)
        with get_session() as s:
            assert s.query(Cache).filter_by(gc_code="GC1").one().last_log_date == D2

    def test_marks_found_with_the_date_of_the_own_log(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET found = 0, found_date = NULL WHERE gc_code = 'GC1'")
        assert repair.repair(db, {repair.FOUND_MARK}, account=ME).fixed == {repair.FOUND_MARK: 1}
        assert _row(db, "SELECT found, found_date FROM caches WHERE gc_code = 'GC1'") == (
            1, "2021-03-04 00:00:00.000000")

    def test_marking_found_keeps_a_found_date(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET found = 0, found_date = '2021-03-05 00:00:00.000000' "
                 "WHERE gc_code = 'GC1'")
        repair.repair(db, {repair.FOUND_MARK}, account=ME)
        assert _row(db, "SELECT found, found_date FROM caches WHERE gc_code = 'GC1'") == (
            1, "2021-03-05 00:00:00.000000")

    def test_only_the_ticked_fix(self, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET found = 0, log_count = 7 WHERE gc_code = 'GC1'")
        assert repair.repair(db, {repair.FOUND_MARK}, account=ME).fixed == {repair.FOUND_MARK: 1}
        assert _row(db, "SELECT log_count FROM caches WHERE gc_code = 'GC1'") == (7,)

    def test_after_deleting_orphans_and_clearing_dates(self, tmp_path):
        # The totals are recalculated from what the other fixes leave.
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE logs SET log_date = 'garbage' WHERE log_id = '2'")
        keys = {f.key for f in repair.scan(db, account=ME).fixable}
        assert keys == {repair.DATE_UNREADABLE, repair.TOTALS}
        repair.repair(db, keys, account=ME)
        assert _row(db, "SELECT last_log_date, last_found_date, log_count FROM caches "
                        "WHERE gc_code = 'GC1'") == (
            "2021-03-04 12:00:00.000000", "2021-03-04 12:00:00.000000", 2)
        assert repair.scan(db, account=ME).problems == ()


class TestDialog:
    def test_uses_the_account_from_settings(self, qtbot, tmp_path):
        db = _database(tmp_path / "T.db")
        _sql(db, "UPDATE caches SET found = 0 WHERE gc_code = 'GC1'")
        with patch.object(drd, "_account", return_value=ME):
            dlg = drd.DatabaseRepairDialog(SimpleNamespace(name="T", path=db))
            qtbot.addWidget(dlg)
            qtbot.waitUntil(lambda: not dlg.is_running() and dlg.scan_result is not None,
                            timeout=10000)
        item = dlg._tree.topLevelItem(0)
        assert item is not None
        assert item.text(0) == tr("repair_chk_found_mark")
        assert item.text(2) == tr("repair_fix_mark_found")
        assert item.text(3) == "GC1 found 0 → 1"
        assert dlg.checked_keys() == {repair.FOUND_MARK}


def test_shown_keeps_tab_separated_copies_intact():
    assert repair_totals.shown("a\tb\nc\td") == "a b | c d"
    assert repair_totals.shown(None) == "NULL"
