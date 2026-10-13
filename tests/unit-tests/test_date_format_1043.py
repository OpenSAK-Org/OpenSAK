# tests/unit-tests/test_date_format_1043.py
"""
Issue #1043: every date picker and every date text uses the date format chosen
in Settings, so a date looks the same in the grid, the detail panel, the
filter dialog, Mark as Found and the waypoint dialog.
"""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.gui import date_format as df
from opensak.utils.types import DateFormat

CASES = [
    (DateFormat.DMY, "dd.MM.yyyy", "25.12.2024"),
    (DateFormat.MDY, "MM/dd/yyyy", "12/25/2024"),
    (DateFormat.YMD, "yyyy-MM-dd", "2024-12-25"),
]


@pytest.fixture
def use_format(monkeypatch):
    """Set the Settings date format seen by the pickers."""
    def _set(fmt: DateFormat) -> None:
        monkeypatch.setattr(
            "opensak.gui.settings.get_settings",
            lambda: SimpleNamespace(date_format=fmt),
        )
    return _set


# ── The helper ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt, pattern, _text", CASES)
def test_fixed_patterns(qapp, fmt, pattern, _text):
    assert df.qt_date_pattern(fmt) == pattern


@pytest.mark.parametrize("fmt, _pattern, text", CASES)
def test_format_date(qapp, fmt, _pattern, text):
    assert df.format_date(date(2024, 12, 25), fmt) == text
    assert df.format_date(datetime(2024, 12, 25, 13, 45), fmt) == text


def test_locale_pattern_is_zero_padded(qapp):
    pattern = df.qt_date_pattern(DateFormat.LOCALE)
    text = df.format_date(date(2024, 5, 3), DateFormat.LOCALE)
    assert "2024" in text
    assert "05" in text and "03" in text, (pattern, text)


def test_apply_date_format_uses_settings(qapp, use_format):
    from PySide6.QtWidgets import QDateEdit

    use_format(DateFormat.DMY)
    edit = QDateEdit()
    df.apply_date_format(edit)
    assert edit.displayFormat() == "dd.MM.yyyy"


# ── The pickers ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt, pattern, _text", CASES)
def test_filter_date_pickers(qtbot, use_format, fmt, pattern, _text):
    from opensak.gui.dialogs.filter_dialog import DateFilterRow

    use_format(fmt)
    row = DateFilterRow("hidden_date", "Hidden")
    qtbot.addWidget(row)
    assert row.date1.displayFormat() == pattern
    assert row.date2.displayFormat() == pattern


def test_filter_date_pickers_with_time_keep_the_date_format(qtbot, use_format):
    from opensak.gui.dialogs.filter_dialog import DateFilterRow

    use_format(DateFormat.DMY)
    row = DateFilterRow("last_gpx_update", "Last GPX update")
    qtbot.addWidget(row)
    row.time_check.setChecked(True)
    assert row.date1.displayFormat() == "dd.MM.yyyy HH:mm"
    row.time_check.setChecked(False)
    assert row.date1.displayFormat() == "dd.MM.yyyy"


def test_mark_found_picker(qtbot, use_format):
    from opensak.gui.dialogs.mark_found_dialog import MarkFoundDialog

    use_format(DateFormat.MDY)
    dlg = MarkFoundDialog("GC12345", "Some cache")
    qtbot.addWidget(dlg)
    assert dlg._date_edit.displayFormat() == "MM/dd/yyyy"


def test_waypoint_date_is_no_longer_hardcoded(qtbot, use_format):
    from opensak.gui.dialogs.waypoint_dialog import OptionalDateEdit

    use_format(DateFormat.DMY)
    edit = OptionalDateEdit()
    qtbot.addWidget(edit)
    assert edit._date.displayFormat() == "dd.MM.yyyy"


# ── Grid and detail panel use the same helper ─────────────────────────────────

@pytest.mark.parametrize("fmt, _pattern, text", CASES)
def test_grid_and_detail_match_the_pickers(qapp, monkeypatch, fmt, _pattern, text):
    from opensak.gui import cache_detail as cd
    from opensak.gui import cache_table as ct

    fake = lambda: SimpleNamespace(date_format=fmt)  # noqa: E731
    monkeypatch.setattr(ct, "get_settings", fake)
    monkeypatch.setattr(cd, "get_settings", fake)
    d = datetime(2024, 12, 25)
    assert ct._format_date(d) == cd._format_date(d) == text
