# tests/unit-tests/test_gsak_filter_import_dialog.py — GSAK filter import
# dialog: filter list, selection, worker and the migration statistics it prints.

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak.gui.dialogs import gsak_filter_import_dialog as fdlg
from opensak.gui.dialogs.gsak_filter_import_dialog import (
    GsakFilterImportDialog,
    GsakFilterImportWorker,
    format_result,
)
from opensak.importer.gsak_filter_importer import (
    Conversion,
    FilterImportEntry,
    GsakFilterImportResult,
    Options,
    import_gsak_filters,
)


def _blob(pairs: dict) -> str:
    return "\r\n".join(f"{k}={v}" for k, v in pairs.items())


_FOUND = _blob({"chkFound": "True", "chkNotFound": "False"})


@pytest.fixture
def gsak_db(tmp_path) -> Path:
    path = tmp_path / "gsak.db3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE TranslateFilters (Type TEXT, Description TEXT, Data TEXT)")
    conn.executemany(
        "INSERT INTO TranslateFilters VALUES ('FI', ?, ?)",
        [("Alpha", _FOUND), ("Beta", _FOUND), ("Gamma export", _FOUND)],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def dlg(qtbot):
    d = GsakFilterImportDialog()
    qtbot.addWidget(d)
    return d


# ── Worker ────────────────────────────────────────────────────────────────────

class TestWorker:
    def test_run_emits_result(self, gsak_db, tmp_path, monkeypatch):
        monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
        w = GsakFilterImportWorker(gsak_db, ["Alpha"], overwrite=False)
        got = []
        w.result_ready.connect(got.append)
        w.run()
        assert len(got) == 1 and got[0].written == 1

    def test_run_reports_errors_instead_of_raising(self, tmp_path):
        w = GsakFilterImportWorker(tmp_path / "missing.db3", ["A"], overwrite=False)
        errs = []
        w.error.connect(errs.append)
        w.run()
        assert errs and "Traceback" in errs[0]

    def test_run_reports_progress(self, gsak_db, tmp_path, monkeypatch):
        monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
        w = GsakFilterImportWorker(gsak_db, ["Alpha", "Beta"], overwrite=False)
        seen = []
        w.progress.connect(lambda d, t: seen.append((d, t)))
        w.run()
        assert seen == [(1, 2), (2, 2)]


# ── Dialog ────────────────────────────────────────────────────────────────────

class TestDialog:
    def test_starts_empty_and_disabled(self, dlg):
        assert dlg._list.count() == 0
        assert dlg._import_btn.isEnabled() is False
        assert dlg._search.isEnabled() is False

    def test_set_path_lists_filters_all_checked(self, dlg, gsak_db):
        dlg.set_path(gsak_db)
        assert [dlg._list.item(i).text() for i in range(dlg._list.count())] == \
            ["Alpha", "Beta", "Gamma export"]
        assert dlg._selected_names() == ["Alpha", "Beta", "Gamma export"]
        assert dlg._import_btn.isEnabled() is True

    def test_unticking_everything_disables_import(self, dlg, gsak_db):
        dlg.set_path(gsak_db)
        dlg._check_visible(False)
        assert dlg._selected_names() == []
        assert dlg._import_btn.isEnabled() is False

    def test_search_hides_non_matching_filters(self, dlg, gsak_db):
        dlg.set_path(gsak_db)
        dlg._search.setText("gamma")
        hidden = [dlg._list.item(i).text()
                  for i in range(dlg._list.count()) if dlg._list.item(i).isHidden()]
        assert hidden == ["Alpha", "Beta"]

    def test_select_all_only_touches_visible_rows(self, dlg, gsak_db):
        """Otherwise "select none" while searching would silently clear the rest."""
        dlg.set_path(gsak_db)
        dlg._check_visible(False)
        dlg._search.setText("alpha")
        dlg._check_visible(True)
        assert dlg._selected_names() == ["Alpha"]

    def test_non_gsak_database_reports_and_stays_disabled(self, dlg, tmp_path, monkeypatch):
        path = tmp_path / "sqlite.db3"
        sqlite3.connect(path).close()
        shown = []
        monkeypatch.setattr(fdlg.QMessageBox, "critical", lambda *a, **k: shown.append(a))
        dlg.set_path(path)
        assert shown and dlg._import_btn.isEnabled() is False

    def test_database_without_filters_reports_and_stays_disabled(self, dlg, tmp_path,
                                                                 monkeypatch):
        path = tmp_path / "gsak.db3"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE TranslateFilters (Type TEXT, Description TEXT, Data TEXT)")
        conn.commit()
        conn.close()
        shown = []
        monkeypatch.setattr(fdlg.QMessageBox, "information", lambda *a, **k: shown.append(a))
        dlg.set_path(path)
        assert shown and dlg._import_btn.isEnabled() is False

    def test_browse_cancel_changes_nothing(self, dlg, monkeypatch):
        monkeypatch.setattr(fdlg.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
        dlg._browse()
        assert dlg._selected_path is None

    def test_start_import_without_a_file_is_a_noop(self, dlg):
        dlg._start_import()
        assert dlg._progress.isVisible() is False and dlg._worker is None

    def test_result_with_written_profiles_signals_completion(self, dlg, gsak_db, tmp_path,
                                                             monkeypatch):
        monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
        fired = []
        dlg.import_completed.connect(lambda: fired.append(True))
        dlg._on_result(import_gsak_filters(gsak_db, out_dir=tmp_path / "f",
                                           opts=Options()))
        assert fired == [True]
        assert dlg._log.toPlainText()

    def test_result_without_written_profiles_stays_quiet(self, dlg):
        fired = []
        dlg.import_completed.connect(lambda: fired.append(True))
        dlg._on_result(GsakFilterImportResult())
        assert fired == []


# ── Statistics ────────────────────────────────────────────────────────────────

def _entry(name: str, native: int = 0, sql: int = 0, comments: int = 0,
           written: bool = True, skipped: bool = False,
           error: str | None = None) -> FilterImportEntry:
    c = Conversion(name)
    for i in range(native):
        c.native(f"n{i}", object())
    for i in range(sql):
        c.sql(f"s{i}", "1 = 1")
    for i in range(comments):
        c.comment(f"c{i}", "nope")
    return FilterImportEntry(name=name, conversion=c, written=written,
                             skipped_existing=skipped, error=error)


class TestStatistics:
    def test_counts_criteria_not_filters(self):
        """A filter with many conditions has to weigh more than a small one."""
        result = GsakFilterImportResult(entries=[
            _entry("big", native=8, comments=2),
            _entry("small", comments=1),
        ])
        assert result.total_criteria == 11
        assert result.migrated_criteria == 8
        assert result.coverage == pytest.approx(8 / 11)

    def test_sql_counts_as_migrated_comments_do_not(self):
        result = GsakFilterImportResult(entries=[_entry("f", native=1, sql=1, comments=2)])
        assert (result.native_criteria, result.sql_criteria) == (1, 1)
        assert result.commented_criteria == 2
        assert result.coverage == 0.5

    def test_everything_migrated_is_100_percent(self):
        result = GsakFilterImportResult(entries=[_entry("f", native=3, sql=1)])
        assert result.coverage == 1.0 and result.fully_migrated == 1

    def test_everything_commented_is_0_percent(self):
        result = GsakFilterImportResult(entries=[_entry("f", comments=3)])
        assert result.coverage == 0.0 and result.not_migrated == 1

    def test_skipped_and_failed_entries_are_left_out_of_coverage(self):
        result = GsakFilterImportResult(entries=[
            _entry("written", native=2),
            _entry("skipped", comments=5, written=False, skipped=True),
            _entry("failed", comments=5, written=False, error="boom"),
        ])
        assert (result.written, result.skipped, result.failed) == (1, 1, 1)
        assert result.total_criteria == 2 and result.coverage == 1.0

    def test_empty_import_is_not_a_division_by_zero(self):
        assert GsakFilterImportResult().coverage == 1.0

    def test_format_result_reports_the_headline_numbers(self):
        result = GsakFilterImportResult(entries=[
            _entry("Good", native=4),
            _entry("Partly", native=1, comments=1),
            _entry("Existing", written=False, skipped=True),
        ])
        text = format_result(result)
        assert "83.3 %" in text                 # 5 of 6 conditions migrated
        assert "Partly" in text                 # listed as incomplete
        assert "Existing" in text               # listed as skipped
        assert "Good" not in text               # fully migrated — nothing to say

    def test_format_result_lists_failures(self):
        text = format_result(GsakFilterImportResult(entries=[
            _entry("Broken", written=False, error="RuntimeError: boom"),
        ]))
        assert "Broken" in text and "boom" in text

    def test_format_result_caps_long_listings(self):
        entries = [_entry(f"F{i:03d}", native=1, comments=1) for i in range(60)]
        text = format_result(GsakFilterImportResult(entries=entries))
        listed = [ln for ln in text.splitlines() if ln.strip().startswith("F0")]
        assert len(listed) == fdlg.MAX_LISTED_FILTERS
        assert f"… {60 - fdlg.MAX_LISTED_FILTERS}" in text
