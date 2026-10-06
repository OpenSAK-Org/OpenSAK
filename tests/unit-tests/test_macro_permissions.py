"""tests/unit-tests/test_macro_permissions.py — folders Lua macros may access.

Covers the Qt-free check (resolve-based, so ".." and symlinks cannot escape),
its use by opensak.read_csv(), approving a folder while a macro runs, and the
Settings → Folder permissions tab.
"""

import os
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import MagicMock

import pytest

from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.macro.permissions import (
    STORE_KEY,
    FolderAccessDenied,
    FolderNotApproved,
    FolderPermission,
    _is_root,
    approvable_folder,
    check_access,
    default_permissions,
    is_root_folder,
    load_permissions,
    protected_inside,
    protected_reason,
    resolve_path,
    safe_write,
    save_permissions,
    with_grant,
)
from opensak.settings_store import get_store


@pytest.fixture(autouse=True)
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "install" / "macros"
    d.mkdir(parents=True)
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d


def _rw(path, read=True, write=True):
    return FolderPermission(str(path), read=read, write=write)


# ── Defaults / storage ───────────────────────────────────────────────────────

def test_defaults_are_temp_read_write_and_macros_read_only(macros_dir):
    temp, macros = default_permissions()
    assert Path(temp.path) == resolve_path(tempfile.gettempdir()) / "opensak"
    assert Path(temp.path).is_dir()
    assert (temp.read, temp.write) == (True, True)
    assert Path(macros.path) == resolve_path(macros_dir)
    assert (macros.read, macros.write) == (True, False)


def test_load_returns_defaults_until_a_list_is_saved():
    assert load_permissions() == default_permissions()
    save_permissions([])
    assert load_permissions() == []
    save_permissions([_rw("/x", write=False)])
    assert get_store().get(STORE_KEY) == [{"path": "/x", "read": True, "write": False}]
    assert load_permissions() == [_rw("/x", write=False)]


def test_saving_the_defaults_stores_nothing():
    save_permissions(default_permissions())
    assert get_store().get(STORE_KEY) is None
    save_permissions([_rw("/x")])
    save_permissions(default_permissions())
    assert get_store().get(STORE_KEY) is None


def test_old_stored_defaults_are_migrated(macros_dir):
    get_store().set(STORE_KEY, [
        {"path": tempfile.gettempdir(), "read": True, "write": True},
        {"path": str(macros_dir), "read": True, "write": False},
    ])
    assert load_permissions() == default_permissions()
    assert get_store().get(STORE_KEY) is None


def test_customised_list_with_whole_temp_folder_is_kept(macros_dir):
    stored = [
        {"path": tempfile.gettempdir(), "read": True, "write": False},
        {"path": str(macros_dir), "read": True, "write": False},
    ]
    get_store().set(STORE_KEY, stored)
    assert [p.to_dict() for p in load_permissions()] == stored


def test_load_skips_malformed_entries():
    get_store().set(STORE_KEY, ["junk", {"read": True}, {"path": "/ok", "read": 1}])
    assert load_permissions() == [FolderPermission("/ok", read=True, write=False)]


# ── OpenSAK's own data ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "cache.db", "x.DB3", "y.sqlite", "z.sqlite3", "cache.db-wal", "cache.db-journal",
    "opensak.json", "opensak.json.bak",
])
def test_database_and_settings_files_are_denied_anywhere(tmp_path, name):
    perms = [_rw(tmp_path)]
    for write in (False, True):
        with pytest.raises(FolderAccessDenied, match="may not"):
            check_access(tmp_path / name, write=write, permissions=perms)


