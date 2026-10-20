"""tests/unit-tests/test_macro_file_io.py — opensak.read_text(),
file_exists(), folder_exists() and list_dir(): reading files and folders
under the folder permissions."""

import os
from pathlib import Path

import pytest

from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.macro.permissions import resolve_path, temp_dir
from opensak.export.file_export import active_database_name


class FakeHost:
    """Only folder approval and the database name are used here."""

    def __init__(self, answer=FolderApproval.DENY):
        self.answer = answer
        self.asked: list[Path] = []

    def end_macro(self):
        pass

    def approve_folder(self, target, folder, write):
        self.asked.append(folder)
        return self.answer

    def database_name(self):
        return active_database_name()

    def __getattr__(self, name):
        # Bound into the opensak table at every run, never called here.
        return lambda *args: None


@pytest.fixture(autouse=True)
def own_temp_dir(tmp_path, monkeypatch):
    """opensak.temp_dir() inside the test's folder."""
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


@pytest.fixture
def tmp(own_temp_dir):
    """opensak.temp_dir(), which macros may read and write by default."""
    folder = temp_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _run(source, host=None, **kwargs):
    out: list[str] = []
    MacroRuntime(host or FakeHost(), output=out.append, **kwargs).run(source)
    return out


def _lua_path(path: Path) -> str:
    return str(path).replace("\\", "/")


# ── opensak.read_text ───────────────────────────────────────────────────────

def test_read_text_utf8_bom_and_line_endings(tmp):
    (tmp / "a.txt").write_bytes("﻿ä\r\nb\rc\n".encode("utf-8"))
    out = _run("""
        local t = opensak.read_text(opensak.temp_dir() .. "/a.txt")
        print(t == "ä\\nb\\nc\\n", #t)
    """)
    assert out == ["true\t7"]


@pytest.mark.parametrize("name", ["a.lua", "a.gpx", "a.bat", "noext", "a.HTML"])
def test_read_text_reads_any_file_type(tmp, name):
    (tmp / name).write_text("x", encoding="utf-8")
    assert _run(f'print(opensak.read_text(opensak.temp_dir() .. "/{name}"))') == ["x"]


@pytest.mark.parametrize("encoding", ["cp1252", "windows-1252", "latin-1", "latin1", "UTF8"])
def test_read_text_encodings(tmp, encoding):
    (tmp / "w.txt").write_bytes("é".encode("utf-8" if encoding == "UTF8" else "cp1252"))
    out = _run(f'print(opensak.read_text(opensak.temp_dir() .. "/w.txt", {{ encoding = "{encoding}" }}) == "é")')
    assert out == ["true"]


def test_read_text_invalid_utf8_suggests_cp1252(tmp):
    (tmp / "w.txt").write_bytes("Zürich".encode("cp1252"))
    with pytest.raises(MacroError, match='not UTF-8 text.*encoding = "cp1252"'):
        _run('opensak.read_text(opensak.temp_dir() .. "/w.txt")')


def test_read_text_relative_path_is_next_to_the_macro(macros_dir):
    (macros_dir / "data").mkdir()
    (macros_dir / "data" / "list.txt").write_text("hello", encoding="utf-8")
    out: list[str] = []
    MacroRuntime(FakeHost(), output=out.append).run(
        'print(opensak.read_text("data/list.txt"))', macro_path=macros_dir / "m.lua")
    assert out == ["hello"]


def test_read_text_asks_for_an_unapproved_folder(tmp_path):
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "a.txt").write_text("ok", encoding="utf-8")
    host = FakeHost(FolderApproval.ONCE)
    out = _run(f'print(opensak.read_text("{_lua_path(folder)}/a.txt"))\n'
               f'print(opensak.read_text("{_lua_path(folder)}/a.txt"))', host)
    assert out == ["ok", "ok"]
    assert host.asked == [resolve_path(folder)]


