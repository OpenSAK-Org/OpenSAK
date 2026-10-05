# tests/unit-tests/test_gsak_import_dialog.py — GSAK import dialog worker + UI (#469 session 4, multi-database backups).

import contextlib
import importlib.util
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak.gui.dialogs import gsak_import_dialog as gdlg
from opensak.gui.dialogs.gsak_import_dialog import GsakImportWorker, GsakImportDialog


def _result(created=1, updated=0, waypoints=0, attributes=0, logs=0, notes=0,
            note_images_replaced=0, trackables=0, corrected=0, skipped=0,
            warnings=None, errors=None):
    return SimpleNamespace(
        created=created, updated=updated, waypoints=waypoints, attributes=attributes,
        logs=logs, notes=notes, note_images_replaced=note_images_replaced,
        trackables=trackables,
        corrected=corrected, skipped=skipped, warnings=warnings or [], errors=errors or [],
    )


@contextlib.contextmanager
def _fake_session():
    yield MagicMock()


# ── GsakImportWorker.run ──────────────────────────────────────────────────────

class TestGsakImportWorker:
    def _patch_common(self, monkeypatch, active_path=Path("/active.db")):
        monkeypatch.setattr("opensak.db.manager.get_db_manager",
                            lambda: SimpleNamespace(active_path=active_path))
        monkeypatch.setattr("opensak.db.database.get_session", _fake_session)
        updated = []
        monkeypatch.setattr(GsakImportWorker, "_update_distances",
                            staticmethod(updated.append))
        return updated

    def test_run_success_emits_result(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.import_gsak_db",
            lambda path, session, progress_cb=None: _result(created=48),
        )
        w = GsakImportWorker(Path("/gsak.db3"))
        got = []
        w.result_ready.connect(lambda r: got.append(r))
        w.run()
        assert len(got) == 1 and got[0].created == 48

    def test_run_reports_progress(self, monkeypatch):
        self._patch_common(monkeypatch)

        def fake_import(path, session, progress_cb=None):
            progress_cb(5, 10)
            return _result()

        monkeypatch.setattr("opensak.importer.gsak_importer.import_gsak_db", fake_import)
        w = GsakImportWorker(Path("/gsak.db3"))
        seen = []
        w.progress.connect(lambda done, total: seen.append((done, total)))
        w.run()
        assert seen == [(5, 10)]

    def test_run_exception_emits_error(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.import_gsak_db",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        w = GsakImportWorker(Path("/gsak.db3"))
        errs = []
        w.error.connect(lambda m: errs.append(m))
        w.run()
        assert errs and "boom" in errs[0]

    def test_run_other_db_uses_private_session(self, monkeypatch):
        # Another target DB gets its own session; the active (global) engine
        # is never swapped from the worker thread.
        self._patch_common(monkeypatch, active_path=Path("/active.db"))
        monkeypatch.setattr("opensak.importer.gsak_importer.import_gsak_db",
                            lambda path, session, progress_cb=None: _result())
        inits, opened = [], []
        monkeypatch.setattr("opensak.db.database.init_db", lambda **k: inits.append(k.get("db_path")))
        monkeypatch.setattr("opensak.db.database.get_session",
                            lambda: pytest.fail("active DB session used for another DB"))
        monkeypatch.setattr("opensak.db.database.session_for",
                            lambda p: opened.append(p) or _fake_session())
        w = GsakImportWorker(Path("/gsak.db3"), target_db_path=Path("/other.db"))
        w.run()
        assert opened == [Path("/other.db")]
        assert inits == []

    def test_run_active_target_uses_active_session(self, monkeypatch):
        self._patch_common(monkeypatch, active_path=Path("/active.db"))
        monkeypatch.setattr("opensak.importer.gsak_importer.import_gsak_db",
                            lambda path, session, progress_cb=None: _result())
        inits = []
        monkeypatch.setattr("opensak.db.database.init_db", lambda **k: inits.append(k.get("db_path")))
        monkeypatch.setattr("opensak.db.database.session_for",
                            lambda p: pytest.fail("private session opened for the active DB"))
        w = GsakImportWorker(Path("/gsak.db3"), target_db_path=Path("/active.db"))
        w.run()
        assert inits == []

    def test_run_replace_clears_target_first(self, monkeypatch):
        self._patch_common(monkeypatch)
        calls = []
        monkeypatch.setattr("opensak.importer.gsak_importer.clear_opensak_cache_data",
                            lambda session: calls.append("clear") or 7)
        monkeypatch.setattr("opensak.importer.gsak_importer.import_gsak_db",
                            lambda path, session, progress_cb=None: calls.append("import") or _result())
        w = GsakImportWorker(Path("/gsak.db3"), replace=True)
        cleared = []
        w.cleared.connect(cleared.append)
        w.run()
        assert calls == ["clear", "import"]
        assert cleared == [7]

    def test_run_updates_distances_of_target(self, monkeypatch):
        updated = self._patch_common(monkeypatch, active_path=Path("/active.db"))
        monkeypatch.setattr("opensak.importer.gsak_importer.import_gsak_db",
                            lambda path, session, progress_cb=None: _result())
        monkeypatch.setattr("opensak.db.database.session_for", lambda p: _fake_session())
        GsakImportWorker(Path("/gsak.db3"), target_db_path=Path("/other.db")).run()
        GsakImportWorker(Path("/gsak.db3")).run()  # no target → the active DB
        assert updated == [Path("/other.db"), Path("/active.db")]

    def test_run_failed_import_does_not_update_distances(self, monkeypatch):
        updated = self._patch_common(monkeypatch)
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.import_gsak_db",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        GsakImportWorker(Path("/gsak.db3")).run()
        assert updated == []

    def test_update_distances_failure_is_only_logged(self, monkeypatch, caplog):
        def boom(*a, **k):
            raise RuntimeError("no distances")
        monkeypatch.setattr("opensak.db.database.distances_up_to_date", boom)
        GsakImportWorker._update_distances(Path("/other.db"))  # must not raise
        assert "could not update distances" in caplog.text

    def test_run_without_replace_does_not_clear(self, monkeypatch):
        self._patch_common(monkeypatch)
        calls = []
        monkeypatch.setattr("opensak.importer.gsak_importer.clear_opensak_cache_data",
                            lambda session: calls.append("clear") or 0)
        monkeypatch.setattr("opensak.importer.gsak_importer.import_gsak_db",
                            lambda path, session, progress_cb=None: _result())
        GsakImportWorker(Path("/gsak.db3")).run()
        assert calls == []


# ── GsakImportDialog ──────────────────────────────────────────────────────────
#
# These run against the real (test-isolated, see conftest) database manager
# and real synthetic GSAK databases, so the whole path — listing the backup,
# unpacking, creating/overwriting OpenSAK databases, cleanup — is exercised.

_spec = importlib.util.spec_from_file_location(
    "_gsak_importer_tests", Path(__file__).with_name("test_gsak_importer.py")
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)


def _gsak_db(path: Path, code: str = "GC1TEST") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return _helpers._make_gsak_db(
        path, caches=[{"Code": code}],
        memos=[{"Code": code, "Url": f"https://coord.info/{code}"}],
    )


def _codes(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT gc_code FROM caches"))
    finally:
        conn.close()


def _backup(tmp_path: Path, dbs: dict[str, str], with_settings: bool = True) -> Path:
    """Zip with one <name>/sqlite.db3 per entry (the value is its cache code)."""
    archive = tmp_path / "GSAKAuto1.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, code in dbs.items():
            zf.write(_gsak_db(tmp_path / "src" / name / "sqlite.db3", code), f"{name}/sqlite.db3")
        if with_settings:
            zf.writestr("gsak.db3", b"settings")
    return archive


def _seed(manager, db_info, tmp_path: Path, code: str) -> None:
    """Put one cache into an existing OpenSAK database, then restore the active one."""
    from opensak.db.database import get_session, init_db
    from opensak.importer.gsak_importer import import_gsak_db
    init_db(db_path=db_info.path)
    with get_session() as s:
        import_gsak_db(_gsak_db(tmp_path / "seed" / "sqlite.db3", code), s)
    manager.ensure_active_initialised()


@pytest.fixture
def manager():
    from opensak.db.manager import get_db_manager
    mgr = get_db_manager()
    mgr.ensure_active_initialised()
    return mgr


@pytest.fixture
def dlg(qtbot, manager):
    d = GsakImportDialog()
    qtbot.addWidget(d)
    return d


class FakeBox:
    """Stand-in for QMessageBox that 'clicks' the button labelled ``choice``."""
    choice = None
    instances: list = []
    Icon = gdlg.QMessageBox.Icon
    ButtonRole = gdlg.QMessageBox.ButtonRole
    StandardButton = gdlg.QMessageBox.StandardButton
    critical = staticmethod(lambda *a, **k: None)
    warning = staticmethod(lambda *a, **k: None)

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
    monkeypatch.setattr(gdlg, "QMessageBox", FakeBox)
    return FakeBox


@pytest.fixture
def sync_workers(monkeypatch):
    """Run the dialog's workers on the calling thread.

    Real QThreads are unreliable inside the full suite (a thread's
    ``finished`` was seen arriving a full minute late after ~2000 other
    tests), so — like the worker tests above, which call run() directly —
    start() just runs the work and then reports completion.
    """
    def _start(self):
        self.run()
        self.finished.emit()

    monkeypatch.setattr(gdlg.GsakImportWorker, "start", _start)
    monkeypatch.setattr(gdlg.GsakExtractWorker, "start", _start)


def _run(dlg, qtbot):
    dlg._start_import()
    assert gdlg.tr("gsak_import_done") in dlg._log.toPlainText(), dlg._log.toPlainText()


def _target(dlg, row):
    return dlg._table.cellWidget(row, gdlg.COL_TARGET)


def _db_by_name(manager, name):
    return {db.name: db for db in manager.databases}[name]


class TestGsakImportDialogListing:
    def test_zip_lists_every_database_named_after_its_folder(self, dlg, tmp_path):
        dlg.set_path(_backup(tmp_path, {"AllCH": "GC1", "AdventureLabs": "GC2"}))
        assert dlg._table.rowCount() == 2
        names = [dlg._table.item(r, gdlg.COL_GSAK).text() for r in range(2)]
        assert names == ["AdventureLabs", "AllCH"]
        assert [_target(dlg, r).currentText() for r in range(2)] == names
        assert all(dlg._table.item(r, gdlg.COL_GSAK).checkState() == Qt.CheckState.Checked
                   for r in range(2))
        assert dlg._table.item(0, gdlg.COL_STATUS).text() == gdlg.tr("gsak_import_status_new")
        assert not dlg._filters_cb.isHidden() and dlg._filters_cb.isChecked()
        assert dlg._import_btn.isEnabled()

    def test_filters_checkbox_hidden_without_gsak_db3(self, dlg, tmp_path):
        dlg.set_path(_backup(tmp_path, {"AllCH": "GC1"}, with_settings=False))
        assert dlg._filters_cb.isHidden()

    def test_single_db3_file_is_one_row(self, dlg, tmp_path):
        dlg.set_path(_gsak_db(tmp_path / "data" / "Sommerhus" / "sqlite.db3"))
        assert dlg._table.rowCount() == 1
        assert _target(dlg, 0).currentText() == "Sommerhus"
        assert dlg._import_btn.isEnabled()

    def test_existing_target_is_flagged(self, dlg, manager, tmp_path):
        manager.new_database("AllCH")
        dlg.set_path(_backup(tmp_path, {"allch": "GC1"}))  # case-insensitive match
        assert dlg._table.item(0, gdlg.COL_STATUS).text() == gdlg.tr("file_locations_col_exists")
        _target(dlg, 0).setCurrentText("Something else")
        assert dlg._table.item(0, gdlg.COL_STATUS).text() == gdlg.tr("gsak_import_status_new")

    def test_zip_without_gsak_content_shows_error(self, dlg, tmp_path, monkeypatch):
        archive = tmp_path / "x.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("readme.txt", "nothing")
        shown = []
        monkeypatch.setattr(gdlg.QMessageBox, "critical", lambda *a, **k: shown.append(a))
        dlg.set_path(archive)
        assert shown
        assert dlg._import_btn.isEnabled() is False

    def test_nothing_ticked_disables_import_unless_filters_wanted(self, dlg, tmp_path):
        dlg.set_path(_backup(tmp_path, {"A": "GC1"}))
        dlg._check_all(False)
        assert dlg._import_btn.isEnabled()          # filters still ticked
        dlg._filters_cb.setChecked(False)
        assert dlg._import_btn.isEnabled() is False
        dlg._check_all(True)
        assert dlg._import_btn.isEnabled()

    def test_browse_sets_path(self, dlg, monkeypatch, tmp_path):
        archive = _backup(tmp_path, {"A": "GC1"})
        monkeypatch.setattr(gdlg.QFileDialog, "getOpenFileName",
                            lambda *a, **k: (str(archive), "f"))
        dlg._browse()
        assert dlg._selected_path == archive
        assert dlg._table.rowCount() == 1

    def test_browse_cancel_leaves_path_unset(self, dlg, monkeypatch):
        monkeypatch.setattr(gdlg.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
        dlg._browse()
        assert dlg._selected_path is None
        assert dlg._import_btn.isEnabled() is False

    def test_start_import_no_path_noop(self, dlg):
        dlg._start_import()
        assert dlg._progress.isVisible() is False


class TestGsakImportDialogConfirmExisting:
    def _jobs(self):
        return [
            gdlg.GsakImportJob("A", "Target", gdlg.MODE_MERGE),
            gdlg.GsakImportJob("B", "Target", gdlg.MODE_MERGE),
            gdlg.GsakImportJob("C", "Fresh", gdlg.MODE_NEW),
        ]

    def test_no_existing_targets_asks_nothing(self, dlg, fake_box):
        jobs = [gdlg.GsakImportJob("C", "Fresh", gdlg.MODE_NEW)]
        assert dlg._confirm_existing(jobs) == jobs
        assert fake_box.instances == []

    def test_overwrite_clears_each_target_once(self, dlg, fake_box, monkeypatch):
        monkeypatch.setattr(gdlg, "tr", lambda key, **kw: f"{key} {kw}" if kw else key)
        fake_box.choice = gdlg.tr("gsak_import_existing_overwrite")
        jobs = dlg._confirm_existing(self._jobs())
        assert [(j.mode, j.replace) for j in jobs] == [
            (gdlg.MODE_REPLACE, True), (gdlg.MODE_REPLACE, False), (gdlg.MODE_NEW, False),
        ]
        assert "Target" in fake_box.instances[0].text

    def test_merge_keeps_jobs(self, dlg, fake_box):
        fake_box.choice = gdlg.tr("gsak_import_existing_merge")
        jobs = dlg._confirm_existing(self._jobs())
        assert [j.mode for j in jobs] == [gdlg.MODE_MERGE, gdlg.MODE_MERGE, gdlg.MODE_NEW]
        assert not any(j.replace for j in jobs)

    def test_skip_drops_existing(self, dlg, fake_box):
        fake_box.choice = gdlg.tr("gsak_import_existing_skip")
        assert [j.gsak_name for j in dlg._confirm_existing(self._jobs())] == ["C"]

    def test_cancel_aborts(self, dlg, fake_box):
        fake_box.choice = "cancel"
        assert dlg._confirm_existing(self._jobs()) is None


@pytest.mark.usefixtures("sync_workers")
class TestGsakImportDialogRun:
    def test_backup_creates_one_database_per_folder_and_cleans_up(
            self, dlg, manager, tmp_path, qtbot, monkeypatch):
        monkeypatch.setattr(gdlg, "tr", lambda key, **kw: f"{key} {kw}" if kw else key)
        filter_calls = []
        monkeypatch.setattr(dlg, "_run_filter_import",
                            lambda p: filter_calls.append((p, p.exists(), p.read_bytes())))
        signals = {"completed": 0, "dbs": 0}
        dlg.import_completed.connect(lambda: signals.__setitem__("completed", signals["completed"] + 1))
        dlg.databases_changed.connect(lambda: signals.__setitem__("dbs", signals["dbs"] + 1))

        dlg.set_path(_backup(tmp_path, {"AllCH": "GC1AAA", "Holidays": "GC2BBB"}))
        _run(dlg, qtbot)

        assert _codes(_db_by_name(manager, "AllCH").path) == ["GC1AAA"]
        assert _codes(_db_by_name(manager, "Holidays").path) == ["GC2BBB"]
        assert _codes(manager.active_path) == []      # active DB untouched
        assert signals == {"completed": 1, "dbs": 2}   # one per created database
        # gsak.db3 was unpacked and handed to the filter import …
        assert len(filter_calls) == 1
        path, existed, data = filter_calls[0]
        assert existed and data == b"settings"
        # … and the whole temp folder is gone afterwards
        assert dlg._temp_dir is None
        assert not path.exists() and not path.parent.parent.exists()
        assert "'AllCH → AllCH'" in dlg._log.toPlainText()

    def test_distances_are_calculated_for_each_target_database(
            self, dlg, manager, tmp_path, qtbot):
        """GsakImportWorker brings each target's distances up to date in the
        background, with the centre stored in that database's own settings —
        so switching to it later doesn't recalculate on the GUI thread."""
        from opensak.db import db_settings
        from opensak.db.database import distances_up_to_date, init_db
        from opensak.gui.settings import get_settings
        active_before = db_settings.read_file(manager.active_path)

        dlg.set_path(_backup(tmp_path, {"AllCH": "GC1AAA"}, with_settings=False))
        _run(dlg, qtbot)

        path = _db_by_name(manager, "AllCH").path
        lat, lon = get_settings().home_for_db_file(path)
        conn = sqlite3.connect(path)
        try:
            distances = [r[0] for r in conn.execute("SELECT distance FROM caches")]
        finally:
            conn.close()
        assert distances and None not in distances
        assert db_settings.read_file(path)["dist_calc_lat"] == lat
        assert db_settings.read_file(manager.active_path) == active_before
        init_db(db_path=path)
        try:
            assert distances_up_to_date(lat, lon, db_path=path)
        finally:
            manager.ensure_active_initialised()

    def test_only_ticked_databases_are_imported(self, dlg, manager, tmp_path, qtbot):
        dlg.set_path(_backup(tmp_path, {"A": "GC1AAA", "B": "GC2BBB"}, with_settings=False))
        dlg._table.item(1, gdlg.COL_GSAK).setCheckState(Qt.CheckState.Unchecked)
        _run(dlg, qtbot)
        names = {db.name for db in manager.databases}
        assert "A" in names and "B" not in names

    def test_overwrite_replaces_existing_database(
            self, dlg, manager, tmp_path, qtbot, fake_box):
        existing = manager.new_database("A")
        _seed(manager, existing, tmp_path, "GC0OLD")
        fake_box.choice = gdlg.tr("gsak_import_existing_overwrite")
        dlg.set_path(_backup(tmp_path, {"A": "GC1NEW"}, with_settings=False))
        _run(dlg, qtbot)
        assert _codes(existing.path) == ["GC1NEW"]

    def test_merge_keeps_existing_caches(self, dlg, manager, tmp_path, qtbot, fake_box):
        existing = manager.new_database("A")
        _seed(manager, existing, tmp_path, "GC0OLD")
        fake_box.choice = gdlg.tr("gsak_import_existing_merge")
        dlg.set_path(_backup(tmp_path, {"A": "GC1NEW"}, with_settings=False))
        _run(dlg, qtbot)
        assert _codes(existing.path) == ["GC0OLD", "GC1NEW"]

    def test_skip_existing_leaves_it_untouched(self, dlg, manager, tmp_path, qtbot, fake_box):
        existing = manager.new_database("A")
        fake_box.choice = gdlg.tr("gsak_import_existing_skip")
        dlg.set_path(_backup(tmp_path, {"A": "GC1AAA", "B": "GC2BBB"}, with_settings=False))
        _run(dlg, qtbot)
        assert _codes(existing.path) == []
        assert _codes(_db_by_name(manager, "B").path) == ["GC2BBB"]

    def test_single_db3_file_imports_without_unpacking(self, dlg, manager, tmp_path, qtbot):
        db = _gsak_db(tmp_path / "data" / "Sommerhus" / "sqlite.db3", "GC9SOM")
        dlg.set_path(db)
        _run(dlg, qtbot)
        assert _codes(_db_by_name(manager, "Sommerhus").path) == ["GC9SOM"]
        assert db.exists()   # the user's own file is never removed

    def test_single_db3_into_the_active_database(self, dlg, manager, tmp_path, qtbot, fake_box):
        # the pre-backup workflow: pick an existing DB (here the active one) and merge
        fake_box.choice = gdlg.tr("gsak_import_existing_merge")
        dlg.set_path(_gsak_db(tmp_path / "data" / "X" / "sqlite.db3", "GC7ACT"))
        _target(dlg, 0).setCurrentText(manager.active.name)
        _run(dlg, qtbot)
        assert _codes(manager.active_path) == ["GC7ACT"]

    def test_prescan_cancel_aborts_and_cleans_up(
            self, dlg, manager, tmp_path, qtbot, monkeypatch, fake_box):
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.scan_gsak_notes_for_embedded_images",
            lambda p: {"affected_notes": 2, "total_images": 3},
        )
        fake_box.choice = "cancel"
        dlg.set_path(_backup(tmp_path, {"A": "GC1AAA"}))
        _run(dlg, qtbot)
        assert "A" not in {db.name for db in manager.databases}
        assert dlg._temp_dir is None

    def test_empty_target_name_is_rejected(self, dlg, tmp_path, monkeypatch, fake_box):
        shown = []
        monkeypatch.setattr(FakeBox, "warning", staticmethod(lambda *a, **k: shown.append(a)))
        dlg.set_path(_backup(tmp_path, {"A": "GC1AAA"}))
        _target(dlg, 0).setCurrentText("  ")
        dlg._start_import()
        assert shown
        assert dlg._worker is None

    # ── Regressions from a real 23-database backup ───────────────────────────

    def test_stray_file_on_the_derived_path_gets_a_free_name(
            self, dlg, manager, tmp_path, qtbot):
        # A leftover, non-database "Default.db" (not in the list) used to make
        # new_database("Default") fail and abort the whole run.
        # (the isolated test manager owns Default.db itself, hence "Stray")
        stray = manager.default_path_for("Stray")
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_bytes(b"not sqlite")
        dlg.set_path(_backup(tmp_path, {"Stray": "GC1DEF"}, with_settings=False))
        assert _target(dlg, 0).currentText() == "Stray-2"
        _run(dlg, qtbot)
        assert _codes(_db_by_name(manager, "Stray-2").path) == ["GC1DEF"]
        assert stray.read_bytes() == b"not sqlite"   # never touched

    def test_blocked_name_typed_by_the_user_is_rejected(
            self, dlg, manager, tmp_path, monkeypatch, fake_box):
        stray = manager.default_path_for("Junk")
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_bytes(b"x")
        shown = []
        monkeypatch.setattr(FakeBox, "warning", staticmethod(lambda *a, **k: shown.append(a)))
        dlg.set_path(_backup(tmp_path, {"A": "GC1AAA"}, with_settings=False))
        _target(dlg, 0).setCurrentText("Junk")
        assert dlg._table.item(0, gdlg.COL_STATUS).text() == gdlg.tr("gsak_import_status_blocked")
        dlg._start_import()
        assert shown and dlg._worker is None

    def test_orphan_database_file_is_offered_and_registered(
            self, dlg, manager, tmp_path, qtbot, fake_box):
        # A valid OpenSAK database file that isn't in the list (e.g. removed
        # from the list earlier) counts as existing and needs confirmation.
        info = manager.new_database("Orphan")
        _seed(manager, info, tmp_path, "GC0OLD")
        manager.remove_from_list(info)
        dlg.set_path(_backup(tmp_path, {"Orphan": "GC1NEW"}, with_settings=False))
        assert dlg._table.item(0, gdlg.COL_STATUS).text() == gdlg.tr("file_locations_col_exists")
        fake_box.choice = gdlg.tr("gsak_import_existing_merge")
        _run(dlg, qtbot)
        assert len(fake_box.instances) == 1
        assert _codes(_db_by_name(manager, "Orphan").path) == ["GC0OLD", "GC1NEW"]

    def test_failing_database_is_skipped_and_the_rest_continues(
            self, dlg, manager, tmp_path, qtbot, monkeypatch):
        real_new = manager.new_database

        def flaky(name, path=None):
            if name == "B":
                raise ValueError("disk says no")
            return real_new(name, path)

        monkeypatch.setattr(manager, "new_database", flaky)
        monkeypatch.setattr(gdlg, "tr", lambda key, **kw: f"{key} {kw}" if kw else key)
        dlg.set_path(_backup(tmp_path, {"A": "GC1AAA", "B": "GC2BBB", "C": "GC3CCC"},
                             with_settings=False))
        _run(dlg, qtbot)
        names = {db.name for db in manager.databases}
        assert {"A", "C"} <= names and "B" not in names
        assert _codes(_db_by_name(manager, "C").path) == ["GC3CCC"]
        log = dlg._log.toPlainText()
        assert "gsak_import_create_failed" in log and "disk says no" in log
        assert "gsak_import_skipped_db {'name': 'B'}" in log

    def test_temp_folder_never_holds_more_than_one_database(
            self, dlg, manager, tmp_path, qtbot, monkeypatch):
        seen = []

        def scan(path):
            seen.append(sorted(p.name for p in dlg._temp_dir.rglob("sqlite.db3")).count("sqlite.db3"))
            return {"affected_notes": 0, "total_images": 0}

        monkeypatch.setattr(
            "opensak.importer.gsak_importer.scan_gsak_notes_for_embedded_images", scan)
        dlg.set_path(_backup(tmp_path, {"A": "GC1AAA", "B": "GC2BBB", "C": "GC3CCC"},
                             with_settings=False))
        _run(dlg, qtbot)
        assert seen == [1, 1, 1]

    def test_prescan_skip_skips_only_that_database(
            self, dlg, manager, tmp_path, qtbot, monkeypatch, fake_box):
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.scan_gsak_notes_for_embedded_images",
            lambda p: {"affected_notes": 1, "total_images": 1},
        )
        fake_box.choice = gdlg.tr("gsak_import_existing_skip")
        dlg.set_path(_backup(tmp_path, {"A": "GC1AAA", "B": "GC2BBB"}, with_settings=False))
        _run(dlg, qtbot)
        names = {db.name for db in manager.databases}
        assert "A" not in names and "B" not in names   # skipped → nothing created
        assert len(fake_box.instances) == 2

    def test_prescan_continue_is_asked_only_once(
            self, dlg, manager, tmp_path, qtbot, monkeypatch, fake_box):
        monkeypatch.setattr(
            "opensak.importer.gsak_importer.scan_gsak_notes_for_embedded_images",
            lambda p: {"affected_notes": 1, "total_images": 1},
        )
        fake_box.choice = gdlg.tr("gsak_prescan_continue")
        dlg.set_path(_backup(tmp_path, {"A": "GC1AAA", "B": "GC2BBB"}, with_settings=False))
        _run(dlg, qtbot)
        assert len(fake_box.instances) == 1
        assert _codes(_db_by_name(manager, "B").path) == ["GC2BBB"]


class TestGsakImportDialogMisc:
    def test_on_result_appends_summary_and_marks_changed(self, dlg):
        dlg._selected_path = Path("/x/gsak.db3")
        dlg._on_result(_result(created=48, waypoints=18, attributes=341, logs=1378,
                                notes=2, note_images_replaced=1, corrected=1))
        assert "48" in dlg._log.toPlainText()
        assert dlg._changed is True

    def test_on_result_no_changes(self, dlg):
        dlg._on_result(_result(created=0, updated=0))
        assert dlg._changed is False

    def test_on_error_appends_log(self, dlg):
        dlg._on_error("boom traceback")
        assert "boom traceback" in dlg._log.toPlainText()

    def test_on_progress_determinate(self, dlg):
        dlg._on_progress(5, 10)
        assert dlg._progress.maximum() == 10
        assert dlg._progress.value() == 5

    def test_on_progress_indeterminate(self, dlg):
        dlg._on_progress(0, -1)
        assert dlg._progress.maximum() == 0

    def test_close_waits_for_worker_and_removes_temp(self, dlg, tmp_path):
        worker = MagicMock()
        worker.isRunning.return_value = True
        dlg._worker = worker
        temp = tmp_path / "gsak_import_x"
        (temp / "00").mkdir(parents=True)
        (temp / "00" / "sqlite.db3").write_bytes(b"x")
        dlg._temp_dir = temp
        dlg._jobs = [gdlg.GsakImportJob("A", "A", gdlg.MODE_NEW)]
        dlg.reject()
        worker.wait.assert_called()
        assert not temp.exists()
        assert dlg._jobs == []
