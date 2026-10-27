"""
tests/unit-tests/test_backup_macros_1137.py — backups include the user's Lua
macros folder, and a restore can bring the macros back (#1137, part of #942).

The root conftest isolates the install folder (and with it the macros
folder) and Path.home() per test, so everything lives under tmp_path.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensak.backup import exit_state
from opensak.backup.backupset import (
    KIND_MANUAL,
    KIND_SAFETY,
    known_macro_databases,
    read_backup_set,
    write_backup_set,
)
from opensak.backup.macros_backup import MACROS_DIRNAME, files_to_back_up, macros_root
from opensak.backup.macros_restore import (
    has_macros,
    plan_macro_restore,
    restore_macros,
)
from opensak.backup.settings_restore import _kept
from opensak.macro import db_access, examples
from opensak.macro.db_access import WriteApproval, read_macro_source, source_hash
from opensak.settings_store import get_store

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 27, 10, 15)

MODES = pytest.mark.parametrize("compress", [False, True], ids=["folder", "zip"])


def _make_db(path: Path, rows: int = 5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (n INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()
    return path


def _rows(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT count(*) FROM t").fetchone()[0]
    finally:
        conn.close()


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def dbs(tmp_path: Path) -> list[SimpleNamespace]:
    path = _make_db(tmp_path / "dbs" / "Default.db")
    return [SimpleNamespace(name="Default", path=path)]


@pytest.fixture
def macros() -> Path:
    """The user's macros folder with a typical mix of files."""
    root = macros_root()
    _write(root / "tag_unfound.lua", "-- Name: Tag\nprint('tag')\n")
    _write(root / "lists" / "challenges.csv", "GC1,GC2\n")
    _write(root / "settings" / "tag_unfound.json", '{"radius": 5}\n')
    return root


def _set_files(backup_set) -> set[str]:
    """The files under macros/ in a set, relative to it."""
    prefix = f"{MACROS_DIRNAME}/"
    if backup_set.compressed:
        with zipfile.ZipFile(backup_set.path) as zf:
            return {n[len(prefix):] for n in zf.namelist() if n.startswith(prefix)}
    folder = backup_set.path / MACROS_DIRNAME
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


def _manifest(backup_set) -> dict:
    if backup_set.compressed:
        with zipfile.ZipFile(backup_set.path) as zf:
            return json.loads(zf.read("manifest.json"))
    return json.loads((backup_set.path / "manifest.json").read_text(encoding="utf-8"))


# ── Backup ────────────────────────────────────────────────────────────────────

@MODES
def test_macros_folder_is_backed_up(dbs, backups, macros, compress):
    result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=compress)

    s = result.backup_set
    assert _set_files(s) == {
        "tag_unfound.lua", "lists/challenges.csv", "settings/tag_unfound.json",
    }
    assert s.macros is not None
    assert s.macros.files == 3
    assert s.macros.path == str(macros)
    assert _manifest(s)["macros"]["dir"] == "macros"
    assert result.skipped_macro_files == ()
    assert has_macros(s)


def test_unchanged_examples_are_left_out_edited_ones_kept(dbs, backups, macros):
    names = examples.list_examples()
    assert len(names) >= 2, "the shipped examples are needed for this test"
    examples.install_example(names[0])
    edited = examples.install_example(names[1])
    edited.write_text(edited.read_text(encoding="utf-8") + "\n-- my change\n",
                      encoding="utf-8")
    # A data file of the user's own next to the examples is theirs too.
    _write(macros / "examples" / "my_list.txt", "mine\n")
    # Example settings are the user's choices, not shipped files.
    _write(macros / "settings" / "examples" / "x.json", "{}\n")

    s = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW).backup_set

    files = _set_files(s)
    assert f"examples/{names[1]}" in files
    assert f"examples/{names[0]}" not in files
    assert "examples/my_list.txt" in files
    assert "settings/examples/x.json" in files
    shipped_data = [
        p.name for p in examples.bundled_examples_dir().iterdir()
        if p.is_file() and p.suffix != ".lua"
    ]
    assert not any(f"examples/{n}" in files for n in shipped_data)


