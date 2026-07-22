# tests/e2e-tests/test_e2e_mainwindow.py — MainWindow actions, dialogs, slots.

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QKeySequence

pytest.importorskip("pytestqt")

import opensak.gui.icon as icon_mod
from opensak.gui.mainwindow import MainWindow
from opensak.lang import tr

# Captured before conftest's autouse fixture no-ops them on the class.
_REAL_INITIAL_LOAD = MainWindow._initial_load
_REAL_CHECK_SETUP = MainWindow._check_setup_complete
_REAL_CHECK_UPDATE_BG = MainWindow._check_update_background


# ── fakes ─────────────────────────────────────────────────────────────────────

class _Sig:
    def __init__(self):
        self._cbs = []

    def connect(self, cb):
        self._cbs.append(cb)

    def emit(self, *a):
        for cb in list(self._cbs):
            cb(*a)


def fake_dialog(*, exec_result=0, signals=(), data=None, attrs=None):
    # Build a non-modal dialog stub class.
    class _Fake:
        def __init__(self, *a, **k):
            self.args = a
            self.kwargs = k
            for s in signals:
                setattr(self, s, _Sig())
            for name, val in (attrs or {}).items():
                setattr(self, name, val)

        def exec(self):
            return exec_result

        def add_files(self, paths):
            self.files = paths

        def get_data(self):
            return data
    return _Fake


def fake_worker():
    # Stub UpdateCheckWorker that never spawns a thread (safe for closeEvent).
    class _W:
        def __init__(self, *a, **k):
            self.update_available = _Sig()
            self.check_done = _Sig()

        def start(self):
            pass

        def isRunning(self):
            return False

        def quit(self):
            pass

        def wait(self, *a):
            pass
    return _W


def fake_worker_capturing(captured: dict):
    """Like fake_worker(), but also records the kwargs it was constructed with."""
    class _W:
        def __init__(self, *a, **k):
            captured.update(k)
            self.update_available = _Sig()
            self.check_done = _Sig()

        def start(self):
            pass

        def isRunning(self):
            return False

        def quit(self):
            pass

        def wait(self, *a):
            pass
    return _W


def _wp_data(gc_code="CW001"):
    return {
        "gc_code": gc_code, "name": "New WP", "cache_type": "Traditional Cache",
        "latitude": 55.5, "longitude": 12.5, "difficulty": 1.0, "terrain": 1.0,
        "available": True, "archived": False, "found": False,
    }


# ── shared isolation fixture ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def iso_settings(tmp_path, monkeypatch):
    # Redirect SettingsStore to a fresh in-memory store per test.
    from opensak import settings_store as ss
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)

    # Reset AppSettings singleton so it picks up the new store
    import opensak.gui.settings as smod
    monkeypatch.setattr(smod, "_settings", None)

    yield SimpleNamespace(store=fresh)


@pytest.fixture
def mbox_yes(monkeypatch):
    monkeypatch.setattr(icon_mod.QMessageBox, "exec",
                        lambda self: icon_mod.QMessageBox.StandardButton.Yes)


@pytest.fixture
def mbox_no(monkeypatch):
    monkeypatch.setattr(icon_mod.QMessageBox, "exec",
                        lambda self: icon_mod.QMessageBox.StandardButton.No)


@pytest.fixture
def mbox_ok(monkeypatch):
    monkeypatch.setattr(icon_mod.QMessageBox, "exec",
                        lambda self: icon_mod.QMessageBox.StandardButton.Ok)


# ── title / db combo / db manager ─────────────────────────────────────────────

class TestDbCombo:
    def test_update_title_with_active(self, seeded_window):
        seeded_window._update_title()
        assert "v" in seeded_window.windowTitle()

    def test_open_db_manager(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.database_dialog.DatabaseManagerDialog",
            fake_dialog(signals=("database_switched", "database_renamed")))
        seeded_window._open_db_manager()

    def test_open_db_manager_blocked_by_trip(self, seeded_window, monkeypatch):
        seeded_window._trip_planner_win = SimpleNamespace(
            isVisible=lambda: True, raise_=lambda: None, activateWindow=lambda: None)
        called = []
        monkeypatch.setattr(
            "opensak.gui.dialogs.database_dialog.DatabaseManagerDialog",
            lambda *a, **k: called.append(True))
        seeded_window._open_db_manager()
        assert called == []

    def test_on_database_switched(self, seeded_window):
        info = SimpleNamespace(name="OtherDB")
        seeded_window._on_database_switched(info)

    def test_on_database_renamed(self, seeded_window):
        # Issue #539 follow-up: a rename must refresh the db combo/title
        # without touching detail panel, map, etc. (unlike a real switch).
        info = SimpleNamespace(name="RenamedDB")
        seeded_window._on_database_renamed(info)
        assert "v" in seeded_window.windowTitle()

    def test_reload_db_combo(self, seeded_window):
        seeded_window._reload_db_combo()
        assert seeded_window._db_combo.count() >= 1

    def test_on_db_combo_changed_no_data(self, seeded_window):
        seeded_window._on_db_combo_changed(999)  # itemData None → early return

    def test_on_db_combo_changed_same_active(self, seeded_window):
        seeded_window._on_db_combo_changed(0)  # equals active → early return


# ── splitter / restore / close ────────────────────────────────────────────────

class TestLayout:
    def test_restore_splitter_ratios(self, seeded_window):
        seeded_window._restore_splitter_ratios()

    def test_save_splitter_ratios(self, seeded_window):
        seeded_window._save_splitter_ratios()


# ── cache list / info bar / filterset ─────────────────────────────────────────

