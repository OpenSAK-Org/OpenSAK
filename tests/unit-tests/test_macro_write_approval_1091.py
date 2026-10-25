"""
tests/unit-tests/test_macro_write_approval_1091.py — a macro's write
approval belongs to its code, not only to its path (#1091).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.macro import MacroError, MacroRuntime, WriteApproval
from opensak.macro import db_access
from opensak.macro.db_access import source_hash


class Host:
    """A MacroHost with what the write functions use; records every write
    question with its *change* and *editing*."""

    def __init__(self, answer=WriteApproval.ALWAYS):
        self.answer = answer
        self.asked: list[tuple[Path | None, db_access.WriteChange | None, bool]] = []

    def database_name(self):
        return "TestDB"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        self.asked.append((macro, change, editing))
        return self.answer

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
    host.answer = WriteApproval.DENY
    with pytest.raises(MacroError, match="did not allow"):
        _run_file(macro, host)
    assert _sort() == 1
    _, change, _ = host.asked[1]
    assert change == db_access.WriteChange("1.0", "1.1", ("write", "open"))
    # Denied: the stored approval still is the old code's.
    assert db_access.approvals()[0].version == "1.0"

    # Allowed again: the new code is approved from now on.
    host.answer = WriteApproval.ALWAYS
    _run_file(macro, host)
    db_access.reset_session()
    _run_file(macro, host)
    assert len(host.asked) == 3 and _sort() == 2
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
    run()
    assert len(host.asked) == 2 and host.asked[1][1] is not None


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
    with pytest.raises(MacroError, match="did not allow"):
        _run_file(macro, host)
    assert host.asked[0][1] is not None


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
    assert len(host.asked) == 1 and _sort() == 17
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
