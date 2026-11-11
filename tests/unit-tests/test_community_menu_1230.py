# tests/unit-tests/test_community_menu_1230.py — issue #1230.
#
# Help → Community lists where other users are: the Facebook group, GitHub
# Discussions, the OpenSAK thread on the GSAK forum and the issue tracker.
# The URLs are kept in one place, opensak/utils/community.py.

from unittest.mock import patch

import pytest

from opensak.utils.community import COMMUNITY_LINKS


def test_links_are_https_and_distinct():
    urls = [url for _key, url in COMMUNITY_LINKS]
    assert len(urls) == 4
    assert len(set(urls)) == len(urls)
    assert all(url.startswith("https://") for url in urls)


def test_link_keys_exist_in_every_language():
    from opensak.lang import AVAILABLE_LANGUAGES
    import importlib
    for code in AVAILABLE_LANGUAGES:
        strings = importlib.import_module(f"opensak.lang.{code}").STRINGS
        for key, _url in COMMUNITY_LINKS:
            assert strings.get(key), f"{code}: {key}"
        assert strings.get("menu_community"), code


pytest.importorskip("pytestqt")

from opensak.gui.mainwindow import MainWindow  # noqa: E402


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

    db_path = tmp_path / "test_community_menu_1230.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="CommunityTest"))

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)

    yield win

    win.close()
    mgr_module._manager = None


def _help_menu(win):
    # findChildren() asks Qt for the menus that exist. Going through
    # menuBar().actions()[i].menu() can hand back a stale wrapper on some
    # PySide6 versions ("Internal C++ object already deleted").
    from PySide6.QtWidgets import QMenu
    from opensak.lang import tr
    for menu in win.findChildren(QMenu):
        if menu.title() == tr("menu_help"):
            return menu
    return None


def test_help_menu_has_community_submenu(window):
    from opensak.lang import tr
    menu = window._community_menu
    assert menu.title() == tr("menu_community")
    help_menu = _help_menu(window)
    assert help_menu is not None
    assert menu.menuAction() in help_menu.actions()
    assert [a.text() for a in menu.actions()] == [tr(key) for key, _url in COMMUNITY_LINKS]


@pytest.mark.parametrize("index", range(len(COMMUNITY_LINKS)))
def test_each_item_opens_its_url(window, index):
    menu = window._community_menu
    with patch("PySide6.QtGui.QDesktopServices.openUrl") as mock_open:
        menu.actions()[index].trigger()
    assert mock_open.call_args[0][0].toString() == COMMUNITY_LINKS[index][1]
