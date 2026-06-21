# tests/unit-tests/test_cache_table.py — cache list model, delegates, view.

import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt, QModelIndex
from PySide6.QtGui import QPixmap, QPainter

from opensak.gui import cache_table as ct
from opensak.gui.cache_table import (
    CacheTableModel,
    CacheTableView,
    SizeBarDelegate,
    GcCodeDelegate,
    _bearing_deg,
    _bearing_deg_batch,
    _bearing_compass,
    _gc_sort_key,
)
from opensak.db.models import Cache, UserNote
from opensak.utils.types import CoordFormat, DateFormat

ALL_COLUMNS = [
    "gc_code", "name", "cache_type", "difficulty", "terrain", "container",
    "country", "state", "county", "distance", "found", "placed_by",
    "hidden_date", "last_log", "log_count", "dnf", "premium_only", "archived",
    "favorite", "corrected", "latitude", "longitude", "found_date", "dnf_date",
    "first_to_find", "favorite_points", "user_flag", "bearing", "user_sort",
    "user_data_1", "user_data_2", "user_data_3", "user_data_4",
]


def _cache(**kw):
    c = Cache(gc_code=kw.pop("gc_code", "GC1"), name=kw.pop("name", "Name"))
    # Transient instances do not get DB-level defaults; set sane visible defaults.
    c.available = kw.pop("available", True)
    c.archived = kw.pop("archived", False)
    c.found = kw.pop("found", False)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _note(lat=56.0, lon=13.0, corrected=True):
    return UserNote(corrected_lat=lat, corrected_lon=lon, is_corrected=corrected)


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch, qapp):
    s = SimpleNamespace(
        home_lat=55.0, home_lon=12.0, use_miles=False,
        coord_format=CoordFormat.DMM, date_format=DateFormat.DMY,
        gc_username="", map_provider="google",
    )
    s.get_maps_url = lambda lat, lon: f"https://maps?{lat},{lon}"
    monkeypatch.setattr(ct, "get_settings", lambda: s)
    monkeypatch.setattr("opensak.gui.settings.get_settings", lambda: s)
    monkeypatch.setattr(ct, "get_cache_type_icon", lambda *a, **k: QPixmap(4, 4))
    monkeypatch.setattr(ct, "get_cache_size_icon", lambda *a, **k: QPixmap(4, 4))
    return s


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(ct, "_get_active_columns", lambda: list(ALL_COLUMNS))
    return CacheTableModel()


# ── pure helpers ────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_bearing_deg_cardinal(self):
        # due north -> ~0, due east -> ~90
        assert _bearing_deg(0, 0, 1, 0) == pytest.approx(0, abs=1)
        assert _bearing_deg(0, 0, 0, 1) == pytest.approx(90, abs=1)

    def test_bearing_compass_format(self):
        out = _bearing_compass(0)
        assert "0°" in out

    def test_bearing_batch_numpy(self):
        out = _bearing_deg_batch(0, 0, [1, 0], [0, 1])
        assert len(out) == 2
        assert float(out[0]) == pytest.approx(0, abs=1)

    def test_bearing_batch_fallback_without_numpy(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "numpy", None)
        out = _bearing_deg_batch(0, 0, [1.0], [0.0])
        assert isinstance(out, list)
        assert out[0] == pytest.approx(0, abs=1)

    def test_gc_sort_key_pads(self):
        assert _gc_sort_key("GC1D") < _gc_sort_key("GC1DCA")
        assert _gc_sort_key("") == ""
        assert _gc_sort_key("ABC").startswith("GC")


# ── model basics ────────────────────────────────────────────────────────────────

class TestModelBasics:
    def test_dimensions(self, model):
        model.load([_cache(gc_code="GC1"), _cache(gc_code="GC2")])
        assert model.rowCount() == 2
        assert model.columnCount() == len(ALL_COLUMNS)

    def test_cache_at_bounds(self, model):
        model.load([_cache()])
        assert model.cache_at(0) is not None
        assert model.cache_at(5) is None

    def test_header_display_and_alignment(self, model):
        name = model.headerData(1, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        assert isinstance(name, str) and name
        align = model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole)
        assert align == Qt.AlignmentFlag.AlignCenter
        assert model.headerData(0, Qt.Orientation.Vertical) is None

    def test_data_invalid_index(self, model):
        assert model.data(QModelIndex()) is None

    def test_reload_columns(self, model, monkeypatch):
        monkeypatch.setattr(ct, "_get_active_columns", lambda: ["gc_code"])
        model.reload_columns()
        assert model._columns == ["gc_code"]


