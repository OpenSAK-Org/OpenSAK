"""
tests/unit-tests/test_found_databases_985.py — offer to add OpenSAK databases
that already exist in the chosen database folder (issue #985).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from opensak import settings_store as ss
from opensak.db.database import SCHEMA_VERSION
from opensak.db.discover import (
    FoundDatabase,
    find_unregistered_databases,
    opensak_schema_version,
)
from opensak.gui.dialogs import database_dialog as dd
from opensak.gui.dialogs import found_databases_dialog as fdd
from opensak.gui.dialogs import welcome_wizard as ww
from opensak.lang import load_language, tr


@pytest.fixture(autouse=True)
def _language():
    load_language("en")


def _opensak_db(path: Path, version: int = SCHEMA_VERSION) -> Path:
    """A minimal file that passes as an OpenSAK database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE caches (id INTEGER PRIMARY KEY, gc_code TEXT)")
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()
    return path


def _names(found: list[FoundDatabase]) -> list[str]:
    return [f.name for f in found]


# ── discover ──────────────────────────────────────────────────────────────────

class TestFindUnregistered:
    def test_finds_databases_not_in_the_list(self, tmp_path):
        _opensak_db(tmp_path / "Denmark.db")
        _opensak_db(tmp_path / "Sweden.db")
        _opensak_db(tmp_path / "Trip.db")
        found = find_unregistered_databases(tmp_path, [tmp_path / "Trip.db"])
        assert _names(found) == ["Denmark", "Sweden"]
        assert found[0].path == tmp_path / "Denmark.db"
        assert found[0].size_bytes > 0

    def test_registered_paths_match_when_spelled_differently(self, tmp_path):
        _opensak_db(tmp_path / "Trip.db")
        (tmp_path / "sub").mkdir()
        assert find_unregistered_databases(
            tmp_path, [tmp_path / "sub" / ".." / "Trip.db"]
        ) == []

    def test_a_real_opensak_database_is_recognised(self, tmp_path):
        from opensak.db.database import dispose_engine, init_db
        path = tmp_path / "Real.db"
        init_db(db_path=path)
        dispose_engine(path)
        assert opensak_schema_version(path) == SCHEMA_VERSION
        assert _names(find_unregistered_databases(tmp_path, [])) == ["Real"]

    def test_skips_what_is_not_an_opensak_database(self, tmp_path):
        (tmp_path / "notes.db").write_text("not sqlite at all", encoding="utf-8")
        conn = sqlite3.connect(str(tmp_path / "other.db"))
        conn.execute("CREATE TABLE things (x)")
        conn.commit()
        conn.close()
        # A GSAK database has a Caches table too, with Code instead of gc_code.
        conn = sqlite3.connect(str(tmp_path / "gsak.db"))
        conn.execute("CREATE TABLE Caches (Code TEXT)")
        conn.commit()
        conn.close()
        assert find_unregistered_databases(tmp_path, []) == []

    def test_skips_a_database_from_a_newer_opensak(self, tmp_path):
        _opensak_db(tmp_path / "Future.db", version=SCHEMA_VERSION + 1)
        assert find_unregistered_databases(tmp_path, []) == []

    def test_skips_sidecars_hidden_files_other_suffixes_and_subfolders(self, tmp_path):
        _opensak_db(tmp_path / "Trip.db")
        (tmp_path / "Trip.db-wal").write_bytes(b"")
        (tmp_path / "Trip.db-shm").write_bytes(b"")
        _opensak_db(tmp_path / ".hidden.db")
        _opensak_db(tmp_path / "Trip.sqlite")
        _opensak_db(tmp_path / "Trip.pre-migration-schema23.db")
        _opensak_db(tmp_path / "backups" / "Trip.pre-migration-schema22.db")
        _opensak_db(tmp_path / "elsewhere" / "Nested.db")
        assert _names(find_unregistered_databases(tmp_path, [])) == ["Trip"]

    def test_missing_folder_gives_nothing(self, tmp_path):
        assert find_unregistered_databases(tmp_path / "nope", []) == []

    def test_reading_changes_nothing(self, tmp_path):
        path = _opensak_db(tmp_path / "Old.db", version=3)
        before_mtime = path.stat().st_mtime_ns
        before_files = sorted(p.name for p in tmp_path.iterdir())
        assert _names(find_unregistered_databases(tmp_path, [])) == ["Old"]
        assert path.stat().st_mtime_ns == before_mtime
        assert sorted(p.name for p in tmp_path.iterdir()) == before_files
        assert opensak_schema_version(path) == 3  # not migrated


# ── dialog ────────────────────────────────────────────────────────────────────

def _found(tmp_path: Path, *names: str) -> list[FoundDatabase]:
    for name in names:
        _opensak_db(tmp_path / f"{name}.db")
    return find_unregistered_databases(tmp_path, [])