class TestCacheList:
    def test_refresh_table_only_single(self, seeded_window):
        seeded_window._search_gc.setText("GC12345")
        seeded_window._refresh_table_only()
        assert seeded_window._cache_table.row_count() == 1

    def test_refresh_table_only_many(self, seeded_window):
        seeded_window._refresh_table_only()
        assert seeded_window._cache_table.row_count() >= 1

    def test_build_filterset_quick_filters(self, seeded_window):
        for idx in range(6):
            seeded_window._quick_filter.setCurrentIndex(idx)
            fs = seeded_window._build_current_filterset()
            assert fs is not None

    def test_build_filterset_search_fields(self, seeded_window):
        seeded_window._search_gc.setText("GC123")
        seeded_window._search_box.setText("Test")
        fs = seeded_window._build_current_filterset()
        assert len(fs) >= 2

    def test_archived_quick_filter_shows_archived(self, seeded_window):
        from opensak.db.database import get_session
        from opensak.db.models import Cache as CacheModel

        with get_session() as session:
            cache = session.query(CacheModel).filter_by(gc_code="GC12345").one()
            cache.archived = True
            session.commit()

        seeded_window._quick_filter.setCurrentIndex(5)  # "Archived" quick filter
        seeded_window._refresh_cache_list()
        codes = [c.gc_code for c in seeded_window._cache_table.get_all_caches()]
        assert "GC12345" in codes

    def test_archived_visible_by_default(self, seeded_window):
        from opensak.db.database import get_session
        from opensak.db.models import Cache as CacheModel

        with get_session() as session:
            cache = session.query(CacheModel).filter_by(gc_code="GC12345").one()
            cache.archived = True
            session.commit()

        seeded_window._quick_filter.setCurrentIndex(0)
        seeded_window._refresh_cache_list()
        codes = [c.gc_code for c in seeded_window._cache_table.get_all_caches()]
        assert "GC12345" in codes

    def test_refresh_with_active_filterset(self, seeded_window):
        from opensak.filters.engine import FilterSet, GcCodeFilter
        fs = FilterSet(mode="AND")
        fs.add(GcCodeFilter("GC12345"))
        seeded_window._current_filterset = fs
        seeded_window._refresh_cache_list()

    def test_update_info_bar(self, seeded_window):
        seeded_window._update_info_bar()

    def test_infobar_shows_filter_count(self, seeded_window):
        # regression for #373: infobar must show count, not generic "Active"
        from opensak.filters.engine import FilterSet, GcCodeFilter, CacheTypeFilter
        fs = FilterSet(mode="AND")
        fs.add(GcCodeFilter("GC12345"))
        fs.add(CacheTypeFilter(["Traditional Cache"]))
        seeded_window._current_filterset = fs
        seeded_window._update_info_bar()
        text = seeded_window._info_bar._filter_lbl.text()
        assert "2" in text
        assert "Active" not in text and "Aktiv" not in text

    def test_update_info_bar_with_owner(self, seeded_window, iso_settings):
        from opensak.gui.settings import get_settings
        get_settings().gc_username = "TestOwner"
        seeded_window._update_info_bar()
        expected = sum(
            1 for c in seeded_window._cache_table.get_all_caches()
            if (c.owner_name or "").strip().lower() == "testowner"
        )
        assert expected > 0
        assert seeded_window._info_bar._owned_lbl.text() == str(expected)

    def test_owned_count_uses_owner_not_placed_by(self, seeded_window, iso_settings):
        """Issue #270: GSAK counts the cache 'Owner', not the original
        'Placed by'. An adopted cache (the two differ) must be counted and
        be filterable by its current owner."""
        from opensak.gui.settings import get_settings
        from opensak.db.database import get_session
        from opensak.db.models import Cache

        with get_session() as session:
            cache = session.query(Cache).filter_by(gc_code="GC12345").one()
            cache.placed_by = "OriginalPlacer"
            cache.owner_name = "AdoptedOwner"
            session.commit()

        get_settings().gc_username = "AdoptedOwner"
        seeded_window._refresh_cache_list()
        assert seeded_window._info_bar._owned_lbl.text() == "1"

        # Clicking the owned tile must filter by owner — a PlacedByFilter
        # would find nothing here since placed_by is "OriginalPlacer".
        seeded_window._filter_by_status("owned")
        assert seeded_window._cache_table.row_count() == 1
        assert seeded_window._cache_table.get_all_caches()[0].gc_code == "GC12345"

    def test_found_count_sums_found_log_count_not_found_caches(self, seeded_window):
        # Regression for #552 (Mike Wood): a relocatable cache found multiple
        # times must contribute its found_log_count, not just 1.
        from opensak.db.database import get_session
        from opensak.db.models import Cache

        with get_session() as session:
            cache = session.query(Cache).filter_by(gc_code="GC12345").one()
            cache.found = True
            cache.found_log_count = 25  # GCCF79-style relocatable cache
            session.commit()

        seeded_window._refresh_cache_list()
        assert seeded_window._info_bar._found_lbl.text() == "25"

    def test_found_count_falls_back_to_one_when_found_log_count_zero(self, seeded_window):
        # Safety net: found=True but found_log_count is 0 (e.g. no
        # gc_username/gc_finder_id configured, or the matching log wasn't in
        # this database) must still contribute 1, never 0 — the count must
        # never regress below the old cache-counting behaviour.
        from opensak.db.database import get_session
        from opensak.db.models import Cache

        with get_session() as session:
            cache = session.query(Cache).filter_by(gc_code="GC12345").one()
            cache.found = True
            cache.found_log_count = 0
            session.commit()

        seeded_window._refresh_cache_list()
        found_text = seeded_window._info_bar._found_lbl.text()
        assert int(found_text) >= 1

    def test_found_count_ignores_not_found_caches_regardless_of_log_count(
        self, seeded_window
    ):
        # found_log_count on a not-found cache (shouldn't normally happen,
        # but defence in depth) must not be counted.
        from opensak.db.database import get_session
        from opensak.db.models import Cache

        with get_session() as session:
            cache = session.query(Cache).filter_by(gc_code="GC12345").one()
            cache.found = False
            cache.found_log_count = 5
            session.commit()

        seeded_window._refresh_cache_list()
        codes_found = [
            c.found_log_count for c in seeded_window._cache_table.get_all_caches()
            if c.gc_code == "GC12345"
        ]
        assert codes_found == [5]  # the stray value is still there...
        # ...but must not have leaked into the footer total for this cache.
        with get_session() as session:
            total_found = sum(
                max(c.found_log_count, 1)
                for c in session.query(Cache).all()
                if c.found
            )
        assert seeded_window._info_bar._found_lbl.text() == str(total_found)



# ── issue #638: skip map data load when disabled ──────────────────────────────

