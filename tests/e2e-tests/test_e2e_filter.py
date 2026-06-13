"""tests/e2e-tests/test_e2e_filter.py — filter scenarios over the 4 seeded caches.

Seed: GC12345/GCAAA01 = Traditional D2.0/T3.0; GC99999/GCAAA02 = Unknown D4.0/T2.5;
all not-found and available.
"""

import pytest

pytest.importorskip("pytestqt")


TOTAL = 4  # caches seeded into the test DB


# ── Quick-filter ───────────────────────────────────────────────────────────────


def test_quick_filter_found_returns_zero(seeded_window, qtbot):
    """Selecting the 'Found' quick-filter hides all caches (none are marked found)."""
    window = seeded_window
    assert window._cache_table.row_count() == TOTAL

    window._quick_filter.setCurrentIndex(2)  # index 2 = Found
    qtbot.wait(50)

    assert window._cache_table.row_count() == 0


def test_quick_filter_not_found_returns_all(seeded_window, qtbot):
    """Selecting 'Not Found' keeps all 4 caches visible."""
    window = seeded_window

    window._quick_filter.setCurrentIndex(2)  # Found → 0 rows
    qtbot.wait(50)
    assert window._cache_table.row_count() == 0

    window._quick_filter.setCurrentIndex(1)  # Not Found → all rows
    qtbot.wait(50)

    assert window._cache_table.row_count() == TOTAL


def test_quick_filter_reset_to_all_restores_count(seeded_window, qtbot):
    """Switching back to 'All' (index 0) after a filter restores full count."""
    window = seeded_window

    window._quick_filter.setCurrentIndex(2)  # Found → 0
    qtbot.wait(50)
    window._quick_filter.setCurrentIndex(0)  # All
    qtbot.wait(50)

    assert window._cache_table.row_count() == TOTAL


# ── Name search ────────────────────────────────────────────────────────────────


def test_name_search_filters_to_matching_rows(seeded_window, qtbot):
    """Typing in the name field narrows the table to caches whose name matches."""
    window = seeded_window

    # "Test Traditional" matches GC12345 and GCAAA01 (both share that name)
    window._search_box.setText("Test Traditional")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 2, timeout=1_000)

    assert window._cache_table.row_count() == 2


def test_name_search_clear_restores_all(seeded_window, qtbot):
    """Clearing the name search field restores the full cache list."""
    window = seeded_window

    window._search_box.setText("Mystery Cache")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 2, timeout=1_000)

    window._search_box.setText("")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == TOTAL, timeout=1_000)

    assert window._cache_table.row_count() == TOTAL


def test_name_search_no_match_returns_zero(seeded_window, qtbot):
    """A search term that matches nothing yields an empty table."""
    window = seeded_window

    window._search_box.setText("zzz_no_match_zzz")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 0, timeout=1_000)

    assert window._cache_table.row_count() == 0


# ── GC-code search ─────────────────────────────────────────────────────────────


def test_gc_search_finds_exact_code(seeded_window, qtbot):
    """Typing a GC code in the GC field returns exactly that one cache."""
    window = seeded_window

    window._search_gc.setText("GC12345")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 1, timeout=1_000)

    assert window._cache_table.row_count() == 1


def test_gc_search_prefix_finds_multiple(seeded_window, qtbot):
    """A partial GC prefix matches all caches whose code starts with it."""
    window = seeded_window

    window._search_gc.setText("GCAAA")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 2, timeout=1_000)

    assert window._cache_table.row_count() == 2


# ── FilterDialog (Ctrl+F path) ─────────────────────────────────────────────────


def test_filter_applied_signal_updates_table(seeded_window, qtbot):
    """
    Simulating what happens when FilterDialog emits filter_applied:
    the main window reloads the table with the new FilterSet.
    """
    from opensak.filters.engine import FilterSet, SortSpec, CacheTypeFilter

    window = seeded_window
    assert window._cache_table.row_count() == TOTAL

    # Only Traditional caches → 2 rows (GC12345 + GCAAA01)
    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    sort = SortSpec("name", ascending=True)

    window._on_filter_applied(fs, sort, "Traditional only")
    qtbot.wait(50)

    assert window._cache_table.row_count() == 2
    assert window._filter_lbl.text() != ""  # label shows active filter name


def test_clear_filter_removes_advanced_filter(seeded_window, qtbot):
    """After _clear_filter the full row count is restored and label is gone."""
    from opensak.filters.engine import FilterSet, SortSpec, CacheTypeFilter

    window = seeded_window

    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "Traditional only")
    qtbot.wait(50)
    assert window._cache_table.row_count() == 2

    window._clear_filter()
    qtbot.wait(50)

    assert window._cache_table.row_count() == TOTAL
    assert window._filter_lbl.text() == ""


