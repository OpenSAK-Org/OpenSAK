"""tests/e2e-tests/test_e2e_filter.py — filter scenarios over the 4 seeded caches.

Seed: GC12345/GCAAA01 = Traditional D2.0/T3.0; GC99999/GCAAA02 = Unknown D4.0/T2.5;
all not-found and available.
"""

import pytest

pytest.importorskip("pytestqt")


TOTAL = 4  # caches seeded into the test DB


# ── Quick-filter ───────────────────────────────────────────────────────────────


def test_quick_filter_found_returns_zero(seeded_window, qtbot):
    # Selecting the 'Found' quick-filter hides all caches (none are marked found).
    window = seeded_window
    assert window._cache_table.row_count() == TOTAL

    window._quick_filter.setCurrentIndex(2)  # index 2 = Found
    qtbot.wait(50)

    assert window._cache_table.row_count() == 0


def test_quick_filter_not_found_returns_all(seeded_window, qtbot):
    # Selecting 'Not Found' keeps all 4 caches visible.
    window = seeded_window

    window._quick_filter.setCurrentIndex(2)  # Found → 0 rows
    qtbot.wait(50)
    assert window._cache_table.row_count() == 0

    window._quick_filter.setCurrentIndex(1)  # Not Found → all rows
    qtbot.wait(50)

    assert window._cache_table.row_count() == TOTAL


def test_quick_filter_reset_to_all_restores_count(seeded_window, qtbot):
    # Switching back to 'All' (index 0) after a filter restores full count.
    window = seeded_window

    window._quick_filter.setCurrentIndex(2)  # Found → 0
    qtbot.wait(50)
    window._quick_filter.setCurrentIndex(0)  # All
    qtbot.wait(50)

    assert window._cache_table.row_count() == TOTAL


# ── Name search ────────────────────────────────────────────────────────────────


def test_name_search_filters_to_matching_rows(seeded_window, qtbot):
    # Typing in the name field narrows the table to caches whose name matches.
    window = seeded_window

    # "Test Traditional" matches GC12345 and GCAAA01 (both share that name)
    window._search_box.setText("Test Traditional")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 2, timeout=1_000)

    assert window._cache_table.row_count() == 2


def test_name_search_clear_restores_all(seeded_window, qtbot):
    # Clearing the name search field restores the full cache list.
    window = seeded_window

    window._search_box.setText("Mystery Cache")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 2, timeout=1_000)

    window._search_box.setText("")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == TOTAL, timeout=1_000)

    assert window._cache_table.row_count() == TOTAL


def test_name_search_no_match_returns_zero(seeded_window, qtbot):
    # A search term that matches nothing yields an empty table.
    window = seeded_window

    window._search_box.setText("zzz_no_match_zzz")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 0, timeout=1_000)

    assert window._cache_table.row_count() == 0


# ── GC-code search ─────────────────────────────────────────────────────────────


def test_gc_search_finds_exact_code(seeded_window, qtbot):
    # Typing a GC code in the GC field returns exactly that one cache.
    window = seeded_window

    window._search_gc.setText("GC12345")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 1, timeout=1_000)

    assert window._cache_table.row_count() == 1


def test_gc_search_prefix_finds_multiple(seeded_window, qtbot):
    # A partial GC prefix matches all caches whose code starts with it.
    window = seeded_window

    window._search_gc.setText("GCAAA")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 2, timeout=1_000)

    assert window._cache_table.row_count() == 2


def test_gc_search_partial_without_gc_prefix(seeded_window, qtbot):
    # "AAA" (no GC prefix) must find GCAAA01 and GCAAA02 via substring match.
    window = seeded_window

    window._search_gc.setText("AAA")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 2, timeout=1_000)

    assert window._cache_table.row_count() == 2


# ── Toolbar profile dropdown: "None" vs "Active (unsaved)" ────────────────────
# Reported by Allan/Mike: the dropdown said "None" while a filter was clearly
# applied (red Clear button, fewer rows) because it was set via a quick search
# box / status click rather than a saved profile.


def test_dropdown_shows_none_with_no_filter(seeded_window, qtbot):
    from opensak.lang import tr
    window = seeded_window
    assert window._filter_profile_combo.currentIndex() == 0
    assert window._filter_profile_combo.currentText() == tr("toolbar_filter_combo_none")


