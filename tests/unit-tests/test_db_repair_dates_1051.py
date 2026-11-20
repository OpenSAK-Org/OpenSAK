"""
tests/unit-tests/test_db_repair_dates_1051.py — database repair, phase 2
(#1051): dates SQLAlchemy can't read (a cache with one fails to load), dates
in another format, and dates before May 2000 or in the future.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.db import companion, repair, repair_dates
from opensak.db.database import dispose_engine, get_session, init_db
from opensak.db.models import Cache, Log
from opensak.db.repair_dates import Detail
from opensak.gui.dialogs import database_repair_dialog as drd
from opensak.lang import load_language, tr
from opensak.utils import flags


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", False)
    companion.reset()
    yield
    dispose_engine()
    companion.reset()


def _database(path: Path) -> Path:
    """Caches GC1 (with a log), GC2 and the event GC3, all dates valid."""
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path=path)
    with get_session() as s:
        gc1 = Cache(gc_code="GC1", name="One", cache_type="Traditional Cache",
                    latitude=47.0, longitude=8.0, hidden_date=datetime(2010, 5, 1))
        s.add_all([gc1,
                   Cache(gc_code="GC2", name="Two", cache_type="Traditional Cache",
                         latitude=47.1, longitude=8.1),
                   Cache(gc_code="GC3", name="Event", cache_type="Event Cache",
                         latitude=47.2, longitude=8.2)])
        s.flush()
        s.add(Log(cache_id=gc1.id, log_id="1", log_type="Found it",
                  log_date=datetime(2020, 1, 2, 3, 4, 5)))
    dispose_engine(path)
    return path


def _sql(path: Path, *statements: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(str(path))
    try:
        with conn:
            for statement in statements:
                conn.execute(statement, params) if params else conn.execute(statement)
    finally:
        conn.close()


def _value(path: Path, sql: str):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def _counts(result: repair.ScanResult) -> dict[str, int]:
    return {f.key: f.count for f in result.problems}


def _load_all(path: Path) -> None:
    """Load every cache and log like the GUI does; raises on a bad date."""
    init_db(db_path=path)
    with get_session() as s:
        for cache in s.query(Cache).all():
            _ = [lg.log_date for lg in cache.logs]
    dispose_engine(path)


class TestNormalize:
    @pytest.mark.parametrize("value, expected", [
        ("2024-03-05", "2024-03-05 00:00:00.000000"),
        ("2024-03-05 10:11:12", "2024-03-05 10:11:12.000000"),
        ("2024-03-05T10:11:12", "2024-03-05 10:11:12.000000"),
        ("2024-03-05T10:11:12Z", "2024-03-05 10:11:12.000000"),
        ("2024-03-05T10:11:12.5", "2024-03-05 10:11:12.500000"),
        # Converted to UTC, as the GPX importer does.
        ("2024-03-05T01:00:00+02:00", "2024-03-04 23:00:00.000000"),
        (" 2024-03-05 ", "2024-03-05 00:00:00.000000"),
        ("2024-3-5", "2024-03-05 00:00:00.000000"),
        ("2024/03/05 7:08", "2024-03-05 07:08:00.000000"),
        ("05.03.2024", "2024-03-05 00:00:00.000000"),
        ("5.3.2024 10:11", "2024-03-05 10:11:00.000000"),
        (20240305, "2024-03-05 00:00:00.000000"),
        (20240305.0, "2024-03-05 00:00:00.000000"),
        (b"2024-03-05", "2024-03-05 00:00:00.000000"),
    ])
    def test_readable(self, value, expected):
        assert repair_dates.normalize(value) == expected

    @pytest.mark.parametrize("value", [
        "", "   ", "abc", "2024-02-30", "0000-00-00 00:00:00", "2024-13-01",
        "2024-03-05 25:00:00", 45000, 45000.5, 1709632800, b"\xff\xfe", True,
    ])
    def test_unreadable(self, value):
        assert repair_dates.normalize(value) is None

    def test_years_before_1000_are_padded(self):
        assert repair_dates.to_stored(datetime(999, 1, 2)) == "0999-01-02 00:00:00.000000"


class TestScan:
    def test_valid_dates_are_not_reported(self, tmp_path):
        assert repair.scan(_database(tmp_path / "Ok.db")).problems == ()

    def test_finds_wrong_formats_and_unreadable_dates(self, tmp_path):
        db = _database(tmp_path / "Dates.db")
        _sql(db,
             "UPDATE caches SET hidden_date = '2011-04-03T10:00:00Z' WHERE gc_code = 'GC1'",
             "UPDATE caches SET found_date = '20240305' WHERE gc_code = 'GC2'",
             "UPDATE logs SET log_date = 'yesterday'",
             "UPDATE caches SET last_updated = '2024-02-30 00:00:00.000000' WHERE gc_code = 'GC2'")
        result = repair.scan(db)
        assert _counts(result) == {repair.DATE_FORMAT: 2, repair.DATE_UNREADABLE: 2}
        by_key = {f.key: f for f in result.findings}
        assert by_key[repair.DATE_FORMAT].details == (
            Detail("GC1", "hidden_date", "2011-04-03T10:00:00Z"),
            Detail("GC2", "found_date", "20240305"))
        assert by_key[repair.DATE_UNREADABLE].details == (
            Detail("GC1", "logs.log_date", "yesterday"),
            Detail("GC2", "last_updated", "2024-02-30 00:00:00.000000"))
        assert by_key[repair.DATE_FORMAT].fixable and by_key[repair.DATE_UNREADABLE].fixable

    def test_too_early_and_future_dates_are_report_only(self, tmp_path):
        db = _database(tmp_path / "Dates.db")
        future = repair_dates.to_stored(datetime.now() + timedelta(days=30))
        _sql(db, "UPDATE logs SET log_date = '1999-12-31 00:00:00.000000'")
        _sql(db, "UPDATE caches SET hidden_date = ? WHERE gc_code IN ('GC2', 'GC3')",
             params=(future,))
        result = repair.scan(db)
        # GC3 is an event: its placed date is the event date.
        assert _counts(result) == {repair.DATE_RANGE: 2}
        finding = next(f for f in result.findings if f.check == repair.DATE_RANGE)
        assert not finding.fixable
        assert finding.details == (Detail("GC1", "logs.log_date", "1999-12-31 00:00:00.000000"),
                                   Detail("GC2", "hidden_date", future))

    def test_a_maze_exhibit_may_be_in_the_future(self, tmp_path):
        # #1051 feedback: e.g. GCBGCCC, a GPS Adventures Maze, placed on the
        # day of the exhibit.
        db = _database(tmp_path / "Dates.db")
        future = repair_dates.to_stored(datetime.now() + timedelta(days=300))
        _sql(db, "UPDATE caches SET cache_type = 'GPS Adventures Maze', hidden_date = ? "
                 "WHERE gc_code = 'GC2'", params=(future,))
        assert repair.scan(db).problems == ()
        assert "GPS Adventures Maze" in repair_dates.DATED_CACHE_TYPES

    def test_first_geocaching_day_is_fine(self, tmp_path):
        db = _database(tmp_path / "Dates.db")
        _sql(db, "UPDATE caches SET hidden_date = '2000-05-03 00:00:00.000000'")
        assert repair.scan(db).problems == ()


class TestRepair:
    def test_a_cache_with_a_bad_date_loads_again(self, tmp_path):
        db = _database(tmp_path / "Dates.db")
        _sql(db, "UPDATE caches SET found_date = '20240305' WHERE gc_code = 'GC2'",
             "UPDATE logs SET log_date = 'yesterday'")
        with pytest.raises((ValueError, TypeError)):
            _load_all(db)
        dispose_engine(db)
        keys = {f.key for f in repair.scan(db).fixable}
        assert repair.repair(db, keys).fixed == {repair.DATE_FORMAT: 1,
                                                 repair.DATE_UNREADABLE: 1}
        _load_all(db)
        assert _value(db, "SELECT found_date FROM caches WHERE gc_code = 'GC2'") \
            == "2024-03-05 00:00:00.000000"
        assert _value(db, "SELECT log_date FROM logs") is None
        assert repair.scan(db).problems == ()

    def test_not_null_columns_get_now(self, tmp_path):
        db = _database(tmp_path / "Dates.db")
        _sql(db, "UPDATE caches SET imported_at = 'broken' WHERE gc_code = 'GC1'")
        before = repair_dates.now_stored()
        repair.repair(db, {repair.DATE_UNREADABLE})
        stamp = _value(db, "SELECT imported_at FROM caches WHERE gc_code = 'GC1'")
        assert stamp >= before
        assert repair.scan(db).problems == ()

    def test_only_the_ticked_date_fix(self, tmp_path):
        db = _database(tmp_path / "Dates.db")
        _sql(db, "UPDATE caches SET hidden_date = '2011-04-03' WHERE gc_code = 'GC1'",
             "UPDATE logs SET log_date = 'yesterday'")
        assert repair.repair(db, {repair.DATE_FORMAT}).fixed == {repair.DATE_FORMAT: 1}
        assert _counts(repair.scan(db)) == {repair.DATE_UNREADABLE: 1}
        assert _value(db, "SELECT log_date FROM logs") == "yesterday"

    def test_dates_of_other_tables_are_checked_too(self, tmp_path):
        db = _database(tmp_path / "Dates.db")
        _sql(db, "CREATE TABLE extra (id INTEGER PRIMARY KEY, seen DATETIME)",
             "INSERT INTO extra (seen) VALUES ('2024-03-05')")
        result = repair.scan(db)
        finding = next(f for f in result.findings if f.check == repair.DATE_FORMAT)
        assert finding.count == 1 and finding.details == (Detail("?", "extra.seen", "2024-03-05"),)
        repair.repair(db, {repair.DATE_FORMAT})
        assert _value(db, "SELECT seen FROM extra") == "2024-03-05 00:00:00.000000"

    def test_report_only_dates_are_left_alone(self, tmp_path):
        db = _database(tmp_path / "Dates.db")
        _sql(db, "UPDATE logs SET log_date = '1999-12-31 00:00:00.000000'")
        assert repair.repair(db, {repair.DATE_RANGE}).fixed == {}
        assert _value(db, "SELECT log_date FROM logs") == "1999-12-31 00:00:00.000000"


class TestDialog:
    def test_lists_the_date_checks(self, qtbot, tmp_path):
        db = _database(tmp_path / "Dates.db")
        _sql(db, "UPDATE caches SET hidden_date = '2011-04-03' WHERE gc_code = 'GC1'",
             "UPDATE logs SET log_date = '1999-12-31 00:00:00.000000'")
        dlg = drd.DatabaseRepairDialog(SimpleNamespace(name="Dates", path=db))
        qtbot.addWidget(dlg)
        qtbot.waitUntil(lambda: not dlg.is_running() and dlg.scan_result is not None,
                        timeout=10000)
        rows = {}
        for i in range(dlg._tree.topLevelItemCount()):
            item = dlg._tree.topLevelItem(i)
            assert item is not None
            rows[item.data(0, drd.KEY_ROLE)] = item
        assert rows[repair.DATE_FORMAT].text(0) == tr("repair_chk_date_format")
        assert rows[repair.DATE_FORMAT].text(2) == tr("repair_fix_convert")
        assert rows[repair.DATE_FORMAT].text(3) == "GC1 hidden_date 2011-04-03"
        assert "GC1 hidden_date 2011-04-03" in rows[repair.DATE_FORMAT].toolTip(3)
        assert rows[repair.DATE_RANGE].text(2) == tr("repair_report_only")
        assert dlg.checked_keys() == {repair.DATE_FORMAT}

    def test_unreadable_dates_are_not_ticked(self, qtbot, tmp_path):
        """#1051 feedback: clearing loses the value (03/05/2024 is ambiguous),
        so the user opts in; the conversion stays ticked."""
        db = _database(tmp_path / "Dates.db")
        _sql(db, "UPDATE caches SET hidden_date = '2011-04-03' WHERE gc_code = 'GC1'",
             "UPDATE logs SET log_date = '03/05/2024'")
        dlg = drd.DatabaseRepairDialog(SimpleNamespace(name="Dates", path=db))
        qtbot.addWidget(dlg)
        qtbot.waitUntil(lambda: not dlg.is_running() and dlg.scan_result is not None,
                        timeout=10000)
        rows = {}
        for i in range(dlg._tree.topLevelItemCount()):
            item = dlg._tree.topLevelItem(i)
            assert item is not None
            rows[item.data(0, drd.KEY_ROLE)] = item
        unreadable = rows[repair.DATE_UNREADABLE]
        assert unreadable.flags() & drd.Qt.ItemFlag.ItemIsUserCheckable
        assert unreadable.checkState(0) == drd.Qt.CheckState.Unchecked
        assert dlg.checked_keys() == {repair.DATE_FORMAT}
        unreadable.setCheckState(0, drd.Qt.CheckState.Checked)
        assert dlg.checked_keys() == {repair.DATE_FORMAT, repair.DATE_UNREADABLE}


class TestPlaceholder:
    """#1051 feedback: GSAK's "no date" 1970-01-01 (e.g. Waypoints.cDate,
    copied by the GSAK import) is fixable, with the waypoint in the example."""

    def _with_waypoints(self, path: Path) -> Path:
        db = _database(path)
        _sql(db,
             "INSERT INTO waypoints (cache_id, parent_gc_code, prefix, name, wp_date) "
             "SELECT id, gc_code, 'S1', 'Stage', "
             "'1970-01-01 00:00:00.000000' FROM caches WHERE gc_code = 'GC1'",
             # In another format: converted, it is the placeholder too.
             "INSERT INTO waypoints (cache_id, parent_gc_code, prefix, name, wp_date) "
             "SELECT id, gc_code, 'S2', 'Stage', "
             "'1970-01-01' FROM caches WHERE gc_code = 'GC1'")
        return db

    def test_reported_as_placeholder_not_as_too_early(self, tmp_path):
        result = repair.scan(self._with_waypoints(tmp_path / "Dates.db"))
        assert _counts(result) == {repair.DATE_PLACEHOLDER: 2}
        finding = next(f for f in result.findings if f.check == repair.DATE_PLACEHOLDER)
        assert finding.fixable
        assert finding.details == (
            Detail("GC1", "waypoints.wp_date S1", "1970-01-01 00:00:00.000000"),
            Detail("GC1", "waypoints.wp_date S2", "1970-01-01"))

    def test_fix_clears_it(self, tmp_path):
        db = self._with_waypoints(tmp_path / "Dates.db")
        _sql(db, "UPDATE caches SET imported_at = '1970-01-01 00:00:00.000000' "
                 "WHERE gc_code = 'GC2'")
        assert repair.repair(db, {repair.DATE_PLACEHOLDER}).fixed == {repair.DATE_PLACEHOLDER: 3}
        assert _value(db, "SELECT count(*) FROM waypoints WHERE wp_date IS NULL") == 2
        # imported_at is NOT NULL: now instead.
        assert _value(db, "SELECT imported_at FROM caches WHERE gc_code = 'GC2'") > "2000"
        assert repair.scan(db).problems == ()

    def test_other_old_dates_are_still_too_early(self, tmp_path):
        db = self._with_waypoints(tmp_path / "Dates.db")
        _sql(db, "UPDATE waypoints SET wp_date = '1970-01-02 00:00:00.000000' "
                 "WHERE prefix = 'S1'")
        counts = _counts(repair.scan(db))
        assert counts == {repair.DATE_PLACEHOLDER: 1, repair.DATE_RANGE: 1}