@MODES
def test_sqlite_files_are_snapshotted_not_copied(dbs, backups, macros, compress):
    db = _make_db(macros / "stats.db3", rows=7)
    conn = sqlite3.connect(str(db))   # held open: a -wal may exist
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO t VALUES (99)")
    conn.commit()
    try:
        s = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW,
                             compress=compress).backup_set
    finally:
        conn.close()

    assert not any(f.startswith("stats.db3") for f in _set_files(s))
    assert [Path(m.path).name for m in s.macro_databases] == ["stats.db3"]
    # Found in the macros folder, not because a macro opened it.
    assert known_macro_databases() == []


def test_safety_set_has_no_macros(dbs, backups, macros):
    s = write_backup_set([], KIND_SAFETY, folder=backups, now=NOW).backup_set
    assert s.macros is None
    assert "macros" not in _manifest(s)
    assert not (s.path / MACROS_DIRNAME).exists()


def test_no_macros_no_key(dbs, backups):
    s = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW).backup_set
    assert s.macros is None
    assert "macros" not in _manifest(s)


def test_unreadable_macro_file_does_not_fail_the_backup(dbs, backups, macros, monkeypatch):
    import shutil as shutil_mod
    real = shutil_mod.copy2

    def copy2(src, dst, *a, **kw):
        if Path(src).name == "challenges.csv":
            raise PermissionError(13, "denied", str(src))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr("opensak.backup.macros_backup.shutil.copy2", copy2)
    result = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW, compress=False)
    assert result.skipped_macro_files == ("lists/challenges.csv",)
    assert "lists/challenges.csv" not in _set_files(result.backup_set)


def test_old_manifest_without_macros_reads(dbs, backups, macros):
    s = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW).backup_set
    manifest = s.path / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["macros"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert read_backup_set(s.path).macros is None


def test_manifest_cannot_point_macros_elsewhere(dbs, backups, macros):
    s = write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW).backup_set
    manifest = s.path / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["macros"]["dir"] = "../elsewhere"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert read_backup_set(s.path).macros is None


# ── Back up on exit ───────────────────────────────────────────────────────────

def test_exit_state_without_macros_is_unchanged(dbs):
    assert "macros" not in exit_state.current_state(dbs)


def test_macro_changes_count_on_exit(macros):
    exit_state.record_backup_state()
    assert not exit_state.has_changes_since_backup()

    _write(macros / "new_macro.lua", "print(1)\n")
    assert exit_state.has_changes_since_backup()
    exit_state.record_backup_state()
    assert not exit_state.has_changes_since_backup()

    (macros / "lists" / "challenges.csv").unlink()
    assert exit_state.has_changes_since_backup()


def test_using_a_macro_database_is_not_a_change(macros):
    db = _make_db(macros / "stats.db3")
    exit_state.record_backup_state()
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    assert not exit_state.has_changes_since_backup()


# ── Restore: plan ─────────────────────────────────────────────────────────────

def _backup(dbs, backups, compress=False):
    return write_backup_set(dbs, KIND_MANUAL, folder=backups, now=NOW,
                            compress=compress).backup_set


@MODES
def test_plan_sorts_files_into_new_same_and_conflicts(dbs, backups, macros, compress):
    s = _backup(dbs, backups, compress)
    (macros / "lists" / "challenges.csv").unlink()
    _write(macros / "tag_unfound.lua", "-- changed since\n")

    plan = plan_macro_restore(s)

    assert [i.rel for i in plan.new] == ["lists/challenges.csv"]
    assert [i.rel for i in plan.same] == ["settings/tag_unfound.json"]
    assert [i.rel for i in plan.conflicts] == ["tag_unfound.lua"]


