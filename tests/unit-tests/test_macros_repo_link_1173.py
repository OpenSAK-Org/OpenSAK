# tests/unit-tests/test_macros_repo_link_1173.py — issue #1173.
#
# Macros → Find more macros… and the empty Macro manager point to the shared
# macro repository, OpenSAK-Macros. The URL lives in one constant.

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.mainwindow import MainWindow
from opensak.lang import load_language, tr
from opensak.macro.metadata import MACROS_REPO_URL

REPO = "https://github.com/OpenSAK-Org/OpenSAK-Macros"


@pytest.fixture(autouse=True)
def _english():
    load_language("en")


@pytest.fixture(autouse=True)
def _quiet_startup(monkeypatch):
    monkeypatch.setattr(MainWindow, "_initial_load", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_update_background", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_setup_complete", lambda self: None)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    import opensak.db.manager as mgr_module
    from opensak.db.database import init_db
    from tests.data import make_fake_manager

    db_path = tmp_path / "test_macros_repo_link_1173.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="RepoLink"))

    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    win.close()
    mgr_module._manager = None


def _macros_menu(win):
    for menu_action in win.menuBar().actions():
        menu = menu_action.menu()
        if menu is not None and menu.title() == tr("menu_macros"):
            return menu
    raise AssertionError("no Macros menu")


def test_url_constant():
    assert MACROS_REPO_URL == REPO


def test_macros_menu_has_find_more_macros(window):
    texts = [a.text() for a in _macros_menu(window).actions()]
    assert texts[-1] == tr("action_find_more_macros")


def test_find_more_macros_opens_the_repository(window):
    with patch("PySide6.QtGui.QDesktopServices.openUrl") as mock_open:
        window._act_find_macros.trigger()
    assert mock_open.call_args[0][0].toString() == REPO


def test_empty_macro_manager_links_to_the_repository(qtbot):
    from PySide6.QtCore import Qt

    from opensak.gui.dialogs.macro_manager import MacroManagerDialog, _empty_text

    text = _empty_text()
    assert f'<a href="{REPO}">OpenSAK-Macros</a>' in text
    assert "<br>" in text and "\n" not in text

    dialog = MacroManagerDialog(host=MagicMock())
    qtbot.addWidget(dialog)
    assert dialog._empty.textFormat() == Qt.TextFormat.RichText
    assert dialog._empty.openExternalLinks()
    assert REPO in dialog._empty.text()


@pytest.mark.parametrize(
    "lang", ["da", "en", "fr", "nl", "pt", "cs", "se", "de", "de_CH", "pl", "es"]
)
def test_every_language_keeps_the_link(lang):
    from opensak.gui.dialogs.macro_manager import _empty_text

    load_language(lang)
    assert f'<a href="{REPO}">OpenSAK-Macros</a>' in _empty_text()