# ── display values per column ───────────────────────────────────────────────────

class TestDisplayValues:
    def test_text_columns(self, model):
        c = _cache(gc_code="GC9", name="Hello", country="DK", state="Z",
                   county="Cty", placed_by="Owner")
        dv = model._display_value
        assert dv(c, "gc_code") == "GC9"
        assert dv(c, "name") == "Hello"
        assert dv(c, "country") == "DK"
        assert dv(c, "state") == "Z"
        assert dv(c, "county") == "Cty"
        assert dv(c, "placed_by") == "Owner"
        assert dv(c, "cache_type") == ""   # icon only
        assert dv(c, "container") == ""    # icon only
        assert dv(c, "unknown_col") == ""

    def test_difficulty_terrain(self, model):
        assert model._display_value(_cache(difficulty=2.5), "difficulty") == "2.5"
        assert model._display_value(_cache(difficulty=None), "difficulty") == "?"
        assert model._display_value(_cache(terrain=3.0), "terrain") == "3.0"
        assert model._display_value(_cache(terrain=None), "terrain") == "?"

    def test_distance_km_and_miles(self, model, fake_settings):
        c = _cache()
        c.id = 1
        model._distances = {1: 10.0}
        assert model._display_value(c, "distance") == "10.0 km"
        fake_settings.use_miles = True
        assert "mi" in model._display_value(c, "distance")
        assert model._display_value(_cache(), "distance") == "?"  # id not in dist

    def test_bearing(self, model, monkeypatch):
        monkeypatch.setattr(
            ct, "tr",
            lambda key, **kw: "N NE E SE S SW W NW" if key == "bearing_dirs" else key,
        )
        c = _cache()
        c.id = 2
        model._bearings = {2: 90.0}
        assert "°" in model._display_value(c, "bearing")
        assert model._display_value(_cache(), "bearing") == "?"

    def test_boolean_markers(self, model):
        assert model._display_value(_cache(found=True), "found") == "✓"
        assert model._display_value(_cache(found=False), "found") == ""
        assert model._display_value(_cache(dnf=True), "dnf") == "DNF"
        assert model._display_value(_cache(premium_only=True), "premium_only") == "P"
        assert model._display_value(_cache(archived=True), "archived") == "✓"
        assert model._display_value(_cache(favorite_point=True), "favorite") == "★"
        assert model._display_value(_cache(first_to_find=True), "first_to_find") == "FTF"
        assert model._display_value(_cache(user_flag=True), "user_flag") == "🚩"

    def test_dates(self, model, fake_settings):
        d = datetime(2024, 3, 1)
        # DMY (default in fake_settings)
        assert model._display_value(_cache(hidden_date=d), "hidden_date") == "01.03.2024"
        assert model._display_value(_cache(last_log_date=d), "last_log") == "01.03.2024"
        assert model._display_value(_cache(found_date=d), "found_date") == "01.03.2024"
        assert model._display_value(_cache(dnf_date=d), "dnf_date") == "01.03.2024"
        assert model._display_value(_cache(), "hidden_date") == ""
        # MDY
        fake_settings.date_format = DateFormat.MDY
        assert model._display_value(_cache(hidden_date=d), "hidden_date") == "03/01/2024"
        # YMD
        fake_settings.date_format = DateFormat.YMD
        assert model._display_value(_cache(hidden_date=d), "hidden_date") == "2024-03-01"
        # LOCALE — just verify it's non-empty (format depends on OS locale)
        fake_settings.date_format = DateFormat.LOCALE
        result = model._display_value(_cache(hidden_date=d), "hidden_date")
        assert result

    def test_counts_and_user_fields(self, model):
        assert model._display_value(_cache(log_count=7), "log_count") == "7"
        assert model._display_value(_cache(favorite_points=12), "favorite_points") == "12"
        assert model._display_value(_cache(user_sort=3), "user_sort") == "3"
        assert model._display_value(_cache(user_data_1="x"), "user_data_1") == "x"
        assert model._display_value(_cache(user_data_4="z"), "user_data_4") == "z"

    def test_corrected_marker(self, model):
        plain = _cache()
        plain.user_note = None
        assert model._display_value(plain, "corrected") == ""
        corr = _cache()
        corr.user_note = _note()
        assert model._display_value(corr, "corrected") == "📍"

    def test_lat_lon_uses_corrected(self, model):
        c = _cache(latitude=55.0, longitude=12.0)
        c.user_note = _note(56.0, 13.0)
        assert model._display_value(c, "latitude") != ""
        assert model._display_value(c, "longitude") != ""
        empty = _cache(latitude=None, longitude=None)
        empty.user_note = None
        assert model._display_value(empty, "latitude") == ""
        assert model._display_value(empty, "longitude") == ""


