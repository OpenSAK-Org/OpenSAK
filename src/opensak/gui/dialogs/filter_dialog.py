"""
src/opensak/gui/dialogs/filter_dialog.py — Komplet filter dialog.

Fem faner:
1. Generelt    — navn, type, D/T, afstand, fundet, tilgængelighed osv.
2. Datoer      — udlagt dato, fundet dato, DNF dato, seneste log dato
3. Øvrigt      — land/stat/kommune, user flag, DNF, favorit points
4. Attributter — alle Groundspeak attributter
5. Where       — rå SQL WHERE-betingelse

Understøtter gem/indlæs filterprofiler.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QCheckBox, QPushButton,
    QComboBox, QDoubleSpinBox, QTabWidget, QWidget,
    QGroupBox, QScrollArea, QGridLayout,
    QDialogButtonBox, QMessageBox, QInputDialog,
    QDateEdit, QSizePolicy, QFrame, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
)
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from PySide6.QtCore import QDate

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
    AttributeFilter, HasTrackableFilter, HasCorrectedFilter, NoCorrectedFilter,
    PremiumFilter, NonPremiumFilter,
    WhereClauseFilter,
    UserFlagFilter, DnfFilter, FtfFilter, FavoritePointsFilter,
    FoundByMeDateFilter, DnfDateFilter, LastLogDateFilter,
    FilterProfile,
)


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


# ── Filter dialog ─────────────────────────────────────────────────────────────

class FilterDialog(QDialog):
    """Komplet filter dialog med tre faner."""

    filter_applied = Signal(object, object, str)  # FilterSet, SortSpec, profile_name

    def __init__(self, parent=None, current_filterset: Optional[FilterSet] = None,
                 last_profile_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("filter_dialog_title"))
        self._attr_boxes: dict[int, tuple] = {}
        # Startsstørrelse: 70% af skærm, aldrig større end 1000x850
        from PySide6.QtWidgets import QApplication
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
        self._attributes_tab = self._build_attributes_tab()
        self._where_tab = self._build_where_tab()
        self._tabs.addTab(self._general_tab, tr("settings_tab_general"))
        self._tabs.addTab(self._dates_tab, tr("filter_tab_dates"))
        self._tabs.addTab(self._misc_tab, tr("filter_tab_misc"))
        self._tabs.addTab(self._attributes_tab, tr("filter_tab_attributes"))
        self._tabs.addTab(self._where_tab, tr("filter_tab_where"))
        layout.addWidget(self._tabs)

        # ── Knapper ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        apply_btn = QPushButton(tr("filter_apply_btn"))
        apply_btn.setStyleSheet("font-weight: bold;")
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

        # Cachenavn
        self._name_filter = QLineEdit()
        self._name_filter.setPlaceholderText(tr("filter_contains_placeholder"))
        layout.addRow(tr("filter_name_label"), self._name_filter)

        # GC kode
        self._gc_filter = QLineEdit()
        self._gc_filter.setPlaceholderText(tr("filter_gc_placeholder"))
        layout.addRow(tr("filter_gc_label"), self._gc_filter)

        # Udlagt af
        self._placed_filter = QLineEdit()
        self._placed_filter.setPlaceholderText(tr("filter_contains_placeholder"))
        layout.addRow(tr("filter_placed_by_label"), self._placed_filter)

        # Owner name
        self._owner_filter = QLineEdit()
        self._owner_filter.setPlaceholderText(tr("filter_contains_placeholder"))
        layout.addRow(tr("filter_owner_name_label"), self._owner_filter)

        spacer = QWidget()
        spacer.setFixedHeight(6)
        layout.addRow(spacer)

        # Cache type
        type_group = QGroupBox(tr("filter_cache_type_group"))
        type_outer = QVBoxLayout(type_group)
        type_layout = QGridLayout()
        self._type_checks: dict[str, QCheckBox] = {}
        for i, ct in enumerate(CACHE_TYPES):
            cb = QCheckBox(ct.replace(" Cache", "").replace("Unknown", "Mystery"))
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
        self._archived_cb.setChecked(False)
        avail_layout.addWidget(self._avail_cb)
        avail_layout.addWidget(self._unavail_cb)
        avail_layout.addWidget(self._archived_cb)
        avail_layout.addStretch()
        layout.addRow(avail_group)

        # Afstand
        dist_group = QGroupBox(tr("filter_distance_group"))
        dist_layout = QHBoxLayout(dist_group)
        self._dist_enabled = QCheckBox(tr("filter_enable"))
        self._dist_enabled.toggled.connect(self._on_dist_toggled)
        dist_layout.addWidget(self._dist_enabled)
        dist_layout.addWidget(QLabel(tr("filter_max")))
        self._dist_max = QDoubleSpinBox()
        self._dist_max.setRange(0.1, 9999.0)
        self._dist_max.setValue(50.0)
        self._dist_max.setSuffix(" km")
        self._dist_max.setEnabled(False)
        dist_layout.addWidget(self._dist_max)
        dist_layout.addStretch()
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
        """Datoer filter fane."""
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        def _make_date_group(title: str):
            """Hjælper: lav en from/to dato-gruppe og returner (group, from_en, from_dt, to_en, to_dt)."""
            group = QGroupBox(title)
            grp_layout = QFormLayout(group)
            from_en = QCheckBox(tr("filter_from"))
            from_dt = QDateEdit()
            from_dt.setCalendarPopup(True)
            from_dt.setDate(QDate(2000, 1, 1))
            from_dt.setEnabled(False)
            from_en.toggled.connect(from_dt.setEnabled)
            row1 = QHBoxLayout()
            row1.addWidget(from_en)
            row1.addWidget(from_dt)
            row1.addStretch()
            grp_layout.addRow(row1)
            to_en = QCheckBox(tr("filter_to"))
            to_dt = QDateEdit()
            to_dt.setCalendarPopup(True)
            to_dt.setDate(QDate.currentDate())
            to_dt.setEnabled(False)
            to_en.toggled.connect(to_dt.setEnabled)
            row2 = QHBoxLayout()
            row2.addWidget(to_en)
            row2.addWidget(to_dt)
            row2.addStretch()
            grp_layout.addRow(row2)
            return group, from_en, from_dt, to_en, to_dt

        # Udlagt dato
        g, self._hidden_from_enabled, self._hidden_from, self._hidden_to_enabled, self._hidden_to = \
            _make_date_group(tr("filter_hidden_date_group"))
        layout.addRow(g)

        # Fundet af mig dato
        g, self._found_from_enabled, self._found_from, self._found_to_enabled, self._found_to = \
            _make_date_group(tr("filter_found_date_group"))
        layout.addRow(g)

        # DNF dato
        g, self._dnf_date_from_enabled, self._dnf_date_from, self._dnf_date_to_enabled, self._dnf_date_to = \
            _make_date_group(tr("col_dnf_date"))
        layout.addRow(g)

        # Seneste log dato
        g, self._log_from_enabled, self._log_from, self._log_to_enabled, self._log_to = \
            _make_date_group(tr("filter_log_date_group"))
        layout.addRow(g)

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

        self._country_filter = QLineEdit()
        self._country_filter.setPlaceholderText(tr("filter_contains_placeholder"))
        geo_layout.addRow(tr("filter_country_label"), self._country_filter)

        self._state_filter = QLineEdit()
        self._state_filter.setPlaceholderText(tr("filter_contains_placeholder"))
        geo_layout.addRow(tr("filter_state_label"), self._state_filter)

        self._county_filter = QLineEdit()
        self._county_filter.setPlaceholderText(tr("filter_contains_placeholder"))
        geo_layout.addRow(tr("filter_county_label"), self._county_filter)

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

    def _build_attributes_tab(self) -> QWidget:
        """Attributter filter fane med scrollbar."""
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(tr("filter_caches_with")))
        self._attr_mode_all = QCheckBox(tr("filter_all_selected"))
        self._attr_mode_all.setChecked(True)
        mode_row.addWidget(self._attr_mode_all)
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
        self._where_error_label.setStyleSheet(
            "color: #cc0000; background: transparent; border: 1px solid #cc0000; border-radius: 4px;"
        )
        self._where_error_label.hide()
        layout.addWidget(self._where_error_label)

        return widget

    def _show_where_info(self) -> None:
        """Show a dialog with the available SQL column reference."""
        from PySide6.QtWidgets import QScrollArea as _QScrollArea
        from PySide6.QtCore import QLocale
        from opensak.gui.settings import get_settings
        from opensak.utils.types import DateFormat

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
            date_col_eg = _loc.toString(QDate(2020, 6, 15), QLocale.FormatType.ShortFormat)
            date_where_eg = _loc.toString(QDate(2023, 1, 1), QLocale.FormatType.ShortFormat)

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
            f"<code>hidden_date &gt; '{date_where_eg}'</code>"
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

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_dist_toggled(self, checked: bool) -> None:
        self._dist_max.setEnabled(checked)

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
        self._name_filter.clear()
        self._gc_filter.clear()
        self._placed_filter.clear()
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
        self._archived_cb.setChecked(False)
        self._dist_enabled.setChecked(False)
        self._dist_max.setValue(50.0)
        self._prem_yes.setChecked(True)
        self._prem_no.setChecked(True)
        self._tb_yes.setChecked(True)
        self._tb_no.setChecked(True)
        self._cc_yes.setChecked(True)
        self._cc_no.setChecked(True)

    def _reset_dates(self) -> None:
        self._hidden_from_enabled.setChecked(False)
        self._hidden_to_enabled.setChecked(False)
        self._found_from_enabled.setChecked(False)
        self._found_to_enabled.setChecked(False)
        self._dnf_date_from_enabled.setChecked(False)
        self._dnf_date_to_enabled.setChecked(False)
        self._log_from_enabled.setChecked(False)
        self._log_to_enabled.setChecked(False)

    def _reset_misc(self) -> None:
        self._country_filter.clear()
        self._state_filter.clear()
        self._county_filter.clear()
        self._flag_yes.setChecked(True)
        self._flag_no.setChecked(True)
        self._dnf_yes.setChecked(True)
        self._dnf_no.setChecked(True)
        self._ftf_yes.setChecked(True)
        self._ftf_no.setChecked(True)
        self._fav_enabled.setChecked(False)
        self._fav_min.setValue(0)
        self._fav_max.setValue(9999)

    def _reset_attributes(self) -> None:
        for ja_cb, nej_cb, ingen_cb in self._attr_boxes.values():
            ja_cb.setChecked(False)
            nej_cb.setChecked(False)
            ingen_cb.setChecked(True)

    def _reset_all(self) -> None:
        self._reset_general()
        self._reset_dates()
        self._reset_misc()
        self._reset_attributes()
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
        elif tab is self._attributes_tab:
            self._reset_attributes()

    # ── Byg FilterSet fra UI ──────────────────────────────────────────────────

    def _build_filterset(self) -> FilterSet:
        fs = FilterSet(mode="AND")

        # Navn
        if self._name_filter.text().strip():
            fs.add(NameFilter(self._name_filter.text().strip()))

        # GC kode
        if self._gc_filter.text().strip():
            fs.add(GcCodeFilter(self._gc_filter.text().strip()))

        # Udlagt af
        if self._placed_filter.text().strip():
            fs.add(PlacedByFilter(self._placed_filter.text().strip()))

        # Owner name
        if self._owner_filter.text().strip():
            fs.add(OwnerFilter(self._owner_filter.text().strip()))

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
        # Hvis alle tre er valgt: ingen filter (vis alt).
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
            fs.add(DistanceFilter(s.home_lat, s.home_lon, self._dist_max.value()))

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

        # Datoer — hjælper til at konvertere QDate til datetime
        def _qdate_to_dt(qdate, end_of_day=False) -> datetime:
            return datetime(
                qdate.year(), qdate.month(), qdate.day(),
                23, 59, 59 if end_of_day else 0,
            )

        # Udlagt dato
        if self._hidden_from_enabled.isChecked() or self._hidden_to_enabled.isChecked():
            from opensak.filters.engine import BaseFilter
            from_date = _qdate_to_dt(self._hidden_from.date()) if self._hidden_from_enabled.isChecked() else None
            to_date   = _qdate_to_dt(self._hidden_to.date(), end_of_day=True) if self._hidden_to_enabled.isChecked() else None

            class HiddenDateFilter(BaseFilter):
                filter_type = "hidden_date_range"
                def __init__(self, fd, td):
                    self.from_date = fd
                    self.to_date   = td
                def matches(self, cache):
                    if cache.hidden_date is None:
                        return False
                    hd = cache.hidden_date.replace(tzinfo=None)
                    if self.from_date and hd < self.from_date:
                        return False
                    if self.to_date and hd > self.to_date:
                        return False
                    return True
                def to_dict(self):
                    return {"filter_type": self.filter_type}

            fs.add(HiddenDateFilter(from_date, to_date))

        # Fundet af mig dato
        if self._found_from_enabled.isChecked() or self._found_to_enabled.isChecked():
            fs.add(FoundByMeDateFilter(
                from_date=_qdate_to_dt(self._found_from.date()) if self._found_from_enabled.isChecked() else None,
                to_date=_qdate_to_dt(self._found_to.date(), end_of_day=True) if self._found_to_enabled.isChecked() else None,
            ))

        # DNF dato
        if self._dnf_date_from_enabled.isChecked() or self._dnf_date_to_enabled.isChecked():
            fs.add(DnfDateFilter(
                from_date=_qdate_to_dt(self._dnf_date_from.date()) if self._dnf_date_from_enabled.isChecked() else None,
                to_date=_qdate_to_dt(self._dnf_date_to.date(), end_of_day=True) if self._dnf_date_to_enabled.isChecked() else None,
            ))

        # Seneste log dato
        if self._log_from_enabled.isChecked() or self._log_to_enabled.isChecked():
            fs.add(LastLogDateFilter(
                from_date=_qdate_to_dt(self._log_from.date()) if self._log_from_enabled.isChecked() else None,
                to_date=_qdate_to_dt(self._log_to.date(), end_of_day=True) if self._log_to_enabled.isChecked() else None,
            ))

        # Øvrigt — Land / Stat / Kommune
        if self._country_filter.text().strip():
            fs.add(CountryFilter(self._country_filter.text().strip()))
        if self._state_filter.text().strip():
            fs.add(StateFilter(self._state_filter.text().strip()))
        if self._county_filter.text().strip():
            fs.add(CountyFilter(self._county_filter.text().strip()))

        # User Flag
        flag_yes = self._flag_yes.isChecked()
        flag_no  = self._flag_no.isChecked()
        if flag_yes and not flag_no:
            fs.add(UserFlagFilter(flagged=True))
        elif flag_no and not flag_yes:
            fs.add(UserFlagFilter(flagged=False))

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
        name, ok = QInputDialog.getText(
            self, tr("filter_save_title"), tr("filter_profile_name_label")
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

        # OR-mode = "any selected"; the UI only has the "all selected" checkbox,
        # so unchecking it expresses ANY (avoids a crash on the missing widget).
        self._attr_mode_all.setChecked(not attr_mode_or_detected)

        for f in flat_filters:
            ftype = getattr(f, "filter_type", None)

            if ftype == "name":
                self._name_filter.setText(getattr(f, "text", ""))
            elif ftype == "gc_code":
                self._gc_filter.setText(getattr(f, "text", ""))
            elif ftype == "placed_by":
                self._placed_filter.setText(getattr(f, "text", ""))
            elif ftype == "owner_name":
                self._owner_filter.setText(getattr(f, "text", ""))
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
                self._dist_max.setValue(getattr(f, "max_km", 10.0))
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
            elif ftype == "attribute":
                attr_id = getattr(f, "attribute_id", None)
                is_on   = getattr(f, "is_on", True)
                if attr_id in self._attr_boxes:
                    ja_cb, nej_cb, _ingen = self._attr_boxes[attr_id]
                    if is_on:
                        ja_cb.setChecked(True)
                    else:
                        nej_cb.setChecked(True)
            elif ftype == "where_clause":
                if self._where_tab is not None:
                    self._where_sql_general.setPlainText(getattr(f, "sql", ""))
            elif ftype == "country":
                self._country_filter.setText(getattr(f, "text", ""))
            elif ftype == "state":
                self._state_filter.setText(getattr(f, "text", ""))
            elif ftype == "county":
                self._county_filter.setText(getattr(f, "text", ""))
            elif ftype == "user_flag":
                flagged = getattr(f, "flagged", True)
                self._flag_yes.setChecked(flagged)
                self._flag_no.setChecked(not flagged)
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
            elif ftype == "found_by_me_date":
                if getattr(f, "from_date", None):
                    self._found_from_enabled.setChecked(True)
                    d = f.from_date
                    self._found_from.setDate(QDate(d.year, d.month, d.day))
                if getattr(f, "to_date", None):
                    self._found_to_enabled.setChecked(True)
                    d = f.to_date
                    self._found_to.setDate(QDate(d.year, d.month, d.day))
            elif ftype == "dnf_date":
                if getattr(f, "from_date", None):
                    self._dnf_date_from_enabled.setChecked(True)
                    d = f.from_date
                    self._dnf_date_from.setDate(QDate(d.year, d.month, d.day))
                if getattr(f, "to_date", None):
                    self._dnf_date_to_enabled.setChecked(True)
                    d = f.to_date
                    self._dnf_date_to.setDate(QDate(d.year, d.month, d.day))
            elif ftype == "last_log_date":
                if getattr(f, "from_date", None):
                    self._log_from_enabled.setChecked(True)
                    d = f.from_date
                    self._log_from.setDate(QDate(d.year, d.month, d.day))
                if getattr(f, "to_date", None):
                    self._log_to_enabled.setChecked(True)
                    d = f.to_date
                    self._log_to.setDate(QDate(d.year, d.month, d.day))
            # Andre/inline filtre ignoreres stille (fx gammel hidden_date)

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

        fs = self._build_filterset()
        profile_name = (
            self._profile_combo.currentText()
            if self._profile_combo.currentData() is not None
            else ""
        )
        self.filter_applied.emit(fs, SortSpec("name"), profile_name)
        self.accept()