def test_dropdown_shows_active_for_gc_search(seeded_window, qtbot):
    from opensak.lang import tr
    window = seeded_window

    window._search_gc.setText("GC12345")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 1, timeout=1_000)

    assert window._filter_profile_combo.currentIndex() == 0
    assert window._filter_profile_combo.currentText() == tr("toolbar_filter_combo_active")


def test_dropdown_shows_active_for_name_search(seeded_window, qtbot):
    from opensak.lang import tr
    window = seeded_window

    window._search_box.setText("Mystery Cache")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 2, timeout=1_000)

    assert window._filter_profile_combo.currentText() == tr("toolbar_filter_combo_active")


def test_dropdown_shows_active_for_quick_filter(seeded_window, qtbot):
    from opensak.lang import tr
    window = seeded_window

    window._quick_filter.setCurrentIndex(2)  # Found → not the "All" default
    qtbot.wait(50)

    assert window._filter_profile_combo.currentText() == tr("toolbar_filter_combo_active")


def test_dropdown_reverts_to_none_when_search_cleared(seeded_window, qtbot):
    from opensak.lang import tr
    window = seeded_window

    window._search_gc.setText("GC12345")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == 1, timeout=1_000)
    assert window._filter_profile_combo.currentText() == tr("toolbar_filter_combo_active")

    window._search_gc.setText("")
    qtbot.waitUntil(lambda: window._cache_table.row_count() == TOTAL, timeout=1_000)

    assert window._filter_profile_combo.currentText() == tr("toolbar_filter_combo_none")


def test_dropdown_shows_active_for_unsaved_advanced_filter(seeded_window, qtbot):
    # An advanced filter applied via the Set-filter dialog without saving it
    # as a named profile — profile_name == "" reaches _on_filter_applied.
    from opensak.lang import tr
    from opensak.filters.engine import FilterSet, SortSpec, CacheTypeFilter

    window = seeded_window
    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "")
    qtbot.wait(50)

    assert window._filter_profile_combo.currentText() == tr("toolbar_filter_combo_active")


def test_dropdown_shows_profile_name_for_saved_filter(seeded_window, qtbot, tmp_path, monkeypatch):
    # A genuinely saved/named profile still shows its own name, not "Active".
    # (Passing an arbitrary label to _on_filter_applied — as the status-bar
    # quick filters do — is *not* the same as a saved profile; see the
    # comment on the combo's `activated` wiring in mainwindow.py.)
    from opensak.filters.engine import FilterSet, SortSpec, CacheTypeFilter, FilterProfile

    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    FilterProfile("Traditional only", fs, SortSpec("name", ascending=True)).save(profiles_dir=tmp_path)
    monkeypatch.setattr(
        FilterProfile, "list_profiles",
        staticmethod(lambda profiles_dir=None: sorted(tmp_path.glob("*.json")))
    )

    window = seeded_window
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "Traditional only")
    qtbot.wait(50)

    assert window._filter_profile_combo.currentText() == "Traditional only"


def test_dropdown_shows_none_after_clear_filter(seeded_window, qtbot):
    from opensak.lang import tr
    from opensak.filters.engine import FilterSet, SortSpec, CacheTypeFilter

    window = seeded_window
    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "")
    qtbot.wait(50)
    assert window._filter_profile_combo.currentText() == tr("toolbar_filter_combo_active")

    window._clear_filter()
    qtbot.wait(50)

    assert window._filter_profile_combo.currentText() == tr("toolbar_filter_combo_none")


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
    # After _clear_filter the full row count is restored and label is gone.
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


# ── Deleting the active saved filter profile (issue #491) ────────────────────


def test_profile_deleted_clears_active_filter(seeded_window, qtbot):
    # Deleting the profile that is currently active must clear the filter
    # and restore the full row count, even though the FilterDialog itself
    # was never applied/closed here (mirrors "Close without Apply" repro).
    from opensak.filters.engine import FilterSet, SortSpec, CacheTypeFilter

    window = seeded_window
    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "Traditional only")
    qtbot.wait(50)
    assert window._cache_table.row_count() == 2
    assert window._active_filter_name == "Traditional only"

    window._on_profile_deleted("Traditional only")
    qtbot.wait(50)

    assert window._cache_table.row_count() == TOTAL
    assert window._active_filter_name == ""
    assert window._filter_lbl.text() == ""