@MODES
def test_restore_into_an_empty_macros_folder(dbs, backups, macros, compress):
    s = _backup(dbs, backups, compress)
    import shutil
    shutil.rmtree(macros)
    macros.mkdir()

    result = restore_macros(plan_macro_restore(s))

    assert sorted(result.restored) == [
        "lists/challenges.csv", "settings/tag_unfound.json", "tag_unfound.lua",
    ]
    assert (macros / "lists" / "challenges.csv").read_text(encoding="utf-8") == "GC1,GC2\n"
    assert result.failed == []


@MODES
def test_conflicts_are_kept_unless_chosen(dbs, backups, macros, compress):
    s = _backup(dbs, backups, compress)
    _write(macros / "tag_unfound.lua", "-- mine\n")
    _write(macros / "settings" / "tag_unfound.json", '{"radius": 9}\n')

    result = restore_macros(plan_macro_restore(s), replace=["tag_unfound.lua"], now=LATER)

    assert result.replaced == ["tag_unfound.lua"]
    assert result.kept == ["settings/tag_unfound.json"]
    assert (macros / "tag_unfound.lua").read_text(encoding="utf-8").startswith("-- Name: Tag")
    bak = macros / "tag_unfound.lua.20261027-101500.bak"
    assert bak.read_text(encoding="utf-8") == "-- mine\n"
    assert (macros / "settings" / "tag_unfound.json").read_text(encoding="utf-8") == (
        '{"radius": 9}\n'
    )


def test_bak_name_is_never_reused(dbs, backups, macros):
    s = _backup(dbs, backups)
    _write(macros / "tag_unfound.lua", "-- mine\n")
    _write(macros / "tag_unfound.lua.20261027-101500.bak", "older\n")

    restore_macros(plan_macro_restore(s), replace=["tag_unfound.lua"], now=LATER)

    assert (macros / "tag_unfound.lua.20261027-101500.bak").read_text(
        encoding="utf-8") == "older\n"
    assert (macros / "tag_unfound.lua.20261027-101500-2.bak").read_text(
        encoding="utf-8") == "-- mine\n"


@MODES
def test_macro_database_restore(dbs, backups, macros, compress):
    db = _make_db(macros / "stats.db3", rows=7)
    s = _backup(dbs, backups, compress)

    # Gone: restored as new.
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    plan = plan_macro_restore(s)
    assert [i.rel for i in plan.new if i.database] == ["stats.db3"]
    restore_macros(plan)
    assert _rows(db) == 7

    # Changed: a conflict; replacing keeps the current one as a .bak.
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO t VALUES (100)")
    conn.commit()
    conn.close()
    plan = plan_macro_restore(s)
    assert [i.rel for i in plan.conflicts] == ["stats.db3"]
    result = restore_macros(plan, replace=["stats.db3"], now=LATER)
    assert result.replaced == ["stats.db3"]
    assert _rows(db) == 7
    assert _rows(macros / "stats.db3.20261027-101500.bak") == 8


def test_macro_database_outside_the_macros_folder_is_not_restored(
    tmp_path, dbs, backups, macros,
):
    from opensak.backup.backupset import remember_macro_database
    outside = _make_db(tmp_path / "elsewhere" / "data.db3")
    remember_macro_database(outside)
    s = _backup(dbs, backups)
    outside.unlink()

    plan = plan_macro_restore(s)
    assert plan.outside == [str(outside.resolve())]
    restore_macros(plan)
    assert not outside.exists()


def test_crafted_zip_cannot_write_outside_the_macros_folder(
    tmp_path, dbs, backups, macros,
):
    s = _backup(dbs, backups, compress=True)
    with zipfile.ZipFile(s.path, "a") as zf:
        zf.writestr("macros/../../evil.lua", "bad")
        zf.writestr("macros/sub/../../../evil2.lua", "bad")
        zf.writestr("macros/hidden.db3", "not restored as a file")

    plan = plan_macro_restore(s)
    rels = {i.rel for i in plan.new + plan.same + plan.conflicts}
    assert not any("evil" in r for r in rels)
    assert "hidden.db3" not in rels
    restore_macros(plan)
    assert not list(tmp_path.rglob("evil*.lua"))