class TestMapEnabledSetting:
    def test_map_load_skipped_when_disabled(self, seeded_window, monkeypatch):
        from opensak.gui.settings import get_settings
        get_settings().map_enabled = False
        calls = []
        monkeypatch.setattr(
            seeded_window._map_widget, "load_caches", lambda caches: calls.append(caches)
        )
        seeded_window._refresh_cache_list()
        assert calls == []

    def test_map_load_runs_when_enabled(self, seeded_window, monkeypatch):
        from opensak.gui.settings import get_settings
        get_settings().map_enabled = True
        calls = []
        monkeypatch.setattr(
            seeded_window._map_widget, "load_caches", lambda caches: calls.append(caches)
        )
        seeded_window._refresh_cache_list()
        assert len(calls) == 1

    def test_table_unaffected_when_map_disabled(self, seeded_window):
        # #638 only guards the map — the table must show exactly the same
        # rows either way.
        from opensak.gui.settings import get_settings
        get_settings().map_enabled = True
        seeded_window._refresh_cache_list()
        with_map = [c.gc_code for c in seeded_window._cache_table.get_all_caches()]

        get_settings().map_enabled = False
        seeded_window._refresh_cache_list()
        without_map = [c.gc_code for c in seeded_window._cache_table.get_all_caches()]

        assert with_map == without_map

    def test_reenabling_mid_session_populates_map(self, seeded_window, monkeypatch):
        # The existing _open_settings() flow already calls
        # _refresh_cache_list() unconditionally after the dialog closes, so
        # toggling map_enabled back on needs no special-case code — this
        # test confirms that's actually true, not just assumed.
        from opensak.gui.settings import get_settings
        get_settings().map_enabled = False
        seeded_window._refresh_cache_list()

        get_settings().map_enabled = True
        calls = []
        monkeypatch.setattr(
            seeded_window._map_widget, "load_caches", lambda caches: calls.append(caches)
        )
        seeded_window._refresh_cache_list()
        assert len(calls) == 1

    def test_placeholder_shown_when_disabled(self, seeded_window):
        # Follow-up: disabling the map only ever skipped the marker data
        # load — the map's own tiles/zoom controls rendered regardless,
        # looking like a stuck/empty map rather than an intentional off
        # state. A QStackedWidget now swaps in a plain placeholder page.
        from opensak.gui.settings import get_settings
        get_settings().map_enabled = False
        seeded_window._update_map_visibility()
        assert seeded_window._map_stack.currentWidget() is seeded_window._map_disabled_placeholder

    def test_real_map_shown_when_enabled(self, seeded_window):
        from opensak.gui.settings import get_settings
        get_settings().map_enabled = True
        seeded_window._update_map_visibility()
        assert seeded_window._map_stack.currentWidget() is seeded_window._map_widget

    def test_placeholder_swap_via_open_settings_flow(self, seeded_window, monkeypatch):
        # _open_settings() calls _update_map_visibility() after the dialog
        # closes — confirm the stack actually swaps through that real flow,
        # not just via the helper method directly.
        from opensak.gui.settings import get_settings

        get_settings().map_enabled = True
        seeded_window._update_map_visibility()
        assert seeded_window._map_stack.currentWidget() is seeded_window._map_widget

        class _FakeDialog:
            def __init__(self, parent=None):
                pass

            def exec(self):
                # Simulate the user unchecking "Show map" and clicking OK —
                # the real dialog's _save() would set this.
                get_settings().map_enabled = False
                return True

        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.SettingsDialog", _FakeDialog
        )
        monkeypatch.setattr(seeded_window, "_reload_home_combo", lambda: None)
        monkeypatch.setattr(seeded_window._map_widget, "reload_map", lambda cb: None)

        seeded_window._open_settings()

        assert seeded_window._map_stack.currentWidget() is seeded_window._map_disabled_placeholder

class TestSelectionSlots:
    def test_on_cache_selected(self, seeded_window):
        cache = seeded_window._cache_table._model.cache_at(0)
        seeded_window._on_cache_selected(cache)

    def test_on_cache_selected_missing(self, seeded_window):
        seeded_window._on_cache_selected(SimpleNamespace(gc_code="NOPE"))

    def test_on_map_cache_selected(self, seeded_window):
        seeded_window._on_map_cache_selected("GC12345")

    def test_on_map_cache_selected_missing(self, seeded_window):
        seeded_window._on_map_cache_selected("NOPE")

    def test_on_corrected_coords_changed(self, seeded_window):
        seeded_window._on_corrected_coords_changed("GC12345")

    def test_load_full_cache(self, seeded_window):
        assert seeded_window._load_full_cache("GC12345") is not None


# ── search ────────────────────────────────────────────────────────────────────

class TestSearch:
    def test_search_changed_empty(self, seeded_window):
        seeded_window._on_search_changed("")

    def test_search_changed_above_threshold(self, seeded_window):
        seeded_window._on_search_changed("abcdef")

    def test_search_changed_below_threshold(self, seeded_window):
        seeded_window._db_count = 50_000  # forces min_chars=3
        seeded_window._on_search_changed("a")

    def test_search_thresholds_adaptive(self, seeded_window):
        for cnt in (0, 5_000, 20_000):
            seeded_window._db_count = cnt
            assert len(seeded_window._search_thresholds()) == 2

    def test_quick_filter_changed(self, seeded_window):
        seeded_window._on_quick_filter_changed(1)


# ── drag & drop ───────────────────────────────────────────────────────────────

def _evt(paths, accept, ignore):
    urls = [SimpleNamespace(toLocalFile=lambda p=p: p) for p in paths]
    mime = SimpleNamespace(hasUrls=lambda: bool(urls), urls=lambda: urls)
    return SimpleNamespace(
        mimeData=lambda: mime,
        acceptProposedAction=accept,
        ignore=ignore,
    )


class TestDragDrop:
    def test_drag_enter_accepts_gpx(self, seeded_window):
        flags = {"accept": False, "ignore": False}
        e = _evt(["/x/a.gpx"],
                 lambda: flags.update(accept=True),
                 lambda: flags.update(ignore=True))
        seeded_window.dragEnterEvent(e)
        assert flags["accept"] is True

    def test_drag_enter_ignores_other(self, seeded_window):
        flags = {"ignore": False}
        e = _evt(["/x/a.txt"], lambda: None, lambda: flags.update(ignore=True))
        seeded_window.dragEnterEvent(e)
        assert flags["ignore"] is True

    def test_drop_imports(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.import_dialog.ImportDialog",
            fake_dialog(signals=("import_completed",)))
        flags = {"accept": False}
        e = _evt(["/x/a.gpx"], lambda: flags.update(accept=True), lambda: None)
        seeded_window.dropEvent(e)
        assert flags["accept"] is True

    def test_drop_no_valid_paths(self, seeded_window):
        flags = {"ignore": False}
        e = _evt(["/x/a.txt"], lambda: None, lambda: flags.update(ignore=True))
        seeded_window.dropEvent(e)
        assert flags["ignore"] is True

    def test_drop_blocked_by_trip(self, seeded_window):
        seeded_window._trip_planner_win = SimpleNamespace(
            isVisible=lambda: True, raise_=lambda: None, activateWindow=lambda: None)
        flags = {"ignore": False}
        e = _evt(["/x/a.gpx"], lambda: None, lambda: flags.update(ignore=True))
        seeded_window.dropEvent(e)
        assert flags["ignore"] is True


# ── import / settings / home ──────────────────────────────────────────────────

