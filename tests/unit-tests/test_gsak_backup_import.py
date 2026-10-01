# tests/unit-tests/test_gsak_backup_import.py — multi-database GSAK backups.
#
# A GSAK backup .zip holds one ``<name>/sqlite.db3`` per GSAK database plus
# ``gsak.db3`` (settings / saved filters). These tests cover listing such a
# backup without unpacking it, unpacking a single member, the backwards-
# compatible single-database helpers, and emptying an OpenSAK database before
# an "overwrite" import.

import importlib.util
import zipfile
from pathlib import Path

import pytest

from opensak.db.models import Cache, Log, UserNote, Waypoint
from opensak.importer.gsak_filter_importer import find_gsak_filter_db
from opensak.importer.gsak_importer import (
    clear_opensak_cache_data,
    extract_gsak_member,
    find_gsak_db3_in_zip,
    import_gsak_db,
    list_gsak_backup,
)

# Reuse the synthetic GSAK-schema builder of the main importer tests (the
# importlib import mode doesn't let test modules import each other by name).
_spec = importlib.util.spec_from_file_location(
    "_gsak_importer_tests", Path(__file__).with_name("test_gsak_importer.py")
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
_make_gsak_db = _helpers._make_gsak_db


@pytest.fixture(autouse=True)
def _temp_in_tmp_path(tmp_path, monkeypatch):
    """The helpers unpack into tempfile.mkdtemp() folders the caller owns —
    keep them inside tmp_path rather than littering the real %TEMP%."""
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _backup(tmp_path: Path, members: dict[str, bytes]) -> Path:
    archive = tmp_path / "GSAKAuto1.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return archive


# ── list_gsak_backup ─────────────────────────────────────────────────────────

class TestListBackup:
    def test_every_database_is_listed_by_folder_name(self, tmp_path):
        archive = _backup(tmp_path, {
            "AllCH/sqlite.db3": b"a" * 10,
            "AdventureLabs/sqlite.db3": b"b" * 20,
            "Holidays/sqlite.db3": b"c",
            "Holidays/Attachments/readme.txt": b"x",
            "gsak.db3": b"settings",
            "Macros/ImportAdventureLabs.db3": b"macro",
        })
        contents = list_gsak_backup(archive)
        assert [db.name for db in contents.databases] == ["AdventureLabs", "AllCH", "Holidays"]
        assert [db.size for db in contents.databases] == [20, 10, 1]
        assert contents.databases[1].member == "AllCH/sqlite.db3"
        assert contents.is_zip
        assert contents.settings_member == "gsak.db3"
        assert contents.has_settings_db

    def test_zip_with_single_database(self, tmp_path):
        contents = list_gsak_backup(_backup(tmp_path, {"Sommerhus/sqlite.db3": b"x"}))
        assert [db.name for db in contents.databases] == ["Sommerhus"]
        assert not contents.has_settings_db

    def test_database_at_zip_root_is_named_after_the_zip(self, tmp_path):
        contents = list_gsak_backup(_backup(tmp_path, {"sqlite.db3": b"x"}))
        assert [db.name for db in contents.databases] == ["GSAKAuto1"]

    def test_shallowest_gsak_db3_wins(self, tmp_path):
        contents = list_gsak_backup(_backup(tmp_path, {
            "Macros/old/gsak.db3": b"nested", "gsak.db3": b"root",
        }))
        assert contents.settings_member == "gsak.db3"

    def test_zip_without_gsak_content(self, tmp_path):
        contents = list_gsak_backup(_backup(tmp_path, {"readme.txt": b"x"}))
        assert contents.databases == []
        assert not contents.has_settings_db

    def test_single_file_is_named_after_its_folder(self, tmp_path):
        db = tmp_path / "gsak" / "data" / "AllCH" / "sqlite.db3"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"x" * 5)
        contents = list_gsak_backup(db)
        assert not contents.is_zip
        assert [(d.name, d.size, d.path) for d in contents.databases] == [("AllCH", 5, db)]
        assert not contents.has_settings_db

    def test_single_file_picks_up_gsak_db3_of_its_install(self, tmp_path):
        db = tmp_path / "gsak" / "data" / "AllCH" / "sqlite.db3"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"x")
        settings = tmp_path / "gsak" / "gsak.db3"
        settings.write_bytes(b"s")
        assert list_gsak_backup(db).settings_path == settings

    def test_renamed_single_file_uses_its_stem(self, tmp_path):
        db = tmp_path / "Export.db3"
        db.write_bytes(b"x")
        assert list_gsak_backup(db).databases[0].name == "Export"


