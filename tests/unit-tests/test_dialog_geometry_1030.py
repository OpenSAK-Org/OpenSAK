# tests/unit-tests/test_dialog_geometry_1030.py — remembered dialog size/position (#1030).
#
# The offscreen QPA platform used in CI gives a single 800x800 screen at
# (0, 0), so "a screen that is no longer connected" is simply any point
# outside that rectangle.

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QDialog

from opensak.gui.dialogs import widgets as w
from opensak.settings_store import get_store

KEY = "test"
STORE_KEY = "dialog_geometry." + KEY


@pytest.fixture
def avail():
    return QApplication.primaryScreen().availableGeometry()


def _make(qtbot, key=KEY):
    dlg = QDialog()
    qtbot.addWidget(dlg)
    w.remember_dialog_geometry(dlg, key)
    return dlg


class TestRestore:
    def test_nothing_saved_leaves_dialog_alone(self, qtbot):
        plain = QDialog()
        qtbot.addWidget(plain)
        dlg = _make(qtbot)
        assert dlg.size() == plain.size()
        assert not dlg.isMaximized()

    def test_restores_size_and_position(self, qtbot, avail):
        get_store().set(STORE_KEY, {"x": avail.left() + 50, "y": avail.top() + 60,
                                    "w": 400, "h": 300, "maximized": False})
        dlg = _make(qtbot)
        assert (dlg.width(), dlg.height()) == (400, 300)
        assert (dlg.x(), dlg.y()) == (avail.left() + 50, avail.top() + 60)

    def test_oversized_is_capped_to_screen(self, qtbot, avail):
        get_store().set(STORE_KEY, {"x": avail.left(), "y": avail.top(),
                                    "w": avail.width() * 3, "h": avail.height() * 3})
        dlg = _make(qtbot)
        assert dlg.width() <= avail.width()
        assert dlg.height() <= avail.height()

    def test_position_pulled_back_inside_screen(self, qtbot, avail):
        # Centre is on the screen, but the window hangs off its right/bottom edge.
        get_store().set(STORE_KEY, {"x": avail.right() - 250, "y": avail.bottom() - 200,
                                    "w": 400, "h": 300})
        dlg = _make(qtbot)
        assert dlg.x() + dlg.width() <= avail.right() + 1
        assert dlg.y() + dlg.height() <= avail.bottom() + 1
        assert dlg.x() >= avail.left() and dlg.y() >= avail.top()

    def test_disconnected_screen_keeps_size_drops_position(self, qtbot, avail):
        # Remembered on a monitor to the right that isn't connected any more.
        far_x = avail.right() + 2000
        get_store().set(STORE_KEY, {"x": far_x, "y": 100, "w": 400, "h": 300})
        dlg = _make(qtbot)
        assert (dlg.width(), dlg.height()) == (400, 300)
        assert dlg.x() < far_x  # not moved off-screen

    def test_respects_existing_maximum_height(self, qtbot, avail):
        get_store().set(STORE_KEY, {"x": avail.left(), "y": avail.top(),
                                    "w": 300, "h": avail.height()})
        dlg = QDialog()
        qtbot.addWidget(dlg)
        dlg.setMaximumHeight(200)
        w.remember_dialog_geometry(dlg, KEY)
        assert dlg.height() == 200

    def test_restores_maximized(self, qtbot):
        get_store().set(STORE_KEY, {"w": 400, "h": 300, "maximized": True})
        dlg = _make(qtbot)
        assert dlg.windowState() & w.Qt.WindowState.WindowMaximized

    @pytest.mark.parametrize("garbage", [
        "not a dict", 42, None, [], {}, {"w": "big", "h": None},
        {"x": "left", "y": 0, "w": 400, "h": 300}, {"w": -5, "h": 0},
    ])
    def test_garbage_is_ignored(self, qtbot, garbage):
        get_store().set(STORE_KEY, garbage)
        _make(qtbot)  # must not raise


class TestSave:
    def test_close_saves_geometry(self, qtbot, avail):
        dlg = _make(qtbot)
        dlg.resize(420, 310)
        dlg.move(avail.left() + 30, avail.top() + 40)
        dlg.show()
        qtbot.waitExposed(dlg)
        dlg.reject()
        saved = get_store().get(STORE_KEY)
        assert saved["w"] == 420 and saved["h"] == 310
        assert saved["maximized"] is False
        assert {"x", "y"} <= saved.keys()

    def test_accept_also_saves(self, qtbot):
        dlg = _make(qtbot)
        dlg.resize(333, 222)
        dlg.accept()
        assert get_store().get(STORE_KEY)["w"] == 333

    def test_maximized_keeps_last_normal_geometry(self, qtbot):
        normal = {"x": 10, "y": 20, "w": 400, "h": 300, "maximized": False}
        get_store().set(STORE_KEY, normal)
        dlg = _make(qtbot)
        dlg.showMaximized()
        qtbot.waitUntil(dlg.isMaximized)
        dlg.reject()
        assert get_store().get(STORE_KEY) == {**normal, "maximized": True}

    def test_round_trip(self, qtbot, avail):
        dlg = _make(qtbot)
        dlg.resize(450, 350)
        dlg.move(avail.left() + 20, avail.top() + 25)
        dlg.reject()
        again = _make(qtbot)
        assert (again.width(), again.height()) == (450, 350)

    def test_keys_are_independent(self, qtbot):
        a = _make(qtbot, "a")
        a.resize(301, 201)
        a.reject()
        assert get_store().get("dialog_geometry.b") is None


class TestSettingsDialog:
    """SettingsDialog is the first dialog wired up (#1030)."""

    @pytest.fixture
    def settings(self, monkeypatch):
        from opensak.gui.dialogs import settings_dialog as sd
        from opensak.gui.settings import AppSettings
        s = AppSettings()
        monkeypatch.setattr(sd, "get_settings", lambda: s)
        monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
        monkeypatch.setattr(
            "opensak.db.manager.get_db_manager",
            lambda: (_ for _ in ()).throw(RuntimeError("no manager in test")),
        )
        monkeypatch.setattr("opensak.api.geocaching.is_logged_in", lambda: False)
        return s

    def test_settings_dialog_remembers_size(self, qtbot, settings):
        from opensak.gui.dialogs.settings_dialog import SettingsDialog
        d = SettingsDialog()
        qtbot.addWidget(d)
        d.resize(700, 500)
        d.reject()
        assert get_store().get("dialog_geometry.settings")["w"] == 700

        d2 = SettingsDialog()
        qtbot.addWidget(d2)
        assert (d2.width(), d2.height()) == (700, 500)
