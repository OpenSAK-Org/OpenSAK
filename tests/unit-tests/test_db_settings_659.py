"""
tests/unit-tests/test_db_settings_659.py — per-database settings stored
inside the database file (issue #659).

Uses a real DatabaseManager (isolated per test by the root conftest) with a
real, open active database, so db_settings is bound the same way it is in
the running app.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from opensak.db import db_settings
from opensak.db.database import dispose_engine, init_db
from opensak.db.db_settings import (
    UUID_KEY,
    ensure_seeded,
    get_value,
    legacy_columns_key,
    legacy_db_key,
    legacy_sort_key,
    read_file,
    set_value,
)
from opensak.settings_store import get_store


@pytest.fixture
def manager(qapp, tmp_path):
    from opensak.db.manager import get_db_manager
    m = get_db_manager()
    m.ensure_active_initialised()
    db_settings._reset_cache()
    yield m
    dispose_engine()
    db_settings._reset_cache()


def _new_db(manager, name: str, tmp_path: Path):
    """Create (and initialise) another database without switching to it."""
    info = manager.new_database(name, tmp_path / f"{name}.db")
    manager.switch_to(manager.databases[0])  # back to the first one
    return info


def _unopened_db(manager, path: Path) -> Path:
    """
    A real OpenSAK database file whose settings have never been bound —
    like one last opened by a version before #659. The active database is
    reopened afterwards.
    """
    init_db(db_path=path)
    dispose_engine(path)
    init_db(db_path=manager.active.path)
    db_settings._reset_cache()
    assert "db_settings" not in _tables(path)
    return path


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


# ── Stored in the database file ─────────────────────────────────────────────

class TestStoredInFile:
    def test_roundtrip(self, manager):
        set_value("home_lat", "legacy.unused", 56.5)
        assert get_value("home_lat", "legacy.unused") == 56.5

    def test_value_is_in_the_database_file_not_opensak_json(self, manager):
        set_value("home_lat", "legacy.home_lat", 56.5)
        assert read_file(manager.active.path)["home_lat"] == 56.5
        assert get_store().get("legacy.home_lat") is None

    def test_json_types_survive(self, manager):
        value = {"gc_code": 90, "name": 300}
        set_value("columns.widths", None, value)
        set_value("sort.ascending", None, False)
        set_value("columns.visible", None, ["gc_code", "name"])
        db_settings._reset_cache()  # force a re-read from the file
        assert get_value("columns.widths", None) == value
        assert get_value("sort.ascending", None) is False
        assert get_value("columns.visible", None) == ["gc_code", "name"]

    def test_survives_reopening(self, manager):
        set_value("dist_calc_method", None, "vincenty")
        path = manager.active.path
        dispose_engine(path)
        init_db(db_path=path)
        assert get_value("dist_calc_method", None) == "vincenty"

    def test_missing_key_returns_default(self, manager):
        assert get_value("not_set_anywhere", None, default=7) == 7

    def test_unreadable_value_is_ignored(self, manager):
        get_value("bind", None)  # first use creates the table
        conn = sqlite3.connect(str(manager.active.path))
        conn.execute(
            "INSERT OR REPLACE INTO db_settings (key, value) VALUES (?, ?)",
            ("home_lat", "{not json"),
        )
        conn.commit()
        conn.close()
        db_settings._reset_cache()
        assert get_value("home_lat", None, default="fallback") == "fallback"

    def test_unknown_keys_from_a_newer_version_are_harmless(self, manager):
        get_value("bind", None)  # first use creates the table
        conn = sqlite3.connect(str(manager.active.path))
        conn.execute(
            "INSERT INTO db_settings (key, value) VALUES (?, ?)",
            ("some_future_setting", json.dumps({"x": 1})),
        )
        conn.commit()
        conn.close()
        db_settings._reset_cache()
        set_value("home_lat", None, 1.0)
        assert get_value("home_lat", None) == 1.0


# ── Unchanged values are not rewritten (#959) ─────────────────────────────────

def _count_writes(engine):
    from sqlalchemy import event
    writes: list[str] = []

    def _on(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("INSERT"):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", _on)
    return writes, lambda: event.remove(engine, "before_cursor_execute", _on)


class TestUnchangedValues:
    """
    #959: the cache table re-emits its sort order every time the list
    loads, so the same sort was written back on every start — new WAL pages
    each time, which the back-up-on-exit check rightly sees as a change.
    """

    def test_same_value_is_not_written_again(self, manager):
        from opensak.db import database
        set_value("sort.field", None, "distance")
        writes, stop = _count_writes(database._engine)
        try:
            set_value("sort.field", None, "distance")
        finally:
            stop()
        assert writes == []

    def test_same_value_leaves_the_wal_alone(self, manager):
        set_value("sort.ascending", None, False)
        wal = Path(str(manager.active.path) + "-wal")
        before = wal.stat().st_size if wal.exists() else 0
        for _ in range(5):
            set_value("sort.ascending", None, False)
        after = wal.stat().st_size if wal.exists() else 0
        assert after == before

    def test_changed_value_is_written(self, manager):
        from opensak.db import database
        set_value("sort.field", None, "distance")
        writes, stop = _count_writes(database._engine)
        try:
            set_value("sort.field", None, "name")
        finally:
            stop()
        assert len(writes) == 1
        assert read_file(manager.active.path)["sort.field"] == "name"

    def test_compared_as_stored_json(self, manager):
        """True and 1 are equal in Python but not as stored values."""
        set_value("sort.ascending", None, 1)
        set_value("sort.ascending", None, True)
        assert read_file(manager.active.path)["sort.ascending"] is True

    def test_first_write_of_a_key_is_never_skipped(self, manager):
        set_value("map_nearby_max_caches", None, None)
        assert "map_nearby_max_caches" in read_file(manager.active.path)


# ── Per database ──────────────────────────────────────────────────────────────

class TestPerDatabase:
    def test_each_database_keeps_its_own_home(self, manager, tmp_path):
        from opensak.gui.settings import AppSettings
        s = AppSettings()
        first = manager.active
        second = _new_db(manager, "Second", tmp_path)

        s.home_lat, s.home_lon = 10.0, 11.0
        manager.switch_to(second)
        s.home_lat, s.home_lon = 20.0, 21.0
        manager.switch_to(first)
        assert (s.home_lat, s.home_lon) == (10.0, 11.0)
        manager.switch_to(second)
        assert (s.home_lat, s.home_lon) == (20.0, 21.0)

    def test_new_database_falls_back_to_last_used_home(self, manager, tmp_path):
        # The global "last used" value is still written, as the default for
        # databases that have no home of their own yet.
        from opensak.gui.settings import AppSettings
        s = AppSettings()
        s.home_lat = 33.0
        third = _new_db(manager, "Third", tmp_path)
        manager.switch_to(third)
        assert s.home_lat == 33.0

    def test_columns_and_sort_are_per_database(self, manager, tmp_path):
        from opensak.gui.dialogs.column_dialog import (
            get_visible_columns, set_visible_columns,
        )
        first = manager.active
        second = _new_db(manager, "Second", tmp_path)
        set_visible_columns(["gc_code", "name", "country"])
        manager.switch_to(second)
        set_visible_columns(["gc_code", "name", "terrain"])
        manager.switch_to(first)
        assert get_visible_columns() == ["gc_code", "name", "country"]
        assert read_file(second.path)["columns.visible"] == ["gc_code", "name", "terrain"]


# ── Travels with the file ─────────────────────────────────────────────────────

class TestTravelsWithTheFile:
    def test_copy_brings_settings_and_gets_a_new_uuid(self, manager, tmp_path):
        from opensak.gui.settings import AppSettings
        s = AppSettings()
        s.home_lat, s.home_lon = 47.1, 8.2
        original = read_file(manager.active.path)

        copy = manager.copy_database(manager.active, "The Copy", tmp_path / "copy.db")

        copied = read_file(copy.path)
        assert copied["home_lat"] == 47.1 and copied["home_lon"] == 8.2
        assert copied[UUID_KEY] != original[UUID_KEY]
        assert read_file(manager.active.path)[UUID_KEY] == original[UUID_KEY]

    def test_file_copied_outside_the_app_keeps_its_settings(self, manager, tmp_path):
        from opensak.gui.settings import AppSettings
        AppSettings().home_lat = 12.25
        src = manager.active.path
        dispose_engine(src)  # checkpoint the WAL, like closing the app
        moved = tmp_path / "elsewhere" / "Moved.db"
        moved.parent.mkdir()
        moved.write_bytes(src.read_bytes())

        opened = manager.open_database(moved)
        manager.switch_to(opened)
        assert AppSettings().home_lat == 12.25

    def test_rename_keeps_settings(self, manager, tmp_path):
        from opensak.gui.dialogs.column_dialog import (
            get_visible_columns, set_visible_columns,
        )
        set_visible_columns(["gc_code", "name", "county"])
        manager.rename(manager.active, "Renamed DB")
        db_settings._reset_cache()
        assert get_visible_columns() == ["gc_code", "name", "county"]


# ── db_uuid ───────────────────────────────────────────────────────────────────

class TestUuid:
    def test_assigned_on_first_use_and_stable(self, manager):
        get_value("anything", None)
        first = read_file(manager.active.path)[UUID_KEY]
        assert isinstance(first, str) and len(first) == 32
        path = manager.active.path
        dispose_engine(path)
        db_settings._reset_cache()
        init_db(db_path=path)
        get_value("anything", None)
        assert read_file(path)[UUID_KEY] == first


# ── Import of the pre-#659 opensak.json settings ─────────────────────────────

class TestLegacyImport:
    def _legacy(self, path: Path, name: str) -> None:
        get_store().set_many({
            legacy_db_key(path, "home_lat"): 60.5,
            legacy_db_key(path, "active_home_name"): "Cabin",
            legacy_db_key(path, "dist_calc_method"): "haversine",
            legacy_sort_key(path, "field"): "distance",
            legacy_sort_key(path, "filter_profile"): "Unfound",
            legacy_columns_key(name, "visible"): ["gc_code", "name", "state"],
            legacy_columns_key(name, "widths"): {"name": 250},
        })

    def test_imported_on_first_open(self, manager, tmp_path):
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        self._legacy(path, "Old Trip")
        info = manager.open_database(path)
        manager.switch_to(info)

        assert get_value("home_lat", None) == 60.5
        assert get_value("active_home_name", None) == "Cabin"
        assert get_value("dist_calc_method", None) == "haversine"
        assert get_value("sort.field", None) == "distance"
        assert get_value("sort.filter_profile", None) == "Unfound"
        assert get_value("columns.visible", None) == ["gc_code", "name", "state"]
        assert get_value("columns.widths", None) == {"name": 250}

    def test_imported_only_once(self, manager, tmp_path):
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        self._legacy(path, "Old Trip")
        ensure_seeded(path, "Old Trip")
        get_store().set(legacy_db_key(path, "home_lat"), 1.0)  # changed later
        ensure_seeded(path, "Old Trip")
        assert read_file(path)["home_lat"] == 60.5

    def test_legacy_keys_are_left_for_older_versions(self, manager, tmp_path):
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        self._legacy(path, "Old Trip")
        ensure_seeded(path, "Old Trip")
        assert get_store().get(legacy_db_key(path, "home_lat")) == 60.5

    def test_import_never_overwrites_values_already_in_the_file(self, manager, tmp_path):
        path = _unopened_db(manager, tmp_path / "Mixed.db")
        conn = sqlite3.connect(str(path))
        conn.execute(db_settings._DDL)
        conn.execute("INSERT INTO db_settings VALUES ('home_lat', '99.0')")
        conn.commit()
        conn.close()
        get_store().set(legacy_db_key(path, "home_lat"), 60.5)
        ensure_seeded(path, "Mixed")
        assert read_file(path)["home_lat"] == 99.0

    def test_rename_before_first_open_keeps_legacy_settings(self, manager, tmp_path):
        # The legacy keys are based on the old path and name — rename()
        # must import them before the file gets its new name.
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        self._legacy(path, "Old Trip")
        info = manager.open_database(path)
        manager.rename(info, "New Trip")
        assert read_file(info.path)["home_lat"] == 60.5
        assert read_file(info.path)["columns.visible"] == ["gc_code", "name", "state"]

    def test_move_before_first_open_keeps_legacy_settings(self, manager, tmp_path):
        # Issue #961: the legacy keys are based on the old path —
        # move_databases_to() must import them before the file is moved,
        # or the database arrives in the new folder without them.
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        self._legacy(path, "Old Trip")
        info = manager.open_database(path)
        new_dir = tmp_path / "new_location"

        errors = manager.move_databases_to(new_dir, delete_originals=True)

        assert errors == []
        assert info.path == new_dir / "Old Trip.db"
        moved = read_file(info.path)
        assert moved["home_lat"] == 60.5
        assert moved["active_home_name"] == "Cabin"
        assert moved["sort.field"] == "distance"
        assert moved["sort.filter_profile"] == "Unfound"

    def test_move_then_open_shows_the_legacy_settings(self, manager, tmp_path):
        # Issue #961, end to end: after the move, opening the database at
        # its new path must not seed it with empty values.
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        self._legacy(path, "Old Trip")
        info = manager.open_database(path)

        manager.move_databases_to(tmp_path / "new_location", delete_originals=True)
        manager.switch_to(info)

        assert get_value("home_lat", None) == 60.5
        assert get_value("sort.filter_profile", None) == "Unfound"

    def test_move_with_keep_originals_also_seeds_the_copy(self, manager, tmp_path):
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        self._legacy(path, "Old Trip")
        info = manager.open_database(path)

        manager.move_databases_to(tmp_path / "new_location", delete_originals=False)

        assert read_file(info.path)["home_lat"] == 60.5
        assert path.exists()

    def test_never_creates_a_missing_file(self, manager, tmp_path):
        ensure_seeded(tmp_path / "missing.db", "Missing")
        assert not (tmp_path / "missing.db").exists()


# ── Binding rule ──────────────────────────────────────────────────────────────

class TestBinding:
    def test_falls_back_to_legacy_key_when_engine_is_another_database(
        self, manager, tmp_path
    ):
        # e.g. an import that has temporarily switched the engine to the
        # database it imports into: settings must not land in that file.
        other = _unopened_db(manager, tmp_path / "ImportTarget.db")
        init_db(db_path=other)
        set_value("home_lat", "legacy.key", 5.5)
        assert get_store().get("legacy.key") == 5.5
        assert "home_lat" not in read_file(other)
        init_db(db_path=manager.active.path)

    def test_falls_back_when_active_entry_has_no_path(self, manager, monkeypatch):
        # Regression: with an engine still open (e.g. from an earlier test in
        # the same process) and a stand-in manager whose active entry has no
        # .path, _bound_engine() raised AttributeError instead of falling back.
        from types import SimpleNamespace
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager",
            lambda: SimpleNamespace(active=SimpleNamespace(name="Fake")),
        )
        set_value("columns.visible", "legacy.columns", ["gc_code"])
        assert get_value("columns.visible", "legacy.columns") == ["gc_code"]
        assert get_store().get("legacy.columns") == ["gc_code"]

    def test_falls_back_without_an_open_database(self, manager):
        dispose_engine()
        set_value("home_lat", "legacy.key", 6.5)
        assert get_value("home_lat", "legacy.key") == 6.5
        assert get_store().get("legacy.key") == 6.5


# ── Files that aren't (or may not be) the active database ───────────────────

class TestFileAccess:
    """peek_value()/write_file(): used by GsakImportWorker for the database
    the engine was switched to, which db_settings doesn't bind."""

    def test_write_file_goes_to_that_file_only(self, manager, tmp_path):
        other = _new_db(manager, "Other", tmp_path)
        db_settings.write_file(other.path, {"dist_calc_lat": 47.1})
        assert read_file(other.path)["dist_calc_lat"] == 47.1
        assert get_value("dist_calc_lat", None) is None  # active untouched

    def test_write_file_to_active_drops_the_cache(self, manager):
        assert get_value("dist_calc_lat", None) is None  # loads the cache
        db_settings.write_file(manager.active.path, {"dist_calc_lat": 47.1})
        assert get_value("dist_calc_lat", None) == 47.1

    def test_write_file_survives_seeding(self, manager, tmp_path):
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        get_store().set(legacy_db_key(path, "dist_calc_lat"), 60.5)
        db_settings.write_file(path, {"dist_calc_lat": 47.1})
        ensure_seeded(path, "Old Trip")
        assert read_file(path)["dist_calc_lat"] == 47.1

    def test_missing_file_is_never_created(self, manager, tmp_path):
        path = tmp_path / "nope.db"
        db_settings.write_file(path, {"home_lat": 1.0})
        assert db_settings.peek_value(path, "home_lat", "dflt") == "dflt"
        assert not path.exists()

    def test_peek_value_reads_the_file(self, manager, tmp_path):
        other = _new_db(manager, "Other", tmp_path)
        db_settings.write_file(other.path, {"home_lat": 47.1})
        assert db_settings.peek_value(other.path, "home_lat") == 47.1

    def test_peek_value_sees_legacy_value_before_seeding(self, manager, tmp_path):
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        get_store().set(legacy_db_key(path, "home_lat"), 60.5)
        assert db_settings.peek_value(path, "home_lat") == 60.5
        assert "db_settings" not in _tables(path)  # peeking never seeds

    def test_peek_value_ignores_legacy_value_after_seeding(self, manager, tmp_path):
        path = _unopened_db(manager, tmp_path / "Old Trip.db")
        ensure_seeded(path, "Old Trip")
        get_store().set(legacy_db_key(path, "home_lat"), 60.5)  # set too late
        assert db_settings.peek_value(path, "home_lat", "dflt") == "dflt"
