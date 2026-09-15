"""
src/opensak/gui/dialogs/settings_dialog.py — Settings dialog.
"""

from __future__ import annotations
from typing import cast
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QCheckBox, QPushButton,
    QDialogButtonBox, QGroupBox, QComboBox,
    QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QTabWidget, QWidget,
    QFrame, QSizePolicy, QSpinBox, QScrollArea, QDoubleSpinBox
)
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from PySide6.QtGui import QPixmap, QFont
from opensak.gui.settings import get_settings, HomePoint
from opensak.gui.dialogs.widgets import DirRow, clamp_dialog_height_to_screen
from opensak.lang import tr, AVAILABLE_LANGUAGES, current_language
from opensak.gui.theme import hint_style
from opensak.coords import FORMATS, format_coords
from opensak.utils.types import CoordFormat, DateFormat, TextSize


# ── Baggrundstråd til OAuth + API-kald ───────────────────────────────────────

class _OAuthWorker(QThread):
    """Kører OAuth flow i baggrunden så GUI ikke fryser."""
    success = Signal(dict)   # token dict
    error   = Signal(str)    # fejlbesked

    def run(self):
        try:
            from opensak.api.geocaching import start_oauth_flow
            token = start_oauth_flow()
            if token:
                self.success.emit(token)
            else:
                self.error.emit(tr("gc_login_no_client_id"))
        except Exception as exc:
            self.error.emit(str(exc))


class _ProfileWorker(QThread):
    """Henter brugerprofil i baggrunden."""
    success = Signal(dict)
    error   = Signal(str)

    def run(self):
        try:
            from opensak.api.geocaching import get_user_profile
            profile = get_user_profile()
            if profile:
                self.success.emit(profile)
            else:
                self.error.emit(tr("gc_profile_error"))
        except Exception as exc:
            self.error.emit(str(exc))


class _ImapTestWorker(QThread):
    """Tester PQ-mailkontoens IMAP-login i baggrunden (issue #443), så
    GUI'en ikke fryser mens forbindelsen forsøges oprettet."""
    success = Signal()
    error   = Signal(str, str)   # (kind: "auth" | "network" | "other", detail)

    def __init__(self, config, password: str, parent=None):
        super().__init__(parent)
        self._config = config
        self._password = password

    def run(self):
        from opensak.email.connection import (
            ImapAuthError, ImapNetworkError, check_connection,
        )
        try:
            check_connection(self._config, self._password)
            self.success.emit()
        except ImapAuthError as exc:
            self.error.emit("auth", str(exc))
        except ImapNetworkError as exc:
            self.error.emit("network", str(exc))
        except Exception as exc:
            self.error.emit("other", str(exc))


