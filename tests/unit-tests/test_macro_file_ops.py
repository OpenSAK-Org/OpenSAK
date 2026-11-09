"""tests/unit-tests/test_macro_file_ops.py — opensak.copy_file, move_file,
delete_file, create_folder, delete_folder and file_info (GSAK's FileCopy /
FileErase): every write goes through the folder permissions, the file-type
allow-list, the protected locations and the link checks."""

import os
from pathlib import Path

import pytest

from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.macro.permissions import (
    FolderAccessDenied,
    FolderPermission,
    resolve_path,
    safe_delete,
)


class FakeHost:
    def __init__(self, approval=FolderApproval.DENY):
        self.approval = approval
        self.asked: list[tuple[Path, bool]] = []

    def end_macro(self):
        pass

    def approve_folder(self, target, folder, write, macro=None):
        self.asked.append((folder, write))
        return self.approval

    def __getattr__(self, name):
        # Bound into the opensak table at every run, never called here.
        return lambda *args: None


@pytest.fixture(autouse=True)
def own_temp_dir(tmp_path, monkeypatch):
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
def src(tmp_path):
    """A folder macros may only read, with a PQ zip in it."""
    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "pq.zip").write_bytes(b"PK\x03\x04 zip data")
    os.utime(folder / "pq.zip", (1_600_000_000, 1_600_000_000))
    return folder


@pytest.fixture
def out(tmp_path):
    """A folder macros may write."""
    folder = tmp_path / "out"
    folder.mkdir()
    return folder


def _p(path: Path) -> str:
    return str(path).replace("\\", "/")


def _perms(src: Path, out: Path, src_write=False):
    return [FolderPermission(str(src), read=True, write=src_write),
            FolderPermission(str(out), read=True, write=True)]


def _run(source, permissions, host=None):
    lines: list[str] = []
    MacroRuntime(host or FakeHost(), output=lines.append,
                 folder_permissions=permissions).run(source)
    return lines


# ── copy_file ────────────────────────────────────────────────────────────────

def test_copy_into_a_folder_keeps_name_and_time(src, out):
    lines = _run(f'print(opensak.copy_file("{_p(src / "pq.zip")}", "{_p(out)}/"))',
                 _perms(src, out))
    assert lines == [str(resolve_path(out / "pq.zip"))]
    assert (out / "pq.zip").read_bytes() == (src / "pq.zip").read_bytes()
    assert int((out / "pq.zip").stat().st_mtime) == 1_600_000_000
    assert (src / "pq.zip").exists()


def test_copy_to_an_existing_folder_and_to_a_new_name(src, out):
    _run(f'opensak.copy_file("{_p(src / "pq.zip")}", "{_p(out)}")\n'
         f'opensak.copy_file("{_p(src / "pq.zip")}", "{_p(out / "renamed.zip")}")',
         _perms(src, out))
    assert sorted(f.name for f in out.iterdir()) == ["pq.zip", "renamed.zip"]


def test_copy_overwrites_or_skips(src, out):
    (out / "pq.zip").write_bytes(b"old")
    lines = _run(f'print(opensak.copy_file("{_p(src / "pq.zip")}", "{_p(out)}/", '
                 '{ if_exists = "skip" }))', _perms(src, out))
    assert lines == ["nil"] and (out / "pq.zip").read_bytes() == b"old"
    _run(f'opensak.copy_file("{_p(src / "pq.zip")}", "{_p(out)}/")', _perms(src, out))
    assert (out / "pq.zip").read_bytes().startswith(b"PK")


@pytest.mark.parametrize("name", ["evil.bat", "evil.exe", "evil.ps1", "evil.lua", "evil",
                                  "evil.zip:x.bat"])
def test_copy_to_a_program_or_script_is_refused_without_asking(src, out, name):
    host = FakeHost(FolderApproval.ONCE)
    with pytest.raises(MacroError, match="only these file types"):
        _run(f'opensak.copy_file("{_p(src / "pq.zip")}", "{_p(out / name)}")',
             _perms(src, out), host)
    assert host.asked == [] and list(out.iterdir()) == []


def test_copy_needs_write_permission_on_the_target(src, out):
    host = FakeHost(FolderApproval.DENY)
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.copy_file("{_p(src / "pq.zip")}", "{_p(src / "copy.zip")}")',
             _perms(src, out), host)
    assert host.asked == [(resolve_path(src), True)]
    assert not (src / "copy.zip").exists()


