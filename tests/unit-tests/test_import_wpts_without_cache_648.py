"""tests/unit-tests/test_import_wpts_without_cache_648.py — issue #648.

Waypoints whose cache isn't in the database were dropped without a word:
importing a Pocket Query's -wpts.gpx on its own, into a database without
its caches, ended as a plain success with every count at 0. The importer
now adds a warning naming how many waypoints were skipped, with example
codes, and the import dialog shows the importer's warnings (it showed none
before) and marks such a file with ⚠️ instead of ✅.
"""

from pathlib import Path

import pytest

from opensak.db.database import get_session, init_db
from opensak.db.models import Waypoint
from opensak.importer import ImportResult, import_gpx, import_zip
from opensak.lang import tr
from tests.data import SAMPLE_GPX, SAMPLE_WPTS_GPX, make_zip, write_gpx


def _wpts_gpx(*codes: str) -> str:
    """A -wpts.gpx with one parking waypoint per code (e.g. "PK9876")."""
    wpts = "".join(
        f'  <wpt lat="55.67" lon="12.56">\n'
        f'    <name>{code}</name>\n'
        f'    <desc>Parking Area</desc>\n'
        f'    <sym>Parking Area</sym>\n'
        f'    <type>Waypoint|Parking Area</type>\n'
        f'    <cmt>Park here.</cmt>\n'
        f'  </wpt>\n'
        for code in codes
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<gpx version="1.0" creator="Groundspeak, Inc." '
        'xmlns="http://www.topografix.com/GPX/1/0">\n'
        '  <name>Waypoints for Cache Listings</name>\n'
        f'{wpts}</gpx>\n'
    )


@pytest.fixture
def empty_db(tmp_path):
    init_db(db_path=tmp_path / "wpts648.db")


@pytest.fixture(autouse=True)
def _english():
    from opensak.lang import load_language
    load_language("en")


def _warning(result: ImportResult) -> str:
    matches = [w for w in result.warnings if "waypoints were not imported" in w]
    assert len(matches) == 1, result.warnings
    return matches[0]


# ── importer ─────────────────────────────────────────────────────────────────

def test_wpts_file_on_its_own_warns(tmp_path, empty_db):
    # The reproduction from the issue: SAMPLE_WPTS_GPX into an empty database.
    wpts = write_gpx(tmp_path, "pq-wpts.gpx", SAMPLE_WPTS_GPX)
    with get_session() as s:
        result = import_gpx(wpts, s)
    assert (result.created, result.updated, result.waypoints) == (0, 0, 0)
    assert result.errors == []
    assert _warning(result) == tr("import_wpts_without_cache", count=1, codes="PK2345")


def test_companion_with_waypoints_for_other_caches_warns(tmp_path, empty_db):
    # GC12345 is in the cache file; 9876 and 5555 are not.
    gpx = write_gpx(tmp_path, "pq.gpx", SAMPLE_GPX)
    wpts = write_gpx(tmp_path, "pq-wpts.gpx", _wpts_gpx("PK2345", "PK9876", "TR5555"))
    with get_session() as s:
        result = import_gpx(gpx, s, wpts_path=wpts)
    assert result.waypoints == 1
    assert _warning(result) == tr("import_wpts_without_cache", count=2, codes="PK9876, TR5555")
    with get_session() as s:
        assert {w.prefix for w in s.query(Waypoint)} == {"PK"}


def test_zip_with_waypoints_for_missing_caches_warns(tmp_path, empty_db):
    zip_path = make_zip(tmp_path, "pq.zip", {
        "pq.gpx": SAMPLE_GPX,
        "pq-wpts.gpx": _wpts_gpx("PK2345", "PK9876"),
    })
    with get_session() as s:
        result = import_zip(zip_path, s)
    assert result.created == 2
    assert _warning(result) == tr("import_wpts_without_cache", count=1, codes="PK9876")


def test_all_waypoints_matched_gives_no_warning(tmp_path, empty_db):
    gpx = write_gpx(tmp_path, "pq.gpx", SAMPLE_GPX)
    wpts = write_gpx(tmp_path, "pq-wpts.gpx", SAMPLE_WPTS_GPX)
    with get_session() as s:
        result = import_gpx(gpx, s, wpts_path=wpts)
    assert result.waypoints == 1
    assert result.warnings == []


def test_wpts_file_imported_after_its_caches_gives_no_warning(tmp_path, empty_db):
    # A real -wpts.gpx names the waypoint after the whole cache code
    # (GC12345 → PK12345), which a file imported on its own matches exactly.
    gpx = write_gpx(tmp_path, "pq.gpx", SAMPLE_GPX)
    wpts = write_gpx(tmp_path, "pq-wpts.gpx", _wpts_gpx("PK12345"))
    with get_session() as s:
        import_gpx(gpx, s)
    with get_session() as s:
        result = import_gpx(wpts, s)
    assert result.waypoints == 1
    assert result.warnings == []


def test_warning_names_at_most_five_codes(tmp_path, empty_db):
    codes = [f"PK{n}" for n in range(1001, 1009)]
    wpts = write_gpx(tmp_path, "pq-wpts.gpx", _wpts_gpx(*codes))
    with get_session() as s:
        result = import_gpx(wpts, s)
    assert _warning(result) == tr(
        "import_wpts_without_cache", count=8,
        codes="PK1001, PK1002, PK1003, PK1004, PK1005, …")


# ── import dialog ────────────────────────────────────────────────────────────

pytest.importorskip("pytestqt")


@pytest.fixture
def dlg(qtbot, monkeypatch):
    from types import SimpleNamespace
    from opensak.gui.dialogs.import_dialog import ImportDialog

    a = SimpleNamespace(name="Active", path=Path("/active.db"))
    mgr = SimpleNamespace(active_path=Path("/active.db"), databases=[a])
    monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: mgr)
    d = ImportDialog()
    qtbot.addWidget(d)
    d.add_files([Path("/pq-wpts.gpx")])
    return d


def _result(created=0, waypoints=0, warnings=()):
    r = ImportResult()
    r.created, r.waypoints = created, waypoints
    r.warnings = list(warnings)
    return r


def test_dialog_shows_the_warnings(dlg):
    dlg._on_file_finished(0, _result(warnings=["No caches for PK2345"]))
    log = dlg._log.toPlainText()
    assert tr("gsak_import_warnings_header", count=1) in log
    assert "No caches for PK2345" in log


def test_dialog_marks_a_file_that_only_warned(dlg):
    dlg._on_file_finished(0, _result(warnings=["No caches for PK2345"]))
    text = dlg._file_list.item(0).text()
    assert "⚠️" in text and "✅" not in text


def test_dialog_still_marks_success_when_something_was_imported(dlg):
    dlg._on_file_finished(0, _result(created=2, warnings=["Some skipped"]))
    assert "✅" in dlg._file_list.item(0).text()
