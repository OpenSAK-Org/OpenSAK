"""
tests/unit-tests/test_gsak_replace_1231.py — overwrite import into a fresh
file, swapped in afterwards (#1231, importer/gsak_replace.py).

Uses real SQLite files and the real engine lifecycle (isolated per test by
the root conftest), and the synthetic GSAK databases of test_gsak_importer.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from opensak.db import companion, db_settings
from opensak.db.database import dispose_engine, get_session, init_db, session_for
from opensak.db.db_settings import ensure_seeded, read_file, write_file
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache
from opensak.importer import gsak_replace
from opensak.importer.gsak_replace import build_path, import_gsak_db_replacing
from opensak.lang import load_language
from opensak.utils import flags

_spec = importlib.util.spec_from_file_location(
    "_gsak_importer_tests", Path(__file__).with_name("test_gsak_importer.py")
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    load_language("en")
    monkeypatch.setattr(flags, "companion_db", True)
    companion.reset()
    yield
    dispose_engine()
    companion.reset()


def _target(path: Path, codes: tuple[str, ...] = ("GCOLD1", "GCOLD2")) -> Path:
    """A closed OpenSAK database holding *codes*, with settings of its own."""
    init_db(db_path=path)
    dispose_engine(path)
    ensure_seeded(path, path.stem)
    with session_for(path) as session:
        for code in codes:
            session.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                              container="Micro", latitude=47.0, longitude=8.0))
    write_file(path, {"home_lat": 47.5, "dist_calc_lat": 47.5, "dist_calc_lon": 8.5,
                      "dist_calc_method": "haversine"})
    return path


def _codes(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {code for (code,) in conn.execute("SELECT gc_code FROM caches")}
    finally:
        conn.close()


def _gsak(tmp_path: Path) -> Path:
    """A GSAK database holding the one cache GC1TEST."""
    folder = tmp_path / "gsak"
    folder.mkdir()
    return _helpers._make_gsak_db(folder / "sqlite.db3")


def test_overwrite_replaces_caches_and_keeps_database_settings(tmp_path):
    target = _target(tmp_path / "Found.db")
    uuid_before = read_file(target)["db_uuid"]

    result = import_gsak_db_replacing(_gsak(tmp_path), target)

    assert result.errors == []
    assert (result.created, result.cleared) == (1, 2)
    assert _codes(target) == {"GC1TEST"}
    settings = read_file(target)
    assert settings["db_uuid"] == uuid_before          # still the same database
    assert settings["home_lat"] == 47.5
    # The old caches' distances don't apply: recalculated after the import.
    assert not {"dist_calc_lat", "dist_calc_lon", "dist_calc_method"} & settings.keys()
    assert not build_path(target).exists()


def test_other_tables_of_the_target_are_carried_over(tmp_path):
    target = _target(tmp_path / "Found.db")
    conn = sqlite3.connect(str(target))
    conn.execute("CREATE TABLE extra_data (k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("CREATE INDEX ix_extra_v ON extra_data (v)")
    conn.execute("INSERT INTO extra_data VALUES ('a', 'kept')")
    conn.commit()
    conn.close()

    import_gsak_db_replacing(_gsak(tmp_path), target)

    conn = sqlite3.connect(str(target))
    try:
        assert conn.execute("SELECT v FROM extra_data").fetchall() == [("kept",)]
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'ix_extra_v'"
        ).fetchone()
    finally:
        conn.close()


def test_unreadable_gsak_database_leaves_the_target_as_it_was(tmp_path):
    target = _target(tmp_path / "Found.db")

    result = import_gsak_db_replacing(tmp_path / "missing.db3", target)

    assert result.errors
    assert result.cleared is None
    assert _codes(target) == {"GCOLD1", "GCOLD2"}
    assert not build_path(target).exists()


def test_failing_import_leaves_the_target_as_it_was(tmp_path, monkeypatch):
    target = _target(tmp_path / "Found.db")

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(gsak_replace, "import_gsak_db", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        import_gsak_db_replacing(_gsak(tmp_path), target)

    assert _codes(target) == {"GCOLD1", "GCOLD2"}
    assert not build_path(target).exists()


def test_leftover_build_file_is_replaced(tmp_path):
    target = _target(tmp_path / "Found.db")
    build_path(target).write_bytes(b"left over from an interrupted run")

    result = import_gsak_db_replacing(_gsak(tmp_path), target)

    assert result.errors == []
    assert _codes(target) == {"GC1TEST"}


def test_companion_rows_of_the_old_caches_are_removed(tmp_path):
    # #1231 decision: overwrite removes the companion rows of the old caches;
    # free (not cache-linked) macro tables stay.
    target = _target(tmp_path / "Found.db")
    conn = sqlite3.connect(str(companion.create(target)))
    conn.execute("CREATE TABLE notes (gc_code TEXT, note TEXT)")
    conn.execute("INSERT INTO notes VALUES ('GCOLD1', 'old'), ('GC1TEST', 'same code')")
    conn.execute("CREATE TABLE macro_state (gc_code TEXT, value TEXT)")
    companion.register(conn, "macro_state", "some_macro.lua", None, companion.KIND_FREE,
                       schema="main")
    conn.execute("INSERT INTO macro_state VALUES ('GCOLD1', 'kept')")
    conn.commit()
    conn.close()

    import_gsak_db_replacing(_gsak(tmp_path), target)

    conn = sqlite3.connect(str(companion.companion_path(target)))
    try:
        assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 0
        assert conn.execute("SELECT value FROM macro_state").fetchall() == [("kept",)]
    finally:
        conn.close()
    # Same db_uuid, so it is still attached to the replaced database.
    assert companion.prepare(target) is companion.Status.OK


def test_active_database_is_replaced_under_the_open_engine(tmp_path):
    target = _target(tmp_path / "Found.db")
    manager = get_db_manager()
    manager.switch_to(manager.open_database(target))   # the active database
    with get_session() as session:               # leaves a pooled connection open
        assert session.execute(text("SELECT count(*) FROM caches")).scalar_one() == 2
    assert db_settings.get_value("dist_calc_lat", None) == 47.5   # now cached

    import_gsak_db_replacing(_gsak(tmp_path), target)

    # The engine stays; its next connection opens the new file.
    with get_session() as session:
        codes = {c for (c,) in session.execute(text("SELECT gc_code FROM caches"))}
        journal = session.execute(text("PRAGMA journal_mode")).scalar_one()
    assert codes == {"GC1TEST"}
    assert journal.lower() == "wal"
    # The cached settings were those of the old file.
    assert db_settings.get_value("dist_calc_lat", None) is None