def test_profile_deleted_of_inactive_profile_leaves_active_filter(seeded_window, qtbot):
    # Deleting a *different*, non-active profile must not touch the
    # currently-applied filter or the cache table.
    from opensak.filters.engine import FilterSet, SortSpec, CacheTypeFilter

    window = seeded_window
    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "Traditional only")
    qtbot.wait(50)
    assert window._cache_table.row_count() == 2

    window._on_profile_deleted("Some other profile")
    qtbot.wait(50)

    assert window._cache_table.row_count() == 2
    assert window._active_filter_name == "Traditional only"


def test_profile_deleted_preserves_quick_search_text(seeded_window, qtbot):
    # Deleting the active advanced filter must not wipe unrelated quick
    # search text (GC code / name) — only _clear_filter() should do that.
    from opensak.filters.engine import FilterSet, SortSpec, CacheTypeFilter

    window = seeded_window
    window._search_gc.setText("GC")
    qtbot.wait(50)

    fs = FilterSet(mode="AND")
    fs.add(CacheTypeFilter(["Traditional Cache"]))
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "Traditional only")
    qtbot.wait(50)

    window._on_profile_deleted("Traditional only")
    qtbot.wait(50)

    assert window._search_gc.text() == "GC"
    assert window._active_filter_name == ""


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


def test_where_clause_invalid_sql_hides_all_rows(seeded_window, qtbot, monkeypatch):
    # Issue #444: invalid SQL still produces zero matches, but that no
    # longer empties the table — the (rejected) filter warns and reopens
    # "Set filter" instead, leaving the previous view untouched.
    import opensak.gui.icon as icon_mod
    from opensak.filters.engine import FilterSet, SortSpec, WhereClauseFilter

    window = seeded_window
    monkeypatch.setattr(icon_mod.QMessageBox, "exec",
                         lambda self: icon_mod.QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(window, "_show_filter_dialog", lambda fs, name: None)

    fs = FilterSet()
    fs.add(WhereClauseFilter("NOT VALID SQL @@@"))
    window._on_filter_applied(fs, SortSpec("name"), "Bad SQL")
    qtbot.wait(50)

    assert window._cache_table.row_count() == TOTAL  # unchanged, not emptied
    assert window._active_filter_name != "Bad SQL"


def test_where_clause_clear_restores_full_count(seeded_window, qtbot):
    # After applying a WHERE filter, _clear_filter restores all rows.
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
    # FilterDialog always shows a WHERE tab.
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    assert dialog._where_tab is not None
    tab_labels = [dialog._tabs.tabText(i) for i in range(dialog._tabs.count())]
    assert any("where" in t.lower() or "sql" in t.lower() for t in tab_labels)
    dialog.close()


def test_where_tab_builds_where_clause_filter(seeded_window, qtbot):
    # Entering SQL in the WHERE tab produces a WhereClauseFilter in the FilterSet.
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
    # An empty WHERE text field does not add a WhereClauseFilter to the set.
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
    # _reset_all() empties the WHERE SQL field and hides the error label.
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    dialog._where_sql_general.setPlainText("difficulty >= 3")
    dialog._reset_all()

    assert dialog._where_sql_general.toPlainText() == ""
    assert not dialog._where_error_label.isVisible()
    dialog.close()


def test_where_tab_validate_valid_sql_returns_none(seeded_window, qtbot):
    # _validate_where_sql returns None for syntactically valid SQL.
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    error = dialog._validate_where_sql("difficulty >= 3")
    assert error is None
    dialog.close()


def test_where_tab_validate_invalid_sql_returns_error(seeded_window, qtbot):
    # _validate_where_sql returns a non-empty error string for bad SQL.
    from opensak.gui.dialogs.filter_dialog import FilterDialog

    dialog = FilterDialog(parent=seeded_window)
    qtbot.addWidget(dialog)

    error = dialog._validate_where_sql("NOT VALID SQL @@@")
    assert error is not None
    assert len(error) > 0
    dialog.close()


# ── TextSearchFilter integration via _on_filter_applied ───────────────────────


def test_text_search_description_narrows_rows(seeded_window, qtbot):
    # Seeded: GC99999/GCAAA02 have long_description "Solve the puzzle first."
    # GC12345/GCAAA01 have "A longer description." — "puzzle" matches only 2.
    from opensak.filters.engine import FilterSet, SortSpec, TextSearchFilter

    window = seeded_window
    assert window._cache_table.row_count() == TOTAL

    fs = FilterSet()
    fs.add(TextSearchFilter("puzzle", search_description=True,
                            search_logs=False, search_notes=False, search_hint=False))
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "Puzzle")
    qtbot.wait(50)

    assert window._cache_table.row_count() == 2