class TestImportSettingsHome:
    def test_open_import_dialog(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.import_dialog.ImportDialog",
            fake_dialog(signals=("import_completed",)))
        seeded_window._open_import_dialog()

    def test_open_import_blocked_by_trip(self, seeded_window):
        seeded_window._trip_planner_win = SimpleNamespace(
            isVisible=lambda: True, raise_=lambda: None, activateWindow=lambda: None)
        seeded_window._open_import_dialog()  # warns, returns

    def test_refresh_after_import(self, seeded_window):
        seeded_window._refresh_after_import()

    def test_open_settings_accepted(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.SettingsDialog",
            fake_dialog(exec_result=1))
        seeded_window._open_settings()

    def test_open_settings_rejected(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.SettingsDialog",
            fake_dialog(exec_result=0))
        seeded_window._open_settings()

    def test_open_settings_accepted_recalculates_distances(self, seeded_window, monkeypatch):
        # Issue #522: editing a home point's own coordinates (or adding a new
        # one that becomes active) in Settings goes through
        # _sync_active_home_coords() -> set_active_home(), which never fires
        # _on_home_changed() — so _open_settings() must recalculate distances
        # itself, or the persisted Cache.distance column goes stale until the
        # next manual home-point switch (causing "distance" sort/column to
        # silently show outdated values).
        calls = []
        monkeypatch.setattr(
            "opensak.db.database.recalculate_distances",
            lambda lat, lon: calls.append((lat, lon)),
        )
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.SettingsDialog",
            fake_dialog(exec_result=1))
        seeded_window._open_settings()
        assert len(calls) == 1

    def test_open_settings_rejected_skips_recalculation(self, seeded_window, monkeypatch):
        # Counterpart to the above: cancelling Settings must NOT trigger a
        # recalculation, since nothing changed.
        calls = []
        monkeypatch.setattr(
            "opensak.db.database.recalculate_distances",
            lambda lat, lon: calls.append((lat, lon)),
        )
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.SettingsDialog",
            fake_dialog(exec_result=0))
        seeded_window._open_settings()
        assert calls == []

    def test_reload_home_combo_with_points(self, seeded_window):
        seeded_window._reload_home_combo()
        assert seeded_window._home_combo.count() >= 1

    def test_reload_home_combo_no_points(self, seeded_window, monkeypatch):
        from opensak.gui.settings import AppSettings
        monkeypatch.setattr(AppSettings, "home_points", property(lambda self: []))
        seeded_window._reload_home_combo()

    def test_on_home_changed_no_name(self, seeded_window):
        seeded_window._home_combo.addItem("none", None)
        seeded_window._on_home_changed(seeded_window._home_combo.count() - 1)


# ── startup hooks ─────────────────────────────────────────────────────────────

class TestStartup:
    def test_initial_load_not_ready(self, seeded_window):
        _REAL_INITIAL_LOAD(seeded_window)

    def test_initial_load_ready(self, seeded_window):
        seeded_window._map_widget._ready = True
        _REAL_INITIAL_LOAD(seeded_window)

    def test_check_setup_complete_already(self, seeded_window, monkeypatch):
        from opensak.gui.settings import AppSettings
        monkeypatch.setattr(AppSettings, "is_setup_complete", lambda self: True)
        _REAL_CHECK_SETUP(seeded_window)

    def test_check_setup_incomplete_opens_settings(self, seeded_window, monkeypatch, mbox_ok):
        from opensak.gui.settings import AppSettings
        monkeypatch.setattr(AppSettings, "is_setup_complete", lambda self: False)
        monkeypatch.setattr(
            "opensak.gui.dialogs.settings_dialog.SettingsDialog",
            fake_dialog(exec_result=0))
        _REAL_CHECK_SETUP(seeded_window)

    def test_check_update_background(self, seeded_window, monkeypatch):
        monkeypatch.setattr("opensak.gui.mainwindow.UpdateCheckWorker", fake_worker())
        _REAL_CHECK_UPDATE_BG(seeded_window)

    def test_check_update_background_forwards_notify_about_betas(
        self, seeded_window, monkeypatch, iso_settings
    ):
        from opensak.gui.settings import get_settings
        captured: dict = {}
        monkeypatch.setattr(
            "opensak.gui.mainwindow.UpdateCheckWorker", fake_worker_capturing(captured)
        )
        get_settings().notify_about_betas = True
        _REAL_CHECK_UPDATE_BG(seeded_window)

        assert captured.get("include_prereleases") is True

    def test_check_update_manual_forwards_notify_about_betas(
        self, seeded_window, monkeypatch, iso_settings
    ):
        from opensak.gui.settings import get_settings
        captured: dict = {}
        monkeypatch.setattr(
            "opensak.gui.mainwindow.UpdateCheckWorker", fake_worker_capturing(captured)
        )
        get_settings().notify_about_betas = False
        seeded_window._check_update_manual()

        assert captured.get("include_prereleases") is False


# ── waypoint CRUD ─────────────────────────────────────────────────────────────