# ── data() roles ────────────────────────────────────────────────────────────────

class TestDataRoles:
    def test_display_role(self, model):
        model.load([_cache(gc_code="GCABC")])
        idx = model.index(0, 0)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "GCABC"

    def test_alignment_role(self, model):
        model.load([_cache()])
        center_idx = model.index(0, ALL_COLUMNS.index("difficulty"))
        assert model.data(center_idx, Qt.ItemDataRole.TextAlignmentRole) == Qt.AlignmentFlag.AlignCenter
        left_idx = model.index(0, ALL_COLUMNS.index("name"))
        assert model.data(left_idx, Qt.ItemDataRole.TextAlignmentRole) != Qt.AlignmentFlag.AlignCenter

    def test_font_role_italic_when_found(self, model):
        model.load([_cache(found=True)])
        font = model.data(model.index(0, 0), Qt.ItemDataRole.FontRole)
        assert font is not None and font.italic()

    def test_tooltip_cache_type(self, model):
        model.load([_cache(cache_type="Unknown Cache")])
        tip = model.data(model.index(0, ALL_COLUMNS.index("cache_type")),
                         Qt.ItemDataRole.ToolTipRole)
        assert "Mystery" in tip

    def test_tooltip_latlon(self, model):
        c = _cache(latitude=55.0, longitude=12.0)
        c.user_note = None
        model.load([c])
        tip = model.data(model.index(0, ALL_COLUMNS.index("latitude")),
                         Qt.ItemDataRole.ToolTipRole)
        assert tip  # original-coords tooltip

    def test_decoration_role_icons(self, model):
        model.load([_cache(cache_type="Traditional Cache", container="small")])
        type_idx = model.index(0, ALL_COLUMNS.index("cache_type"))
        cont_idx = model.index(0, ALL_COLUMNS.index("container"))
        assert model.data(type_idx, Qt.ItemDataRole.DecorationRole) is not None
        assert model.data(cont_idx, Qt.ItemDataRole.DecorationRole) is not None

    def test_userrole_returns_cache_and_dict(self, model):
        model.load([_cache(container="Micro", cache_type="Virtual Cache")])
        idx = model.index(0, 0)
        assert isinstance(model.data(idx, Qt.ItemDataRole.UserRole), Cache)
        d = model.data(idx, Qt.ItemDataRole.UserRole + 10)
        assert d["size"] == "micro" and d["type"] == "virtual cache"

    def test_unhandled_role_none(self, model):
        model.load([_cache()])
        assert model.data(model.index(0, 0), Qt.ItemDataRole.WhatsThisRole) is None


# ── icon key mapping ────────────────────────────────────────────────────────────

