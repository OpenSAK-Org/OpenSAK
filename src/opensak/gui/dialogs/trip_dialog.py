"""
src/opensak/gui/dialogs/trip_dialog.py — Turplanlægger dialog.

To faner:
  1. Radius  — find caches inden for X km fra centerpunkt
  2. Rute    — find caches langs en rute med op til 10 punkter (A→B→C…)
"""

from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING
import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QGroupBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox,
    QTabWidget, QWidget, QLineEdit, QListWidget,
    QListWidgetItem, QFrame, QScrollArea
)

from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr
from opensak.gui.settings import get_settings
from opensak.gui.theme import hint_style
from opensak.filters.engine import _haversine_km
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen


# ── Geometri-hjælper ──────────────────────────────────────────────────────────

def _dist_to_segment_km(
    p_lat: float, p_lon: float,
    a_lat: float, a_lon: float,
    b_lat: float, b_lon: float,
) -> float:
    """
    Mindste afstand i km fra punkt P til linjestykket A→B.
    Bruger plan approksimation (god nok for < 500 km segmenter).
    """
    # Konvertér til approks. kartesiske koordinater (km)
    cos_lat = math.cos(math.radians((a_lat + b_lat) / 2))
    ax = a_lon * cos_lat * 111.32
    ay = a_lat * 111.32
    bx = b_lon * cos_lat * 111.32
    by = b_lat * 111.32
    px = p_lon * cos_lat * 111.32
    py = p_lat * 111.32

    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 1e-12:          # A og B er samme punkt
        return math.hypot(px - ax, py - ay)

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _dist_to_route_km(
    p_lat: float, p_lon: float,
    waypoints: list[tuple[float, float]],
) -> float:
    """Mindste afstand fra punkt P til en hvilken som helst del af ruten."""
    if len(waypoints) == 1:
        return _haversine_km(p_lat, p_lon, waypoints[0][0], waypoints[0][1])
    best = float("inf")
    for i in range(len(waypoints) - 1):
        d = _dist_to_segment_km(
            p_lat, p_lon,
            waypoints[i][0], waypoints[i][1],
            waypoints[i + 1][0], waypoints[i + 1][1],
        )
        best = min(best, d)
    return best


def _position_along_route(
    p_lat: float, p_lon: float,
    waypoints: list[tuple[float, float]],
) -> float:
    """
    Returner en 'position' (0.0 … total_længde) der angiver hvor langt
    langs ruten det nærmeste punkt er. Bruges til at sortere caches
    i kørerækkefølge.
    """
    if len(waypoints) == 1:
        return 0.0

    cos_lat = math.cos(math.radians(
        sum(w[0] for w in waypoints) / len(waypoints)
    ))

    def to_xy(lat, lon):
        return lon * cos_lat * 111.32, lat * 111.32

    px, py = to_xy(p_lat, p_lon)
    best_pos = 0.0
    best_dist = float("inf")
    cumulative = 0.0

    for i in range(len(waypoints) - 1):
        ax, ay = to_xy(waypoints[i][0], waypoints[i][1])
        bx, by = to_xy(waypoints[i + 1][0], waypoints[i + 1][1])
        dx, dy = bx - ax, by - ay
        seg_len = math.hypot(dx, dy)
        seg_len_sq = seg_len * seg_len

        if seg_len_sq < 1e-12:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))

        proj_x = ax + t * dx
        proj_y = ay + t * dy
        d = math.hypot(px - proj_x, py - proj_y)

        if d < best_dist:
            best_dist = d
            best_pos = cumulative + t * seg_len

        cumulative += seg_len

    return best_pos


# ── Fælles preview-mixin ──────────────────────────────────────────────────────

# Mixin'en kombineres altid med en QDialog; for type-checkeren arver den
# QWidget (så self kan bruges som dialog-parent), men ikke ved runtime hvor
# det ville give metaklasse-konflikt med QDialog.
if TYPE_CHECKING:
    _PreviewMixinBase = QWidget
else:
    _PreviewMixinBase = object


