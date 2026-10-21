# tests/e2e-tests/test_e2e_gsak_filter_import.py — importing GSAK's saved
# filters through File → Import from GSAK Database, all the way to a usable
# filter profile.

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from opensak.filters.engine import FilterProfile


def _blob(pairs: dict) -> str:
    return "\r\n".join(f"{k}={v}" for k, v in pairs.items())


@pytest.fixture
def gsak_db(tmp_path) -> Path:
    """A gsak.db3 with two saved filters: one that migrates whole, one that
    leaves a condition behind in the Where tab."""
    path = tmp_path / "gsak.db3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Settings (Type collate nocase, Description, Data)")
    conn.executemany("INSERT INTO Settings VALUES ('FI', ?, ?)", [
        ("Not found", _blob({"chkFound": "False", "chkNotFound": "True"})),
        ("Watched", _blob({"chkFound": "False", "chkNotFound": "True",
                           "chkWatchYes": "True", "chkWatchNo": "False",
                           # A regex on the PQ source file: OpenSAK has no GUI
                           # row for it and SQLite no REGEXP, so it stays behind.
                           "cbxSource": "7", "edtSource": "^PQ"})),
    ])
    conn.commit()
    conn.close()
    return path


def _open_dialog(window, monkeypatch, gsak_db, profiles_dir):
    """Choose gsak.db3 in the GSAK import and run it (filters only) — the
    filter dialog it hands over to comes back with the file already chosen."""
    from opensak.gui.dialogs import gsak_filter_import_dialog as fdlg
    from opensak.gui.dialogs import gsak_import_dialog as idlg

    monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: profiles_dir)
    opened = {}
    monkeypatch.setattr(idlg.GsakImportDialog, "exec",
                        lambda self: opened.setdefault("import", self))
    monkeypatch.setattr(fdlg.GsakFilterImportDialog, "exec",
                        lambda self: opened.setdefault("dlg", self))
    window._act_gsak_import.trigger()
    imp = opened["import"]
    imp.set_path(gsak_db)
    imp._locations_cb.setChecked(False)
    imp._start_import()
    return opened["dlg"]


class TestGsakFilterImportE2E:
    def test_no_separate_menu_action_any_more(self, empty_window):
        # The GSAK import covers it: a backup or gsak.db3 offers the filters.
        assert not hasattr(empty_window, "_act_gsak_filter_import")

    def test_gsak_db3_alone_offers_only_filters_and_locations(
        self, empty_window, monkeypatch, gsak_db
    ):
        from opensak.gui.dialogs import gsak_import_dialog as idlg

        opened = {}
        monkeypatch.setattr(idlg.GsakImportDialog, "exec",
                            lambda self: opened.setdefault("import", self))
        empty_window._act_gsak_import.trigger()
        imp = opened["import"]
        imp.set_path(gsak_db)
        assert imp._table.rowCount() == 0
        assert not imp._filters_cb.isHidden() and imp._filters_cb.isChecked()
        assert not imp._locations_cb.isHidden()
        assert imp._import_btn.isEnabled()

    def test_import_writes_profiles_the_filter_dialog_can_load(
        self, empty_window, tmp_path, monkeypatch, gsak_db, qtbot
    ):
        profiles_dir = tmp_path / "appdata"
        dlg = _open_dialog(empty_window, monkeypatch, gsak_db, profiles_dir)
        assert dlg._selected_names() == ["Not found", "Watched"]

        with qtbot.waitSignal(dlg.import_completed, timeout=10_000):
            dlg._start_import()
        qtbot.waitUntil(lambda: dlg._worker is None, timeout=10_000)

        written = sorted(p.name for p in (profiles_dir / "filters").glob("*.json"))
        assert written == ["Not found.json", "Watched.json"]

        # Both profiles load back as real filter sets…
        profile = FilterProfile.load(profiles_dir / "filters" / "Watched.json")
        types = [f.filter_type for f in profile.filterset._filters]
        assert types == ["not_found", "where_clause"]

        # …and the Where clause holds the watch-list SQL plus a comment about
        # the source-file regex, which cannot run as SQL.
        sql = profile.filterset._filters[1].sql
        assert "coalesce(watch, 0) = 1" in sql
        assert "Source" in sql
        assert sql.splitlines()[-1].startswith("(")     # ends on runnable SQL

    def test_results_log_reports_the_migration_coverage(
        self, empty_window, tmp_path, monkeypatch, gsak_db, qtbot
    ):
        dlg = _open_dialog(empty_window, monkeypatch, gsak_db, tmp_path / "appdata")
        with qtbot.waitSignal(dlg.import_completed, timeout=10_000):
            dlg._start_import()
        qtbot.waitUntil(lambda: dlg._worker is None, timeout=10_000)

        log = dlg._log.toPlainText()
        # 4 conditions in total (not-found ×2, watch list, source regex), of
        # which the source regex could not be migrated.
        assert "75.0 %" in log
        assert "Watched" in log        # listed as incomplete
        assert "Not found" not in log.split("Watched")[-1]   # it was complete

    def test_second_import_skips_existing_profiles(
        self, empty_window, tmp_path, monkeypatch, gsak_db, qtbot
    ):
        profiles_dir = tmp_path / "appdata"
        dlg = _open_dialog(empty_window, monkeypatch, gsak_db, profiles_dir)
        with qtbot.waitSignal(dlg.import_completed, timeout=10_000):
            dlg._start_import()
        qtbot.waitUntil(lambda: dlg._worker is None, timeout=10_000)
        first = {p: p.read_text(encoding="utf-8")
                 for p in (profiles_dir / "filters").glob("*.json")}

        # Second run: nothing is written, so import_completed never fires —
        # only the worker finishing says it is done.
        dlg = _open_dialog(empty_window, monkeypatch, gsak_db, profiles_dir)
        fired = []
        dlg.import_completed.connect(lambda: fired.append(True))
        dlg._start_import()
        qtbot.waitUntil(lambda: dlg._worker is None, timeout=10_000)

        assert fired == []
        assert {p: p.read_text(encoding="utf-8")
                for p in (profiles_dir / "filters").glob("*.json")} == first
        assert dlg._log.toPlainText().count("Watched") >= 1     # listed as skipped

    def test_imported_profile_appears_in_the_toolbar_dropdown(
        self, empty_window, tmp_path, monkeypatch, gsak_db, qtbot
    ):
        profiles_dir = tmp_path / "appdata"
        # The GSAK import passes import_completed on to the main window.
        dlg = _open_dialog(empty_window, monkeypatch, gsak_db, profiles_dir)
        with qtbot.waitSignal(dlg.import_completed, timeout=10_000):
            dlg._start_import()
        qtbot.waitUntil(lambda: dlg._worker is None, timeout=10_000)

        combo = empty_window._filter_profile_combo
        names = [combo.itemText(i) for i in range(combo.count())]
        assert "Watched" in names