def test_own_files_and_folders_are_denied(tmp_path, monkeypatch, macros_dir):
    from opensak import config, settings_store
    install, dbs = tmp_path / "install", tmp_path / "dbs"
    dbs.mkdir()
    monkeypatch.setattr(settings_store, "get_install_dir", lambda: install)
    monkeypatch.setattr(settings_store, "get_db_dir", lambda: dbs)
    perms = [_rw(tmp_path)]

    for target in (install / "gc_token.json", install / "opensak.log",
                   dbs / "notes.csv", config.get_gc_token_path()):
        assert protected_reason(resolve_path(target)) is not None
        with pytest.raises(FolderAccessDenied):
            check_access(target, write=True, permissions=perms)
    assert protected_reason(resolve_path(settings_store._bootstrap_path())) is not None
    # The macros folder inside the data folder stays usable
    check_access(macros_dir / "a.csv", write=True, permissions=perms)
    check_access(tmp_path / "elsewhere" / "a.gpx", write=True, permissions=perms)


def _own_data(tmp_path, monkeypatch, databases=()):
    from types import SimpleNamespace
    from opensak import settings_store
    install, dbs = tmp_path / "home" / "install", tmp_path / "home" / "dbs"
    install.mkdir(parents=True)
    dbs.mkdir()
    monkeypatch.setattr(settings_store, "get_install_dir", lambda: install)
    monkeypatch.setattr(settings_store, "get_db_dir", lambda: dbs)
    manager = SimpleNamespace(databases=[SimpleNamespace(path=d) for d in databases])
    monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
    return install, dbs


def test_protected_inside_lists_own_data_below_the_folder(tmp_path, monkeypatch):
    elsewhere = tmp_path / "gc" / "other.db"
    install, dbs = _own_data(tmp_path, monkeypatch,
                             databases=[tmp_path / "home" / "dbs" / "a.db", elsewhere])
    found = protected_inside(tmp_path)
    assert resolve_path(install) in found and resolve_path(dbs) in found
    assert resolve_path(elsewhere) in found
    assert resolve_path(dbs / "a.db") not in found       # covered by its folder
    assert protected_inside(tmp_path / "gc") == [resolve_path(elsewhere)]
    (tmp_path / "gpx").mkdir()
    assert protected_inside(tmp_path / "gpx") == []


# ── safe_write ───────────────────────────────────────────────────────────────

