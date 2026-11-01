# tests/unit-tests/test_sort_last_four_logs_1154.py — issue #1154.
#
# The Last four logs column sorted by its stored text, so effectively by the
# date of the newest log, and caches showing the same squares (DDDD, DDFF, ...)
# didn't end up together. A filter's saved sort ignored the field altogether.
# Both now sort by the pattern the squares show.

from types import SimpleNamespace

import pytest

from opensak.filters.engine import (
    SortSpec, _last_four_logs_pattern, apply_filters, apply_filters_lightweight,
    sort_by_last_four_logs,
)


def _logs(*entries: tuple[str, str]) -> str:
    """The stored text for (date, log type) entries, newest first."""
    return "\n".join(f"{date}T00:00:00\t{log_type}\tSomeone" for date, log_type in entries)


DNF, FOUND, NOTE = "Didn't find it", "Found it", "Write note"

# Deliberately not in pattern order, and the dates contradict it: the stored
# text would put GC_FFFF first when descending and GC_DDFF between the DDDDs.
CACHES = {
    "GC_FFFF": _logs(("2026-09-01", FOUND), ("2026-08-01", FOUND),
                     ("2026-07-01", FOUND), ("2026-06-01", FOUND)),
    "GC_NONE": "",
    "GC_DDDD_OLD": _logs(("2020-01-04", DNF), ("2020-01-03", DNF),
                         ("2020-01-02", DNF), ("2020-01-01", DNF)),
    "GC_DF": _logs(("2023-01-02", DNF), ("2023-01-01", FOUND)),
    "GC_DDFF": _logs(("2022-01-04", DNF), ("2022-01-03", DNF),
                     ("2022-01-02", FOUND), ("2022-01-01", FOUND)),
    "GC_DDDD_NEW": _logs(("2025-01-04", DNF), ("2025-01-03", DNF),
                         ("2025-01-02", DNF), ("2025-01-01", DNF)),
    "GC_NFFF": _logs(("2024-01-04", NOTE), ("2024-01-03", FOUND),
                     ("2024-01-02", FOUND), ("2024-01-01", FOUND)),
}

ASCENDING = ["GC_DDDD_NEW", "GC_DDDD_OLD", "GC_DDFF", "GC_DF",
             "GC_FFFF", "GC_NFFF", "GC_NONE"]
# Patterns reversed; the newest cache stays first within DDDD
DESCENDING = ["GC_NONE", "GC_NFFF", "GC_FFFF", "GC_DF",
              "GC_DDFF", "GC_DDDD_NEW", "GC_DDDD_OLD"]


def _plain_caches() -> list[SimpleNamespace]:
    return [SimpleNamespace(gc_code=code, last_four_logs=raw) for code, raw in CACHES.items()]


@pytest.mark.parametrize("raw, expected", [
    (_logs(("2026-01-02", DNF), ("2026-01-01", FOUND)), "DF~~"),
    (_logs(("2026-01-03", "Attended"), ("2026-01-02", "Webcam Photo Taken"),
           ("2026-01-01", "Owner Maintenance")), "FFN~"),
    ("", "~~~~"),
    (None, "~~~~"),
    ("not a log line", "~~~~"),
])
def test_pattern_matches_the_squares(raw, expected):
    assert _last_four_logs_pattern(SimpleNamespace(last_four_logs=raw)) == expected


@pytest.mark.parametrize("reverse, expected", [(False, ASCENDING), (True, DESCENDING)])
def test_sort_groups_by_pattern(reverse, expected):
    caches = _plain_caches()
    sort_by_last_four_logs(caches, reverse=reverse)
    assert [c.gc_code for c in caches] == expected


@pytest.mark.parametrize("ascending, expected", [(True, ASCENDING), (False, DESCENDING)])
@pytest.mark.parametrize("query", [apply_filters, apply_filters_lightweight])
def test_saved_filter_sort_uses_the_same_order(db_session, make_cache, query, ascending, expected):
    for code, raw in CACHES.items():
        db_session.add(make_cache(code, last_four_logs=raw or None))
    db_session.commit()
    results = query(db_session, None, SortSpec("last_four_logs", ascending=ascending))
    assert [c.gc_code for c in results] == expected


def test_header_click_uses_the_same_order(monkeypatch, qtbot):
    pytest.importorskip("pytestqt")
    from PySide6.QtCore import Qt
    import opensak.gui.cache_table as ct

    monkeypatch.setattr(ct, "_get_active_columns", lambda: ["gc_code", "last_four_logs"])
    model = ct.CacheTableModel()
    model._caches = _plain_caches()

    model.sort(1, Qt.SortOrder.AscendingOrder)
    assert [c.gc_code for c in model._caches] == ASCENDING
    model.sort(1, Qt.SortOrder.DescendingOrder)
    assert [c.gc_code for c in model._caches] == DESCENDING
