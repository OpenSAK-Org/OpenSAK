"""
tests/unit-tests/test_migrate_all_databases.py — "Apply to all databases"
after a pre-migration backup notice (follow-up to issue #549).

When opening one database migrated it, the notice offers to back up and
update every other outdated database in the list in one go, without
switching away from the active database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from opensak.backup.premigration import backup_path_for, take_notices
from opensak.db import database
from opensak.db.database import (
    SCHEMA_VERSION,
    _migrated_paths,
    dispose_engine,
    init_db,
    migrate_databases,
    needs_migration,
)

OLD = SCHEMA_VERSION - 1


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _make_old_db(path: Path, version: int = OLD) -> Path:
    """A real OpenSAK database, rewound to look like it's at *version*."""
    _migrated_paths.discard(path)
    init_db(db_path=path)
    dispose_engine(path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()
    _migrated_paths.discard(path)
    take_notices()
    return path


def _make_current_db(path: Path) -> Path:
    _migrated_paths.discard(path)
    init_db(db_path=path)
    dispose_engine(path)
    return path


# ── needs_migration / migrate_databases ──────────────────────────────────────

class TestNeedsMigration:
    def test_old_database(self, tmp_path):
        assert needs_migration(_make_old_db(tmp_path / "Old.db"))

    def test_current_database(self, tmp_path):
        assert not needs_migration(_make_current_db(tmp_path / "New.db"))

    def test_missing_file(self, tmp_path):
        assert not needs_migration(tmp_path / "nope.db")


class TestMigrateDatabases:
    def test_migrates_and_backs_up_each_old_database(self, tmp_path):
        a = _make_old_db(tmp_path / "A.db")
        b = _make_old_db(tmp_path / "B.db")

        assert migrate_databases([a, b]) == []

        assert _user_version(a) == SCHEMA_VERSION
        assert _user_version(b) == SCHEMA_VERSION
        assert backup_path_for(a, OLD).exists()
        assert backup_path_for(b, OLD).exists()
        assert {n.db_path for n in take_notices()} == {a, b}

    def test_active_database_is_left_untouched(self, tmp_path):
        other = _make_old_db(tmp_path / "Other.db")
        active = _make_current_db(tmp_path / "Active.db")
        init_db(db_path=active)
        try:
            migrate_databases([other])
            assert Path(database.get_engine().url.database).resolve() == active.resolve()
        finally:
            dispose_engine(active)
        take_notices()

    def test_current_database_is_skipped(self, tmp_path):
        db = _make_current_db(tmp_path / "New.db")
        assert migrate_databases([db]) == []
        assert not backup_path_for(db, OLD).exists()
        assert take_notices() == []

    def test_failure_on_one_does_not_stop_the_others(self, tmp_path):
        a = _make_old_db(tmp_path / "A.db")
        b = _make_old_db(tmp_path / "B.db")
        real_open = database._open_engine

        def flaky(path):
            if path == a:
                raise RuntimeError("boom")
            return real_open(path)

        with patch.object(database, "_open_engine", side_effect=flaky):
            failed = migrate_databases([a, b])

        assert [(p, str(e)) for p, e in failed] == [(a, "boom")]
        assert _user_version(b) == SCHEMA_VERSION
        take_notices()


# ── GUI: the "Apply to all databases" button ─────────────────────────────────

def _fake_manager(active: Path, others: list[Path]):
    dbs = [SimpleNamespace(name=p.stem, path=p, exists=p.exists())
           for p in [active, *others]]
    return MagicMock(active_path=active, databases=dbs)


class TestMainWindowApplyToAll:
    def test_no_other_outdated_databases_plain_dialog(self, qapp, tmp_path):
        from opensak.gui import mainwindow

        active = _make_old_db(tmp_path / "Active.db")
        current = _make_current_db(tmp_path / "Current.db")
        migrate_databases([active])  # queues the notice for the active DB
        manager = _fake_manager(active, [current])
        with patch.object(mainwindow, "QMessageBox") as box, \
                patch("opensak.db.manager.get_db_manager", return_value=manager):
            mainwindow.MainWindow.show_premigration_backup_notices(MagicMock())

        box.information.assert_called_once()
        box.assert_not_called()  # no custom box with an extra button

    def test_offers_button_and_migrates_others(self, qapp, tmp_path):
        from opensak.gui import mainwindow
        from opensak.lang import tr

        active = _make_old_db(tmp_path / "Active.db")
        other = _make_old_db(tmp_path / "Other.db")
        migrate_databases([active])
        manager = _fake_manager(active, [other])
        window = MagicMock()
        with patch.object(mainwindow, "QMessageBox") as box, \
                patch("opensak.db.manager.get_db_manager", return_value=manager):
            msg = box.return_value
            msg.clickedButton.return_value = msg.addButton.return_value
            mainwindow.MainWindow.show_premigration_backup_notices(window)

        assert msg.addButton.call_args_list[0].args[0] == tr(
            "premigration_apply_all_btn", count=1
        )
        window._migrate_other_databases.assert_called_once()
        (pending,) = window._migrate_other_databases.call_args.args
        assert [db.path for db in pending] == [other]

    def test_migrate_other_databases_reports_backups(self, qapp, tmp_path):
        from opensak.gui import mainwindow

        other = _make_old_db(tmp_path / "Other.db")
        pending = [SimpleNamespace(name="Other", path=other, exists=True)]
        with patch.object(mainwindow, "QMessageBox") as box, \
                patch.object(mainwindow, "QProgressDialog"):
            mainwindow.MainWindow._migrate_other_databases(MagicMock(), pending)

        assert _user_version(other) == SCHEMA_VERSION
        box.warning.assert_not_called()
        _parent, _title, text = box.information.call_args.args
        assert str(backup_path_for(other, OLD)) in text
