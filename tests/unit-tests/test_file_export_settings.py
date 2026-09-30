# tests/unit-tests/test_file_export_settings.py — saved settings for the
# GPX/LOC/GGZ file export dialog.

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from opensak.export.file_export_settings import (
    FileExportProfile, FileExportSettings,
)


# ── Settings / profile storage ────────────────────────────────────────────────

class TestFileExportSettings:
    def test_roundtrip(self):
        s = FileExportSettings(fmt="ggz", output_path="/x/y.ggz")
        assert FileExportSettings.from_dict(s.to_dict()) == s

    def test_missing_keys_fall_back_to_defaults(self):
        assert FileExportSettings.from_dict({}) == FileExportSettings()

    def test_invalid_values_fall_back_to_defaults(self):
        s = FileExportSettings.from_dict({"fmt": "kml", "output_path": 42})
        assert s == FileExportSettings()

    def test_unknown_keys_are_ignored(self):
        s = FileExportSettings.from_dict({"fmt": "loc", "from_the_future": True})
        assert s.fmt == "loc"


class TestFileExportProfile:
    def test_save_load_list(self, tmp_path):
        FileExportProfile("Garmin", FileExportSettings(fmt="ggz")).save(tmp_path)
        FileExportProfile("Phone", FileExportSettings(fmt="gpx")).save(tmp_path)
        paths = FileExportProfile.list_profiles(tmp_path)
        profiles = [FileExportProfile.load(p) for p in paths]
        assert [(p.name, p.settings.fmt) for p in profiles] == [
            ("Garmin", "ggz"), ("Phone", "gpx"),
        ]

    def test_list_empty_when_dir_missing(self, tmp_path):
        assert FileExportProfile.list_profiles(tmp_path / "nope") == []

    def test_last_used_roundtrip_and_hidden_from_list(self, tmp_path):
        s = FileExportSettings(fmt="loc", output_path="/a/b.loc")
        FileExportProfile.save_last_used(s, tmp_path)
        assert FileExportProfile.load_last_used(tmp_path) == s
        assert FileExportProfile.list_profiles(tmp_path) == []

    def test_last_used_defaults_when_missing_or_corrupt(self, tmp_path):
        assert FileExportProfile.load_last_used(tmp_path) == FileExportSettings()
        FileExportProfile.last_used_path(tmp_path).write_text("{", encoding="utf-8")
        assert FileExportProfile.load_last_used(tmp_path) == FileExportSettings()

    def test_user_name_cannot_clobber_last_used(self, tmp_path):
        reserved = FileExportProfile.last_used_path(tmp_path)
        assert FileExportProfile.profile_path("__last_used__", tmp_path) != reserved

    def test_default_dir_is_in_app_data(self):
        from opensak.config import get_app_data_dir
        assert FileExportProfile.default_dir() == get_app_data_dir() / "export_settings"

    def test_file_format(self, tmp_path):
        path = FileExportProfile("A/B", FileExportSettings(fmt="loc")).save(tmp_path)
        assert path.name == "A_B.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"name": "A/B", "settings": {"fmt": "loc", "output_path": ""}}


# ── Dialog integration ────────────────────────────────────────────────────────

pytestqt = pytest.importorskip("pytestqt")


@pytest.fixture
def fed():
    from opensak.gui.dialogs import file_export_dialog
    return file_export_dialog


def _cache():
    return SimpleNamespace(latitude=55.0, longitude=12.0)


def _combo_names(dlg):
    c = dlg._settings_combo
    return [c.itemText(i) for i in range(1, c.count())]


class TestFileExportDialogSettings:
    def test_opens_with_last_used(self, qtbot, fed):
        FileExportProfile.save_last_used(FileExportSettings(fmt="ggz"))
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        assert dlg._current_fmt() == "ggz"
        assert dlg._settings_combo.currentData() is None
        assert dlg._del_btn.isEnabled() is False

    def test_save_then_select_profile_applies_it(self, qtbot, fed, monkeypatch):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._btn_loc.setChecked(True)
        monkeypatch.setattr(fed.QInputDialog, "getText", lambda *a, **k: ("Loc", True))
        dlg._save_profile()
        assert _combo_names(dlg) == ["Loc"]
        assert dlg._settings_combo.currentText() == "Loc"
        assert dlg._del_btn.isEnabled() is True

        dlg._settings_combo.setCurrentIndex(0)   # back to "last used" (defaults)
        assert dlg._current_fmt() == "gpx"
        dlg._settings_combo.setCurrentIndex(1)
        assert dlg._current_fmt() == "loc"

    def test_overwrite_declined_keeps_existing(self, qtbot, fed, monkeypatch):
        FileExportProfile("P", FileExportSettings(fmt="ggz")).save()
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._btn_loc.setChecked(True)
        monkeypatch.setattr(fed.QInputDialog, "getText", lambda *a, **k: ("P", True))
        monkeypatch.setattr(fed.QMessageBox, "question",
                            lambda *a, **k: fed.QMessageBox.StandardButton.No)
        dlg._save_profile()
        path = FileExportProfile.profile_path("P")
        assert FileExportProfile.load(path).settings.fmt == "ggz"

    def test_delete_profile(self, qtbot, fed, monkeypatch):
        FileExportProfile("P", FileExportSettings(fmt="ggz")).save()
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._settings_combo.setCurrentIndex(1)
        monkeypatch.setattr(fed.QMessageBox, "question",
                            lambda *a, **k: fed.QMessageBox.StandardButton.Yes)
        dlg._delete_profile()
        assert _combo_names(dlg) == []
        assert not FileExportProfile.profile_path("P").exists()
        assert dlg._current_fmt() == "ggz"   # what is shown stays

    def test_export_records_last_used_and_prefills_path(self, qtbot, fed, monkeypatch, tmp_path):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._btn_loc.setChecked(True)
        target = tmp_path / "out" / "mine"
        seen_defaults = []

        def fake_save(parent, title, default, flt):
            seen_defaults.append(default)
            return str(target), flt

        monkeypatch.setattr(fed.QFileDialog, "getSaveFileName", fake_save)
        worker = MagicMock()
        monkeypatch.setattr(fed, "_ExportWorker", lambda *a, **k: worker)
        dlg._do_export()

        last = FileExportProfile.load_last_used()
        assert last.fmt == "loc"
        assert last.output_path == str(target.with_suffix(".loc"))

        dlg2 = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg2)
        dlg2._btn_gpx.setChecked(True)
        dlg2._do_export()
        assert seen_defaults[-1] == str(target.with_suffix(".gpx"))
