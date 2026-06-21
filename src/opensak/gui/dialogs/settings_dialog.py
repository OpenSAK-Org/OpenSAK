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
    QFrame, QSizePolicy, QSpinBox, QScrollArea
)
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from PySide6.QtGui import QPixmap, QFont
from opensak.gui.settings import get_settings, HomePoint
from opensak.gui.dialogs.widgets import DirRow
from opensak.lang import tr, AVAILABLE_LANGUAGES, current_language
from opensak.coords import FORMATS, format_coords
from opensak.utils.types import CoordFormat, DateFormat


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


# ── Hoved-dialog ──────────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings_dialog_title"))
        self.setMinimumWidth(520)
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
        self._tabs.addTab(self._build_gc_tab(),         tr("settings_tab_geocaching"))
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
        self._home_protected_hint.setStyleSheet("color: gray; font-size: 10px;")
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
            "color: gray; font-size: 10px; padding-left: 2px;"
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

        self._miles_cb = QCheckBox(tr("settings_use_miles"))
        disp_layout.addWidget(self._miles_cb)

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
        appear_hint.setStyleSheet("color: gray; font-size: 10px;")
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
        hint.setStyleSheet("color: gray; font-size: 10px;")
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
        hint.setStyleSheet("color: gray; font-size: 10px;")
        user_layout.addWidget(hint)

        # Home Location
        home_loc_row = QHBoxLayout()
        home_loc_row.addWidget(QLabel(tr("settings_gc_home_location_label")))
        self._gc_home_location = QLineEdit()
        self._gc_home_location.setPlaceholderText(tr("coord_conv_placeholder"))
        home_loc_row.addWidget(self._gc_home_location)
        user_layout.addLayout(home_loc_row)

        self._home_loc_hint = QLabel("")
        self._home_loc_hint.setStyleSheet("color: gray; font-size: 10px; padding-left: 2px;")
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

    # ── Fane 3: Advanced ─────────────────────────────────────────────────────

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
        install_note.setStyleSheet("color: gray; font-size: 10px;")
        folders_layout.addWidget(install_note)

        folders_layout.addSpacing(8)
        folders_layout.addWidget(QLabel(tr("settings_db_dir_label")))
        self._db_dir_row = DirRow(get_db_dir())
        folders_layout.addWidget(self._db_dir_row)

        folders_hint = QLabel(tr("settings_folders_restart_hint"))
        folders_hint.setWordWrap(True)
        folders_hint.setStyleSheet("color: gray; font-size: 10px;")
        folders_layout.addWidget(folders_hint)

        layout.addWidget(folders_group)

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
        search_hint.setStyleSheet("color: gray; font-size: 10px;")
        search_hint.setWordWrap(True)
        search_layout.addWidget(search_hint)

        layout.addWidget(search_group)

        # ── Location refinement (only shown when update-location flag is on) ──
        from opensak.utils import flags
        if flags.update_location:
            loc_ref_group = QGroupBox(tr("settings_group_nominatim"))
            loc_ref_layout = QVBoxLayout(loc_ref_group)

            self._nominatim_cb: QCheckBox | None = QCheckBox(tr("settings_nominatim_cb"))
            loc_ref_layout.addWidget(self._nominatim_cb)

            nominatim_hint = QLabel(tr("settings_nominatim_hint"))
            nominatim_hint.setWordWrap(True)
            nominatim_hint.setStyleSheet("color: gray; font-size: 10px;")
            loc_ref_layout.addWidget(nominatim_hint)

            layout.addWidget(loc_ref_group)
        else:
            self._nominatim_cb = None

        # ── Opdateringer ──────────────────────────────────────────────────────
        update_group = QGroupBox(tr("settings_group_updates"))
        update_layout = QVBoxLayout(update_group)

        self._update_check_cb = QCheckBox(tr("settings_update_check_label"))
        update_layout.addWidget(self._update_check_cb)

        layout.addWidget(update_group)

        layout.addStretch()
        return tab

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
        self._gc_status_label.setStyleSheet("color: gray; font-size: 10px;")
        name_col.addWidget(self._gc_status_label)
        name_col.addStretch()

        top_row.addLayout(name_col)
        top_row.addStretch()
        status_layout.addLayout(top_row)

        # Fund-tæller
        self._gc_finds_label = QLabel("")
        self._gc_finds_label.setStyleSheet("color: gray; font-size: 10px; padding-left: 40px;")
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
        return tab

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
        self._gc_status_label.setStyleSheet("color: gray; font-size: 10px;")
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
        self._miles_cb.setChecked(s.use_miles)
        idx = self._map_provider.findData(s.map_provider)
        self._map_provider.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self._coord_format.findData(s.coord_format)
        self._coord_format.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self._date_format.findData(s.date_format)
        self._date_format.setCurrentIndex(idx if idx >= 0 else 0)
        lang_idx = self._lang_combo.findData(current_language())
        self._lang_combo.setCurrentIndex(lang_idx if lang_idx >= 0 else 0)
        theme_idx = self._theme_combo.findData(s.theme)
        self._theme_combo.setCurrentIndex(theme_idx if theme_idx >= 0 else 0)
        self._on_theme_changed()  # opdater preview-farve
        self._gc_username.setText(s.gc_username)
        self._gc_home_location.setText(s.gc_home_location)
        self._search_min_chars.setValue(s.search_min_chars)
        self._search_debounce_ms.setValue(s.search_debounce_ms)
        if self._nominatim_cb is not None:
            self._nominatim_cb.setChecked(s.nominatim_enabled)
        self._update_check_cb.setChecked(s.updates_check_enabled)
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
        s.use_miles         = self._miles_cb.isChecked()
        s.map_provider      = self._map_provider.currentData()
        s.coord_format      = self._coord_format.currentData()
        s.date_format       = self._date_format.currentData()
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
        s.sync()

        # Database-mappe — kun gem og advar hvis brugeren faktisk har ændret den
        from opensak.settings_store import get_db_dir, get_store
        new_db_dir = self._db_dir_row.path
        if new_db_dir != get_db_dir():
            from opensak.db.manager import get_db_manager
            manager = get_db_manager()
            existing_count = len(manager.databases)

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