# ── WhereClauseFilter integration via _on_filter_applied ──────────────────────


def test_where_clause_filter_reduces_row_count(seeded_window, qtbot):
    """WhereClauseFilter passed to _on_filter_applied narrows the visible rows.

    Seeded DB: GC12345 (D=2.0), GC99999 (D=4.0), GCAAA01 (D=2.0), GCAAA02 (D=4.0).
    difficulty >= 4.0 should keep GC99999 and GCAAA02 only.
    """
    from opensak.filters.engine import FilterSet, SortSpec, WhereClauseFilter

    window = seeded_window
    assert window._cache_table.row_count() == TOTAL

    fs = FilterSet()
    fs.add(WhereClauseFilter("difficulty >= 4.0"))
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "High D")
    qtbot.wait(50)

    assert window._cache_table.row_count() == 2


def test_where_clause_invalid_sql_hides_all_rows(seeded_window, qtbot):
    """Invalid SQL produces zero matches — the table empties without crashing."""
    from opensak.filters.engine import FilterSet, SortSpec, WhereClauseFilter

    window = seeded_window

    fs = FilterSet()
    fs.add(WhereClauseFilter("NOT VALID SQL @@@"))
    window._on_filter_applied(fs, SortSpec("name"), "Bad SQL")
    qtbot.wait(50)

    assert window._cache_table.row_count() == 0


def test_where_clause_clear_restores_full_count(seeded_window, qtbot):
    """After applying a WHERE filter, _clear_filter restores all rows."""
    from opensak.filters.engine import FilterSet, SortSpec, WhereClauseFilter

    window = seeded_window

    fs = FilterSet()
    fs.add(WhereClauseFilter("difficulty >= 4.0"))
    window._on_filter_applied(fs, SortSpec("name"), "High D")
    qtbot.wait(50)
    assert window._cache_table.row_count() == 2

    window._clear_filter()
    qtbot.wait(50)

    assert window._cache_table.row_count() == TOTAL


# ── FilterDialog WHERE tab ────────────────────────────────────────────────────


def test_where_tab_present(seeded_window, qtbot):
    """FilterDialog always shows a WHERE tab."""
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    assert dialog._where_tab is not None
    tab_labels = [dialog._tabs.tabText(i) for i in range(dialog._tabs.count())]
    assert any("where" in t.lower() or "sql" in t.lower() for t in tab_labels)
    dialog.close()


def test_where_tab_builds_where_clause_filter(seeded_window, qtbot):
    """Entering SQL in the WHERE tab produces a WhereClauseFilter in the FilterSet."""
    from opensak.gui.dialogs.filter_dialog import FilterDialog
    from opensak.filters.engine import WhereClauseFilter, _iter_filters

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    dialog._where_sql_general.setPlainText("difficulty >= 3")
    fs = dialog._build_filterset()

    where_filters = [f for f in _iter_filters(fs) if isinstance(f, WhereClauseFilter)]
    assert len(where_filters) == 1
    assert where_filters[0].sql == "difficulty >= 3"
    dialog.close()


def test_where_tab_empty_sql_adds_no_filter(seeded_window, qtbot):
    """An empty WHERE text field does not add a WhereClauseFilter to the set."""
    from opensak.gui.dialogs.filter_dialog import FilterDialog
    from opensak.filters.engine import WhereClauseFilter, _iter_filters

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    dialog._where_sql_general.setPlainText("")
    fs = dialog._build_filterset()

    where_filters = [f for f in _iter_filters(fs) if isinstance(f, WhereClauseFilter)]
    assert len(where_filters) == 0
    dialog.close()


def test_where_tab_reset_clears_sql(seeded_window, qtbot):
    """_reset_all() empties the WHERE SQL field and hides the error label."""
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    dialog._where_sql_general.setPlainText("difficulty >= 3")
    dialog._reset_all()

    assert dialog._where_sql_general.toPlainText() == ""
    assert not dialog._where_error_label.isVisible()
    dialog.close()


def test_where_tab_validate_valid_sql_returns_none(seeded_window, qtbot):
    """_validate_where_sql returns None for syntactically valid SQL."""
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    error = dialog._validate_where_sql("difficulty >= 3")
    assert error is None
    dialog.close()


def test_where_tab_validate_invalid_sql_returns_error(seeded_window, qtbot):
    """_validate_where_sql returns a non-empty error string for bad SQL."""
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    error = dialog._validate_where_sql("NOT VALID SQL @@@")
    assert error is not None
    assert len(error) > 0
    dialog.close()
