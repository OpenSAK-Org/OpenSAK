"""tests/unit-tests/test_macro_sandbox_windows_paths_1359.py — macro sandbox
hardening for Windows special paths and for the spelling of protected paths
(#1359).

E: on Windows, check_access() refuses device names ("COM3.gpx"), alternate
data streams ("a.gpx:x") and the \\\\.\\ / \\\\?\\ prefixes. resolve() keeps
"\\\\?\\C:\\…", so such a path used to miss the deny list and was even offered
for approval.

F: the deny list ignores upper/lower case (macOS does not fix the case in
resolve()) and matches existing files by identity, in protected_reason()
and in protected_inside().

The Windows checks are pure string checks, so they run on every platform
with the module's _WINDOWS switch set.
"""

import os
from pathlib import Path

import pytest

from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.macro import permissions as perm_mod
from opensak.macro.permissions import (
    FolderAccessDenied,
    FolderNotApproved,
    FolderPermission,
    check_access,
    protected_inside,
    protected_reason,
    resolve_macro_path,
    resolve_path,
    windows_path_reason,
)
from opensak.utils.file_names import is_reserved_device_name


@pytest.fixture(autouse=True)
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "install" / "macros"
    d.mkdir(parents=True)
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(perm_mod, "_WINDOWS", True)


def _rw(path):
    return [FolderPermission(str(path), read=True, write=True)]


# ── E: device names ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "CON", "prn", "Aux", "NUL", "COM1", "com9", "COM0", "LPT1", "LPT9",
    "COM\u00b9", "LPT\u00b3", "CONIN$", "conout$",
    "COM3.gpx", "nul.tar.gz", "NUL.", "con .txt", "COM3 ",
])
def test_reserved_device_names(name):
    assert is_reserved_device_name(name)


@pytest.mark.parametrize("name", [
    "COM10", "COM", "LPT", "CONSOLE", "NULL", "my_COM3", "_COM3", "COM3_x.gpx",
    "AUXILIARY.txt", "a.CON", "",
])
def test_ordinary_names(name):
    assert not is_reserved_device_name(name)


# ── E: Windows path check ────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    r"\\.\COM3",
    r"\\.\C:\Temp\a.gpx",
    "//./COM3",
    r"\\?\C:\Temp\a.gpx",
    "//?/C:/Temp/a.gpx",
    r"\\?\GLOBALROOT\Device\Null",
    r"\\?\UNC\server\share\a.gpx",
    r"\??\C:\Temp\a.gpx",
])
def test_device_namespace_prefixes_are_refused(path):
    assert "device paths" in windows_path_reason(path)


@pytest.mark.parametrize("path", [
    r"C:\Temp\data.db:x",
    r"C:\Temp\a.gpx::$DATA",
    r"C:\Temp\folder:x\a.gpx",
    r"\\server\share\a.gpx:x",
    "a.gpx:x",
])
def test_alternate_data_streams_are_refused(path):
    assert "alternate data stream" in windows_path_reason(path)


@pytest.mark.parametrize("path", [
    r"C:\Temp\COM3",
    r"C:\Temp\COM3.gpx",
    r"C:\Temp\nul .txt",
    r"C:\Temp\CON\a.gpx",
    r"\\server\share\LPT1.txt",
    "COM3.gpx",
])
def test_device_names_in_a_path_are_refused(path):
    assert "device name" in windows_path_reason(path)


@pytest.mark.parametrize("path", [
    r"C:\Temp\a.gpx",
    r"C:\Temp\COM30.gpx",
    r"C:\Users\x\Documents\con_list.txt",
    r"\\server\share\a.gpx",
    "relative/a.gpx",
    "C:relative.gpx",
])
def test_plain_windows_paths_pass(path):
    assert windows_path_reason(path) is None


def test_device_name_is_refused_not_offered_for_approval(tmp_path, windows):
    # Regression: "COM3.gpx" in a permitted folder used to pass every check.
    for target in (tmp_path / "COM3.gpx", tmp_path / "nul"):
        with pytest.raises(FolderAccessDenied, match="device name") as exc:
            check_access(target, write=True, permissions=_rw(tmp_path))
        assert not isinstance(exc.value, FolderNotApproved)


def test_stream_is_refused_also_without_suffix_list(tmp_path, windows):
    # Before, ":" was only refused when the caller passed allowed_suffixes.
    with pytest.raises(FolderAccessDenied, match="alternate data stream"):
        check_access(f"{tmp_path}{os.sep}a.gpx:x", write=False, permissions=_rw(tmp_path))


def test_prefixed_path_is_refused_before_resolving(tmp_path, windows, monkeypatch):
    # resolve() would open "\\.\COM3" to look at it, so it must not be called.
    def no_resolve(path):
        raise AssertionError(f"resolve_path({path!r}) called")

    monkeypatch.setattr(perm_mod, "resolve_path", no_resolve)
    for path in (r"\\.\COM3", "//./COM3", rf"\\?\{tmp_path}{os.sep}a.gpx"):
        with pytest.raises(FolderAccessDenied, match="device paths") as exc:
            check_access(path, write=True, permissions=_rw(tmp_path))
        assert not isinstance(exc.value, FolderNotApproved)


def test_env_var_cannot_smuggle_a_device_name(tmp_path, windows, monkeypatch):
    monkeypatch.setenv("OPENSAK_TEST_DEVICE", "COM3")
    with pytest.raises(FolderAccessDenied, match="device name"):
        resolve_macro_path(f"{tmp_path}{os.sep}%OPENSAK_TEST_DEVICE%.gpx", "write")


