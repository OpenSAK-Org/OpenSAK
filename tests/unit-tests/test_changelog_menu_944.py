# tests/unit-tests/test_changelog_menu_944.py — issue #944.
#
# Help → "What's New (Changelog)" opens CHANGELOG.md on GitHub. Stable
# installs get the `main` copy; beta installs get the `beta` copy, since
# beta entries aren't on `main` until the release goes stable.

from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from opensak.updater import changelog_url_for_version
from opensak.gui.mainwindow import MainWindow

MAIN_URL = "https://github.com/OpenSAK-Org/opensak/blob/main/CHANGELOG.md"
BETA_URL = "https://github.com/OpenSAK-Org/opensak/blob/beta/CHANGELOG.md"


class TestChangelogUrlForVersion:
    @pytest.mark.parametrize("version", ["1.20.0", "v1.20.0", "1.18.1"])
    def test_stable_versions_point_at_main(self, version):
        assert changelog_url_for_version(version) == MAIN_URL

    @pytest.mark.parametrize(
        "version", ["1.21.0-beta.1", "v1.21.0-beta.12", "1.21.0-rc.1", "1.21.0-alpha.2"]
    )
    def test_prerelease_versions_point_at_beta(self, version):
        assert changelog_url_for_version(version) == BETA_URL


@pytest.fixture(autouse=True)
def _quiet_startup(monkeypatch):
    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from opensak.lang import load_language
    from tests.data import make_fake_manager

    load_language("en")

    db_path = tmp_path / "test_changelog_menu_944.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="ChangelogTest"))

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)

    yield win

    win.close()
    mgr_module._manager = None


def _help_menu_texts(win):
    from opensak.lang import tr
    for menu_action in win.menuBar().actions():
        menu = menu_action.menu()
        if menu is not None and menu.title() == tr("menu_help"):
            return [a.text() for a in menu.actions()]
    return []


class TestChangelogMenuAction:
    def test_help_menu_contains_changelog_entry(self, window):
        assert "What's New (Changelog)" in _help_menu_texts(window)

    def test_opens_main_changelog_on_stable(self, window):
        with patch("opensak.__version__", "1.20.0"):
            with patch("PySide6.QtGui.QDesktopServices.openUrl") as mock_open:
                window._open_changelog()
        assert mock_open.call_args[0][0].toString() == MAIN_URL

    def test_opens_beta_changelog_on_beta(self, window):
        with patch("opensak.__version__", "1.21.0-beta.1"):
            with patch("PySide6.QtGui.QDesktopServices.openUrl") as mock_open:
                window._open_changelog()
        assert mock_open.call_args[0][0].toString() == BETA_URL
