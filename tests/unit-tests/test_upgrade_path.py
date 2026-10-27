# tests/unit-tests/test_upgrade_path.py
"""
Issue #997: upgrading straight from an older stable release keeps everything.

Each folder in tests/fixtures/upgrade/ holds data written by that release's
own code (scripts/make_upgrade_fixture.py): databases, opensak.json,
bootstrap.json and filter profiles, plus expected.json describing the data.
These tests put a copy in tmp_path, start the current code on it the way the
app does, and check that the upgrade keeps the data and settings, moves the
per-database settings into the database files (#659), backs every database
up exactly once before migrating it (#549), and does nothing more on the
next start.

Fixtures from v1.21.0 on also hold a backup set written by that release
(backup/), which the current code must be able to restore (#1193). The
tests at the end cover what the 1.22.0 cycle adds on top of an upgraded
database: the last_user_update triggers and is_owner (migration 28, #1140)
and the companion database, which only appears once something is stored
in it (#1034).

Add a folder for every new stable release (RELEASE_CHECKLIST.md §7); the
tests pick it up automatically.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pytestqt")

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "upgrade"
FIXTURES = sorted(p for p in FIXTURE_ROOT.iterdir() if p.is_dir()) if FIXTURE_ROOT.is_dir() else []

ROOT_TOKEN = "{ROOT}"
ROOT_SAFE_TOKEN = "{ROOT_SAFE}"


# ── Putting a fixture in place ────────────────────────────────────────────────

def _safe_path(path: str) -> str:
    return path.replace("/", "_").replace("\\", "_")


def _materialize(value: Any, root: Path) -> Any:
    """Undo make_upgrade_fixture.tokenize() for *root* on this platform."""
    def fix(s: str) -> str:
        if ROOT_SAFE_TOKEN in s:
            s = s.replace(ROOT_SAFE_TOKEN, _safe_path(str(root)))
        if ROOT_TOKEN in s:
            # A path written by the old release on this platform would use
            # this platform's separator, in keys ("sort.<path>.field") too.
            s = s.replace("/", os.sep).replace(ROOT_TOKEN, str(root))
        return s

    if isinstance(value, dict):
        return {fix(k): _materialize(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_materialize(v, root) for v in value]
    if isinstance(value, str):
        return fix(value)
    return value


def _read_json(path: Path, root: Path) -> Any:
    return _materialize(json.loads(path.read_text(encoding="utf-8")), root)


@pytest.fixture(params=FIXTURES, ids=[p.name for p in FIXTURES])
def installed(request, tmp_path, monkeypatch, qapp):
    """The fixture copied into tmp_path as an existing installation."""
    import opensak.db.manager as mgr
    import opensak.gui.settings as smod
    from opensak import settings_store as ss
    from opensak.db import db_settings
    from opensak.db.database import dispose_engine

    fixture: Path = request.param
    root = tmp_path / "root"
    root.mkdir()
    for folder in ("install", "databases", "backup"):
        if (fixture / folder).is_dir():
            shutil.copytree(fixture / folder, root / folder)
    for folder in ("install", "backup"):
        for f in (root / folder).rglob("*.json"):
            f.write_text(json.dumps(_read_json(f, root), indent=2), encoding="utf-8")

    bootstrap = ss._bootstrap_path()  # inside the fake home (tests/conftest.py)
    bootstrap.parent.mkdir(parents=True, exist_ok=True)
    bootstrap.write_text(
        json.dumps(_read_json(fixture / "bootstrap.json", root)), encoding="utf-8"
    )

    # The real, file-backed settings store instead of the in-memory one the
    # unit-test conftest installs — reading opensak.json is part of the test.
    monkeypatch.setattr(ss, "_store", None)
    monkeypatch.setattr(smod, "_settings", None)
    monkeypatch.setattr(mgr, "_manager", None)
    db_settings._reset_cache()

    yield root, _read_json(fixture / "expected.json", root)

    dispose_engine()
    db_settings._reset_cache()


def _start_app():
    """The start-up steps app.main() runs before the main window is built."""
    from opensak.app import _migrate_legacy_db
    from opensak.db.manager import get_db_manager
    from opensak.gui.settings import get_settings
    from opensak.settings_store import (
        get_store, is_first_run, migrate_from_qsettings, repair_corrupted_bool_keys,
    )

    store = get_store()
    migrate_from_qsettings(store)
    repair_corrupted_bool_keys(store)
    first_run = is_first_run()
    _migrate_legacy_db()
    manager = get_db_manager()
    manager.ensure_active_initialised()
    get_settings().home_lat  # the main window reads it at once; binds db settings
    return manager, first_run


def _open(manager, name: str):
    """Switch to *name* the way the toolbar does, and read a setting."""
    from opensak.gui.settings import get_settings
    db = next(d for d in manager.databases if d.name == name)
    manager.switch_to(db)
    get_settings().home_lat
    return db


def _backups(root: Path) -> list[Path]:
    return sorted(root.rglob("*.pre-migration-schema*.db"))


def _schema(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(str(path))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {
            "version": conn.execute("PRAGMA user_version").fetchone()[0],
            "columns": {
                t: sorted(r[1] for r in conn.execute(f'PRAGMA table_info("{t}")'))
                for t in tables
            },
            "indexes": sorted(r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name NOT LIKE 'sqlite_%'")),
        }
    finally:
        conn.close()


def _query(path: Path, sql: str, *args) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


# ── The tests ─────────────────────────────────────────────────────────────────

def test_starts_without_wizard_on_the_same_database(installed):
    root, expected = installed
    manager, first_run = _start_app()

    assert first_run is False, "an upgrading user must not see the Welcome Wizard"
    assert manager.active is not None
    assert manager.active.name == expected["active"]
    assert {d.name for d in manager.databases} >= set(expected["databases"])
    from opensak.db.manager import get_db_manager
    assert get_db_manager().take_startup_fallback() is None


@pytest.mark.parametrize("how", ["switch", "apply_to_all"])
def test_every_database_is_migrated_with_its_data(installed, how):
    root, expected = installed
    from opensak.db.database import SCHEMA_VERSION, init_db, migrate_databases

    manager, _ = _start_app()
    if how == "apply_to_all":
        # #1027: "Apply to all databases" after the first backup notice.
        others = [d.path for d in manager.databases if d != manager.active]
        assert migrate_databases(others) == []
    for name in expected["databases"]:
        _open(manager, name)

    # A brand-new database as the app has it once opened: create_all() plus
    # the db_settings table (#659) that opening a database adds.
    from opensak.db.db_settings import ensure_seeded
    fresh = root / "fresh.db"
    init_db(db_path=fresh)
    ensure_seeded(fresh, "fresh")
    reference = _schema(fresh)
    assert reference["version"] == SCHEMA_VERSION

    for name, spec in expected["databases"].items():
        path = root / "databases" / spec["file"]
        schema = _schema(path)
        assert schema == reference, f"{name}: schema differs from a new database"

        counts = {t: _query(path, f"SELECT count(*) FROM {t}")[0][0] for t in spec["counts"]}
        assert counts == spec["counts"], name

        notes = dict(_query(path,
            "SELECT c.gc_code, n.note FROM user_notes n JOIN caches c ON c.id = n.cache_id "
            "WHERE n.note IS NOT NULL"))
        assert notes == spec["notes"], name

        corrected = {
            code: [lat, lon] for code, lat, lon in _query(path,
                "SELECT c.gc_code, n.corrected_lat, n.corrected_lon FROM user_notes n "
                "JOIN caches c ON c.id = n.cache_id WHERE n.is_corrected = 1")
        }
        assert corrected == spec["corrected"], name

        locked = sorted(r[0] for r in _query(path, "SELECT gc_code FROM caches WHERE locked = 1"))
        assert locked == sorted(spec["locked"]), name
        flagged = sorted(r[0] for r in _query(path, "SELECT gc_code FROM caches WHERE user_flag = 1"))
        assert flagged == sorted(spec["flagged"]), name


def test_per_database_settings_move_into_the_database_files(installed):
    """#659: home location, sort order and column layout follow each database."""
    root, expected = installed
    from opensak.db.db_settings import read_file

    manager, _ = _start_app()
    for name, spec in expected["databases"].items():
        db = _open(manager, name)
        stored = read_file(db.path)
        for key, value in spec["db_settings"].items():
            assert key in stored, f"{name}: {key} was not moved into the database"
            if isinstance(value, float):
                assert stored[key] == pytest.approx(value), f"{name}: {key}"
            else:
                assert stored[key] == value, f"{name}: {key}"

    # Each database keeps its own home location, not the last one set.
    from opensak.gui.settings import get_settings
    for name, spec in expected["databases"].items():
        _open(manager, name)
        assert get_settings().home_lat == pytest.approx(spec["db_settings"]["home_lat"])


