"""
tests/unit-tests/test_db_repair_1051.py — database repair, phase 1 (#1051):
the data checks of db/repair.py on a database with deliberately broken
rows, the fixes, a second run that finds nothing, and the Repair dialog
opened from the Maintenance dialog.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from opensak.db import companion, maintenance, repair
from opensak.db.database import dispose_engine, get_session, init_db
from opensak.db.models import Cache, Log, UserNote, Waypoint
from opensak.db.repair_dates import Detail
from opensak.gui.dialogs import database_maintenance_dialog as dmd
from opensak.gui.dialogs import database_repair_dialog as drd
from opensak.lang import load_language, tr
from opensak.utils import flags

USER_DATE = "2025-01-02 03:04:05.000000"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", True)
    companion.reset()
    yield
    dispose_engine()
    companion.reset()


def _healthy(path: Path) -> Path:
    """A database without problems: two caches, a custom waypoint linked to
    GC1, a waypoint, a log and a correction to the listed coordinates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path=path)
    with get_session() as s:
        gc1 = Cache(gc_code="GC1", name="One", cache_type="Traditional Cache",
                    latitude=47.0, longitude=8.0, difficulty=1.5, terrain=5.0,
                    log_count=1, waypoint_count=1)
        gc2 = Cache(gc_code="GC2", name="Two", cache_type="Mystery Cache",
                    latitude=47.1, longitude=8.1, difficulty=2.0, terrain=2.0)
        cw = Cache(gc_code="CW1", name="Parking", cache_type="Custom",
                   latitude=47.2, longitude=8.2, parent_gc_code="GC1")
        s.add_all([gc1, gc2, cw])
        s.flush()
        s.add(Waypoint(cache_id=gc1.id, parent_gc_code="GC1", prefix="PK", name="Parking"))
        s.add(Log(cache_id=gc1.id, log_id="1", log_type="Found it", finder="me"))
        # Corrected to the listed coordinates: a real choice, not a problem.
        s.add(UserNote(cache_id=gc2.id, corrected_lat=47.1, corrected_lon=8.1,
                       is_corrected=True))
    dispose_engine(path)
    return path


