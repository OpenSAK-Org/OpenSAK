"""
src/opensak/gui/dialogs/filter_dialog.py — Komplet filter dialog.

Syv faner:
1. Generelt    — navn, type, D/T, afstand, fundet, tilgængelighed osv.
2. Datoer      — udlagt dato, fundet dato, DNF dato, seneste log dato
3. Øvrigt      — land/stat/kommune, user flag, DNF, favorit points
4. Linje/Polygon — caches langs en linje, i et polygon eller nær punkter
5. Attributter — alle Groundspeak attributter
6. Tekstsøgning — søg i beskrivelse, logs, noter og hint
7. Where       — rå SQL WHERE-betingelse

Understøtter gem/indlæs filterprofiler.
"""

from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QCheckBox, QPushButton, QRadioButton,
    QComboBox, QDoubleSpinBox, QSpinBox, QTabWidget, QWidget,
    QGroupBox, QScrollArea, QGridLayout,
    QDialogButtonBox, QMessageBox, QInputDialog, QFileDialog,
    QDateEdit, QSizePolicy, QFrame, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
)
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from PySide6.QtCore import QDate

from opensak.gui.widgets.center_point_picker import CenterPointPicker
from opensak.lang import tr
from opensak.filters.engine import (
    FilterSet, SortSpec,
    CacheTypeFilter, ContainerFilter,
    DifficultyFilter, TerrainFilter,
    FoundFilter, NotFoundFilter,
    AvailableFilter, ArchivedFilter, AvailabilityFilter,
    CountryFilter, StateFilter, CountyFilter,
    NameFilter, GcCodeFilter,
    PlacedByFilter, OwnerFilter, DistanceFilter,
    LinePolygonFilter, lookup_code_coords, user_flagged_codes,
    TextMatchFilter, TEXT_OPS_VALUELESS,
    AttributeFilter, HasTrackableFilter, HasCorrectedFilter, NoCorrectedFilter,
    PremiumFilter, NonPremiumFilter,
    WhereClauseFilter,
    UserFlagFilter, LockedFilter, DnfFilter, FtfFilter, FavoritePointsFilter,
    DateFilter, LEGACY_DATE_FILTER_FIELDS,
    TextSearchFilter,
    FilterProfile,
)
from opensak.filters.line_polygon import LP_MIN_POINTS, parse_points_text, read_points_file


# ── Groundspeak attribut definitioner ─────────────────────────────────────────
# Komplet officiel liste fra geocaching.com/about/icons.aspx
# Kilde for ID-numre: Groundspeak Live API / Project-GC database dump
# Format: (groundspeak_id, translation_key)
#
# TILLADELSER (Allowed/Not Allowed)
#   1  Dogs
#   32 Bicycles
#   33 Motorcycles
#   34 Off-road vehicles
#   35 Snowmobiles
#   36 Horses
#   16 Campfires
#   65 Trucks/RVs
#
# BETINGELSER (Yes/No)
#   6  Recommended for kids
#   7  Takes less than an hour
#   8  Scenic view
#   9  Significant hike
#   10 Difficult climbing
#   11 May require wading
#   12 May require swimming
#   13 Available at all times
#   14 Recommended at night
#   15 Available during winter
#   40 Stealth required
#   68 Needs maintenance
#   18 Dangerous animals / Livestock
#   49 Field puzzle
#   37 Night cache
#   53 Park and grab
#   57 Abandoned structure
#   43 Short hike (<1 km)
#   44 Medium hike (1-10 km)
#   45 Long hike (>10 km)
#   62 Seasonal access
#   22 Recommended for tourists
#   46 Yard (private residence)
#   60 Teamwork required
#   71 Challenge cache
#   72 Power trail
#   73 Bonus cache
#
# SPECIELLE (Yes/No)
#   67 Lost and Found tour
#   69 Partnership cache
#   70 GeoTour
#   74 Solution checker
#
# UDSTYR (Required/Not Required)
#   2  Access or parking fee
#   3  Climbing gear
#   4  Boat
#   5  Scuba gear
#   51 Flashlight required
#   50 UV light required
#   41 May require snowshoes
#   58 May require cross country skis
#   9  Special tool required  ← NOTE: 9 is "Significant hike" above
#      Actually ID 9 = Significant hike, special tool = different ID
#      From DB: id=9 is "Significant Hike", no separate "special tool" shown
#      Geocaching.com page says "Special tool required" — this maps to id=25
#      But DB shows id=25 = "Stroller accessible"? Let me use what geocaching.com image filenames show
#   25 Stroller accessible (from API result: id=41=stroller, but DB says 25)
#      → Use image filename as ground truth: stroller=41, special_tool=25 per some sources
#   64 Tree climbing required
#
# FARER (Present/Not Present)
#   17 Poisonous plants
#   18 Dangerous animals  (same id used for livestock above — they are the same attribute)
#   19 Ticks
#   20 Abandoned mines
#   21 Cliff / falling rocks
#   52 Hunting area
#   26 Dangerous area
#   28 Thorns  ← from geocaching.com list; 28 also = Public restrooms in some mappings
#              → Use geocaching.com image URL to verify: thorns = id 62 in some, 28 in others
#
# FACILITETER (Yes/No)
#   24 Wheelchair accessible
#   23 Parking nearby
#   27 Public transportation nearby
#   28 Drinking water nearby  ← conflict with Thorns above
#   29 Public restrooms nearby
#   30 Telephone nearby
#   21 Picnic tables nearby  ← conflict with Cliff above
#   47 Camping nearby
#   41 Stroller accessible
#   66 Fuel nearby
#   31 Food nearby
#
# NOTE: There are ID conflicts in various sources. The Project-GC DB dump (search result)
# is the most authoritative. We use those IDs. The DB showed:
#   17=Poisonous plants, 18=Dangerous Animals, 19=Ticks, 20=Abandoned mines, 21=Cliff/rocks
#   22=Scenic view(?), but geocaching.com page groups differently.
#   We keep IDs that are confirmed from the API JSON example (id=24=wheelchair, id=13=available,
#   id=7=onehour, id=6=kids, id=41=stroller, id=28=restrooms, id=26=public transport)
#   and the DB (1=dogs, 2=fee, 3=rappelling/climbing, 4=boat, 5=scuba, 6=kids, 7=onehour,
#   8=scenic, 9=hiking, 10=climbing, 11=wading, 12=swimming, 13=available, 14=night,
#   15=winter, 17=poisonoak, 18=dangerousanimals, 19=ticks, 20=mine, 21=cliff)

from opensak.utils.constants import ATTRIBUTES, CACHE_TYPES, CONTAINER_SIZES
from opensak.utils.types import TEXT_SIZE_MAP
from opensak.gui.icon_provider import get_cache_type_icon
from opensak.gui.settings import get_settings


# ── D/T spin box: snaps to valid 0.5-increment values (1.0–5.0) ──────────────

class DTSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox restricted to the nine standard D/T values (1.0–5.0 in 0.5 steps).
    The text field is read-only — value can only be changed via the arrow buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(1.0, 5.0)
        self.setSingleStep(0.5)
        self.setDecimals(1)
        self.setValue(1.0)
        self.lineEdit().setReadOnly(True)


# ── Hjælper widget: tre-tilstands checkbox (Ja / Nej / Ingen) ─────────────────