class TestIconKeys:
    def test_type_icon_archived_disabled(self):
        assert CacheTableModel._type_icon_key(_cache(archived=True)) == "archived"
        assert CacheTableModel._type_icon_key(_cache(archived=False, available=False)) == "disabled"

    def test_type_icon_mapping(self):
        assert CacheTableModel._type_icon_key(_cache(cache_type="Multi-cache")) == "multi"
        assert CacheTableModel._type_icon_key(_cache(cache_type="Weird Type")) == "unknown"

    def test_size_icon_mapping(self):
        assert CacheTableModel._size_icon_key(_cache(container="large")) == "large"
        assert CacheTableModel._size_icon_key(_cache(container="whatever")) == "other"


# ── effective coords ────────────────────────────────────────────────────────────

class TestEffectiveCoords:
    def test_returns_corrected(self):
        c = _cache(latitude=1.0, longitude=2.0)
        c.user_note = _note(9.0, 8.0)
        assert CacheTableModel._effective_coords(c) == (9.0, 8.0)

    def test_returns_original_when_not_corrected(self):
        c = _cache(latitude=1.0, longitude=2.0)
        c.user_note = None
        assert CacheTableModel._effective_coords(c) == (1.0, 2.0)


# ── sort ────────────────────────────────────────────────────────────────────────

class TestSort:
    def _loaded(self, model, caches):
        model.load(caches)
        return model

    def test_sort_out_of_range_noop(self, model):
        model.load([_cache()])
        model.sort(999)  # no crash

    def test_sort_by_name(self, model):
        m = self._loaded(model, [_cache(gc_code="A", name="Beta"), _cache(gc_code="B", name="alpha")])
        m.sort(ALL_COLUMNS.index("name"), Qt.SortOrder.AscendingOrder)
        assert m.cache_at(0).name == "alpha"

    def test_sort_by_difficulty_desc(self, model):
        m = self._loaded(model, [_cache(gc_code="A", difficulty=1.0), _cache(gc_code="B", difficulty=5.0)])
        m.sort(ALL_COLUMNS.index("difficulty"), Qt.SortOrder.DescendingOrder)
        assert m.cache_at(0).difficulty == 5.0

    def test_sort_by_gc_code(self, model):
        m = self._loaded(model, [_cache(gc_code="GC1DCA"), _cache(gc_code="GC1D")])
        m.sort(ALL_COLUMNS.index("gc_code"), Qt.SortOrder.AscendingOrder)
        assert m.cache_at(0).gc_code == "GC1D"

    def test_sort_distance_and_bearing(self, model):
        a, b = _cache(gc_code="A"), _cache(gc_code="B")
        a.id, b.id = 1, 2
        m = self._loaded(model, [a, b])
        m._distances = {1: 50.0, 2: 5.0}
        m._bearings = {1: 300.0, 2: 10.0}
        m.sort(ALL_COLUMNS.index("distance"), Qt.SortOrder.AscendingOrder)
        assert m.cache_at(0).gc_code == "B"
        m.sort(ALL_COLUMNS.index("bearing"), Qt.SortOrder.AscendingOrder)
        assert m.cache_at(0).gc_code == "B"

    def test_sort_various_columns_do_not_crash(self, model):
        d = datetime(2024, 1, 1)
        caches = [
            _cache(gc_code="A", terrain=2.0, found=True, log_count=3, favorite_points=1,
                   user_sort=2, first_to_find=True, user_flag=True, container="micro",
                   hidden_date=d, last_log_date=d, found_date=d, dnf_date=d,
                   latitude=55.0, longitude=12.0, country="DK"),
            _cache(gc_code="B", country="ZZ"),
        ]
        caches[1].user_note = None
        caches[0].user_note = _note(1.0, 2.0)
        m = self._loaded(model, caches)
        for col in ("terrain", "found", "corrected", "log_count", "last_log",
                    "hidden_date", "found_date", "dnf_date", "first_to_find",
                    "user_flag", "user_sort", "favorite_points", "container",
                    "latitude", "longitude", "country"):
            m.sort(ALL_COLUMNS.index(col), Qt.SortOrder.AscendingOrder)
            m.sort(ALL_COLUMNS.index(col), Qt.SortOrder.DescendingOrder)

    def test_sort_emits_signal(self, model):
        got = []
        model.sort_changed.connect(lambda c, a: got.append((c, a)))
        model.load([_cache()])
        model.sort(ALL_COLUMNS.index("name"), Qt.SortOrder.AscendingOrder)
        assert got and got[-1][0] == "name"

    def test_sort_none_vs_string_no_crash(self, model):
        # Regression: Optional[str] columns (country, state, etc.) caused
        # TypeError when some caches had None and others had a string value,
        # because the else branch returned int(0) for None and str for strings.
        caches = [
            _cache(gc_code="A", country="Denmark", state="Region H", placed_by="Alice"),
            _cache(gc_code="B"),  # country/state/placed_by all None
        ]
        model.load(caches)
        for col in ("country", "state", "county", "placed_by", "user_data_1"):
            model.sort(ALL_COLUMNS.index(col), Qt.SortOrder.AscendingOrder)
            model.sort(ALL_COLUMNS.index(col), Qt.SortOrder.DescendingOrder)


