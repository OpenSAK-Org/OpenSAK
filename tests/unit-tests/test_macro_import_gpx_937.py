"""tests/unit-tests/test_macro_import_gpx_937.py — the unattended GPX / PQ
zip import (#937): opensak.import_gpx() with a saved import setting, a
file or a folder; the target database; missing and failing files; the
folder and write approvals; no question asked."""

import sqlite3
from pathlib import Path

import pytest

from opensak.db.manager import get_db_manager
from opensak.importer.gpx_import_settings import GpxImportProfile, GpxImportSettings
from opensak.macro import FolderPermission, MacroError, MacroRuntime, WriteApproval
from tests.data import SAMPLE_GPX, SAMPLE_WPTS_GPX, make_variant_gpx, make_zip, write_gpx


@pytest.fixture(autouse=True)
def _temp_in_tmp_path(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.fixture
def manager():
    mgr = get_db_manager()
    mgr.ensure_active_initialised()
    return mgr


def _codes(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT gc_code FROM caches"))
    finally:
        conn.close()


def _pq_folder(tmp_path: Path) -> Path:
    """A download folder: a PQ zip, a GPX with its waypoints file, and a
    file of another type."""
    folder = tmp_path / "pq"
    folder.mkdir()
    write_gpx(folder, "a.gpx", SAMPLE_GPX)
    write_gpx(folder, "a-wpts.gpx", SAMPLE_WPTS_GPX)
    # make_zip leaves its temp files next to the zip, so not in the folder.
    make_zip(tmp_path, "b.zip", {"b.gpx": make_variant_gpx("GCAAA01", "GCAAA02")}).rename(
        folder / "b.zip")
    (folder / "readme.txt").write_text("not a GPX", encoding="utf-8")
    return folder


def _save_setting(tmp_path: Path, name: str, **settings) -> None:
    GpxImportProfile(name, GpxImportSettings(**settings)).save(tmp_path / "import_settings")


class Host:
    def __init__(self, answer=WriteApproval.SESSION):
        self.answer = answer
        self.events: list[str] = []
        self.folders_asked: list[Path] = []

    def database_name(self):
        return "Default"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        self.events.append("write?")
        return self.answer

    def approve_folder(self, target, folder, write):
        from opensak.macro.runtime import FolderApproval
        self.folders_asked.append(folder)
        return FolderApproval.ONCE

    def database_list_changed(self):
        self.events.append("list")

    def caches_changed(self, codes, added):
        self.events.append(f"changed:{added}")

    def caches_removed(self):
        self.events.append("removed")

    def end_macro(self):
        pass


def _run(source, folder, host=None, permitted=True):
    out: list[str] = []
    host = host or Host()
    permissions = [FolderPermission(str(folder), read=True, write=False)] if permitted else []
    MacroRuntime(host, output=out.append, folder_permissions=permissions,
                 import_settings_dir=folder / "import_settings").run(
        source, chunk_name="m.lua", base_dir=folder)
    return host, out


# ── Sources ──────────────────────────────────────────────────────────────────


def test_folder_imports_every_gpx_and_zip(manager, tmp_path):
    _pq_folder(tmp_path)
    host, out = _run("""
        local r = opensak.import_gpx("pq", { quiet = true })
        print(r.ok, r.kind, r.database, r.created, r.updated, r.waypoints, #r.files)
        for _, f in ipairs(r.files) do print(f.path:match("[^/\\\\]+$"), f.created) end
    """, tmp_path)
    assert out[0] == f"true\tfolder\t{manager.active.name}\t4\t0\t1\t2"
    # The waypoints file is imported with a.gpx, not on its own.
    assert out[1:] == ["a.gpx\t2", "b.zip\t2"]
    assert {"GC12345", "GC99999", "GCAAA01", "GCAAA02"} <= set(_codes(manager.active.path))
    assert "changed:True" in host.events


def test_single_file(manager, tmp_path):
    write_gpx(tmp_path, "one.gpx", make_variant_gpx("GCONE01", "GCONE02"))
    _, out = _run('local r = opensak.import_gpx("one.gpx", { quiet = true }) '
                  'print(r.kind, r.created)', tmp_path)
    assert out == ["file\t2"]


def test_second_run_updates(manager, tmp_path):
    _pq_folder(tmp_path)
    _run('opensak.import_gpx("pq", { quiet = true })', tmp_path)
    _, out = _run('local r = opensak.import_gpx("pq", { quiet = true }) '
                  'print(r.created, r.updated)', tmp_path)
    assert out == ["0\t4"]


def test_empty_folder_is_not_an_error(manager, tmp_path):
    (tmp_path / "empty").mkdir()
    _, out = _run('local r = opensak.import_gpx("empty") print(r.ok, #r.files)', tmp_path)
    assert out[-1] == "true\t0"
    assert "no .gpx or .zip files to import" in out[1]


def test_prints_the_summary(manager, tmp_path):
    _pq_folder(tmp_path)
    _, out = _run('opensak.import_gpx("pq")', tmp_path)
    assert out[0] == f"GPX import from pq (folder) into {manager.active.name}"
    assert out[1].startswith("  a.gpx: 2 new, 0 updated")


# ── Saved settings ───────────────────────────────────────────────────────────


def test_saved_setting_with_its_database(manager, tmp_path):
    target = manager.new_database("Nightly")
    folder = _pq_folder(tmp_path)
    single = write_gpx(tmp_path, "single.gpx",
                       make_variant_gpx("GCSGL01", "GCSGL02", "555", "556"))
    _save_setting(tmp_path, "Nightly PQs", database=str(target.path),
                  files=[str(single), str(tmp_path / "gone.gpx")], folders=[str(folder)])
    host, out = _run("""
        local r = opensak.import_gpx("Nightly PQs", { quiet = true })
        print(r.ok, r.kind, r.database, r.created, #r.files, #r.missing)
    """, tmp_path)
    assert out == ["true\tsetting\tNightly\t6\t3\t1"]
    assert "GCSGL01" in _codes(target.path) and "GCAAA01" in _codes(target.path)
    assert "GCSGL01" not in _codes(manager.active.path)
    # Another database than the active one: nothing to refresh.
    assert "changed:True" not in host.events


def test_database_option_wins_over_the_setting(manager, tmp_path):
    a = manager.new_database("SettingDb")
    b = manager.new_database("OptionDb")
    _save_setting(tmp_path, "S", database=str(a.path), folders=[str(_pq_folder(tmp_path))])
    _run('opensak.import_gpx("S", { database = "OptionDb", quiet = true })', tmp_path)
    assert _codes(a.path) == [] and "GCAAA01" in _codes(b.path)


def test_setting_without_database_uses_the_active_one(manager, tmp_path):
    _save_setting(tmp_path, "S", folders=[str(_pq_folder(tmp_path))])
    _, out = _run('print(opensak.import_gpx("S", { quiet = true }).database)', tmp_path)
    assert out == [manager.active.name]


def test_setting_name_comes_before_a_path(manager, tmp_path):
    _pq_folder(tmp_path)
    _save_setting(tmp_path, "pq", files=[])
    _, out = _run('local r = opensak.import_gpx("pq", { quiet = true }) print(r.kind, #r.files)',
                  tmp_path)
    assert out == ["setting\t0"]


def test_setting_with_a_removed_database(manager, tmp_path):
    _save_setting(tmp_path, "S", database=str(tmp_path / "removed.db"), files=[])
    with pytest.raises(MacroError, match="no longer in the list"):
        _run('opensak.import_gpx("S")', tmp_path)


def test_refused_folder_of_a_setting_fails_only_that_entry(manager, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    write_gpx(allowed, "ok.gpx", make_variant_gpx("GCOK001", "GCOK002"))
    other = tmp_path.parent / f"{tmp_path.name}-other"
    other.mkdir()
    write_gpx(other, "x.gpx", SAMPLE_GPX)
    _save_setting(allowed, "S", folders=[str(other), str(allowed)])

    class Refusing(Host):
        def approve_folder(self, target, folder, write):
            from opensak.macro.runtime import FolderApproval
            self.folders_asked.append(folder)
            return FolderApproval.DENY

    _, out = _run("""
        local r = opensak.import_gpx("S", { quiet = true })
        print(r.ok, r.errors, r.created, r.files[1].error ~= nil)
    """, allowed, host=Refusing())
    assert out == ["false\t1\t2\ttrue"]


# ── Failures and errors ──────────────────────────────────────────────────────


def test_broken_file_is_reported_and_the_others_imported(manager, tmp_path):
    folder = _pq_folder(tmp_path)
    (folder / "0broken.zip").write_text("not a zip", encoding="utf-8")
    _, out = _run("""
        local r = opensak.import_gpx("pq", { quiet = true })
        print(r.ok, r.created, r.errors)
    """, tmp_path)
    assert out == ["false\t4\t1"]


def test_folder_is_approved_once(manager, tmp_path):
    folder = _pq_folder(tmp_path)
    host, _ = _run('opensak.import_gpx("pq", { quiet = true })', tmp_path, permitted=False)
    assert host.folders_asked == [folder]


@pytest.mark.parametrize("source, options, message", [
    ('"nope"', "nil", "neither a saved import setting nor an existing file"),
    ('"notes.txt"', "nil", "is not a .gpx or .zip file"),
    ('""', "nil", "expects the name of a saved import setting"),
    ('"pq"', "{ bogus = 1 }", "unknown option"),
    ('"pq"', "{ quiet = 1 }", "quiet must be true or false"),
    ('"pq"', '{ database = "Nope" }', "no database named 'Nope'"),
    ('"pq"', "3", "options must be a table"),
])
def test_bad_arguments(manager, tmp_path, source, options, message):
    _pq_folder(tmp_path)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(MacroError, match=message):
        _run(f"opensak.import_gpx({source}, {options})", tmp_path)


def test_write_denied(manager, tmp_path):
    _pq_folder(tmp_path)
    with pytest.raises(MacroError, match="did not allow"):
        _run('opensak.import_gpx("pq")', tmp_path, host=Host(WriteApproval.DENY))
    assert "GCAAA01" not in _codes(manager.active.path)


def test_not_available_without_write_flag(manager, tmp_path, monkeypatch):
    from opensak.utils import flags
    monkeypatch.setattr(flags, "lua_macro_write", False)
    _, out = _run("print(opensak.import_gpx)", tmp_path)
    assert out == ["nil"]


def test_refused_inside_a_transaction(manager, tmp_path):
    _pq_folder(tmp_path)
    with pytest.raises(MacroError, match="transaction"):
        _run('opensak.transaction(function() opensak.import_gpx("pq") end)', tmp_path)