def test_copy_needs_read_permission_on_the_source(src, out):
    host = FakeHost(FolderApproval.DENY)
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.copy_file("{_p(src / "pq.zip")}", "{_p(out)}/")',
             [FolderPermission(str(out), read=True, write=True)], host)
    assert host.asked == [(resolve_path(src), False)]


def test_copy_of_a_database_is_refused(src, out):
    (src / "sqlite.db3").write_bytes(b"SQLite format 3\x00")
    with pytest.raises(MacroError, match="database file"):
        _run(f'opensak.copy_file("{_p(src / "sqlite.db3")}", "{_p(out / "x.txt")}")',
             _perms(src, out))


def test_copy_onto_itself_is_refused(out):
    (out / "a.gpx").write_text("<gpx/>", encoding="utf-8")
    with pytest.raises(MacroError, match="onto itself"):
        _run(f'opensak.copy_file("{_p(out / "a.gpx")}", "{_p(out)}/")',
             [FolderPermission(str(out), read=True, write=True)])
    assert (out / "a.gpx").read_text(encoding="utf-8") == "<gpx/>"


def test_copy_never_writes_through_a_hard_link(src, out, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    try:
        os.link(victim, out / "link.txt")
    except OSError:
        pytest.skip("hard links not supported here")
    (src / "new.txt").write_text("new", encoding="utf-8")
    with pytest.raises(MacroError, match="hard link"):
        _run(f'opensak.copy_file("{_p(src / "new.txt")}", "{_p(out / "link.txt")}")',
             _perms(src, out))
    assert victim.read_text(encoding="utf-8") == "keep"


def test_copy_missing_source_and_bad_arguments(src, out):
    with pytest.raises(MacroError, match="file not found"):
        _run(f'opensak.copy_file("{_p(src / "nope.zip")}", "{_p(out)}/")', _perms(src, out))
    with pytest.raises(MacroError, match="if_exists must be one of overwrite, skip"):
        _run(f'opensak.copy_file("{_p(src / "pq.zip")}", "{_p(out)}/", {{ if_exists = "x" }})',
             _perms(src, out))
    with pytest.raises(MacroError, match="expects a target file or folder path"):
        _run(f'opensak.copy_file("{_p(src / "pq.zip")}")', _perms(src, out))


def test_copy_size_limit(src, out, monkeypatch):
    monkeypatch.setattr("opensak.macro.runtime.MAX_COPY_BYTES", 4)
    with pytest.raises(MacroError, match="larger than"):
        _run(f'opensak.copy_file("{_p(src / "pq.zip")}", "{_p(out)}/")', _perms(src, out))
    assert list(out.iterdir()) == []


# ── move_file ────────────────────────────────────────────────────────────────

def test_move_deletes_the_source(src, out):
    _run(f'opensak.move_file("{_p(src / "pq.zip")}", "{_p(out / "done")}/")',
         _perms(src, out, src_write=True))
    assert not (src / "pq.zip").exists()
    assert (out / "done" / "pq.zip").read_bytes().startswith(b"PK")


def test_move_without_write_on_the_source_copies_nothing(src, out):
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.move_file("{_p(src / "pq.zip")}", "{_p(out)}/")', _perms(src, out))
    assert (src / "pq.zip").exists() and list(out.iterdir()) == []


def test_move_of_another_file_type_is_refused(src, out):
    (src / "tool.exe").write_bytes(b"MZ")
    with pytest.raises(MacroError, match="only these file types"):
        _run(f'opensak.move_file("{_p(src / "tool.exe")}", "{_p(out / "tool.txt")}")',
             _perms(src, out, src_write=True))
    assert (src / "tool.exe").exists() and list(out.iterdir()) == []


# ── delete_file ──────────────────────────────────────────────────────────────

def test_delete_file(out):
    (out / "old.gpx").write_text("x", encoding="utf-8")
    lines = _run(f'print(opensak.delete_file("{_p(out / "old.gpx")}"), '
                 f'opensak.delete_file("{_p(out / "old.gpx")}"))',
                 [FolderPermission(str(out), read=True, write=True)])
    assert lines == ["true\tfalse"] and not (out / "old.gpx").exists()


def test_delete_needs_write_permission(src):
    host = FakeHost(FolderApproval.DENY)
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.delete_file("{_p(src / "pq.zip")}")',
             [FolderPermission(str(src), read=True, write=False)], host)
    assert (src / "pq.zip").exists() and host.asked == [(resolve_path(src), True)]


@pytest.mark.parametrize("name", ["doc.docx", "tool.exe", "script.lua", "data.db3"])
def test_delete_of_other_file_types_is_refused(out, name):
    (out / name).write_bytes(b"x")
    with pytest.raises(MacroError, match="only these file types|database file"):
        _run(f'opensak.delete_file("{_p(out / name)}")',
             [FolderPermission(str(out), read=True, write=True)])
    assert (out / name).exists()