def _broken(path: Path) -> Path:
    """_healthy() plus one problem of every kind."""
    _healthy(path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        gc1 = conn.execute("SELECT id FROM caches WHERE gc_code = 'GC1'").fetchone()[0]
        gc2 = conn.execute("SELECT id FROM caches WHERE gc_code = 'GC2'").fetchone()[0]
        conn.executescript(f"""
            INSERT INTO logs (cache_id, log_id, log_type) VALUES (9999, 'x1', 'Note');
            INSERT INTO logs (cache_id, log_id, log_type) VALUES (9999, 'x2', 'Note');
            INSERT INTO waypoints (cache_id, prefix, name) VALUES (9999, 'S1', 'Stage');
            INSERT INTO attributes (cache_id, attribute_id) VALUES (9998, 1);
            INSERT INTO trackables (cache_id, name) VALUES (9998, 'TB');
            INSERT INTO user_notes (cache_id, note) VALUES (9997, 'gone');
            INSERT INTO caches (gc_code, name, cache_type, latitude, longitude, parent_gc_code)
                VALUES ('CW2', 'Lost link', 'Custom', 47.3, 8.3, 'gc404');
            UPDATE waypoints SET parent_gc_code = 'GC2' WHERE cache_id = {gc1};
            UPDATE user_notes SET corrected_lat = NULL WHERE cache_id = {gc2};
            INSERT INTO caches (gc_code, name, cache_type, latitude, longitude,
                                difficulty, terrain)
                VALUES ('GC3', 'Bad', 'Traditional Cache', 95.0, 8.0, 1.3, 6.0);
            INSERT INTO caches (gc_code, name, cache_type, latitude, longitude)
                VALUES ('GC4', '  ', 'Traditional Cache', 47.0, 8.0);
        """)
        conn.execute("UPDATE caches SET last_user_update = ? WHERE id = ?", (USER_DATE, gc2))
        conn.commit()
    finally:
        conn.close()
    return path


def _counts(result: repair.ScanResult) -> dict[str, int]:
    return {f.key: f.count for f in result.problems}


class TestScan:
    def test_healthy_database_has_no_problems(self, tmp_path):
        result = repair.scan(_healthy(tmp_path / "Ok.db"), with_companion=True)
        assert not result.damaged
        assert result.problems == ()

    def test_finds_every_problem(self, tmp_path):
        result = repair.scan(_broken(tmp_path / "Broken.db"))
        assert _counts(result) == {
            "orphans:logs": 2, "orphans:waypoints": 1, "orphans:attributes": 1,
            "orphans:trackables": 1, "orphans:user_notes": 1,
            repair.PARENT_LINK: 1, repair.WAYPOINT_PARENT: 1, repair.CORRECTED_FLAG: 1,
            repair.COORDS: 1, repair.RATINGS: 1, repair.IDENTITY: 1,
        }
        by_key = {f.key: f for f in result.findings}
        # Each affected row with the field and its stored value (#1051 feedback).
        assert by_key[repair.PARENT_LINK].details == (Detail("CW2", "parent_gc_code", "gc404"),)
        assert by_key[repair.WAYPOINT_PARENT].details == (
            Detail("GC1", "waypoints.parent_gc_code", "GC2"),)
        assert by_key[repair.CORRECTED_FLAG].details == (Detail("GC2", "corrected", "NULL, 8.1"),)
        assert by_key[repair.RATINGS].details == (
            Detail("GC3", "difficulty / terrain", "1.3 / 6.0"),)
        assert by_key[repair.IDENTITY].details == (
            Detail("GC4", "gc_code / name", "'GC4' / '  '"),)
        assert by_key[repair.COORDS].details == (
            Detail("GC3", "latitude, longitude", "95.0, 8.0"),)
        assert by_key[repair.RATINGS].examples == ("GC3 difficulty / terrain 1.3 / 6.0",)
        assert not by_key[repair.COORDS].fixable
        # The parent may be in another database or be imported later.
        assert not by_key[repair.PARENT_LINK].fixable
        assert {f.key for f in result.fixable} == {
            "orphans:logs", "orphans:waypoints", "orphans:attributes", "orphans:trackables",
            "orphans:user_notes", repair.WAYPOINT_PARENT, repair.CORRECTED_FLAG,
        }

    def test_ratings_in_half_steps_are_fine(self, tmp_path):
        db = _healthy(tmp_path / "Ok.db")
        conn = sqlite3.connect(str(db))
        with conn:
            conn.execute("UPDATE caches SET difficulty = 4.5, terrain = 1.0 WHERE gc_code = 'GC1'")
        conn.close()
        assert repair.RATINGS not in _counts(repair.scan(db))

    def test_corrected_coordinates_out_of_range(self, tmp_path):
        db = _healthy(tmp_path / "Ok.db")
        conn = sqlite3.connect(str(db))
        with conn:
            conn.execute("UPDATE user_notes SET corrected_lon = 200")
        conn.close()
        assert _counts(repair.scan(db)) == {repair.CORRECTED_COORDS: 1}

    def test_examples_are_limited(self, tmp_path):
        db = _healthy(tmp_path / "Ok.db")
        conn = sqlite3.connect(str(db))
        with conn:
            for n in range(repair.MAX_EXAMPLES + 3):
                conn.execute("INSERT INTO caches (gc_code, name, cache_type, latitude, longitude, "
                             f"difficulty) VALUES ('GCX{n}', 'x', 'T', 1, 1, 7)")
        conn.close()
        with patch.object(repair, "MAX_DETAILS", repair.MAX_EXAMPLES + 1):
            finding = next(f for f in repair.scan(db).findings if f.check == repair.RATINGS)
        assert finding.count == repair.MAX_EXAMPLES + 3
        assert len(finding.examples) == repair.MAX_EXAMPLES
        assert len(finding.details) == repair.MAX_EXAMPLES + 1
        assert [d.gc_code for d in finding.details] == sorted(d.gc_code for d in finding.details)

    def test_damaged_file_skips_the_data_checks(self, tmp_path):
        db = _healthy(tmp_path / "Ok.db")
        with patch.object(repair, "_connect") as connect:
            connect.return_value.execute.return_value.fetchall.return_value = [
                ("*** in database main ***",)]
            result = repair.scan(db)
        assert result.damaged == ("*** in database main ***",)
        assert result.findings == ()

    def test_companion_orphans(self, tmp_path):
        db = _healthy(tmp_path / "Ok.db")
        companion.create(db)
        conn = sqlite3.connect(str(companion.companion_path(db)))
        with conn:
            conn.execute("CREATE TABLE notes (gc_code TEXT PRIMARY KEY, note TEXT)")
            conn.execute("INSERT INTO notes VALUES ('GC1', 'kept'), ('GC404', 'orphan')")
        conn.close()
        result = repair.scan(db, with_companion=True)
        assert _counts(result) == {repair.COMPANION_ORPHANS: 1}
        assert repair.repair(db, {repair.COMPANION_ORPHANS}).fixed == {repair.COMPANION_ORPHANS: 1}
        assert repair.scan(db, with_companion=True).problems == ()


class TestRepair:
    def test_fixes_everything_and_a_second_run_finds_nothing_fixable(self, tmp_path):
        db = _broken(tmp_path / "Broken.db")
        keys = {f.key for f in repair.scan(db).fixable}
        result = repair.repair(db, keys)
        assert result.fixed == {
            "orphans:logs": 2, "orphans:waypoints": 1, "orphans:attributes": 1,
            "orphans:trackables": 1, "orphans:user_notes": 1,
            repair.WAYPOINT_PARENT: 1, repair.CORRECTED_FLAG: 1,
        }
        again = repair.scan(db)
        assert again.fixable == ()
        # Report-only problems are left for the user.
        assert set(_counts(again)) == {repair.PARENT_LINK, repair.COORDS, repair.RATINGS,
                                       repair.IDENTITY}
        assert maintenance.check_file(db).ok
        assert repair.repair(db, keys).fixed == {}

    def test_keeps_the_good_rows(self, tmp_path):
        db = _broken(tmp_path / "Broken.db")
        repair.repair(db, {f.key for f in repair.scan(db).fixable})
        init_db(db_path=db)
        with get_session() as s:
            gc1 = s.query(Cache).filter_by(gc_code="GC1").one()
            assert [lg.log_id for lg in gc1.logs] == ["1"]
            assert [w.parent_gc_code for w in gc1.waypoints] == ["GC1"]
            assert s.query(Cache).filter_by(gc_code="CW1").one().parent_gc_code == "GC1"
            assert s.query(Cache).filter_by(gc_code="CW2").one().parent_gc_code == "gc404"
            gc2 = s.query(Cache).filter_by(gc_code="GC2").one()
            assert gc2.user_note is not None and not gc2.user_note.is_corrected
            assert gc2.user_note.corrected_source is None

    def test_clearing_the_flag_keeps_last_user_update(self, tmp_path):
        db = _broken(tmp_path / "Broken.db")
        repair.repair(db, {repair.CORRECTED_FLAG})
        conn = sqlite3.connect(str(db))
        stamp = conn.execute(
            "SELECT last_user_update FROM caches WHERE gc_code = 'GC2'").fetchone()[0]
        conn.close()
        assert stamp == USER_DATE

    def test_only_the_chosen_fixes(self, tmp_path):
        db = _broken(tmp_path / "Broken.db")
        result = repair.repair(db, {"orphans:logs", repair.COORDS, repair.PARENT_LINK})
        assert result.fixed == {"orphans:logs": 2}
        counts = _counts(repair.scan(db))
        assert "orphans:logs" not in counts
        assert counts["orphans:waypoints"] == 1
        assert counts[repair.COORDS] == 1
        assert counts[repair.PARENT_LINK] == 1

    def test_a_failure_changes_nothing(self, tmp_path):
        db = _broken(tmp_path / "Broken.db")
        with patch.object(repair, "_clear_corrected_flags", side_effect=sqlite3.OperationalError):
            with pytest.raises(sqlite3.OperationalError):
                repair.repair(db, {"orphans:logs", repair.CORRECTED_FLAG})
        assert _counts(repair.scan(db))["orphans:logs"] == 2


# ── The dialog's work and text ────────────────────────────────────────────────

class TestRunRepair:
    def test_backup_fix_and_summary(self, tmp_path):
        db = _broken(tmp_path / "Broken.db")
        keys = {f.key for f in repair.scan(db).fixable}
        made = SimpleNamespace(backup_set=SimpleNamespace(path=tmp_path / "set"))
        with patch("opensak.backup.backupset.write_backup_set", return_value=made) as write:
            outcome = drd.run_repair(SimpleNamespace(name="Broken", path=db), keys, True,
                                     lambda _s: None)
        write.assert_called_once()
        lines = drd.repair_lines(outcome)
        assert lines[0] == tr("maint_backup_done", path=str(tmp_path / "set"))
        assert tr("repair_fixed", count=2, check=tr("repair_chk_orphans", table="logs")) in lines
        assert outcome.compact is None or outcome.compact.status in (
            maintenance.DONE, maintenance.NOT_WORTHWHILE)

    def test_nothing_to_fix(self, tmp_path):
        db = _healthy(tmp_path / "Ok.db")
        outcome = drd.run_repair(SimpleNamespace(name="Ok", path=db), set(), False,
                                 lambda _s: None)
        assert drd.repair_lines(outcome)[0] == tr("repair_nothing_fixed")

    def test_compacts_when_a_quarter_is_free(self, tmp_path):
        db = _healthy(tmp_path / "Ok.db")
        conn = sqlite3.connect(str(db))
        with conn:
            conn.execute("CREATE TABLE filler (x BLOB)")
            for _ in range(60):
                conn.execute("INSERT INTO filler VALUES (zeroblob(1024 * 1024))")
        with conn:
            conn.execute("DROP TABLE filler")
        conn.close()
        with patch.object(maintenance, "MANUAL_MIN_RECLAIM_BYTES", 0):
            outcome = drd.run_repair(SimpleNamespace(name="Ok", path=db), set(), False,
                                     lambda _s: None)
        assert outcome.compact is not None and outcome.compact.ran
        assert maintenance.file_info(db).stats.free_bytes == 0


# ── The dialog ────────────────────────────────────────────────────────────────

def _dialog(qtbot, db: Path) -> drd.DatabaseRepairDialog:
    dlg = drd.DatabaseRepairDialog(SimpleNamespace(name=db.stem, path=db))
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: not dlg.is_running() and dlg.scan_result is not None, timeout=10000)
    return dlg


