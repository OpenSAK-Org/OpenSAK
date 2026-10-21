"""tests/unit-tests/test_macro_center.py — opensak.center(), centers() and
set_center() (GSAK's Centre): the saved centre points, and changing the
active one only with the macro's permission to change the database."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from opensak.gui.settings import HomePoint, get_settings
from opensak.macro import MacroError, MacroRuntime, WriteApproval


@pytest.fixture(autouse=True)
def points():
    s = get_settings()
    s.gc_home_location = "N47 22.000 E008 32.000"
    s.home_points = [HomePoint("Zürich", 47.37, 8.54), HomePoint("Aarau", 47.39, 8.04)]
    s.set_active_home(HomePoint("Aarau", 47.39, 8.04))
    return s


class Host:
    def __init__(self, answer=WriteApproval.SESSION):
        self.answer = answer
        self.events: list = []

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def database_name(self):
        return "Default"

    def approve_macro_write(self, macro):
        self.events.append("write?")
        return self.answer

    def set_center(self, name, lat, lon):
        self.events.append((name, round(lat, 5), round(lon, 5)))
        get_settings().set_active_home(HomePoint(name, lat, lon))

    def end_macro(self):
        pass


def _run(source, host=None):
    out: list[str] = []
    host = host or Host()
    MacroRuntime(host, output=out.append).run(source)  # type: ignore[arg-type]
    return host, out


def test_center_is_the_active_point():
    _, out = _run("local c = opensak.center() print(c.name, c.lat, c.lon, c.saved)")
    assert out == ["Aarau\t47.39\t8.04\ttrue"]


def test_centers_lists_home_first_then_by_name():
    _, out = _run("for _, p in ipairs(opensak.centers()) do print(p.name, p.active) end")
    assert out == ["★ Home\tfalse", "Aarau\ttrue", "Zürich\tfalse"]


def test_set_center_to_a_saved_point():
    host, out = _run('local c = opensak.set_center("Zürich") print(c.name, c.saved)')
    assert host.events == ["write?", ("Zürich", 47.37, 8.54)]
    assert out == ["Zürich\ttrue"]


def test_set_center_to_home_uses_its_coordinates():
    host, _ = _run('opensak.set_center("★ Home")')
    assert host.events[-1] == ("★ Home", pytest.approx(47.36667), pytest.approx(8.53333))


@pytest.mark.parametrize("args, name", [
    ('47.5, 8.7, "Bonus"', "Bonus"),
    ('"N47 30.000 E008 42.000", "Bonus"', "Bonus"),
    ("47.5, 8.7", "📍 "),
    ('"N47 30.000 E008 42.000"', "📍 "),
])
def test_set_center_to_coordinates(args, name):
    host, out = _run(f"local c = opensak.set_center({args}) print(c.name, c.saved)")
    label, lat, lon = host.events[-1]
    assert label.startswith(name) and (lat, lon) == (47.5, 8.7)
    assert out[0].endswith("\tfalse")
    # Coordinates are not saved as a user location.
    assert [p.name for p in get_settings().home_points] == ["★ Home", "Zürich", "Aarau"]


@pytest.mark.parametrize("call, message", [
    ('opensak.set_center("Nowhere")', "neither a saved centre point"),
    ("opensak.set_center()", "expects the name of a saved centre point"),
    ("opensak.set_center(95, 8)", "out of range"),
    ('opensak.set_center(47, 8, "Zürich")', "is a saved centre point"),
    ("opensak.set_center(47, 8, 3)", "name after the coordinates must be a string"),
])
def test_bad_arguments_change_nothing_and_ask_nothing(call, message):
    host = Host()
    with pytest.raises(MacroError, match=message):
        _run(call, host)
    assert host.events == []
    assert get_settings().active_home_name == "Aarau"


def test_write_denied_keeps_the_centre():
    host = Host(WriteApproval.DENY)
    with pytest.raises(MacroError, match="did not allow"):
        _run('opensak.set_center("Zürich")', host)
    assert host.events == ["write?"]
    assert get_settings().active_home_name == "Aarau"


def test_refused_inside_a_transaction():
    from opensak.db.manager import get_db_manager
    get_db_manager().ensure_active_initialised()
    host = Host()
    with pytest.raises(MacroError, match="transaction"):
        _run('opensak.transaction(function() opensak.set_center("Zürich") end)', host)
    assert ("Zürich", 47.37, 8.54) not in host.events


def test_host_without_set_center():
    class Plain(Host):
        set_center = None  # type: ignore[assignment]

    with pytest.raises(MacroError, match="not available"):
        _run('opensak.set_center("Zürich")', Plain())


def test_set_center_needs_the_write_flag_but_reading_does_not(monkeypatch):
    from opensak.utils import flags
    monkeypatch.setattr(flags, "lua_macro_write", False)
    _, out = _run("print(opensak.set_center, opensak.center().name, #opensak.centers())")
    assert out == ["nil\tAarau\t3"]


# ── MainWindow: the host side ────────────────────────────────────────────────

def test_mainwindow_set_center_stores_and_recalculates(monkeypatch):
    from opensak.gui.mainwindow import MainWindow

    recalculated = []
    monkeypatch.setattr("opensak.db.database.recalculate_distances",
                        lambda lat, lon: recalculated.append((lat, lon)))
    window = SimpleNamespace(
        _reload_home_combo=MagicMock(), _map_widget=MagicMock(),
        _refresh_cache_list=MagicMock(), _update_info_bar=MagicMock(),
        _statusbar=MagicMock(),
    )
    window._activate_center = lambda point: MainWindow._activate_center(window, point)
    MainWindow.set_center(window, "Bonus", 47.5, 8.7)  # type: ignore[arg-type]
    s = get_settings()
    assert (s.active_home_name, s.home_lat, s.home_lon) == ("Bonus", 47.5, 8.7)
    assert recalculated == [(47.5, 8.7)]
    window._reload_home_combo.assert_called_once()
    window._map_widget.pan_to_location.assert_called_once_with(47.5, 8.7, "Bonus")
    window._refresh_cache_list.assert_called_once()