class TestWaypoints:
    def test_next_cw_id(self, seeded_window):
        assert seeded_window._next_cw_id() == "CW001"

    def test_add_waypoint(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.waypoint_dialog.WaypointDialog",
            fake_dialog(exec_result=1, data=_wp_data("CW001")))
        seeded_window._add_waypoint()
        assert seeded_window._load_full_cache("CW001") is not None

    def test_add_waypoint_existing_warns(self, seeded_window, monkeypatch, mbox_ok):
        monkeypatch.setattr(
            "opensak.gui.dialogs.waypoint_dialog.WaypointDialog",
            fake_dialog(exec_result=1, data=_wp_data("GC12345")))
        seeded_window._add_waypoint()

    def test_add_waypoint_cancelled(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.waypoint_dialog.WaypointDialog",
            fake_dialog(exec_result=0))
        seeded_window._add_waypoint()

    def test_edit_waypoint_no_selection(self, seeded_window):
        seeded_window._cache_table.clearSelection()
        seeded_window._edit_waypoint()

    def test_edit_waypoint_from_cache(self, seeded_window, monkeypatch):
        cache = seeded_window._load_full_cache("GC12345")
        monkeypatch.setattr(
            "opensak.gui.dialogs.waypoint_dialog.WaypointDialog",
            fake_dialog(exec_result=1, data=_wp_data("GC12345")))
        seeded_window._edit_waypoint_from_cache(cache)
        updated = seeded_window._load_full_cache("GC12345")
        assert updated.name == "New WP"

    def test_edit_waypoint_from_cache_cancelled(self, seeded_window, monkeypatch):
        cache = seeded_window._load_full_cache("GC12345")
        before = cache.name
        monkeypatch.setattr(
            "opensak.gui.dialogs.waypoint_dialog.WaypointDialog",
            fake_dialog(exec_result=0))
        seeded_window._edit_waypoint_from_cache(cache)
        assert seeded_window._load_full_cache("GC12345").name == before

    def test_edit_waypoint_from_cache_with_deferred_fields(self, seeded_window, monkeypatch):
        """Regression test: apply_filters() (used by the grid/_refresh_cache_list)
        defer()'s short_description/long_description/encoded_hints for performance.
        A cache object coming straight from that query -- exactly what the grid
        passes to Edit Cache via right-click or the menu -- must not blow up with
        DetachedInstanceError when the dialog opens (_edit_waypoint_from_cache
        must reload a full copy first; see _load_full_cache()).
        """
        from opensak.db.database import get_session
        from opensak.filters.engine import apply_filters, FilterSet
        from opensak.gui.dialogs import waypoint_dialog as wpd

        with get_session() as session:
            caches = apply_filters(session, FilterSet())
        row_cache = next(c for c in caches if c.gc_code == "GC12345")
        # Session is now closed -- row_cache's text fields are still deferred.

        # Real __init__ / _populate() must run (that's what crashed before the
        # fix); only stub exec() so the modal dialog doesn't block the test.
        monkeypatch.setattr(wpd.WaypointDialog, "exec", lambda self: 0)
        seeded_window._edit_waypoint_from_cache(row_cache)  # must not raise

    def test_delete_waypoint_no_selection(self, seeded_window):
        seeded_window._cache_table.clearSelection()
        seeded_window._delete_waypoint()

    def test_delete_waypoint_confirmed(self, seeded_window, monkeypatch, mbox_yes):
        seeded_window._cache_table.select_by_gc_code("GC99999")
        seeded_window._delete_waypoint()
        assert seeded_window._load_full_cache("GC99999") is None

    def test_delete_waypoint_declined(self, seeded_window, monkeypatch, mbox_no):
        seeded_window._cache_table.select_by_gc_code("GC12345")
        seeded_window._delete_waypoint()
        assert seeded_window._load_full_cache("GC12345") is not None


# ── bulk delete / flags ───────────────────────────────────────────────────────

class TestBulkAndFlags:
    def _flag(self, window, gc_code):
        from opensak.db.database import get_session
        from opensak.db.models import Cache
        with get_session() as s:
            c = s.query(Cache).filter_by(gc_code=gc_code).first()
            c.user_flag = True
        window._refresh_cache_list()

    def test_delete_flagged_none(self, seeded_window, mbox_ok):
        seeded_window._delete_flagged_caches()  # info, returns

    def test_delete_flagged_confirmed(self, seeded_window, mbox_yes):
        self._flag(seeded_window, "GC12345")
        seeded_window._delete_flagged_caches()
        assert seeded_window._load_full_cache("GC12345") is None

    def test_delete_filtered_none(self, empty_window, mbox_ok):
        empty_window._delete_filtered_caches()  # info, returns

    def test_delete_filtered_confirmed(self, seeded_window, mbox_yes):
        seeded_window._delete_filtered_caches()
        assert seeded_window._cache_table.row_count() == 0

    def test_bulk_delete_empty_codes(self, seeded_window):
        seeded_window._bulk_delete_caches(["NONEXIST"])  # cache_ids empty branch

    def test_clear_all_flags_confirmed(self, seeded_window, mbox_yes):
        self._flag(seeded_window, "GC12345")
        seeded_window._clear_all_flags()

    def test_clear_all_flags_declined(self, seeded_window, mbox_no):
        seeded_window._clear_all_flags()

    def test_on_flags_changed(self, seeded_window):
        self._flag(seeded_window, "GC12345")
        seeded_window._on_flags_changed()


# ── sort save/load ────────────────────────────────────────────────────────────

class TestSort:
    def test_on_sort_changed(self, seeded_window):
        seeded_window._on_sort_changed("name", False)
        assert seeded_window._current_sort.field == "name"

    def test_save_sort_no_active(self, seeded_window, monkeypatch):
        monkeypatch.setattr("opensak.db.manager.get_db_manager",
                            lambda: SimpleNamespace(active=None))
        seeded_window._save_sort_for_active_db()

    def test_load_sort_no_active(self, seeded_window, monkeypatch):
        monkeypatch.setattr("opensak.db.manager.get_db_manager",
                            lambda: SimpleNamespace(active=None))
        seeded_window._load_sort_for_active_db()

    def test_load_sort_with_saved_profile(self, seeded_window, monkeypatch, iso_settings):
        from opensak.db.manager import get_db_manager
        from opensak.filters.engine import FilterSet, SortSpec
        from opensak.settings_store import get_store
        key = f"sort.{get_db_manager().active.path}"
        get_store().set(f"{key}.filter_profile", "MyProfile")
        prof = SimpleNamespace(name="MyProfile", filterset=FilterSet(),
                               sort=SortSpec("name"))
        monkeypatch.setattr("opensak.filters.engine.FilterProfile.list_profiles",
                            staticmethod(lambda: [Path("/x/p.json")]))
        monkeypatch.setattr("opensak.filters.engine.FilterProfile.load",
                            staticmethod(lambda p: prof))
        seeded_window._load_sort_for_active_db()
        assert seeded_window._active_filter_name == "MyProfile"

    def test_load_sort_unknown_field_falls_back(self, seeded_window, iso_settings):
        # Regression for #498: opensak.json deles på tværs af alle installerede
        # versioner. Hvis en nyere version har gemt et sort-felt denne version
        # ikke kender (fx "some_future_field"), må opstart IKKE crashe — den skal
        # falde tilbage til "name" og rette den gemte værdi.
        from opensak.db.manager import get_db_manager
        from opensak.settings_store import get_store
        key = f"sort.{get_db_manager().active.path}"
        get_store().set(f"{key}.field", "some_future_field")
        seeded_window._load_sort_for_active_db()
        assert seeded_window._current_sort.field == "name"
        assert get_store().get(f"{key}.field") == "name"

    def test_load_sort_profile_load_error(self, seeded_window, monkeypatch, iso_settings):
        from opensak.db.manager import get_db_manager
        from opensak.settings_store import get_store
        key = f"sort.{get_db_manager().active.path}"
        get_store().set(f"{key}.filter_profile", "Ghost")
        monkeypatch.setattr("opensak.filters.engine.FilterProfile.list_profiles",
                            staticmethod(lambda: [Path("/x/p.json")]))
        monkeypatch.setattr(
            "opensak.filters.engine.FilterProfile.load",
            staticmethod(lambda p: (_ for _ in ()).throw(RuntimeError("bad"))))
        seeded_window._load_sort_for_active_db()  # except → reset to no filter
        assert seeded_window._active_filter_name == ""