def test_read_text_denied_by_the_user(tmp_path):
    (tmp_path / "a.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.read_text("{_lua_path(tmp_path)}/a.txt")')


def test_read_text_refuses_a_database_file(tmp):
    (tmp / "sqlite.db3").write_bytes(b"SQLite format 3\0")
    with pytest.raises(MacroError, match="database file"):
        _run('opensak.read_text(opensak.temp_dir() .. "/sqlite.db3")')


def test_read_text_size_limit(tmp, monkeypatch):
    monkeypatch.setattr("opensak.macro.runtime.MAX_READ_BYTES", 10)
    (tmp / "big.txt").write_text("x" * 11, encoding="utf-8")
    with pytest.raises(MacroError, match="larger than"):
        _run('opensak.read_text(opensak.temp_dir() .. "/big.txt")')


@pytest.mark.parametrize("call, message", [
    ('opensak.read_text()', "expects a file path"),
    ('opensak.read_text("  ")', "expects a file path"),
    ('opensak.read_text(opensak.temp_dir() .. "/a.txt", { mode = "r" })', "options must be"),
    ('opensak.read_text(opensak.temp_dir() .. "/a.txt", { encoding = "utf-16" })', "encoding must be"),
    ('opensak.read_text(opensak.temp_dir() .. "/a.txt", { encoding = "nope" })', "encoding must be"),
    ('opensak.read_text(opensak.temp_dir() .. "/a.txt", { encoding = 1 })', "encoding must be"),
    ('opensak.read_text(opensak.temp_dir() .. "/missing.txt")', "file not found"),
    ('opensak.read_text(opensak.temp_dir())', "is a folder"),
])
def test_read_text_errors(tmp, call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)


# ── opensak.file_exists / folder_exists ─────────────────────────────────────

def test_file_and_folder_exists(tmp):
    (tmp / "a.txt").write_text("x", encoding="utf-8")
    (tmp / "sub").mkdir()
    out = _run("""
        local t = opensak.temp_dir()
        print(opensak.file_exists(t .. "/a.txt"), opensak.file_exists(t .. "/b.txt"),
              opensak.file_exists(t .. "/sub"))
        print(opensak.folder_exists(t .. "/sub"), opensak.folder_exists(t .. "/a.txt"),
              opensak.folder_exists(t .. "/none"), opensak.folder_exists(t))
    """)
    assert out == ["true\tfalse\tfalse", "true\tfalse\tfalse\ttrue"]


def test_file_exists_may_check_a_database_file(tmp):
    (tmp / "sqlite.db3").write_bytes(b"SQLite format 3\0")
    assert _run('print(opensak.file_exists(opensak.temp_dir() .. "/sqlite.db3"))') == ["true"]


def test_file_exists_needs_read_permission(tmp_path):
    host = FakeHost()
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.file_exists("{_lua_path(tmp_path)}/a.txt")', host)
    assert host.asked == [resolve_path(tmp_path)]


@pytest.mark.parametrize("call, message", [
    ("opensak.file_exists()", "expects a file path"),
    ("opensak.folder_exists(1)", "expects a folder path"),
])
def test_exists_argument_errors(call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)


# ── opensak.list_dir ────────────────────────────────────────────────────────

def test_list_dir_entries_sorted_by_name(tmp):
    (tmp / "b.GPX").write_bytes(b"12345")
    (tmp / "a.gpx").write_bytes(b"")
    (tmp / "C.txt").write_bytes(b"x")
    (tmp / "sub").mkdir()
    os.utime(tmp / "b.GPX", (1_700_000_000, 1_700_000_000))
    out = _run("""
        for _, e in ipairs(opensak.list_dir(opensak.temp_dir())) do
            print(e.name, e.dir, e.size, math.type(e.modified), e.path)
        end
        for _, e in ipairs(opensak.list_dir(opensak.temp_dir(), "*.gpx")) do
            print(e.name, e.modified)
        end
    """)
    assert out == [
        f"a.gpx\tfalse\t0\tinteger\t{tmp / 'a.gpx'}",
        f"b.GPX\tfalse\t5\tinteger\t{tmp / 'b.GPX'}",
        f"C.txt\tfalse\t1\tinteger\t{tmp / 'C.txt'}",
        f"sub\ttrue\t0\tinteger\t{tmp / 'sub'}",
        f"a.gpx\t{int((tmp / 'a.gpx').stat().st_mtime)}",
        "b.GPX\t1700000000",
    ]


def test_list_dir_does_not_go_into_subfolders(tmp):
    (tmp / "sub").mkdir()
    (tmp / "sub" / "deep.gpx").write_bytes(b"")
    assert _run('print(#opensak.list_dir(opensak.temp_dir(), "*.gpx"))') == ["0"]


def test_list_dir_relative_path_is_next_to_the_macro(macros_dir):
    (macros_dir / "data").mkdir()
    (macros_dir / "data" / "x.txt").write_bytes(b"")
    out: list[str] = []
    MacroRuntime(FakeHost(), output=out.append).run(
        'print(opensak.list_dir("data")[1].name)', macro_path=macros_dir / "m.lua")
    assert out == ["x.txt"]


def test_list_dir_asks_for_the_folder_itself(tmp_path):
    folder = tmp_path / "gpx"
    folder.mkdir()
    (folder / "a.gpx").write_bytes(b"")
    host = FakeHost(FolderApproval.ONCE)
    out = _run(f'print(#opensak.list_dir("{_lua_path(folder)}"))\n'
               f'print(opensak.read_text("{_lua_path(folder)}/a.gpx") == "")', host)
    assert out == ["1", "true"]
    assert host.asked == [resolve_path(folder)]


def test_list_dir_denied_by_the_user(tmp_path):
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.list_dir("{_lua_path(tmp_path)}")')


def test_list_dir_refuses_a_filesystem_root():
    host = FakeHost(FolderApproval.ONCE)
    with pytest.raises(MacroError):
        _run('opensak.list_dir("/")', host)
    assert host.asked == []


def test_list_dir_entry_limit(tmp, monkeypatch):
    monkeypatch.setattr("opensak.macro.runtime.MAX_DIR_ENTRIES", 2)
    for name in ("a.txt", "b.txt", "c.gpx"):
        (tmp / name).write_bytes(b"")
    assert _run('print(#opensak.list_dir(opensak.temp_dir(), "*.txt"))') == ["2"]
    with pytest.raises(MacroError, match="more than 2 entries"):
        _run("opensak.list_dir(opensak.temp_dir())")


@pytest.mark.parametrize("call, message", [
    ("opensak.list_dir()", "expects a folder path"),
    ('opensak.list_dir(opensak.temp_dir(), "a/*.gpx")', "pattern must be"),
    ('opensak.list_dir(opensak.temp_dir(), "")', "pattern must be"),
    ("opensak.list_dir(opensak.temp_dir(), 1)", "pattern must be"),
    ('opensak.list_dir(opensak.temp_dir() .. "/none")', "folder not found"),
])
def test_list_dir_errors(tmp, call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)
