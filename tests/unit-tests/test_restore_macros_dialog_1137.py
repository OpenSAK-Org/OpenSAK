"""
tests/unit-tests/test_restore_macros_dialog_1137.py — "Also restore macros"
in File → Restore from backup… (#1137, part of #942).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak.backup.backupset import KIND_MANUAL, write_backup_set
from opensak.backup.macros_backup import macros_root
from opensak.db.database import dispose_engine
from opensak.gui.dialogs import restore_dialog as rdlg
from opensak.gui.dialogs.restore_dialog import (
    MacroConflictDialog,
    RestoreDialog,
    RestoreWorker,
)
from opensak.lang import load_language, tr


@pytest.fixture(autouse=True)
def english():
    load_language("en")


@pytest.fixture
def manager(qapp, tmp_path):
    from opensak.db.manager import get_db_manager
    m = get_db_manager()
    m.new_database("Trip", tmp_path / "dbs" / "Trip.db")
    dispose_engine(tmp_path / "dbs" / "Trip.db")
    yield m
    dispose_engine()


@pytest.fixture
def macros() -> Path:
    root = macros_root()
    (root / "tag.lua").write_text("print('tag')\n", encoding="utf-8")
    (root / "settings").mkdir(exist_ok=True)
    (root / "settings" / "tag.json").write_text("{}\n", encoding="utf-8")
    return root


def _backup(manager, tmp_path):
    db = next(d for d in manager.databases if d.name == "Trip")
    return write_backup_set([db], KIND_MANUAL, folder=tmp_path / "backups").backup_set


@pytest.fixture
def boxes():
    with patch.object(rdlg, "QMessageBox") as box:
        yield box


@pytest.fixture
def sync_worker(monkeypatch):
    monkeypatch.setattr(RestoreWorker, "start", lambda self: self.run())


def _open(qtbot, folder: Path) -> RestoreDialog:
    with patch.object(rdlg, "get_backup_dir", return_value=folder):
        d = RestoreDialog()
    qtbot.addWidget(d)
    return d


def _untick_databases(d: RestoreDialog) -> None:
    for i in range(d._db_list.count()):
        d._db_list.item(i).setCheckState(Qt.CheckState.Unchecked)


def test_ticked_for_a_set_with_macros(qtbot, manager, macros, tmp_path, boxes):
    _backup(manager, tmp_path)
    d = _open(qtbot, tmp_path / "backups")
    assert d._macros_cb.isEnabled() and d._macros_cb.isChecked()
    assert d._macros_cb.toolTip() == tr("restore_macros_count", count=2)


def test_disabled_for_a_set_without_macros(qtbot, manager, tmp_path, boxes):
    _backup(manager, tmp_path)
    d = _open(qtbot, tmp_path / "backups")
    assert not d._macros_cb.isEnabled() and not d._macros_cb.isChecked()
    assert d._macros_cb.toolTip() == tr("restore_macros_none")


def test_macros_only_restore(qtbot, manager, macros, tmp_path, boxes):
    _backup(manager, tmp_path)
    (macros / "tag.lua").unlink()
    d = _open(qtbot, tmp_path / "backups")
    _untick_databases(d)

    d._start()

    assert (macros / "tag.lua").read_text(encoding="utf-8") == "print('tag')\n"
    title, text = boxes.information.call_args.args[1:3]
    assert title == tr("restore_done_title")
    assert tr("restore_done_macros", restored=1, replaced=0, kept=0, unchanged=1) in text
    assert d.result() == RestoreDialog.DialogCode.Accepted


def test_unticked_macros_are_left_alone(qtbot, manager, macros, tmp_path, boxes,
                                        sync_worker):
    _backup(manager, tmp_path)
    (macros / "tag.lua").unlink()
    boxes.question.return_value = rdlg.QMessageBox.StandardButton.No
    d = _open(qtbot, tmp_path / "backups")
    d._macros_cb.setChecked(False)
    d._start()
    assert not (macros / "tag.lua").exists()


def test_conflicts_ask_and_cancel_stops_everything(
    qtbot, manager, macros, tmp_path, boxes, sync_worker,
):
    _backup(manager, tmp_path)
    (macros / "tag.lua").write_text("-- mine\n", encoding="utf-8")
    before = list(manager.databases)
    d = _open(qtbot, tmp_path / "backups")
    with patch.object(MacroConflictDialog, "exec",
                      return_value=MacroConflictDialog.DialogCode.Rejected):
        d._start()
    assert (macros / "tag.lua").read_text(encoding="utf-8") == "-- mine\n"
    assert manager.databases == before


def test_conflicts_replaced_when_ticked(
    qtbot, manager, macros, tmp_path, boxes, sync_worker,
):
    _backup(manager, tmp_path)
    (macros / "tag.lua").write_text("-- mine\n", encoding="utf-8")
    boxes.question.return_value = rdlg.QMessageBox.StandardButton.No
    d = _open(qtbot, tmp_path / "backups")

    def _exec(dialog):
        dialog._set_all(Qt.CheckState.Checked)
        return MacroConflictDialog.DialogCode.Accepted

    with patch.object(MacroConflictDialog, "exec", _exec):
        d._start()

    assert (macros / "tag.lua").read_text(encoding="utf-8") == "print('tag')\n"
    assert len(list(macros.glob("tag.lua.*.bak"))) == 1
    text = boxes.question.call_args.args[2]
    assert tr("restore_done_macros", restored=0, replaced=1, kept=0, unchanged=1) in text


def test_busy_macro_blocks_the_macros_restore(qtbot, manager, macros, tmp_path, boxes):
    _backup(manager, tmp_path)
    d = _open(qtbot, tmp_path / "backups")
    with patch("opensak.macro.macro_running", return_value=True):
        d._start()
    assert boxes.warning.call_args.args[2] == tr("restore_macros_busy")


def test_conflict_dialog_lists_and_returns_choices(qtbot):
    from opensak.backup.macros_restore import MacroRestoreItem
    items = [
        MacroRestoreItem("a.lua", "macros/a.lua", Path("/x/a.lua")),
        MacroRestoreItem("stats.db3", "macro_databases/stats.db3", Path("/x/stats.db3"),
                         database=True),
    ]
    dialog = MacroConflictDialog(items)
    qtbot.addWidget(dialog)
    assert dialog.replace_rels() == []
    assert dialog._list.item(1).text() == tr("restore_macros_conflict_db", name="stats.db3")
    dialog._list.item(0).setCheckState(Qt.CheckState.Checked)
    assert dialog.replace_rels() == ["a.lua"]
    dialog._set_all(Qt.CheckState.Checked)
    assert dialog.replace_rels() == ["a.lua", "stats.db3"]