class TriStateBox(QWidget):
    """Tre-tilstands kontrol: Ja ✓ / Nej ✗ / Ingen (ignorér)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._ja  = QCheckBox(tr("yes"))
        self._nej = QCheckBox(tr("no"))
        layout.addWidget(self._ja)
        layout.addWidget(self._nej)

    @property
    def state(self) -> Optional[bool]:
        """None=ignorér, True=ja, False=nej"""
        if self._ja.isChecked() and not self._nej.isChecked():
            return True
        if self._nej.isChecked() and not self._ja.isChecked():
            return False
        return None

    def reset(self) -> None:
        self._ja.setChecked(False)
        self._nej.setChecked(False)


# ── Hjælper widget: tekstfilter med operator-vælger ───────────────────────────

# (operator, oversættelsesnøgle) i dropdown-rækkefølge. Nøglerne står som
# literals, så test_no_unused_keys kan finde dem.
_TEXT_OP_LABELS: tuple[tuple[str, str], ...] = (
    ("contains",     "filter_op_contains"),
    ("not_contains", "filter_op_not_contains"),
    ("equals",       "filter_op_equals"),
    ("not_equals",   "filter_op_not_equals"),
    ("starts_with",  "filter_op_starts_with"),
    ("ends_with",    "filter_op_ends_with"),
    ("in_list",      "filter_op_in_list"),
    ("not_in_list",  "filter_op_not_in_list"),
    ("empty",        "filter_op_empty"),
    ("not_empty",    "filter_op_not_empty"),
    ("regex",        "filter_op_regex"),
    ("not_regex",    "filter_op_not_regex"),
)


class TextFilterRow(QWidget):
    """Operator dropdown + value field for one text filter (name, owner, …).

    *placeholder* is shown for "contains"; the list and regex operators show
    their own hint, and "empty"/"not empty" disable the value field.
    """

    def __init__(self, label: str, placeholder: str, parent=None):
        super().__init__(parent)
        self.label = label
        self._placeholder = placeholder
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.combo = QComboBox()
        for op, key in _TEXT_OP_LABELS:
            self.combo.addItem(tr(key), op)
        self.edit = QLineEdit()
        layout.addWidget(self.combo)
        layout.addWidget(self.edit, 1)
        self.combo.currentIndexChanged.connect(self._on_op_changed)
        self._on_op_changed()

    def op(self) -> str:
        return self.combo.currentData()

    def set_op(self, op: str) -> None:
        index = self.combo.findData(op)
        self.combo.setCurrentIndex(index if index >= 0 else 0)

    def reset(self) -> None:
        self.set_op("contains")
        self.edit.clear()

    def build(self, cls: type[TextMatchFilter]) -> Optional[TextMatchFilter]:
        """Filter for the current input, or None when the row is not set."""
        op = self.op()
        if op in TEXT_OPS_VALUELESS:
            return cls("", op)
        text = self.edit.text().strip()
        return cls(text, op) if text else None

    def load(self, f) -> None:
        self.set_op(getattr(f, "op", "contains"))
        self.edit.setText(getattr(f, "text", ""))

    def _on_op_changed(self) -> None:
        op = self.op()
        self.edit.setEnabled(op not in TEXT_OPS_VALUELESS)
        if op in ("in_list", "not_in_list"):
            placeholder = tr("filter_in_list_placeholder")
        elif op in ("regex", "not_regex"):
            placeholder = tr("filter_regex_placeholder")
        elif op == "contains":
            placeholder = self._placeholder
        else:
            placeholder = ""
        self.edit.setPlaceholderText(placeholder)


# ── Hjælper widget: GSAK-lignende datofilter ──────────────────────────────────

# Dropdown-rækkefølge som i GSAK. Nøglerne står som literals, så
# test_no_unused_keys kan finde dem. "any" = intet filter.
_DATE_OP_LABELS: tuple[tuple[str, str], ...] = (
    ("any",          "filter_date_op_any"),
    ("on_or_before", "filter_date_op_on_or_before"),
    ("on_or_after",  "filter_date_op_on_or_after"),
    ("equal",        "filter_date_op_equal"),
    ("between",      "filter_date_op_between"),
    ("during",       "filter_date_op_during"),
    ("not_during",   "filter_date_op_not_during"),
    ("compare",      "filter_date_op_compare"),
)
_DATE_UNIT_LABELS: tuple[tuple[str, str], ...] = (
    ("days",   "filter_date_unit_days"),
    ("weeks",  "filter_date_unit_weeks"),
    ("months", "filter_date_unit_months"),
    ("years",  "filter_date_unit_years"),
)
_DATE_COMPARE_LABELS: tuple[tuple[str, str], ...] = (
    ("equal",          "filter_date_cmp_equal"),
    ("older",          "filter_date_cmp_older"),
    ("older_or_equal", "filter_date_cmp_older_or_equal"),
    ("newer",          "filter_date_cmp_newer"),
    ("newer_or_equal", "filter_date_cmp_newer_or_equal"),
    ("within",         "filter_date_cmp_within"),
    ("outside",        "filter_date_cmp_outside"),
)
# Datofelterne i GSAK's rækkefølge, med deres label.
_DATE_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("last_found_date", "col_last_found_date"),
    ("hidden_date",     "filter_hidden_date_group"),
    ("found_date",      "filter_found_date_group"),
    ("dnf_date",        "col_dnf_date"),
    ("creation_date",   "col_creation_date"),
    ("last_gpx_update", "col_last_gpx_update"),
    ("last_log_date",   "filter_log_date_group"),
    ("changed_date",    "col_changed_date"),
)
_DATE_OPS_WITH_DATE1 = ("on_or_before", "on_or_after", "equal", "between")
_DATE_OPS_RELATIVE = ("during", "not_during")


# ── Linje/polygon-fanen ───────────────────────────────────────────────────────

# (filtertype, oversættelsesnøgle) i GSAK's rækkefølge. Nøglerne står som
# literals, så test_no_unused_keys kan finde dem.
_LP_MODE_LABELS: tuple[tuple[str, str], ...] = (
    ("line",    "filter_lp_type_line"),
    ("polygon", "filter_lp_type_polygon"),
    ("points",  "filter_lp_type_points"),
)
_LP_DEFAULT_DISTANCE = 1.0  # i brugerens enhed (km / mi)


def _format_lp_point(point: tuple[float, float]) -> str:
    return f"{point[0]:.6f}, {point[1]:.6f}"


def _qdate_to_date(qdate: QDate) -> date:
    return date(qdate.year(), qdate.month(), qdate.day())


class DateFilterRow(QWidget):
    """Operator dropdown + inputs for one date field (GSAK's Dates tab).

    Depending on the operator it shows one or two date pickers, "Last
    [N] [days/weeks/months/years]", or a comparison with another date field
    (plus a day count for "within"/"outside"). The label turns bold while the
    row is active.
    """

    def __init__(self, field: str, label: str, parent=None):
        super().__init__(parent)
        self.field = field
        self.label = QLabel(label)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.op_combo = QComboBox()
        for op, key in _DATE_OP_LABELS:
            self.op_combo.addItem(tr(key), op)
        layout.addWidget(self.op_combo)

        self.date1 = self._make_date_edit()
        self.date2 = self._make_date_edit()
        layout.addWidget(self.date1)
        layout.addWidget(self.date2)

        self._relative = QWidget()
        rel_layout = QHBoxLayout(self._relative)
        rel_layout.setContentsMargins(0, 0, 0, 0)
        rel_layout.addWidget(QLabel(tr("filter_date_last")))
        self.amount = QSpinBox()
        self.amount.setRange(0, 9999)
        self.amount.setValue(1)
        rel_layout.addWidget(self.amount)
        self.unit_combo = QComboBox()
        for unit, key in _DATE_UNIT_LABELS:
            self.unit_combo.addItem(tr(key), unit)
        rel_layout.addWidget(self.unit_combo)
        layout.addWidget(self._relative)

        self._compare = QWidget()
        cmp_layout = QHBoxLayout(self._compare)
        cmp_layout.setContentsMargins(0, 0, 0, 0)
        self.other_combo = QComboBox()
        for other, key in _DATE_FIELD_LABELS:
            if other != field:
                self.other_combo.addItem(tr(key), other)
        cmp_layout.addWidget(self.other_combo)
        self.compare_combo = QComboBox()
        for op, key in _DATE_COMPARE_LABELS:
            self.compare_combo.addItem(tr(key), op)
        cmp_layout.addWidget(self.compare_combo)
        self.compare_days = QSpinBox()
        self.compare_days.setRange(0, 99999)
        cmp_layout.addWidget(self.compare_days)
        self._days_label = QLabel(tr("filter_date_unit_days"))
        cmp_layout.addWidget(self._days_label)
        layout.addWidget(self._compare)
        layout.addStretch()

        self.op_combo.currentIndexChanged.connect(self._update_inputs)
        self.compare_combo.currentIndexChanged.connect(self._update_inputs)
        self._update_inputs()

    @staticmethod
    def _make_date_edit() -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDate(QDate.currentDate())
        return edit

    def op(self) -> str:
        return self.op_combo.currentData()

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def reset(self) -> None:
        self.op_combo.setCurrentIndex(0)
        self.date1.setDate(QDate.currentDate())
        self.date2.setDate(QDate.currentDate())
        self.amount.setValue(1)
        self.unit_combo.setCurrentIndex(0)
        self.other_combo.setCurrentIndex(0)
        self.compare_combo.setCurrentIndex(0)
        self.compare_days.setValue(0)

    def build(self) -> Optional[DateFilter]:
        """Filter for the current input, or None when the row is "Any"."""
        op = self.op()
        if op == "any":
            return None
        # Only the dates the operator uses — keeps saved profiles free of
        # stale picker values.
        return DateFilter(
            self.field, op,
            date1=_qdate_to_date(self.date1.date()) if op in _DATE_OPS_WITH_DATE1 else None,
            date2=_qdate_to_date(self.date2.date()) if op == "between" else None,
            amount=self.amount.value(),
            unit=self.unit_combo.currentData(),
            other_field=self.other_combo.currentData(),
            compare_op=self.compare_combo.currentData(),
            compare_days=self.compare_days.value(),
        )

    def load(self, f: DateFilter) -> None:
        self._select(self.op_combo, f.op)
        for edit, value in ((self.date1, f.date1), (self.date2, f.date2)):
            if value is not None:
                edit.setDate(QDate(value.year, value.month, value.day))
        self.amount.setValue(f.amount)
        self._select(self.unit_combo, f.unit)
        self._select(self.other_combo, f.other_field)
        self._select(self.compare_combo, f.compare_op)
        self.compare_days.setValue(f.compare_days)

    def _update_inputs(self) -> None:
        op = self.op()
        self.date1.setVisible(op in _DATE_OPS_WITH_DATE1)
        self.date2.setVisible(op == "between")
        self._relative.setVisible(op in _DATE_OPS_RELATIVE)
        self._compare.setVisible(op == "compare")
        needs_days = self.compare_combo.currentData() in ("within", "outside")
        self.compare_days.setVisible(needs_days)
        self._days_label.setVisible(needs_days)
        font = self.label.font()
        font.setBold(op != "any")
        self.label.setFont(font)


# ── Filter dialog ─────────────────────────────────────────────────────────────

class FilterDialog(QDialog):
    """Komplet filter dialog med tre faner."""

    filter_applied = Signal(object, object, str)  # FilterSet, SortSpec, profile_name
    profile_deleted = Signal(str)  # profile_name — fires immediately on delete (issue #491),
                                    # independent of whether the dialog is later applied or closed
    profile_saved = Signal(str)    # profile_name — fires immediately on save (issue #682),
                                    # same reasoning as profile_deleted above: the toolbar
                                    # dropdown must refresh even if the user saves a profile
                                    # and then closes the dialog without clicking Apply.

    def __init__(self, parent=None, current_filterset: Optional[FilterSet] = None,
                 last_profile_name: str = "", current_cache=None):
        super().__init__(parent)
        self.setWindowTitle(tr("filter_dialog_title"))
        self._attr_boxes: dict[int, tuple] = {}
        # Cache currently selected in the main window's table, if any — lets
        # the "Afstand"-fanens center-punkt-vælger tilbyde "denne cache" som
        # centrum (issue #511). None if nothing is selected.
        self._current_cache = current_cache
        # Startsstørrelse: 70% af skærm, aldrig større end 1000x850
        from PySide6.QtWidgets import QApplication
        # Issue #580: brugte tidligere altid QApplication.primaryScreen(),
        # så filter-vinduet konsekvent åbnede på den primære skærm — også
        # når selve OpenSAK-vinduet kørte på en sekundær skærm i en
        # multi-monitor-opsætning. Brug i stedet skærmen forældrevinduet
        # (hovedvinduet) rent faktisk er på, ligesom Settings-dialogen og
        # de øvrige undervinduer allerede gør (de har ingen tilsvarende
        # override og følger derfor naturligt forælderens skærm).
        screen = parent.screen() if parent is not None else None
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen:
            rect = screen.availableGeometry()
            w = min(1000, int(rect.width()  * 0.70))
            h = min(850,  int(rect.height() * 0.70))
            self.resize(w, h)
            # Centrér på skærmen
            self.move(
                rect.x() + (rect.width()  - w) // 2,
                rect.y() + (rect.height() - h) // 2,
            )
        self._setup_ui()
        if last_profile_name:
            for i in range(self._profile_combo.count()):
                if self._profile_combo.itemText(i) == last_profile_name:
                    self._profile_combo.setCurrentIndex(i)
                    break
        elif current_filterset:
            self._load_filterset(current_filterset)

    # ── UI bygning ────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Gem/indlæs profil ─────────────────────────────────────────────────
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel(tr("filter_saved_label")))
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(180)
        # Undgå at udløse _on_profile_selected mens vi fylder combo
        self._profile_combo.blockSignals(True)
        self._load_profiles_into_combo()
        self._profile_combo.blockSignals(False)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        profile_row.addWidget(self._profile_combo)

        save_btn = QPushButton(tr("filter_save_btn"))
        save_btn.setMaximumWidth(110)
        save_btn.setAutoDefault(False)
        save_btn.clicked.connect(self._save_profile)
        profile_row.addWidget(save_btn)

        self._del_btn = QPushButton("🗑")
        self._del_btn.setMaximumWidth(40)
        self._del_btn.setToolTip(tr("filter_delete_profile_tooltip"))
        self._del_btn.setEnabled(False)
        self._del_btn.clicked.connect(self._delete_profile)
        profile_row.addWidget(self._del_btn)

        profile_row.addStretch()
        layout.addLayout(profile_row)

        # ── Faneblade ─────────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._general_tab = self._build_general_tab()
        self._dates_tab = self._build_dates_tab()
        self._misc_tab = self._build_misc_tab()
        self._line_polygon_tab = self._build_line_polygon_tab()
        self._attributes_tab = self._build_attributes_tab()
        self._text_search_tab = self._build_text_search_tab()
        self._where_tab = self._build_where_tab()
        self._tabs.addTab(self._general_tab, tr("settings_tab_general"))
        self._tabs.addTab(self._dates_tab, tr("filter_tab_dates"))
        self._tabs.addTab(self._misc_tab, tr("filter_tab_misc"))
        self._tabs.addTab(self._line_polygon_tab, tr("filter_tab_line_polygon"))
        self._tabs.addTab(self._attributes_tab, tr("filter_tab_attributes"))
        self._tabs.addTab(self._text_search_tab, tr("filter_tab_text_search"))
        self._tabs.addTab(self._where_tab, tr("filter_tab_where"))
        layout.addWidget(self._tabs)

        # ── Knapper ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        apply_btn = QPushButton(tr("filter_apply_btn"))
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(apply_btn)

        reset_btn = QPushButton(tr("filter_reset_all_btn"))
        reset_btn.clicked.connect(self._reset_all)
        btn_row.addWidget(reset_btn)

        reset_tab_btn = QPushButton(tr("filter_reset_tab_btn"))
        reset_tab_btn.clicked.connect(self._reset_current_tab)
        btn_row.addWidget(reset_tab_btn)

        btn_row.addStretch()

        cancel_btn = QPushButton(tr("cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

    def _build_general_tab(self) -> QWidget:
        """Generelt filter fane — indpakket i QScrollArea så indhold ikke klemmes."""
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QFormLayout(inner)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Cachenavn / GC kode / Udlagt af / Owner name — hver med operator-
        # vælger (Indeholder, Er lig med, RegEx, …). *_filter er selve
        # tekstfeltet, som før.
        self._name_row = TextFilterRow(tr("filter_name_label"), tr("filter_contains_placeholder"))
        layout.addRow(self._name_row.label, self._name_row)
        self._gc_row = TextFilterRow(tr("filter_gc_label"), tr("filter_gc_placeholder"))
        layout.addRow(self._gc_row.label, self._gc_row)
        self._placed_row = TextFilterRow(tr("filter_placed_by_label"), tr("filter_contains_placeholder"))
        layout.addRow(self._placed_row.label, self._placed_row)
        self._owner_row = TextFilterRow(tr("filter_owner_name_label"), tr("filter_contains_placeholder"))
        layout.addRow(self._owner_row.label, self._owner_row)
        self._name_filter = self._name_row.edit
        self._gc_filter = self._gc_row.edit
        self._placed_filter = self._placed_row.edit
        self._owner_filter = self._owner_row.edit

        spacer = QWidget()
        spacer.setFixedHeight(6)
        layout.addRow(spacer)

        # Cache type
        type_group = QGroupBox(tr("filter_cache_type_group"))
        type_outer = QVBoxLayout(type_group)
        type_layout = QGridLayout()
        self._type_checks: dict[str, QCheckBox] = {}
        # Same icons and size as the cache table's type column
        type_icon_size = TEXT_SIZE_MAP[get_settings().text_size]["grid_icon"]
        for i, ct in enumerate(CACHE_TYPES):
            cb = QCheckBox(ct.replace(" Cache", "").replace("Unknown", "Mystery"))
            cb.setIcon(get_cache_type_icon(ct, size=type_icon_size))
            cb.setIconSize(QSize(type_icon_size, type_icon_size))
            cb.setChecked(True)
            self._type_checks[ct] = cb
            type_layout.addWidget(cb, i // 3, i % 3)
        type_outer.addLayout(type_layout)
        type_btn_row = QHBoxLayout()
        type_enable_all = QPushButton(tr("filter_type_enable_all"))
        type_enable_all.clicked.connect(self._enable_all_types)
        type_disable_all = QPushButton(tr("filter_type_disable_all"))
        type_disable_all.clicked.connect(self._disable_all_types)
        type_btn_row.addWidget(type_enable_all)
        type_btn_row.addWidget(type_disable_all)
        type_btn_row.addStretch()
        type_outer.addLayout(type_btn_row)
        layout.addRow(type_group)

        # Container
        cont_group = QGroupBox(tr("filter_container_group"))
        cont_layout = QHBoxLayout(cont_group)
        self._cont_checks: dict[str, QCheckBox] = {}
        for cs in CONTAINER_SIZES:
            cb = QCheckBox(cs)
            cb.setChecked(True)
            self._cont_checks[cs] = cb
            cont_layout.addWidget(cb)
        layout.addRow(cont_group)

        # Sværhedsgrad
        dt_group = QGroupBox(tr("filter_dt_group"))
        dt_layout = QFormLayout(dt_group)

        d_row = QHBoxLayout()
        self._diff_min = DTSpinBox()
        self._diff_max = DTSpinBox()
        self._diff_max.setValue(5.0)
        d_row.addWidget(QLabel(tr("filter_from")))
        d_row.addWidget(self._diff_min)
        d_row.addWidget(QLabel(tr("filter_to")))
        d_row.addWidget(self._diff_max)
        d_row.addStretch()
        dt_layout.addRow(tr("wp_label_difficulty"), d_row)

        t_row = QHBoxLayout()
        self._terr_min = DTSpinBox()
        self._terr_max = DTSpinBox()
        self._terr_max.setValue(5.0)
        t_row.addWidget(QLabel(tr("filter_from")))
        t_row.addWidget(self._terr_min)
        t_row.addWidget(QLabel(tr("filter_to")))
        t_row.addWidget(self._terr_max)
        t_row.addStretch()
        dt_layout.addRow(tr("wp_label_terrain"), t_row)
        layout.addRow(dt_group)

        # Fundet status
        found_group = QGroupBox(tr("filter_found_group"))
        found_layout = QHBoxLayout(found_group)
        self._found_cb   = QCheckBox(tr("quick_found"))
        self._found_cb.setChecked(True)
        self._notfound_cb = QCheckBox(tr("quick_not_found"))
        self._notfound_cb.setChecked(True)
        found_layout.addWidget(self._found_cb)
        found_layout.addWidget(self._notfound_cb)
        found_layout.addStretch()
        layout.addRow(found_group)

        # Tilgængelighed
        avail_group = QGroupBox(tr("filter_avail_group"))
        avail_layout = QHBoxLayout(avail_group)
        self._avail_cb    = QCheckBox(tr("filter_available"))
        self._avail_cb.setChecked(True)
        self._unavail_cb  = QCheckBox(tr("filter_unavailable"))
        self._unavail_cb.setChecked(True)
        self._archived_cb = QCheckBox(tr("quick_archived"))
        # Issue #576 (Mike): GSAK always shows archived caches unless a
        # filter is explicitly set to hide them — OpenSAK previously hid
        # them by default, which surprised users and (per Mike's report)
        # made an explicit "show archived" choice forget itself on reopen.
        self._archived_cb.setChecked(True)
        avail_layout.addWidget(self._avail_cb)
        avail_layout.addWidget(self._unavail_cb)
        avail_layout.addWidget(self._archived_cb)
        avail_layout.addStretch()
        layout.addRow(avail_group)

        # Afstand
        dist_group = QGroupBox(tr("filter_distance_group"))
        dist_outer = QVBoxLayout(dist_group)

        dist_row = QHBoxLayout()
        self._dist_enabled = QCheckBox(tr("filter_enable"))
        self._dist_enabled.toggled.connect(self._on_dist_toggled)
        dist_row.addWidget(self._dist_enabled)
        dist_row.addWidget(QLabel(tr("filter_min")))
        self._dist_min = QDoubleSpinBox()
        self._dist_min.setRange(0.0, 9999.0)
        self._dist_min.setValue(0.0)
        from opensak.gui.settings import get_settings as _gs
        _unit = " mi" if _gs().use_miles else " km"
        self._dist_min.setSuffix(_unit)
        self._dist_min.setEnabled(False)
        dist_row.addWidget(self._dist_min)
        dist_row.addWidget(QLabel(tr("filter_max")))
        self._dist_max = QDoubleSpinBox()
        self._dist_max.setRange(0.1, 9999.0)
        self._dist_max.setValue(50.0)
        self._dist_max.setSuffix(_unit)
        self._dist_max.setEnabled(False)
        dist_row.addWidget(self._dist_max)
        dist_row.addStretch()
        dist_outer.addLayout(dist_row)

        # Center-punkt (issue #511) — genbrugelig widget, delt med den
        # planlagte quick "Where"-boks i toolbaren (#558).
        center_row = QHBoxLayout()
        center_row.addWidget(QLabel(tr("center_point_label")))
        self._center_picker = CenterPointPicker(self)
        self._center_picker.set_current_cache(self._current_cache)
        self._center_picker.setEnabled(False)
        center_row.addWidget(self._center_picker, 1)
        dist_outer.addLayout(center_row)

        layout.addRow(dist_group)

        # Premium
        prem_group = QGroupBox(tr("col_premium"))
        prem_layout = QHBoxLayout(prem_group)
        self._prem_yes = QCheckBox(tr("filter_premium_only"))
        self._prem_yes.setChecked(True)
        self._prem_no  = QCheckBox(tr("filter_not_premium"))
        self._prem_no.setChecked(True)
        prem_layout.addWidget(self._prem_yes)
        prem_layout.addWidget(self._prem_no)
        prem_layout.addStretch()
        layout.addRow(prem_group)

        # Trackables
        tb_group = QGroupBox(tr("filter_trackables_group"))
        tb_layout = QHBoxLayout(tb_group)
        self._tb_yes = QCheckBox(tr("filter_has_trackables"))
        self._tb_yes.setChecked(True)
        self._tb_no  = QCheckBox(tr("filter_no_trackables"))
        self._tb_no.setChecked(True)
        tb_layout.addWidget(self._tb_yes)
        tb_layout.addWidget(self._tb_no)
        tb_layout.addStretch()
        layout.addRow(tb_group)

        # Corrected Coordinates
        cc_group = QGroupBox(tr("filter_corrected_group"))
        cc_layout = QHBoxLayout(cc_group)
        self._cc_yes = QCheckBox(tr("filter_has_corrected"))
        self._cc_yes.setChecked(True)
        self._cc_no  = QCheckBox(tr("filter_no_corrected"))
        self._cc_no.setChecked(True)
        cc_layout.addWidget(self._cc_yes)
        cc_layout.addWidget(self._cc_no)
        cc_layout.addStretch()
        layout.addRow(cc_group)

        scroll.setWidget(inner)
        outer_layout.addWidget(scroll)
        return outer

    def _build_dates_tab(self) -> QWidget:
        """Datoer filter fane — én GSAK-lignende operator-række pr. datofelt."""
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        self._date_rows: dict[str, DateFilterRow] = {}
        for field, key in _DATE_FIELD_LABELS:
            row = DateFilterRow(field, tr(key))
            self._date_rows[field] = row
            layout.addRow(row.label, row)

        return widget

    def _build_misc_tab(self) -> QWidget:
        """Øvrigt filter fane — land, user flag, DNF, favorit points."""
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QFormLayout(inner)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Land / Stat / Kommune
        geo_group = QGroupBox(tr("filter_geo_group"))
        geo_layout = QFormLayout(geo_group)

        self._country_row = TextFilterRow(tr("col_country"), tr("filter_contains_placeholder"))
        geo_layout.addRow(self._country_row.label, self._country_row)
        self._state_row = TextFilterRow(tr("filter_state_label"), tr("filter_contains_placeholder"))
        geo_layout.addRow(self._state_row.label, self._state_row)
        self._county_row = TextFilterRow(tr("filter_county_label"), tr("filter_contains_placeholder"))
        geo_layout.addRow(self._county_row.label, self._county_row)
        self._country_filter = self._country_row.edit
        self._state_filter = self._state_row.edit
        self._county_filter = self._county_row.edit

        layout.addRow(geo_group)

        # User Flag
        flag_group = QGroupBox(tr("filter_user_flag_group"))
        flag_layout = QHBoxLayout(flag_group)
        self._flag_yes = QCheckBox(tr("yes"))
        self._flag_yes.setChecked(True)
        self._flag_no  = QCheckBox(tr("no"))
        self._flag_no.setChecked(True)
        flag_layout.addWidget(self._flag_yes)
        flag_layout.addWidget(self._flag_no)
        flag_layout.addStretch()
        layout.addRow(flag_group)

        # Locked (issue #202)
        locked_group = QGroupBox(tr("filter_locked_group"))
        locked_layout = QHBoxLayout(locked_group)
        self._locked_yes = QCheckBox(tr("yes"))
        self._locked_yes.setChecked(True)
        self._locked_no  = QCheckBox(tr("no"))
        self._locked_no.setChecked(True)
        locked_layout.addWidget(self._locked_yes)
        locked_layout.addWidget(self._locked_no)
        locked_layout.addStretch()
        layout.addRow(locked_group)

        # DNF
        dnf_group = QGroupBox(tr("filter_dnf_group"))
        dnf_layout = QHBoxLayout(dnf_group)
        self._dnf_yes = QCheckBox(tr("yes"))
        self._dnf_yes.setChecked(True)
        self._dnf_no  = QCheckBox(tr("no"))
        self._dnf_no.setChecked(True)
        dnf_layout.addWidget(self._dnf_yes)
        dnf_layout.addWidget(self._dnf_no)
        dnf_layout.addStretch()
        layout.addRow(dnf_group)

        # FTF
        ftf_group = QGroupBox(tr("filter_ftf_group"))
        ftf_layout = QHBoxLayout(ftf_group)
        self._ftf_yes = QCheckBox(tr("yes"))
        self._ftf_yes.setChecked(True)
        self._ftf_no  = QCheckBox(tr("no"))
        self._ftf_no.setChecked(True)
        ftf_layout.addWidget(self._ftf_yes)
        ftf_layout.addWidget(self._ftf_no)
        ftf_layout.addStretch()
        layout.addRow(ftf_group)

        # Favorit points
        fav_group = QGroupBox(tr("filter_fav_points_group"))
        fav_layout = QHBoxLayout(fav_group)
        self._fav_enabled = QCheckBox(tr("filter_enable"))
        self._fav_enabled.toggled.connect(self._on_fav_toggled)
        fav_layout.addWidget(self._fav_enabled)
        fav_layout.addWidget(QLabel(tr("filter_from")))
        self._fav_min = QDoubleSpinBox()
        self._fav_min.setRange(0, 9999)
        self._fav_min.setDecimals(0)
        self._fav_min.setValue(0)
        self._fav_min.setEnabled(False)
        fav_layout.addWidget(self._fav_min)
        fav_layout.addWidget(QLabel(tr("filter_to")))
        self._fav_max = QDoubleSpinBox()
        self._fav_max.setRange(0, 9999)
        self._fav_max.setDecimals(0)
        self._fav_max.setValue(9999)
        self._fav_max.setEnabled(False)
        fav_layout.addWidget(self._fav_max)
        fav_layout.addStretch()
        layout.addRow(fav_group)

        inner.setLayout(layout)
        scroll.setWidget(inner)
        outer_layout.addWidget(scroll)
        return outer

    def _build_line_polygon_tab(self) -> QWidget:
        """Linje/Polygon fane — GSAK's linje-/polygonfilter: caches langs en
        rute, inden for et område eller nær en række punkter."""
        from opensak.gui.settings import get_settings as _gs
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(10, 10, 10, 10)

        # Venstre: punktliste, markerede caches, punkter fra fil
        left = QVBoxLayout()
        left.addWidget(QLabel(tr("filter_lp_points_label")))
        self._lp_text = QPlainTextEdit()
        self._lp_text.setPlaceholderText(tr("filter_lp_points_placeholder"))
        self._lp_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        left.addWidget(self._lp_text, 1)

        flagged_btn = QPushButton(tr("filter_lp_add_flagged_btn"))
        flagged_btn.setAutoDefault(False)
        flagged_btn.clicked.connect(self._add_flagged_points)
        left.addWidget(flagged_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        file_group = QGroupBox(tr("filter_lp_file_group"))
        file_layout = QVBoxLayout(file_group)
        file_btn = QPushButton(tr("filter_lp_choose_file_btn"))
        file_btn.setAutoDefault(False)
        file_btn.clicked.connect(self._load_points_file)
        file_layout.addWidget(file_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        file_mode_row = QHBoxLayout()
        self._lp_replace = QRadioButton(tr("filter_lp_replace"))
        self._lp_replace.setChecked(True)
        self._lp_append = QRadioButton(tr("filter_lp_append"))
        file_mode_row.addWidget(self._lp_replace)
        file_mode_row.addWidget(self._lp_append)
        file_mode_row.addStretch()
        file_layout.addLayout(file_mode_row)
        left.addWidget(file_group)
        layout.addLayout(left, 1)

        # Højre: forklaring, filtertype, afstand, udeluk
        right = QVBoxLayout()
        desc_label = QLabel(tr("filter_lp_description"))
        desc_label.setWordWrap(True)
        right.addWidget(desc_label)

        type_group = QGroupBox(tr("filter_lp_type_group"))
        type_layout = QHBoxLayout(type_group)
        self._lp_mode_buttons: dict[str, QRadioButton] = {}
        for mode, key in _LP_MODE_LABELS:
            button = QRadioButton(tr(key))
            type_layout.addWidget(button)
            self._lp_mode_buttons[mode] = button
        self._lp_mode_buttons["line"].setChecked(True)
        right.addWidget(type_group)

        dist_row = QHBoxLayout()
        dist_row.addWidget(QLabel(tr("filter_lp_distance_label")))
        self._lp_distance = QDoubleSpinBox()
        self._lp_distance.setRange(0.0, 99999.0)
        self._lp_distance.setDecimals(3)
        self._lp_distance.setValue(_LP_DEFAULT_DISTANCE)
        self._lp_distance.setSuffix(" mi" if _gs().use_miles else " km")
        dist_row.addWidget(self._lp_distance)
        dist_row.addStretch()
        right.addLayout(dist_row)

        self._lp_exclude = QCheckBox(tr("filter_lp_exclude"))
        right.addWidget(self._lp_exclude)
        right.addStretch()
        layout.addLayout(right, 1)
        return widget

    def _build_attributes_tab(self) -> QWidget:
        """Attributter filter fane med scrollbar."""
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Mode — ALLE valgte attributter skal passe (AND) eller blot ÉN af dem (OR)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(tr("filter_caches_with")))
        self._attr_mode_all = QRadioButton(tr("filter_all_selected"))
        self._attr_mode_any = QRadioButton(tr("filter_any_selected"))
        self._attr_mode_all.setChecked(True)
        mode_row.addWidget(self._attr_mode_all)
        mode_row.addWidget(self._attr_mode_any)
        mode_row.addStretch()
        outer_layout.addLayout(mode_row)

        # Deduplicate keys (keep only first occurrence per attr_key)
        seen_keys: set[str] = set()
        unique_attrs: list[tuple[int, str]] = []
        for attr_id, attr_key in ATTRIBUTES:
            if attr_key not in seen_keys:
                seen_keys.add(attr_key)
                unique_attrs.append((attr_id, attr_key))

        table = QTableWidget(len(unique_attrs), 4)
        table.setHorizontalHeaderLabels([
            tr("filter_attr_col_name"), tr("yes"), tr("no"), tr("filter_none_short"),
        ])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setShowGrid(False)

        for i, (attr_id, attr_key) in enumerate(unique_attrs):
            name_item = QTableWidgetItem(tr(attr_key))
            name_item.setToolTip(f"Attribut ID: {attr_id}")
            table.setItem(i, 0, name_item)

            ja_cb    = QCheckBox()
            nej_cb   = QCheckBox()
            ingen_cb = QCheckBox()
            ingen_cb.setChecked(True)

            def make_exclusive(j, n, ig):
                def on_ja(v):
                    if v:
                        n.setChecked(False)
                        ig.setChecked(False)
                def on_nej(v):
                    if v:
                        j.setChecked(False)
                        ig.setChecked(False)
                def on_ingen(v):
                    if v:
                        j.setChecked(False)
                        n.setChecked(False)
                j.toggled.connect(on_ja)
                n.toggled.connect(on_nej)
                ig.toggled.connect(on_ingen)

            make_exclusive(ja_cb, nej_cb, ingen_cb)

            for col, cb in enumerate([ja_cb, nej_cb, ingen_cb], start=1):
                cell = QWidget()
                cell_layout = QHBoxLayout(cell)
                cell_layout.addWidget(cb)
                cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                table.setCellWidget(i, col, cell)

            self._attr_boxes[attr_id] = (ja_cb, nej_cb, ingen_cb)

        outer_layout.addWidget(table)
        return outer

    def _build_text_search_tab(self) -> QWidget:
        """Tekstsøgning fane — søg i fritekst felter."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        group = QGroupBox(tr("filter_text_search_group"))
        group_layout = QFormLayout(group)
        group_layout.setSpacing(8)

        self._text_search_input = QLineEdit()
        self._text_search_input.setPlaceholderText(tr("filter_text_search_placeholder"))
        group_layout.addRow(tr("filter_text_search_label"), self._text_search_input)

        self._text_search_description = QCheckBox(tr("detail_tab_desc"))
        self._text_search_description.setChecked(True)
        group_layout.addRow(self._text_search_description)

        self._text_search_logs = QCheckBox(tr("detail_tab_logs"))
        self._text_search_logs.setChecked(True)
        group_layout.addRow(self._text_search_logs)

        self._text_search_notes = QCheckBox(tr("filter_text_search_notes"))
        self._text_search_notes.setChecked(True)
        group_layout.addRow(self._text_search_notes)

        self._text_search_hint = QCheckBox(tr("detail_tab_hint"))
        self._text_search_hint.setChecked(False)
        group_layout.addRow(self._text_search_hint)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def _build_where_tab(self) -> QWidget:
        """Where filter fane — SQL WHERE clause editor."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Description row with info button
        header_row = QHBoxLayout()
        desc_label = QLabel(tr("filter_where_description"))
        desc_label.setWordWrap(True)
        desc_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header_row.addWidget(desc_label)

        info_btn = QPushButton("ⓘ")
        info_btn.setMaximumWidth(32)
        info_btn.setFlat(True)
        info_btn.setToolTip(tr("filter_where_info_tooltip"))
        info_btn.clicked.connect(self._show_where_info)
        header_row.addWidget(info_btn, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header_row)

        # SQL text area
        self._where_sql_general = QPlainTextEdit()
        self._where_sql_general.setPlaceholderText(tr("filter_where_sql_placeholder"))
        layout.addWidget(self._where_sql_general)

        # Error box (hidden until a SQL error occurs) — scrollable so large errors don't break the layout
        self._where_error_label = QPlainTextEdit()
        self._where_error_label.setReadOnly(True)
        self._where_error_label.setMaximumHeight(120)
        # Issue #613: the previous style was hardcoded for light theme
        # (dark-red text on a transparent background) — on Windows dark
        # mode that meant dark-red-on-dark-gray, reported as hard to read.
        # Pick an explicit, opaque, theme-appropriate background instead of
        # relying on "transparent" + whatever happens to sit behind it.
        from opensak.gui.settings import get_settings
        from opensak.gui.theme import effective_theme
        if effective_theme(get_settings().theme) == "dark":
            self._where_error_label.setStyleSheet(
                "color: #ff8a80; background: #3a1f1f;"
                "border: 1px solid #ff8a80; border-radius: 4px;"
            )
        else:
            self._where_error_label.setStyleSheet(
                "color: #cc0000; background: transparent;"
                "border: 1px solid #cc0000; border-radius: 4px;"
            )
        self._where_error_label.hide()
        layout.addWidget(self._where_error_label)

        return widget

    def _show_where_info(self) -> None:
        """Show a dialog with the available SQL column reference."""
        from PySide6.QtWidgets import QScrollArea as _QScrollArea
        from PySide6.QtCore import QLocale
        from opensak.gui.settings import get_settings
        from opensak.utils.types import DateFormat, norm_locale_date_fmt

        settings = get_settings()
        dist_unit = "mi" if settings.use_miles else "km"

        fmt = settings.date_format
        if fmt == DateFormat.DMY:
            date_col_eg = "15.06.2020"
            date_where_eg = "01.01.2023"
        elif fmt == DateFormat.MDY:
            date_col_eg = "06/15/2020"
            date_where_eg = "01/01/2023"
        elif fmt == DateFormat.YMD:
            date_col_eg = "2020-06-15"
            date_where_eg = "2023-01-01"
        else:  # LOCALE
            _loc = QLocale.system()
            _fmt = norm_locale_date_fmt(_loc.dateFormat(QLocale.FormatType.ShortFormat))
            date_col_eg = _loc.toString(QDate(2020, 6, 15), _fmt)
            date_where_eg = _loc.toString(QDate(2023, 1, 1), _fmt)

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("filter_where_info_title"))
        dlg.resize(560, 480)

        outer = QVBoxLayout(dlg)

        scroll = _QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QLabel()
        content.setTextFormat(Qt.TextFormat.RichText)
        content.setWordWrap(True)
        content.setContentsMargins(8, 8, 8, 8)
        content.setText(
            f"<b>{tr('filter_where_help_heading')}</b><br><br>"
            f"{tr('filter_where_help_intro')}<br><br>"
            "<table cellspacing='4'>"
            f"<tr><th align='left'>{tr('filter_where_col_header')}</th>"
            f"<th align='left'>{tr('col_type')}</th>"
            f"<th align='left'>{tr('filter_where_notes_header')}</th></tr>"
            "<tr><td><code>gc_code</code></td><td>text</td><td>e.g. <code>'GC12345'</code></td></tr>"
            f"<tr><td><code>name</code></td><td>text</td><td>{tr('filter_where_note_name')}</td></tr>"
            f"<tr><td><code>long_description</code></td><td>text</td><td>{tr('filter_where_note_long_desc')}</td></tr>"
            "<tr><td><code>cache_type</code></td><td>text</td>"
            "<td><code>'Traditional Cache'</code>, <code>'Multi-cache'</code>, "
            "<code>'Mystery Cache'</code>, …</td></tr>"
            "<tr><td><code>container</code></td><td>text</td>"
            "<td><code>'Nano'</code>, <code>'Micro'</code>, <code>'Small'</code>, "
            "<code>'Regular'</code>, <code>'Large'</code></td></tr>"
            "<tr><td><code>difficulty</code></td><td>decimal</td><td>1.0 – 5.0</td></tr>"
            "<tr><td><code>terrain</code></td><td>decimal</td><td>1.0 – 5.0</td></tr>"
            f"<tr><td><code>placed_by</code></td><td>text</td><td>{tr('filter_where_note_placed_by')}</td></tr>"
            "<tr><td><code>country</code></td><td>text</td><td>e.g. <code>'Denmark'</code></td></tr>"
            f"<tr><td><code>state</code></td><td>text</td><td>{tr('filter_where_note_state')}</td></tr>"
            f"<tr><td><code>county</code></td><td>text</td><td>{tr('filter_where_note_county')}</td></tr>"
            "<tr><td><code>hidden_date</code></td><td>datetime</td>"
            f"<td>e.g. <code>'{date_col_eg}'</code></td></tr>"
            "<tr><td><code>available</code></td><td>boolean</td><td>1 or 0</td></tr>"
            "<tr><td><code>archived</code></td><td>boolean</td><td>1 or 0</td></tr>"
            f"<tr><td><code>found</code></td><td>boolean</td><td>{tr('filter_where_note_found')}</td></tr>"
            "<tr><td><code>premium_only</code></td><td>boolean</td><td>1 or 0</td></tr>"
            f"<tr><td><code>favorite_points</code></td><td>integer</td><td>{tr('filter_where_note_fav')}</td></tr>"
            f"<tr><td><code>log_count</code></td><td>integer</td><td>{tr('filter_where_note_logcount')}</td></tr>"
            f"<tr><td><code>distance</code></td><td>decimal</td><td>{tr('filter_where_note_distance', unit=dist_unit)}</td></tr>"
            f"<tr><td><code>user_data_1</code> – <code>user_data_4</code></td>"
            f"<td>text</td><td>{tr('filter_where_note_userdata')}</td></tr>"
            "</table><br>"
            f"<b>{tr('filter_where_examples_heading')}</b><br>"
            "<code>difficulty &gt;= 4 AND terrain &gt;= 4</code><br>"
            "<code>cache_type = 'Traditional Cache' AND country = 'Denmark'</code><br>"
            "<code>favorite_points &gt; 100</code><br>"
            "<code>found = 0 AND available = 1</code><br>"
            "<code>name LIKE '%night%'</code><br>"
            "<code>long_description LIKE '%waterfall%'</code><br>"
            f"<code>hidden_date &gt; '{date_where_eg}'</code><br><br>"
            f"<b>{tr('filter_where_subquery_heading')}</b><br>"
            "<table cellspacing='4'>"
            f"<tr><th align='left'>{tr('filter_where_col_header')}</th>"
            f"<th align='left'>{tr('filter_where_notes_header')}</th></tr>"
            f"<tr><td><code>logs.text</code></td><td>{tr('filter_where_note_log_text')}</td></tr>"
            f"<tr><td><code>user_notes.note</code></td><td>{tr('filter_where_note_user_note')}</td></tr>"
            "</table><br>"
            "<code>EXISTS (SELECT 1 FROM logs WHERE logs.cache_id = caches.id AND logs.text LIKE '%TFTC%')</code><br>"
            "<code>EXISTS (SELECT 1 FROM user_notes WHERE user_notes.cache_id = caches.id AND user_notes.note LIKE '%bookmark%')</code>"
        )

        scroll.setWidget(content)
        outer.addWidget(scroll)

        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(dlg.accept)
        outer.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

        dlg.exec()

    def _validate_where_sql(self, sql: str) -> Optional[str]:
        """Return an error message if the SQL is invalid, or None if valid."""
        try:
            from opensak.db.database import get_session
            from sqlalchemy import text as _sa_text
            with get_session() as session:
                session.execute(_sa_text(f"SELECT 1 FROM caches WHERE ({sql}) LIMIT 0"))
            return None
        except Exception as exc:
            return str(exc)

    def _validate_text_filters(self) -> bool:
        """Warn about, and focus, the first text filter with an invalid regex."""
        for row, cls in self._general_text_rows() + self._geo_text_rows():
            text_filter = row.build(cls)
            if text_filter is None or text_filter.regex_error is None:
                continue
            for i in range(self._tabs.count()):
                tab = self._tabs.widget(i)
                if tab is not None and tab.isAncestorOf(row):
                    self._tabs.setCurrentIndex(i)
            row.edit.setFocus()
            QMessageBox.warning(
                self, tr("warning"),
                tr("filter_regex_invalid", field=row.label.rstrip(":"),
                   error=text_filter.regex_error),
            )
            return False
        return True

    # ── Slots ─────────────────────────────────────────────────────────────────

    # ── Linje/Polygon ────────────────────────────────────────────────────────

    def _lp_mode(self) -> str:
        for mode, button in self._lp_mode_buttons.items():
            if button.isChecked():
                return mode
        return "line"

    def _lp_distance_km(self) -> float:
        from opensak.gui.settings import get_settings as _gs
        value = self._lp_distance.value()
        return value * 1.60934 if _gs().use_miles else value

    @staticmethod
    def _resolve_point_code(code: str) -> Optional[tuple[float, float]]:
        """Coordinates for a "W,<code>" line, from the open database."""
        try:
            from opensak.db.database import get_session
            with get_session() as session:
                return lookup_code_coords(session, code)
        except Exception:
            return None

    def _lp_points(self) -> tuple[list[tuple[float, float]], list[str]]:
        return parse_points_text(self._lp_text.toPlainText(), self._resolve_point_code)

    def _build_line_polygon_filter(self) -> Optional[LinePolygonFilter]:
        """Filter for the Line/Polygon tab, or None when the tab is unused or
        incomplete — _validate_line_polygon() tells the user why."""
        points, bad = self._lp_points()
        mode = self._lp_mode()
        distance_km = self._lp_distance_km()
        if bad or len(points) < LP_MIN_POINTS[mode]:
            return None
        if mode != "polygon" and distance_km <= 0:
            return None
        return LinePolygonFilter(
            points, mode, distance_km,
            exclude=self._lp_exclude.isChecked(),
            text=self._lp_text.toPlainText().strip(),
        )

    def _validate_line_polygon(self) -> bool:
        """Warn about, and show, a Line/Polygon tab that is filled in but
        can't be used: unreadable lines, too few points or no distance."""
        points, bad = self._lp_points()
        if not points and not bad:
            return True  # fanen er ikke i brug
        mode = self._lp_mode()
        if bad:
            message = tr("filter_lp_invalid_lines", lines="\n".join(bad[:10]))
        elif len(points) < LP_MIN_POINTS[mode]:
            message = tr("filter_lp_too_few_points", count=LP_MIN_POINTS[mode])
        elif mode != "polygon" and self._lp_distance_km() <= 0:
            message = tr("filter_lp_distance_required")
        else:
            return True
        self._tabs.setCurrentWidget(self._line_polygon_tab)
        QMessageBox.warning(self, tr("warning"), message)
        return False

    def _add_lp_lines(self, lines: list[str], replace: bool = False) -> None:
        current = "" if replace else self._lp_text.toPlainText().rstrip()
        added = "\n".join(lines)
        self._lp_text.setPlainText(f"{current}\n{added}" if current else added)

    def _add_flagged_points(self) -> None:
        """Tilføj en "W,<kode>"-linje for hver cache med user flag."""
        try:
            from opensak.db.database import get_session
            with get_session() as session:
                codes = user_flagged_codes(session)
        except Exception:
            codes = []
        if not codes:
            QMessageBox.information(self, tr("filter_tab_line_polygon"), tr("filter_lp_no_flagged"))
            return
        self._add_lp_lines([f"W,{code}" for code in codes])

    def _load_points_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("filter_lp_file_group"), "", tr("filter_lp_file_filter"),
        )
        if not path:
            return
        try:
            points = read_points_file(Path(path), self._resolve_point_code)
        except Exception as exc:
            QMessageBox.warning(self, tr("error"), tr("filter_lp_file_error", error=exc))
            return
        if not points:
            QMessageBox.warning(self, tr("warning"), tr("filter_lp_file_no_points"))
            return
        self._add_lp_lines(
            [_format_lp_point(p) for p in points],
            replace=self._lp_replace.isChecked(),
        )

    def _on_dist_toggled(self, checked: bool) -> None:
        self._dist_max.setEnabled(checked)
        self._dist_min.setEnabled(checked)
        self._center_picker.setEnabled(checked)

    def _on_fav_toggled(self, checked: bool) -> None:
        self._fav_min.setEnabled(checked)
        self._fav_max.setEnabled(checked)

    def _enable_all_types(self) -> None:
        for cb in self._type_checks.values():
            cb.setChecked(True)

    def _disable_all_types(self) -> None:
        for cb in self._type_checks.values():
            cb.setChecked(False)

    def _reset_general(self) -> None:
        for row, _cls in self._general_text_rows():
            row.reset()
        for cb in self._type_checks.values():
            cb.setChecked(True)
        for cb in self._cont_checks.values():
            cb.setChecked(True)
        self._diff_min.setValue(1.0)
        self._diff_max.setValue(5.0)
        self._terr_min.setValue(1.0)
        self._terr_max.setValue(5.0)
        self._found_cb.setChecked(True)
        self._notfound_cb.setChecked(True)
        self._avail_cb.setChecked(True)
        self._unavail_cb.setChecked(True)
        self._archived_cb.setChecked(True)  # issue #576 — GSAK-style default
        self._dist_enabled.setChecked(False)
        self._dist_max.setValue(50.0)
        self._dist_min.setValue(0.0)
        self._center_picker.set_state({"kind": "home"})
        self._prem_yes.setChecked(True)
        self._prem_no.setChecked(True)
        self._tb_yes.setChecked(True)
        self._tb_no.setChecked(True)
        self._cc_yes.setChecked(True)
        self._cc_no.setChecked(True)

    def _reset_dates(self) -> None:
        for row in self._date_rows.values():
            row.reset()

    def _reset_misc(self) -> None:
        for row, _cls in self._geo_text_rows():
            row.reset()
        self._flag_yes.setChecked(True)
        self._flag_no.setChecked(True)
        self._locked_yes.setChecked(True)
        self._locked_no.setChecked(True)
        self._dnf_yes.setChecked(True)
        self._dnf_no.setChecked(True)
        self._ftf_yes.setChecked(True)
        self._ftf_no.setChecked(True)
        self._fav_enabled.setChecked(False)
        self._fav_min.setValue(0)
        self._fav_max.setValue(9999)

    def _reset_attributes(self) -> None:
        self._attr_mode_all.setChecked(True)
        for ja_cb, nej_cb, ingen_cb in self._attr_boxes.values():
            ja_cb.setChecked(False)
            nej_cb.setChecked(False)
            ingen_cb.setChecked(True)

    def _reset_text_search(self) -> None:
        self._text_search_input.clear()
        self._text_search_description.setChecked(True)
        self._text_search_logs.setChecked(True)
        self._text_search_notes.setChecked(True)
        self._text_search_hint.setChecked(False)

    def _reset_line_polygon(self) -> None:
        self._lp_text.clear()
        self._lp_mode_buttons["line"].setChecked(True)
        self._lp_distance.setValue(_LP_DEFAULT_DISTANCE)
        self._lp_exclude.setChecked(False)
        self._lp_replace.setChecked(True)

    def _reset_all(self) -> None:
        self._reset_general()
        self._reset_dates()
        self._reset_misc()
        self._reset_line_polygon()
        self._reset_attributes()
        self._reset_text_search()
        if self._where_tab is not None:
            self._where_sql_general.clear()
            self._where_error_label.hide()

    def _reset_current_tab(self) -> None:
        tab = self._tabs.currentWidget()
        if tab is self._where_tab:
            self._where_sql_general.clear()
            self._where_error_label.hide()
        elif tab is self._general_tab:
            self._reset_general()
        elif tab is self._dates_tab:
            self._reset_dates()
        elif tab is self._misc_tab:
            self._reset_misc()
        elif tab is self._line_polygon_tab:
            self._reset_line_polygon()
        elif tab is self._attributes_tab:
            self._reset_attributes()
        elif tab is self._text_search_tab:
            self._reset_text_search()

    # ── Byg FilterSet fra UI ──────────────────────────────────────────────────

    def _general_text_rows(self) -> list[tuple[TextFilterRow, type[TextMatchFilter]]]:
        return [
            (self._name_row, NameFilter),
            (self._gc_row, GcCodeFilter),
            (self._placed_row, PlacedByFilter),
            (self._owner_row, OwnerFilter),
        ]

    def _geo_text_rows(self) -> list[tuple[TextFilterRow, type[TextMatchFilter]]]:
        return [
            (self._country_row, CountryFilter),
            (self._state_row, StateFilter),
            (self._county_row, CountyFilter),
        ]

    def _build_filterset(self) -> FilterSet:
        fs = FilterSet(mode="AND")

        # Navn / GC kode / Udlagt af / Owner name
        for row, cls in self._general_text_rows():
            text_filter = row.build(cls)
            if text_filter is not None:
                fs.add(text_filter)

        # Cache type — byg OR gruppe af valgte typer
        selected_types = [t for t, cb in self._type_checks.items() if cb.isChecked()]
        if selected_types and len(selected_types) < len(CACHE_TYPES):
            fs.add(CacheTypeFilter(selected_types))

        # Container
        selected_cont = [c for c, cb in self._cont_checks.items() if cb.isChecked()]
        if selected_cont and len(selected_cont) < len(CONTAINER_SIZES):
            fs.add(ContainerFilter(selected_cont))

        # D/T
        if self._diff_min.value() > 1.0 or self._diff_max.value() < 5.0:
            fs.add(DifficultyFilter(self._diff_min.value(), self._diff_max.value()))
        if self._terr_min.value() > 1.0 or self._terr_max.value() < 5.0:
            fs.add(TerrainFilter(self._terr_min.value(), self._terr_max.value()))

        # Fundet — byg OR gruppe
        show_found    = self._found_cb.isChecked()
        show_notfound = self._notfound_cb.isChecked()
        if show_found and not show_notfound:
            fs.add(FoundFilter())
        elif show_notfound and not show_found:
            fs.add(NotFoundFilter())
        # Begge valgt = vis alt = ingen filter

        # Tilgængelighed — byg OR-gruppe af de valgte statusser.
        # Brugeren kan vælge enhver kombination af: tilgængelig / utilgængelig / arkiveret.
        # Alle tre er default (issue #576 — GSAK viser altid arkiverede caches
        # medmindre brugeren aktivt vælger at skjule dem), så "alle tre valgt"
        # er nu det reelle no-op-udgangspunkt: ingen filter, ingen badge.
        # Fravælges én eller flere, er det en bevidst brugerhandling og skal
        # ligesom alle andre faner tælle med i "N active".
        avail    = self._avail_cb.isChecked()
        unavail  = self._unavail_cb.isChecked()
        archived = self._archived_cb.isChecked()

        if not (avail and unavail and archived):
            # Mindst én er fravalgt — tilføj filter
            fs.add(AvailabilityFilter(
                show_avail=avail,
                show_unavail=unavail,
                show_archived=archived,
            ))

        # Afstand
        if self._dist_enabled.isChecked():
            from opensak.gui.settings import get_settings
            s = get_settings()
            center = self._center_picker.get_center()
            if center is None:
                QMessageBox.warning(self, tr("warning"), tr("center_point_invalid_warning"))
            else:
                lat, lon = center
                dist_val = self._dist_max.value()
                min_val = self._dist_min.value()
                max_km = dist_val * 1.60934 if s.use_miles else dist_val
                min_km = min_val * 1.60934 if s.use_miles else min_val
                fs.add(DistanceFilter(
                    lat, lon, max_km, min_km,
                    center_state=self._center_picker.to_state(),
                ))

        # Premium
        prem_yes = self._prem_yes.isChecked()
        prem_no  = self._prem_no.isChecked()
        if prem_yes and not prem_no:
            fs.add(PremiumFilter())
        elif prem_no and not prem_yes:
            fs.add(NonPremiumFilter())

        # Trackables
        tb_yes = self._tb_yes.isChecked()
        tb_no  = self._tb_no.isChecked()
        if tb_yes and not tb_no:
            fs.add(HasTrackableFilter())

        # Corrected Coordinates
        cc_yes = self._cc_yes.isChecked()
        cc_no  = self._cc_no.isChecked()
        if cc_yes and not cc_no:
            fs.add(HasCorrectedFilter())
        elif cc_no and not cc_yes:
            fs.add(NoCorrectedFilter())
        # Begge valgt (eller ingen) = vis alt = intet filter

        # Datoer — én DateFilter pr. datofelt med en valgt operator
        for date_row in self._date_rows.values():
            date_filter = date_row.build()
            if date_filter is not None:
                fs.add(date_filter)

        # Øvrigt — Land / Stat / Kommune
        for row, cls in self._geo_text_rows():
            text_filter = row.build(cls)
            if text_filter is not None:
                fs.add(text_filter)

        # User Flag
        flag_yes = self._flag_yes.isChecked()
        flag_no  = self._flag_no.isChecked()
        if flag_yes and not flag_no:
            fs.add(UserFlagFilter(flagged=True))
        elif flag_no and not flag_yes:
            fs.add(UserFlagFilter(flagged=False))

        # Locked (issue #202)
        locked_yes = self._locked_yes.isChecked()
        locked_no  = self._locked_no.isChecked()
        if locked_yes and not locked_no:
            fs.add(LockedFilter(locked=True))
        elif locked_no and not locked_yes:
            fs.add(LockedFilter(locked=False))

        # DNF
        dnf_yes = self._dnf_yes.isChecked()
        dnf_no  = self._dnf_no.isChecked()
        if dnf_yes and not dnf_no:
            fs.add(DnfFilter(has_dnf=True))
        elif dnf_no and not dnf_yes:
            fs.add(DnfFilter(has_dnf=False))

        # FTF
        ftf_yes = self._ftf_yes.isChecked()
        ftf_no  = self._ftf_no.isChecked()
        if ftf_yes and not ftf_no:
            fs.add(FtfFilter(has_ftf=True))
        elif ftf_no and not ftf_yes:
            fs.add(FtfFilter(has_ftf=False))

        # Favorit points
        if self._fav_enabled.isChecked():
            fs.add(FavoritePointsFilter(
                min_pts=int(self._fav_min.value()),
                max_pts=int(self._fav_max.value()),
            ))

        # Linje/Polygon
        lp_filter = self._build_line_polygon_filter()
        if lp_filter is not None:
            fs.add(lp_filter)

        # Attributter
        attr_mode_and = self._attr_mode_all.isChecked()
        attr_filters  = []
        for attr_id, (ja_cb, nej_cb, _ingen_cb) in self._attr_boxes.items():
            if ja_cb.isChecked():
                attr_filters.append(AttributeFilter(attr_id, True))
            elif nej_cb.isChecked():
                attr_filters.append(AttributeFilter(attr_id, False))

        if attr_filters:
            if attr_mode_and:
                for af in attr_filters:
                    fs.add(af)
            else:
                attr_or = FilterSet(mode="OR")
                for af in attr_filters:
                    attr_or.add(af)
                fs.add(attr_or)

        # Tekstsøgning
        ts_text = self._text_search_input.text().strip()
        if ts_text:
            fs.add(TextSearchFilter(
                text=ts_text,
                search_description=self._text_search_description.isChecked(),
                search_logs=self._text_search_logs.isChecked(),
                search_notes=self._text_search_notes.isChecked(),
                search_hint=self._text_search_hint.isChecked(),
            ))

        # WHERE clause
        if self._where_tab is not None:
            sql = self._where_sql_general.toPlainText().strip()
            if sql:
                fs.add(WhereClauseFilter(sql))

        return fs

    # ── Gem/indlæs profiler ───────────────────────────────────────────────────

    def _load_profiles_into_combo(self) -> None:
        self._profile_combo.clear()
        self._profile_combo.addItem(tr("filter_none"), None)
        for path in FilterProfile.list_profiles():
            try:
                p = FilterProfile.load(path)
                self._profile_combo.addItem(p.name, path)
            except Exception:
                pass

    def _on_profile_selected(self, index: int) -> None:
        path = self._profile_combo.currentData()
        self._del_btn.setEnabled(path is not None)
        if path is None:
            return
        try:
            profile = FilterProfile.load(path)
            # _load_filterset kalder selv _reset_all først
            self._load_filterset(profile.filterset)
        except Exception as e:
            QMessageBox.warning(self, tr("error"), tr("filter_load_error", error=e))

    def _save_profile(self) -> None:
        # Suggest a name for the selected profile so it can easily be overwritten.
        current = (
            self._profile_combo.currentText()
            if self._profile_combo.currentData() is not None
            else ""
        )
        name, ok = QInputDialog.getText(
            self, tr("filter_save_title"), tr("filter_profile_name_label"),
            text=current,
        )
        if not ok or not name.strip():
            return
        fs = self._build_filterset()
        profile = FilterProfile(name.strip(), fs)
        profile.save()
        self._load_profiles_into_combo()
        # Vælg den nye profil i combo
        for i in range(self._profile_combo.count()):
            if self._profile_combo.itemText(i) == name.strip():
                self._profile_combo.setCurrentIndex(i)
                break
        # issue #682: rapportér straks til MainWindow at en ny profil er
        # gemt, uanset om dialogen bagefter lukkes med Apply eller bare
        # med Close/Escape — samme mønster som profile_deleted (#491).
        self.profile_saved.emit(name.strip())
        QMessageBox.information(self, tr("filter_saved_title"), tr("filter_saved_msg", name=name))

    def _delete_profile(self) -> None:
        path = self._profile_combo.currentData()
        if path is None:
            return
        name = self._profile_combo.currentText()
        reply = QMessageBox.question(
            self, tr("filter_delete_title"),
            tr("filter_delete_msg", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            import os
            try:
                os.remove(path)
            except Exception:
                pass
            self._load_profiles_into_combo()
            # issue #491: rapportér straks til MainWindow at profilen er væk, uanset
            # om dialogen bagefter lukkes med Apply eller bare med Close/Escape.
            self.profile_deleted.emit(name)

    def _load_filterset(self, fs: FilterSet) -> None:
        """Udfyld UI felter fra et eksisterende FilterSet.

        Itererer gennem filtrene og sætter de matchende widgets.
        Ukendte/inline-definerede filtre ignoreres stille.
        """
        # Først: ryd UI så vi starter fra en kendt tilstand
        self._reset_all()

        # Saml filtre — hvis der er en nested OR-gruppe (fx attributter i OR-mode),
        # flad den ud, men husk at attributmode skal sættes.
        attr_mode_or_detected = False
        flat_filters: list = []
        for f in fs._filters:
            if isinstance(f, FilterSet):
                if f.mode == "OR":
                    # Antag at OR-grupper kommer fra attributter
                    attr_mode_or_detected = True
                flat_filters.extend(f._filters)
            else:
                flat_filters.append(f)

        # OR-mode = "ONE of the selected attributes". The two mode radios are
        # exclusive, so check the matching one (setChecked(False) is a no-op).
        if attr_mode_or_detected:
            self._attr_mode_any.setChecked(True)
        else:
            self._attr_mode_all.setChecked(True)

        text_rows = {
            cls.filter_type: row
            for row, cls in self._general_text_rows() + self._geo_text_rows()
        }
        for f in flat_filters:
            ftype = getattr(f, "filter_type", None)

            if ftype in text_rows:
                text_rows[ftype].load(f)
            elif ftype == "cache_type":
                types = getattr(f, "types", [])
                for ct, cb in self._type_checks.items():
                    cb.setChecked(ct in types)
            elif ftype == "container":
                sizes = getattr(f, "sizes", [])
                for cs, cb in self._cont_checks.items():
                    cb.setChecked(cs in sizes)
            elif ftype == "difficulty":
                self._diff_min.setValue(getattr(f, "min_difficulty", 1.0))
                self._diff_max.setValue(getattr(f, "max_difficulty", 5.0))
            elif ftype == "terrain":
                self._terr_min.setValue(getattr(f, "min_terrain", 1.0))
                self._terr_max.setValue(getattr(f, "max_terrain", 5.0))
            elif ftype == "found":
                self._found_cb.setChecked(True)
                self._notfound_cb.setChecked(False)
            elif ftype == "not_found":
                self._found_cb.setChecked(False)
                self._notfound_cb.setChecked(True)
            elif ftype == "availability":
                self._avail_cb.setChecked(getattr(f, "show_avail", True))
                self._unavail_cb.setChecked(getattr(f, "show_unavail", False))
                self._archived_cb.setChecked(getattr(f, "show_archived", False))
            elif ftype == "available":
                # Legacy / simpelt AvailableFilter
                self._avail_cb.setChecked(True)
                self._unavail_cb.setChecked(False)
                self._archived_cb.setChecked(False)
            elif ftype == "archived":
                self._avail_cb.setChecked(False)
                self._unavail_cb.setChecked(False)
                self._archived_cb.setChecked(True)
            elif ftype == "distance":
                self._dist_enabled.setChecked(True)
                from opensak.gui.settings import get_settings as _gs
                _use_mi = _gs().use_miles
                saved_km = getattr(f, "max_km", 10.0)
                self._dist_max.setValue(saved_km * 0.621371 if _use_mi else saved_km)
                saved_min_km = getattr(f, "min_km", 0.0)
                self._dist_min.setValue(saved_min_km * 0.621371 if _use_mi else saved_min_km)
                center_state = getattr(f, "center_state", None)
                if center_state:
                    self._center_picker.set_state(center_state)
                else:
                    # Filter gemt før #511 (eller uden center-punkt-info) —
                    # vis det gemte punkt som et redigerbart custom-punkt i
                    # stedet for stiltiende at antage Home.
                    self._center_picker.set_state({
                        "kind": "custom",
                        "text": f"{f.lat:.6f}, {f.lon:.6f}",
                    })
            elif ftype == "premium":
                self._prem_yes.setChecked(True)
                self._prem_no.setChecked(False)
            elif ftype == "non_premium":
                self._prem_yes.setChecked(False)
                self._prem_no.setChecked(True)
            elif ftype == "has_trackable":
                self._tb_yes.setChecked(True)
                self._tb_no.setChecked(False)
            elif ftype == "has_corrected":
                self._cc_yes.setChecked(True)
                self._cc_no.setChecked(False)
            elif ftype == "no_corrected":
                self._cc_yes.setChecked(False)
                self._cc_no.setChecked(True)
            elif ftype == "line_polygon":
                self._lp_text.setPlainText(
                    f.text or "\n".join(_format_lp_point(p) for p in f.points)
                )
                self._lp_mode_buttons[f.mode].setChecked(True)
                from opensak.gui.settings import get_settings as _gs
                self._lp_distance.setValue(
                    f.distance_km * 0.621371 if _gs().use_miles else f.distance_km
                )
                self._lp_exclude.setChecked(f.exclude)
            elif ftype == "attribute":
                attr_id = getattr(f, "attribute_id", None)
                is_on   = getattr(f, "is_on", True)
                if attr_id in self._attr_boxes:
                    ja_cb, nej_cb, _ingen = self._attr_boxes[attr_id]
                    if is_on:
                        ja_cb.setChecked(True)
                    else:
                        nej_cb.setChecked(True)
            elif ftype == "text_search":
                self._text_search_input.setText(getattr(f, "text", ""))
                self._text_search_description.setChecked(getattr(f, "search_description", True))
                self._text_search_logs.setChecked(getattr(f, "search_logs", True))
                self._text_search_notes.setChecked(getattr(f, "search_notes", True))
                self._text_search_hint.setChecked(getattr(f, "search_hint", False))
            elif ftype == "where_clause":
                if self._where_tab is not None:
                    self._where_sql_general.setPlainText(getattr(f, "sql", ""))
            elif ftype == "user_flag":
                flagged = getattr(f, "flagged", True)
                self._flag_yes.setChecked(flagged)
                self._flag_no.setChecked(not flagged)
            elif ftype == "locked":
                locked = getattr(f, "locked", True)
                self._locked_yes.setChecked(locked)
                self._locked_no.setChecked(not locked)
            elif ftype == "dnf":
                has_dnf = getattr(f, "has_dnf", True)
                self._dnf_yes.setChecked(has_dnf)
                self._dnf_no.setChecked(not has_dnf)
            elif ftype == "ftf":
                has_ftf = getattr(f, "has_ftf", True)
                self._ftf_yes.setChecked(has_ftf)
                self._ftf_no.setChecked(not has_ftf)
            elif ftype == "favorite_points":
                self._fav_enabled.setChecked(True)
                self._fav_min.setValue(getattr(f, "min_pts", 0))
                self._fav_max.setValue(getattr(f, "max_pts", 9999))
            elif ftype == "date":
                row = self._date_rows.get(f.field)
                if row is not None:
                    row.load(f)
            elif ftype in LEGACY_DATE_FILTER_FIELDS:
                # Profiles saved before the GSAK-style date filter hold
                # from/to range filters — show them as the equivalent
                # operator (Between / On or after / On or before).
                converted = DateFilter.from_legacy(f)
                if converted is not None:
                    self._date_rows[converted.field].load(converted)
            # Andre/ukendte filtre ignoreres stille

    # ── Apply ─────────────────────────────────────────────────────────────────

    def _apply(self) -> None:
        # Validate WHERE clause SQL before applying
        if self._where_tab is not None:
            sql = self._where_sql_general.toPlainText().strip()
            if sql:
                error = self._validate_where_sql(sql)
                if error:
                    self._where_error_label.setPlainText(
                        f"{tr('filter_where_error_prefix')} {error}"
                    )
                    self._where_error_label.show()
                    self._tabs.setCurrentWidget(self._where_tab)
                    return
            self._where_error_label.hide()

        # Et ugyldigt regulært udtryk ville stille matche ingenting — afvis det
        if not self._validate_text_filters():
            return
        if not self._validate_line_polygon():
            return

        fs = self._build_filterset()
        profile_name = (
            self._profile_combo.currentText()
            if self._profile_combo.currentData() is not None
            else ""
        )
        self.filter_applied.emit(fs, SortSpec("name"), profile_name)
        self.accept()