class TestDialog:
    def test_everything_is_ticked(self, qtbot, tmp_path):
        found = _found(tmp_path, "A", "B")
        d = fdd.FoundDatabasesDialog(found, tmp_path)
        qtbot.addWidget(d)
        assert d.selected_paths() == [tmp_path / "A.db", tmp_path / "B.db"]

    def test_untick_one(self, qtbot, tmp_path):
        found = _found(tmp_path, "A", "B")
        d = fdd.FoundDatabasesDialog(found, tmp_path)
        qtbot.addWidget(d)
        d._list.item(0).setCheckState(Qt.CheckState.Unchecked)
        assert d.selected_paths() == [tmp_path / "B.db"]
        assert d._add_btn.isEnabled()

    def test_add_is_disabled_with_nothing_ticked(self, qtbot, tmp_path):
        found = _found(tmp_path, "A")
        d = fdd.FoundDatabasesDialog(found, tmp_path)
        qtbot.addWidget(d)
        d._list.item(0).setCheckState(Qt.CheckState.Unchecked)
        assert not d._add_btn.isEnabled()

    def test_not_now_adds_nothing(self, tmp_path, monkeypatch):
        found = _found(tmp_path, "A")
        monkeypatch.setattr(fdd.FoundDatabasesDialog, "exec",
                            lambda self: QDialog.DialogCode.Rejected)
        assert fdd.ask_which_to_add(found, tmp_path) == []

    def test_add_returns_the_ticked_ones(self, tmp_path, monkeypatch):
        found = _found(tmp_path, "A")
        monkeypatch.setattr(fdd.FoundDatabasesDialog, "exec",
                            lambda self: QDialog.DialogCode.Accepted)
        assert fdd.ask_which_to_add(found, tmp_path) == [tmp_path / "A.db"]


# ── Welcome Wizard ────────────────────────────────────────────────────────────

@pytest.fixture
def install_dir(tmp_path, monkeypatch):
    bootstrap = tmp_path / "bootstrap.json"
    monkeypatch.setattr(ss, "_bootstrap_path", lambda: bootstrap)
    monkeypatch.setattr(ss, "_store", None)
    folder = tmp_path / "install"
    folder.mkdir()
    ss.set_install_dir(folder)
    return folder


@pytest.fixture
def asked(monkeypatch):
    """Answers the found-databases dialog with everything it was shown."""
    calls: list[list[str]] = []

    def fake(found, folder, parent=None):
        calls.append([f.name for f in found])
        return [f.path for f in found]

    monkeypatch.setattr(fdd, "ask_which_to_add", fake)
    return calls


def _wizard_on_db_page(qtbot, db_dir: Path) -> ww.WelcomeWizard:
    w = ww.WelcomeWizard()
    qtbot.addWidget(w)
    w._db_row.set_path(db_dir)
    w._stack.setCurrentIndex(w._db_page_index)
    return w


def _listed_paths() -> list[str]:
    return [e["path"] for e in ss.get_store().get("databases.list") or []]


class TestWizard:
    def test_offers_and_adds_existing_databases(self, qtbot, install_dir, tmp_path, asked):
        db_dir = tmp_path / "dbs"
        _opensak_db(db_dir / "Denmark.db")
        _opensak_db(db_dir / "Sweden.db")
        w = _wizard_on_db_page(qtbot, db_dir)
        w._go_next()
        assert asked == [["Denmark", "Sweden"]]
        w._backup_row.set_path(tmp_path / "usb")
        w._finish()
        listed = _listed_paths()
        assert str(db_dir / "Denmark.db") in listed
        assert str(db_dir / "Sweden.db") in listed

    def test_default_db_is_not_offered_on_a_fresh_install(
        self, qtbot, install_dir, tmp_path, asked
    ):
        # The manager picks up <folder>/Default.db as the Default database.
        db_dir = tmp_path / "dbs"
        _opensak_db(db_dir / "Default.db")
        _opensak_db(db_dir / "Trip.db")
        w = _wizard_on_db_page(qtbot, db_dir)
        w._go_next()
        assert asked == [["Trip"]]

    def test_databases_already_in_the_list_are_not_offered(
        self, qtbot, install_dir, tmp_path, asked
    ):
        db_dir = tmp_path / "dbs"
        _opensak_db(db_dir / "Trip.db")
        _opensak_db(db_dir / "New.db")
        ss.get_store().set("databases.list", [
            {"name": "Trip", "path": str(db_dir / "Trip.db")},
        ])
        w = _wizard_on_db_page(qtbot, db_dir)
        w._go_next()
        assert asked == [["New"]]

    def test_nothing_found_asks_nothing(self, qtbot, install_dir, tmp_path, asked):
        w = _wizard_on_db_page(qtbot, tmp_path / "empty")
        w._go_next()
        assert asked == []

    def test_same_folder_is_not_asked_twice(self, qtbot, install_dir, tmp_path, asked):
        db_dir = tmp_path / "dbs"
        _opensak_db(db_dir / "Trip.db")
        w = _wizard_on_db_page(qtbot, db_dir)
        w._go_next()
        w._go_back()
        w._go_next()
        assert asked == [["Trip"]]

    def test_a_changed_folder_is_asked_again(self, qtbot, install_dir, tmp_path, asked):
        _opensak_db(tmp_path / "a" / "One.db")
        _opensak_db(tmp_path / "b" / "Two.db")
        w = _wizard_on_db_page(qtbot, tmp_path / "a")
        w._go_next()
        w._go_back()
        w._db_row.set_path(tmp_path / "b")
        w._go_next()
        assert asked == [["One"], ["Two"]]
        assert w._dbs_to_add == [tmp_path / "b" / "Two.db"]

    def test_skip_adds_nothing(self, qtbot, install_dir, tmp_path, asked):
        db_dir = tmp_path / "dbs"
        _opensak_db(db_dir / "Trip.db")
        w = _wizard_on_db_page(qtbot, db_dir)
        w._go_next()
        w._skip()
        assert str(db_dir / "Trip.db") not in _listed_paths()


