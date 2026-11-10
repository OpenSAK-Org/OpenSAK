# tests/e2e-tests/test_e2e_db_switch_hints_column.py
#
# Regression test: switching from a database whose cache list has the
# Hints column off to a database that has it on crashed on the next
# repaint with
#   AttributeError: LightweightCache has no attribute 'encoded_hints'
# _on_database_switched() swapped in the new database's columns while the
# table still held the previous database's LightweightCache rows (the fast
# query path, used because the old database had Hints off). Reported live
# on v1.22.0-beta.12 (Linux Mint).

import pytest

pytest.importorskip("pytestqt")


def _make_two_db_manager(db_path_a, db_path_b):
    from opensak.db.manager import DatabaseInfo

    class _FakeTwoManager:
        def __init__(self):
            self._a = DatabaseInfo("Alpha", db_path_a)
            self._b = DatabaseInfo("Beta", db_path_b)
            self._active = self._a

        @property
        def active(self):
            return self._active

        @property
        def active_path(self):
            return self._active.path

        @property
        def databases(self):
            return [self._a, self._b]

        def ensure_active_initialised(self):
            pass

        def switch_to(self, db_info):
            self._active = db_info

        def new_database(self, _name, path=None):
            raise RuntimeError("new_database called during e2e test")

    return _FakeTwoManager()


def _all_cells_display(model) -> None:
    """Ask the model for every cell's DisplayRole text, like a repaint."""
    from PySide6.QtCore import Qt
    for row in range(model.rowCount()):
        for col in range(model.columnCount()):
            model.data(model.index(row, col), Qt.ItemDataRole.DisplayRole)


@pytest.fixture
def hints_switch_window(qtbot, tmp_path, monkeypatch, make_cache):
    import opensak.db.manager as mgr_module
    from opensak.db.database import get_session, init_db
    from opensak.lang import load_language

    load_language("en")

    db_path_a = tmp_path / "alpha.db"
    db_path_b = tmp_path / "beta.db"
    fake_mgr = _make_two_db_manager(db_path_a, db_path_b)
    monkeypatch.setattr(mgr_module, "_manager", fake_mgr)

    # Beta: one cache with a hint, and the Hints column on.
    from opensak.gui.dialogs.column_dialog import get_visible_columns, set_visible_columns
    fake_mgr._active = fake_mgr._b
    init_db(db_path=db_path_b)
    with get_session() as s:
        s.add(make_cache(gc_code="GCBETA1", encoded_hints="Under the rock"))
    set_visible_columns([c for c in get_visible_columns() if c != "hints"] + ["hints"])

    # Alpha (active at start): one cache, Hints column off.
    fake_mgr._active = fake_mgr._a
    init_db(db_path=db_path_a)
    with get_session() as s:
        s.add(make_cache(gc_code="GCALPHA1"))
    set_visible_columns([c for c in get_visible_columns() if c not in ("hints", "notes")])

    from opensak.gui.mainwindow import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    qtbot.wait(200)

    yield window, fake_mgr

    mgr_module._manager = None


def test_switch_to_db_with_hints_column_does_not_crash(hints_switch_window, qtbot):
    from opensak.db.database import init_db
    from opensak.filters.engine import FilterSet, LightweightCache, apply_filters_auto
    from opensak.db.database import get_session

    window, fake_mgr = hints_switch_window
    model = window._cache_table._model

    # Precondition: Alpha's rows come from the lightweight path (Hints off).
    with get_session() as s:
        rows = apply_filters_auto(s, FilterSet(), columns=window._visible_table_columns())
    window._cache_table.load_caches(rows)
    assert rows and isinstance(rows[0], LightweightCache)
    assert model.has_lightweight_caches()

    # Switch to Beta, the way DatabaseManager/the combo does it.
    fake_mgr.switch_to(fake_mgr._b)
    init_db(db_path=fake_mgr._b.path)
    window._on_database_switched(fake_mgr._b)

    # Beta's columns are now active, and no stale lightweight row may be
    # painted with them — this raised AttributeError (encoded_hints).
    assert "hints" in window._visible_table_columns()
    _all_cells_display(model)
    assert not any(c.gc_code == "GCALPHA1" for c in model._caches)

    # Once the refresh has run, Beta's own row shows its hint. In production
    # reload_map() runs it when the map is ready; there is no WebEngine in
    # tests, so run it directly.
    window._refresh_cache_list()
    qtbot.waitUntil(lambda: model.rowCount() == 1, timeout=5000)
    _all_cells_display(model)
    assert model._caches[0].gc_code == "GCBETA1"