def _leftovers(folder: Path) -> list[str]:
    return [p.name for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_safe_write_writes_text_and_bytes(tmp_path):
    perms = [_rw(tmp_path)]
    target = safe_write(tmp_path / "sub" / "a.gpx", "<gpx>ä</gpx>", permissions=perms)
    assert target == resolve_path(tmp_path / "sub" / "a.gpx")
    assert target.read_text(encoding="utf-8") == "<gpx>ä</gpx>"
    safe_write(target, b"\x00\x01", permissions=perms)        # overwrite
    assert target.read_bytes() == b"\x00\x01"
    assert _leftovers(target.parent) == []


def test_safe_write_respects_folder_list_and_deny_list(tmp_path):
    with pytest.raises(FolderAccessDenied):
        safe_write(tmp_path / "a.gpx", "x", permissions=[_rw(tmp_path, write=False)])
    with pytest.raises(FolderAccessDenied, match="database"):
        safe_write(tmp_path / "x.db", "x", permissions=[_rw(tmp_path)])
    assert not (tmp_path / "a.gpx").exists() and not (tmp_path / "x.db").exists()
    assert _leftovers(tmp_path) == []


def test_safe_write_refuses_hard_link(tmp_path):
    outside, permitted = tmp_path / "outside", tmp_path / "permitted"
    outside.mkdir()
    permitted.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("original", encoding="utf-8")
    link = permitted / "innocent.txt"
    try:
        os.link(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("hard links not supported here")
    with pytest.raises(FolderAccessDenied, match="hard link"):
        safe_write(link, "changed", permissions=[_rw(permitted)])
    assert secret.read_text(encoding="utf-8") == "original"
    assert _leftovers(permitted) == []


def test_safe_write_replaces_instead_of_writing_in_place(tmp_path, monkeypatch):
    """A hard link that appears after the first check still does not reach
    the other file: the folder entry is replaced, the old file stays."""
    from opensak.macro import permissions as perm_mod
    outside, permitted = tmp_path / "outside", tmp_path / "permitted"
    outside.mkdir()
    permitted.mkdir()
    target = permitted / "a.gpx"
    target.write_text("old", encoding="utf-8")
    other = outside / "other.txt"
    try:
        os.link(target, other)
    except (OSError, NotImplementedError):
        pytest.skip("hard links not supported here")
    monkeypatch.setattr(perm_mod, "_refuse_hard_link", lambda _: None)
    safe_write(target, "new", permissions=[_rw(permitted)])
    assert target.read_text(encoding="utf-8") == "new"
    assert other.read_text(encoding="utf-8") == "old"


def test_safe_write_rechecks_before_replace(tmp_path, monkeypatch):
    from opensak.macro import permissions as perm_mod
    real = perm_mod.check_access
    calls = []

    def swapped(path, write=False, permissions=None):
        calls.append(path)
        if len(calls) == 2:
            return real(tmp_path / "elsewhere.gpx", write, permissions)
        return real(path, write, permissions)

    monkeypatch.setattr(perm_mod, "check_access", swapped)
    with pytest.raises(FolderAccessDenied, match="changed while writing"):
        safe_write(tmp_path / "a.gpx", "x", permissions=[_rw(tmp_path)])
    assert not (tmp_path / "a.gpx").exists()
    assert _leftovers(tmp_path) == []


def test_safe_write_cleans_up_on_failure(tmp_path, monkeypatch):
    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        safe_write(tmp_path / "a.gpx", "x", permissions=[_rw(tmp_path)])
    assert not (tmp_path / "a.gpx").exists()
    assert _leftovers(tmp_path) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_safe_write_keeps_mode_of_overwritten_file(tmp_path):
    target = tmp_path / "a.gpx"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o640)
    safe_write(target, "new", permissions=[_rw(tmp_path)])
    assert target.stat().st_mode & 0o777 == 0o640


# ── check_access ─────────────────────────────────────────────────────────────

def test_read_and_write_are_checked_separately(tmp_path):
    perms = [_rw(tmp_path / "ro", write=False), _rw(tmp_path / "wo", read=False)]
    assert check_access(tmp_path / "ro" / "a.csv", permissions=perms) == \
        resolve_path(tmp_path / "ro" / "a.csv")
    with pytest.raises(FolderAccessDenied):
        check_access(tmp_path / "ro" / "a.csv", write=True, permissions=perms)
    check_access(tmp_path / "wo" / "a.csv", write=True, permissions=perms)
    with pytest.raises(FolderAccessDenied):
        check_access(tmp_path / "wo" / "a.csv", permissions=perms)


def test_path_outside_every_folder_is_denied(tmp_path):
    with pytest.raises(FolderAccessDenied, match="Folder permissions"):
        check_access(tmp_path / "other" / "a.csv", permissions=[_rw(tmp_path / "ok")])


def test_dotdot_cannot_escape(tmp_path):
    (tmp_path / "ok").mkdir()
    (tmp_path / "secret.csv").write_text("x", encoding="utf-8")
    with pytest.raises(FolderAccessDenied):
        check_access(tmp_path / "ok" / ".." / "secret.csv", permissions=[_rw(tmp_path / "ok")])


def test_sibling_with_common_prefix_is_not_inside(tmp_path):
    with pytest.raises(FolderAccessDenied):
        check_access(tmp_path / "ok2" / "a.csv", permissions=[_rw(tmp_path / "ok")])


def test_symlink_cannot_escape(tmp_path):
    ok, outside = tmp_path / "ok", tmp_path / "outside"
    ok.mkdir()
    outside.mkdir()
    (outside / "secret.csv").write_text("x", encoding="utf-8")
    try:
        (ok / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    with pytest.raises(FolderAccessDenied):
        check_access(ok / "link" / "secret.csv", permissions=[_rw(ok)])


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_junction_cannot_escape(tmp_path):
    # Unlike symlinks, junctions need no admin rights / developer mode.
    import _winapi
    ok, outside = tmp_path / "ok", tmp_path / "outside"
    ok.mkdir()
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(ok / "link"))
    with pytest.raises(FolderAccessDenied):
        check_access(ok / "link" / "secret.csv", permissions=[_rw(ok)])


def test_most_specific_folder_wins(tmp_path):
    perms = [_rw(tmp_path), _rw(tmp_path / "locked", read=False, write=False)]
    check_access(tmp_path / "a.csv", permissions=perms)
    with pytest.raises(FolderAccessDenied):
        check_access(tmp_path / "locked" / "a.csv", permissions=perms)


@pytest.mark.parametrize("path, expected", [
    (PurePosixPath("/"), True),                         # Linux / macOS
    (PureWindowsPath("C:\\"), True),                    # drive
    (PureWindowsPath("\\\\server\\share\\"), True),     # network share
    (PurePosixPath("/home"), False),
    (PurePosixPath("/Volumes/USB"), False),
    (PureWindowsPath("C:\\Data"), False),
    (PureWindowsPath("\\\\server\\share\\gpx"), False),
])
def test_root_detection_on_every_platform(path, expected):
    assert _is_root(path) is expected


def test_is_root_folder_resolves_first(tmp_path):
    assert is_root_folder(tmp_path.anchor)
    assert is_root_folder(os.path.join(str(tmp_path), *[".."] * len(tmp_path.parts)))
    assert not is_root_folder(tmp_path)


def test_root_entry_grants_nothing(tmp_path):
    with pytest.raises(FolderAccessDenied):
        check_access(tmp_path / "a.csv", write=True, permissions=[_rw(tmp_path.anchor)])
    # a real folder below the root still works next to it
    check_access(tmp_path / "a.csv", permissions=[_rw(tmp_path.anchor), _rw(tmp_path)])


def test_load_drops_root_entries(tmp_path):
    save_permissions([_rw(tmp_path.anchor), _rw(tmp_path)])
    assert load_permissions() == [_rw(tmp_path)]


def test_listed_folder_with_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSAK_TEST_DIR", str(tmp_path))
    check_access(tmp_path / "a.csv", permissions=[_rw(os.path.join("$OPENSAK_TEST_DIR"))])


# ── opensak.read_csv() ───────────────────────────────────────────────────────

def _read(tmp_path, perms, name="a.csv"):
    out = []
    MacroRuntime(MagicMock(),output=out.append, folder_permissions=perms).run(
        f'print(#opensak.read_csv("{name}"))', base_dir=tmp_path)
    return out


def test_read_csv_in_permitted_folder(tmp_path):
    (tmp_path / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    assert _read(tmp_path, [_rw(tmp_path, write=False)]) == ["1"]


def test_read_csv_outside_permitted_folders_fails(tmp_path):
    (tmp_path / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    with pytest.raises(MacroError, match="may not read"):
        _read(tmp_path, [_rw(tmp_path / "elsewhere")])


def test_read_csv_relative_dotdot_fails(tmp_path):
    (tmp_path / "ok").mkdir()
    (tmp_path / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    with pytest.raises(MacroError, match="may not read"):
        _read(tmp_path / "ok", [_rw(tmp_path / "ok")], name="../a.csv")


def test_read_csv_uses_saved_list_by_default(tmp_path):
    (tmp_path / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    save_permissions([])
    with pytest.raises(MacroError, match="may not read"):
        MacroRuntime(MagicMock(), output=lambda _: None).run(
            'opensak.read_csv("a.csv")', base_dir=tmp_path)
    save_permissions([_rw(tmp_path, write=False)])
    MacroRuntime(MagicMock(),output=lambda _: None).run(
        'opensak.read_csv("a.csv")', base_dir=tmp_path)


# ── Approval while a macro runs ──────────────────────────────────────────────

def _host(answer):
    host = MagicMock()
    host.approve_folder.return_value = answer
    return host


def _run(host, perms, source, base_dir):
    out = []
    MacroRuntime(host, output=out.append, folder_permissions=perms).run(
        source, base_dir=base_dir)
    return out


def test_only_unlisted_folders_raise_not_approved(tmp_path):
    with pytest.raises(FolderNotApproved) as info:
        check_access(tmp_path / "a.csv", write=True, permissions=[])
    assert info.value.target == resolve_path(tmp_path / "a.csv")
    assert info.value.write is True
    with pytest.raises(FolderAccessDenied) as info:
        check_access(tmp_path / "x.db", permissions=[_rw(tmp_path)])
    assert not isinstance(info.value, FolderNotApproved)


def test_approvable_folder_is_the_parent_but_never_a_root(tmp_path):
    target = resolve_path(tmp_path / "a.csv")
    assert approvable_folder(target) == target.parent
    assert approvable_folder(Path(tmp_path.anchor) / "a.csv") is None


def test_with_grant_extends_existing_entry_and_copies(tmp_path):
    folder = resolve_path(tmp_path)
    perms = [_rw(tmp_path / "other"), _rw(folder, write=False)]
    granted = with_grant(perms, folder, write=True)
    assert [(p.read, p.write) for p in granted] == [(True, True), (True, True)]
    assert perms[1].write is False                    # original untouched
    added = with_grant([], folder, write=True)
    assert added == [FolderPermission(str(folder), read=False, write=True)]


def test_approve_once_reads_without_saving(tmp_path):
    (tmp_path / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    host = _host(FolderApproval.ONCE)
    out = _run(host, [], 'print(#opensak.read_csv("a.csv")); print(#opensak.read_csv("a.csv"))',
               tmp_path)
    assert out == ["1", "1"]
    host.approve_folder.assert_called_once_with(
        resolve_path(tmp_path / "a.csv"), resolve_path(tmp_path), False)
    assert get_store().get(STORE_KEY) is None


def test_approve_once_lasts_for_one_run_only(tmp_path):
    (tmp_path / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    host = _host(FolderApproval.ONCE)
    rt = MacroRuntime(host, output=lambda _: None, folder_permissions=[])
    rt.run('opensak.read_csv("a.csv")', base_dir=tmp_path)
    rt.run('opensak.read_csv("a.csv")', base_dir=tmp_path)
    assert host.approve_folder.call_count == 2


def test_approve_always_saves_the_folder(tmp_path):
    (tmp_path / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    save_permissions([_rw(tmp_path / "other")])
    host = _host(FolderApproval.ALWAYS)
    rt = MacroRuntime(host, output=lambda _: None)        # uses the saved list
    rt.run('opensak.read_csv("a.csv")', base_dir=tmp_path)
    assert load_permissions() == [
        _rw(tmp_path / "other"),
        FolderPermission(str(resolve_path(tmp_path)), read=True, write=False),
    ]
    rt.run('opensak.read_csv("a.csv")', base_dir=tmp_path)
    host.approve_folder.assert_called_once()


@pytest.mark.parametrize("answer", [FolderApproval.DENY, None, "always"])
def test_deny_fails_and_is_not_asked_again(tmp_path, answer):
    (tmp_path / "a.csv").write_text("code\nGC1\n", encoding="utf-8")
    host = _host(answer)
    with pytest.raises(MacroError, match="denied by the user"):
        _run(host, [], 'pcall(opensak.read_csv, "a.csv"); opensak.read_csv("a.csv")', tmp_path)
    host.approve_folder.assert_called_once()
    assert get_store().get(STORE_KEY) is None


def test_protected_files_are_never_offered(tmp_path):
    (tmp_path / "x.db").write_text("code\nGC1\n", encoding="utf-8")
    host = _host(FolderApproval.ALWAYS)
    with pytest.raises(MacroError, match="database"):
        _run(host, [], 'opensak.read_csv("x.db")', tmp_path)
    host.approve_folder.assert_not_called()


def test_read_and_write_are_approved_separately(tmp_path):
    host = _host(FolderApproval.ONCE)
    rt = MacroRuntime(host, output=lambda _: None, folder_permissions=[])
    rt.run("", base_dir=tmp_path)
    rt._check_access(tmp_path / "a.gpx", write=False)
    rt._check_access(tmp_path / "a.gpx", write=False)
    assert host.approve_folder.call_count == 1
    host.approve_folder.return_value = FolderApproval.DENY
    with pytest.raises(MacroError, match="may not write"):
        rt._check_access(tmp_path / "a.gpx", write=True)
    assert [c.args[2] for c in host.approve_folder.call_args_list] == [False, True]


# ── Settings → Folder permissions ────────────────────────────────────────────

pytest.importorskip("pytestqt")


@pytest.fixture
def dlg(qtbot, monkeypatch):
    from opensak.gui.dialogs import settings_dialog as sd
    from opensak.gui.settings import AppSettings
    from opensak.utils import flags

    s = AppSettings()
    monkeypatch.setattr(sd, "get_settings", lambda: s)
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
    monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)
    monkeypatch.setattr(flags, "lua_macros", True)
    d = sd.SettingsDialog()
    qtbot.addWidget(d)
    return d


def _rows(d):
    return [(p.path, p.read, p.write) for p in d._collect_permissions()]


def test_tab_lists_defaults(dlg):
    from opensak.lang import tr
    titles = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
    assert tr("settings_tab_folder_permissions") in titles
    assert [tuple(r[1:]) for r in _rows(dlg)] == [(True, True), (True, False)]


def test_tab_hidden_without_lua_macros_flag(qtbot, monkeypatch):
    from opensak.gui.dialogs import settings_dialog as sd
    from opensak.utils import flags
    monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)
    monkeypatch.setattr(flags, "lua_macros", False)
    d = sd.SettingsDialog()
    qtbot.addWidget(d)
    assert d._perm_table is None


def test_toggle_write_and_save(dlg):
    from PySide6.QtCore import Qt
    dlg._perm_table.item(1, dlg._PERM_COL_WRITE).setCheckState(Qt.CheckState.Checked)
    dlg._save()
    assert [(p.read, p.write) for p in load_permissions()] == [(True, True), (True, True)]


@pytest.mark.parametrize("answer, remaining", [("Yes", 1), ("No", 2)])
def test_clearing_both_boxes_asks_to_remove(dlg, monkeypatch, answer, remaining):
    from PySide6.QtCore import Qt
    from opensak.gui.dialogs import settings_dialog as sd
    ask = MagicMock(return_value=getattr(sd.QMessageBox.StandardButton, answer))
    monkeypatch.setattr(sd.QMessageBox, "question", ask)
    table = dlg._perm_table
    table.item(1, dlg._PERM_COL_READ).setCheckState(Qt.CheckState.Unchecked)
    ask.assert_called_once()
    assert table.rowCount() == remaining


def test_clearing_one_box_does_not_ask(dlg, monkeypatch):
    from PySide6.QtCore import Qt
    from opensak.gui.dialogs import settings_dialog as sd
    ask = MagicMock()
    monkeypatch.setattr(sd.QMessageBox, "question", ask)
    dlg._perm_table.item(0, dlg._PERM_COL_WRITE).setCheckState(Qt.CheckState.Unchecked)
    ask.assert_not_called()


def test_add_stores_resolved_path_and_rejects_duplicates(dlg, tmp_path, monkeypatch):
    from opensak.gui.dialogs import settings_dialog as sd
    (tmp_path / "data").mkdir()
    dlg._add_perm_folder(str(tmp_path / "data" / ".." / "data"))
    assert _rows(dlg)[-1] == (str(resolve_path(tmp_path / "data")), True, False)

    info = MagicMock()
    monkeypatch.setattr(sd.QMessageBox, "information", info)
    dlg._add_perm_folder(str(tmp_path / "data"))
    info.assert_called_once()
    assert len(_rows(dlg)) == 3


def test_add_refuses_root_folder(dlg, tmp_path, monkeypatch):
    from opensak.gui.dialogs import settings_dialog as sd
    warn = MagicMock()
    monkeypatch.setattr(sd.QMessageBox, "warning", warn)
    dlg._add_perm_folder(tmp_path.anchor)
    warn.assert_called_once()
    assert len(_rows(dlg)) == 2


def test_saving_other_settings_does_not_store_the_defaults(dlg):
    dlg._save()
    assert get_store().get(STORE_KEY) is None


def test_remove_selected_and_save_empty_list(dlg):
    for _ in range(2):
        dlg._perm_table.selectRow(0)
        dlg._on_remove_perm_folder()
    dlg._save()
    assert load_permissions() == []


# ── Approval dialog ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("button, expected", [
    ("macro_access_once", FolderApproval.ONCE),
    ("macro_access_always", FolderApproval.ALWAYS),
    ("macro_access_deny", FolderApproval.DENY),
    (None, FolderApproval.DENY),                      # closed / Escape
])
def test_approval_dialog_answers(qtbot, monkeypatch, tmp_path, button, expected):
    from opensak.gui.dialogs import macro_dialog as md
    monkeypatch.setattr(md, "tr", lambda key, **kw: " | ".join([key, *kw.values()]))
    tr = md.tr
    seen = {}

    def fake_exec(box):
        seen["text"] = box.text()
        seen["plain"] = box.textFormat()
        seen["default"] = box.defaultButton().text()
        if button:
            next(b for b in box.buttons() if b.text() == tr(button)).click()
        return 0

    monkeypatch.setattr(md.QMessageBox, "exec", fake_exec)
    target = tmp_path / "<b>a</b>.gpx"
    assert md.ask_folder_approval(None, target, tmp_path, write=True) is expected
    assert str(target) in seen["text"] and str(tmp_path) in seen["text"]
    assert seen["plain"] == md.Qt.TextFormat.PlainText
    assert seen["default"] == tr("macro_access_deny")


@pytest.mark.parametrize("answer, checked", [("Yes", True), ("No", False)])
def test_write_on_folder_with_own_data_asks(dlg, tmp_path, monkeypatch, answer, checked):
    from PySide6.QtCore import Qt
    from opensak.gui.dialogs import settings_dialog as sd
    _own_data(tmp_path, monkeypatch)
    ask = MagicMock(return_value=getattr(sd.QMessageBox.StandardButton, answer))
    monkeypatch.setattr(sd.QMessageBox, "question", ask)
    dlg._add_perm_folder(str(tmp_path))
    row = dlg._perm_table.rowCount() - 1
    dlg._perm_table.item(row, dlg._PERM_COL_WRITE).setCheckState(Qt.CheckState.Checked)
    ask.assert_called_once()
    assert ask.call_args.args[1] == sd.tr("settings_folder_perm_protected_title")
    assert dlg._perm_row_checked(row, dlg._PERM_COL_WRITE) is checked
    assert dlg._perm_row_checked(row, dlg._PERM_COL_READ)


def test_write_on_plain_folder_does_not_ask(dlg, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from opensak.gui.dialogs import settings_dialog as sd
    _own_data(tmp_path, monkeypatch)
    ask = MagicMock()
    monkeypatch.setattr(sd.QMessageBox, "question", ask)
    (tmp_path / "gpx").mkdir()
    dlg._add_perm_folder(str(tmp_path / "gpx"))
    row = dlg._perm_table.rowCount() - 1
    dlg._perm_table.item(row, dlg._PERM_COL_WRITE).setCheckState(Qt.CheckState.Checked)
    ask.assert_not_called()
    assert dlg._perm_row_checked(row, dlg._PERM_COL_WRITE)