# ── Manage Databases → Scan database folder ───────────────────────────────────

class _Manager:
    def __init__(self, databases):
        self.databases = list(databases)
        self.active = self.databases[0] if self.databases else None
        self.opened: list[Path] = []

    def open_database(self, path: Path):
        if path.name == "Broken.db":
            raise ValueError("cannot open")
        self.opened.append(path)
        info = dd.DatabaseInfo(path.stem, path)
        self.databases.append(info)
        return info


@pytest.fixture
def db_dir(tmp_path, monkeypatch):
    folder = tmp_path / "dbs"
    folder.mkdir()
    monkeypatch.setattr("opensak.settings_store.get_db_dir", lambda: folder)
    return folder


@pytest.fixture
def boxes(monkeypatch):
    info, warn = MagicMock(), MagicMock()
    monkeypatch.setattr(dd.QMessageBox, "information", info)
    monkeypatch.setattr(dd.QMessageBox, "warning", warn)
    return info, warn


def _manage(qtbot, monkeypatch, manager):
    monkeypatch.setattr(dd, "get_db_manager", lambda: manager)
    d = dd.DatabaseManagerDialog()
    qtbot.addWidget(d)
    return d


class TestScanDatabaseFolder:
    def test_nothing_new_says_so(self, qtbot, monkeypatch, db_dir, boxes):
        _opensak_db(db_dir / "Default.db")
        manager = _Manager([dd.DatabaseInfo("Default", db_dir / "Default.db")])
        d = _manage(qtbot, monkeypatch, manager)
        d._scan_database_folder()
        info, _ = boxes
        assert info.call_args.args[2] == tr("db_found_none", folder=str(db_dir))

    def test_adds_the_ticked_databases(self, qtbot, monkeypatch, db_dir, boxes):
        _opensak_db(db_dir / "Default.db")
        _opensak_db(db_dir / "Trip.db")
        manager = _Manager([dd.DatabaseInfo("Default", db_dir / "Default.db")])
        d = _manage(qtbot, monkeypatch, manager)
        monkeypatch.setattr(dd, "ask_which_to_add",
                            lambda found, folder, parent=None: [f.path for f in found])
        d._scan_database_folder()
        assert manager.opened == [db_dir / "Trip.db"]
        assert d._list.count() == 2
        info, _ = boxes
        assert "Trip" in info.call_args.args[2]

    def test_not_now_adds_nothing(self, qtbot, monkeypatch, db_dir, boxes):
        _opensak_db(db_dir / "Trip.db")
        manager = _Manager([])
        d = _manage(qtbot, monkeypatch, manager)
        monkeypatch.setattr(dd, "ask_which_to_add", lambda *a, **k: [])
        d._scan_database_folder()
        assert manager.opened == []
        info, warn = boxes
        info.assert_not_called()
        warn.assert_not_called()

    def test_one_failure_does_not_stop_the_rest(self, qtbot, monkeypatch, db_dir, boxes):
        _opensak_db(db_dir / "Broken.db")
        _opensak_db(db_dir / "Trip.db")
        manager = _Manager([])
        d = _manage(qtbot, monkeypatch, manager)
        monkeypatch.setattr(dd, "ask_which_to_add",
                            lambda found, folder, parent=None: [f.path for f in found])
        d._scan_database_folder()
        assert manager.opened == [db_dir / "Trip.db"]
        info, warn = boxes
        assert "Broken.db" in warn.call_args.args[2]
        info.assert_called_once()
