"""
src/opensak/gui/mainwindow.py — Main application window.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, cast
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QStatusBar,
    QToolBar, QPushButton, QComboBox,
    QSizePolicy, QMessageBox, QWidgetAction
)

from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.db.database import get_session, db_health_check
from opensak.db.models import Cache
from opensak.filters.engine import (
    FilterSet, SortSpec, apply_filters,
    AvailableFilter, NotFoundFilter, CacheTypeFilter,
    DifficultyFilter, TerrainFilter
)
from opensak.gui.cache_table import CacheTableView
from opensak.gui.cache_detail import CacheDetailPanel
from opensak.coords import format_coords
from opensak.gui.settings import get_settings
from opensak.lang import tr
from opensak.utils.types import GcCode
from opensak.utils.utils import normalize_geocacher_name
from opensak.updater import UpdateCheckWorker, RELEASES_PAGE

if TYPE_CHECKING:
    from opensak.gui.dialogs.trip_dialog import TripPlannerDialog


class ClickableLabel(QLabel):
    """QLabel der opfører sig som en klikbar knap (issue #270).

    Bruges til de farvede count-felter i InfoBar, så et klik kan filtrere
    cache-listen til den status feltet repræsenterer — ligesom man kan
    klikke på status-tællerne i GSAK's Count panel.
    """

    clicked = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class InfoBar(QFrame):
    """GSAK-style info bar between cache list and detail/map panel (issue #116).

    Shows (left to right):
      Filter name | Total caches in DB | Flagged count | Center point
      ... spacer ...
      Count label:  Found (gul bg)  All-in-filter (neutral)  Inactive (rød bg)  Owned (grøn bg)

    Issue #270: count-felterne matcher nu samme farver som gc_code-kolonnen
    (sort tekst på farvet baggrund, GSAK-style) og er klikbare — et klik
    filtrerer cache-listen til den tilsvarende status, ligesom i GSAK.
    """

    found_clicked    = Signal()
    all_clicked      = Signal()
    inactive_clicked = Signal()
    owned_clicked    = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setFixedHeight(24)
        self.setStyleSheet(
            "InfoBar {"
            "  background-color: palette(window);"
            "  border: 1px solid palette(mid);"
            "  padding: 0 2px;"
            "}"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(0)

        small = "font-size: 11px;"

        # ── Left side ─────────────────────────────────────────────────────────
        self._filter_lbl = QLabel("")
        self._filter_lbl.setStyleSheet(f"{small} color: palette(text);")
        row.addWidget(self._filter_lbl)

        row.addWidget(self._sep())

        self._total_lbl = QLabel("")
        self._total_lbl.setStyleSheet(small)
        row.addWidget(self._total_lbl)

        row.addWidget(self._sep())

        self._flag_lbl = QLabel("")
        self._flag_lbl.setStyleSheet(small)
        row.addWidget(self._flag_lbl)

        row.addWidget(self._sep())

        self._center_lbl = QLabel("")
        self._center_lbl.setStyleSheet(f"{small} color: palette(text);")
        row.addWidget(self._center_lbl)

        # ── Spacer ────────────────────────────────────────────────────────────
        row.addStretch()

        # ── Right side: color-coded counts (issue #270 — GSAK-farver, klikbare) ─
        count_style = (
            f"{small} font-weight: bold; color: #000000; "
            "padding: 1px 5px; border-radius: 3px;"
        )

        lbl_prefix = QLabel(tr("infobar_count_label"))
        lbl_prefix.setStyleSheet(f"{small} padding: 0 4px;")
        row.addWidget(lbl_prefix)

        self._found_lbl = ClickableLabel("0")
        self._found_lbl.setStyleSheet(f"{count_style} background-color: #f9e79f;")   # gul — fundet
        self._found_lbl.setToolTip(tr("infobar_found_tooltip"))
        row.addWidget(self._found_lbl)

        self._all_lbl = ClickableLabel("0")
        self._all_lbl.setStyleSheet(
            f"{small} font-weight: bold; padding: 1px 5px; color: palette(text);"
        )
        self._all_lbl.setToolTip(tr("infobar_all_tooltip"))
        row.addWidget(self._all_lbl)

        self._inactive_lbl = ClickableLabel("0")
        self._inactive_lbl.setStyleSheet(f"{count_style} background-color: #f1948a;")  # rød — arkiveret/disabled
        self._inactive_lbl.setToolTip(tr("infobar_inactive_tooltip"))
        row.addWidget(self._inactive_lbl)

        self._owned_lbl = ClickableLabel("0")
        self._owned_lbl.setStyleSheet(f"{count_style} background-color: #7dcea0;")   # grøn — egne caches
        self._owned_lbl.setToolTip(tr("infobar_owned_tooltip"))
        row.addWidget(self._owned_lbl)

        self._found_lbl.clicked.connect(self.found_clicked)
        self._all_lbl.clicked.connect(self.all_clicked)
        self._inactive_lbl.clicked.connect(self.inactive_clicked)
        self._owned_lbl.clicked.connect(self.owned_clicked)

    @staticmethod
    def _sep() -> QFrame:
        s = QFrame()
        s.setFrameShape(QFrame.Shape.VLine)
        s.setFrameShadow(QFrame.Shadow.Sunken)
        s.setFixedWidth(16)
        return s

    def update_counts(
        self,
        filter_name: str,
        total_in_db: int,
        flagged: int,
        center_name: str,
        found: int,
        all_in_filter: int,
        inactive: int,
        owned: int,
    ) -> None:
        self._filter_lbl.setText(
            f"{tr('infobar_filter')}: {filter_name}" if filter_name
            else f"{tr('infobar_filter')}: {tr('infobar_filter_none')}"
        )
        self._total_lbl.setText(f"{total_in_db} {tr('infobar_total')}")
        self._flag_lbl.setText(f"🚩 = {flagged}")
        self._center_lbl.setText(
            f"{tr('infobar_center')}: {center_name}" if center_name
            else f"{tr('infobar_center')}: —"
        )
        self._found_lbl.setText(str(found))
        self._all_lbl.setText(str(all_in_filter))
        self._inactive_lbl.setText(str(inactive))
        self._owned_lbl.setText(str(owned))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(800, 500)
        self._current_filterset = FilterSet()
        self._current_sort = SortSpec("name", ascending=True)
        self._active_filter_name = ""
        self._trip_planner_win: TripPlannerDialog | None = None
        self._db_count: int = 0
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._refresh_cache_list)
        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_search_bar()
        self._setup_statusbar()
        self._restore_state()
        self._update_title()
        self._reload_home_combo()
        self._reload_db_combo()
        self.setAcceptDrops(True)
        # Load caches after UI is ready
        QTimer.singleShot(500, self._initial_load)
        # Tjek for opdateringer i baggrunden (5 sek forsinkelse — GUI er klar)
        QTimer.singleShot(5000, self._check_update_background)
        QTimer.singleShot(7000, self._check_setup_complete)

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # ── Main splitter: cache list (top) | info bar + bottom panel (below) ─
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setObjectName("main_splitter")

        # Top: cache list — fuld bredde
        self._cache_table = CacheTableView()
        self._cache_table.cache_selected.connect(self._on_cache_selected)
        self._cache_table.flags_changed.connect(self._on_flags_changed)
        self._cache_table.sort_changed.connect(self._on_sort_changed)
        self._cache_table.location_updated.connect(self._refresh_cache_list)
        self._cache_table.edit_requested.connect(self._edit_waypoint_from_cache)
        self._splitter.addWidget(self._cache_table)

        # Bottom container: info bar (fixed) + horisontal splitter (resizable)
        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)

        # Info bar (GSAK-style, issue #116)
        self._info_bar = InfoBar()
        self._info_bar.found_clicked.connect(lambda: self._filter_by_status("found"))
        self._info_bar.all_clicked.connect(lambda: self._filter_by_status("all"))
        self._info_bar.inactive_clicked.connect(lambda: self._filter_by_status("inactive"))
        self._info_bar.owned_clicked.connect(lambda: self._filter_by_status("owned"))
        bottom_layout.addWidget(self._info_bar)

        # Horisontal splitter — detaljer til venstre, kort til højre
        self._bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._bottom_splitter.setObjectName("bottom_splitter")

        self._detail_panel = CacheDetailPanel()
        self._detail_panel.corrected_coords_changed.connect(self._on_corrected_coords_changed)
        self._bottom_splitter.addWidget(self._detail_panel)

        # Map widget
        from opensak.gui.map_widget import MapWidget
        self._map_widget = MapWidget()
        self._map_widget.cache_selected.connect(self._on_map_cache_selected)
        self._map_widget.set_corrected_requested.connect(self._on_set_corrected_from_map)
        self._map_widget.setMinimumWidth(300)
        self._bottom_splitter.addWidget(self._map_widget)

        self._bottom_splitter.setSizes([560, 540])
        bottom_layout.addWidget(self._bottom_splitter)

        self._splitter.addWidget(bottom_container)
        self._splitter.setSizes([380, 400])

        main_layout.addWidget(self._splitter)

    def _setup_menu(self) -> None:
        menubar = self.menuBar()

        # ── Fil ───────────────────────────────────────────────────────────────
        file_menu = menubar.addMenu(tr("menu_file"))

        self._act_db_manager = QAction(tr("action_db_manager"), self)
        self._act_db_manager.setShortcut(QKeySequence("Ctrl+D"))
        self._act_db_manager.triggered.connect(self._open_db_manager)
        file_menu.addAction(self._act_db_manager)

        file_menu.addSeparator()

        self._act_import = QAction(tr("action_import"), self)
        self._act_import.setShortcut(QKeySequence("Ctrl+I"))
        self._act_import.triggered.connect(self._open_import_dialog)
        file_menu.addAction(self._act_import)

        file_menu.addSeparator()

        # ── Export ──────────────────────────────────────────────────────────────
        act_export = QAction(tr("action_export"), self)
        act_export.triggered.connect(self._open_file_export)
        file_menu.addAction(act_export)

        act_kml_export = QAction(tr("action_kml_export"), self)
        act_kml_export.triggered.connect(self._open_kml_export)
        file_menu.addAction(act_kml_export)

        file_menu.addSeparator()

        act_quit = QAction(tr("action_quit"), self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # ── Waypoint ──────────────────────────────────────────────────────────
        wp_menu = menubar.addMenu(tr("menu_waypoint"))

        act_wp_add = QAction(tr("action_wp_add"), self)
        act_wp_add.setShortcut(QKeySequence("Ctrl+N"))
        act_wp_add.triggered.connect(self._add_waypoint)
        wp_menu.addAction(act_wp_add)

        self._act_wp_edit = QAction(tr("action_wp_edit"), self)
        self._act_wp_edit.setShortcut(QKeySequence("Ctrl+E"))
        self._act_wp_edit.setEnabled(False)
        self._act_wp_edit.triggered.connect(self._edit_waypoint)
        wp_menu.addAction(self._act_wp_edit)

        self._act_wp_delete = QAction(tr("action_wp_delete"), self)
        self._act_wp_delete.setShortcut(QKeySequence("Delete"))
        self._act_wp_delete.setEnabled(False)
        self._act_wp_delete.triggered.connect(self._delete_waypoint)
        wp_menu.addAction(self._act_wp_delete)

        wp_menu.addSeparator()

        act_delete_flagged = QAction(tr("action_delete_flagged"), self)
        act_delete_flagged.triggered.connect(self._delete_flagged_caches)
        wp_menu.addAction(act_delete_flagged)

        act_delete_filtered = QAction(tr("action_delete_filtered"), self)
        act_delete_filtered.triggered.connect(self._delete_filtered_caches)
        wp_menu.addAction(act_delete_filtered)

        wp_menu.addSeparator()

        act_clear_flags = QAction(tr("action_clear_flags"), self)
        act_clear_flags.triggered.connect(self._clear_all_flags)
        wp_menu.addAction(act_clear_flags)

        from opensak.utils import flags
        if flags.update_location:
            wp_menu.addSeparator()

            act_update_location = QAction(tr("action_update_location"), self)
            act_update_location.triggered.connect(self._open_update_location)
            wp_menu.addAction(act_update_location)

            wp_menu.addSeparator()

            act_download_boundaries = QAction(tr("action_download_boundaries"), self)
            act_download_boundaries.triggered.connect(self._open_download_boundaries)
            wp_menu.addAction(act_download_boundaries)

            act_check_boundaries = QAction(tr("action_check_boundaries"), self)
            act_check_boundaries.triggered.connect(self._open_check_boundaries)
            wp_menu.addAction(act_check_boundaries)

        # ── Vis ───────────────────────────────────────────────────────────────
        view_menu = menubar.addMenu(tr("menu_view"))

        act_refresh = QAction(tr("action_refresh"), self)
        act_refresh.setShortcut(QKeySequence("F5"))
        act_refresh.triggered.connect(self._refresh_cache_list)
        view_menu.addAction(act_refresh)

        view_menu.addSeparator()

        act_filter = QAction(tr("action_filter"), self)
        act_filter.setShortcut("Ctrl+F")
        act_filter.triggered.connect(self._open_filter_dialog)
        view_menu.addAction(act_filter)

        act_clear = QAction(tr("action_clear_filter"), self)
        act_clear.triggered.connect(self._clear_filter)
        view_menu.addAction(act_clear)

        view_menu.addSeparator()

        act_columns = QAction(tr("action_columns"), self)
        act_columns.triggered.connect(self._open_column_chooser)
        view_menu.addAction(act_columns)

        # ── Funktioner ────────────────────────────────────────────────────────
        tools_menu = menubar.addMenu(tr("menu_tools"))

        act_settings = QAction(tr("action_settings"), self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        # Ctrl+, triggers macOS PreferencesRole auto-assignment, relabeling the action "Preferences".
        act_settings.setMenuRole(QAction.MenuRole.NoRole)
        act_settings.triggered.connect(self._open_settings)
        tools_menu.addAction(act_settings)

        tools_menu.addSeparator()

        act_found_update = QAction(tr("action_found_update"), self)
        act_found_update.triggered.connect(self._open_found_updater)
        tools_menu.addAction(act_found_update)

        # ── GPS ───────────────────────────────────────────────────────────────
        gps_menu = menubar.addMenu("&GPS")

        self._act_gps_export = QAction(tr("action_gps_export"), self)
        self._act_gps_export.setShortcut(QKeySequence("Ctrl+G"))
        self._act_gps_export.triggered.connect(self._open_gps_export)
        gps_menu.addAction(self._act_gps_export)

        self._act_trip_planner = QAction(tr("action_trip_planner"), self)
        self._act_trip_planner.setShortcut(QKeySequence("Ctrl+T"))
        self._act_trip_planner.triggered.connect(self._open_trip_planner)
        gps_menu.addAction(self._act_trip_planner)

        # ── Geocaching Værktøjer ──────────────────────────────────────────────
        gc_tools_menu = menubar.addMenu(tr("menu_gc_tools"))

        act_coord_converter = QAction(tr("action_coord_converter"), self)
        act_coord_converter.setShortcut(QKeySequence("Ctrl+K"))
        act_coord_converter.triggered.connect(self._open_coord_converter)
        gc_tools_menu.addAction(act_coord_converter)

        act_projection = QAction(tr("action_projection"), self)
        act_projection.setShortcut(QKeySequence("Ctrl+P"))
        act_projection.triggered.connect(self._open_projection)
        gc_tools_menu.addAction(act_projection)

        gc_tools_menu.addSeparator()

        act_checksum = QAction(tr("action_checksum"), self)
        act_checksum.triggered.connect(self._open_checksum)
        gc_tools_menu.addAction(act_checksum)

        act_midpoint = QAction(tr("action_midpoint"), self)
        act_midpoint.triggered.connect(self._open_midpoint)
        gc_tools_menu.addAction(act_midpoint)

        act_dist_bearing = QAction(tr("action_dist_bearing"), self)
        act_dist_bearing.triggered.connect(self._open_dist_bearing)
        gc_tools_menu.addAction(act_dist_bearing)

        # ── Hjælp ─────────────────────────────────────────────────────────────
        help_menu = menubar.addMenu(tr("menu_help"))

        act_about = QAction(tr("action_about"), self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

        act_check_update = QAction(tr("action_check_update"), self)
        act_check_update.triggered.connect(self._check_update_manual)
        help_menu.addAction(act_check_update)

        help_menu.addSeparator()

        act_open_log = QAction(tr("action_open_log_file"), self)
        act_open_log.triggered.connect(self._open_log_file)
        help_menu.addAction(act_open_log)

        # ── Vis-dropdown i menulinjen ─────────────────────────────────────────
        menubar.addSeparator()

        # Vis-dropdown
        self._quick_filter = QComboBox()
        self._quick_filter.setFixedWidth(140)
        self._quick_filter.addItems([
            tr("quick_all"),
            tr("quick_not_found"),
            tr("quick_found"),
            tr("quick_available"),
            tr("quick_traditional_easy"),
            tr("quick_archived"),
        ])
        self._quick_filter.currentIndexChanged.connect(self._on_quick_filter_changed)
        filter_action = QWidgetAction(self)
        filter_action.setDefaultWidget(self._quick_filter)
        menubar.addAction(filter_action)

        # Aktivt filter label
        self._filter_lbl = QLabel("")
        self._filter_lbl.setStyleSheet("color: #e65100; font-style: italic; padding: 0 4px;")
        filter_lbl_action = QWidgetAction(self)
        filter_lbl_action.setDefaultWidget(self._filter_lbl)
        menubar.addAction(filter_lbl_action)

        # Cache-tæller (højrejusteret via spacer)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer_action = QWidgetAction(self)
        spacer_action.setDefaultWidget(spacer)
        menubar.addAction(spacer_action)

        self._count_lbl = QLabel(tr("count_caches", count=0))
        self._count_lbl.setStyleSheet("color: palette(mid); padding: 0 8px;")
        count_action = QWidgetAction(self)
        count_action.setDefaultWidget(self._count_lbl)
        menubar.addAction(count_action)

    def _setup_toolbar(self) -> None:
        tb = QToolBar("Værktøjslinje")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        # Databaser
        self._act_db_manager.setText(tr("action_db_manager"))
        self._act_db_manager.setToolTip(tr("action_db_manager") + " (Ctrl+D)")
        tb.addAction(self._act_db_manager)

        self._db_combo = QComboBox()
        self._db_combo.setMinimumWidth(140)
        self._db_combo.setMaximumWidth(220)
        self._db_combo.setToolTip(tr("toolbar_db_combo_tooltip"))
        self._db_combo.currentIndexChanged.connect(self._on_db_combo_changed)
        db_combo_action = QWidgetAction(self)
        db_combo_action.setDefaultWidget(self._db_combo)
        tb.addAction(db_combo_action)

        # Importer
        self._act_import.setText(tr("action_import"))
        self._act_import.setToolTip(tr("action_import") + " (Ctrl+I)")
        tb.addAction(self._act_import)

        tb.addSeparator()

        # Opdater
        refresh_act = QAction(f"⟳  {tr('toolbar_refresh')}", self)
        refresh_act.setToolTip(tr("toolbar_refresh") + " (F5)")
        refresh_act.triggered.connect(self._refresh_cache_list)
        tb.addAction(refresh_act)

        tb.addSeparator()

        # Filter
        self._act_filter = QAction(f"🔍  {tr('toolbar_filter')}", self)
        self._act_filter.setShortcut("Ctrl+F")
        self._act_filter.setToolTip(tr("toolbar_filter") + " (Ctrl+F)")
        self._act_filter.triggered.connect(self._open_filter_dialog)
        tb.addAction(self._act_filter)

        # Nulstil filter — rød knap når aktiv, grå når inaktiv
        self._btn_clear_filter = QPushButton("✕")
        self._btn_clear_filter.setToolTip(tr("toolbar_clear_filter"))
        self._btn_clear_filter.setFixedSize(26, 26)
        self._btn_clear_filter.setFlat(True)
        self._btn_clear_filter.clicked.connect(self._clear_filter)
        self._set_clear_filter_active(False)
        clear_filter_action = QWidgetAction(self)
        clear_filter_action.setDefaultWidget(self._btn_clear_filter)
        tb.addAction(clear_filter_action)

        # Filter-profil dropdown
        self._filter_profile_combo = QComboBox()
        self._filter_profile_combo.setMinimumWidth(140)
        self._filter_profile_combo.setMaximumWidth(200)
        self._filter_profile_combo.setToolTip(tr("toolbar_filter_combo_tooltip"))
        self._filter_profile_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        filter_combo_action = QWidgetAction(self)
        filter_combo_action.setDefaultWidget(self._filter_profile_combo)
        tb.addAction(filter_combo_action)
        self._filter_profile_combo.currentIndexChanged.connect(
            self._on_filter_profile_combo_changed
        )
        self._populate_filter_profile_combo()

        tb.addSeparator()

        # GPS
        gps_act = QAction(f"📤  {tr('gps_dialog_title')}", self)
        gps_act.setToolTip(tr("gps_dialog_title") + " (Ctrl+G)")
        gps_act.triggered.connect(self._open_gps_export)
        tb.addAction(gps_act)

        tb.addSeparator()

        # Turplanlægger
        trip_act = QAction(f"🗺️  {tr('toolbar_trip')}", self)
        trip_act.setToolTip(tr("toolbar_trip_tooltip") + " (Ctrl+T)")
        trip_act.triggered.connect(self._open_trip_planner)
        tb.addAction(trip_act)

        tb.addSeparator()

        # Hjem-dropdown
        self._home_combo = QComboBox()
        self._home_combo.setMinimumWidth(130)
        self._home_combo.setMaximumWidth(180)
        self._home_combo.setToolTip(tr("toolbar_home_combo_tooltip"))
        self._home_combo.currentIndexChanged.connect(self._on_home_changed)
        home_combo_action = QWidgetAction(self)
        home_combo_action.setDefaultWidget(self._home_combo)
        tb.addAction(home_combo_action)

        home_act = QAction("⌂", self)
        home_act.setToolTip(tr("toolbar_home_tooltip"))
        home_act.triggered.connect(lambda: self._map_widget.pan_to_home())
        tb.addAction(home_act)

        tb.addSeparator()

        # Indstillinger — kun ikon
        settings_act = QAction("⚙", self)
        settings_act.setToolTip(tr("action_settings").replace("&", "").replace("…", ""))
        settings_act.triggered.connect(self._open_settings)
        tb.addAction(settings_act)

    def _setup_search_bar(self) -> None:
        """Søgelinje på linje 3 under primær toolbar — højrejusteret via HBoxLayout.

        Søgelinjen tvinges altid synlig fordi:
        - Den indeholder essentielle søgefelter (GC code, Name)
        - Qt gemmer toolbar-synlighed i QSettings, så et utilsigtet højreklik-
          uncheck ville skjule den permanent for brugeren
        - Den er planlagt til at indeholde flere felter (status, hurtig-nav)
        """
        from PySide6.QtWidgets import (
            QToolBar, QLabel, QLineEdit, QWidgetAction, QWidget, QSizePolicy, QHBoxLayout
        )
        sb = QToolBar(tr("search"))
        sb.setObjectName("search_toolbar")
        sb.setMovable(False)
        # Issue #86 follow-up: forhindre at brugeren skjuler søgelinjen via
        # højreklik-menu på toolbar-området. toggleViewAction() er den action
        # Qt bruger i toolbar-kontekstmenuen — ved at deaktivere den fjerner
        # vi muligheden for at skjule søgelinjen utilsigtet.
        sb.toggleViewAction().setEnabled(False)
        sb.toggleViewAction().setVisible(False)
        self.addToolBarBreak()
        self.addToolBar(sb)
        # Tving synlig — overskriver evt. gemt QSettings-state hvor en bruger
        # tidligere har skjult toolbar'en
        sb.setVisible(True)

        # Container-widget med HBoxLayout — spacer skubber felterne til højre
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(4)

        # GC-nummer label + felt
        gc_lbl = QLabel(tr("search_gc_label") + ":")
        gc_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        row.addWidget(gc_lbl)

        self._search_gc = QLineEdit()
        self._search_gc.setPlaceholderText("GC12345")
        self._search_gc.setFixedWidth(110)
        self._search_gc.setClearButtonEnabled(True)
        self._search_gc.textChanged.connect(self._on_search_changed)
        row.addWidget(self._search_gc)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        row.addWidget(sep)

        # Navn label + felt
        name_lbl = QLabel(tr("col_name") + ":")
        name_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        row.addWidget(name_lbl)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText(tr("search_placeholder"))
        self._search_box.setFixedWidth(220)
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._on_search_changed)
        row.addWidget(self._search_box)

        # Spacer — skubber felterne til venstre (issue #125)
        row.addStretch()

        container_action = QWidgetAction(self)
        container_action.setDefaultWidget(container)
        sb.addAction(container_action)

    def _setup_statusbar(self) -> None:
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage(tr("status_ready"))

    # ── State save/restore ────────────────────────────────────────────────────

    def _restore_state(self) -> None:
        s = get_settings()
        if s.window_geometry:
            self.restoreGeometry(s.window_geometry)
        if s.window_state:
            # Version 2: toolbar-rækkefølge ændret (Filter før GPS/Trip Planner).
            # Hvis gemt state er fra en ældre version ignoreres den automatisk,
            # så toolbar-layoutet altid er korrekt efter en opgradering.
            self.restoreState(s.window_state, 2)
        self._load_sort_for_active_db()

        # Gendan splitter-størrelser som procentandele af vinduets størrelse.
        # Vi gemmer ratios (0.0–1.0) i stedet for absolutte pixels, så
        # layoutet ser fornuftigt ud uanset skærmopløsning (issue #62).
        QTimer.singleShot(0, self._restore_splitter_ratios)

    def _update_title(self) -> None:
        """Opdatér vinduestitel med aktiv database navn og versionsnummer."""
        from opensak import __version__
        from opensak.db.manager import get_db_manager
        manager = get_db_manager()
        if manager.active:
            self.setWindowTitle(
                tr("window_title_with_db", db_name=manager.active.name) + f"  v{__version__}"
            )
        else:
            self.setWindowTitle(tr("window_title") + f"  v{__version__}")

    def _open_db_manager(self) -> None:
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.database_dialog import DatabaseManagerDialog
        dlg = DatabaseManagerDialog(self)
        dlg.database_switched.connect(self._on_database_switched)
        dlg.exec()

    def _on_database_switched(self, db_info) -> None:
        """Kaldes når brugeren skifter aktiv database."""
        self._update_title()
        self._reload_db_combo()
        self._detail_panel.clear()
        self._load_sort_for_active_db()
        self._reload_home_combo()
        # Genindlæs kolonner for den nye database (issue #199)
        self._cache_table.reload_columns()
        # Reload kort med aktuel lokation for denne DB
        self._map_widget.reload_map(self._refresh_cache_list)
        self._statusbar.showMessage(
            tr("status_db_name", db_name=db_info.name), 4000
        )

    def _reload_db_combo(self) -> None:
        """Genindlæs database-dropdown fra manager."""
        from opensak.db.manager import get_db_manager
        manager = get_db_manager()
        self._db_combo.blockSignals(True)
        self._db_combo.clear()
        databases = manager.databases
        if not databases:
            self._db_combo.addItem(tr("toolbar_db_no_databases"), None)
        else:
            for db in databases:
                self._db_combo.addItem(db.name, db)
            for i in range(self._db_combo.count()):
                if self._db_combo.itemData(i) == manager.active:
                    self._db_combo.setCurrentIndex(i)
                    break
        self._db_combo.blockSignals(False)

    def _on_db_combo_changed(self, index: int) -> None:
        """Skift aktiv database fra dropdown uden at åbne dialogen."""
        from opensak.db.manager import get_db_manager
        db = self._db_combo.itemData(index)
        if not db:
            return
        manager = get_db_manager()
        if db == manager.active:
            return
        manager.switch_to(db)
        self._on_database_switched(db)

    def _restore_splitter_ratios(self) -> None:
        """Gendan splitter-størrelser fra gemte procentandele (issue #62).

        Ratios gemmes som floats (0.0–1.0) så layoutet skalerer korrekt
        på tværs af skærmopløsninger og platforme.
        """
        s = get_settings()
        total_v = self._splitter.height()
        ratio_v = s.splitter_ratio_top
        if total_v > 10:
            top = int(total_v * ratio_v)
            self._splitter.setSizes([top, total_v - top])
        else:
            self._splitter.setSizes([380, 400])

        total_h = self._bottom_splitter.width()
        ratio_h = s.bottom_splitter_ratio_left
        if total_h > 10:
            left = int(total_h * ratio_h)
            self._bottom_splitter.setSizes([left, total_h - left])
        else:
            self._bottom_splitter.setSizes([560, 540])

    def _save_splitter_ratios(self) -> None:
        """Gem splitter-størrelser som procentandele (issue #62)."""
        s = get_settings()
        sizes_v = self._splitter.sizes()
        total_v = sum(sizes_v)
        if total_v > 0:
            s.splitter_ratio_top = sizes_v[0] / total_v

        sizes_h = self._bottom_splitter.sizes()
        total_h = sum(sizes_h)
        if total_h > 0:
            s.bottom_splitter_ratio_left = sizes_h[0] / total_h

    def closeEvent(self, event) -> None:
        s = get_settings()
        s.window_geometry = self.saveGeometry()
        s.window_state    = self.saveState(2)
        self._save_splitter_ratios()
        s.sync()
        # Stop update workers so they don't make network calls after window close
        for attr in ("_update_worker", "_manual_update_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                worker.quit()
                worker.wait(500)
        # Tear down the map's WebEngine page before its profile while the event
        # loop is still alive. The map's QWebEngineProfile/QWebEnginePage are
        # parent-less; relying on aboutToQuit (which fires only at app exit) means
        # each closed window leaks them, and Python GC may release the profile
        # before the page — Qt's "Expect troubles!" warning — eventually crashing
        # the Chromium render process across long e2e runs. Cleaning up here makes
        # the teardown deterministic and per-window.
        map_widget = getattr(self, "_map_widget", None)
        if map_widget is not None:
            map_widget._cleanup_webengine()
        super().closeEvent(event)

    # ── Cache list ────────────────────────────────────────────────────────────

    def _refresh_cache_list(self) -> None:
        """Reload caches from DB applying current filters.

        Combines the advanced filter (self._current_filterset, set via the
        filter dialog) with the quick-filter / search-box filters so that
        returning from Settings or any other dialog never discards the active
        filter (fixes #128).
        """
        quick_fs = self._build_current_filterset()

        # Wrap both filtersets in a top-level AND so they work together.
        # If _current_filterset is empty it has no effect (FilterSet.matches()
        # returns True for an empty set), so this is safe in all cases.
        if len(self._current_filterset) > 0 or len(quick_fs) > 0:
            from opensak.filters.engine import FilterSet as _FS
            combined = _FS(mode="AND")
            if len(self._current_filterset) > 0:
                combined.add(self._current_filterset)
            if len(quick_fs) > 0:
                combined.add(quick_fs)
            fs = combined
        else:
            fs = quick_fs

        with get_session() as session:
            caches = apply_filters(session, fs, self._current_sort)

        self._cache_table.load_caches(caches)
        self._map_widget.load_caches(caches)
        count = self._cache_table.row_count()
        if count == 1:
            self._count_lbl.setText(tr("count_cache_single"))
        else:
            self._count_lbl.setText(tr("count_caches", count=count))
        self._update_info_bar()

    def _update_info_bar(self) -> None:
        """Recalculate and update the GSAK-style info bar (issue #116)."""
        s = get_settings()
        caches = self._cache_table.get_all_caches()

        # Total caches in database (not just filtered)
        with get_session() as session:
            total_in_db = session.query(Cache).count()
        self._db_count = total_in_db

        # Filter name: named profile > generic "Active" > empty (shows None)
        if self._active_filter_name:
            filter_name = self._active_filter_name
        elif len(self._current_filterset) > 0:
            filter_name = tr("infobar_filter_active")
        else:
            filter_name = ""

        # Flagged count
        flagged = sum(1 for c in caches if c.user_flag)

        # Center point name
        center_name = s.active_home_name or ""

        # Color-coded counts (from filtered caches)
        found = sum(1 for c in caches if c.found)
        all_in_filter = len(caches)
        inactive = sum(1 for c in caches if c.archived or not c.available)

        # Owned: match owner_name against stored GC username (issue #270 —
        # GSAK counts the 'Owner' tag, not 'Placed by'; adopted caches can
        # have a different placed_by than the current owner). Comparison is
        # whitespace/case-normalized (issue #272: irregular GPX whitespace).
        gc_user = normalize_geocacher_name(s.gc_username)
        if gc_user:
            owned = sum(
                1 for c in caches
                if normalize_geocacher_name(c.owner_name) == gc_user
            )
        else:
            owned = 0

        self._info_bar.update_counts(
            filter_name=filter_name,
            total_in_db=total_in_db,
            flagged=flagged,
            center_name=center_name,
            found=found,
            all_in_filter=all_in_filter,
            inactive=inactive,
            owned=owned,
        )

    def _filter_by_status(self, status: str) -> None:
        """Klik på et farvet count-felt i info-baren (issue #270).

        Anvender et filter der matcher præcis den status der blev klikket
        på — ligesom man i GSAK kan klikke på status-tællerne i Count panel
        for at filtrere cache-listen til den status.
        """
        if status == "all":
            self._clear_filter()
            return

        from opensak.filters.engine import FoundFilter, AvailabilityFilter, OwnerFilter
        fs = FilterSet(mode="AND")

        if status == "found":
            fs.add(FoundFilter())
            label = tr("infobar_filter_found")
        elif status == "owned":
            s = get_settings()
            gc_user = (s.gc_username or "").strip()
            if not gc_user:
                self._statusbar.showMessage(tr("infobar_owned_no_username"), 5000)
                return
            fs.add(OwnerFilter(gc_user))  # issue #270: match 'owner', not 'placed_by'
            label = tr("infobar_filter_owned")
        elif status == "inactive":
            # Samme definition som i _update_info_bar: archived OR ikke tilgængelig
            fs.add(AvailabilityFilter(show_avail=False, show_unavail=True, show_archived=True))
            label = tr("infobar_filter_inactive")
        else:
            return

        self._on_filter_applied(fs, self._current_sort, label)

    def _build_current_filterset(self) -> FilterSet:
        """Build a FilterSet from the current quick filter + search box."""
        fs = FilterSet(mode="AND")
        idx = self._quick_filter.currentIndex()

        if idx == 1:   # Ikke fundne / Not found
            fs.add(NotFoundFilter())
        elif idx == 2:  # Fundne / Found
            from opensak.filters.engine import FoundFilter
            fs.add(FoundFilter())
        elif idx == 3:  # Tilgængelige ikke fundne / Available not found
            fs.add(AvailableFilter())
            fs.add(NotFoundFilter())
        elif idx == 4:  # Traditional let / Traditional easy
            fs.add(CacheTypeFilter(["Traditional Cache"]))
            fs.add(DifficultyFilter(max_difficulty=2.0))
            fs.add(TerrainFilter(max_terrain=2.0))
            fs.add(AvailableFilter())
        elif idx == 5:  # Arkiverede / Archived
            from opensak.filters.engine import ArchivedFilter
            fs.add(ArchivedFilter())

        # Hide archived caches globally unless the user explicitly asked for them
        # (idx 5 = "Archived" quick filter) or enabled them in display settings.
        if idx != 5 and not get_settings().show_archived:
            from opensak.filters.engine import AvailabilityFilter
            fs.add(AvailabilityFilter(show_avail=True, show_unavail=True, show_archived=False))

        # GC-nummer søgefelt (søger kun i GC kode)
        gc_search = self._search_gc.text().strip()
        if gc_search:
            from opensak.filters.engine import GcCodeFilter
            fs.add(GcCodeFilter(gc_search))

        # Navn-søgefelt — matcher kun på navn (GSAK-style, issue #86)
        # Issue #80 introduced a combined Name+GC code search here, but
        # users prefer the GSAK convention of separate search fields with
        # clear, single purposes. GC code search lives in its own field.
        name_search = self._search_box.text().strip()
        if name_search:
            from opensak.filters.engine import NameFilter
            fs.add(NameFilter(name_search))

        return fs

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_full_cache(self, gc_code: GcCode):
        """
        Indlæs en enkelt cache fra DB med alle relationer eager-loaded.

        apply_filters() bruger noload() på logs/waypoints/user_note for
        performance ved store databaser. Denne hjælper bruges når brugeren
        vælger en cache, så detaljepanelet altid får komplette data.
        """
        from opensak.db.models import Cache as CacheModel
        from sqlalchemy.orm import joinedload
        with get_session() as session:
            return session.query(CacheModel).options(
                joinedload(CacheModel.logs),
                joinedload(CacheModel.attributes),
                joinedload(CacheModel.waypoints),
                joinedload(CacheModel.user_note),
            ).filter_by(gc_code=gc_code).first()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_cache_selected(self, cache: Cache) -> None:
        """Kaldes når brugeren klikker på en cache i tabellen."""
        full = self._load_full_cache(cache.gc_code)
        if not full:
            return
        self._detail_panel.show_cache(full)
        self._map_widget.pan_to_cache(full.gc_code)
        self._map_widget.set_active_cache(full.gc_code)
        self._act_wp_edit.setEnabled(True)
        self._act_wp_delete.setEnabled(True)
        if full.latitude and full.longitude:
            coords = format_coords(full.latitude, full.longitude, get_settings().coord_format)
            self._statusbar.showMessage(
                f"{full.gc_code} — {full.name} ({coords})"
            )

    def _on_map_cache_selected(self, gc_code: GcCode) -> None:
        """Kaldes når brugeren klikker på en pin på kortet."""
        full = self._load_full_cache(gc_code)
        if full:
            self._cache_table.select_by_gc_code(gc_code)
            self._detail_panel.show_cache(full)
            self._map_widget.set_active_cache(gc_code)
            self._statusbar.showMessage(
                f"{full.gc_code} — {full.name}"
            )

    def _on_set_corrected_from_map(self, gc_code: GcCode, lat: float, lon: float) -> None:
        """Sæt korrigerede koordinater på en cache via højreklik på kortet."""
        from opensak.db.database import get_session
        from opensak.db.models import UserNote
        from opensak.coords import format_coords
        from opensak.gui.settings import get_settings
        from sqlalchemy.orm import joinedload

        with get_session() as session:
            cache = session.query(Cache).options(
                joinedload(Cache.user_note)
            ).filter_by(gc_code=gc_code).first()
            if not cache:
                return
            note = cache.user_note
            if note is None:
                note = UserNote(cache_id=cache.id)
                session.add(note)
            note.corrected_lat = lat
            note.corrected_lon = lon
            note.is_corrected = True
            session.commit()

        coords = format_coords(lat, lon, get_settings().coord_format)
        self._statusbar.showMessage(
            tr("map_ctx_corrected_set").format(gc_code=gc_code, coords=coords)
        )
        self._on_corrected_coords_changed(gc_code)

    def _on_corrected_coords_changed(self, gc_code: GcCode) -> None:
        """Update the map pin and table row after corrected coordinates change."""
        self._cache_table.refresh_cache_row(gc_code)
        full = self._load_full_cache(gc_code)
        if full:
            self._map_widget.update_cache(full)

    def _on_search_changed(self, text: str) -> None:
        has_search = bool(self._search_gc.text().strip() or self._search_box.text().strip())
        if has_search:
            self._set_clear_filter_active(True)
        elif not self._active_filter_name:
            self._set_clear_filter_active(False)
        min_chars, debounce_ms = self._search_thresholds()
        if text == "":
            # Clearing always fires immediately
            self._search_timer.stop()
            self._refresh_cache_list()
        elif len(text) >= min_chars:
            # Threshold met — fire quickly to feel responsive
            self._search_timer.start(80)
        else:
            # Below threshold — fire after the full debounce so a pause triggers search
            self._search_timer.start(debounce_ms)

    def _search_thresholds(self) -> tuple[int, int]:
        """Return (min_chars, debounce_ms), adaptive if not overridden in settings."""
        s = get_settings()
        user_min   = s.search_min_chars
        user_delay = s.search_debounce_ms
        count = self._db_count
        if count >= 10_000:
            adaptive_min, adaptive_delay = 3, 600
        elif count >= 1_000:
            adaptive_min, adaptive_delay = 2, 400
        else:
            adaptive_min, adaptive_delay = 1, 200
        min_chars   = user_min   if user_min   > 0 else adaptive_min
        debounce_ms = user_delay if user_delay > 0 else adaptive_delay
        return min_chars, debounce_ms

    def _on_quick_filter_changed(self, index: int) -> None:
        self._refresh_cache_list()

    # ── Drag & drop ───────────────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept drag if it contains GPX or ZIP files."""
        mime = event.mimeData()
        if mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls()]
            if any(p.lower().endswith((".gpx", ".zip", ".loc")) for p in paths):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        """Open import dialog with dropped GPX/ZIP files pre-loaded."""
        from pathlib import Path
        from opensak.gui.dialogs.import_dialog import ImportDialog

        if self._trip_planner_active():
            self._warn_trip_planner_active()
            event.ignore()
            return

        paths = [
            Path(u.toLocalFile())
            for u in event.mimeData().urls()
            if u.toLocalFile().lower().endswith((".gpx", ".zip", ".loc"))
        ]
        if not paths:
            event.ignore()
            return

        event.acceptProposedAction()
        dlg = ImportDialog(self)
        dlg.add_files(paths)
        dlg.import_completed.connect(self._refresh_after_import)
        dlg.exec()

    def _open_import_dialog(self) -> None:
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.import_dialog import ImportDialog
        dlg = ImportDialog(self)
        dlg.import_completed.connect(self._refresh_after_import)
        dlg.exec()

    def _refresh_after_import(self) -> None:
        """Reload both cache table and map after a successful import."""
        self._refresh_cache_list()
        count = self._cache_table.row_count()
        self._statusbar.showMessage(
            tr("import_table_loaded", count=count), 5000
        )

    def _refresh_table_only(self) -> None:
        """Reload cache-tabellen uden at opdatere kortet. Bruges efter import."""
        fs = self._build_current_filterset()
        with get_session() as session:
            caches = apply_filters(session, fs, self._current_sort)
        self._cache_table.load_caches(caches)
        count = self._cache_table.row_count()
        if count == 1:
            self._count_lbl.setText(tr("count_cache_single"))
        else:
            self._count_lbl.setText(tr("count_caches", count=count))
        self._statusbar.showMessage(
            tr("import_table_loaded", count=count), 5000
        )

    def _open_settings(self) -> None:
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        if dlg.exec():
            self._reload_home_combo()
            self._map_widget.reload_map(self._refresh_cache_list)
            self._refresh_cache_list()
            cache = self._cache_table.selected_cache()
            if cache:
                full = self._load_full_cache(cache.gc_code)
                if full:
                    self._detail_panel.show_cache(full)

    def _reload_home_combo(self) -> None:
        """Genindlæs hjemmepunkts-dropdown fra settings."""
        s = get_settings()
        points = s.home_points
        active = s.active_home_name
        self._home_combo.blockSignals(True)
        self._home_combo.clear()
        if not points:
            self._home_combo.addItem(tr("toolbar_home_no_points"), None)
        else:
            for p in points:
                self._home_combo.addItem(p.name, p.name)
            # Sæt aktiv
            for i in range(self._home_combo.count()):
                if self._home_combo.itemData(i) == active:
                    self._home_combo.setCurrentIndex(i)
                    break
        self._home_combo.blockSignals(False)
        self._sync_active_home_coords()

    def _sync_active_home_coords(self) -> None:
        # _reload_home_combo blocks signals, so _on_home_changed never fires
        # during a reload. This ensures home_lat/home_lon always reflect the
        # active home point before _update_distances reads them.
        s = get_settings()
        name = s.active_home_name
        for p in s.home_points:
            if p.name == name:
                if p.name == "★ Home":
                    real = s.get_gc_home_point()
                    s.set_active_home(real if real else p)
                else:
                    s.set_active_home(p)
                return

    def _on_home_changed(self, index: int) -> None:
        """Skift aktivt hjemmepunkt — gem per-db og pan kort."""
        name = self._home_combo.itemData(index)
        if not name:
            return
        s = get_settings()
        for p in s.home_points:
            if p.name == name:
                # ★ Home: brug koordinater fra gc_home_location
                if p.name == "★ Home":
                    real = s.get_gc_home_point()
                    point = real if real else p
                else:
                    point = p
                # Gem via settings API
                s.set_active_home(point)
                # Pan kort til ny lokation — INGEN HTML reload
                self._map_widget.pan_to_location(point.lat, point.lon, point.name)
                # Opdater distances i cache-listen
                self._refresh_cache_list()
                self._update_info_bar()
                self._statusbar.showMessage(
                    tr("status_home_changed", name=point.name), 3000
                )
                break

    def _initial_load(self) -> None:
        """Første load ved opstart — vent på kort hvis ikke klar."""
        if not self._map_widget.is_ready():
            self._map_widget.set_pending_refresh(self._refresh_cache_list)
        else:
            self._refresh_cache_list()

    def _check_setup_complete(self) -> None:
        """Vis velkomst-dialog hvis setup mangler."""
        from opensak.gui.settings import get_settings
        s = get_settings()
        if not s.is_setup_complete():
            from opensak.gui.icon import OpenSAKMessageBox
            msg = OpenSAKMessageBox(self)
            msg.setWindowTitle(tr("setup_welcome_title"))
            msg.setText(tr("setup_welcome_msg"))
            msg.setStandardButtons(
                OpenSAKMessageBox.StandardButton.Ok |
                OpenSAKMessageBox.StandardButton.Cancel
            )
            msg.button(OpenSAKMessageBox.StandardButton.Ok).setText(
                tr("setup_open_settings")
            )
            if msg.exec() == OpenSAKMessageBox.StandardButton.Ok:
                self._open_settings()

    def _next_cw_id(self) -> str:
        """Return the next available CWnnn id for the active database."""
        from opensak.db.database import get_session
        from opensak.db.models import Cache
        import re
        with get_session() as session:
            rows = (
                session.query(Cache.gc_code)
                .filter(Cache.gc_code.like("CW%"))
                .all()
            )
        nums = []
        for (gc,) in rows:
            m = re.fullmatch(r"CW(\d+)", gc or "")
            if m:
                nums.append(int(m.group(1)))
        nxt = max(nums, default=0) + 1
        return f"CW{nxt:03d}"

    def _add_waypoint(self) -> None:
        from opensak.gui.dialogs.waypoint_dialog import WaypointDialog
        from opensak.db.database import get_session
        from opensak.db.models import Cache
        dlg = WaypointDialog(self, next_cw_id=self._next_cw_id())
        if dlg.exec():
            data = dlg.get_data()
            with get_session() as session:
                existing = session.query(Cache).filter_by(
                    gc_code=data["gc_code"]
                ).first()
                if existing:
                    QMessageBox.warning(
                        self,
                        tr("wp_already_exists_title"),
                        tr("wp_already_exists_msg", gc_code=data["gc_code"])
                    )
                    return
                cache = Cache(**data)
                session.add(cache)
            self._refresh_cache_list()
            self._statusbar.showMessage(
                tr("status_cache_added", gc_code=data["gc_code"]), 3000
            )

    def _edit_waypoint(self) -> None:
        cache = self._cache_table.selected_cache()
        if not cache:
            return
        self._edit_waypoint_from_cache(cache)

    def _edit_waypoint_from_cache(self, cache) -> None:
        """Åbn WaypointDialog for en given cache (bruges fra menu og højreklik)."""
        from opensak.gui.dialogs.waypoint_dialog import WaypointDialog
        from opensak.db.database import get_session
        from opensak.db.models import Cache

        # apply_filters() defer()'er short_description/long_description/
        # encoded_hints i listevisningen (ydelse på store DB'er). Cache-objektet
        # fra tabel-rækken kan derfor IKKE bruges direkte her — _populate() ville
        # udløse et forsinket load på en allerede lukket session og kaste
        # DetachedInstanceError. Genindlæs altid en komplet kopi først,
        # samme mønster som _on_cache_selected()/_load_full_cache().
        full_cache = self._load_full_cache(cache.gc_code)
        if not full_cache:
            return

        dlg = WaypointDialog(self, cache=full_cache)
        if dlg.exec():
            data = dlg.get_data()
            with get_session() as session:
                c = session.query(Cache).filter_by(
                    gc_code=data["gc_code"]
                ).first()
                if c:
                    for field, value in data.items():
                        if field != "gc_code":
                            setattr(c, field, value)
            self._refresh_cache_list()
            self._statusbar.showMessage(
                tr("status_cache_updated", gc_code=data["gc_code"]), 3000
            )

    def _delete_waypoint(self) -> None:
        cache = self._cache_table.selected_cache()
        if not cache:
            return
        from opensak.db.database import get_session
        from opensak.db.models import Cache
        reply = QMessageBox.question(
            self,
            tr("wp_delete_title"),
            tr("wp_delete_msg", gc_code=cache.gc_code, name=cache.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            with get_session() as session:
                c = session.query(Cache).filter_by(
                    gc_code=cache.gc_code
                ).first()
                if c:
                    session.delete(c)
            self._detail_panel.clear()
            self._act_wp_edit.setEnabled(False)
            self._act_wp_delete.setEnabled(False)
            self._refresh_cache_list()
            self._statusbar.showMessage(
                tr("status_cache_deleted", gc_code=cache.gc_code), 3000
            )

    def _delete_flagged_caches(self) -> None:
        """Slet alle caches med Flag=True i det aktive filter."""
        caches = self._cache_table.get_flagged_caches()
        if not caches:
            QMessageBox.information(
                self,
                tr("delete_flagged_title"),
                tr("delete_flagged_none"),
            )
            return
        reply = QMessageBox.question(
            self,
            tr("delete_flagged_title"),
            tr("delete_flagged_msg", count=len(caches)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            gc_codes = [c.gc_code for c in caches]
            self._bulk_delete_caches(gc_codes)
            self._detail_panel.clear()
            self._act_wp_edit.setEnabled(False)
            self._act_wp_delete.setEnabled(False)
            self._refresh_cache_list()
            self._statusbar.showMessage(
                tr("status_deleted_count", count=len(gc_codes)), 3000
            )

    def _delete_filtered_caches(self) -> None:
        """Slet alle caches i det aktive filter (uanset flag)."""
        caches = self._cache_table.get_all_caches()
        if not caches:
            QMessageBox.information(
                self,
                tr("delete_filtered_title"),
                tr("delete_filtered_none"),
            )
            return
        reply = QMessageBox.question(
            self,
            tr("delete_filtered_title"),
            tr("delete_filtered_msg", count=len(caches)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            gc_codes = [c.gc_code for c in caches]
            self._bulk_delete_caches(gc_codes)
            self._detail_panel.clear()
            self._act_wp_edit.setEnabled(False)
            self._act_wp_delete.setEnabled(False)
            self._refresh_cache_list()
            self._statusbar.showMessage(
                tr("status_deleted_count", count=len(gc_codes)), 3000
            )

    def _bulk_delete_caches(self, gc_codes: list[str]) -> None:
        """Delete caches and all child records by GC codes (bulk SQL)."""
        from opensak.db.models import (
            Cache as CacheModel, Log, Attribute, Trackable, Waypoint, UserNote,
        )
        with get_session() as session:
            cache_ids = [
                row[0] for row in
                session.query(CacheModel.id).filter(
                    CacheModel.gc_code.in_(gc_codes)
                ).all()
            ]
            if cache_ids:
                session.query(Log).filter(Log.cache_id.in_(cache_ids)).delete(synchronize_session=False)
                session.query(Attribute).filter(Attribute.cache_id.in_(cache_ids)).delete(synchronize_session=False)
                session.query(Trackable).filter(Trackable.cache_id.in_(cache_ids)).delete(synchronize_session=False)
                session.query(Waypoint).filter(Waypoint.cache_id.in_(cache_ids)).delete(synchronize_session=False)
                session.query(UserNote).filter(UserNote.cache_id.in_(cache_ids)).delete(synchronize_session=False)
                session.query(CacheModel).filter(CacheModel.id.in_(cache_ids)).delete(synchronize_session=False)

    def _clear_all_flags(self) -> None:
        """Fjern alle flag (user_flag=False) på alle caches i aktiv database."""
        reply = QMessageBox.question(
            self,
            tr("action_clear_flags"),
            tr("clear_flags_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from opensak.db.models import Cache as CacheModel
            with get_session() as session:
                session.query(CacheModel).filter(
                    CacheModel.user_flag == True  # noqa: E712
                ).update({CacheModel.user_flag: False}, synchronize_session=False)
            self._refresh_cache_list()
            self._statusbar.showMessage(tr("status_flags_cleared"), 3000)

    def _on_flags_changed(self) -> None:
        """Opdatér statuslinjen når et flag toggler."""
        flagged = len(self._cache_table.get_flagged_caches())
        total = self._cache_table.row_count()
        if flagged:
            self._statusbar.showMessage(
                tr("status_flagged_count", flagged=flagged, total=total), 3000
            )
        self._update_info_bar()

    def _on_sort_changed(self, col_id: str, ascending: bool) -> None:
        """Kaldes når brugeren klikker en kolonneheader i tabellen."""
        self._current_sort = SortSpec(col_id, ascending=ascending)
        self._save_sort_for_active_db()

    def _save_sort_for_active_db(self) -> None:
        """Gem aktuel sortering og aktivt filter-profil per database i opensak.json."""
        from opensak.db.manager import get_db_manager
        from opensak.settings_store import get_store
        manager = get_db_manager()
        if not manager.active:
            print("DEBUG save: ingen aktiv database")
            return
        key = f"sort.{str(manager.active.path)}"
        get_store().set_many({
            f"{key}.field":          self._current_sort.field,
            f"{key}.ascending":      self._current_sort.ascending,
            f"{key}.filter_profile": self._active_filter_name,
        })

    def _load_sort_for_active_db(self) -> None:
        """Indlaes gemt sortering og filter-profil for den aktive database fra opensak.json."""
        from opensak.db.manager import get_db_manager
        from opensak.settings_store import get_store
        from opensak.filters.engine import FilterProfile
        manager = get_db_manager()
        if not manager.active:
            print("DEBUG load: ingen aktiv database")
            return
        s = get_store()
        key = f"sort.{str(manager.active.path)}"
        field = str(s.get(f"{key}.field", "name"))
        asc_raw = s.get(f"{key}.ascending", True)
        ascending = asc_raw if isinstance(asc_raw, bool) else str(asc_raw).lower() in ("true", "1", "yes")
        self._current_sort = SortSpec(field, ascending=ascending)
        # Genanvend sort-indikatoren i tabellen hvis den allerede er loaded
        if hasattr(self, "_cache_table"):
            self._cache_table.apply_sort(field, ascending)
        # Genindlæs gemt filter-profil for denne database
        profile_name = str(s.get(f"{key}.filter_profile", ""))
        if profile_name:
            paths = FilterProfile.list_profiles()
            for path in paths:
                try:
                    profile = FilterProfile.load(path)
                    if profile.name == profile_name:
                        self._current_filterset = profile.filterset
                        self._current_sort = profile.sort
                        self._active_filter_name = profile.name
                        self._set_clear_filter_active(True)
                        if hasattr(self, "_filter_lbl"):
                            self._filter_lbl.setText(f"🔍 {profile.name}")
                        if hasattr(self, "_filter_profile_combo"):
                            self._populate_filter_profile_combo(select_name=profile.name)
                        return
                except Exception as e:
                    print(f"DEBUG load: fejl ved indlæsning af {path}: {e}")
        # Ingen gemt profil — nulstil filter
        self._current_filterset = FilterSet()
        self._active_filter_name = ""
        self._set_clear_filter_active(False)
        if hasattr(self, "_filter_lbl"):
            self._filter_lbl.setText("")
        if hasattr(self, "_filter_profile_combo"):
            self._populate_filter_profile_combo(select_name=None)

    # ── Trip Planner guard ────────────────────────────────────────────────────

    def _trip_planner_active(self) -> bool:
        """Returnerer True hvis Trip Planner vinduet er åbent."""
        return (
            hasattr(self, "_trip_planner_win")
            and self._trip_planner_win is not None
            and self._trip_planner_win.isVisible()
        )

    def _warn_trip_planner_active(self) -> None:
        """Bringer Trip Planner i forgrunden og viser en statusbar-besked."""
        if self._trip_planner_win is not None:
            self._trip_planner_win.raise_()
            self._trip_planner_win.activateWindow()
        self._statusbar.showMessage(tr("trip_planner_close_first"), 3000)

    # ── Dialog åbne-metoder ────────────────────────────────────────────────────

    def _open_filter_dialog(self) -> None:
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.filter_dialog import FilterDialog
        dlg = FilterDialog(self, self._current_filterset, self._active_filter_name)
        dlg.filter_applied.connect(self._on_filter_applied)
        dlg.exec()

    def _on_filter_applied(self, filterset, sort, profile_name: str) -> None:
        self._current_filterset = filterset
        self._current_sort = sort
        self._active_filter_name = profile_name
        self._save_sort_for_active_db()
        self._set_clear_filter_active(True)
        label = profile_name if profile_name else tr("filter_active_label")
        self._filter_lbl.setText(f"🔍 {label}")
        self._quick_filter.setCurrentIndex(0)
        self._populate_filter_profile_combo(select_name=profile_name)
        with get_session() as session:
            from opensak.filters.engine import apply_filters
            caches = apply_filters(session, filterset, sort)
        self._cache_table.load_caches(caches)
        self._map_widget.load_caches(caches)
        count = self._cache_table.row_count()
        if count == 1:
            self._count_lbl.setText(tr("count_cache_single"))
        else:
            self._count_lbl.setText(tr("count_caches", count=count))
        self._statusbar.showMessage(tr("status_filter_result", count=count), 3000)
        self._update_info_bar()

    def _set_clear_filter_active(self, active: bool) -> None:
        """Sæt klar-filter knappens farve og tilstand — rød når aktiv, grå når inaktiv."""
        self._btn_clear_filter.setEnabled(active)
        if active:
            self._btn_clear_filter.setStyleSheet(
                "QPushButton { color: #d32f2f; font-size: 14px; font-weight: bold; border: none; }"
                "QPushButton:hover { color: #b71c1c; }"
            )
        else:
            self._btn_clear_filter.setStyleSheet(
                "QPushButton { color: #9e9e9e; font-size: 14px; font-weight: bold; border: none; }"
            )

    def _clear_filter(self) -> None:
        self._current_filterset = FilterSet()
        self._active_filter_name = ""
        for field in (self._search_gc, self._search_box):
            field.blockSignals(True)
            field.clear()
            field.blockSignals(False)
        self._set_clear_filter_active(False)
        self._filter_lbl.setText("")
        self._populate_filter_profile_combo(select_name=None)
        self._refresh_cache_list()
        self._statusbar.showMessage(tr("status_filter_reset"), 3000)

    def _populate_filter_profile_combo(self, select_name: str | None = None) -> None:
        """Genindlæs alle gemte filter-profiler i toolbar-dropdown.

        Kalder blockSignals for at undgå at currentIndexChanged-signalet
        afirer mens vi udfylder listen.
        """
        from opensak.filters.engine import FilterProfile
        self._filter_profile_combo.blockSignals(True)
        self._filter_profile_combo.clear()
        self._filter_profile_combo.addItem(tr("toolbar_filter_combo_none"), userData=None)
        paths = FilterProfile.list_profiles()
        for path in paths:
            try:
                profile = FilterProfile.load(path)
                self._filter_profile_combo.addItem(profile.name, userData=path)
            except Exception:
                pass
        # Sæt valgt element
        if select_name:
            idx = self._filter_profile_combo.findText(select_name)
            self._filter_profile_combo.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            self._filter_profile_combo.setCurrentIndex(0)
        self._filter_profile_combo.blockSignals(False)

    def _on_filter_profile_combo_changed(self, index: int) -> None:
        """Bruger har valgt en profil i toolbar-dropdown — anvend filteret øjeblikkeligt."""
        if index == 0:
            # "Ingen" valgt — ryd aktivt filter
            self._clear_filter()
            return
        path = self._filter_profile_combo.itemData(index)
        if path is None:
            return
        from opensak.filters.engine import FilterProfile
        try:
            profile = FilterProfile.load(path)
        except Exception as exc:
            self._statusbar.showMessage(str(exc), 4000)
            return
        self._current_filterset = profile.filterset
        self._current_sort = profile.sort
        self._active_filter_name = profile.name
        self._save_sort_for_active_db()
        self._set_clear_filter_active(True)
        self._filter_lbl.setText(f"🔍 {profile.name}")
        self._quick_filter.setCurrentIndex(0)
        with get_session() as session:
            from opensak.filters.engine import apply_filters
            caches = apply_filters(session, profile.filterset, profile.sort)
        self._cache_table.load_caches(caches)
        self._map_widget.load_caches(caches)
        count = self._cache_table.row_count()
        if count == 1:
            self._count_lbl.setText(tr("count_cache_single"))
        else:
            self._count_lbl.setText(tr("count_caches", count=count))
        self._statusbar.showMessage(tr("status_filter_result", count=count), 3000)
        self._update_info_bar()

    def _open_column_chooser(self) -> None:
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.column_dialog import ColumnChooserDialog
        dlg = ColumnChooserDialog(self)
        if dlg.exec():
            self._cache_table.reload_columns()

    def _open_gps_export(self) -> None:
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.gps_dialog import GpsExportDialog
        caches = [
            self._cache_table._model.cache_at(i)
            for i in range(self._cache_table.row_count())
        ]
        caches = [c for c in caches if c is not None]
        dlg = GpsExportDialog(self, caches=caches)
        dlg.exec()

    def _open_file_export(self) -> None:
        # Format (GPX / LOC / GGZ) is chosen inside the dialog.
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        caches = [
            self._cache_table._model.cache_at(i)
            for i in range(self._cache_table.row_count())
        ]
        caches = [c for c in caches if c is not None]
        if not caches:
            QMessageBox.information(
                self,
                tr("kml_no_caches_title"),
                tr("kml_no_caches_msg"),
            )
            return
        from opensak.gui.dialogs.file_export_dialog import FileExportDialog
        dlg = FileExportDialog(caches, parent=self)
        dlg.exec()

    def _open_kml_export(self) -> None:
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        caches = [
            self._cache_table._model.cache_at(i)
            for i in range(self._cache_table.row_count())
        ]
        caches = [c for c in caches if c is not None]
        if not caches:
            QMessageBox.information(
                self,
                tr("kml_no_caches_title"),
                tr("kml_no_caches_msg"),
            )
            return
        from opensak.gui.dialogs.kml_export_dialog import KmlExportDialog
        dlg = KmlExportDialog(caches, parent=self)
        dlg.exec()

    def _open_trip_planner(self) -> None:
        from opensak.gui.dialogs.trip_dialog import TripPlannerDialog
        # Issue #134: only one Trip Planner window at a time — if already open,
        # just bring it to the front instead of opening a second instance.
        if (
            hasattr(self, "_trip_planner_win")
            and self._trip_planner_win is not None
            and self._trip_planner_win.isVisible()
        ):
            self._trip_planner_win.raise_()
            self._trip_planner_win.activateWindow()
            return

        caches = [
            self._cache_table._model.cache_at(i)
            for i in range(self._cache_table.row_count())
        ]
        caches = [c for c in caches if c is not None]
        # show() i stedet for exec() — ikke-modal så kortvinduet kan få fokus
        self._trip_planner_win = TripPlannerDialog(self, caches=caches)
        self._trip_planner_win.show()
        self._trip_planner_win.raise_()
        self._trip_planner_win.activateWindow()

    def _open_found_updater(self) -> None:
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.found_dialog import FoundUpdaterDialog
        dlg = FoundUpdaterDialog(self)
        dlg.update_completed.connect(self._refresh_cache_list)
        dlg.exec()

    def _open_update_location(self) -> None:
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.update_location_dialog import UpdateLocationDialog
        dlg = UpdateLocationDialog(self)
        dlg.location_updated.connect(self._refresh_cache_list)
        dlg.exec()

    def _open_download_boundaries(self) -> None:
        from opensak.gui.dialogs.boundary_packs_dialog import BoundaryDownloadDialog
        BoundaryDownloadDialog(self).exec()

    def _open_check_boundaries(self) -> None:
        from opensak.gui.dialogs.boundary_packs_dialog import BoundaryCheckDialog
        BoundaryCheckDialog(self).exec()

    def _open_coord_converter(self) -> None:
        """Åbn koordinatkonverter — præ-udfyld med valgt cache hvis mulig."""
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.coord_converter_dialog import CoordConverterDialog
        cache = self._cache_table.selected_cache()
        if cache and cache.latitude and cache.longitude:
            dlg = CoordConverterDialog(cache.latitude, cache.longitude, parent=self)
        else:
            dlg = CoordConverterDialog(parent=self)
        dlg.exec()

    def _open_projection(self) -> None:
        """Åbn koordinatprojektions-dialog — præ-udfyld med valgt cache hvis mulig."""
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.projection_dialog import ProjectionDialog
        cache = self._cache_table.selected_cache()
        if cache and cache.latitude and cache.longitude:
            dlg = ProjectionDialog(cache.latitude, cache.longitude, parent=self)
        else:
            dlg = ProjectionDialog(parent=self)
        dlg.exec()

    def _open_checksum(self) -> None:
        """Åbn tjeksum-beregner — præ-udfyld med valgt cache hvis mulig."""
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.checksum_dialog import ChecksumDialog
        cache = self._cache_table.selected_cache()
        if cache and cache.latitude and cache.longitude:
            dlg = ChecksumDialog(cache.latitude, cache.longitude, parent=self)
        else:
            dlg = ChecksumDialog(parent=self)
        dlg.exec()

    def _open_midpoint(self) -> None:
        """Åbn midtpunkt-beregner — præ-udfyld punkt A med valgt cache hvis mulig."""
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.midpoint_dialog import MidpointDialog
        cache = self._cache_table.selected_cache()
        if cache and cache.latitude and cache.longitude:
            dlg = MidpointDialog(cache.latitude, cache.longitude, parent=self)
        else:
            dlg = MidpointDialog(parent=self)
        dlg.exec()

    def _open_dist_bearing(self) -> None:
        """Åbn afstand & retning — præ-udfyld punkt A med valgt cache hvis mulig."""
        if self._trip_planner_active():
            self._warn_trip_planner_active()
            return
        from opensak.gui.dialogs.distance_bearing_dialog import DistanceBearingDialog
        cache = self._cache_table.selected_cache()
        if cache and cache.latitude and cache.longitude:
            dlg = DistanceBearingDialog(cache.latitude, cache.longitude, parent=self)
        else:
            dlg = DistanceBearingDialog(parent=self)
        dlg.exec()

    def _show_about(self) -> None:
        from opensak import __version__
        QMessageBox.about(
            self,
            tr("about_title"),
            tr("about_text", version=__version__),
        )

    def _open_log_file(self) -> None:
        """Åbn logfilen i systemets standard tekstprogram (issue #232)."""
        from opensak.config import get_log_path
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        log_path = get_log_path()
        if not log_path.exists():
            QMessageBox.information(
                self,
                tr("action_open_log_file"),
                tr("log_file_not_found", path=str(log_path)),
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path)))

    # ── Opdateringsstjek ───────────────────────────────────────────────────────

    def _check_update_background(self) -> None:
        """Kald ved opstart — tjekker lydløst i baggrunden."""
        from opensak import __version__
        from opensak.gui.settings import get_settings
        if not get_settings().updates_check_enabled:
            return
        self._update_worker = UpdateCheckWorker(__version__, parent=self)
        self._update_worker.update_available.connect(self._on_update_available)
        self._update_worker.start()

    def _check_update_manual(self) -> None:
        """Kald fra menuen — viser resultat uanset om der er opdatering."""
        from opensak import __version__
        self._manual_update_worker = UpdateCheckWorker(__version__, parent=self)
        self._manual_update_worker.update_available.connect(
            lambda tag, url, is_prerelease: self._on_update_available(
                tag, url, is_prerelease, manual=True
            )
        )
        self._manual_update_worker.check_done.connect(
            self._on_manual_check_done
        )
        self._manual_update_worker.start()
        self._manual_found_update = False

    def _on_manual_check_done(self) -> None:
        """Vises kun ved manuel tjek — hvis ingen opdatering fundet."""
        if not getattr(self, "_manual_found_update", False):
            QMessageBox.information(
                self,
                tr("update_uptodate_title"),
                tr("update_uptodate_msg"),
            )

    def _on_update_available(
        self, latest_tag: str, url: str, is_prerelease: bool = False, *, manual: bool = False
    ) -> None:
        """Vis notifikationsdialog om ny version (stabil eller beta)."""
        self._manual_found_update = True

        # Ved automatisk tjek: ignorer versioner brugeren har valgt at springe over
        if not manual:
            from opensak.gui.settings import get_settings
            if get_settings().updates_skipped_version == latest_tag:
                return

        from opensak import __version__
        changelog_url = "https://github.com/AgreeDK/opensak/blob/main/CHANGELOG.md"

        msg = QMessageBox(self)
        if is_prerelease:
            msg.setWindowTitle(tr("beta_update_available_title"))
            msg.setText(tr("beta_update_available_msg", latest=latest_tag, current=__version__))
        else:
            msg.setWindowTitle(tr("update_available_title"))
            msg.setText(tr("update_available_msg", latest=latest_tag, current=__version__))
        msg.setInformativeText(
            tr("update_available_info")
            + f'  <a href="{changelog_url}">{tr("update_changelog")}</a>'
        )
        msg.setTextFormat(Qt.TextFormat.RichText)
        btn_open = msg.addButton(tr("update_open_releases"), QMessageBox.ButtonRole.AcceptRole)
        btn_skip = msg.addButton(tr("update_skip_version"), QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton(tr("update_later"), QMessageBox.ButtonRole.RejectRole)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == btn_open:
            import webbrowser
            webbrowser.open(url)
        elif clicked == btn_skip:
            from opensak.gui.settings import get_settings
            get_settings().updates_skipped_version = latest_tag

