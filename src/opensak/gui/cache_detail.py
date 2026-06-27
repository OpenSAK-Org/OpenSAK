"""
src/opensak/gui/cache_detail.py — Cache detail panel (right side).
Shows name, type, D/T, description, hints and recent logs.
Supports corrected coordinates (user-solved mystery cache finals).
"""

from __future__ import annotations
import re
import webbrowser
from datetime import datetime
from opensak.utils.constants import LOG_COLOURS
from PySide6.QtCore import Qt, QUrl, Signal, QDate, QLocale, QEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTextBrowser, QTabWidget, QFrame, QSizePolicy,
    QPushButton, QPlainTextEdit
)
from PySide6.QtGui import QFont
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage

from opensak.db.models import Cache
from opensak.lang import tr
from opensak.coords import format_coords
from opensak.gui.settings import get_settings
from opensak.gui.icon_provider import get_cache_type_pixmap_composite
from opensak.utils.types import DateFormat, TEXT_SIZE_MAP, norm_locale_date_fmt
from opensak.hint_detect import split_hint


def _format_date(d: datetime) -> str:
    fmt = get_settings().date_format
    if fmt == DateFormat.DMY:
        return d.strftime("%d.%m.%Y")
    if fmt == DateFormat.MDY:
        return d.strftime("%m/%d/%Y")
    if fmt == DateFormat.YMD:
        return d.strftime("%Y-%m-%d")
    qd = QDate(d.year, d.month, d.day)
    locale_fmt = norm_locale_date_fmt(QLocale.system().dateFormat(QLocale.FormatType.ShortFormat))
    return QLocale.system().toString(qd, locale_fmt)


# issue #219 — geocaching.com logge bruger markdown-links: [linktekst](https://url)
# Disse vises i dag som rå tekst; konverter dem til klikbare <a> tags.
_MD_LINK_RE = re.compile(r'\[([^\[\]]+)\]\((https?://[^\s)]+)\)')


def _convert_markdown_links(text: str) -> str:
    """Konverter markdown-links [tekst](url) i logtekst til klikbare HTML-links."""
    return _MD_LINK_RE.sub(r'<a href="\2">\1</a>', text)


class _DescWebPage(QWebEnginePage):
    """Custom page der åbner links i systemets browser i stedet for i WebEngine."""

    def acceptNavigationRequest(self, url: QUrl | str, nav_type, is_main_frame: bool) -> bool:
        if isinstance(url, str):
            url = QUrl(url)
        # Tillad den første load (about:blank eller data: URL med HTML indhold)
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeTyped:
            return True
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeRedirect:
            return True
        # Alt andet (link-klik) sendes til systemets browser
        if url.scheme() in ("http", "https"):
            webbrowser.open(url.toString())
            return False
        return False