def _rows(dlg) -> dict[str, object]:
    out = {}
    for i in range(dlg._tree.topLevelItemCount()):
        item = dlg._tree.topLevelItem(i)
        assert item is not None
        out[item.data(0, drd.KEY_ROLE)] = item
    return out


class TestDialog:
    def test_lists_the_problems_and_ticks_the_fixable_ones(self, qtbot, tmp_path):
        dlg = _dialog(qtbot, _broken(tmp_path / "Broken.db"))
        rows = _rows(dlg)
        assert rows["orphans:logs"].checkState(0) == drd.Qt.CheckState.Checked
        report = rows[repair.COORDS]
        assert not report.flags() & drd.Qt.ItemFlag.ItemIsUserCheckable
        assert report.text(2) == tr("repair_report_only")
        assert "GC3" in rows[repair.COORDS].text(3)
        link = rows[repair.PARENT_LINK]
        assert not link.flags() & drd.Qt.ItemFlag.ItemIsUserCheckable
        assert link.text(2) == tr("repair_report_only")
        assert dlg._backup.isChecked()
        assert dlg._btn_repair.isEnabled()
        assert dlg.checked_keys() == {f.key for f in dlg.scan_result.fixable}

    def test_unticking_everything_disables_repair(self, qtbot, tmp_path):
        dlg = _dialog(qtbot, _broken(tmp_path / "Broken.db"))
        for item in _rows(dlg).values():
            if item.flags() & drd.Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(0, drd.Qt.CheckState.Unchecked)
        assert not dlg._btn_repair.isEnabled()

    def test_healthy_database(self, qtbot, tmp_path):
        dlg = _dialog(qtbot, _healthy(tmp_path / "Ok.db"))
        assert dlg._tree.topLevelItemCount() == 0
        assert dlg._summary.text() == tr("repair_scan_clean")
        assert not dlg._btn_repair.isEnabled()

    def test_repair_then_scans_again(self, qtbot, tmp_path):
        db = _broken(tmp_path / "Broken.db")
        init_db(db_path=db)        # the active database
        dlg = _dialog(qtbot, db)
        dlg._backup.setChecked(False)
        dlg._repair()
        qtbot.waitUntil(lambda: not dlg.is_running() and dlg.scan_result is not None
                        and not dlg.scan_result.fixable, timeout=20000)
        assert tr("repair_fixed", count=2, check=tr("repair_chk_orphans", table="logs")) \
            in dlg._results.toPlainText()
        assert set(_rows(dlg)) == {repair.PARENT_LINK, repair.COORDS, repair.RATINGS,
                                   repair.IDENTITY}
        with get_session() as s:            # the active database is still usable
            assert s.query(Log).count() == 1

    def test_damaged_file_points_to_restore(self, qtbot, tmp_path):
        db = _healthy(tmp_path / "Ok.db")
        damaged = repair.ScanResult(db, damaged=("page 3 is never used",))
        with patch.object(repair, "scan", return_value=damaged):
            dlg = _dialog(qtbot, db)
        assert dlg._summary.text().startswith(tr("repair_damaged"))
        assert tr("maint_check_main_failed") in dlg._summary.text()
        assert "page 3 is never used" in dlg._results.toPlainText()
        assert not dlg._btn_repair.isEnabled()

    def test_maintenance_dialog_opens_repair(self, qtbot, tmp_path):
        db = _healthy(tmp_path / "Ok.db")
        dlg = dmd.DatabaseMaintenanceDialog(SimpleNamespace(name="Ok", path=db))
        qtbot.addWidget(dlg)
        qtbot.waitUntil(lambda: not dlg.is_running() and dlg._info.main is not None,
                        timeout=10000)
        with patch.object(drd.DatabaseRepairDialog, "exec", return_value=0) as run:
            dlg._btn_repair.click()
        run.assert_called_once()
        qtbot.waitUntil(lambda: not dlg.is_running(), timeout=10000)


