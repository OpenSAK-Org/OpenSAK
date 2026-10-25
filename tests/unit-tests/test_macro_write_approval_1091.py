"""
tests/unit-tests/test_macro_write_approval_1091.py — a macro's write
approval belongs to its code, not only to its path (#1091).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.macro import FolderApproval, FolderPermission, MacroError, MacroRuntime, WriteApproval
from opensak.macro import db_access
from opensak.macro.db_access import source_hash


class Host:
    """A MacroHost with what the write functions use; records every write
    question with its *change* and *editing*."""

    def __init__(self, answer=WriteApproval.ALWAYS):
        self.answer = answer
        self.asked: list[tuple[Path | None, db_access.MacroChange | None, bool]] = []
        # Answers and questions of approve_macro_change() / approve_folder()
        self.change_answer = WriteApproval.ALWAYS
        self.changes: list[tuple[Path, db_access.MacroChange, bool]] = []
        self.folder_answer = FolderApproval.ALWAYS
        self.folders: list[tuple[Path, bool, Path | None]] = []

    def database_name(self):
        return "TestDB"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        self.asked.append((macro, change, editing))
        return self.answer

    def approve_macro_change(self, macro, change, editing=False):
        self.changes.append((macro, change, editing))
        return self.change_answer

    def approve_folder(self, target, folder, write, macro=None):
        self.folders.append((folder, write, macro))
        return self.folder_answer

    def caches_changed(self, codes, added):
        pass

    def set_corrected_coords(self, gc_code, lat, lon):
        return True

    def end_macro(self):
        pass


@pytest.fixture(scope="module", autouse=True)
def database(tmp_db):
    with get_session() as s:
        s.add(Cache(gc_code="GCA1", name="Approval test", cache_type="Traditional Cache",
                    latitude=47.0, longitude=8.0))
    yield


def _write(path: Path, sort: int, header: str = "") -> str:
    source = f'{header}opensak.update("GCA1", {{ user_sort = {sort} }})\n'
    path.write_text(source, encoding="utf-8")
    return source


def _run_file(path, host, external_editor=False):
    """Run *path* the way the Macro manager (or the macro server) does."""
    MacroRuntime(host, output=lambda _t: None, external_editor=external_editor).run(
        db_access.read_macro_source(path), chunk_name=path.name, base_dir=path.parent,
        macro_path=path,
    )


def _sort() -> int | None:
    with get_session() as s:
        return s.query(Cache).filter_by(gc_code="GCA1").one().user_sort


def test_replaced_file_is_asked_about_again(tmp_path):
    macro = tmp_path / "update.lua"
    _write(macro, 1, "-- Version: 1.0\n-- Permissions: none\n")
    host = Host()
    _run_file(macro, host)
    _run_file(macro, host)
    assert len(host.asked) == 1 and host.asked[0][1] is None

    # A downloaded update replaces the file: it inherits nothing.
    _write(macro, 2, "-- Version: 1.1\n-- Permissions: write, open\n")
    host.change_answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="did not allow"):
        _run_file(macro, host)
    assert _sort() == 1
    [(_, change, _)] = host.changes
    assert change == db_access.MacroChange("1.0", "1.1", ("write", "open"), write=True)
    assert len(host.asked) == 1         # declined as a whole: no second question
    # Denied: the stored approval still is the old code's.
    assert db_access.approvals()[0].version == "1.0"

    # Allowed again: the new code is approved from now on.
    host.change_answer = WriteApproval.ALWAYS
    _run_file(macro, host)
    db_access.reset_session()
    _run_file(macro, host)
    assert len(host.changes) == 2 and len(host.asked) == 1 and _sort() == 2
    assert [a.version for a in db_access.approvals()] == ["1.1"]


def test_session_approval_belongs_to_the_code_too(tmp_path):
    macro = tmp_path / "session.lua"
    _write(macro, 3)
    host = Host(WriteApproval.SESSION)
    _run_file(macro, host)
    _write(macro, 4)
    _run_file(macro, host)
    assert len(host.asked) == 2 and host.asked[1][1] is not None


def test_same_text_from_every_entry_point_has_the_same_hash(tmp_path):
    """A BOM and CRLF newlines do not count: the file hashes as it runs."""
    plain, windows = tmp_path / "plain.lua", tmp_path / "windows.lua"
    plain.write_bytes(b'print("a")\nprint("b")\n')
    windows.write_bytes(b'\xef\xbb\xbfprint("a")\r\nprint("b")\r\n')
    assert (source_hash(db_access.read_macro_source(plain))
            == source_hash(db_access.read_macro_source(windows))
            == source_hash('print("a")\nprint("b")\n')
            == source_hash('﻿print("a")\nprint("b")\n'))


def test_called_macro_is_checked_against_its_file(tmp_path):
    helper = tmp_path / "helper.lua"
    _write(helper, 5)
    caller = tmp_path / "caller.lua"
    caller.write_text('opensak.run_macro("helper.lua")\n', encoding="utf-8")
    from opensak.macro import FolderPermission
    host = Host()
    perms = [FolderPermission(str(tmp_path), read=True)]

    def run():
        MacroRuntime(host, output=lambda _t: None, folder_permissions=perms).run(
            db_access.read_macro_source(caller), chunk_name="caller.lua",
            base_dir=tmp_path, macro_path=caller,
            # The caller's editor hashes never approve the called macro.
            edited_from={source_hash('opensak.update("GCA1", { user_sort = 6 })\n')},
        )

    run()
    run()
    assert [a[0] for a in host.asked] == [helper]
    _write(helper, 6)
    host.change_answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="did not allow macro 'helper.lua'"):
        run()
    assert [c[0] for c in host.changes] == [helper]


# ── OpenSAK's own editor ─────────────────────────────────────────────────────

def test_editor_text_counts_as_an_edit_of_what_it_had(tmp_path):
    macro = tmp_path / "edit.lua"
    loaded = _write(macro, 7)
    host = Host()
    _run_file(macro, host)
    # Edited in OpenSAK's editor and run before saving: no new question.
    edited = loaded.replace("7", "8")
    MacroRuntime(host, output=lambda _t: None).run(
        edited, chunk_name=macro.name, macro_path=macro,
        edited_from={source_hash(loaded), source_hash(edited)},
    )
    assert len(host.asked) == 1 and _sort() == 8
    # Saved by the editor: the approval moves to the new code.
    macro.write_text(edited, encoding="utf-8")
    assert db_access.carry_over(macro, {source_hash(loaded)}, source_hash(edited))
    db_access.reset_session()
    _run_file(macro, host)
    assert len(host.asked) == 1


def test_editor_save_does_not_approve_a_replaced_file(tmp_path):
    macro = tmp_path / "replaced.lua"
    approved = _write(macro, 9)
    db_access.approve(macro, WriteApproval.ALWAYS, source_hash(approved))
    # Replaced outside OpenSAK, then opened in the editor and saved.
    replaced = _write(macro, 10)
    assert not db_access.carry_over(macro, {source_hash(replaced)}, source_hash(replaced))
    host = Host(WriteApproval.DENY)
    host.change_answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="did not allow"):
        _run_file(macro, host)
    assert len(host.changes) == 1


def test_dialog_editor_carries_the_approval_over(qtbot, tmp_path, monkeypatch):
    from opensak.gui.dialogs.macro_dialog import MacroDialog
    from opensak.macro import recent
    monkeypatch.setattr(recent, "recent_macros", lambda: [])
    macro = tmp_path / "dialog.lua"
    loaded = _write(macro, 11)
    db_access.approve(macro, WriteApproval.ALWAYS, source_hash(loaded))
    dialog = MacroDialog(Host())
    qtbot.addWidget(dialog)
    assert dialog.open_path(macro, ask=False)
    dialog._editor.setPlainText(loaded.replace("11", "12"))
    assert dialog._write(macro)
    assert db_access.is_approved(macro, source_hash(macro.read_text(encoding="utf-8")))

    # Changed on disk and reloaded: a later save carries nothing over.
    _write(macro, 13)
    dialog._reload(macro.read_text(encoding="utf-8"))
    dialog._editor.setPlainText(loaded.replace("11", "14"))
    assert dialog._write(macro)
    assert not db_access.is_approved(macro, source_hash(macro.read_text(encoding="utf-8")))


# ── Runs from an external editor ─────────────────────────────────────────────

def test_while_i_edit_it_covers_only_runs_from_an_external_editor(tmp_path):
    macro = tmp_path / "vscode.lua"
    _write(macro, 15)
    host = Host(WriteApproval.EDITING)
    _run_file(macro, host, external_editor=True)
    assert host.asked == [(macro, None, True)]
    for sort in (16, 17):           # saved in the other editor and run again
        _write(macro, sort)
        _run_file(macro, host, external_editor=True)
    assert len(host.asked) == 1 and _sort() == 17 and host.changes == []
    assert db_access.always_approved() == []

    # The Macro manager still checks the code — and never offers EDITING.
    with pytest.raises(MacroError, match="did not allow"):
        _run_file(macro, host)
    assert host.asked[-1] == (macro, None, False)
    db_access.reset_session()       # OpenSAK restarted
    host.answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="did not allow"):
        _run_file(macro, host, external_editor=True)


# ── Stored approvals ─────────────────────────────────────────────────────────

def test_old_path_list_is_converted(tmp_path):
    from opensak.settings_store import get_store
    kept, gone = tmp_path / "kept.lua", tmp_path / "gone.lua"
    source = _write(kept, 18, "-- Version: 2.0\n")
    get_store().set(db_access.LEGACY_STORE_KEY, [str(kept), str(gone)])
    records = db_access.approvals()
    assert [(r.path, r.sha256, r.version) for r in records] == [
        (str(kept.resolve()), source_hash(source), "2.0")]
    assert get_store().get(db_access.LEGACY_STORE_KEY) is None
    host = Host(WriteApproval.DENY)
    _run_file(kept, host)
    assert host.asked == []


def test_removed_in_settings_ends_every_approval(tmp_path):
    macro = tmp_path / "removed.lua"
    source = _write(macro, 19)
    db_access.approve(macro, WriteApproval.ALWAYS, source_hash(source))
    db_access.approve(macro, WriteApproval.EDITING, source_hash(source))
    db_access.save_always_approved([])
    assert not db_access.is_approved(macro, source_hash(source), editing=True)


# ── Folders allowed to one macro (phase 2) ───────────────────────────────────

def _reader(path: Path, folder: Path, header: str = "") -> str:
    (folder / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    source = f'{header}opensak.read_csv("{(folder / "a.csv").as_posix()}")\n'
    path.write_text(source, encoding="utf-8")
    return source


def _run_reader(path, host, external_editor=False):
    MacroRuntime(host, output=lambda _t: None, folder_permissions=[],
                 external_editor=external_editor).run(
        db_access.read_macro_source(path), chunk_name=path.name, base_dir=path.parent,
        macro_path=path,
    )


def test_folder_allowed_always_belongs_to_the_macro_and_its_code(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    macro = tmp_path / "reader.lua"
    _reader(macro, data, "-- Version: 1\n")
    host = Host()
    _run_reader(macro, host)
    _run_reader(macro, host)
    assert host.folders == [(data.resolve(), False, macro)]

    # Replaced: asked once whether it may have its folder again.
    _reader(macro, data, "-- Version: 2\n")
    host.change_answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="denied by the user"):
        _run_reader(macro, host)
    [(_, change, _)] = host.changes
    assert change.write is False and change.old_version == "1"
    assert change.folders == (FolderPermission(str(data.resolve()), read=True),)
    assert len(host.folders) == 1       # declined as a whole: no folder question

    host.change_answer = WriteApproval.ALWAYS
    _run_reader(macro, host)
    _run_reader(macro, host)
    assert len(host.changes) == 2 and len(host.folders) == 1
    assert db_access.approvals()[0].version == "2"


def test_new_grant_for_changed_code_replaces_the_declined_record(tmp_path):
    data, other = tmp_path / "data", tmp_path / "other"
    data.mkdir()
    other.mkdir()
    macro = tmp_path / "reader.lua"
    _reader(macro, data)
    host = Host()
    _run_reader(macro, host)
    db_access.approve(macro, WriteApproval.ALWAYS, source_hash(_reader(macro, data)))
    # Updated to read another folder: nothing of the old record applies.
    _reader(macro, other)
    _run_reader(macro, host)
    assert host.changes == []           # the old folder does not cover it
    [record] = db_access.approvals()
    assert not record.write
    assert record.folders == (FolderPermission(str(other.resolve()), read=True),)


def test_called_macro_does_not_inherit_its_callers_folders(tmp_path):
    data, macros = tmp_path / "data", tmp_path / "macros"
    data.mkdir()
    macros.mkdir()
    helper = macros / "helper.lua"
    _reader(helper, data)
    caller = macros / "caller.lua"
    caller.write_text(
        f'opensak.read_csv("{(data / "a.csv").as_posix()}")\n'
        'opensak.run_macro("helper.lua")\n', encoding="utf-8")
    host = Host()
    host.folder_answer = FolderApproval.ONCE
    MacroRuntime(host, output=lambda _t: None,
                 folder_permissions=[FolderPermission(str(macros), read=True)]).run(
        db_access.read_macro_source(caller), chunk_name="caller.lua", base_dir=macros,
        macro_path=caller,
    )
    # Allowed to the caller this time only: the helper is asked on its own.
    assert [(f[0], f[2]) for f in host.folders] == [
        (data.resolve(), caller), (data.resolve(), helper)]


def test_while_i_edit_it_keeps_the_folders(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    macro = tmp_path / "vscode.lua"
    _reader(macro, data, "-- v1\n")
    host = Host()
    _run_reader(macro, host, external_editor=True)
    _reader(macro, data, "-- v2\n")
    host.change_answer = WriteApproval.EDITING
    _run_reader(macro, host, external_editor=True)
    _reader(macro, data, "-- v3\n")
    _run_reader(macro, host, external_editor=True)
    assert len(host.changes) == 1 and host.changes[0][2] is True
    assert len(host.folders) == 1
    # Changing databases was never allowed, so it is still asked about.
    assert not db_access.is_approved(macro, "x", editing=True)


def test_settings_list_shows_what_each_macro_may_do(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import Qt
    from opensak.gui.dialogs import settings_dialog as sd
    from opensak.utils import flags
    monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)
    monkeypatch.setattr(flags, "lua_macros", True)
    monkeypatch.setattr(flags, "lua_macro_write", False)
    macro = tmp_path / "reader.lua"
    db_access.grant_folder(macro, tmp_path, False, source_hash(""))
    db_access.grant_folder(macro, tmp_path, True, source_hash(""))
    d = sd.SettingsDialog()
    qtbot.addWidget(d)
    assert d._db_write_list is not None and d._db_write_list.count() == 1
    item = d._db_write_list.item(0)
    # The name, then one line per right (read and write: one folder line)
    assert len(item.text().splitlines()) == 2
    assert item.text().splitlines()[0] == "reader.lua"
    assert item.data(Qt.ItemDataRole.UserRole) == str(macro.resolve())
    d._db_write_list.setCurrentItem(item)
    d._on_remove_db_write()
    d._save()
    assert db_access.approvals() == []