# ── filter dialog / profiles ──────────────────────────────────────────────────

class TestFilters:
    def test_open_filter_dialog(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.filter_dialog.FilterDialog",
            fake_dialog(signals=("filter_applied", "profile_deleted")))
        seeded_window._open_filter_dialog()

    def test_open_filter_blocked_by_trip(self, seeded_window):
        seeded_window._trip_planner_win = SimpleNamespace(
            isVisible=lambda: True, raise_=lambda: None, activateWindow=lambda: None)
        seeded_window._open_filter_dialog()

    def test_on_filter_applied(self, seeded_window):
        from opensak.filters.engine import FilterSet, SortSpec
        seeded_window._on_filter_applied(FilterSet(), SortSpec("name"), "Prof")
        assert seeded_window._active_filter_name == "Prof"

    def test_on_filter_applied_single(self, seeded_window):
        from opensak.filters.engine import FilterSet, SortSpec, GcCodeFilter
        fs = FilterSet(mode="AND")
        fs.add(GcCodeFilter("GC12345"))
        seeded_window._on_filter_applied(fs, SortSpec("name"), "")

    def test_set_clear_filter_active(self, seeded_window):
        seeded_window._set_clear_filter_active(True)
        assert seeded_window._btn_clear_filter.isEnabled()
        # Issue #559: the button previously had no visible hover feedback
        # when active, making it look non-interactive compared to the rest
        # of the toolbar. Assert the hover rule now exists.
        assert ":hover" in seeded_window._btn_clear_filter.styleSheet()

        seeded_window._set_clear_filter_active(False)
        assert not seeded_window._btn_clear_filter.isEnabled()
        # No hover rule while inactive — it's deliberately non-interactive.
        assert ":hover" not in seeded_window._btn_clear_filter.styleSheet()

    def test_clear_filter(self, seeded_window):
        seeded_window._clear_filter()

    def test_populate_filter_profile_combo(self, seeded_window):
        seeded_window._populate_filter_profile_combo()

    def test_populate_combo_load_error(self, seeded_window, monkeypatch):
        monkeypatch.setattr("opensak.filters.engine.FilterProfile.list_profiles",
                            staticmethod(lambda: [Path("/x/p.json")]))
        monkeypatch.setattr(
            "opensak.filters.engine.FilterProfile.load",
            staticmethod(lambda p: (_ for _ in ()).throw(RuntimeError("bad"))))
        seeded_window._populate_filter_profile_combo()

    def test_filter_profile_combo_changed_none(self, seeded_window):
        seeded_window._on_filter_profile_combo_changed(0)  # → clear_filter

    def test_filter_profile_combo_changed_none_data(self, seeded_window):
        seeded_window._filter_profile_combo.addItem("X", userData=None)
        idx = seeded_window._filter_profile_combo.count() - 1
        seeded_window._on_filter_profile_combo_changed(idx)  # path None → return

    def test_filter_profile_combo_changed_single(self, seeded_window, monkeypatch):
        from opensak.filters.engine import FilterSet, SortSpec, GcCodeFilter
        fs = FilterSet(mode="AND")
        fs.add(GcCodeFilter("GC12345"))
        prof = SimpleNamespace(name="P1", filterset=fs, sort=SortSpec("name"))
        monkeypatch.setattr("opensak.filters.engine.FilterProfile.load",
                            staticmethod(lambda p: prof))
        seeded_window._filter_profile_combo.addItem("P1", userData=Path("/x/p.json"))
        idx = seeded_window._filter_profile_combo.count() - 1
        seeded_window._on_filter_profile_combo_changed(idx)
        assert seeded_window._cache_table.row_count() == 1

    def test_filter_profile_combo_changed_profile(self, seeded_window, monkeypatch):
        from opensak.filters.engine import FilterSet, SortSpec
        prof = SimpleNamespace(name="P", filterset=FilterSet(), sort=SortSpec("name"))
        monkeypatch.setattr("opensak.filters.engine.FilterProfile.load",
                            staticmethod(lambda p: prof))
        seeded_window._filter_profile_combo.addItem("P", userData=Path("/x/p.json"))
        idx = seeded_window._filter_profile_combo.count() - 1
        seeded_window._on_filter_profile_combo_changed(idx)
        assert seeded_window._active_filter_name == "P"

    def test_filter_profile_combo_changed_load_error(self, seeded_window, monkeypatch):
        def boom(p):
            raise RuntimeError("bad")
        monkeypatch.setattr("opensak.filters.engine.FilterProfile.load",
                            staticmethod(boom))
        seeded_window._filter_profile_combo.addItem("P", userData=Path("/x/p.json"))
        idx = seeded_window._filter_profile_combo.count() - 1
        seeded_window._on_filter_profile_combo_changed(idx)

    def test_status_filter_leaves_combo_showing_none(self, seeded_window):
        # Regression for #553: a status-bar-triggered filter (e.g. clicking
        # the yellow "Found" count) applies via _on_filter_applied() with a
        # translated UI label ("Found") rather than a saved profile name.
        # _populate_filter_profile_combo() can't find "Found" among the
        # saved profiles, so it falls back to index 0 ("None") even though a
        # filter is actively applied behind the scenes.
        from opensak.filters.engine import FilterSet, GcCodeFilter, SortSpec
        fs = FilterSet(mode="AND")
        fs.add(GcCodeFilter("GC12345"))
        seeded_window._on_filter_applied(fs, SortSpec("name"), "Found")
        assert seeded_window._filter_profile_combo.currentIndex() == 0
        assert len(seeded_window._current_filterset) > 0

    def test_filter_profile_combo_uses_activated_not_current_index_changed(
        self, seeded_window
    ):
        # Regression for #553: after a status filter leaves the combo showing
        # "None" (see test above), the user re-selecting "None" from the
        # dropdown does not change its index (it's already 0), so
        # currentIndexChanged never fires and the filter used to stay stuck.
        # activated() fires on every user selection regardless of whether the
        # index changed, so it must be the signal driving
        # _on_filter_profile_combo_changed.
        from opensak.filters.engine import FilterSet, GcCodeFilter, SortSpec
        fs = FilterSet(mode="AND")
        fs.add(GcCodeFilter("GC12345"))
        seeded_window._on_filter_applied(fs, SortSpec("name"), "Found")
        assert len(seeded_window._current_filterset) > 0
        seeded_window._filter_profile_combo.activated.emit(0)
        assert len(seeded_window._current_filterset) == 0
        assert seeded_window._active_filter_name == ""

    def test_clear_filter_action_has_escape_shortcut(self, seeded_window):
        # Regression for #553: Escape should clear the active filter,
        # matching GSAK muscle memory (Mike Wood's request).
        assert seeded_window._act_clear_filter.shortcut() == QKeySequence("Escape")

    def test_clear_filter_shortcut_is_registered_and_configurable(self, seeded_window):
        # The Escape shortcut must go through the same configurable
        # shortcut-registry as every other shortcut, so it shows up in the
        # Keyboard Shortcuts dialog and can be remapped by the user.
        entry = next(
            e for e in seeded_window._shortcut_registry if e[0] == "clear_filter"
        )
        _key, label_key, actions = entry
        assert label_key == "shortcut_clear_filter"
        assert seeded_window._act_clear_filter in actions