class _PreviewMixin(_PreviewMixinBase):
    """Delt preview-tabel og eksport-logik for begge faner."""

    _selected_caches: list

    def _build_preview_widget(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._result_lbl = QLabel("")
        self._result_lbl.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._result_lbl)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            tr("search_gc_label"),
            tr("col_name"),
            tr("col_type"),
            "D", "T",
            tr("col_distance"),
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        for col in (0, 3, 4, 5):
            self._table.setColumnWidth(col, 75)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        layout.addWidget(self._table)

        # Export-knapper
        btn_row = QHBoxLayout()
        self._btn_export_gps = QPushButton(tr("trip_btn_export_gps"))
        self._btn_export_gps.setStyleSheet("font-weight: bold;")
        self._btn_export_gps.setEnabled(False)
        self._btn_export_gps.clicked.connect(self._export_to_gps)
        btn_row.addWidget(self._btn_export_gps)

        self._btn_export_file = QPushButton(tr("trip_btn_export_file"))
        self._btn_export_file.setEnabled(False)
        self._btn_export_file.clicked.connect(self._export_to_file)
        btn_row.addWidget(self._btn_export_file)

        self._btn_save_db = QPushButton(tr("trip_btn_save_db"))
        self._btn_save_db.setEnabled(False)
        self._btn_save_db.setToolTip(tr("trip_btn_save_db_tooltip"))
        self._btn_save_db.clicked.connect(self._save_to_database)
        btn_row.addWidget(self._btn_save_db)

        btn_row.addStretch()

        self._btn_preview_map = QPushButton(tr("trip_btn_preview_map"))
        self._btn_preview_map.setEnabled(False)
        self._btn_preview_map.setToolTip(tr("trip_btn_preview_map_tooltip"))
        self._btn_preview_map.clicked.connect(self._open_map_preview)
        btn_row.addWidget(self._btn_preview_map)

        layout.addLayout(btn_row)
        return w

    def _populate_table(
        self,
        caches: list,
        dist_map: dict,
        warning: str = "",
    ) -> None:
        count = len(caches)
        if warning:
            self._result_lbl.setText(warning)
            self._result_lbl.setStyleSheet("font-weight: bold; color: #c62828;")
        else:
            self._result_lbl.setText(tr("trip_result_label", count=count))
            self._result_lbl.setStyleSheet("font-weight: bold; color: inherit;")

        self._btn_export_gps.setEnabled(count > 0 and not warning)
        self._btn_export_file.setEnabled(count > 0 and not warning)
        self._btn_save_db.setEnabled(count > 0 and not warning)
        self._btn_preview_map.setEnabled(count > 0 and not warning)

        s = get_settings()
        unit = "mi" if s.use_miles else "km"
        factor = 0.621371 if s.use_miles else 1.0

        self._table.setRowCount(count)
        for row, cache in enumerate(caches):
            d = dist_map.get(id(cache), None)
            dist_str = f"{d * factor:.1f} {unit}" if (
                d is not None and d < float("inf")
            ) else "?"
            cache_type = (cache.cache_type or "").replace(
                " Cache", ""
            ).replace("Unknown", "Mystery")
            items = [
                cache.gc_code or "",
                cache.name or "",
                cache_type,
                f"{cache.difficulty:.1f}" if cache.difficulty else "?",
                f"{cache.terrain:.1f}" if cache.terrain else "?",
                dist_str,
            ]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                if col in (3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)

    def _export_to_gps(self) -> None:
        if not self._selected_caches:
            return
        from opensak.gui.dialogs.gps_dialog import GpsExportDialog
        dlg = GpsExportDialog(self, caches=self._selected_caches)
        dlg.exec()

    def _export_to_file(self) -> None:
        if not self._selected_caches:
            return
        from PySide6.QtWidgets import QFileDialog
        from opensak.gps.garmin import export_to_file
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("trip_save_dialog_title"),
            str(Path.home() / tr("trip_default_file")),
            tr("gps_file_filter", fmt="GPX", ext="gpx"),
        )
        if not path:
            return
        try:
            from opensak.db.database import reload_caches_full
            caches = reload_caches_full(self._selected_caches)
            result = export_to_file(caches, Path(path))
            QMessageBox.information(
                self, tr("trip_export_done_title"), str(result)
            )
        except Exception as e:
            QMessageBox.critical(self, tr("error"), str(e))

    def _open_map_preview(self) -> None:
        """Åbn popup-vindue med de valgte tur-caches vist på kort."""
        if not self._selected_caches:
            return
        from PySide6.QtWidgets import QApplication
        # Gem reference på QApplication så vinduet lever uafhængigt af dialogen
        # og ikke lukkes når Trip Planner lukkes
        app = QApplication.instance()
        win = TripMapPreviewDialog(self._selected_caches)
        # Gem på app-objektet så GC ikke sletter det
        if app is not None:
            app._trip_map_win = win  # type: ignore[attr-defined]
        win.show()
        win.raise_()
        win.activateWindow()

    def _save_to_database(self) -> None:
        """Gem de valgte tur-caches i en ny eller eksisterende database."""
        if not self._selected_caches:
            return

        from PySide6.QtWidgets import QFileDialog, QInputDialog
        from opensak.db.database import _make_engine, _run_migrations
        from opensak.db.models import Base, Cache as CacheModel
        from sqlalchemy.orm import Session
        from sqlalchemy import select

        # ── Vælg: ny eller eksisterende database ─────────────────────────────
        choices = [
            tr("trip_db_choice_new"),
            tr("trip_db_choice_existing"),
        ]
        choice, ok = QInputDialog.getItem(
            self,
            tr("trip_db_choice_title"),
            tr("trip_db_choice_label"),
            choices, 0, False,
        )
        if not ok:
            return

        new_db = (choice == tr("trip_db_choice_new"))

        # Brug samme mappe som den aktive database som default
        try:
            from opensak.db.manager import get_db_manager
            active_path = get_db_manager().active_path
            default_dir = active_path.parent if active_path else Path.home()
        except Exception:
            default_dir = Path.home()

        if new_db:
            path, _ = QFileDialog.getSaveFileName(
                self,
                tr("trip_db_new_title"),
                str(default_dir / "tur.db"),
                tr("trip_db_filter"),
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self,
                tr("trip_db_open_title"),
                str(default_dir),
                tr("trip_db_filter"),
            )

        if not path:
            return

        db_path = Path(path)

        try:
            engine = _make_engine(db_path)
            # Opret tabeller hvis de ikke findes (ny database)
            Base.metadata.create_all(engine)
            _run_migrations(engine)

            added = 0
            skipped = 0

            with Session(engine) as session:
                for src in self._selected_caches:
                    # Tjek om GC-kode allerede findes
                    exists = session.scalar(
                        select(CacheModel).where(
                            CacheModel.gc_code == src.gc_code
                        )
                    )
                    if exists:
                        skipped += 1
                        continue

                    # Kopier cache til ny session (detach fra original session)
                    new_cache = CacheModel()
                    # Kopiér alle kolonneværdier
                    for col in CacheModel.__table__.columns:
                        setattr(new_cache, col.name, getattr(src, col.name, None))

                    session.add(new_cache)
                    added += 1

                session.commit()

            msg = tr("trip_db_saved_msg", added=added, skipped=skipped, path=db_path.name)
            QMessageBox.information(self, tr("trip_db_saved_title"), msg)

        except Exception as e:
            QMessageBox.critical(self, tr("error"), str(e))