def test_restore_into_a_moved_macros_folder(tmp_path, dbs, backups, macros):
    s = _backup(dbs, backups)
    other = tmp_path / "new_macros"
    other.mkdir()
    result = restore_macros(plan_macro_restore(s, root=other))
    assert (other / "tag_unfound.lua").is_file()
    assert "tag_unfound.lua" in result.restored


# ── Restore: approvals ────────────────────────────────────────────────────────

def _approve(path: Path) -> str:
    sha = source_hash(read_macro_source(path))
    db_access.approve(path, WriteApproval.ALWAYS, sha)
    return sha


@MODES
def test_approvals_come_back_with_the_macro(dbs, backups, macros, compress):
    macro = macros / "tag_unfound.lua"
    sha = _approve(macro)
    s = _backup(dbs, backups, compress)

    macro.unlink()
    db_access.save_always_approved([])
    db_access.reset_session()
    assert not db_access.is_approved(macro, sha)

    result = restore_macros(plan_macro_restore(s))

    assert result.approvals == 1
    assert db_access.is_approved(macro, sha)


def test_kept_macro_does_not_get_the_backups_approval(dbs, backups, macros):
    macro = macros / "tag_unfound.lua"
    _approve(macro)
    s = _backup(dbs, backups)

    _write(macro, "-- my new code\n")
    db_access.save_always_approved([])
    db_access.reset_session()

    result = restore_macros(plan_macro_restore(s))   # conflict, kept
    assert result.approvals == 0
    assert db_access.approvals() == []


def test_approval_follows_a_moved_macros_folder(tmp_path, dbs, backups, macros):
    macro = macros / "tag_unfound.lua"
    sha = _approve(macro)
    s = _backup(dbs, backups)
    db_access.save_always_approved([])
    db_access.reset_session()

    other = tmp_path / "new_macros"
    other.mkdir()
    result = restore_macros(plan_macro_restore(s, root=other))

    assert result.approvals == 1
    assert db_access.is_approved(other / "tag_unfound.lua", sha)
    assert not db_access.is_approved(macro, sha)


def test_existing_approval_for_the_same_code_is_kept(dbs, backups, macros):
    macro = macros / "tag_unfound.lua"
    sha = _approve(macro)
    s = _backup(dbs, backups)
    # Today's record allows more than the backup's.
    db_access.grant_folder(macro, macros / "lists", True, sha)
    before = db_access.approvals()

    result = restore_macros(plan_macro_restore(s))   # same file: unchanged

    assert result.unchanged and result.approvals == 0
    assert db_access.approvals() == before


# ── Custom fields (#1028) are covered by the settings part of a backup ────────

def test_global_custom_field_definitions_are_in_the_backup_and_restored(dbs, backups):
    from opensak.db.custom_fields import GLOBAL_DELETED_KEY, GLOBAL_KEY
    get_store().set(GLOBAL_KEY, [{"name": "Puzzle", "type": "text"}])
    get_store().set(GLOBAL_DELETED_KEY, ["Old"])

    s = _backup(dbs, backups)
    saved = json.loads((s.path / "settings" / "opensak.json").read_text(encoding="utf-8"))

    assert saved[GLOBAL_KEY] == [{"name": "Puzzle", "type": "text"}]
    assert saved[GLOBAL_DELETED_KEY] == ["Old"]
    # "Also restore settings" takes them from the backup, not today's file.
    assert not _kept(GLOBAL_KEY) and not _kept(GLOBAL_DELETED_KEY)


def test_files_to_back_up_skips_links(tmp_path):
    root = tmp_path / "m"
    _write(root / "a.lua", "x")
    target = _write(tmp_path / "outside" / "secret.txt", "s")
    try:
        (root / "link.txt").symlink_to(target)
        (root / "linkdir").symlink_to(target.parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links not available")
    assert [f.rel for f in files_to_back_up(root)] == ["a.lua"]