# ── flags / setData (needs DB) ──────────────────────────────────────────────────

class TestSetData:
    def test_flags_editable_columns(self, model):
        model.load([_cache()])
        uf = model.index(0, ALL_COLUMNS.index("user_flag"))
        assert model.flags(uf) & Qt.ItemFlag.ItemIsEditable
        nf = model.index(0, ALL_COLUMNS.index("name"))
        assert not (model.flags(nf) & Qt.ItemFlag.ItemIsEditable)

    def test_setdata_invalid_or_wrong_column(self, model):
        model.load([_cache()])
        assert model.setData(QModelIndex(), None) is False
        assert model.setData(model.index(0, ALL_COLUMNS.index("name")), None) is False

    def test_setdata_toggles_user_flag(self, model, db_session, make_cache):
        c = make_cache(gc_code="GCSET")
        db_session.add(c)
        db_session.commit()
        cache = _cache(gc_code="GCSET", user_flag=False)
        model.load([cache])
        fired = []
        model.flags_changed.connect(lambda: fired.append(True))
        ok = model.setData(model.index(0, ALL_COLUMNS.index("user_flag")), None)
        assert ok is True
        assert cache.user_flag is True
        assert fired == [True]

    def test_setdata_toggles_first_to_find(self, model, db_session, make_cache):
        c = make_cache(gc_code="GCFTF")
        db_session.add(c)
        db_session.commit()
        cache = _cache(gc_code="GCFTF", first_to_find=False)
        model.load([cache])
        ok = model.setData(model.index(0, ALL_COLUMNS.index("first_to_find")), None)
        assert ok is True
        assert cache.first_to_find is True


# ── delegates ───────────────────────────────────────────────────────────────────