class CacheDetailPanel(QWidget):
    """Displays full details for a single selected cache."""

    corrected_coords_changed = Signal(str)  # gc_code
    waypoints_tab_shown = Signal(str)       # JSON: [{lat, lon, prefix, wp_type, name}, ...]
    waypoints_tab_hidden = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self.clear()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(4)

        # ── Header ────────────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        header_row.setContentsMargins(0, 0, 0, 0)

        self._type_icon_lbl = QLabel()
        self._type_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._type_icon_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        header_row.addWidget(self._type_icon_lbl)

        self._title = QLabel(tr("detail_select_cache"))
        font = QFont()
        font.setPointSize(TEXT_SIZE_MAP[get_settings().text_size]["label"])
        font.setBold(True)
        self._title.setFont(font)
        self._title.setWordWrap(True)
        header_row.addWidget(self._title, 1)

        layout.addLayout(header_row)

        # ── Meta row (GC code | Type | D/T | Container | Country) ────────────
        meta_frame = QFrame()
        meta_frame.setFrameShape(QFrame.Shape.StyledPanel)
        meta_layout = QHBoxLayout(meta_frame)
        meta_layout.setContentsMargins(6, 4, 6, 4)
        meta_layout.setSpacing(16)

        self._gc_code_lbl  = self._meta_label("—")
        self._gc_code_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gc_code_lbl.setToolTip(tr("detail_gc_tooltip"))
        self._gc_code_lbl.mousePressEvent = self._open_on_geocaching  # type: ignore[method-assign]
        self._type_lbl     = self._meta_label("—")
        self._dt_lbl       = self._meta_label("—")
        self._container_lbl = self._meta_label("—")
        self._country_lbl  = self._meta_label("—")
        self._coords_lbl   = self._meta_label("—")
        self._coords_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._coords_lbl.setToolTip(tr("detail_coords_tooltip"))
        self._coords_lbl.mousePressEvent = self._open_in_maps  # type: ignore[method-assign]

        for lbl, caption in [
            (self._gc_code_lbl,   tr("col_gc_code")),
            (self._type_lbl,      tr("col_type")),
            (self._dt_lbl,        tr("detail_dt")),
            (self._container_lbl, tr("col_container")),
            (self._country_lbl,   tr("col_country")),
            (self._coords_lbl,    tr("detail_coords")),
        ]:
            col = QVBoxLayout()
            col.setSpacing(1)
            cap = QLabel(caption)
            cap.setStyleSheet("color: gray; font-size: 10px;")
            col.addWidget(cap)
            col.addWidget(lbl)
            meta_layout.addLayout(col)

        # Koordinatkonverter knap
        self._conv_btn = QPushButton("⇄")
        self._conv_btn.setToolTip(tr("detail_coord_converter_tooltip"))
        self._conv_btn.setMaximumWidth(30)
        self._conv_btn.setMaximumHeight(30)
        self._conv_btn.setEnabled(False)
        self._conv_btn.clicked.connect(self._open_coord_converter)
        meta_layout.addWidget(self._conv_btn)

        meta_layout.addStretch()
        layout.addWidget(meta_frame)

        # ── Corrected coordinates row ─────────────────────────────────────────
        self._corrected_frame = QFrame()
        self._corrected_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._corrected_frame.setStyleSheet(
            "QFrame { background-color: #fff8e1; border: 1px solid #f9a825; border-radius: 4px; }"
        )
        corrected_layout = QHBoxLayout(self._corrected_frame)
        corrected_layout.setContentsMargins(8, 4, 8, 4)
        corrected_layout.setSpacing(8)

        # 📍 ikon + label
        pin_lbl = QLabel("📍")
        pin_lbl.setStyleSheet("border: none; background: transparent;")
        corrected_layout.addWidget(pin_lbl)

        corrected_col = QVBoxLayout()
        corrected_col.setSpacing(1)
        cap_corrected = QLabel(tr("detail_corrected_coords"))
        cap_corrected.setStyleSheet("color: #e65100; font-size: 10px; border: none; background: transparent;")
        self._corrected_lbl = QLabel("—")
        corrected_font = QFont()
        corrected_font.setPointSize(TEXT_SIZE_MAP[get_settings().text_size]["secondary"])
        corrected_font.setBold(True)
        self._corrected_lbl.setFont(corrected_font)
        self._corrected_lbl.setStyleSheet(
            "color: #e65100; text-decoration: underline; border: none; background: transparent;"
        )
        self._corrected_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._corrected_lbl.setToolTip(tr("detail_corrected_tooltip"))
        self._corrected_lbl.mousePressEvent = self._open_corrected_in_maps  # type: ignore[method-assign]
        corrected_col.addWidget(cap_corrected)
        corrected_col.addWidget(self._corrected_lbl)
        corrected_layout.addLayout(corrected_col)

        corrected_layout.addStretch()

        # Rediger knap
        self._edit_corrected_btn = QPushButton(tr("detail_corrected_edit_btn"))
        self._edit_corrected_btn.setToolTip(tr("detail_corrected_edit_tooltip"))
        self._edit_corrected_btn.setMaximumHeight(28)
        self._edit_corrected_btn.clicked.connect(self._edit_corrected_coords)
        corrected_layout.addWidget(self._edit_corrected_btn)

        # Slet knap
        self._clear_corrected_btn = QPushButton("✕")
        self._clear_corrected_btn.setToolTip(tr("detail_corrected_clear_tooltip"))
        self._clear_corrected_btn.setMaximumWidth(28)
        self._clear_corrected_btn.setMaximumHeight(28)
        self._clear_corrected_btn.setStyleSheet("color: #c62828;")
        self._clear_corrected_btn.clicked.connect(self._clear_corrected_coords)
        corrected_layout.addWidget(self._clear_corrected_btn)

        self._corrected_frame.setVisible(False)   # skjult indtil en cache vises
        layout.addWidget(self._corrected_frame)

        # Tilføj-knap til corrected coords (vises når der IKKE er korrigerede koordinater)
        self._add_corrected_row = QHBoxLayout()
        self._add_corrected_btn = QPushButton("📍  " + tr("detail_corrected_add_btn"))
        self._add_corrected_btn.setStyleSheet("color: #e65100; font-size: 10px;")
        self._add_corrected_btn.setFlat(True)
        self._add_corrected_btn.setMaximumHeight(22)
        self._add_corrected_btn.clicked.connect(self._edit_corrected_coords)
        self._add_corrected_btn.setVisible(False)
        self._add_corrected_row.addWidget(self._add_corrected_btn)
        self._add_corrected_row.addStretch()
        layout.addLayout(self._add_corrected_row)

        # ── Placed by / hidden date ───────────────────────────────────────────
        self._placed_lbl = QLabel("")
        self._placed_lbl.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._placed_lbl)

        # ── Tabs: Description | Hint | Logs ───────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        # Beskrivelse — QWebEngineView så eksterne billeder og CJK-fonte virker.
        # _DescWebPage må IKKE have Qt-parent — vi rydder selv op i
        # _cleanup_webengine() der kaldes via QApplication.aboutToQuit.
        # Dette undgår Qt's 'Expect troubles' advarsel ved nedlukning.
        # Under test (OPENSAK_DISABLE_WEBENGINE) bruges en QTextBrowser i stedet,
        # så ingen Chromium startes — setHtml()-API'et er identisk.
        from opensak.gui._headless import webengine_disabled
        self._desc_view: QWebEngineView | QTextBrowser
        if webengine_disabled():
            self._desc_view = QTextBrowser()
            self._desc_page = None
        else:
            self._desc_view = QWebEngineView()
            self._desc_page = _DescWebPage()
            self._desc_view.setPage(self._desc_page)

            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self._cleanup_webengine)
        self._tabs.addTab(self._desc_view, tr("detail_tab_desc"))

        hint_widget = QWidget()
        hint_layout = QVBoxLayout(hint_widget)
        hint_layout.setContentsMargins(0, 4, 0, 0)
        hint_layout.setSpacing(4)

        hint_btn_row = QHBoxLayout()
        self._decode_btn = QPushButton(tr("detail_decode_btn"))
        self._decode_btn.setMaximumWidth(200)
        self._decode_btn.clicked.connect(self._toggle_hint_decode)
        self._hint_decoded = False
        self._hint_plain = ""
        self._hint_cipher = ""
        hint_btn_row.addWidget(self._decode_btn)
        hint_btn_row.addStretch()
        hint_layout.addLayout(hint_btn_row)

        self._hint_browser = QTextBrowser()
        hint_layout.addWidget(self._hint_browser)
        self._tabs.addTab(hint_widget, tr("detail_tab_hint"))

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 4, 0, 0)
        log_layout.setSpacing(4)

        self._log_browser = QTextBrowser()
        self._log_browser.setOpenExternalLinks(True)  # issue #219 — links åbnes i systemets browser
        log_layout.addWidget(self._log_browser)
        self._tabs.addTab(log_widget, tr("detail_tab_logs"))

        wp_widget = QWidget()
        wp_layout = QVBoxLayout(wp_widget)
        wp_layout.setContentsMargins(0, 4, 0, 0)
        self._wp_browser = QTextBrowser()
        wp_layout.addWidget(self._wp_browser)
        self._tabs.addTab(wp_widget, tr("detail_tab_waypoints"))

        note_widget = QWidget()
        note_layout = QVBoxLayout(note_widget)
        note_layout.setContentsMargins(0, 4, 0, 0)
        self._note_editor = QPlainTextEdit()
        self._note_editor.setPlaceholderText(tr("detail_note_placeholder"))
        self._note_editor.installEventFilter(self)
        note_layout.addWidget(self._note_editor)
        self._tabs.addTab(note_widget, tr("detail_tab_notes"))

        layout.addWidget(self._tabs)
        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _meta_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        font = QFont()
        font.setPointSize(TEXT_SIZE_MAP[get_settings().text_size]["secondary"])
        font.setBold(True)
        lbl.setFont(font)
        return lbl

    def _toggle_hint_decode(self) -> None:
        self._hint_decoded = not self._hint_decoded
        if self._hint_decoded:
            shown = self._hint_plain if self._hint_plain else tr("detail_no_hint")
            self._hint_browser.setPlainText(shown)
            self._decode_btn.setText(tr("detail_encode_btn"))
        else:
            shown = self._hint_cipher if self._hint_cipher else tr("detail_no_hint")
            self._hint_browser.setPlainText(shown)
            self._decode_btn.setText(tr("detail_decode_btn"))

    def _format_coords(self, lat: float, lon: float) -> str:
        settings = get_settings()
        fmt = settings.coord_format
        return format_coords(lat, lon, fmt)

    def _open_on_geocaching(self, event) -> None:
        if self._current_gc_code:
            webbrowser.open(f"https://www.geocaching.com/geocache/{self._current_gc_code}")

    def _open_in_maps(self, event) -> None:
        if self._current_lat is None:
            return
        settings = get_settings()
        app = settings.map_provider
        lat, lon = self._current_lat, self._current_lon
        if app == "google":
            webbrowser.open(f"https://www.google.com/maps?q={lat},{lon}")
        else:
            webbrowser.open(f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=15")

    def _open_corrected_in_maps(self, event) -> None:
        if self._corrected_lat is None:
            return
        settings = get_settings()
        app = settings.map_provider
        lat, lon = self._corrected_lat, self._corrected_lon
        if app == "google":
            webbrowser.open(f"https://www.google.com/maps?q={lat},{lon}")
        else:
            webbrowser.open(f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=15")

    def _open_coord_converter(self) -> None:
        if self._current_lat is None:
            return
        from opensak.gui.dialogs.coord_converter_dialog import CoordConverterDialog
        dlg = CoordConverterDialog(self._current_lat, self._current_lon, parent=self)
        dlg.exec()

    def _edit_corrected_coords(self) -> None:
        if not self._current_gc_code:
            return
        from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog
        dlg = CorrectedCoordsDialog(
            gc_code=self._current_gc_code,
            corrected_lat=self._corrected_lat,
            corrected_lon=self._corrected_lon,
            parent=self,
        )
        if dlg.exec():
            lat, lon = dlg.get_coords()
            self._save_corrected_coords(lat, lon)

    def _clear_corrected_coords(self) -> None:
        self._save_corrected_coords(None, None)

    def _save_corrected_coords(self, lat, lon) -> None:
        from opensak.db.database import get_session
        from opensak.db.models import Cache as CacheModel, UserNote
        with get_session() as session:
            cache_row = session.query(CacheModel).filter_by(
                gc_code=self._current_gc_code
            ).first()
            if not cache_row:
                return
            note = cache_row.user_note
            if note is None:
                note = UserNote(cache_id=cache_row.id)
                session.add(note)
            note.corrected_lat = lat
            note.corrected_lon = lon
            note.is_corrected = (lat is not None and lon is not None)

        self._corrected_lat = lat
        self._corrected_lon = lon
        self._update_corrected_ui()
        if self._current_gc_code:
            self.corrected_coords_changed.emit(self._current_gc_code)

    def eventFilter(self, obj, event) -> bool:
        note_editor = getattr(self, "_note_editor", None)
        if note_editor is not None and obj is note_editor and event.type() == QEvent.Type.FocusOut:
            self._save_note()
        return super().eventFilter(obj, event)

    def _save_note(self) -> None:
        if not self._current_gc_code:
            return
        from opensak.db.database import get_session
        from opensak.db.models import Cache as CacheModel, UserNote
        text = self._note_editor.toPlainText().strip() or None
        with get_session() as s:
            cache_row = s.query(CacheModel).filter_by(gc_code=self._current_gc_code).one_or_none()
            if cache_row is None:
                return
            note = cache_row.user_note
            if note is None:
                s.flush()
                note = UserNote(cache_id=cache_row.id)
                s.add(note)
                s.flush()
                cache_row.user_note = note
            note.note = text

    def _update_corrected_ui(self) -> None:
        """Opdater visningen af korrigerede koordinater."""
        has_corrected = self._corrected_lat is not None
        self._corrected_frame.setVisible(has_corrected)
        self._add_corrected_btn.setVisible(
            not has_corrected and self._current_gc_code is not None
        )
        if has_corrected:
            self._corrected_lbl.setText(
                self._format_coords(self._corrected_lat, self._corrected_lon)
            )

    def _apply_ui_sizes(self) -> None:
        """Re-apply text/icon sizes from current settings. Called after settings change."""
        sizes = TEXT_SIZE_MAP[get_settings().text_size]

        # Title (main label)
        font = self._title.font()
        font.setPointSize(sizes["label"])
        font.setBold(True)
        self._title.setFont(font)

        # Metadata labels (GC Code, Type, D/T, Container, Country, Coords)
        for lbl in [self._gc_code_lbl, self._type_lbl, self._dt_lbl,
                    self._container_lbl, self._country_lbl, self._coords_lbl]:
            font = lbl.font()
            font.setPointSize(sizes["secondary"])
            font.setBold(True)
            lbl.setFont(font)

        # Corrected coords label
        font = self._corrected_lbl.font()
        font.setPointSize(sizes["secondary"])
        font.setBold(True)
        self._corrected_lbl.setFont(font)

        # Type icon — re-render at new size if a cache is shown
        icon_size = sizes["detail_icon"]
        cache_type = getattr(self, "_current_cache_type", None)
        if cache_type is not None:
            found = getattr(self, "_current_found", False)
            dnf = getattr(self, "_current_dnf", False)
            pixmap = get_cache_type_pixmap_composite(cache_type, icon_size, found=found, dnf=dnf)
            self._type_icon_lbl.setPixmap(pixmap)
            self._type_icon_lbl.setFixedSize(pixmap.width(), pixmap.height())

        self._title.update()

    def refresh_sizes(self) -> None:
        self._apply_ui_sizes()

    def clear(self) -> None:
        self._current_gc_code: str | None = None
        self._current_lat: float | None = None
        self._current_lon: float | None = None
        self._corrected_lat = None
        self._corrected_lon = None
        self._current_cache_type: str | None = None
        self._current_found: bool = False
        self._current_dnf: bool = False
        self._type_icon_lbl.clear()
        self._coords_lbl.setStyleSheet("")
        self._title.setText(tr("detail_select_cache"))
        self._gc_code_lbl.setText("—")
        self._gc_code_lbl.setStyleSheet("")
        self._type_lbl.setText("—")
        self._dt_lbl.setText("—")
        self._container_lbl.setText("—")
        self._country_lbl.setText("—")
        self._coords_lbl.setText("—")
        self._placed_lbl.setText("")
        self._desc_view.setHtml("")
        self._hint_browser.setPlainText("")
        self._log_browser.setHtml("")
        self._wp_browser.setPlainText("")
        self._note_editor.setPlainText("")
        self._tabs.setTabText(3, tr("detail_tab_waypoints"))
        self._hint_plain = ""
        self._hint_cipher = ""
        self._hint_decoded = False
        self._decode_btn.setText(tr("detail_decode_btn"))
        self._conv_btn.setEnabled(False)
        self._corrected_frame.setVisible(False)
        self._add_corrected_btn.setVisible(False)
        self._current_waypoints: list = []
        self._tabs.setTabVisible(4, False)
        self.waypoints_tab_hidden.emit()

    def show_cache(self, cache: Cache) -> None:
        """Populate the panel with data from *cache*."""
        # Title
        found_mark = " ✓" if cache.found else ""
        archived_mark = tr("detail_archived_mark") if cache.archived else ""
        self._title.setText(f"{cache.name}{found_mark}{archived_mark}")

        # Type icon — store for resize and render at current size
        self._current_cache_type = cache.cache_type
        self._current_found = bool(cache.found)
        self._current_dnf = bool(cache.dnf)
        self._apply_ui_sizes()  # Re-apply sizes (also renders the icon)

        # Meta — GC kode som klikbart link
        gc = cache.gc_code or "—"
        self._gc_code_lbl.setText(gc)
        self._gc_code_lbl.setStyleSheet(
            "color: #1565c0; text-decoration: underline; font-weight: bold;"
            if cache.gc_code else ""
        )
        self._current_gc_code = cache.gc_code
        self._type_lbl.setText(
            (cache.cache_type or "—")
            .replace(" Cache", "")
            .replace("Unknown", "Mystery")
        )
        d = f"{cache.difficulty:.1f}" if cache.difficulty else "?"
        t = f"{cache.terrain:.1f}" if cache.terrain else "?"
        self._dt_lbl.setText(f"{d} / {t}")
        self._container_lbl.setText(cache.container or "—")
        self._country_lbl.setText(
            f"{cache.country or '—'}"
            + (f" / {cache.state}" if cache.state else "")
        )
        if cache.latitude and cache.longitude:
            self._coords_lbl.setText(
                self._format_coords(cache.latitude, cache.longitude)
            )
            self._coords_lbl.setStyleSheet(
                "color: #1565c0; text-decoration: underline; font-weight: bold;"
            )
            self._current_lat = cache.latitude
            self._current_lon = cache.longitude
            self._conv_btn.setEnabled(True)
        else:
            self._coords_lbl.setText("—")
            self._coords_lbl.setStyleSheet("")
            self._current_lat = None
            self._current_lon = None
            self._conv_btn.setEnabled(False)

        # Korrigerede koordinater fra UserNote
        self._corrected_lat = None
        self._corrected_lon = None
        if cache.user_note and cache.user_note.is_corrected:
            self._corrected_lat = cache.user_note.corrected_lat
            self._corrected_lon = cache.user_note.corrected_lon
        self._update_corrected_ui()

        # Placed by / date
        parts = []
        if cache.placed_by:
            parts.append(tr("detail_placed_by", name=cache.placed_by))
        if cache.hidden_date:
            parts.append(tr("detail_hidden_date", date=_format_date(cache.hidden_date)))
        self._placed_lbl.setText("   |   ".join(parts))

        # Description — renderes via QWebEngineView så billeder og CJK-fonte virker
        if cache.long_description:
            if cache.long_desc_html:
                self._desc_view.setHtml(_wrap_html(cache.long_description))
            else:
                self._desc_view.setHtml(_wrap_html(
                    f"<pre style='white-space:pre-wrap;font-family:sans-serif'>"
                    f"{cache.long_description}</pre>"
                ))
        elif cache.short_description:
            if cache.short_desc_html:
                self._desc_view.setHtml(_wrap_html(cache.short_description))
            else:
                self._desc_view.setHtml(_wrap_html(
                    f"<pre style='white-space:pre-wrap;font-family:sans-serif'>"
                    f"{cache.short_description}</pre>"
                ))
        else:
            self._desc_view.setHtml(_wrap_html(
                f"<p style='color:gray'>{tr('detail_no_description')}</p>"
            ))

        # Hint — issue #329: geocaching.com leverer hints i klartekst i
        # moderne PQ'er, men ældre GSAK-eksporter kan stadig indeholde ægte
        # ROT13-kodet tekst. split_hint() gætter hvilken er hvilken og vi
        # viser altid den skjulte udgave som standard (spoiler-beskyttelse).
        self._hint_plain, self._hint_cipher = split_hint(cache.encoded_hints or "")
        self._hint_decoded = False
        self._decode_btn.setText(tr("detail_decode_btn"))
        self._hint_browser.setPlainText(
            self._hint_cipher if self._hint_cipher else tr("detail_no_hint")
        )

        # Personal note
        self._tabs.setTabVisible(4, True)
        self._note_editor.setPlainText(
            (cache.user_note.note or "") if cache.user_note else ""
        )

        # Logs — viser alle (sorteret efter dato, nyeste først)
        self._render_logs(cache)

        # Child waypoints
        self._render_waypoints(cache)

    def _render_logs(self, cache: Cache) -> None:
        logs = sorted(
            cache.logs,
            key=lambda l: l.log_date or 0,
            reverse=True
        )
        self._tabs.setTabText(2, tr("detail_tab_logs_count", count=len(logs)) if logs else tr("detail_tab_logs"))
        self._render_log_html(logs)

    def _render_log_html(self, logs: list) -> None:
        if not logs:
            self._log_browser.setPlainText(tr("detail_no_logs"))
            return

        colours = LOG_COLOURS
        html = []
        for log in logs:
            colour = colours.get(log.log_type, "#555555")
            date_str = _format_date(log.log_date) if log.log_date else "?"
            # issue #218 — ingen trunkering: hele logteksten vises (QTextBrowser scroller selv)
            text = log.text or ""
            # issue #219 — markdown-links [tekst](url) gøres til klikbare <a> tags
            text = _convert_markdown_links(text)
            html.append(
                f'<p><b style="color:{colour}">{log.log_type}</b> '
                f'— {log.finder or "?"} '
                f'<span style="color:gray">({date_str})</span><br>'
                f'{text}</p><hr>'
            )

        self._log_browser.setHtml("".join(html))

    def _render_waypoints(self, cache: Cache) -> None:
        wps = cache.waypoints
        self._current_waypoints = list(wps)
        count = len(wps)
        tab_idx = 3
        self._tabs.setTabText(
            tab_idx,
            tr("detail_tab_waypoints_count", count=count) if count else tr("detail_tab_waypoints"),
        )
        if not wps:
            self._wp_browser.setPlainText(tr("detail_no_waypoints"))
            return
        html = []
        for wp in sorted(wps, key=lambda w: w.prefix or ""):
            if wp.latitude is not None and wp.longitude is not None:
                coords = self._format_coords(wp.latitude, wp.longitude)
            else:
                coords = tr("detail_wp_no_coords")
            name_part = f" — {wp.name}" if wp.name else ""
            html.append(
                f'<p><b>[{wp.prefix}]</b> <b>{wp.wp_type or "?"}</b>{name_part}'
                f'<br><span style="color:gray">{coords}</span></p>'
            )
            if wp.description:
                html.append(f'<p style="margin-top:2px">{wp.description}</p>')
            if wp.comment:
                html.append(f'<p style="color:#555;margin-top:2px"><i>{wp.comment}</i></p>')
            html.append("<hr>")
        self._wp_browser.setHtml("".join(html))
        if self._tabs.currentIndex() == 3:
            self._emit_waypoint_markers()

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 3:
            self._emit_waypoint_markers()
        else:
            self.waypoints_tab_hidden.emit()

    def _emit_waypoint_markers(self) -> None:
        import json
        data = [
            {"lat": wp.latitude, "lon": wp.longitude,
             "prefix": wp.prefix or "", "wp_type": wp.wp_type or "",
             "name": wp.name or ""}
            for wp in self._current_waypoints
            if wp.latitude is not None and wp.longitude is not None
        ]
        self.waypoints_tab_shown.emit(json.dumps(data))

    def _cleanup_webengine(self) -> None:
        """Slet QWebEnginePage før Qt rydder defaultProfile op.
        Kaldes via QApplication.aboutToQuit signalet — skal køre BEFORE
        QWebEngineProfile destrueres for at undgå 'Expect troubles' advarsel."""
        if self._desc_page is None or not isinstance(self._desc_view, QWebEngineView):
            return  # headless/test mode — QTextBrowser, intet at rydde op
        try:
            self._desc_view.setPage(None)  # type: ignore[arg-type]
            self._desc_page.deleteLater()
        except RuntimeError:
            # Widget er allerede slettet af Qt — intet at gøre
            pass


def _wrap_html(body: str) -> str:
    """Pak beskrivelsens HTML ind i et komplet dokument med korrekt charset og font-fallback."""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial,
                 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', sans-serif;
    font-size: 13px;
    margin: 8px;
    padding: 0;
  }}
  img {{ max-width: 100%; height: auto; }}
  a {{ color: #1565c0; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