# ── Unpacking ────────────────────────────────────────────────────────────────

class TestExtract:
    def test_extracts_only_the_member(self, tmp_path):
        archive = _backup(tmp_path, {"A/sqlite.db3": b"aaa", "B/sqlite.db3": b"bbb"})
        seen = []
        out = extract_gsak_member(archive, "B/sqlite.db3", tmp_path / "out",
                                  progress_cb=seen.append)
        assert out == tmp_path / "out" / "sqlite.db3"
        assert out.read_bytes() == b"bbb"
        assert list((tmp_path / "out").iterdir()) == [out]
        assert seen == [3]

    def test_member_path_cannot_escape_dest_dir(self, tmp_path):
        archive = _backup(tmp_path, {"../../evil/sqlite.db3": b"x"})
        out = extract_gsak_member(archive, "../../evil/sqlite.db3", tmp_path / "out")
        assert out.parent == tmp_path / "out"

    def test_find_gsak_db3_in_zip_still_returns_first_database(self, tmp_path):
        archive = _backup(tmp_path, {
            "Zulu/sqlite.db3": b"z", "Alpha/sqlite.db3": b"a", "gsak.db3": b"s",
        })
        out = find_gsak_db3_in_zip(archive)
        assert out.name == "sqlite.db3" and out.read_bytes() == b"a"
        # only that one database was unpacked
        assert [p.name for p in out.parent.iterdir()] == ["sqlite.db3"]

    def test_find_gsak_db3_in_zip_passes_plain_files_through(self, tmp_path):
        db = tmp_path / "sqlite.db3"
        assert find_gsak_db3_in_zip(db) == db

    def test_find_gsak_db3_in_zip_without_database_raises(self, tmp_path):
        with pytest.raises(ValueError):
            find_gsak_db3_in_zip(_backup(tmp_path, {"gsak.db3": b"s"}))

    def test_find_gsak_filter_db_unpacks_only_gsak_db3(self, tmp_path):
        archive = _backup(tmp_path, {"A/sqlite.db3": b"a", "gsak.db3": b"s"})
        out = find_gsak_filter_db(archive)
        assert out.read_bytes() == b"s"
        assert [p.name for p in out.parent.iterdir()] == ["gsak.db3"]


# ── Overwrite ────────────────────────────────────────────────────────────────

def test_clear_opensak_cache_data_empties_the_database(db_session, tmp_path):
    gsak = _make_gsak_db(
        tmp_path / "sqlite.db3",
        memos=[{"Code": "GC1TEST", "UserNote": "note"}],
        waypoints=[{"cParent": "GC1TEST", "cCode": "PK1TEST", "cPrefix": "PK",
                    "cName": "Parking", "cType": "Parking Area",
                    "cLat": "47.1", "cLon": "8.1", "cByuser": 0, "cDate": "", "cFlag": 0}],
        logs=[{"lParent": "GC1TEST", "lLogId": 1, "lType": "Found it", "lBy": "me",
               "lDate": "2020-01-01", "lTime": "", "lLat": "", "lLon": "",
               "lEncoded": 0, "lownerid": 1, "lHasHtml": 0, "lIsowner": 0}],
    )
    import_gsak_db(gsak, db_session)
    assert db_session.query(Cache).count() == 1
    assert db_session.query(Waypoint).count() == 1
    assert db_session.query(Log).count() == 1

    assert clear_opensak_cache_data(db_session) == 1
    for model in (Cache, Waypoint, Log, UserNote):
        assert db_session.query(model).count() == 0

    # …and the import afterwards creates, rather than updates
    result = import_gsak_db(gsak, db_session)
    assert result.created == 1 and result.updated == 0