@pytest.mark.skipif(os.name != "nt", reason="\\\\?\\ paths exist on Windows only")
def test_long_path_prefix_cannot_reach_protected_data(tmp_path, monkeypatch):
    # Regression: resolve() keeps the \\?\ prefix, so "\\?\C:\…\install\x"
    # was not inside the protected folder "C:\…\install" — and the folder
    # was even offered for approval.
    from opensak import settings_store
    install = tmp_path / "install"
    monkeypatch.setattr(settings_store, "get_install_dir", lambda: install)
    with pytest.raises(FolderAccessDenied) as exc:
        check_access(rf"\\?\{install}\gc_token.json", permissions=_rw(tmp_path))
    assert not isinstance(exc.value, FolderNotApproved)


def test_plain_names_still_allowed_on_windows(tmp_path, windows):
    perms = _rw(tmp_path)
    assert check_access(tmp_path / "COM30.gpx", write=True, permissions=perms) \
        == resolve_path(tmp_path / "COM30.gpx")
    check_access(tmp_path / "sub" / "a.gpx", write=False, permissions=perms)


def test_device_names_are_ordinary_files_elsewhere(tmp_path, monkeypatch):
    # On Linux and macOS "COM3.gpx" is just a file name.
    monkeypatch.setattr(perm_mod, "_WINDOWS", False)
    check_access(tmp_path / "COM3.gpx", write=True, permissions=_rw(tmp_path))


class FakeHost:
    def end_macro(self):
        pass

    def approve_folder(self, target, folder, write, macro=None):
        return FolderApproval.DENY

    def __getattr__(self, name):
        return lambda *args: None


def test_write_text_to_device_is_refused(tmp_path, windows, monkeypatch):
    monkeypatch.setattr(MacroRuntime, "_folder_list", lambda self: _rw(tmp_path))
    target = str(tmp_path / "COM3.txt").replace("\\", "/")
    with pytest.raises(MacroError, match="device name"):
        MacroRuntime(FakeHost(), output=lambda _: None).run(
            f'opensak.write_text("{target}", "x")'
        )


# ── F: spelling of protected paths ───────────────────────────────────────────

def _own_data(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from opensak import settings_store
    install, dbs = tmp_path / "install", tmp_path / "dbs"
    dbs.mkdir()
    bootstrap = tmp_path / "boot" / "bootstrap.json"
    bootstrap.parent.mkdir()
    bootstrap.write_text("{}")
    monkeypatch.setattr(settings_store, "get_install_dir", lambda: install)
    monkeypatch.setattr(settings_store, "get_db_dir", lambda: dbs)
    monkeypatch.setattr(settings_store, "_bootstrap_path", lambda: bootstrap)
    manager = SimpleNamespace(databases=[])
    monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
    return install, dbs, bootstrap


def _respell(path: Path, base: Path) -> Path:
    """*path* below *base* in upper case: another spelling of the same
    place on Windows and macOS."""
    return base / str(path.relative_to(base)).upper()


def test_other_case_of_protected_folder_is_denied(tmp_path, monkeypatch):
    install, dbs, _ = _own_data(tmp_path, monkeypatch)
    perms = _rw(tmp_path)
    for target in (install / "gc_token.json", install / "notes.txt", dbs / "x.csv"):
        respelled = resolve_path(_respell(target, tmp_path))
        assert protected_reason(respelled) is not None, respelled
        with pytest.raises(FolderAccessDenied):
            check_access(_respell(target, tmp_path), write=True, permissions=perms)


def test_other_case_of_protected_file_is_denied(tmp_path, monkeypatch):
    _, _, bootstrap = _own_data(tmp_path, monkeypatch)
    respelled = resolve_path(_respell(bootstrap, tmp_path))
    assert protected_reason(respelled) == "it is one of OpenSAK's own files"


def test_other_case_of_macro_settings_folder_is_denied(tmp_path, monkeypatch, macros_dir):
    from opensak.macro.settings import SETTINGS_DIRNAME
    _own_data(tmp_path, monkeypatch)
    target = resolve_path(macros_dir) / SETTINGS_DIRNAME.upper() / "other.json"
    assert "saved settings" in protected_reason(target)


def test_macros_folder_stays_usable_in_any_case(tmp_path, monkeypatch, macros_dir):
    _own_data(tmp_path, monkeypatch)
    perms = _rw(tmp_path)
    check_access(macros_dir / "a.csv", write=True, permissions=perms)
    assert protected_reason(resolve_path(_respell(macros_dir / "a.csv", tmp_path))) is None


def test_hard_link_to_protected_file_is_denied(tmp_path, monkeypatch):
    _, _, bootstrap = _own_data(tmp_path, monkeypatch)
    link = tmp_path / "elsewhere" / "notes.txt"
    link.parent.mkdir()
    try:
        os.link(bootstrap, link)
    except OSError as exc:
        pytest.skip(f"no hard links here: {exc}")
    assert protected_reason(resolve_path(link)) == "it is one of OpenSAK's own files"
    with pytest.raises(FolderAccessDenied):
        check_access(link, write=False, permissions=_rw(tmp_path))


def test_unrelated_files_are_not_matched(tmp_path, monkeypatch):
    _own_data(tmp_path, monkeypatch)
    (tmp_path / "gpx").mkdir()
    (tmp_path / "gpx" / "a.gpx").write_text("x")
    assert protected_reason(resolve_path(tmp_path / "gpx" / "a.gpx")) is None
    assert protected_reason(resolve_path(tmp_path / "installer" / "a.gpx")) is None


def test_protected_inside_ignores_case(tmp_path, monkeypatch):
    install, dbs, _ = _own_data(tmp_path, monkeypatch)
    found = protected_inside(tmp_path)
    assert resolve_path(install) in found and resolve_path(dbs) in found
    # The Settings warning matches the respelled folder too.
    respelled = protected_inside(_respell(dbs, tmp_path))
    assert respelled == [resolve_path(dbs)]
