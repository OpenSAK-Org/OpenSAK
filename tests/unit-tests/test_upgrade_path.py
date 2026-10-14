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
    for folder in ("install", "databases"):
        shutil.copytree(fixture / folder, root / folder)
    for f in (root / "install").rglob("*.json"):
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