def test_global_settings_and_filter_profiles_survive(installed):
    root, expected = installed
    from opensak.filters.engine import FilterProfile
    from opensak.gui.settings import get_settings

    _start_app()
    settings = get_settings()
    for key, value in expected["global"].items():
        actual = getattr(settings, key)
        actual = getattr(actual, "value", actual)  # enums
        assert actual == value, key

    profiles = {FilterProfile.load(p).name for p in FilterProfile.list_profiles()}
    assert profiles >= set(expected["profiles"])


def test_each_database_is_backed_up_once_before_migrating(installed):
    """#549/#988: one untouched copy per migrated database — and none when
    the release had the current schema already, so nothing was migrated."""
    root, expected = installed
    from opensak.db.database import SCHEMA_VERSION

    manager, _ = _start_app()
    for name in expected["databases"]:
        _open(manager, name)

    backups = _backups(root)
    if expected["schema_version_before"] >= SCHEMA_VERSION:
        assert backups == [], "nothing to migrate, so no backup expected"
        return
    names = {b.name.split(".pre-migration-")[0] for b in backups}
    assert names >= {Path(s["file"]).stem for s in expected["databases"].values()}
    assert len(backups) == len(names), f"more than one backup per database: {backups}"

    before = expected["schema_version_before"]
    for name, spec in expected["databases"].items():
        stem = Path(spec["file"]).stem
        backup = next(b for b in backups if b.name.startswith(stem + "."))
        assert _query(backup, "PRAGMA user_version")[0][0] == before
        assert _query(backup, "SELECT count(*) FROM caches")[0][0] == spec["counts"]["caches"]


