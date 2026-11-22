"""tests/unit-tests/test_macro_repository_1342.py — macros from OpenSAK-Macros (#1342).

Covers opensak.macro.repository: reading index.json, matching local macros
by hash (own macros, renamed copies, other line endings, changed locally,
withdrawn, newer API, untracked), install records, the once-a-day startup
check, and installing, updating and removing with checked downloads. No
network: downloads go through a fake fetcher.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from opensak.macro import repository as repo
from tests.macro_index_data import (
    GONE, PNG, README, V1, V2, FakeFetch, _file, _index_data, _sha,
)

@pytest.fixture
def index() -> repo.MacroIndex:
    return repo.parse_index(_index_data())


@pytest.fixture
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "macros"
    d.mkdir()
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d.resolve()


def _put(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _info(path: Path):
    from opensak.macro.metadata import read_metadata
    return read_metadata(path)


# ── Reading the index ─────────────────────────────────────────────────────────

def test_parse_index(index):
    macro = index.macros["roadsheet"]
    assert macro.name == "Roadsheet" and macro.version == "1.1" and macro.requires_api == 4
    assert [f.name for f in macro.files] == ["README.md", "roadsheet.lua", "img/shot.png"]
    assert macro.lua == "roadsheet.lua" and macro.lua_file.sha256 == _sha(V2)
    assert [v.version for v in macro.versions] == ["1.1", "1.0"]
    assert macro.declared == ("write", "network:example.com")
    assert macro.changes_since("1.0") == (("1.1", "prints 2"),)
    assert macro.changes_since("0.1") == macro.changelog
    assert index.withdrawn["old-one"].hashes == {_sha(GONE)}
    assert index.find_hash(_sha(V1)) == ("roadsheet", "1.0")
    assert index.find_hash(_sha(GONE)) == ("old-one", "0.9")
    assert index.find_hash(_sha(b"mine")) is None


def test_newer_schema_is_refused():
    with pytest.raises(repo.RepositoryError, match="format 2"):
        repo.parse_index({"schema": 2, "macros": []})
    with pytest.raises(repo.RepositoryError):
        repo.parse_index([])


@pytest.mark.parametrize("macro_id", ["../evil", ".hidden", "a/b", "", "c:\\x"])
def test_unsafe_ids_are_left_out(macro_id):
    data = _index_data()
    data["macros"][0]["id"] = macro_id
    assert repo.parse_index(data).macros == {}


@pytest.mark.parametrize("path, url", [
    ("macros/roadsheet/../../outside.lua", None),
    ("macros/other/x.lua", None),
    ("macros/roadsheet/.git/config", None),
    ("macros/roadsheet/README.md", "https://evil.example.com/README.md"),
])
def test_unsafe_files_are_left_out(path, url):
    entry = _file(path, README)
    if url:
        entry["url"] = url
    data = _index_data(files=[entry, _file("macros/roadsheet/roadsheet.lua", V2)])
    assert [f.name for f in repo.parse_index(data).macros["roadsheet"].files] == ["roadsheet.lua"]


def test_fetch_index_uses_the_fetcher():
    fetch = FakeFetch(_index_data())
    index = repo.fetch_index(fetch)
    assert fetch.urls == [repo.INDEX_URL] and "roadsheet" in index.macros
    with pytest.raises(repo.RepositoryError, match="JSON"):
        repo.fetch_index(lambda url, limit: b"<html>")


# ── Matching local macros ─────────────────────────────────────────────────────

def test_own_macro_is_local_and_untouched(macros_dir, index):
    mine = _put(macros_dir / "roadsheet" / "roadsheet.lua", b"-- Name: Roadsheet\nprint('mine')\n")
    statuses = repo.match_macros([_info(mine)], index, 4)
    assert statuses[mine].state == repo.LOCAL and not statuses[mine].from_index
    assert repo.install_records() == {}       # a name and folder match is not enough


def test_hash_match_is_adopted_even_renamed(macros_dir, index):
    copy = _put(macros_dir / "My road sheet.lua", V1)
    status = repo.match_macros([_info(copy)], index, 4)[copy]
    assert (status.state, status.id, status.version, status.latest) == \
        (repo.UPDATE, "roadsheet", "1.0", "1.1")
    record = repo.install_records()["roadsheet"]
    assert record.lua == "My road sheet.lua" and record.version == "1.0"
    # Edited afterwards: still known, now changed locally.
    copy.write_bytes(V1 + b"-- my change\n")
    status = repo.match_macros([_info(copy)], index, 4)[copy]
    assert (status.state, status.version) == (repo.CHANGED, "1.0")
    assert status.can_update


@pytest.mark.parametrize("data", [
    V2.replace(b"\r\n", b"\n"), b"\xef\xbb\xbf" + V2, b"\xef\xbb\xbf" + V2.replace(b"\r\n", b"\n"),
])
def test_other_line_endings_and_bom_still_match(macros_dir, index, data):
    copy = _put(macros_dir / "roadsheet.lua", data)
    assert repo.match_macro(copy, index, 4).state == repo.UP_TO_DATE


def test_newer_api_and_withdrawn(macros_dir):
    index = repo.parse_index(_index_data(latest_api=99))
    old = _put(macros_dir / "roadsheet.lua", V1)
    gone = _put(macros_dir / "old.lua", GONE)
    statuses = repo.match_macros([_info(old), _info(gone)], index, 4)
    assert statuses[old].state == repo.NEEDS_NEWER_API and not statuses[old].can_update
    assert statuses[gone].state == repo.WITHDRAWN
    assert statuses[gone].withdrawn.name == "Old one"
    assert repo.withdrawn_macros(statuses) == {gone: statuses[gone]}


def test_stop_tracking_and_track_again(macros_dir, index):
    copy = _put(macros_dir / "roadsheet.lua", V1)
    repo.match_macros([_info(copy)], index, 4)
    assert repo.record_for(copy) is not None
    repo.set_tracked(copy, False)
    assert repo.record_for(copy) is None and not repo.is_tracked(copy)
    statuses = repo.match_macros([_info(copy)], index, 4)
    assert statuses[copy].state == repo.UNTRACKED and repo.install_records() == {}
    repo.set_tracked(copy, True)
    assert repo.match_macros([_info(copy)], index, 4)[copy].state == repo.UPDATE


# ── Startup check ─────────────────────────────────────────────────────────────

def test_startup_check_once_a_day():
    now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    assert repo.check_at_startup()                 # on by default
    assert repo.startup_check_due(now)             # never checked
    repo.note_checked(now - timedelta(hours=23))
    assert not repo.startup_check_due(now)
    repo.note_checked(now - timedelta(hours=25))
    assert repo.startup_check_due(now)
    repo.note_checked(now + timedelta(hours=5))    # clock went back: check
    assert repo.startup_check_due(now)
    repo.set_check_at_startup(False)
    assert not repo.startup_check_due(now)


# ── Install, update, remove ───────────────────────────────────────────────────

def test_install_into_own_folder(macros_dir, index):
    macro = index.macros["roadsheet"]
    fetch = FakeFetch(_index_data())
    record = repo.install(macro, repo.download_files(macro, fetch=fetch))
    folder = macros_dir / "roadsheet"
    assert (folder / "roadsheet.lua").read_bytes() == V2         # bytes as published
    assert (folder / "README.md").read_bytes() == README
    assert (folder / "img" / "shot.png").read_bytes() == PNG
    assert record.lua == "roadsheet/roadsheet.lua" and record.version == "1.1"
    assert set(record.files) == {"roadsheet/README.md", "roadsheet/roadsheet.lua",
                                 "roadsheet/img/shot.png"}
    assert not (folder / repo.PREVIOUS_DIR).exists()
    lua = folder / "roadsheet.lua"
    assert repo.match_macro(lua, index, 4).state == repo.UP_TO_DATE


def test_tampered_download_writes_nothing(macros_dir, index):
    macro = index.macros["roadsheet"]
    lua_url = macro.lua_file.url
    fetch = FakeFetch(_index_data(), {lua_url: V2 + b"os.execute('evil')"})
    with pytest.raises(repo.RepositoryError, match="does not match"):
        repo.download_files(macro, fetch=fetch)
    assert not (macros_dir / "roadsheet").exists()


def test_update_keeps_previous_version(macros_dir, index):
    folder = macros_dir / "roadsheet"
    old_lua = _put(folder / "roadsheet.lua", V1)
    _put(folder / "notes.txt", b"mine")                           # not from the index
    old_readme = _put(folder / "README.md", b"old readme")
    _put(folder / "roadsheet.db3", b"user data")
    repo.match_macros([_info(old_lua)], index, 4)                 # adopted
    status = repo.match_macro(old_lua, index, 4)
    assert status.state == repo.UPDATE
    target, only_lua = repo.update_folder(status, old_lua)
    assert (target, only_lua) == (folder, False)                  # its own folder
    macro = status.macro
    repo.install(macro, repo.download_files(macro, only_lua, FakeFetch(_index_data())),
                 target, status.record, lua_target=old_lua)
    assert old_lua.read_bytes() == V2 and old_readme.read_bytes() == README
    previous = folder / repo.PREVIOUS_DIR
    assert (previous / "roadsheet.lua").read_bytes() == V1
    assert (previous / "README.md").read_bytes() == b"old readme"
    assert (folder / "notes.txt").read_bytes() == b"mine"
    assert (folder / "roadsheet.db3").read_bytes() == b"user data"
    from opensak.macro.metadata import discover_macros
    assert [m.path for m in discover_macros(macros_dir)] == [old_lua]   # .previous is hidden


def test_update_of_hand_copied_macro_only_replaces_its_file(macros_dir, index):
    copy = _put(macros_dir / "My road sheet.lua", V1)
    status = repo.match_macros([_info(copy)], index, 4)[copy]
    target, only_lua = repo.update_folder(status, copy)
    assert (target, only_lua) == (macros_dir, True)
    fetch = FakeFetch(_index_data())
    repo.install(status.macro, repo.download_files(status.macro, only_lua, fetch),
                 target, status.record, lua_target=copy)
    assert copy.read_bytes() == V2
    assert not (macros_dir / "README.md").exists() and len(fetch.urls) == 1
    assert repo.record_for(copy).version == "1.1"


def test_update_drops_unchanged_files_the_new_version_lacks(macros_dir, index):
    macro = index.macros["roadsheet"]
    folder = macros_dir / "roadsheet"
    old = _put(folder / "old.png", b"old image")
    keep = _put(folder / "edited.txt", b"edited")
    record = repo.InstallRecord("roadsheet", "1.0", "roadsheet/roadsheet.lua", {
        "roadsheet/old.png": _sha(b"old image"), "roadsheet/edited.txt": _sha(b"original"),
    }, "")
    repo.install(macro, repo.download_files(macro, fetch=FakeFetch(_index_data())),
                 record=record)
    assert not old.exists() and (folder / repo.PREVIOUS_DIR / "old.png").exists()
    assert keep.read_bytes() == b"edited"


def test_remove_keeps_companion_data_unless_asked(macros_dir, index):
    macro = index.macros["roadsheet"]
    repo.install(macro, repo.download_files(macro, fetch=FakeFetch(_index_data())))
    folder = macros_dir / "roadsheet"
    lua = folder / "roadsheet.lua"
    data = _put(folder / "roadsheet.db3", b"user data")
    result = repo.remove(lua)
    assert result.kept_data == (data,) and result.kept == ()
    assert data.exists() and not lua.exists()
    assert not (folder / "README.md").exists() and repo.install_records() == {}
    assert sorted(p.name for p in folder.rglob("*")) == ["roadsheet.db3"]
    repo.remove(lua, delete_data=True)
    assert not folder.exists()


def test_remove_keeps_edited_extra_files(macros_dir, index):
    macro = index.macros["roadsheet"]
    repo.install(macro, repo.download_files(macro, fetch=FakeFetch(_index_data())))
    readme = macros_dir / "roadsheet" / "README.md"
    readme.write_bytes(b"my notes")
    result = repo.remove(macros_dir / "roadsheet" / "roadsheet.lua")
    assert result.kept == (readme,) and result.kept_data == () and readme.exists()


def _saved(macros_dir: Path) -> tuple[Path, Path]:
    """The saved settings and remembered form values of roadsheet/roadsheet.lua."""
    settings = macros_dir / "settings"
    return (_put(settings / "roadsheet" / "roadsheet.json", b"{}"),
            _put(settings / ".forms" / "roadsheet" / "roadsheet.json", b"{}"))


def test_remove_deletes_saved_settings_with_the_data(macros_dir, index):
    # The macro's opensak.settings and remembered form values were left
    # behind after a remove, even with "also delete its data" ticked.
    macro = index.macros["roadsheet"]
    repo.install(macro, repo.download_files(macro, fetch=FakeFetch(_index_data())))
    lua = macros_dir / "roadsheet" / "roadsheet.lua"
    saved, form = _saved(macros_dir)
    other = _put(macros_dir / "settings" / "other.json", b"{}")
    assert repo.data_files(lua) == [saved, form]
    result = repo.remove(lua, delete_data=True)
    assert saved in result.removed and form in result.removed and result.kept_data == ()
    assert not (macros_dir / "roadsheet").exists()
    # The emptied folders go, the settings of other macros stay.
    assert not saved.parent.exists() and not form.parent.exists()
    assert other.exists()


def test_remove_reports_saved_settings_it_keeps(macros_dir, index):
    macro = index.macros["roadsheet"]
    repo.install(macro, repo.download_files(macro, fetch=FakeFetch(_index_data())))
    saved, form = _saved(macros_dir)
    result = repo.remove(macros_dir / "roadsheet" / "roadsheet.lua")
    assert result.kept_data == (saved, form) and result.kept == ()
    assert saved.exists() and form.exists()


def test_installed_files_become_tracked_again(macros_dir, index):
    lua = macros_dir / "roadsheet" / "roadsheet.lua"
    repo.set_tracked(lua, False)
    macro = index.macros["roadsheet"]
    repo.install(macro, repo.download_files(macro, fetch=FakeFetch(_index_data())))
    assert repo.is_tracked(lua) and repo.record_for(lua) is not None
