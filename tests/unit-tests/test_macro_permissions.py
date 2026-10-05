"""tests/unit-tests/test_macro_permissions.py — folders Lua macros may access.

Covers the Qt-free check (resolve-based, so ".." and symlinks cannot escape),
its use by opensak.read_csv(), and the Settings → Folder permissions tab.
"""

import os
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import MagicMock

import pytest

from opensak.macro import MacroError, MacroRuntime
from opensak.macro.permissions import (
    STORE_KEY,
    FolderAccessDenied,
    FolderPermission,
    _is_root,
    check_access,
    default_permissions,
    is_root_folder,
    load_permissions,
    protected_reason,
    resolve_path,
    safe_write,
    save_permissions,
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
