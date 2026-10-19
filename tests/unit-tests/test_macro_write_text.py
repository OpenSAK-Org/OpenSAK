"""tests/unit-tests/test_macro_write_text.py — opensak.write_text() with its
file-type allow-list and the autostart deny (security item G)."""

import os
from pathlib import Path

import pytest

from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.macro.permissions import (
    FolderAccessDenied,
    FolderPermission,
    check_access,
    resolve_path,
    safe_write,
    temp_dir,
    write_denied_reason,
)
from opensak.export.file_export import active_database_name


class FakeHost:
    """Only folder approval and the database name are used here."""

    def end_macro(self):
        pass

    def approve_folder(self, target, folder, write):
        return FolderApproval.DENY

    def database_name(self):
        return active_database_name()

    def __getattr__(self, name):
        # Bound into the opensak table at every run, never called here.
        return lambda *args: None


@pytest.fixture(autouse=True)
def own_temp_dir(tmp_path, monkeypatch):
    """opensak.temp_dir() inside the test's folder, so the reports these
    tests write never replace real ones in the system temp folder."""
    import tempfile

    folder = tmp_path / "systemp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.fixture(autouse=True)
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "install" / "macros"
    d.mkdir(parents=True)
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d


def _run(source, host=None, **kwargs):
    out: list[str] = []
    MacroRuntime(host or FakeHost(), output=out.append, **kwargs).run(source)
    return out


def _lua_path(path: Path) -> str:
    return str(path).replace("\\", "/")


# ── Security G: allowed file types, autostart locations ─────────────────────

def _rw(path):
    return [FolderPermission(str(path), read=True, write=True)]


@pytest.mark.parametrize("name", ["a.bat", "a.PS1", "a.lua", "a.exe", "a", "a.html.", "a.html:x"])
def test_suffix_outside_allow_list_is_refused(tmp_path, name):
    with pytest.raises(FolderAccessDenied, match="only these file types"):
        check_access(tmp_path / name, write=True, permissions=_rw(tmp_path),
                     allowed_suffixes=(".html", ".txt"))


def test_allowed_suffix_any_case_and_reads_are_not_limited(tmp_path):
    perms = _rw(tmp_path)
    assert check_access(tmp_path / "A.HTML", write=True, permissions=perms,
                        allowed_suffixes=(".html",)) == resolve_path(tmp_path / "A.HTML")
    # Reads and writes without an allow-list keep working as before.
    check_access(tmp_path / "a.lua", write=False, permissions=perms, allowed_suffixes=(".html",))
    check_access(tmp_path / "a.gpx", write=True, permissions=perms)


def test_safe_write_checks_allowed_suffixes(tmp_path):
    with pytest.raises(FolderAccessDenied, match="only these file types"):
        safe_write(tmp_path / "x.cmd", "x", permissions=_rw(tmp_path),
                   allowed_suffixes=iter([".txt"]))
    target = safe_write(tmp_path / "x.txt", "x", permissions=_rw(tmp_path),
                        allowed_suffixes=iter([".txt"]))    # iterated twice
    assert target.read_text() == "x"


@pytest.mark.parametrize("relative", [
    ".bashrc", ".zshrc", ".profile", ".config/autostart/x.txt",
    "Library/LaunchAgents/x.txt",
])
def test_autostart_locations_are_never_written(relative):
    home = Path.home()      # tmp folder (conftest)
    target = home / relative
    with pytest.raises(FolderAccessDenied, match="autostart"):
        check_access(target, write=True, permissions=_rw(home))
    # Reading them is not part of this rule.
    check_access(target, write=False, permissions=_rw(home))


def test_windows_startup_folder_is_never_written(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    startup = tmp_path / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert write_denied_reason(resolve_path(startup / "x.txt")) == "it is an autostart location"
    if os.name == "nt":     # case-insensitive file names
        assert write_denied_reason(resolve_path(str(startup / "x.txt").upper())) is not None


# ── opensak.write_text ──────────────────────────────────────────────────────

def test_write_text_writes_utf8_and_appends():
    out = _run("""
        local p = opensak.write_text(opensak.temp_dir() .. "/report.html", "<p>ä</p>")
        print(p)
        opensak.write_text(p, "\\n<p>2</p>", { append = true })
        print(opensak.write_text(opensak.temp_dir() .. "/n.txt", 42))
    """)
    target = temp_dir() / "report.html"
    assert out == [str(target), str(temp_dir() / "n.txt")]
    assert target.read_text(encoding="utf-8") == "<p>ä</p>\n<p>2</p>"
    assert (temp_dir() / "n.txt").read_text() == "42"


def test_write_text_relative_path_is_next_to_the_macro(macros_dir):
    host = FakeHost()
    host.approve_folder = lambda target, folder, write: FolderApproval.ONCE
    out: list[str] = []
    MacroRuntime(host, output=out.append).run(
        'print(opensak.write_text("out/r.txt", "x"))', macro_path=macros_dir / "m.lua")
    assert out == [str(resolve_path(macros_dir / "out" / "r.txt"))]


@pytest.mark.parametrize("name", ["evil.bat", "other.lua", "x.gpx", "noext"])
def test_write_text_refuses_other_file_types_without_asking(name):
    host = FakeHost()
    asked = []
    host.approve_folder = lambda *a: asked.append(a) or FolderApproval.ONCE
    with pytest.raises(MacroError, match="only these file types can be written"):
        _run(f'opensak.write_text(opensak.temp_dir() .. "/{name}", "x")', host)
    assert asked == []
    assert not (temp_dir() / name).exists()


def test_write_text_needs_write_permission(tmp_path):
    host = FakeHost()
    host.approve_folder = lambda *a: FolderApproval.DENY
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.write_text("{_lua_path(tmp_path)}/r.html", "x")', host)
    assert not (tmp_path / "r.html").exists()


@pytest.mark.parametrize("call, message", [
    ('opensak.write_text()', "expects a file path"),
    ('opensak.write_text(opensak.temp_dir() .. "/a.txt", {})', "text must be a string"),
    ('opensak.write_text(opensak.temp_dir() .. "/a.txt", "x", { mode = "a" })', "options must be"),
    ('opensak.write_text(opensak.temp_dir() .. "/a.txt", "x", { append = 1 })', "append must be"),
])
def test_write_text_argument_errors(call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)


def test_write_text_size_limit(monkeypatch):
    monkeypatch.setattr("opensak.macro.runtime.MAX_WRITE_BYTES", 10)
    with pytest.raises(MacroError, match="larger than"):
        _run('opensak.write_text(opensak.temp_dir() .. "/big.txt", string.rep("x", 11))')
