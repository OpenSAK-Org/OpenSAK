"""
tests/unit-tests/test_restore_clean_install_1193.py — a 1.22.0 backup set
with a companion database and macros restores on a clean install (#1193).

The backup is written in one isolated installation (home folder, install
folder, macros folder); then a second, empty one is set up the way a new
computer would be, and the set is restored there: the database with its
companion, the macros, their settings and databases, and the approvals.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from opensak.backup.backupset import KIND_MANUAL, read_backup_set, write_backup_set
from opensak.backup.macros_backup import macros_root
from opensak.backup.macros_restore import plan_macro_restore, restore_macros
from opensak.backup.restore import restore_database
from opensak.db import companion
from opensak.db.database import dispose_engine
from opensak.macro import db_access
from opensak.macro.db_access import WriteApproval, read_macro_source, source_hash

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
MACRO = "-- OpenSAK Macro\n-- Name: Tagger\n-- Permissions: write\nprint('tag')\n"


def _new_machine(home: Path, monkeypatch) -> None:
    """Point OpenSAK at an empty home folder, as on a newly set up computer."""
    import opensak.db.manager as dbmanager
    import opensak.settings_store as ss

    dispose_engine()
    monkeypatch.setattr(ss.Path, "home", lambda: home)
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    ss.reset_store()
    monkeypatch.setattr(dbmanager, "_manager", None)
    db_access.reset_session()
    companion.reset()


def test_backup_with_companion_and_macros_restores_on_a_clean_install(
    tmp_path, monkeypatch,
):
    from opensak.db.manager import get_db_manager

    # ── The old computer ──────────────────────────────────────────────────
    manager = get_db_manager()
    db = manager.new_database("Trip", tmp_path / "old" / "dbs" / "Trip.db")
    dispose_engine(db.path)
    comp = companion.create(db.path)
    conn = sqlite3.connect(str(comp))
    conn.execute("CREATE TABLE my_notes (code TEXT)")
    conn.execute("INSERT INTO my_notes VALUES ('GC1')")
    conn.commit()
    conn.close()

    macros = macros_root()
    macro = macros / "tagger.lua"
    macro.write_text(MACRO, encoding="utf-8")
    (macros / "settings").mkdir(exist_ok=True)
    (macros / "settings" / "tagger.json").write_text('{"tag": "x"}\n', encoding="utf-8")
    stats = sqlite3.connect(str(macros / "tagger.db3"))
    stats.execute("CREATE TABLE seen (code TEXT)")
    stats.execute("INSERT INTO seen VALUES ('GC2')")
    stats.commit()
    stats.close()
    sha = source_hash(read_macro_source(macro))
    db_access.approve(macro, WriteApproval.ALWAYS, sha)

    written = write_backup_set(
        [db], KIND_MANUAL, folder=tmp_path / "usb", now=NOW,
    ).backup_set
    # The backup drive goes to the new computer.
    carried = tmp_path / "carried" / written.path.name
    shutil.copytree(written.path, carried)

    # ── The new computer ──────────────────────────────────────────────────
    _new_machine(tmp_path / "newhome", monkeypatch)
    new_macros = macros_root()
    assert new_macros != macros
    assert not any(new_macros.iterdir())
    assert db_access.approvals() == []

    backup_set = read_backup_set(carried)
    entry = backup_set.databases[0]
    assert entry.companion is not None

    info = restore_database(backup_set, entry)
    restored_comp = companion.companion_path(info.path)
    rows = sqlite3.connect(str(restored_comp)).execute(
        "SELECT code FROM my_notes").fetchall()
    assert rows == [("GC1",)]

    plan = plan_macro_restore(backup_set)
    assert plan.conflicts == [] and plan.outside == []
    result = restore_macros(plan)

    assert sorted(result.restored) == [
        "settings/tagger.json", "tagger.db3", "tagger.lua",
    ]
    assert (new_macros / "tagger.lua").read_text(encoding="utf-8") == MACRO
    assert (new_macros / "settings" / "tagger.json").is_file()
    seen = sqlite3.connect(str(new_macros / "tagger.db3")).execute(
        "SELECT code FROM seen").fetchall()
    assert seen == [("GC2",)]

    # The macro's "Always" approval came along, for the macro's new place.
    assert result.approvals == 1
    assert db_access.is_approved(new_macros / "tagger.lua", sha)
    dispose_engine()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    dispose_engine()
    companion.reset()