def test_second_start_migrates_nothing(installed, monkeypatch):
    root, expected = installed
    import opensak.db.database as database
    import opensak.db.manager as mgr
    import opensak.gui.settings as smod
    from opensak import settings_store as ss

    manager, _ = _start_app()
    for name in expected["databases"]:
        _open(manager, name)
    backups = _backups(root)
    versions = {
        n: _query(root / "databases" / s["file"], "PRAGMA user_version")[0][0]
        for n, s in expected["databases"].items()
    }

    # Restart: fresh singletons, and forget which databases this process
    # already migrated, as a new process would.
    database.dispose_engine()
    monkeypatch.setattr(ss, "_store", None)
    monkeypatch.setattr(smod, "_settings", None)
    monkeypatch.setattr(mgr, "_manager", None)
    monkeypatch.setattr(database, "_migrated_paths", set())
    manager, first_run = _start_app()
    for name in expected["databases"]:
        _open(manager, name)

    assert first_run is False
    assert _backups(root) == backups
    assert {
        n: _query(root / "databases" / s["file"], "PRAGMA user_version")[0][0]
        for n, s in expected["databases"].items()
    } == versions


# ── What the 1.22.0 cycle adds on top of an upgraded database (#1193) ────────

def _open_all(root: Path, expected: dict) -> Any:
    manager, _ = _start_app()
    for name in expected["databases"]:
        _open(manager, name)
    return manager


