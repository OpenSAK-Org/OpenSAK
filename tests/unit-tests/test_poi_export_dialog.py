# tests/unit-tests/test_poi_export_dialog.py — the Garmin POI export dialog.

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import opensak.gui.dialogs.poi_export_dialog as ped
from opensak.export.gpi import GpiPoint
from opensak.export.poi_export import PoiFile, PoiGroup
from opensak.export.poi_export_settings import PoiExportProfile, PoiExportSettings
from opensak.gui.dialogs.poi_export_dialog import PoiExportDialog


def _cache():
    return SimpleNamespace(gc_code="GC1", latitude=47.0, longitude=8.0)


def _dialog(qtbot):
    dlg = PoiExportDialog([_cache()])
    qtbot.addWidget(dlg)
    return dlg


def _planned(folder: Path, *names: str) -> list[PoiFile]:
    return [PoiFile(folder / n, PoiGroup("Cat", points=[GpiPoint(47, 8, n)])) for n in names]


def test_settings_survive_the_dialog(qtbot):
    dlg = _dialog(qtbot)
    settings = PoiExportSettings(
        folder="/x", file_name="{database}", if_exists="skip", use_corrected_coords=False,
        include_waypoints=True, waypoint_flag="unflagged", split_by_type=True,
        max_points=7, name="{code}", smart_length=9, description="d", extra="e",
        extra_field="address", category="Cat", proximity=1.5, proximity_unit="km",
        icon="/i.bmp",
    )
    dlg._apply_settings(settings)
    assert dlg._collect_settings() == settings


def test_opens_with_last_used_settings(qtbot):
    PoiExportProfile.save_last_used(PoiExportSettings(category="Last"))
    assert _dialog(qtbot)._edit_category.text() == "Last"


def test_waypoint_options_follow_checkboxes(qtbot):
    dlg = _dialog(qtbot)
    dlg._chk_waypoints_only.setChecked(False)
    dlg._chk_waypoints.setChecked(False)
    assert not dlg._chk_split.isEnabled()
    assert not dlg._flag_buttons["all"].isEnabled()
    dlg._chk_waypoints_only.setChecked(True)
    assert not dlg._chk_waypoints.isEnabled()
    assert dlg._chk_split.isEnabled() and dlg._flag_buttons["flagged"].isEnabled()


def test_preview_shows_gpi_file(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(ped, "tr", lambda key, **kw: kw.get("name", key))
    dlg = _dialog(qtbot)
    dlg._edit_folder.setText(str(tmp_path))
    dlg._edit_file_name.setText("poi_{count}")
    dlg._chk_split.setChecked(False)
    assert dlg._lbl_file_name_preview.full_text().endswith(str(tmp_path / "poi_1.gpi"))
    dlg._chk_waypoints.setChecked(True)
    dlg._chk_split.setChecked(True)
    assert dlg._lbl_file_name_preview.full_text().endswith(
        "poi_1.gpi poi_export_preview_split")


def test_do_export_folder_cancel_does_nothing(qtbot, monkeypatch):
    dlg = _dialog(qtbot)
    dlg._edit_folder.setText("")
    monkeypatch.setattr(ped.QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    dlg._do_export()
    assert dlg._btn_export.isEnabled() is True


def test_do_export_starts_worker_and_saves_last_used(qtbot, monkeypatch, tmp_path):
    dlg = _dialog(qtbot)
    dlg._edit_folder.setText(str(tmp_path))
    dlg._edit_category.setText("Started")
    captured = {}

    class FakeWorker:
        def __init__(self, caches, settings, folder, *names):
            captured.update(caches=caches, folder=folder, settings=settings)
            self.planned = MagicMock()
            self.error = MagicMock()

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(ped, "_PlanWorker", FakeWorker)
    dlg._do_export()
    assert captured["started"] and captured["folder"] == tmp_path
    assert captured["settings"].category == "Started"
    assert PoiExportProfile.load_last_used().category == "Started"
    assert dlg._btn_export.isEnabled() is False


def test_write_writes_planned_files(qtbot, tmp_path):
    dlg = _dialog(qtbot)
    dlg._write(_planned(tmp_path, "a.gpi", "b.gpi"), PoiExportSettings())
    assert (tmp_path / "a.gpi").read_bytes()[8:16] == b"GRMREC00"
    assert (tmp_path / "b.gpi").exists()
    assert dlg._log.toPlainText().count("✓") == 2
    assert dlg._btn_export.isEnabled() is True


def test_write_asks_once_and_keeps_existing_files_on_no(qtbot, monkeypatch, tmp_path):
    dlg = _dialog(qtbot)
    (tmp_path / "a.gpi").write_bytes(b"old")
    question = MagicMock(return_value=ped.QMessageBox.StandardButton.No)
    monkeypatch.setattr(ped.QMessageBox, "question", question)
    dlg._write(_planned(tmp_path, "a.gpi", "b.gpi"), PoiExportSettings(if_exists="ask"))
    assert question.call_count == 1
    assert (tmp_path / "a.gpi").read_bytes() == b"old"
    assert (tmp_path / "b.gpi").exists()
    assert "–" in dlg._log.toPlainText() and "✓" in dlg._log.toPlainText()


def test_write_skip_leaves_existing_file(qtbot, tmp_path):
    dlg = _dialog(qtbot)
    (tmp_path / "a.gpi").write_bytes(b"old")
    dlg._write(_planned(tmp_path, "a.gpi"), PoiExportSettings(if_exists="skip"))
    assert (tmp_path / "a.gpi").read_bytes() == b"old"


def test_write_without_points_says_so(qtbot):
    dlg = _dialog(qtbot)
    dlg._write([], PoiExportSettings())
    assert dlg._log.toPlainText().startswith("–")


def test_write_reports_unreadable_icon(qtbot, monkeypatch, tmp_path):
    dlg = _dialog(qtbot)
    (tmp_path / "icon.bmp").write_text("no image")
    critical = MagicMock()
    monkeypatch.setattr(ped.QMessageBox, "critical", critical)
    dlg._write(_planned(tmp_path, "a.gpi"), PoiExportSettings(icon=str(tmp_path / "icon.bmp")))
    assert critical.call_count == 1
    assert not (tmp_path / "a.gpi").exists()
    assert dlg._log.toPlainText().startswith("✗")