# ── Copy list (#1051 feedback: fix report-only problems by hand) ──────────────

class TestCopy:
    def test_copy_text(self):
        findings = [
            repair.Finding(repair.RATINGS, 3, False, (
                Detail("GC3", "difficulty / terrain", "1.3 / 6.0"),
                Detail("GC5", "difficulty / terrain", "7.0 / 1.0"))),
            repair.Finding(repair.ORPHANS, 4, True, table="logs"),
        ]
        lines = drd.copy_text(findings).splitlines()
        assert lines[0].split("\t") == [tr("repair_col_check"), tr("col_gc_code"),
                                        tr("repair_col_field"), tr("repair_col_value")]
        ratings = tr("repair_chk_ratings")
        assert lines[1].split("\t") == [ratings, "GC3", "difficulty / terrain", "1.3 / 6.0"]
        assert lines[2].split("\t") == [ratings, "GC5", "difficulty / terrain", "7.0 / 1.0"]
        assert lines[3].split("\t") == [ratings, "", "", tr("repair_copy_more", count="1")]
        assert lines[4].split("\t") == [tr("repair_chk_orphans", table="logs"), "", "",
                                        tr("repair_copy_no_code", count="4")]

    def test_copies_all_rows_or_the_selected_ones(self, qtbot, tmp_path):
        dlg = _dialog(qtbot, _broken(tmp_path / "Broken.db"))
        clipboard = drd.QGuiApplication.clipboard()
        assert clipboard is not None and dlg._btn_copy.isEnabled()
        dlg._btn_copy.click()
        text = clipboard.text()
        assert "GC3\tlatitude, longitude\t95.0, 8.0" in text
        assert "CW2\tparent_gc_code\tgc404" in text
        assert tr("repair_copied", count=f"{len(text.splitlines()) - 1:,}") \
            in dlg._results.toPlainText()

        _rows(dlg)[repair.RATINGS].setSelected(True)
        dlg.copy_list()
        lines = clipboard.text().splitlines()
        assert len(lines) == 2
        assert lines[1].startswith(tr("repair_chk_ratings") + "\tGC3\t")

    def test_nothing_to_copy_on_a_healthy_database(self, qtbot, tmp_path):
        dlg = _dialog(qtbot, _healthy(tmp_path / "Ok.db"))
        assert not dlg._btn_copy.isEnabled()