class TestDelegates:
    def _paint(self, delegate, model, col, *, selected=False):
        # Paint column ``col`` of row 0 via a real QModelIndex.
        from PySide6.QtWidgets import QStyleOptionViewItem, QStyle
        from PySide6.QtCore import QRect
        idx = model.index(0, ALL_COLUMNS.index(col))
        pm = QPixmap(80, 24)
        painter = QPainter(pm)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 80, 24)
        if selected:
            opt.state |= QStyle.StateFlag.State_Selected
        delegate.paint(painter, opt, idx)
        painter.end()

    def test_size_bar_delegate_paints_physical(self, model):
        model.load([_cache(container="small", cache_type="traditional cache")])
        d = SizeBarDelegate()
        self._paint(d, model, "container")
        self._paint(d, model, "container", selected=True)

    def test_size_bar_delegate_paints_letter(self, model):
        model.load([_cache(container="other", cache_type="virtual cache")])
        self._paint(SizeBarDelegate(), model, "container")

    def test_size_bar_sizehint(self, model):
        from PySide6.QtWidgets import QStyleOptionViewItem
        model.load([_cache()])
        idx = model.index(0, ALL_COLUMNS.index("container"))
        sh = SizeBarDelegate().sizeHint(QStyleOptionViewItem(), idx)
        assert sh.height() >= 20

    def test_gc_code_delegate_colors(self, model, fake_settings):
        d = GcCodeDelegate()
        model.load([_cache(gc_code="GCARCH", archived=True)])
        self._paint(d, model, "gc_code")                       # archived bg
        model.load([_cache(gc_code="GCF", found=True, available=True, archived=False)])
        self._paint(d, model, "gc_code")                       # found green
        self._paint(d, model, "gc_code", selected=True)        # selected
        plain = _cache(gc_code="GCP", found=False, available=True, archived=False)
        plain.owner_name = None
        model.load([plain])
        self._paint(d, model, "gc_code")                       # default path

    def test_gc_code_delegate_disabled_text(self, model):
        c = _cache(gc_code="GCDIS", found=False, available=False, archived=False)
        c.owner_name = None
        model.load([c])
        self._paint(GcCodeDelegate(), model, "gc_code")

    def test_gc_code_delegate_placed_by_user(self, model, fake_settings):
        fake_settings.gc_username = "me"
        c = _cache(gc_code="GCMINE", found=False, available=True, archived=False)
        c.owner_name = "Me"
        model.load([c])
        self._paint(GcCodeDelegate(), model, "gc_code")

    def test_gc_code_delegate_owner_match_irregular_whitespace(self, model, fake_settings):
        """Issue #272: some GPX exports embed non-breaking spaces or doubled
        whitespace inside multi-word owner names. The owner match must still
        succeed once both sides are whitespace-normalized."""
        fake_settings.gc_username = "Cheminer  Will"      # double space, as typed in Settings
        c = _cache(gc_code="GCNBSP", found=False, available=True, archived=False)
        c.owner_name = "Cheminer\xa0Will"                  # non-breaking space, from GPX
        model.load([c])
        d = GcCodeDelegate()
        idx = model.index(0, ALL_COLUMNS.index("gc_code"))
        assert d._bg_color(idx) == GcCodeDelegate._COLOR_PLACED

    def test_gc_code_delegate_owner_match_gsak_stats_suffix(self, model, fake_settings):
        """Issue #272 (confirmed root cause, see Owned_Export.gpx): GSAK's
        statistics macro (e.g. FindStatGen) appends found/hide counts to the
        owner field on export, e.g. 'Cheminer Will (F=1361 H=54)'. This must
        still match the plain username configured in Settings."""
        fake_settings.gc_username = "Cheminer Will"
        c = _cache(gc_code="GCSTATS", found=False, available=True, archived=False)
        c.owner_name = "Cheminer Will (F=1361 H=54)"
        model.load([c])
        d = GcCodeDelegate()
        idx = model.index(0, ALL_COLUMNS.index("gc_code"))
        assert d._bg_color(idx) == GcCodeDelegate._COLOR_PLACED

    def test_gc_code_delegate_uses_owner_not_placed_by(self, model, fake_settings):
        """Issue #270: an adopted cache (owner differs from the original
        placer) must still be colored as 'owned' when owner_name matches —
        even though placed_by does not."""
        fake_settings.gc_username = "AdoptedOwner"
        c = _cache(gc_code="GCADOPT", found=False, available=True, archived=False)
        c.placed_by = "OriginalPlacer"
        c.owner_name = "AdoptedOwner"
        model.load([c])
        d = GcCodeDelegate()
        idx = model.index(0, ALL_COLUMNS.index("gc_code"))
        assert d._bg_color(idx) == GcCodeDelegate._COLOR_PLACED

    def test_gc_code_bg_none_when_no_cache(self, model):
        d = GcCodeDelegate()

        class _Idx:
            def data(self, role):
                return None
        assert d._bg_color(_Idx()) is None


# ── view ────────────────────────────────────────────────────────────────────────

@pytest.fixture
def view(monkeypatch, qtbot):
    monkeypatch.setattr(ct, "_get_active_columns", lambda: list(ALL_COLUMNS))
    monkeypatch.setattr(ct, "get_column_widths", lambda: {})
    monkeypatch.setattr(ct, "set_column_widths", lambda w: None)
    v = CacheTableView()
    qtbot.addWidget(v)
    return v