def test_delete_file_refuses_a_folder(out):
    (out / "sub.txt").mkdir()
    with pytest.raises(MacroError, match="it is a folder"):
        _run(f'opensak.delete_file("{_p(out / "sub.txt")}")',
             [FolderPermission(str(out), read=True, write=True)])
    assert (out / "sub.txt").is_dir()


def test_delete_refuses_a_link_and_keeps_its_target(out, tmp_path):
    target = out / "real.txt"
    target.write_text("keep", encoding="utf-8")
    try:
        (out / "link.txt").symlink_to(target)
    except OSError:
        pytest.skip("symbolic links not available")
    with pytest.raises(MacroError, match="it is a link"):
        _run(f'opensak.delete_file("{_p(out / "link.txt")}")',
             [FolderPermission(str(out), read=True, write=True)])
    assert target.read_text(encoding="utf-8") == "keep"


def test_delete_in_an_autostart_location_is_refused(tmp_path, monkeypatch):
    startup = tmp_path / "appdata" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    (startup / "x.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    with pytest.raises(MacroError, match="autostart"):
        _run(f'opensak.delete_file("{_p(startup / "x.txt")}")',
             [FolderPermission(str(tmp_path), read=True, write=True)])
    assert (startup / "x.txt").exists()


def test_safe_delete_checks_again_right_before_deleting(out, monkeypatch):
    from opensak.macro import permissions

    (out / "a.txt").write_text("x", encoding="utf-8")
    real = permissions.check_access
    calls = []

    def moved(path, **kwargs):
        calls.append(path)
        result = real(path, **kwargs)
        return result if len(calls) == 1 else result.with_name("elsewhere.txt")

    monkeypatch.setattr(permissions, "check_access", moved)
    with pytest.raises(FolderAccessDenied, match="path changed"):
        safe_delete(out / "a.txt", [FolderPermission(str(out), read=True, write=True)],
                    (".txt",))
    assert (out / "a.txt").exists()


# ── create_folder / delete_folder ────────────────────────────────────────────

def test_create_and_delete_folder(out):
    perms = [FolderPermission(str(out), read=True, write=True)]
    lines = _run(f'print(opensak.create_folder("{_p(out / "a" / "b")}"))\n'
                 f'opensak.create_folder("{_p(out / "a" / "b")}")', perms)
    assert lines == [str(resolve_path(out / "a" / "b"))] and (out / "a" / "b").is_dir()
    lines = _run(f'print(opensak.delete_folder("{_p(out / "a" / "b")}"), '
                 f'opensak.delete_folder("{_p(out / "a" / "b")}"))', perms)
    assert lines == ["true\tfalse"] and not (out / "a" / "b").exists()


def test_create_folder_needs_write_permission(src):
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.create_folder("{_p(src / "new")}")',
             [FolderPermission(str(src), read=True, write=False)])
    assert not (src / "new").exists()


def test_delete_folder_only_when_empty(out):
    (out / "full").mkdir()
    (out / "full" / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(MacroError, match="is not empty"):
        _run(f'opensak.delete_folder("{_p(out / "full")}")',
             [FolderPermission(str(out), read=True, write=True)])
    assert (out / "full" / "a.txt").exists()


def test_a_permitted_folder_itself_is_never_deleted(tmp_path, out):
    with pytest.raises(MacroError, match="Folder permissions"):
        _run(f'opensak.delete_folder("{_p(out)}")',
             [FolderPermission(str(tmp_path), read=True, write=True),
              FolderPermission(str(out), read=True, write=True)])
    assert out.is_dir()


def test_delete_folder_refuses_a_file(out):
    (out / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(MacroError, match="not a folder"):
        _run(f'opensak.delete_folder("{_p(out / "a.txt")}")',
             [FolderPermission(str(out), read=True, write=True)])


# ── file_info ────────────────────────────────────────────────────────────────

def test_file_info(src):
    lines = _run(f'local f = opensak.file_info("{_p(src / "pq.zip")}")\n'
                 'print(f.name, f.dir, f.size, f.modified)\n'
                 f'print(opensak.file_info("{_p(src / "nope")}"))',
                 [FolderPermission(str(src), read=True, write=False)])
    assert lines == [f"pq.zip\tfalse\t{(src / 'pq.zip').stat().st_size}\t1600000000", "nil"]


def test_file_info_needs_read_permission(src):
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.file_info("{_p(src / "pq.zip")}")', [])
