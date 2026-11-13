"""
tests/unit-tests/test_headless_set_center_1304.py — opensak.set_center() in
`opensak run` (#1304, follow-up to #1203). It changes the centre of the
active database for the run: distances are recalculated and stored, so
sorting by distance works right after it, and the centre and distances are
set back when the run ends, also after an error, for every database it
changed. The global defaults the GUI uses for new databases stay as they
were. No QApplication and no display.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from opensak import cli
from opensak.db import db_settings
from opensak.db.database import recalculate_distances
from opensak.db.manager import get_db_manager
from opensak.gui.settings import HomePoint, get_settings
from opensak.settings_store import get_store
from opensak.utils.run_macro import EXIT_MACRO_ERROR, EXIT_NEEDS_UI, EXIT_OK

ZURICH = HomePoint("Zurich", 47.37, 8.54)
BASEL = HomePoint("Basel", 47.56, 7.59)
CENTRE_KEYS = ("active_home_name", "home_lat", "home_lon")
GLOBAL_KEYS = ("homepoints.active_name", "location.home_lat", "location.home_lon")


def _macro(folder: Path, source: str) -> Path:
    path = folder / "m.lua"
    path.write_text(source, encoding="utf-8")
    return path


def _run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _distances(db) -> dict[str, float]:
    conn = sqlite3.connect(db.path)
    try:
        return dict(conn.execute("SELECT gc_code, distance FROM caches"))
    finally:
        conn.close()


def _centre(db) -> dict:
    stored = db_settings.read_file(db.path)
    return {key: stored.get(key) for key in CENTRE_KEYS}


def _new_db(name: str, tmp_path: Path, capsys, centre: HomePoint | None):
    """A database *name* with three caches west to east, centred on
    *centre* (None: no centre of its own stored)."""
    manager = get_db_manager()
    manager.ensure_active_initialised()
    db = next((d for d in manager.databases if d.name == name), None) \
        or manager.new_database(name)
    inserts = "".join(
        f'opensak.insert{{ code = "GC{name[:2].upper()}{i}", name = "C{i}", '
        f'type = "Traditional Cache", lat = 47.5, lon = {lon} }}\n'
        for i, lon in enumerate((7.6, 8.0, 8.5))
    )
    folder = tmp_path / name
    folder.mkdir()
    assert _run(capsys, "run", _macro(folder, inserts), "--db", name, "--allow-write")[0] == EXIT_OK
    if centre is not None:
        db_settings.write_file(db.path, {"active_home_name": centre.name,
                                         "home_lat": centre.lat, "home_lon": centre.lon})
    lat, lon = get_settings().home_for_db_file(db.path)
    recalculate_distances(lat, lon, db_path=db.path)
    return db


@pytest.fixture
def points():
    get_settings().home_points = [ZURICH, BASEL]


@pytest.fixture
def nightly(tmp_path, capsys, points):
    return _new_db("Nightly", tmp_path, capsys, ZURICH)


def test_sorts_by_distance_for_this_run_only(capsys, tmp_path, nightly):
    before = (_centre(nightly), _distances(nightly))
    globals_before = {key: get_store().get(key) for key in GLOBAL_KEYS}
    macro = _macro(tmp_path, """
        opensak.set_center("Basel")
        opensak.sort("distance")
        print(opensak.center().name, table.concat(opensak.codes(), ","))
        opensak.set_center("Zurich")
        opensak.sort("distance")
        print(opensak.center().name, table.concat(opensak.codes(), ","))
    """)
    code, out, err = _run(capsys, "run", macro, "--db", "Nightly", "--allow-write")
    assert (code, err) == (EXIT_OK, "")
    assert out.splitlines() == ["Basel\tGCNI0,GCNI1,GCNI2", "Zurich\tGCNI2,GCNI1,GCNI0"]
    # Set back: the database's centre, its distances, and the GUI's defaults.
    assert (_centre(nightly), _distances(nightly)) == before
    assert {key: get_store().get(key) for key in GLOBAL_KEYS} == globals_before


def test_needs_a_write_approval(capsys, tmp_path, nightly):
    before = _distances(nightly)
    macro = _macro(tmp_path, 'opensak.set_center("Basel")\n')
    code, _, err = _run(capsys, "run", macro, "--db", "Nightly")
    assert code == EXIT_NEEDS_UI and "--allow-write" in err
    assert _centre(nightly)["active_home_name"] == "Zurich"
    assert _distances(nightly) == before


def test_set_back_after_an_error(capsys, tmp_path, nightly):
    before = (_centre(nightly), _distances(nightly))
    macro = _macro(tmp_path, 'opensak.set_center("Basel")\nerror("boom")\n')
    assert _run(capsys, "run", macro, "--db", "Nightly", "--allow-write")[0] == EXIT_MACRO_ERROR
    assert (_centre(nightly), _distances(nightly)) == before


def test_database_without_a_centre_of_its_own(capsys, tmp_path, points):
    # Its centre comes from the global default; it gets none stored.
    plain = _new_db("Plain", tmp_path, capsys, None)
    before = (get_settings().home_for_db_file(plain.path), _distances(plain))
    macro = _macro(tmp_path, 'opensak.set_center(47.0, 7.0, "Somewhere")\n')
    assert _run(capsys, "run", macro, "--db", "Plain", "--allow-write")[0] == EXIT_OK
    assert _centre(plain) == dict.fromkeys(CENTRE_KEYS)
    assert (get_settings().home_for_db_file(plain.path), _distances(plain)) == before


def test_every_database_it_changed_is_set_back(capsys, tmp_path, nightly):
    other = _new_db("Other", tmp_path, capsys, BASEL)
    before = {db.name: (_centre(db), _distances(db)) for db in (nightly, other)}
    macro = _macro(tmp_path, """
        opensak.set_center(46.0, 6.0, "South")
        opensak.switch_database("Other")
        opensak.set_center(48.0, 9.0, "North")
        print(opensak.center().name)
    """)
    code, out, _ = _run(capsys, "run", macro, "--db", "Nightly", "--allow-write")
    assert (code, out) == (EXIT_OK, "North\n")
    assert {db.name: (_centre(db), _distances(db)) for db in (nightly, other)} == before