class TestView:
    def test_construction_sets_delegates(self, view):
        # container + gc_code columns get custom delegates
        assert isinstance(view.itemDelegateForColumn(ALL_COLUMNS.index("container")),
                          SizeBarDelegate)
        assert isinstance(view.itemDelegateForColumn(ALL_COLUMNS.index("gc_code")),
                          GcCodeDelegate)

    def test_load_and_counts(self, view):
        view.load_caches([_cache(gc_code="A"), _cache(gc_code="B", user_flag=True)])
        assert view.row_count() == 2
        assert len(view.get_all_caches()) == 2
        assert len(view.get_flagged_caches()) == 1

    def test_select_by_gc_code_emits(self, view):
        view.load_caches([_cache(gc_code="A"), _cache(gc_code="B")])
        got = []
        view.cache_selected.connect(lambda c: got.append(c.gc_code))
        view.select_by_gc_code("B")
        assert view.selected_cache().gc_code == "B"
        assert got and got[-1] == "B"

    def test_select_by_gc_code_missing(self, view):
        view.load_caches([_cache(gc_code="A")])
        view.select_by_gc_code("ZZZ")  # no crash, no selection change

    def test_selected_cache_none_when_empty(self, view):
        view.load_caches([])
        assert view.selected_cache() is None

    def test_reload_columns(self, view, monkeypatch):
        monkeypatch.setattr(ct, "_get_active_columns", lambda: ["gc_code", "name"])
        view.reload_columns()
        assert view._model.columnCount() == 2

    def test_apply_sort_and_model_sort_changed(self, view):
        view.load_caches([_cache(gc_code="B", name="b"), _cache(gc_code="A", name="a")])
        fired = []
        view.sort_changed.connect(lambda c, a: fired.append((c, a)))
        view.apply_sort("name", True)
        assert view._last_sort_col == ALL_COLUMNS.index("name")
        assert view._last_sort_asc is True
        # apply_sort blocks signals, so a user sort exercises the relay
        view._model.sort(ALL_COLUMNS.index("gc_code"), Qt.SortOrder.AscendingOrder)
        assert fired and fired[-1][0] == "gc_code"

    def test_apply_sort_unknown_column_noop(self, view):
        view.load_caches([_cache()])
        view.apply_sort("does_not_exist", True)  # no crash

    def test_double_click_corrected_opens_editor(self, view, monkeypatch):
        view.load_caches([_cache(gc_code="A")])
        called = []
        monkeypatch.setattr(view, "_edit_corrected", lambda c: called.append(c.gc_code))
        idx = view._model.index(0, ALL_COLUMNS.index("corrected"))
        view._on_double_clicked(idx)
        assert called == ["A"]

    def test_double_click_other_column_noop(self, view, monkeypatch):
        view.load_caches([_cache(gc_code="A")])
        called = []
        monkeypatch.setattr(view, "_edit_corrected", lambda c: called.append(c))
        view._on_double_clicked(view._model.index(0, ALL_COLUMNS.index("name")))
        assert called == []

    def test_copy_to_clipboard(self, view):
        view._copy_to_clipboard("hello")
        from PySide6.QtWidgets import QApplication
        assert QApplication.clipboard().text() == "hello"

    def test_column_resized_persists(self, view, monkeypatch):
        saved = {}
        monkeypatch.setattr(ct, "get_column_widths", lambda: dict(saved))
        monkeypatch.setattr(ct, "set_column_widths", lambda w: saved.update(w))
        view._applying_widths = False
        view._on_column_resized(0, 50, 123)
        assert saved.get("gc_code") == 123

    def test_column_resized_ignored_while_applying(self, view, monkeypatch):
        called = []
        monkeypatch.setattr(ct, "set_column_widths", lambda w: called.append(w))
        view._applying_widths = True
        view._on_column_resized(0, 50, 99)
        assert called == []

    # ── DB-backed operations ─────────────────────────────────────────────────

    def test_toggle_found(self, view, db_session, make_cache):
        db_session.add(make_cache(gc_code="GCTF"))
        db_session.commit()
        cache = _cache(gc_code="GCTF", found=False)
        view.load_caches([cache])
        view._toggle_found(cache, True)
        assert cache.found is True

    def test_save_and_clear_corrected(self, view, db_session, make_cache):
        db_session.add(make_cache(gc_code="GCCC"))
        db_session.commit()
        cache = _cache(gc_code="GCCC", latitude=55.0, longitude=12.0)
        view.load_caches([cache])
        view._save_corrected(cache, 56.0, 13.0)
        refreshed = view._model.cache_at(0)
        assert refreshed.user_note is not None
        assert refreshed.user_note.is_corrected is True
        view._clear_corrected(refreshed)
        assert view._model.cache_at(0).user_note.is_corrected is False

    def test_save_corrected_missing_cache_noop(self, view, db_session):
        cache = _cache(gc_code="GCNONE")
        view.load_caches([cache])
        view._save_corrected(cache, 1.0, 2.0)  # not in DB -> early return, no crash

    # ── dialog openers (dialogs faked) ───────────────────────────────────────

    def test_edit_corrected_saves_on_accept(self, view, monkeypatch):
        cache = _cache(gc_code="A", latitude=55.0, longitude=12.0)
        cache.user_note = None

        class FakeDlg:
            def __init__(self, **kw):
                pass
            def exec(self):
                return 1
            def get_coords(self):
                return 56.0, 13.0
        monkeypatch.setattr(
            "opensak.gui.dialogs.corrected_coords_dialog.CorrectedCoordsDialog", FakeDlg
        )
        saved = []
        monkeypatch.setattr(view, "_save_corrected", lambda c, la, lo: saved.append((la, lo)))
        view._edit_corrected(cache)
        assert saved == [(56.0, 13.0)]

    def test_edit_corrected_cancelled(self, view, monkeypatch):
        cache = _cache(gc_code="A", latitude=55.0, longitude=12.0)
        cache.user_note = None

        class FakeDlg:
            def __init__(self, **kw):
                pass
            def exec(self):
                return 0
            def get_coords(self):
                raise AssertionError("should not be called")
        monkeypatch.setattr(
            "opensak.gui.dialogs.corrected_coords_dialog.CorrectedCoordsDialog", FakeDlg
        )
        saved = []
        monkeypatch.setattr(view, "_save_corrected", lambda *a: saved.append(a))
        view._edit_corrected(cache)
        assert saved == []

    def test_open_converter(self, view, monkeypatch):
        opened = []

        class FakeDlg:
            def __init__(self, *a, **k):
                opened.append(a)
            def exec(self):
                return 0
        monkeypatch.setattr(
            "opensak.gui.dialogs.coord_converter_dialog.CoordConverterDialog", FakeDlg
        )
        view._open_converter(55.0, 12.0)
        assert opened

    def test_update_location(self, view, monkeypatch):
        class FakeDlg:
            def __init__(self, *a, **k):
                self.location_updated = SimpleNamespace(connect=lambda *x: None)
            def exec(self):
                return 0
        monkeypatch.setattr(
            "opensak.gui.dialogs.update_location_dialog.UpdateLocationDialog", FakeDlg
        )
        view._update_location("GC123")  # no crash

    # ── context menu ─────────────────────────────────────────────────────────

    def test_context_menu_builds(self, view, qtbot, monkeypatch):
        # Neutralise the blocking exec via a QMenu subclass.
        class _Menu(ct.QMenu):
            def exec(self, *a, **k):
                return None
        monkeypatch.setattr(ct, "QMenu", _Menu)
        monkeypatch.setattr(ct.webbrowser, "open", lambda *a, **k: None)
        from opensak.utils import flags
        monkeypatch.setattr(flags, "update_location", True, raising=False)

        c = _cache(gc_code="GCMENU", latitude=55.0, longitude=12.0)
        c.user_note = _note()  # corrected -> edit/clear branch
        view.load_caches([c])
        view.show()
        qtbot.addWidget(view)
        pos = view.visualRect(view._model.index(0, 0)).center()
        view._show_context_menu(pos)  # builds full menu, exec is a no-op

    def test_context_menu_no_cache_noop(self, view):
        view.load_caches([])
        from PySide6.QtCore import QPoint
        view._show_context_menu(QPoint(0, 0))  # indexAt -> invalid -> early return