# ── Hoved-dialog ──────────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings_dialog_title"))
        self.setMinimumWidth(520)
        # Microsoft Store certification (10.1.2.10 Functionality, 31 Aug
        # 2026, issue #811): on a 2560x1600 display at 200% scaling, this
        # dialog's natural sizeHint (all four tabs' content combined)
        # exceeded the screen's available height, cutting off the bottom
        # of the General tab (the "Geocaching profile" group) with no way
        # to reach it. See clamp_dialog_height_to_screen()'s docstring for
        # the full reasoning — every tab below is now wrapped in a
        # QScrollArea specifically so this cap has somewhere to send the
        # overflow instead of just clipping it.
        clamp_dialog_height_to_screen(self, parent)
        self._oauth_worker        = None
        self._profile_worker      = None
        self._editing_original_name: str | None = None   # Issue #157
        self._setup_ui()
        self._load()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Tab-widget med tre faner
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_general_tab(),   tr("settings_tab_general"))
        self._tabs.addTab(self._build_map_tab(),        tr("settings_tab_map"))
        self._tabs.addTab(self._build_gc_tab(),         tr("settings_tab_geocaching"))
        self._tabs.addTab(self._build_pq_email_tab(),   tr("settings_tab_pq_email"))
        self._tabs.addTab(self._build_advanced_tab(),   tr("settings_tab_advanced"))

        layout.addWidget(self._tabs)

        # Knapper
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── Fane 1: Generelle indstillinger ──────────────────────────────────────

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── Hjemmepunkter ─────────────────────────────────────────────────────
        loc_group = QGroupBox(tr("settings_group_user_locations"))
        loc_layout = QVBoxLayout(loc_group)

        self._points_table = QTableWidget(0, 3)
        self._points_table.setHorizontalHeaderLabels([
            tr("col_name"),
            tr("settings_hp_col_lat"),
            tr("settings_hp_col_lon"),
        ])
        self._points_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._points_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._points_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._points_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._points_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._points_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._points_table.verticalHeader().setVisible(False)
        self._points_table.setShowGrid(False)
        self._points_table.setAlternatingRowColors(True)
        self._points_table.verticalHeader().setDefaultSectionSize(24)
        self._points_table.setMaximumHeight(160)
        self._points_table.itemSelectionChanged.connect(self._on_point_selected)
        loc_layout.addWidget(self._points_table)

        list_btn_row = QHBoxLayout()

        self._btn_edit = QPushButton(tr("edit"))
        self._btn_edit.setEnabled(False)
        self._btn_edit.clicked.connect(self._edit_point)
        list_btn_row.addWidget(self._btn_edit)

        self._btn_delete = QPushButton(tr("delete"))
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._delete_point)
        list_btn_row.addWidget(self._btn_delete)
        list_btn_row.addStretch()
        loc_layout.addLayout(list_btn_row)

        self._home_protected_hint = QLabel(tr("settings_hp_home_protected_hint"))
        self._home_protected_hint.setStyleSheet(hint_style())
        self._home_protected_hint.setVisible(False)
        loc_layout.addWidget(self._home_protected_hint)

        add_group = QGroupBox(tr("settings_hp_add_group"))
        add_layout = QVBoxLayout(add_group)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("settings_hp_name_label")))
        self._new_name = QLineEdit()
        self._new_name.setPlaceholderText(tr("settings_hp_name_placeholder"))
        self._new_name.setMaximumWidth(180)
        name_row.addWidget(self._new_name)
        name_row.addStretch()
        add_layout.addLayout(name_row)

        coord_row = QHBoxLayout()
        coord_row.addWidget(QLabel(tr("settings_hp_coord_label")))
        self._new_coord = QLineEdit()
        self._new_coord.setPlaceholderText(tr("coord_conv_placeholder"))
        coord_row.addWidget(self._new_coord)
        add_layout.addLayout(coord_row)

        self._coord_hint = QLabel("")
        self._coord_hint.setStyleSheet(
            hint_style(extra="padding-left: 2px;")
        )
        add_layout.addWidget(self._coord_hint)
        self._new_coord.textChanged.connect(self._on_coord_changed)

        add_btn_row = QHBoxLayout()
        self._btn_add = QPushButton(tr("settings_hp_add_btn"))
        self._btn_add.clicked.connect(self._add_point)
        add_btn_row.addWidget(self._btn_add)
        add_btn_row.addStretch()
        add_layout.addLayout(add_btn_row)

        loc_layout.addWidget(add_group)
        layout.addWidget(loc_group)

        # ── Visning ───────────────────────────────────────────────────────────
        disp_group = QGroupBox(tr("settings_group_display"))
        disp_layout = QVBoxLayout(disp_group)

        unit_row = QHBoxLayout()
        unit_row.addWidget(QLabel(tr("dist_distance_label")))
        self._unit_combo = QComboBox()
        self._unit_combo.addItem(tr("trip_unit_km"), False)
        self._unit_combo.addItem(tr("trip_unit_mi"), True)
        unit_row.addWidget(self._unit_combo)
        unit_row.addStretch()
        disp_layout.addLayout(unit_row)

        map_row = QHBoxLayout()
        map_row.addWidget(QLabel(tr("settings_map_label")))
        self._map_provider = QComboBox()
        self._map_provider.addItem(tr("settings_map_google"), "google")
        self._map_provider.addItem(tr("settings_map_osm"), "osm")
        map_row.addWidget(self._map_provider)
        map_row.addStretch()
        disp_layout.addLayout(map_row)

        coord_fmt_row = QHBoxLayout()
        coord_fmt_row.addWidget(QLabel(tr("settings_coord_format_label")))
        self._coord_format = QComboBox()
        self._coord_format.addItem("DMM  —  N55 47.250 E012 25.000", CoordFormat.DMM)
        self._coord_format.addItem("DMS  —  N55° 47' 15\" E012° 25' 00\"", CoordFormat.DMS)
        self._coord_format.addItem("DD   —  55.78750, 12.41667", CoordFormat.DD)
        coord_fmt_row.addWidget(self._coord_format)
        coord_fmt_row.addStretch()
        disp_layout.addLayout(coord_fmt_row)

        date_fmt_row = QHBoxLayout()
        date_fmt_row.addWidget(QLabel(tr("settings_date_format_label")))
        self._date_format = QComboBox()
        self._date_format.addItem(tr("settings_date_format_locale"), DateFormat.LOCALE)
        self._date_format.addItem("dd.mm.yyyy", DateFormat.DMY)
        self._date_format.addItem("mm/dd/yyyy", DateFormat.MDY)
        self._date_format.addItem("yyyy-mm-dd", DateFormat.YMD)
        date_fmt_row.addWidget(self._date_format)
        date_fmt_row.addStretch()
        disp_layout.addLayout(date_fmt_row)

        text_size_row = QHBoxLayout()
        text_size_row.addWidget(QLabel(tr("settings_text_size_label")))
        self._text_size = QComboBox()
        self._text_size.addItem(tr("settings_text_size_small"), TextSize.SMALL)
        self._text_size.addItem(tr("settings_text_size_medium"), TextSize.MEDIUM)
        self._text_size.addItem(tr("settings_text_size_large"), TextSize.LARGE)
        text_size_row.addWidget(self._text_size)
        text_size_row.addStretch()
        disp_layout.addLayout(text_size_row)

        # Issue #499: default hints to their decoded (plain-text) state
        # instead of always starting hidden behind the ROT13-style spoiler
        # protection — some users always want to see the hint immediately.
        self._decode_hints_cb = QCheckBox(tr("settings_default_decode_hints_cb"))
        disp_layout.addWidget(self._decode_hints_cb)

        layout.addWidget(disp_group)

        # ── Udseende (tema) ───────────────────────────────────────────────────
        appear_group = QGroupBox(tr("settings_group_appearance"))
        appear_layout = QVBoxLayout(appear_group)

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel(tr("settings_theme_label")))
        self._theme_combo = QComboBox()
        self._theme_combo.addItem(tr("settings_theme_auto"),  "auto")
        self._theme_combo.addItem(tr("settings_theme_light"), "light")
        self._theme_combo.addItem(tr("settings_theme_dark"),  "dark")
        theme_row.addWidget(self._theme_combo)

        self._theme_preview = QLabel()
        self._theme_preview.setFixedSize(16, 16)
        self._theme_preview.setStyleSheet(
            "border-radius: 8px; border: 1px solid palette(mid);"
        )
        theme_row.addWidget(self._theme_preview)
        theme_row.addStretch()
        appear_layout.addLayout(theme_row)

        appear_hint = QLabel(tr("settings_theme_hint"))
        appear_hint.setStyleSheet(hint_style())
        appear_layout.addWidget(appear_hint)

        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        layout.addWidget(appear_group)

        # ── Sprog ─────────────────────────────────────────────────────────────
        lang_group = QGroupBox(tr("settings_group_language"))
        lang_layout = QVBoxLayout(lang_group)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel(tr("settings_language_label")))
        self._lang_combo = QComboBox()
        for code, name in AVAILABLE_LANGUAGES.items():
            self._lang_combo.addItem(name, code)
        lang_row.addWidget(self._lang_combo)
        lang_row.addStretch()
        lang_layout.addLayout(lang_row)

        hint = QLabel(tr("settings_language_hint"))
        hint.setStyleSheet(hint_style())
        lang_layout.addWidget(hint)

        layout.addWidget(lang_group)

        # ── Geocaching brugernavn ─────────────────────────────────────────────
        user_group = QGroupBox(tr("settings_group_user"))
        user_layout = QVBoxLayout(user_group)

        user_row = QHBoxLayout()
        user_row.addWidget(QLabel(tr("settings_gc_username_label")))
        self._gc_username = QLineEdit()
        self._gc_username.setPlaceholderText(tr("settings_gc_username_placeholder"))
        self._gc_username.setMaximumWidth(200)
        user_row.addWidget(self._gc_username)
        user_row.addStretch()
        user_layout.addLayout(user_row)

        hint = QLabel(tr("settings_gc_username_hint"))
        hint.setStyleSheet(hint_style())
        user_layout.addWidget(hint)

        # Home Location
        home_loc_row = QHBoxLayout()
        home_loc_row.addWidget(QLabel(tr("settings_gc_home_location_label")))
        self._gc_home_location = QLineEdit()
        self._gc_home_location.setPlaceholderText(tr("coord_conv_placeholder"))
        home_loc_row.addWidget(self._gc_home_location)
        user_layout.addLayout(home_loc_row)

        self._home_loc_hint = QLabel("")
        self._home_loc_hint.setStyleSheet(hint_style(extra="padding-left: 2px;"))
        user_layout.addWidget(self._home_loc_hint)
        self._gc_home_location.textChanged.connect(self._on_home_loc_changed)

        home_loc_btn_row = QHBoxLayout()
        self._btn_save_home_loc = QPushButton(tr("settings_gc_home_location_save"))
        self._btn_save_home_loc.clicked.connect(self._save_home_location)
        home_loc_btn_row.addWidget(self._btn_save_home_loc)
        home_loc_btn_row.addStretch()
        user_layout.addLayout(home_loc_btn_row)

        layout.addWidget(user_group)

        layout.addStretch()

        # Wrap i QScrollArea så dialogen virker på små skærme (Windows/høj DPI)
        scroll = QScrollArea()
        scroll.setWidget(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    # ── Fane: Kort ────────────────────────────────────────────────────────────

    def _build_map_tab(self) -> QWidget:
        # Issue #638: dedicated tab for map-related settings, split out of
        # General so map-specific settings have a natural shared home
        # instead of accumulating in General. #639 added the second
        # setting below, so a QGroupBox now makes sense (a single-setting
        # group would have just duplicated the tab's own name — see the
        # comment that used to be here, removed once this stopped applying).
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        group = QGroupBox(tr("settings_group_map"))
        group_layout = QVBoxLayout(group)

        self._map_enabled_cb = QCheckBox(tr("settings_map_enabled_cb"))
        group_layout.addWidget(self._map_enabled_cb)

        map_enabled_note = QLabel(tr("settings_map_enabled_note"))
        map_enabled_note.setWordWrap(True)
        map_enabled_note.setStyleSheet(hint_style())
        group_layout.addWidget(map_enabled_note)

        group_layout.addSpacing(8)

        # Issue #639: cap the map to the nearest N caches from home,
        # rather than every filtered result — dramatically faster on large
        # databases (SQL LIMIT push-down, see apply_filters_lightweight()'s
        # push_limit parameter) and arguably more useful in practice too
        # (a map with hundreds of thousands of pins isn't very readable at
        # normal zoom anyway).
        max_caches_row = QHBoxLayout()
        max_caches_row.addWidget(QLabel(tr("settings_map_max_caches_label")))
        self._map_max_caches = QSpinBox()
        self._map_max_caches.setRange(0, 100000)
        self._map_max_caches.setSingleStep(100)
        self._map_max_caches.setSpecialValueText(tr("settings_map_unlimited"))
        self._map_max_caches.setFixedWidth(100)
        max_caches_row.addWidget(self._map_max_caches)
        max_caches_row.addStretch()
        group_layout.addLayout(max_caches_row)

        max_caches_note = QLabel(tr("settings_map_max_caches_note"))
        max_caches_note.setWordWrap(True)
        max_caches_note.setStyleSheet(hint_style())
        group_layout.addWidget(max_caches_note)

        group_layout.addSpacing(8)

        # Issue #718: split-screen map (the single selected cache's map)
        # uses its own radius-bounded query, independent of the overview
        # map_max_caches above — see get_nearby_caches() in filters/engine.py.
        # Saved per database (AppSettings.map_nearby_radius_km /
        # map_nearby_max_caches), same pattern as home_lat/home_lon.
        nearby_radius_row = QHBoxLayout()
        nearby_radius_row.addWidget(QLabel(tr("settings_map_nearby_radius_label")))
        self._map_nearby_radius = QDoubleSpinBox()
        self._map_nearby_radius.setRange(0.0, 1000.0)
        self._map_nearby_radius.setDecimals(1)
        self._map_nearby_radius.setSingleStep(0.5)
        self._map_nearby_radius.setFixedWidth(100)
        nearby_radius_row.addWidget(self._map_nearby_radius)
        nearby_radius_row.addStretch()
        group_layout.addLayout(nearby_radius_row)

        nearby_max_row = QHBoxLayout()
        nearby_max_row.addWidget(QLabel(tr("settings_map_nearby_max_caches_label")))
        self._map_nearby_max_caches = QSpinBox()
        self._map_nearby_max_caches.setRange(1, 100000)
        self._map_nearby_max_caches.setSingleStep(50)
        self._map_nearby_max_caches.setFixedWidth(100)
        nearby_max_row.addWidget(self._map_nearby_max_caches)
        nearby_max_row.addStretch()
        group_layout.addLayout(nearby_max_row)

        nearby_note = QLabel(tr("settings_map_nearby_note"))
        nearby_note.setWordWrap(True)
        nearby_note.setStyleSheet(hint_style())
        group_layout.addWidget(nearby_note)

        layout.addWidget(group)
        layout.addStretch()

        # Same fix as General/Advanced (see #811 comment on __init__ for
        # the full reasoning): certification only caught the General tab
        # specifically, but every tab shares the same dialog-height cap
        # now, so every tab needs a way to scroll if its content doesn't
        # fit — not just the one tab that happened to get tested.
        scroll = QScrollArea()
        scroll.setWidget(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    # ── Fane: Avanceret ───────────────────────────────────────────────────────

    def _build_advanced_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ── Folders (install / database location) ──────────────────────────────
        from opensak.settings_store import get_install_dir, get_db_dir

        folders_group = QGroupBox(tr("settings_group_folders"))
        folders_layout = QVBoxLayout(folders_group)

        folders_layout.addWidget(QLabel(tr("settings_install_dir_label")))
        self._install_dir_row = DirRow(get_install_dir(), browsable=False)
        folders_layout.addWidget(self._install_dir_row)
        install_note = QLabel(tr("settings_install_dir_note"))
        install_note.setWordWrap(True)
        install_note.setStyleSheet(hint_style())
        folders_layout.addWidget(install_note)

        # Issue #358: tidligere lovede teksten ovenfor at man kunne "køre
        # opsætnings-guiden igen", men der var ingen vej til faktisk at gøre
        # det — wizarden blev kun vist automatisk ved allerførste opstart.
        run_wizard_row = QHBoxLayout()
        run_wizard_btn = QPushButton(tr("settings_run_wizard_button"))
        run_wizard_btn.clicked.connect(self._on_run_wizard_again)
        run_wizard_row.addWidget(run_wizard_btn)
        run_wizard_row.addStretch()
        folders_layout.addLayout(run_wizard_row)

        folders_layout.addSpacing(8)
        folders_layout.addWidget(QLabel(tr("settings_db_dir_label")))
        self._db_dir_row = DirRow(get_db_dir())
        folders_layout.addWidget(self._db_dir_row)

        folders_hint = QLabel(tr("settings_folders_restart_hint"))
        folders_hint.setWordWrap(True)
        folders_hint.setStyleSheet(hint_style())
        folders_layout.addWidget(folders_hint)

        # Issue #519: custom icons folder — read-only path (not user-browsable,
        # it's always <install_dir>/icons) plus an "Open folder" button so
        # users can drop in replacement SVGs without knowing the path.
        from opensak.config import get_icons_dir

        folders_layout.addSpacing(8)
        folders_layout.addWidget(QLabel(tr("settings_icons_dir_label")))
        icons_dir_row = QHBoxLayout()
        self._icons_dir_row = DirRow(get_icons_dir(), browsable=False)
        icons_dir_row.addWidget(self._icons_dir_row)
        open_icons_btn = QPushButton(tr("settings_open_icons_folder_button"))
        open_icons_btn.clicked.connect(self._on_open_icons_folder)
        icons_dir_row.addWidget(open_icons_btn)
        folders_layout.addLayout(icons_dir_row)
        icons_note = QLabel(tr("settings_icons_dir_note"))
        icons_note.setWordWrap(True)
        icons_note.setStyleSheet(hint_style())
        folders_layout.addWidget(icons_note)

        icon_guide_row = QHBoxLayout()
        icon_guide_btn = QPushButton(tr("settings_view_icon_guide_button"))
        icon_guide_btn.clicked.connect(self._on_open_icon_guide)
        icon_guide_row.addWidget(icon_guide_btn)
        icon_guide_row.addStretch()
        folders_layout.addLayout(icon_guide_row)

        layout.addWidget(folders_group)

        # ── AppImage (kun synlig når kørende som AppImage — issue #835) ────────
        from opensak import appimage as _appimage_mod

        if _appimage_mod.is_running_as_appimage():
            appimage_group = QGroupBox(tr("settings_group_appimage"))
            appimage_layout = QVBoxLayout(appimage_group)

            is_integrated = _appimage_mod.is_appimage_integrated()
            if is_integrated:
                status_text = tr("settings_appimage_status_integrated")
                button_label = tr("settings_appimage_reinstall_button")
            else:
                status_text = tr("settings_appimage_status_not_integrated")
                button_label = tr("settings_appimage_install_button")

            self._appimage_status_lbl = QLabel(status_text)
            appimage_layout.addWidget(self._appimage_status_lbl)

            appimage_btn_row = QHBoxLayout()
            self._appimage_install_btn = QPushButton(button_label)
            self._appimage_install_btn.clicked.connect(self._on_appimage_install_clicked)
            appimage_btn_row.addWidget(self._appimage_install_btn)

            # Afinstaller kun relevant (og kun vist) når der rent faktisk
            # er noget integreret at fjerne — issue #837.
            self._appimage_uninstall_btn = QPushButton(tr("settings_appimage_uninstall_button"))
            self._appimage_uninstall_btn.clicked.connect(self._on_appimage_uninstall_clicked)
            self._appimage_uninstall_btn.setVisible(is_integrated)
            appimage_btn_row.addWidget(self._appimage_uninstall_btn)

            appimage_btn_row.addStretch()
            appimage_layout.addLayout(appimage_btn_row)

            appimage_hint = QLabel(tr("settings_appimage_hint"))
            appimage_hint.setWordWrap(True)
            appimage_hint.setStyleSheet(hint_style())
            appimage_layout.addWidget(appimage_hint)

            layout.addWidget(appimage_group)

        # ── Search behaviour ──────────────────────────────────────────────────
        search_group = QGroupBox(tr("settings_group_search"))
        search_layout = QVBoxLayout(search_group)

        min_row = QHBoxLayout()
        min_row.addWidget(QLabel(tr("settings_search_min_chars_label")))
        self._search_min_chars = QSpinBox()
        self._search_min_chars.setRange(0, 20)
        self._search_min_chars.setSpecialValueText(tr("settings_search_auto"))
        self._search_min_chars.setFixedWidth(80)
        min_row.addWidget(self._search_min_chars)
        min_row.addStretch()
        search_layout.addLayout(min_row)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel(tr("settings_search_debounce_label")))
        self._search_debounce_ms = QSpinBox()
        self._search_debounce_ms.setRange(0, 2000)
        self._search_debounce_ms.setSingleStep(50)
        self._search_debounce_ms.setSpecialValueText(tr("settings_search_auto"))
        self._search_debounce_ms.setFixedWidth(80)
        delay_row.addWidget(self._search_debounce_ms)
        delay_row.addStretch()
        search_layout.addLayout(delay_row)

        search_hint = QLabel(tr("settings_search_hint"))
        search_hint.setStyleSheet(hint_style())
        search_hint.setWordWrap(True)
        search_layout.addWidget(search_hint)

        layout.addWidget(search_group)

        # ── Location refinement (only shown when reverse-geocoding flag is on) ──
        from opensak.utils import flags
        if flags.reverse_geocoding:
            loc_ref_group = QGroupBox(tr("settings_group_nominatim"))
            loc_ref_layout = QVBoxLayout(loc_ref_group)

            self._nominatim_cb: QCheckBox | None = QCheckBox(tr("settings_nominatim_cb"))
            loc_ref_layout.addWidget(self._nominatim_cb)

            nominatim_hint = QLabel(tr("settings_nominatim_hint"))
            nominatim_hint.setWordWrap(True)
            nominatim_hint.setStyleSheet(hint_style())
            loc_ref_layout.addWidget(nominatim_hint)

            layout.addWidget(loc_ref_group)
        else:
            self._nominatim_cb = None

        # ── Opdateringer ──────────────────────────────────────────────────────
        update_group = QGroupBox(tr("settings_group_updates"))
        update_layout = QVBoxLayout(update_group)

        self._update_check_cb = QCheckBox(tr("settings_update_check_label"))
        update_layout.addWidget(self._update_check_cb)

        # Only meaningful for users running a stable release — a beta user
        # already gets checked against both stable and beta releases
        # automatically (see UpdateCheckWorker). Shown regardless, so the
        # choice is remembered if/when the user is back on stable.
        self._notify_betas_cb = QCheckBox(tr("settings_notify_betas_label"))
        update_layout.addWidget(self._notify_betas_cb)

        layout.addWidget(update_group)

        # ── Distance calculation ───────────────────────────────────────────────
        dist_group = QGroupBox(tr("settings_group_distance"))
        dist_layout = QVBoxLayout(dist_group)

        method_row = QHBoxLayout()
        method_row.addWidget(QLabel(tr("settings_distance_method_label")))
        self._distance_method_combo: QComboBox = QComboBox()
        self._distance_method_combo.addItem(tr("settings_distance_haversine"), "haversine")
        self._distance_method_combo.addItem(tr("settings_distance_vincenty"),  "vincenty")
        method_row.addWidget(self._distance_method_combo)
        method_row.addStretch()
        dist_layout.addLayout(method_row)

        dist_hint = QLabel(tr("settings_distance_hint"))
        dist_hint.setWordWrap(True)
        dist_hint.setStyleSheet(hint_style())
        dist_layout.addWidget(dist_hint)

        layout.addWidget(dist_group)

        layout.addStretch()

        # Issue #805: Advanced has the most stacked group boxes of any tab
        # (Folders, Search, Location refinement, Updates, Distance), and was
        # the only tab besides General not wrapped in a QScrollArea — on
        # small/high-DPI screens the dialog can't grow to fit everything, and
        # Qt squashes the controls instead of letting the user scroll.
        scroll = QScrollArea()
        scroll.setWidget(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    def _on_open_icon_guide(self) -> None:
        """Åbn den bundlede icon-navngivnings-guide i systemets standard browser (issue #519 follow-up)."""
        from opensak.gui.icon_provider import get_icon_guide_path
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_icon_guide_path())))

    def _on_open_icons_folder(self) -> None:
        """Åbn brugerens custom-icons mappe i systemets filhåndtering (issue #519)."""
        from opensak.config import get_icons_dir
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_icons_dir())))

    def _on_run_wizard_again(self) -> None:
        """
        Genåbn velkomst-wizarden manuelt (issue #358).

        Wizarden gemmer selv sine valg direkte i settings_store, uafhængigt
        af denne dialogs egen _save()/accept()-flow. Vi opdaterer derfor kun
        de viste mappe-felter bagefter, og advarer kun om genstart hvis der
        faktisk blev ændret noget der kræver det.
        """
        from opensak.settings_store import get_install_dir, get_db_dir
        from opensak.gui.dialogs.welcome_wizard import WelcomeWizard

        old_install_dir = get_install_dir()
        old_db_dir = get_db_dir()
        old_lang = current_language()

        wizard = WelcomeWizard(self)
        wizard.exec()

        new_install_dir = get_install_dir()
        new_db_dir = get_db_dir()
        self._install_dir_row.set_path(new_install_dir)
        self._db_dir_row.set_path(new_db_dir)

        if new_install_dir != old_install_dir:
            QMessageBox.information(
                self,
                tr("restart_required"),
                tr("settings_run_wizard_restart_notice"),
            )
        elif new_db_dir != old_db_dir:
            QMessageBox.information(
                self,
                tr("restart_required"),
                tr("settings_db_dir_changed_message"),
            )
        elif current_language() != old_lang:
            QMessageBox.information(
                self,
                tr("restart_required"),
                tr("restart_message"),
            )

    def _on_appimage_install_clicked(self) -> None:
        """
        Installér (eller genintallér) OpenSAK i programmenuen manuelt
        (issue #835, §7 punkt 3).

        Nødvendig så "Spørg ikke igen" ved førstegangs-prompten ikke bliver
        en irreversibel fælde — kalder samme integrate_appimage() som
        selve prompten.
        """
        from opensak import appimage as _appimage_mod

        result = _appimage_mod.integrate_appimage()
        if result.success:
            self._appimage_status_lbl.setText(tr("settings_appimage_status_integrated"))
            self._appimage_install_btn.setText(tr("settings_appimage_reinstall_button"))
            self._appimage_uninstall_btn.setVisible(True)
            QMessageBox.information(
                self,
                tr("appimage_integrate_success_title"),
                tr("appimage_integrate_success_msg"),
            )
        else:
            QMessageBox.warning(
                self,
                tr("appimage_integrate_error_title"),
                tr("appimage_integrate_error_msg", error=result.error or ""),
            )

    def _on_appimage_uninstall_clicked(self) -> None:
        """
        Fjern OpenSAK fra programmenuen (og valgfrit alle data) — issue
        #837. Lukker både indstillinger-dialogen og selve applikationen
        ved succes, jf. §4.3 i designdokumentet ("Luk applikationen").
        """
        from opensak.gui.dialogs.appimage_uninstall_dialog import confirm_and_uninstall

        if confirm_and_uninstall(self):
            self.accept()
            from PySide6.QtWidgets import QApplication
            QApplication.quit()

    # ── Fane 2: Geocaching.com ────────────────────────────────────────────────

    def _build_gc_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Status-boks (viser login-status + brugerinfo)
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.Shape.StyledPanel)
        status_frame.setStyleSheet(
            "QFrame { border-radius: 6px; padding: 8px; }"
        )
        status_layout = QVBoxLayout(status_frame)
        status_layout.setSpacing(4)

        # Ikon + navn i samme række
        top_row = QHBoxLayout()

        name_col = QVBoxLayout()
        self._gc_username_label = QLabel(tr("gc_not_logged_in"))
        self._gc_username_label.setFont(QFont("Sans Serif", 11, QFont.Weight.Bold))
        name_col.addWidget(self._gc_username_label)

        self._gc_status_label = QLabel(tr("gc_status_offline"))
        self._gc_status_label.setStyleSheet(hint_style())
        name_col.addWidget(self._gc_status_label)
        name_col.addStretch()

        top_row.addLayout(name_col)
        top_row.addStretch()
        status_layout.addLayout(top_row)

        # Fund-tæller
        self._gc_finds_label = QLabel("")
        self._gc_finds_label.setStyleSheet(hint_style(extra="padding-left: 40px;"))
        status_layout.addWidget(self._gc_finds_label)

        layout.addWidget(status_frame)

        # Knap-række
        btn_row = QHBoxLayout()

        self._gc_login_btn = QPushButton(tr("gc_login_btn"))
        self._gc_login_btn.setMinimumWidth(140)
        self._gc_login_btn.clicked.connect(self._on_gc_login)
        btn_row.addWidget(self._gc_login_btn)

        self._gc_logout_btn = QPushButton(tr("gc_logout_btn"))
        self._gc_logout_btn.setMinimumWidth(100)
        self._gc_logout_btn.clicked.connect(self._on_gc_logout)
        self._gc_logout_btn.setEnabled(False)
        btn_row.addWidget(self._gc_logout_btn)

        self._gc_refresh_btn = QPushButton(tr("gc_refresh_btn"))
        self._gc_refresh_btn.setMinimumWidth(100)
        self._gc_refresh_btn.clicked.connect(self._on_gc_refresh_profile)
        self._gc_refresh_btn.setEnabled(False)
        btn_row.addWidget(self._gc_refresh_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # Forklaring — hvad bruges API'en til
        info_group = QGroupBox(tr("gc_info_group"))
        info_layout = QVBoxLayout(info_group)

        for text_key in [
            "gc_info_favorites",
            "gc_info_trackables",
            "gc_info_finds",
        ]:
            row = QHBoxLayout()
            bullet = QLabel("•")
            bullet.setFixedWidth(14)
            row.addWidget(bullet)
            lbl = QLabel(tr(text_key))
            lbl.setWordWrap(True)
            row.addWidget(lbl)
            info_layout.addLayout(row)

        layout.addWidget(info_group)

        # API-status note (vises kun hvis CLIENT_ID ikke er sat)
        self._gc_api_note = QLabel(tr("gc_api_not_configured"))
        self._gc_api_note.setStyleSheet(
            "color: #b07800; font-size: 10px; padding: 4px;"
            "background: #fff8e1; border-radius: 4px;"
        )
        self._gc_api_note.setWordWrap(True)
        layout.addWidget(self._gc_api_note)

        layout.addStretch()

        # Same fix as General/Advanced (see #811 comment on __init__ for
        # the full reasoning): certification only caught the General tab
        # specifically, but every tab shares the same dialog-height cap
        # now, so every tab needs a way to scroll if its content doesn't
        # fit — not just the one tab that happened to get tested.
        scroll = QScrollArea()
        scroll.setWidget(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    # ── Fane: PQ Email (issue #443) ───────────────────────────────────────────

    def _build_pq_email_tab(self) -> QWidget:
        from opensak.email.connection import DEFAULT_IMAP_SSL_PORT

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        intro = QLabel(tr("pq_email_intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(hint_style())
        layout.addWidget(intro)

        form_group = QGroupBox(tr("pq_email_group_account"))
        form = QFormLayout(form_group)

        self._pq_email_host = QLineEdit()
        self._pq_email_host.setPlaceholderText("imap.example.com")
        form.addRow(tr("pq_email_host_label"), self._pq_email_host)

        self._pq_email_port = QSpinBox()
        self._pq_email_port.setRange(1, 65535)
        self._pq_email_port.setValue(DEFAULT_IMAP_SSL_PORT)
        form.addRow(tr("pq_email_port_label"), self._pq_email_port)

        self._pq_email_ssl_cb = QCheckBox(tr("pq_email_ssl_label"))
        self._pq_email_ssl_cb.setChecked(True)
        self._pq_email_ssl_cb.toggled.connect(self._on_pq_email_ssl_toggled)
        form.addRow("", self._pq_email_ssl_cb)

        self._pq_email_username = QLineEdit()
        form.addRow(tr("pq_email_username_label"), self._pq_email_username)

        self._pq_email_password = QLineEdit()
        self._pq_email_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr("pq_email_password_label"), self._pq_email_password)

        self._pq_email_password_hint = QLabel("")
        self._pq_email_password_hint.setStyleSheet(hint_style())
        self._pq_email_password_hint.setWordWrap(True)
        form.addRow("", self._pq_email_password_hint)

        layout.addWidget(form_group)

        btn_row = QHBoxLayout()
        self._pq_email_test_btn = QPushButton(tr("pq_email_test_btn"))
        self._pq_email_test_btn.clicked.connect(self._on_pq_email_test)
        btn_row.addWidget(self._pq_email_test_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._pq_email_status = QLabel("")
        self._pq_email_status.setWordWrap(True)
        layout.addWidget(self._pq_email_status)

        layout.addStretch()

        # Samme begrundelse som de øvrige faner — se #811-kommentaren i
        # __init__ for den fulde forklaring af hvorfor hver fane skal
        # kunne scrolle uafhængigt af de andre.
        scroll = QScrollArea()
        scroll.setWidget(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        return scroll

    def _on_pq_email_ssl_toggled(self, checked: bool) -> None:
        # Skift standardport automatisk når SSL slås til/fra — men kun
        # hvis feltet stadig står på en af de to standardporte, så vi
        # ikke overskriver et bevidst valgt ikke-standard portnummer.
        from opensak.email.connection import DEFAULT_IMAP_PORT, DEFAULT_IMAP_SSL_PORT
        current = self._pq_email_port.value()
        if current in (DEFAULT_IMAP_SSL_PORT, DEFAULT_IMAP_PORT):
            self._pq_email_port.setValue(
                DEFAULT_IMAP_SSL_PORT if checked else DEFAULT_IMAP_PORT
            )

    def _on_pq_email_test(self) -> None:
        from opensak.email import credentials
        from opensak.email.connection import ImapConfig

        host = self._pq_email_host.text().strip()
        username = self._pq_email_username.text().strip()
        if not host or not username:
            QMessageBox.warning(
                self, tr("pq_email_test_btn"), tr("pq_email_missing_fields")
            )
            return

        password = self._pq_email_password.text()
        if not password:
            # Intet nyt kodeord tastet ind — brug det gemte, hvis der
            # findes ét for det brugernavn der står i feltet nu.
            password = credentials.get_password(username) or ""
        if not password:
            QMessageBox.warning(
                self, tr("pq_email_test_btn"), tr("pq_email_missing_password")
            )
            return

        config = ImapConfig(
            host=host,
            port=self._pq_email_port.value(),
            use_ssl=self._pq_email_ssl_cb.isChecked(),
            username=username,
        )

        self._pq_email_test_btn.setEnabled(False)
        self._pq_email_status.setText(tr("pq_email_testing"))

        self._pq_email_worker = _ImapTestWorker(config, password, self)
        self._pq_email_worker.success.connect(self._on_pq_email_test_success)
        self._pq_email_worker.error.connect(self._on_pq_email_test_error)
        self._pq_email_worker.start()

    def _on_pq_email_test_success(self) -> None:
        self._pq_email_test_btn.setEnabled(True)
        self._pq_email_status.setText(tr("pq_email_test_success"))

    def _on_pq_email_test_error(self, kind: str, detail: str) -> None:
        self._pq_email_test_btn.setEnabled(True)
        if kind == "auth":
            msg = tr("pq_email_test_error_auth", detail=detail)
        elif kind == "network":
            msg = tr("pq_email_test_error_network", detail=detail)
        else:
            msg = tr("pq_email_test_error_other", detail=detail)
        self._pq_email_status.setText(msg)

    # ── GC login/logout ───────────────────────────────────────────────────────

    def _on_gc_login(self) -> None:
        """Start OAuth flow i baggrundstråd."""
        from opensak.api.geocaching import GC_CLIENT_ID
        if not GC_CLIENT_ID:
            QMessageBox.information(
                self,
                tr("gc_login_unavailable_title"),
                tr("gc_login_unavailable_msg"),
            )
            return

        self._gc_login_btn.setEnabled(False)
        self._gc_login_btn.setText(tr("gc_login_waiting"))
        self._gc_status_label.setText(tr("gc_status_waiting"))

        self._oauth_worker = _OAuthWorker(self)
        self._oauth_worker.success.connect(self._on_gc_login_success)
        self._oauth_worker.error.connect(self._on_gc_login_error)
        self._oauth_worker.start()

    def _on_gc_login_success(self, token: dict) -> None:
        self._gc_login_btn.setText(tr("gc_login_btn"))
        self._gc_login_btn.setEnabled(False)
        self._gc_logout_btn.setEnabled(True)
        self._gc_refresh_btn.setEnabled(True)
        self._gc_status_label.setText(tr("gc_status_online"))
        self._gc_status_label.setStyleSheet("color: #2e7d32; font-size: 10px;")
        # Hent profil med det samme
        self._on_gc_refresh_profile()

    def _on_gc_login_error(self, msg: str) -> None:
        self._gc_login_btn.setEnabled(True)
        self._gc_login_btn.setText(tr("gc_login_btn"))
        self._gc_status_label.setText(tr("gc_status_offline"))
        QMessageBox.warning(self, tr("gc_login_error_title"), msg)

    def _on_gc_logout(self) -> None:
        reply = QMessageBox.question(
            self,
            tr("gc_logout_btn"),
            tr("gc_logout_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from opensak.api.geocaching import logout
            logout()
            self._update_gc_ui_logged_out()

    def _on_gc_refresh_profile(self) -> None:
        self._gc_refresh_btn.setEnabled(False)
        self._gc_status_label.setText(tr("gc_status_fetching"))

        self._profile_worker = _ProfileWorker(self)
        self._profile_worker.success.connect(self._on_profile_loaded)
        self._profile_worker.error.connect(self._on_profile_error)
        self._profile_worker.start()

    def _on_profile_loaded(self, profile: dict) -> None:
        username  = profile.get("username", "?")
        finds     = profile.get("findCount", "?")
        self._gc_username_label.setText(username)
        self._gc_finds_label.setText(tr("gc_find_count", count=finds))
        self._gc_status_label.setText(tr("gc_status_online"))
        self._gc_status_label.setStyleSheet("color: #2e7d32; font-size: 10px;")
        self._gc_refresh_btn.setEnabled(True)
        self._gc_login_btn.setEnabled(False)
        self._gc_logout_btn.setEnabled(True)

    def _on_profile_error(self, msg: str) -> None:
        self._gc_status_label.setText(tr("gc_status_error"))
        self._gc_status_label.setStyleSheet("color: #c62828; font-size: 10px;")
        self._gc_refresh_btn.setEnabled(True)

    def _update_gc_ui_logged_out(self) -> None:
        self._gc_username_label.setText(tr("gc_not_logged_in"))
        self._gc_finds_label.setText("")
        self._gc_status_label.setText(tr("gc_status_offline"))
        self._gc_status_label.setStyleSheet(hint_style())
        self._gc_login_btn.setEnabled(True)
        self._gc_logout_btn.setEnabled(False)
        self._gc_refresh_btn.setEnabled(False)

    def _refresh_gc_status_on_open(self) -> None:
        """Tjek login-status når dialogen åbnes."""
        from opensak.api.geocaching import is_logged_in, GC_CLIENT_ID

        # Skjul API-note hvis CLIENT_ID er sat
        self._gc_api_note.setVisible(not bool(GC_CLIENT_ID))

        if is_logged_in():
            self._gc_login_btn.setEnabled(False)
            self._gc_logout_btn.setEnabled(True)
            self._gc_refresh_btn.setEnabled(True)
            self._gc_status_label.setText(tr("gc_status_fetching"))
            self._on_gc_refresh_profile()
        else:
            self._update_gc_ui_logged_out()

    # ── Hjemmepunkter — hjælpefunktioner ──────────────────────────────────────

    def _reload_points_table(self) -> None:
        s = get_settings()
        points = s.home_points
        active = s.active_home_name
        fmt = s.coord_format
        self._points_table.setRowCount(len(points))
        for row, p in enumerate(points):
            is_star_home = p.name == "★ Home"
            label = f"★  {p.name}" if p.name == active else p.name
            if is_star_home:
                label = "★ Home" if p.name != active else "★ Home  ★"
            name_item = QTableWidgetItem(label)
            if p.name == active:
                name_item.setForeground(Qt.GlobalColor.darkGreen)
            if is_star_home:
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)

            coords_str = format_coords(p.lat, p.lon, fmt)
            if "," in coords_str:
                halves = coords_str.split(",")
                lat_str = halves[0].strip()
                lon_str = halves[1].strip() if len(halves) > 1 else ""
            else:
                parts = coords_str.split()
                mid = len(parts) // 2
                lat_str = " ".join(parts[:mid])
                lon_str = " ".join(parts[mid:])

            self._points_table.setItem(row, 0, name_item)
            self._points_table.setItem(row, 1, QTableWidgetItem(lat_str))
            self._points_table.setItem(row, 2, QTableWidgetItem(lon_str))

        self._btn_edit.setEnabled(False)
        self._btn_delete.setEnabled(False)
        self._home_protected_hint.setVisible(False)

    def _selected_point(self) -> HomePoint | None:
        row = self._points_table.currentRow()
        points = get_settings().home_points
        if 0 <= row < len(points):
            return points[row]
        return None

    def _on_point_selected(self) -> None:
        has = self._points_table.currentRow() >= 0 and bool(
            self._points_table.selectedItems()
        )
        p = self._selected_point() if has else None
        is_home = p is not None and p.name == "★ Home"

        # ★ Home kan ikke redigeres eller slettes herfra
        self._btn_edit.setEnabled(has and not is_home)
        self._btn_delete.setEnabled(has and not is_home)
        self._home_protected_hint.setVisible(is_home)

    def _delete_point(self) -> None:
        p = self._selected_point()
        if not p:
            return
        if p.name == "★ Home":
            return  # kan ikke slettes herfra
        reply = QMessageBox.question(
            self,
            tr("settings_hp_delete_title"),
            tr("settings_hp_delete_msg", name=p.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            get_settings().remove_home_point(p.name)
            self._reload_points_table()

    def _edit_point(self) -> None:
        p = self._selected_point()
        if not p:
            return
        if p.name == "★ Home":
            return  # redigeres under Geocaching profil
        fmt = get_settings().coord_format
        self._editing_original_name = p.name   # Issue #157: gem originalt navn
        self._new_name.setText(p.name)
        self._new_coord.setText(format_coords(p.lat, p.lon, fmt))
        self._btn_add.setText(tr("save"))       # Issue #157: vis at vi redigerer
        self._new_name.setFocus()

    def _on_home_loc_changed(self, text: str) -> None:
        if not text.strip():
            self._home_loc_hint.setText("")
            return
        try:
            from opensak.coords import parse_coords
            coord = parse_coords(text)
            if coord is None:
                raise ValueError
            lat, lon = coord
            fmt = get_settings().coord_format
            self._home_loc_hint.setText(f"✓  {format_coords(lat, lon, fmt)}")
            self._home_loc_hint.setStyleSheet(
                "color: #2e7d32; font-size: 10px; padding-left: 2px;"
            )
        except Exception:
            self._home_loc_hint.setText(tr("settings_hp_coord_error"))
            self._home_loc_hint.setStyleSheet(
                "color: #c62828; font-size: 10px; padding-left: 2px;"
            )

    def _save_home_location(self) -> None:
        """Gem Home Location fra Geocaching profil-feltet."""
        text = self._gc_home_location.text().strip()
        if not text:
            get_settings().gc_home_location = ""
            get_settings().sync()
            self._reload_points_table()
            return
        from opensak.coords import parse_coords
        if parse_coords(text) is None:  # returns None on failure, never raises
            from opensak.gui.icon import OpenSAKMessageBox
            OpenSAKMessageBox.warning(self, tr("warning"), tr("settings_hp_coord_invalid"))
            return
        get_settings().gc_home_location = text
        get_settings().sync()
        self._reload_points_table()

    def _on_theme_changed(self, _index: int = 0) -> None:
        """Opdater preview-farve og anvend tema live som forhåndsvisning."""
        theme = self._theme_combo.currentData()
        from opensak.gui.theme import effective_theme
        resolved = effective_theme(theme)
        color = "#1E1E1E" if resolved == "dark" else "#F5F5F5"
        self._theme_preview.setStyleSheet(
            f"border-radius: 8px; border: 1px solid palette(mid);"
            f"background-color: {color};"
        )

    def _on_coord_changed(self, text: str) -> None:
        if not text.strip():
            self._coord_hint.setText("")
            return
        try:
            from opensak.coords import parse_coords
            coord = parse_coords(text)
            if coord is None:
                raise ValueError
            lat, lon = coord
            fmt = get_settings().coord_format
            self._coord_hint.setText(f"✓  {format_coords(lat, lon, fmt)}")
            self._coord_hint.setStyleSheet(
                "color: #2e7d32; font-size: 10px; padding-left: 2px;"
            )
        except Exception:
            self._coord_hint.setText(tr("settings_hp_coord_error"))
            self._coord_hint.setStyleSheet(
                "color: #c62828; font-size: 10px; padding-left: 2px;"
            )

    def _add_point(self) -> None:
        name = self._new_name.text().strip()
        coord_text = self._new_coord.text().strip()

        if not name:
            QMessageBox.warning(self, tr("warning"), tr("settings_hp_name_required"))
            return
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

        s = get_settings()
        point = HomePoint(name, lat, lon)

        # Issue #157: hvis vi redigerer og har ændret navn, fjern det gamle punkt
        if self._editing_original_name and self._editing_original_name != name:
            was_active = s.active_home_name == self._editing_original_name
            s.remove_home_point(self._editing_original_name)
            if was_active:
                s.set_active_home(point)

        s.add_or_update_home_point(point)
        s.sync()

        is_active = (s.active_home_name == name
                     or s.active_home_name == self._editing_original_name)
        if len(s.home_points) == 1 or not s.active_home_name or is_active:
            s.set_active_home(point)

        self._editing_original_name = None
        self._btn_add.setText(tr("settings_hp_add_btn"))   # Issue #157: nulstil knaptekst
        self._new_name.clear()
        self._new_coord.clear()
        self._coord_hint.setText("")
        self._reload_points_table()

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        s = get_settings()
        self._reload_points_table()
        from opensak.settings_store import get_install_dir, get_db_dir
        self._install_dir_row.set_path(get_install_dir())
        self._db_dir_row.set_path(get_db_dir())
        idx = self._unit_combo.findData(s.use_miles)
        self._unit_combo.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self._map_provider.findData(s.map_provider)
        self._map_provider.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self._coord_format.findData(s.coord_format)
        self._coord_format.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self._date_format.findData(s.date_format)
        self._date_format.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self._text_size.findData(s.text_size)
        self._text_size.setCurrentIndex(idx if idx >= 0 else 0)
        self._decode_hints_cb.setChecked(s.default_decode_hints)
        self._map_enabled_cb.setChecked(s.map_enabled)
        self._map_max_caches.setValue(s.map_max_caches)
        # Issue #718 — stored value is always km; display in the user's
        # configured unit, same conversion used by filter_dialog.py's
        # DistanceFilter radius field.
        if s.use_miles:
            self._map_nearby_radius.setSuffix(" mi")
            self._map_nearby_radius.setValue(s.map_nearby_radius_km * 0.621371)
        else:
            self._map_nearby_radius.setSuffix(" km")
            self._map_nearby_radius.setValue(s.map_nearby_radius_km)
        self._map_nearby_max_caches.setValue(s.map_nearby_max_caches)
        lang_idx = self._lang_combo.findData(current_language())
        self._lang_combo.setCurrentIndex(lang_idx if lang_idx >= 0 else 0)
        theme_idx = self._theme_combo.findData(s.theme)
        self._theme_combo.setCurrentIndex(theme_idx if theme_idx >= 0 else 0)
        self._on_theme_changed()  # opdater preview-farve
        self._gc_username.setText(s.gc_username)
        self._gc_home_location.setText(s.gc_home_location)
        self._pq_email_host.setText(s.pq_email_host)
        self._pq_email_port.setValue(s.pq_email_port)
        self._pq_email_ssl_cb.setChecked(s.pq_email_use_ssl)
        self._pq_email_username.setText(s.pq_email_username)
        self._pq_email_password.clear()
        from opensak.email import credentials
        if s.pq_email_username and credentials.get_password(s.pq_email_username):
            self._pq_email_password_hint.setText(tr("pq_email_password_saved_hint"))
        else:
            self._pq_email_password_hint.setText("")
        self._pq_email_status.setText("")
        self._search_min_chars.setValue(s.search_min_chars)
        self._search_debounce_ms.setValue(s.search_debounce_ms)
        if self._nominatim_cb is not None:
            self._nominatim_cb.setChecked(s.nominatim_enabled)
        self._update_check_cb.setChecked(s.updates_check_enabled)
        self._notify_betas_cb.setChecked(s.notify_about_betas)
        idx = self._distance_method_combo.findData(s.distance_method)
        self._distance_method_combo.setCurrentIndex(idx if idx >= 0 else 0)
        # Opdater GC-status
        self._refresh_gc_status_on_open()

    def _save(self) -> None:
        # Auto-commit any in-progress home-point edit when the user clicks OK
        if self._editing_original_name is not None:
            self._add_point()
            if self._editing_original_name is not None:
                return  # validation failed — keep dialog open

        s = get_settings()
        s.gc_username       = self._gc_username.text()
        # Home Location gemmes via knappen, men vi gemmer også ved OK
        # (hvis brugeren har tastet uden at trykke Gem)
        home_text = self._gc_home_location.text().strip()
        if not home_text:
            s.gc_home_location = ""
        else:
            from opensak.coords import parse_coords
            if parse_coords(home_text) is not None:  # keep existing if invalid
                s.gc_home_location = home_text
        s.use_miles         = bool(self._unit_combo.currentData())
        s.map_provider      = self._map_provider.currentData()
        s.coord_format      = self._coord_format.currentData()
        s.date_format       = self._date_format.currentData()
        s.text_size         = self._text_size.currentData()
        s.default_decode_hints = self._decode_hints_cb.isChecked()
        s.map_enabled = self._map_enabled_cb.isChecked()
        s.map_max_caches = self._map_max_caches.value()
        # Issue #718 — convert back to km for storage if the field was
        # shown in miles (unit combo already saved above via s.use_miles).
        radius_val = self._map_nearby_radius.value()
        s.map_nearby_radius_km = (
            radius_val * 1.60934 if s.use_miles else radius_val
        )
        s.map_nearby_max_caches = self._map_nearby_max_caches.value()
        s.search_min_chars  = self._search_min_chars.value()
        s.search_debounce_ms = self._search_debounce_ms.value()
        new_theme = self._theme_combo.currentData()
        if new_theme != s.theme:
            s.theme = new_theme
            # Anvend nyt tema øjeblikkeligt — ingen genstart nødvendig
            from PySide6.QtWidgets import QApplication
            from opensak.gui.theme import apply_theme
            app = QApplication.instance()
            if app is not None:
                apply_theme(cast(QApplication, app), new_theme)
        if self._nominatim_cb is not None:
            s.nominatim_enabled = self._nominatim_cb.isChecked()
        s.updates_check_enabled = self._update_check_cb.isChecked()
        s.notify_about_betas = self._notify_betas_cb.isChecked()
        s.distance_method = self._distance_method_combo.currentData()

        # PQ Email (issue #443) — kodeordet gemmes kun i OS keyring
        # (opensak.email.credentials), aldrig i opensak.json. Et tomt
        # kodeord-felt betyder "behold det eksisterende kodeord".
        from opensak.email import credentials
        s.pq_email_host = self._pq_email_host.text()
        s.pq_email_port = self._pq_email_port.value()
        s.pq_email_use_ssl = self._pq_email_ssl_cb.isChecked()
        s.pq_email_username = self._pq_email_username.text()
        new_pq_password = self._pq_email_password.text()
        if new_pq_password and s.pq_email_username:
            credentials.set_password(s.pq_email_username, new_pq_password)

        s.sync()

        # Database-mappe — kun gem og advar hvis brugeren faktisk har ændret den
        from opensak.settings_store import get_db_dir, get_store
        new_db_dir = self._db_dir_row.path
        if new_db_dir != get_db_dir():
            from opensak.db.manager import get_db_manager
            manager = get_db_manager()
            # Issue #609: tæl kun databaser der rent faktisk har en fysisk
            # fil på disk — se samme rettelse i welcome_wizard.py.
            existing_count = sum(1 for db in manager.databases if db.exists)

            if existing_count > 0:
                move_box = QMessageBox(self)
                move_box.setWindowTitle(tr("settings_move_databases_title"))
                move_box.setText(
                    tr("settings_move_databases_msg", count=existing_count)
                )
                move_box.setIcon(QMessageBox.Icon.Question)
                btn_move_keep = move_box.addButton(
                    tr("settings_move_keep_originals"), QMessageBox.ButtonRole.AcceptRole
                )
                btn_move_delete = move_box.addButton(
                    tr("settings_move_delete_originals"), QMessageBox.ButtonRole.DestructiveRole
                )
                btn_no_move = move_box.addButton(
                    tr("settings_move_skip"), QMessageBox.ButtonRole.RejectRole
                )
                move_box.exec()
                clicked = move_box.clickedButton()

                if clicked in (btn_move_keep, btn_move_delete):
                    delete_originals = clicked == btn_move_delete
                    errors = manager.move_databases_to(new_db_dir, delete_originals)
                    if errors:
                        QMessageBox.warning(
                            self,
                            tr("settings_move_errors_title"),
                            "\n".join(errors),
                        )
                # btn_no_move: gemmer kun stien — eksisterende databaser
                # forbliver hvor de er, kun nye oprettes i den nye mappe.

            new_db_dir.mkdir(parents=True, exist_ok=True)
            get_store().set("databases.dir", str(new_db_dir))
            QMessageBox.information(
                self,
                tr("restart_required"),
                tr("settings_db_dir_changed_message"),
            )

        new_lang = self._lang_combo.currentData()
        if new_lang != current_language():
            from opensak.config import set_language
            set_language(new_lang)
            QMessageBox.information(
                self,
                tr("restart_required"),
                tr("restart_message"),
            )

        self.accept()