def test_user_update_triggers_stamp_after_the_upgrade(installed):
    """Migration 28 (#1140): the triggers exist, the migration itself stamps
    nothing, and a user change afterwards is stamped."""
    root, expected = installed
    from opensak.db.database import _USER_UPDATE_TRIGGERS

    _open_all(root, expected)
    for name, spec in expected["databases"].items():
        path = root / "databases" / spec["file"]
        triggers = {r[0] for r in _query(
            path, "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert triggers >= set(_USER_UPDATE_TRIGGERS), name
        assert _query(path, "SELECT count(*) FROM caches "
                            "WHERE last_user_update IS NOT NULL")[0][0] == 0, name

    path = root / "databases" / expected["databases"]["Denmark"]["file"]
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("UPDATE caches SET user_flag = NOT user_flag WHERE gc_code = 'GCFX004'")
        conn.commit()
    finally:
        conn.close()
    stamped = _query(path, "SELECT gc_code FROM caches WHERE last_user_update IS NOT NULL")
    assert stamped == [("GCFX004",)]


def test_is_owner_follows_the_account_after_the_upgrade(installed):
    """Migration 28 (#1140): is_owner from the account in Settings, which the
    fixture has (user.gc_username / user.gc_finder_id)."""
    root, expected = installed
    store = json.loads((root / "install" / "opensak.json").read_text(encoding="utf-8"))
    username = store.get("user.gc_username")
    if not username:
        pytest.skip("this fixture has no account")
    # One of the user's own caches, as the old release stored it.
    denmark = root / "databases" / expected["databases"]["Denmark"]["file"]
    conn = sqlite3.connect(str(denmark))
    try:
        conn.execute("UPDATE caches SET owner_name = ?, owner_id = '7' "
                     "WHERE gc_code = 'GCFX005'", (username,))
        conn.commit()
    finally:
        conn.close()

    _open_all(root, expected)

    owned = _query(denmark, "SELECT gc_code FROM caches WHERE is_owner = 1")
    assert owned == [("GCFX005",)]
    malta = root / "databases" / expected["databases"]["Malta Trip"]["file"]
    assert _query(malta, "SELECT count(*) FROM caches WHERE is_owner = 1")[0][0] == 0


def test_no_companion_file_until_something_is_stored(installed):
    """#1034: opening, migrating and switching between the upgraded
    databases creates no companion files."""
    root, expected = installed
    from opensak.db import companion

    _open_all(root, expected)
    for spec in expected["databases"].values():
        path = root / "databases" / spec["file"]
        assert not companion.companion_path(path).exists(), spec["file"]
    assert not [p for p in root.rglob("*") if companion.is_companion_file(p)]


def test_backup_set_from_the_old_release_restores(installed):
    """#1193: a set written by the old release (no companions, no macros in
    its manifest) restores with the current code: as new databases, with
    their data, migrated on first open, and without a companion."""
    root, expected = installed
    if "backup" not in expected:
        pytest.skip("this release had no backups")
    from opensak.backup.backupset import read_backup_set
    from opensak.backup.macros_restore import has_macros
    from opensak.backup.restore import restore_database
    from opensak.db import companion
    from opensak.db.database import SCHEMA_VERSION

    manager, _ = _start_app()
    backup_set = read_backup_set(root / "backup" / expected["backup"])
    assert backup_set.opensak_version == expected["generated_by"]
    assert backup_set.macros is None and backup_set.macro_databases == ()
    assert not has_macros(backup_set)
    assert all(e.companion is None for e in backup_set.databases)

    for entry in backup_set.databases:
        if entry.name not in expected["databases"]:
            continue
        spec = expected["databases"][entry.name]
        info = restore_database(backup_set, entry)
        assert info.path != root / "databases" / spec["file"]
        assert not companion.companion_path(info.path).exists()

        _open(manager, info.name)
        assert _query(info.path, "PRAGMA user_version")[0][0] == SCHEMA_VERSION
        counts = {t: _query(info.path, f"SELECT count(*) FROM {t}")[0][0]
                  for t in spec["counts"]}
        assert counts == spec["counts"], entry.name
        assert not companion.companion_path(info.path).exists()

