# tests/unit-tests/test_file_export_settings.py — saved settings for the
# GPX/LOC/GGZ file export dialog.

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from datetime import datetime

from opensak.export.file_export_settings import (
    FileExportProfile, FileExportSettings, expand_file_name,
)


# ── Settings / profile storage ────────────────────────────────────────────────

class TestFileExportSettings:
    def test_roundtrip(self):
        s = FileExportSettings(fmt="ggz", output_path="/x/y.ggz",
                               use_corrected_coords=False, max_records=250,
                               file_name="{database}_{date}", folder="/x",
                               if_exists="skip")
        assert FileExportSettings.from_dict(s.to_dict()) == s

    def test_missing_keys_fall_back_to_defaults(self):
        assert FileExportSettings.from_dict({}) == FileExportSettings()

    def test_invalid_values_fall_back_to_defaults(self):
        s = FileExportSettings.from_dict({"fmt": "kml", "output_path": 42})
        assert s == FileExportSettings()

    @pytest.mark.parametrize("value", ["yes", 1, None])
    def test_invalid_use_corrected_falls_back(self, value):
        s = FileExportSettings.from_dict({"use_corrected_coords": value})
        assert s.use_corrected_coords is True

    @pytest.mark.parametrize("value", [-1, "10", 2.5, True, None])
    def test_invalid_max_records_falls_back(self, value):
        assert FileExportSettings.from_dict({"max_records": value}).max_records == 0

    @pytest.mark.parametrize("value", [None, 5, ["x"]])
    def test_invalid_file_name_falls_back(self, value):
        assert FileExportSettings.from_dict({"file_name": value}).file_name == ""

    def test_folder_migrated_from_output_path(self, tmp_path):
        old = {"output_path": str(tmp_path / "sub" / "a.gpx")}
        assert FileExportSettings.from_dict(old).folder == str(tmp_path / "sub")
        assert FileExportSettings.from_dict({}).folder == ""

    def test_saved_folder_wins_over_output_path(self):
        s = FileExportSettings.from_dict({"output_path": "/a/b.gpx", "folder": ""})
        assert s.folder == ""

    @pytest.mark.parametrize("value", ["replace", None, 1])
    def test_invalid_if_exists_falls_back(self, value):
        assert FileExportSettings.from_dict({"if_exists": value}).if_exists == "ask"

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
        assert data == {"name": "A/B", "settings": {
            "fmt": "loc", "output_path": "",
            "use_corrected_coords": True, "max_records": 0, "file_name": "",
            "folder": "", "if_exists": "ask",
        }}