def test_text_search_hint_narrows_rows(seeded_window, qtbot):
    # "rock" appears only in GC12345/GCAAA01 hint ("Under a rock.").
    from opensak.filters.engine import FilterSet, SortSpec, TextSearchFilter

    window = seeded_window
    assert window._cache_table.row_count() == TOTAL

    fs = FilterSet()
    fs.add(TextSearchFilter("rock", search_description=False,
                            search_logs=False, search_notes=False, search_hint=True))
    window._on_filter_applied(fs, SortSpec("name", ascending=True), "Rock hint")
    qtbot.wait(50)

    assert window._cache_table.row_count() == 2


def test_text_search_no_match_returns_zero(seeded_window, qtbot, monkeypatch):
    # Issue #444: a filter matching zero caches must NOT be applied — the
    # previous view stays as-is, a warning is shown, and "Set filter" is
    # reopened with the same (rejected) criteria so the user can adjust them.
    import opensak.gui.icon as icon_mod
    from opensak.filters.engine import FilterSet, SortSpec, TextSearchFilter

    window = seeded_window
    assert window._cache_table.row_count() == TOTAL

    warned = []
    monkeypatch.setattr(icon_mod.QMessageBox, "exec",
                         lambda self: warned.append(self.text()) or icon_mod.QMessageBox.StandardButton.Ok)
    reopened = []
    monkeypatch.setattr(window, "_show_filter_dialog",
                         lambda fs, name: reopened.append((fs, name)))

    fs = FilterSet()
    fs.add(TextSearchFilter("zzznomatch"))
    window._on_filter_applied(fs, SortSpec("name"), "No match")
    qtbot.wait(50)

    assert warned  # the "no results" warning was shown
    assert window._cache_table.row_count() == TOTAL  # previous view untouched
    assert window._active_filter_name != "No match"  # filter was NOT committed
    assert reopened and reopened[0][1] == "No match"  # dialog reopened with same criteria


def test_filter_zero_results_leaves_existing_filter_active(seeded_window, qtbot, monkeypatch):
    # Applying a second, zero-result filter must not clobber an already
    # active filter — the view stays on the first filter's results.
    import opensak.gui.icon as icon_mod
    from opensak.filters.engine import FilterSet, SortSpec, CacheTypeFilter, TextSearchFilter

    window = seeded_window
    monkeypatch.setattr(icon_mod.QMessageBox, "exec",
                         lambda self: icon_mod.QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(window, "_show_filter_dialog", lambda fs, name: None)

    fs1 = FilterSet(mode="AND")
    fs1.add(CacheTypeFilter(["Traditional Cache"]))
    window._on_filter_applied(fs1, SortSpec("name", ascending=True), "Traditional only")
    qtbot.wait(50)
    assert window._cache_table.row_count() == 2
    assert window._active_filter_name == "Traditional only"

    fs2 = FilterSet()
    fs2.add(TextSearchFilter("zzznomatch"))
    window._on_filter_applied(fs2, SortSpec("name"), "No match")
    qtbot.wait(50)

    assert window._cache_table.row_count() == 2  # unchanged — still "Traditional only"
    assert window._active_filter_name == "Traditional only"


def test_where_exists_logs_narrows_rows(seeded_window, qtbot):
    # "TFTC" appears only in GC12345/GCAAA01 log texts.
    from opensak.filters.engine import FilterSet, SortSpec, WhereClauseFilter

    window = seeded_window
    assert window._cache_table.row_count() == TOTAL

    fs = FilterSet()
    fs.add(WhereClauseFilter(
        "EXISTS (SELECT 1 FROM logs WHERE logs.cache_id = caches.id AND logs.text LIKE '%TFTC%')"
    ))
    window._on_filter_applied(fs, SortSpec("name"), "TFTC logs")
    qtbot.wait(50)

    assert window._cache_table.row_count() == 2