# ── Hoved-dialog ─────────────────────────────────────────────────────────────

class TripPlannerDialog(_PreviewMixin, QDialog):
    """Dialog til at planlægge en geocaching-tur."""

    MAX_ROUTE_POINTS = 10

    def __init__(self, parent=None, caches=None):
        super().__init__(parent)
        self.setWindowTitle(tr("trip_dialog_title"))
        self.setMinimumWidth(720)
        self.setMinimumHeight(620)
        # Issue #811: cap so this can't exceed the screen at high DPI
        # scaling — paired with wrapping the scrollable content below in
        # a QScrollArea in _setup_ui, since setMinimumHeight(620) alone
        # doesn't stop the dialog growing taller than that on a small
        # screen once the tabs + preview are all populated.
        clamp_dialog_height_to_screen(self, parent)
        self._all_caches = caches or []
        self._selected_caches: list = []
        self._route_points: list[tuple[str, float, float]] = []  # (label, lat, lon)
        self._setup_ui()
        self._update_preview()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Issue #811: filter_group + tabs + preview all go inside a
        # scrollable content widget, so they can be reached even if the
        # dialog's screen-height cap (see __init__) leaves less room than
        # their combined sizeHint needs. The close button stays outside
        # the scroll area, in the dialog's own layout, so it's always
        # visible regardless of scroll position.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        # Criteria-gruppe: antal + kilde-label på én linje
        filter_group = QGroupBox(tr("trip_criteria_group"))
        fl = QVBoxLayout(filter_group)
        fl.setSpacing(6)

        count_row = QHBoxLayout()
        count_row.addWidget(QLabel(tr("trip_count_label")))
        self._spin_count = QSpinBox()
        self._spin_count.setRange(1, 500)
        self._spin_count.setValue(20)
        self._spin_count.setMaximumWidth(90)
        self._spin_count.valueChanged.connect(self._update_preview)
        count_row.addWidget(self._spin_count)
        count_row.addSpacing(16)
        src_lbl = QLabel(tr("trip_source_label", count=len(self._all_caches)))
        src_lbl.setStyleSheet(hint_style(font_size=None, extra="font-style: italic;"))
        count_row.addWidget(src_lbl)
        count_row.addStretch()
        fl.addLayout(count_row)

        content_layout.addWidget(filter_group)

        # ── Faner ─────────────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_radius_tab(), tr("trip_tab_radius"))
        self._tabs.addTab(self._build_route_tab(),  tr("trip_tab_route"))
        self._tabs.currentChanged.connect(self._update_preview)
        content_layout.addWidget(self._tabs)

        # ── Forhåndsvisning ───────────────────────────────────────────────────
        prev_group = QGroupBox(tr("trip_preview_group"))
        prev_layout = QVBoxLayout(prev_group)
        prev_layout.addWidget(self._build_preview_widget())
        content_layout.addWidget(prev_group)

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll)

        # Luk-knap
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    # ── Radius-fane ───────────────────────────────────────────────────────────

    def _build_radius_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        # Radius
        radius_row = QHBoxLayout()
        radius_row.addWidget(QLabel(tr("trip_radius_label")))
        self._spin_radius = QDoubleSpinBox()
        self._spin_radius.setRange(0.0, 500.0)
        self._spin_radius.setValue(0.0)
        self._spin_radius.setSingleStep(5.0)
        self._spin_radius.setMaximumWidth(100)
        self._spin_radius.setSpecialValueText(tr("trip_radius_all"))
        self._spin_radius.valueChanged.connect(self._update_preview)
        s = get_settings()
        unit = tr("trip_unit_mi") if s.use_miles else tr("trip_unit_km")
        radius_row.addWidget(self._spin_radius)
        radius_row.addWidget(QLabel(unit))
        radius_row.addStretch()
        layout.addLayout(radius_row)

        # Sortering
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel(tr("trip_sort_label")))
        self._combo_sort = QComboBox()
        self._combo_sort.addItem(tr("trip_sort_distance"),    "distance")
        self._combo_sort.addItem(tr("trip_sort_difficulty"),  "difficulty")
        self._combo_sort.addItem(tr("trip_sort_terrain"),     "terrain")
        self._combo_sort.addItem(tr("trip_sort_hidden_date"), "hidden_date")
        self._combo_sort.addItem(tr("trip_sort_name"),        "name")
        self._combo_sort.currentIndexChanged.connect(self._update_preview)
        sort_row.addWidget(self._combo_sort)
        sort_row.addStretch()
        layout.addLayout(sort_row)

        # Info-boks
        info = QLabel(tr("trip_center_info"))
        info.setWordWrap(True)
        info.setStyleSheet(
            "color: #1565c0; background: #e3f2fd; "
            "padding: 4px 6px; border-radius: 3px; font-size: 10px;"
        )
        layout.addWidget(info)
        layout.addStretch()
        return w

    # ── Rute-fane ─────────────────────────────────────────────────────────────

    def _build_route_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        # Korridor-bredde
        corr_row = QHBoxLayout()
        corr_row.addWidget(QLabel(tr("trip_corridor_label")))
        self._spin_corridor = QDoubleSpinBox()
        self._spin_corridor.setRange(0.1, 100.0)
        self._spin_corridor.setValue(2.0)
        self._spin_corridor.setSingleStep(0.5)
        self._spin_corridor.setMaximumWidth(100)
        self._spin_corridor.valueChanged.connect(self._update_preview)
        s = get_settings()
        unit = tr("trip_unit_mi") if s.use_miles else tr("trip_unit_km")
        corr_row.addWidget(self._spin_corridor)
        corr_row.addWidget(QLabel(unit))
        corr_row.addStretch()
        layout.addLayout(corr_row)

        # Rutepunkter
        pts_group = QGroupBox(
            tr("trip_route_points_group", max=self.MAX_ROUTE_POINTS)
        )
        pts_layout = QVBoxLayout(pts_group)

        # Liste
        self._route_list = QListWidget()
        self._route_list.setMaximumHeight(140)
        self._route_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self._route_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._route_list.model().rowsMoved.connect(self._on_route_reordered)
        pts_layout.addWidget(self._route_list)

        # Op/ned/slet knapper
        list_btn_row = QHBoxLayout()
        self._btn_pt_up = QPushButton("▲")
        self._btn_pt_up.setMaximumWidth(36)
        self._btn_pt_up.setToolTip(tr("trip_route_btn_up"))
        self._btn_pt_up.clicked.connect(self._move_point_up)
        list_btn_row.addWidget(self._btn_pt_up)

        self._btn_pt_down = QPushButton("▼")
        self._btn_pt_down.setMaximumWidth(36)
        self._btn_pt_down.setToolTip(tr("trip_route_btn_down"))
        self._btn_pt_down.clicked.connect(self._move_point_down)
        list_btn_row.addWidget(self._btn_pt_down)

        self._btn_pt_del = QPushButton("✕")
        self._btn_pt_del.setMaximumWidth(36)
        self._btn_pt_del.setToolTip(tr("trip_route_btn_del"))
        self._btn_pt_del.clicked.connect(self._delete_point)
        list_btn_row.addWidget(self._btn_pt_del)

        self._btn_pt_clear = QPushButton(tr("trip_route_btn_clear"))
        self._btn_pt_clear.clicked.connect(self._clear_points)
        list_btn_row.addWidget(self._btn_pt_clear)
        list_btn_row.addStretch()
        pts_layout.addLayout(list_btn_row)

        layout.addWidget(pts_group)

        # Input til nyt punkt
        add_group = QGroupBox(tr("trip_route_add_group"))
        add_layout = QVBoxLayout(add_group)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("settings_hp_name_label")))
        self._pt_name = QLineEdit()
        self._pt_name.setPlaceholderText(tr("trip_route_name_placeholder"))
        self._pt_name.setMaximumWidth(160)
        name_row.addWidget(self._pt_name)
        name_row.addStretch()
        add_layout.addLayout(name_row)

        coord_row = QHBoxLayout()
        coord_row.addWidget(QLabel(tr("trip_route_coord_label")))
        self._pt_coord = QLineEdit()
        self._pt_coord.setPlaceholderText(tr("coord_conv_placeholder"))
        self._pt_coord.textChanged.connect(self._on_pt_coord_changed)
        coord_row.addWidget(self._pt_coord)
        add_layout.addLayout(coord_row)

        self._pt_hint = QLabel("")
        self._pt_hint.setStyleSheet(
            hint_style(extra="padding-left: 2px;")
        )
        add_layout.addWidget(self._pt_hint)

        # Knap + hent fra hjemmepunkter
        add_btn_row = QHBoxLayout()
        self._btn_pt_add = QPushButton(tr("trip_route_add_btn"))
        self._btn_pt_add.clicked.connect(self._add_route_point)
        add_btn_row.addWidget(self._btn_pt_add)

        self._btn_pt_from_home = QPushButton(tr("trip_route_from_home"))
        self._btn_pt_from_home.setToolTip(tr("trip_route_from_home_tooltip"))
        self._btn_pt_from_home.clicked.connect(self._add_from_home_points)
        add_btn_row.addWidget(self._btn_pt_from_home)

        add_btn_row.addStretch()
        add_layout.addLayout(add_btn_row)

        layout.addWidget(add_group)

        # Info
        route_info = QLabel(tr("trip_route_info"))
        route_info.setWordWrap(True)
        route_info.setStyleSheet(
            "color: #1565c0; background: #e3f2fd; "
            "padding: 4px 6px; border-radius: 3px; font-size: 10px;"
        )
        layout.addWidget(route_info)
        layout.addStretch()
        return w

    # ── Rute-punkt logik ──────────────────────────────────────────────────────

    def _on_pt_coord_changed(self, text: str) -> None:
        if not text.strip():
            self._pt_hint.setText("")
            return
        try:
            from opensak.coords import parse_coords, format_coords
            coord = parse_coords(text)
            if coord is None:
                raise ValueError
            lat, lon = coord
            fmt = get_settings().coord_format
            self._pt_hint.setText(f"✓  {format_coords(lat, lon, fmt)}")
            self._pt_hint.setStyleSheet(
                "color: #2e7d32; font-size: 10px; padding-left: 2px;"
            )
        except Exception:
            self._pt_hint.setText(tr("settings_hp_coord_error"))
            self._pt_hint.setStyleSheet(
                "color: #c62828; font-size: 10px; padding-left: 2px;"
            )

    def _add_route_point(self) -> None:
        if len(self._route_points) >= self.MAX_ROUTE_POINTS:
            QMessageBox.warning(
                self, tr("warning"),
                tr("trip_route_max_reached", max=self.MAX_ROUTE_POINTS)
            )
            return
        coord_text = self._pt_coord.text().strip()
        name = self._pt_name.text().strip()
        if not coord_text:
            QMessageBox.warning(self, tr("warning"), tr("settings_hp_coord_required"))
            return
        try:
            from opensak.coords import parse_coords
            coord = parse_coords(coord_text)
            if coord is None:
                raise ValueError
            lat, lon = coord
        except Exception:
            QMessageBox.warning(self, tr("warning"), tr("settings_hp_coord_invalid"))
            return

        if not name:
            name = chr(ord('A') + len(self._route_points))

        self._route_points.append((name, lat, lon))
        self._refresh_route_list()
        self._pt_name.clear()
        self._pt_coord.clear()
        self._pt_hint.setText("")
        self._update_preview()

    def _add_from_home_points(self) -> None:
        """Tilføj et hjemmepunkt fra listen som rutepunkt."""
        if len(self._route_points) >= self.MAX_ROUTE_POINTS:
            QMessageBox.warning(
                self, tr("warning"),
                tr("trip_route_max_reached", max=self.MAX_ROUTE_POINTS)
            )
            return
        s = get_settings()
        points = s.home_points
        if not points:
            QMessageBox.information(
                self, tr("info"), tr("trip_route_no_home_points")
            )
            return

        from PySide6.QtWidgets import QInputDialog
        names = [p.name for p in points]
        name, ok = QInputDialog.getItem(
            self,
            tr("trip_route_pick_home_title"),
            tr("trip_route_pick_home_label"),
            names, 0, False
        )
        if ok and name:
            for p in points:
                if p.name == name:
                    if len(self._route_points) >= self.MAX_ROUTE_POINTS:
                        return
                    self._route_points.append((p.name, p.lat, p.lon))
                    self._refresh_route_list()
                    self._update_preview()
                    break

    def _refresh_route_list(self) -> None:
        self._route_list.clear()
        for i, (name, lat, lon) in enumerate(self._route_points):
            from opensak.coords import format_coords
            fmt = get_settings().coord_format
            coord_str = format_coords(lat, lon, fmt)
            label = f"{chr(ord('A') + i)}  {name}  —  {coord_str}"
            self._route_list.addItem(QListWidgetItem(label))

    def _on_route_reordered(self) -> None:
        """Drag-drop i listen — synk _route_points til ny rækkefølge."""
        # Vi gemmer kun (name,lat,lon) — hent fra list-tekst er upræcist,
        # så vi bruger en hjælpe-liste der opdateres ved add/move/del.
        self._update_preview()

    def _move_point_up(self) -> None:
        i = self._route_list.currentRow()
        if i > 0:
            self._route_points[i - 1], self._route_points[i] = (
                self._route_points[i], self._route_points[i - 1]
            )
            self._refresh_route_list()
            self._route_list.setCurrentRow(i - 1)
            self._update_preview()

    def _move_point_down(self) -> None:
        i = self._route_list.currentRow()
        if 0 <= i < len(self._route_points) - 1:
            self._route_points[i], self._route_points[i + 1] = (
                self._route_points[i + 1], self._route_points[i]
            )
            self._refresh_route_list()
            self._route_list.setCurrentRow(i + 1)
            self._update_preview()

    def _delete_point(self) -> None:
        i = self._route_list.currentRow()
        if 0 <= i < len(self._route_points):
            self._route_points.pop(i)
            self._refresh_route_list()
            self._update_preview()

    def _clear_points(self) -> None:
        self._route_points.clear()
        self._refresh_route_list()
        self._update_preview()

    # ── Beregning ─────────────────────────────────────────────────────────────

    def _base_filter(self, caches: list) -> list:
        """Anvend fælles filtre (found, available) — altid aktive."""
        caches = [c for c in caches if not c.found]
        caches = [c for c in caches if c.available and not c.archived]
        return caches

    def _compute_radius(self) -> tuple[list, dict, str]:
        """Beregn caches for radius-fanen."""
        s        = get_settings()
        home_lat = s.home_lat
        home_lon = s.home_lon
        radius   = self._spin_radius.value()
        sort_key = self._combo_sort.currentData()
        max_n    = self._spin_count.value()

        caches = self._base_filter(list(self._all_caches))

        def dist(c):
            if c.latitude is not None and c.longitude is not None:
                return _haversine_km(home_lat, home_lon, c.latitude, c.longitude)
            return float("inf")

        warning = ""
        if radius > 0.0:
            if (not home_lat or not home_lon
                    or (home_lat == 0.0 and home_lon == 0.0)):
                warning = tr("trip_no_center_warning")
            else:
                max_km = radius * 1.60934 if s.use_miles else radius
                caches = [c for c in caches if dist(c) <= max_km]

        if sort_key == "distance":
            caches.sort(key=dist)
        elif sort_key == "difficulty":
            caches.sort(key=lambda c: (c.difficulty or 0))
        elif sort_key == "terrain":
            caches.sort(key=lambda c: (c.terrain or 0))
        elif sort_key == "hidden_date":
            caches.sort(key=lambda c: (c.hidden_date or ""), reverse=True)
        elif sort_key == "name":
            caches.sort(key=lambda c: (c.name or "").lower())

        caches = caches[:max_n]
        dist_map = {id(c): dist(c) for c in caches}
        return caches, dist_map, warning

    def _compute_route(self) -> tuple[list, dict, str]:
        """Beregn caches for rute-fanen."""
        if not self._route_points:
            return [], {}, tr("trip_route_no_points_yet")

        s         = get_settings()
        corridor  = self._spin_corridor.value()
        max_km    = corridor * 1.60934 if s.use_miles else corridor
        max_n     = self._spin_count.value()

        waypoints = [(lat, lon) for _, lat, lon in self._route_points]
        caches = self._base_filter(list(self._all_caches))

        def route_dist(c):
            if c.latitude is not None and c.longitude is not None:
                return _dist_to_route_km(c.latitude, c.longitude, waypoints)
            return float("inf")

        # Korridor-filter
        caches = [c for c in caches if route_dist(c) <= max_km]

        # Sortér i kørerækkefølge langs ruten
        caches.sort(key=lambda c: _position_along_route(
            c.latitude, c.longitude, waypoints
        ) if c.latitude is not None else float("inf"))

        caches = caches[:max_n]
        dist_map = {id(c): route_dist(c) for c in caches}
        return caches, dist_map, ""

    def _update_preview(self) -> None:
        tab = self._tabs.currentIndex()
        if tab == 0:
            caches, dist_map, warning = self._compute_radius()
        else:
            caches, dist_map, warning = self._compute_route()

        self._selected_caches = caches if not warning else []
        self._populate_table(caches, dist_map, warning)

        # Opdater kortvinduet automatisk hvis det er åbent
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        win = getattr(app, "_trip_map_win", None)
        if win is not None and win.isVisible() and self._selected_caches:
            win.update_caches(self._selected_caches)


