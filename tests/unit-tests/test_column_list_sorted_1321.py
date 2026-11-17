# tests/unit-tests/test_column_list_sorted_1321.py — issue #1321.
#
# The column list in the View window (ColumnChooserDialog) followed the
# internal column order, so with 50+ columns a column was hard to find
# (C3GPS). It is now alphabetical by the shown name, by the rules of the
# UI language. The order of the columns in the cache list is unchanged.

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from opensak import lang
from opensak.gui.dialogs import column_dialog as cd
from opensak.gui.dialogs.column_dialog import (
    ColumnChooserDialog, get_visible_columns, set_visible_columns, sorted_by_name,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from opensak import settings_store as ss
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)
    return fresh


@pytest.fixture
def language():
    """Switch the UI language for a test and switch back afterwards."""
    before = lang.current_language()
    yield lang.load_language
    lang.load_language(before)


def _cols(*names: str) -> list:
    return [(f"id{i}", name, 50, False) for i, name in enumerate(names)]


def _names(columns: list) -> list[str]:
    return [c[1] for c in columns]


class TestSortedByName:
    def test_case_insensitive(self, language):
        language("en")
        assert _names(sorted_by_name(_cols("bearing", "Archived", "Country"))) == [
            "Archived", "bearing", "Country"]

    def test_leading_icon_is_ignored(self, language):
        language("en")
        cols = _cols("Name", "🚩 User Flag", "🔒 Locked", "Archived")
        assert _names(sorted_by_name(cols)) == [
            "Archived", "🔒 Locked", "Name", "🚩 User Flag"]

    def test_danish_letters_after_z(self, language):
        language("da")
        cols = _cols("Ændret", "Zone", "Østlig", "Arkiveret", "Låst")
        assert _names(sorted_by_name(cols)) == [
            "Arkiveret", "Låst", "Zone", "Ændret", "Østlig"]

    def test_swedish_uses_swedish_rules(self, language):
        # OpenSAK's "se" is Swedish, not Qt's Northern Sami: å before ä/ö.
        language("se")
        cols = _cols("Ödla", "Ålder", "Ägare", "Zon")
        assert _names(sorted_by_name(cols)) == ["Zon", "Ålder", "Ägare", "Ödla"]

    def test_accents_sort_with_the_base_letter_in_french(self, language):
        language("fr")
        cols = _cols("Facile", "État", "Difficulté")
        assert _names(sorted_by_name(cols)) == ["Difficulté", "État", "Facile"]

    def test_keeps_the_whole_tuples(self, language):
        language("en")
        cols = [("b", "Beta", 10, True), ("a", "Alpha", 20, False)]
        assert sorted_by_name(cols) == [("a", "Alpha", 20, False), ("b", "Beta", 10, True)]


class TestDialog:
    @pytest.mark.parametrize("code", ["en", "da", "de"])
    def test_list_is_alphabetical(self, qtbot, store, language, code):
        language(code)
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        shown = [dlg._list.item(i).text() for i in range(dlg._list.count())]
        assert shown == _names(sorted_by_name(cd.get_all_columns()))
        assert len(shown) == len(cd.get_all_columns())

    def test_cache_list_order_is_unchanged(self, qtbot, store, language):
        language("en")
        set_visible_columns(["name", "gc_code", "found", "difficulty"])
        dlg = ColumnChooserDialog()
        qtbot.addWidget(dlg)
        for i in range(dlg._list.count()):
            item = dlg._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "country":
                item.setCheckState(Qt.CheckState.Checked)
        dlg._save_and_accept()
        saved = get_visible_columns()
        assert [c for c in saved if c != "country"][:4] == ["name", "gc_code", "found", "difficulty"]
        assert saved[-1] == "country"
