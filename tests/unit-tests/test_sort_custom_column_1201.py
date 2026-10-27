# tests/unit-tests/test_sort_custom_column_1201.py — issue #1201.
#
# Clicking the header of a custom field column (id "custom:<name>") raised
# "Unknown sort field" in MainWindow._on_sort_changed: SortSpec only knew the
# fields in SORT_FIELDS. The Lat/Lon columns had the same problem. Every
# column the grid can show must be accepted, and the sort must be saved and
# restored for the database like any other.

import pytest

pytest.importorskip("pytestqt")

from opensak.filters.engine import SORT_FIELDS, SortSpec, is_sort_field
from opensak.gui.mainwindow import MainWindow


def test_every_grid_column_is_a_sort_field():
    from opensak.gui.dialogs.column_dialog import custom_column_id, get_all_columns

    ids = [c[0] for c in get_all_columns()] + [custom_column_id("LFL")]
    missing = [col for col in ids if not is_sort_field(col)]
    assert missing == [], f"columns SortSpec would refuse: {missing}"
    for col in ids:
        SortSpec(col)


@pytest.mark.parametrize("field", ["custom:LFL", "custom:My field", "latitude", "longitude"])
def test_accepted(field):
    assert SortSpec(field, ascending=False).field == field


@pytest.mark.parametrize("field", ["custom:", "nonsense", "Custom:LFL"])
def test_still_refused(field):
    with pytest.raises(ValueError, match="Unknown sort field"):
        SortSpec(field)


def test_round_trip_through_a_filter_profile():
    spec = SortSpec.from_dict(SortSpec("custom:LFL", ascending=False).to_dict())
    assert (spec.field, spec.ascending) == ("custom:LFL", False)


def test_apply_filters_leaves_the_order_to_the_grid(db_session, make_cache):
    from opensak.filters.engine import apply_filters

    for code, name in (("GC2", "b"), ("GC1", "a")):
        db_session.add(make_cache(code, name=name))
    db_session.commit()
    results = apply_filters(db_session, None, SortSpec("custom:LFL"))
    assert {c.gc_code for c in results} == {"GC1", "GC2"}
    assert "custom:LFL" not in SORT_FIELDS


# ── The main window ───────────────────────────────────────────────────────────

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
    db_path = tmp_path / "test_sort_custom_column_1201.db"
    init_db(db_path=db_path)
    monkeypatch.setattr(mgr_module, "_manager", make_fake_manager(db_path, name="SortTest"))
    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    win.close()
    mgr_module._manager = None


@pytest.mark.parametrize("col", ["custom:LFL", "latitude"])
def test_header_click_is_saved_and_restored(window, col):
    from opensak.db import db_settings

    window._on_sort_changed(col, False)   # raised ValueError before #1201

    assert (window._current_sort.field, window._current_sort.ascending) == (col, False)
    assert db_settings.get_value("sort.field", None, None) == col

    window._current_sort = SortSpec("name")
    window._load_sort_for_active_db()
    assert (window._current_sort.field, window._current_sort.ascending) == (col, False)