# ── Kort-preview dialog ───────────────────────────────────────────────────────

class TripMapPreviewDialog(QWidget):
    """
    Ikke-blokerende kortvindue der viser de valgte tur-caches.
    Bruger QWidget med Qt.Window flag — det eneste der fungerer pålideligt
    som selvstændigt top-level vindue med QWebEngineView på X11/Cinnamon.
    Gemmes på QApplication så det lever uafhængigt af Trip Planner dialogen.
    """

    def __init__(self, caches: list):
        # Ingen parent + Qt.Window = ægte selvstændigt vindue
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(tr("trip_map_preview_title"))
        self.setMinimumSize(700, 500)
        self.resize(820, 580)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._caches = caches
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Info-linje
        self._info_lbl = QLabel(tr("trip_map_preview_info", count=len(self._caches)))
        self._info_lbl.setStyleSheet(hint_style(extra="font-style: italic;"))
        layout.addWidget(self._info_lbl)

        # Kort
        from opensak.gui.map_widget import MapWidget
        self._map = MapWidget()
        layout.addWidget(self._map)

        # Luk-knap
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        # Indlæs caches — map_widget loader asynkront når siden er klar
        self._map.load_caches(self._caches)

    def update_caches(self, caches: list) -> None:
        """Opdater kortet med nye caches — kaldes automatisk fra Trip Planner."""
        self._caches = caches
        self._info_lbl.setText(tr("trip_map_preview_info", count=len(caches)))
        self._map.load_caches(caches)