# ── tool/export dialogs ───────────────────────────────────────────────────────

class TestToolDialogs:
    def test_open_column_chooser(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.column_dialog.ColumnChooserDialog",
            fake_dialog(exec_result=1))
        seeded_window._open_column_chooser()

    def test_open_gps_export(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.gps_dialog.GpsExportDialog", fake_dialog())
        seeded_window._open_gps_export()

    def test_open_file_export(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.file_export_dialog.FileExportDialog", fake_dialog())
        seeded_window._open_file_export()

    def test_open_file_export_no_caches(self, empty_window, mbox_ok):
        empty_window._open_file_export()

    def test_open_kml_export(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.kml_export_dialog.KmlExportDialog", fake_dialog())
        seeded_window._open_kml_export()

    def test_open_kml_export_no_caches(self, empty_window, mbox_ok):
        empty_window._open_kml_export()

    def test_open_found_updater(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.found_dialog.FoundUpdaterDialog",
            fake_dialog(signals=("update_completed",)))
        seeded_window._open_found_updater()

    def test_open_update_location(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.update_location_dialog.UpdateLocationDialog",
            fake_dialog(signals=("location_updated",)))
        seeded_window._open_update_location()

    @pytest.mark.parametrize("attr,mod,cls", [
        ("_open_coord_converter", "coord_converter_dialog", "CoordConverterDialog"),
        ("_open_projection", "projection_dialog", "ProjectionDialog"),
        ("_open_checksum", "checksum_dialog", "ChecksumDialog"),
        ("_open_midpoint", "midpoint_dialog", "MidpointDialog"),
        ("_open_dist_bearing", "distance_bearing_dialog", "DistanceBearingDialog"),
    ])
    def test_open_coord_tools_with_selection(self, seeded_window, monkeypatch, attr, mod, cls):
        monkeypatch.setattr(f"opensak.gui.dialogs.{mod}.{cls}", fake_dialog())
        seeded_window._cache_table.select_by_gc_code("GC12345")
        getattr(seeded_window, attr)()

    @pytest.mark.parametrize("attr,mod,cls", [
        ("_open_coord_converter", "coord_converter_dialog", "CoordConverterDialog"),
        ("_open_projection", "projection_dialog", "ProjectionDialog"),
        ("_open_checksum", "checksum_dialog", "ChecksumDialog"),
        ("_open_midpoint", "midpoint_dialog", "MidpointDialog"),
        ("_open_dist_bearing", "distance_bearing_dialog", "DistanceBearingDialog"),
    ])
    def test_open_coord_tools_no_selection(self, seeded_window, monkeypatch, attr, mod, cls):
        monkeypatch.setattr(f"opensak.gui.dialogs.{mod}.{cls}", fake_dialog())
        seeded_window._cache_table.clearSelection()
        getattr(seeded_window, attr)()


class TestTripBlocked:
    # Every guarded opener short-circuits when the Trip Planner is open.

    @pytest.fixture
    def blocked(self, seeded_window):
        seeded_window._trip_planner_win = SimpleNamespace(
            isVisible=lambda: True, raise_=lambda: None, activateWindow=lambda: None)
        return seeded_window

    @pytest.mark.parametrize("method", [
        "_open_settings", "_open_column_chooser", "_open_gps_export",
        "_open_kml_export", "_open_found_updater", "_open_update_location",
        "_open_coord_converter", "_open_projection", "_open_checksum",
        "_open_midpoint", "_open_dist_bearing",
    ])
    def test_blocked(self, blocked, method):
        getattr(blocked, method)()  # warns, returns without constructing dialog

    def test_file_export_blocked(self, blocked):
        blocked._open_file_export()


class TestHomeAndCwExtra:
    def test_reload_home_combo_syncs_home_coords(self, seeded_window):
        # Regression: _reload_home_combo blocked signals, so home_lat/lon were
        # never written on startup/db-switch, causing distances from Copenhagen.
        from opensak.gui.settings import HomePoint, get_settings
        s = get_settings()
        p = HomePoint("Idaho", 43.5, -116.2)
        s.add_or_update_home_point(p)
        s.active_home_name = p.name  # name only — lat/lon not yet written
        assert s.home_lat == pytest.approx(55.6761)  # Copenhagen default
        seeded_window._reload_home_combo()
        assert s.home_lat == pytest.approx(43.5)
        assert s.home_lon == pytest.approx(-116.2)

    def test_reload_home_combo_syncs_star_home_coords(self, seeded_window):
        # ★ Home coordinates come from gc_home_location, not homepoints.list.
        from opensak.gui.settings import get_settings
        s = get_settings()
        s.gc_home_location = "N43 30.000 W116 12.000"
        s.active_home_name = "★ Home"
        seeded_window._reload_home_combo()
        assert s.home_lat == pytest.approx(43.5, abs=0.01)
        assert s.home_lon == pytest.approx(-116.2, abs=0.01)

    def test_on_home_changed_user_and_gc_home(self, seeded_window, iso_settings):
        from opensak.gui.settings import HomePoint, get_settings
        s = get_settings()
        s.gc_home_location = "N55 40.566 E012 34.098"
        s.home_points = [HomePoint("Work", 56.0, 10.0)]
        seeded_window._reload_home_combo()
        visited = 0
        for i in range(seeded_window._home_combo.count()):
            if seeded_window._home_combo.itemData(i):
                seeded_window._on_home_changed(i)
                visited += 1
        assert visited >= 2  # "★ Home" + "Work"

    def test_next_cw_id_increments(self, seeded_window, monkeypatch):
        monkeypatch.setattr(
            "opensak.gui.dialogs.waypoint_dialog.WaypointDialog",
            fake_dialog(exec_result=1, data=_wp_data("CW001")))
        seeded_window._add_waypoint()
        assert seeded_window._next_cw_id() == "CW002"

    def test_edit_waypoint_with_selection(self, seeded_window, monkeypatch):
        seeded_window._cache_table.select_by_gc_code("GC12345")
        monkeypatch.setattr(
            "opensak.gui.dialogs.waypoint_dialog.WaypointDialog",
            fake_dialog(exec_result=0))
        seeded_window._edit_waypoint()


# ── trip planner ──────────────────────────────────────────────────────────────

class TestTripPlanner:
    def test_open_trip_planner(self, seeded_window, monkeypatch):
        class FakeTrip:
            def __init__(self, *a, **k):
                self._visible = False

            def show(self):
                self._visible = True

            def raise_(self):
                pass

            def activateWindow(self):
                pass

            def isVisible(self):
                return self._visible
        monkeypatch.setattr(
            "opensak.gui.dialogs.trip_dialog.TripPlannerDialog", FakeTrip)
        seeded_window._open_trip_planner()
        assert seeded_window._trip_planner_win is not None

    def test_open_trip_planner_already_open(self, seeded_window, monkeypatch):
        raised = []
        seeded_window._trip_planner_win = SimpleNamespace(
            isVisible=lambda: True,
            raise_=lambda: raised.append("r"),
            activateWindow=lambda: None)
        monkeypatch.setattr(
            "opensak.gui.dialogs.trip_dialog.TripPlannerDialog",
            lambda *a, **k: pytest.fail("should not construct"))
        seeded_window._open_trip_planner()
        assert raised == ["r"]

    def test_warn_trip_planner_active(self, seeded_window):
        seeded_window._trip_planner_win = SimpleNamespace(
            raise_=lambda: None, activateWindow=lambda: None)
        seeded_window._warn_trip_planner_active()


# ── about / updates ───────────────────────────────────────────────────────────

class TestAboutUpdates:
    def test_show_about(self, seeded_window, mbox_ok):
        seeded_window._show_about()

    def test_check_update_manual(self, seeded_window, monkeypatch):
        monkeypatch.setattr("opensak.gui.mainwindow.UpdateCheckWorker", fake_worker())
        seeded_window._check_update_manual()

    def test_on_manual_check_done_no_update(self, seeded_window, mbox_ok):
        seeded_window._manual_found_update = False
        seeded_window._on_manual_check_done()

    def test_on_manual_check_done_found(self, seeded_window):
        seeded_window._manual_found_update = True
        seeded_window._on_manual_check_done()  # no dialog

    def test_on_update_available_manual(self, seeded_window, mbox_ok):
        seeded_window._on_update_available("v9.9.9", "http://x", manual=True)

    def test_on_update_available_auto(self, seeded_window, mbox_ok):
        seeded_window._on_update_available("v9.9.9", "http://x", manual=False)

    def test_on_update_available_skipped(self, seeded_window, mbox_ok, iso_settings):
        from opensak.gui.settings import get_settings
        get_settings().updates_skipped_version = "v1.2.3"
        seeded_window._on_update_available("v1.2.3", "http://x", manual=False)

    def _click_update_dialog_button(self, monkeypatch, button_text):
        """Simulate clicking a specific button by its (translated) text."""
        def _clicked(self):
            for btn in self.buttons():
                if btn.text() == button_text:
                    return btn
            raise AssertionError(f"no button with text {button_text!r} found")

        monkeypatch.setattr(icon_mod.QMessageBox, "exec", lambda self: None)
        monkeypatch.setattr(icon_mod.QMessageBox, "clickedButton", _clicked)

    def test_on_update_available_open_releases_opens_url(self, seeded_window, monkeypatch):
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
        self._click_update_dialog_button(monkeypatch, tr("update_open_releases"))

        seeded_window._on_update_available("v9.9.9", "http://example.com/release", manual=True)

        assert opened == ["http://example.com/release"]

    def test_on_update_available_skip_sets_skipped_version(
        self, seeded_window, monkeypatch, iso_settings
    ):
        from opensak.gui.settings import get_settings
        self._click_update_dialog_button(monkeypatch, tr("update_skip_version"))

        seeded_window._on_update_available("v9.9.9", "http://x", manual=True)

        assert get_settings().updates_skipped_version == "v9.9.9"

    def test_on_update_available_support_button_opens_support_page(
        self, seeded_window, monkeypatch
    ):
        # New "Support OpenSAK" button on the update-available dialog:
        # a more visible spot than the Help menu, which users who never
        # open Help would otherwise never see.
        calls = []
        monkeypatch.setattr(seeded_window, "_open_support_page", lambda: calls.append(True))
        self._click_update_dialog_button(monkeypatch, tr("action_support_opensak"))

        seeded_window._on_update_available("v9.9.9", "http://x", manual=True)

        assert calls == [True]

    def test_on_update_available_support_button_does_not_skip_or_open_releases(
        self, seeded_window, monkeypatch, iso_settings
    ):
        # Clicking Support must not also record a skipped version or open
        # the release page — it's an independent, non-committal action.
        from opensak.gui.settings import get_settings
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
        monkeypatch.setattr(seeded_window, "_open_support_page", lambda: None)
        self._click_update_dialog_button(monkeypatch, tr("action_support_opensak"))

        seeded_window._on_update_available("v9.9.9", "http://x", manual=True)

        assert opened == []
        assert get_settings().updates_skipped_version != "v9.9.9"

    def test_on_update_available_changelog_link_uses_tag_not_branch(
        self, seeded_window, monkeypatch
    ):
        """Regression for the bug fixed in beta.10: the changelog link must
        always pin to the immutable release tag, never a moving branch name
        like 'main' or 'beta'. A branch-HEAD link drifts as soon as that
        branch moves on, so beta testers ended up seeing the stable
        changelog instead of the beta entry they were told about — exactly
        what a hardcoded 'blob/main/CHANGELOG.md' caused before. Fail this
        test before reinstating any literal branch name in the link.
        """
        captured: dict[str, str] = {}

        def fake_exec(self):
            captured["informative_text"] = self.informativeText()
            return icon_mod.QMessageBox.StandardButton.Ok

        monkeypatch.setattr(icon_mod.QMessageBox, "exec", fake_exec)

        seeded_window._on_update_available(
            "v1.14.0-beta.12", "http://x", is_prerelease=True, manual=True
        )

        text = captured["informative_text"]
        assert "blob/v1.14.0-beta.12/CHANGELOG.md" in text
        assert "blob/main/" not in text
        assert "blob/beta/" not in text
