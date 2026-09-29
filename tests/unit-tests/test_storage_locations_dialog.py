# tests/unit-tests/test_storage_locations_dialog.py — Help → "OpenSAK File
# Locations…" (issue #907).
#
# Isolation: den autouse _isolated_app_paths-fixture i tests/conftest.py
# patcher Path.home(), APPDATA/XDG_* og paths.qsettings_location() til en
# tmp-mappe pr. test.

from __future__ import annotations

from pathlib import Path

import pytest

from opensak.gui.dialogs import storage_locations_dialog as sld
from opensak.lang import load_language, tr
from opensak.paths import LocationKind, StorageLocation, get_all_storage_locations
from opensak.settings_store import get_install_dir, get_store


@pytest.fixture(autouse=True)
def _language():
    load_language("en")


@pytest.fixture
def dialog(qtbot):
    d = sld.StorageLocationsDialog()
    qtbot.addWidget(d)
    return d


def _row_for(d: sld.StorageLocationsDialog, kind: LocationKind) -> int:
    for row, loc in enumerate(d._locations):
        if loc.kind is kind:
            return row
    raise AssertionError(f"no row for {kind}")


# ── Hjælpefunktioner ─────────────────────────────────────────────────────────

def test_every_location_kind_has_a_description():
    assert set(sld._KIND_KEYS) == set(LocationKind)


@pytest.mark.parametrize("size, expected", [
    (None, "—"),
    (0, "0 B"),
    (1023, "1023 B"),
    (1024, "1.0 KB"),
    (5 * 1024 * 1024, "5.0 MB"),
    (3 * 1024 ** 3, "3.0 GB"),
    (2048 * 1024 ** 3, "2048.0 GB"),
])
def test_format_size(size, expected):
    assert sld.format_size(size) == expected


def test_path_size_sums_directory_recursively(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "sub" / "b.bin").write_bytes(b"x" * 50)

    assert sld.path_size(tmp_path) == 150
    assert sld.path_size(tmp_path / "a.bin") == 100
    assert sld.path_size(tmp_path / "missing") is None


def test_keyring_entry_shows_service_and_username():
    loc = StorageLocation(LocationKind.KEYRING_ENTRY, "cacher@example.com", is_path=False)

    assert sld.display_location(loc) == "OpenSAK PQ Email / cacher@example.com"
    assert sld.exists_text(loc) == "—"
    assert sld.folder_to_open(loc) is None


def test_folder_to_open_uses_parent_for_files(tmp_path):
    f = tmp_path / "OpenSAK.conf"
    f.write_text("x", encoding="utf-8")

    assert sld.folder_to_open(StorageLocation(LocationKind.QT_SETTINGS, str(f))) == tmp_path
    assert sld.folder_to_open(StorageLocation(LocationKind.INSTALL_DIR, str(tmp_path))) == tmp_path


def test_folder_to_open_none_when_nothing_exists(tmp_path):
    missing = tmp_path / "gone" / "file.db"

    assert sld.folder_to_open(StorageLocation(LocationKind.EXTERNAL_DATABASE, str(missing))) is None


def test_build_report_lists_every_location():
    get_store().set("pq_email.username", "cacher@example.com")
    locations = get_all_storage_locations()

    report = sld.build_report(locations)

    assert report.startswith(tr("file_locations_title"))
    for loc in locations:
        assert sld.display_location(loc) in report
        assert sld.describe(loc) in report


# ── Dialog ───────────────────────────────────────────────────────────────────

def test_dialog_has_one_row_per_location(dialog):
    assert dialog._table.rowCount() == len(get_all_storage_locations())


def test_install_dir_row_contents(dialog):
    row = _row_for(dialog, LocationKind.INSTALL_DIR)
    t = dialog._table

    assert t.item(row, dialog.COL_CONTENTS).text() == tr("file_locations_kind_install_dir")
    assert t.item(row, dialog.COL_LOCATION).text() == str(get_install_dir())
    assert t.item(row, dialog.COL_EXISTS).text() == tr("yes")
    assert t.cellWidget(row, dialog.COL_OPEN) is not None


def test_missing_external_database_has_no_open_button(qtbot, tmp_path):
    missing = tmp_path / "usb-drive" / "Challenges.db"
    get_store().set("databases.list", [{"name": "Challenges", "path": str(missing)}])

    d = sld.StorageLocationsDialog()
    qtbot.addWidget(d)
    row = _row_for(d, LocationKind.EXTERNAL_DATABASE)

    assert d._table.item(row, d.COL_EXISTS).text() == tr("no")
    assert d._table.cellWidget(row, d.COL_OPEN) is None


def test_open_folder_button_opens_the_folder(dialog, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(sld.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))

    row = _row_for(dialog, LocationKind.INSTALL_DIR)
    dialog._table.cellWidget(row, dialog.COL_OPEN).click()

    assert [Path(p) for p in opened] == [get_install_dir()]


def test_copy_all_puts_report_on_clipboard(dialog):
    from PySide6.QtWidgets import QApplication

    dialog._copy_btn.click()

    assert QApplication.clipboard().text() == sld.build_report(dialog._locations)
