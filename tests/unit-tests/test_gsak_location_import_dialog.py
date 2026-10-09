# tests/unit-tests/test_gsak_location_import_dialog.py — preview of GSAK user locations (#1001).

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak.gui.dialogs import gsak_location_import_dialog as ldlg
from opensak.gui.dialogs.gsak_location_import_dialog import GsakLocationImportDialog
from opensak.importer.gsak_location_importer import parse_gsak_locations

TEXT = "Zurich,47.371722, 8.537466\nBroken, nonsense\nParis,N48° 51.767 E2° 19.886"


class FakeBox:
    """Stand-in for QMessageBox that 'clicks' the button labelled ``choice``."""
    choice = None
    instances: list = []
    Icon = ldlg.QMessageBox.Icon
    ButtonRole = ldlg.QMessageBox.ButtonRole
    StandardButton = ldlg.QMessageBox.StandardButton

    def __init__(self, parent=None):
        self.buttons = {}
        self.text = ""
        FakeBox.instances.append(self)

    def setIcon(self, _i): pass
    def setWindowTitle(self, _t): pass
    def setText(self, t): self.text = t
    def setDefaultButton(self, _b): pass
    def exec(self): pass

    def addButton(self, label, role=None):
        btn = SimpleNamespace(label=label)
        self.buttons[label if isinstance(label, str) else "cancel"] = btn
        return btn

    def clickedButton(self):
        return self.buttons.get(FakeBox.choice)


@pytest.fixture
def fake_box(monkeypatch):
    FakeBox.instances = []
    FakeBox.choice = None
    monkeypatch.setattr(ldlg, "QMessageBox", FakeBox)
    return FakeBox


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(
        "opensak.db.manager.get_db_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("no manager")),
    )
    from opensak.gui.settings import get_settings
    return get_settings()


def _dialog(qtbot, text=TEXT):
    dlg = GsakLocationImportDialog(parse_gsak_locations(text))
    qtbot.addWidget(dlg)
    return dlg


def _names(dlg):
    return [loc.name for loc in dlg.checked_locations()]


class TestPreview:
    def test_valid_rows_ticked_invalid_rows_flagged(self, qtbot, settings):
        dlg = _dialog(qtbot)
        assert dlg._table.rowCount() == 3
        assert _names(dlg) == ["Zurich", "Paris"]
        broken = dlg._table.item(1, ldlg.COL_NAME)
        assert not broken.flags() & Qt.ItemFlag.ItemIsUserCheckable
        status = dlg._table.item(1, ldlg.COL_STATUS).text()
        assert ldlg.tr("gsak_location_import_error_bad_coord") in status
        assert dlg._table.item(1, ldlg.COL_LINE).text() == "2"
        assert dlg._table.item(0, ldlg.COL_STATUS).text() == ldlg.tr(
            "gsak_location_import_status_new")
        assert dlg._import_btn.isEnabled()

    def test_existing_name_is_marked(self, qtbot, settings):
        from opensak.gui.settings import HomePoint
        settings.home_points = [HomePoint("Paris", 1.0, 2.0)]
        dlg = _dialog(qtbot)
        assert dlg._table.item(2, ldlg.COL_STATUS).text() == ldlg.tr("file_locations_col_exists")

    def test_select_none_disables_import_and_skips_invalid(self, qtbot, settings):
        dlg = _dialog(qtbot)
        dlg._check_all(False)
        assert _names(dlg) == [] and not dlg._import_btn.isEnabled()
        dlg._check_all(True)
        assert _names(dlg) == ["Zurich", "Paris"]

    def test_unticked_row_is_not_imported(self, qtbot, settings, fake_box):
        dlg = _dialog(qtbot)
        dlg._table.item(0, ldlg.COL_NAME).setCheckState(Qt.CheckState.Unchecked)
        dlg._import()
        assert dlg.result_data.added == ["Paris"]
        assert [p.name for p in settings.home_points] == ["Paris"]
        assert fake_box.instances == []          # nothing existed — no question


class TestImportExisting:
    @pytest.fixture
    def existing(self, settings):
        from opensak.gui.settings import HomePoint
        settings.home_points = [HomePoint("Zurich", 1.0, 2.0)]
        return settings

    def test_overwrite(self, qtbot, existing, fake_box, monkeypatch):
        monkeypatch.setattr(ldlg, "tr", lambda key, **kw: f"{key} {kw}" if kw else key)
        fake_box.choice = ldlg.tr("gsak_import_existing_overwrite")
        dlg = _dialog(qtbot)
        dlg._import()
        assert "Zurich" in fake_box.instances[0].text
        assert dlg.result_data.updated == ["Zurich"]
        assert existing.home_points[0].lat == pytest.approx(47.371722)

    def test_skip(self, qtbot, existing, fake_box):
        fake_box.choice = ldlg.tr("gsak_import_existing_skip")
        dlg = _dialog(qtbot)
        dlg._import()
        assert dlg.result_data.skipped == ["Zurich"]
        assert dlg.result_data.added == ["Paris"]
        assert existing.home_points[0].lat == 1.0

    def test_cancel_writes_nothing_and_keeps_dialog_open(self, qtbot, existing, fake_box):
        fake_box.choice = "cancel"
        dlg = _dialog(qtbot)
        dlg._import()
        assert dlg.result_data is None
        assert [p.name for p in existing.home_points] == ["Zurich"]