class TestExpandFileName:
    NOW = datetime(2026, 10, 3, 7, 8, 9)

    def _expand(self, template, **kw):
        kw.setdefault("now", self.NOW)
        return expand_file_name(template, **kw)

    def test_fixed_name_is_kept(self):
        assert self._expand("My caches") == "My caches"

    def test_date_time_variables(self):
        assert self._expand("{date}_{time}") == "2026-10-03_07-08-09"
        assert self._expand("{datetime}") == "2026-10-03_07-08-09"
        assert self._expand("{year}{month}{day}-{hour}{minute}{second}") == "20261003-070809"

    def test_database_format_count(self):
        name = self._expand("{database}-{format}-{count}", database="Zurich",
                            fmt="ggz", count=12)
        assert name == "Zurich-ggz-12"

    def test_filter_variable(self):
        assert self._expand("{database}_{filter}", database="DB",
                            filter_name="Tradis") == "DB_Tradis"
        assert self._expand("{filter}") == "opensak_export"   # no saved filter

    def test_variables_are_case_insensitive(self):
        assert self._expand("{Database}_{DATE}", database="DB") == "DB_2026-10-03"

    def test_unknown_variable_is_kept(self):
        assert self._expand("x_{nope}") == "x_{nope}"

    def test_invalid_characters_are_replaced(self):
        assert self._expand("a/b:c", database="") == "a_b_c"
        assert self._expand("{database}", database='x<y>|"z') == "x_y___z"

    def test_extension_is_dropped(self):
        assert self._expand("export.gpx", fmt="gpx") == "export"
        assert self._expand("export.GPX", fmt="gpx") == "export"
        assert self._expand("export.loc", fmt="gpx") == "export.loc"

    def test_empty_falls_back_to_default(self):
        assert self._expand("") == "opensak_export"
        assert self._expand("{database}", database="") == "opensak_export"
        assert self._expand(" .. ") == "opensak_export"

    def test_uses_current_time_by_default(self):
        assert expand_file_name("{year}") == datetime.now().strftime("%Y")


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

    def test_new_options_saved_and_applied(self, qtbot, fed, monkeypatch):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._chk_corrected.setChecked(False)
        dlg._spin_max.setValue(42)
        monkeypatch.setattr(fed.QInputDialog, "getText", lambda *a, **k: ("Opt", True))
        dlg._save_profile()
        saved = FileExportProfile.load(FileExportProfile.profile_path("Opt")).settings
        assert saved.use_corrected_coords is False
        assert saved.max_records == 42

        dlg._settings_combo.setCurrentIndex(0)   # "last used" (defaults)
        assert dlg._chk_corrected.isChecked() is True
        assert dlg._spin_max.value() == 0
        dlg._settings_combo.setCurrentIndex(1)
        assert dlg._chk_corrected.isChecked() is False
        assert dlg._spin_max.value() == 42

    def test_export_applies_max_records_and_coord_choice(self, qtbot, fed, monkeypatch, tmp_path):
        caches = [_cache() for _ in range(5)]
        caches.insert(0, SimpleNamespace(latitude=None, longitude=None))
        dlg = fed.FileExportDialog(caches)
        qtbot.addWidget(dlg)
        dlg._chk_corrected.setChecked(False)
        dlg._spin_max.setValue(3)
        dlg._edit_folder.setText(str(tmp_path))
        calls = []
        monkeypatch.setattr(fed, "_ExportWorker",
                            lambda *a, **k: calls.append((a, k)) or MagicMock())
        dlg._do_export()
        (args, kwargs), = calls
        assert args[0] == caches[1:4]
        assert kwargs["use_corrected"] is False
        last = FileExportProfile.load_last_used()
        assert (last.use_corrected_coords, last.max_records) == (False, 3)

    def test_file_name_and_folder_saved_and_applied(self, qtbot, fed, monkeypatch, tmp_path):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._edit_file_name.setText("{database}_{date}")
        dlg._edit_folder.setText(str(tmp_path))
        monkeypatch.setattr(fed.QInputDialog, "getText", lambda *a, **k: ("Named", True))
        dlg._save_profile()
        saved = FileExportProfile.load(FileExportProfile.profile_path("Named")).settings
        assert saved.file_name == "{database}_{date}"
        assert saved.folder == str(tmp_path)

        dlg._settings_combo.setCurrentIndex(0)   # "last used" (defaults)
        assert dlg._edit_file_name.text() == ""
        assert dlg._edit_folder.text() == ""
        dlg._settings_combo.setCurrentIndex(1)
        assert dlg._edit_file_name.text() == "{database}_{date}"
        assert dlg._edit_folder.text() == str(tmp_path)

    def test_preview_shows_target_path(self, qtbot, fed, tmp_path):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        assert dlg._output_preview() == "opensak_export.gpx"
        dlg._edit_folder.setText(str(tmp_path))
        dlg._edit_file_name.setText("mine")
        assert dlg._output_preview() == str(tmp_path / "mine.gpx")

    def test_long_preview_does_not_widen_dialog(self, qtbot, fed):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        width = dlg.sizeHint().width()
        dlg._edit_folder.setText("C:\\" + "very_long_folder_name\\" * 20)
        assert dlg.sizeHint().width() == width
        label = dlg._lbl_file_name_preview
        long_path = dlg._output_preview()
        assert long_path.endswith("opensak_export.gpx")
        label.set_full_text(long_path)
        label.resize(200, label.height())
        assert "…" in label.text()
        assert label.toolTip() == long_path

    def test_file_name_help_button(self, qtbot, fed, monkeypatch):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        shown = []
        monkeypatch.setattr(fed.QMessageBox, "information",
                            lambda *a, **k: shown.append(a))
        dlg._btn_name_help.click()
        assert len(shown) == 1

    def test_help_text_lists_every_variable(self):
        from opensak.export.file_export_settings import FILE_NAME_VARIABLES
        from opensak.lang.en import STRINGS
        help_text = STRINGS["file_export_file_name_help"]
        for var in FILE_NAME_VARIABLES:
            assert f"{{{var}}}" in help_text

    def test_browse_folder(self, qtbot, fed, monkeypatch, tmp_path):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        monkeypatch.setattr(fed.QFileDialog, "getExistingDirectory",
                            lambda *a, **k: str(tmp_path))
        assert dlg._browse_folder() is True
        assert dlg._edit_folder.text() == str(tmp_path)
        monkeypatch.setattr(fed.QFileDialog, "getExistingDirectory", lambda *a, **k: "")
        assert dlg._browse_folder() is False
        assert dlg._edit_folder.text() == str(tmp_path)   # cancel keeps it

    def test_preview_follows_template_and_format(self, qtbot, fed, monkeypatch):
        monkeypatch.setattr(fed.FileExportDialog, "_database_name",
                            staticmethod(lambda: "Home"))
        dlg = fed.FileExportDialog([_cache(), _cache()], filter_name="Tradis")
        qtbot.addWidget(dlg)
        dlg._edit_file_name.setText("{database}-{filter}")
        assert dlg._expanded_file_name() == "Home-Tradis.gpx"
        dlg._edit_file_name.setText("{database}-{count}")
        assert dlg._expanded_file_name() == "Home-2.gpx"
        dlg._btn_ggz.setChecked(True)
        assert dlg._expanded_file_name() == "Home-2.ggz"
        dlg._spin_max.setValue(1)
        assert dlg._expanded_file_name() == "Home-1.ggz"

    def test_export_writes_to_folder_without_asking(
            self, qtbot, fed, monkeypatch, tmp_path):
        FileExportProfile.save_last_used(FileExportSettings(
            folder=str(tmp_path), file_name="{database}_fixed",
        ))
        monkeypatch.setattr(fed.FileExportDialog, "_database_name",
                            staticmethod(lambda: "Home"))
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)

        def no_dialog(*a, **k):
            raise AssertionError("no file dialog expected")

        monkeypatch.setattr(fed.QFileDialog, "getSaveFileName", no_dialog)
        monkeypatch.setattr(fed.QFileDialog, "getExistingDirectory", no_dialog)
        paths = []
        monkeypatch.setattr(fed, "_ExportWorker",
                            lambda *a, **k: paths.append(a[1]) or MagicMock())
        dlg._do_export()
        assert paths == [tmp_path / "Home_fixed.gpx"]
        last = FileExportProfile.load_last_used()
        assert last.file_name == "{database}_fixed"
        assert last.output_path == str(tmp_path / "Home_fixed.gpx")

    def test_if_exists_saved_and_applied(self, qtbot, fed, monkeypatch):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        assert dlg._combo_if_exists.currentData() == "ask"
        dlg._combo_if_exists.setCurrentIndex(dlg._combo_if_exists.findData("skip"))
        monkeypatch.setattr(fed.QInputDialog, "getText", lambda *a, **k: ("S", True))
        dlg._save_profile()
        saved = FileExportProfile.load(FileExportProfile.profile_path("S")).settings
        assert saved.if_exists == "skip"
        dlg._settings_combo.setCurrentIndex(0)   # "last used" (defaults)
        assert dlg._combo_if_exists.currentData() == "ask"
        dlg._settings_combo.setCurrentIndex(1)
        assert dlg._combo_if_exists.currentData() == "skip"

    @pytest.mark.parametrize("if_exists,answer,exported", [
        ("overwrite", None, True),
        ("skip", None, False),
        ("ask", "Yes", True),
        ("ask", "No", False),
    ])
    def test_existing_file(self, qtbot, fed, monkeypatch, tmp_path,
                           if_exists, answer, exported):
        (tmp_path / "same.gpx").write_text("old", encoding="utf-8")
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._edit_folder.setText(str(tmp_path))
        dlg._edit_file_name.setText("same")
        dlg._combo_if_exists.setCurrentIndex(dlg._combo_if_exists.findData(if_exists))
        asked = []

        def fake_question(*a, **k):
            asked.append(a)
            return getattr(fed.QMessageBox.StandardButton, answer)

        monkeypatch.setattr(fed.QMessageBox, "question", fake_question)
        paths = []
        monkeypatch.setattr(fed, "_ExportWorker",
                            lambda *a, **k: paths.append(a[1]) or MagicMock())
        dlg._do_export()
        assert bool(paths) is exported
        assert bool(asked) is (if_exists == "ask")
        if if_exists == "skip":
            assert not dlg._log.isHidden()   # tells the user it was skipped

    def test_database_name_from_active_db(self, fed, monkeypatch):
        from opensak.db import manager
        fake = SimpleNamespace(active=SimpleNamespace(name="Active DB"))
        monkeypatch.setattr(manager, "get_db_manager", lambda: fake)
        assert fed.FileExportDialog._database_name() == "Active DB"
        monkeypatch.setattr(manager, "get_db_manager",
                            lambda: SimpleNamespace(active=None))
        assert fed.FileExportDialog._database_name() == ""

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

    def test_first_export_asks_for_folder_once(self, qtbot, fed, monkeypatch, tmp_path):
        dlg = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg)
        dlg._btn_loc.setChecked(True)
        target = tmp_path / "out"
        asked = []
        monkeypatch.setattr(fed.QFileDialog, "getExistingDirectory",
                            lambda *a, **k: asked.append(a) or str(target))
        paths = []
        monkeypatch.setattr(fed, "_ExportWorker",
                            lambda *a, **k: paths.append(a[1]) or MagicMock())
        dlg._do_export()

        last = FileExportProfile.load_last_used()
        assert last.fmt == "loc"
        assert last.folder == str(target)
        assert last.output_path == str(target / "opensak_export.loc")

        dlg2 = fed.FileExportDialog([_cache()])
        qtbot.addWidget(dlg2)
        dlg2._btn_gpx.setChecked(True)
        dlg2._do_export()
        assert len(asked) == 1
        assert paths == [target / "opensak_export.loc", target / "opensak_export.gpx"]
