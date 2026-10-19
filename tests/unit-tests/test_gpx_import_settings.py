# tests/unit-tests/test_gpx_import_settings.py — saved settings for the
# GPX / PQ zip import dialog.

import pytest

from opensak.importer.gpx_import_settings import GpxImportProfile, GpxImportSettings


class TestGpxImportSettings:
    def test_roundtrip(self):
        s = GpxImportSettings(database="/db/a.db", files=["/x/a.gpx", "/x/b.zip"])
        assert GpxImportSettings.from_dict(s.to_dict()) == s

    def test_missing_keys_fall_back_to_defaults(self):
        assert GpxImportSettings.from_dict({}) == GpxImportSettings()

    @pytest.mark.parametrize("value", [None, 5, ["x"]])
    def test_invalid_database_falls_back(self, value):
        assert GpxImportSettings.from_dict({"database": value}).database == ""

    @pytest.mark.parametrize("value", [None, "a.gpx", {"a": 1}])
    def test_invalid_files_fall_back(self, value):
        assert GpxImportSettings.from_dict({"files": value}).files == []

    def test_invalid_file_entries_are_dropped(self):
        s = GpxImportSettings.from_dict({"files": ["/a.gpx", 3, "", None, "/b.zip"]})
        assert s.files == ["/a.gpx", "/b.zip"]


class TestGpxImportProfile:
    def test_save_load_list(self, tmp_path):
        path = GpxImportProfile("Nightly", GpxImportSettings(files=["/a.gpx"])).save(tmp_path)
        assert GpxImportProfile.list_profiles(tmp_path) == [path]
        loaded = GpxImportProfile.load(path)
        assert loaded.name == "Nightly" and loaded.settings.files == ["/a.gpx"]

    def test_last_used_roundtrip_and_not_listed(self, tmp_path):
        assert GpxImportProfile.load_last_used(tmp_path) == GpxImportSettings()
        GpxImportProfile.save_last_used(GpxImportSettings(database="/d.db"), tmp_path)
        assert GpxImportProfile.load_last_used(tmp_path).database == "/d.db"
        assert GpxImportProfile.list_profiles(tmp_path) == []

    def test_stored_apart_from_export_settings(self):
        from opensak.export.file_export_settings import FileExportProfile
        assert GpxImportProfile.default_dir() != FileExportProfile.default_dir()
        assert GpxImportProfile.default_dir().name == "import_settings"


class TestGpxImportSettingsFolders:
    def test_folders_roundtrip(self):
        s = GpxImportSettings(folders=["/pq", "/more"])
        assert GpxImportSettings.from_dict(s.to_dict()).folders == ["/pq", "/more"]

    def test_settings_without_folders_still_load(self):
        assert GpxImportSettings.from_dict({"files": ["/a.gpx"]}).folders == []

    @pytest.mark.parametrize("value", [None, "/pq", 3])
    def test_invalid_folders_fall_back(self, value):
        assert GpxImportSettings.from_dict({"folders": value}).folders == []
