"""
src/opensak/gui/cache_table.py — Sortable cache list table widget.
Understøtter dynamiske kolonner valgt af brugeren.
"""

from __future__ import annotations
import webbrowser
from typing import Optional
from datetime import datetime

from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QPersistentModelIndex, Signal, QPoint, QLocale, QDate
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QTableView, QHeaderView, QAbstractItemView, QMenu, QApplication

from opensak.db.models import Cache
from opensak.filters.engine import _haversine_km, haversine_km_batch
from opensak.gui.settings import get_settings
from opensak.coords import format_coords, format_lat, format_lon, format_lat, format_lon
from opensak.lang import tr
from opensak.utils.types import DateFormat, GcCode
from opensak.utils.utils import normalize_geocacher_name
from opensak.gui.icon_provider import get_cache_type_icon, get_cache_size_icon
from opensak.gui.dialogs.column_dialog import get_column_widths, set_column_widths
import math


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Beregn azimut (kompasretning) fra punkt 1 til punkt 2 i grader (0-360)."""
    r = math.pi / 180
    dlon = (lon2 - lon1) * r
    la1, la2 = lat1 * r, lat2 * r
    x = math.sin(dlon) * math.cos(la2)
    y = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _bearing_deg_batch(lat0: float, lon0: float, lats, lons):
    """Vectorised bearing (deg 0-360) from (lat0, lon0) to each (lats[i], lons[i]).

    numpy-accelerated counterpart of _bearing_deg, used to compute every cache's
    bearing in one array op on table refresh. Falls back to a Python loop when
    numpy is unavailable. Returns a numpy array or a list of floats.
    """
    try:
        import numpy as np
    except ImportError:
        return [_bearing_deg(lat0, lon0, la, lo) for la, lo in zip(lats, lons)]

    r = math.pi / 180
    la0 = lat0 * r
    la = np.asarray(lats, dtype=float) * r
    dlon = (np.asarray(lons, dtype=float) - lon0) * r
    x = np.sin(dlon) * np.cos(la)
    y = math.cos(la0) * np.sin(la) - math.sin(la0) * np.cos(la) * np.cos(dlon)
    return (np.degrees(np.arctan2(x, y)) + 360) % 360


def _bearing_compass(deg: float) -> str:
    dirs = tr("bearing_dirs").split()
    idx = round(deg / 45) % 8
    return f"{dirs[idx]} {int(round(deg))}°"


# ── Alle mulige kolonner ──────────────────────────────────────────────────────

def get_column_defs() -> dict:
    """Returner kolonnenavne oversat til det aktive sprog."""
    return {
        "gc_code":      (tr("col_gc_code"),           80),
        "name":         (tr("col_name"),             260),
        "cache_type":   (tr("col_type"),              28),
        "difficulty":   (tr("col_difficulty"),        50),
        "terrain":      (tr("col_terrain"),           50),
        "container":    (tr("col_container"),         80),
        "country":      (tr("col_country"),           80),
        "state":        (tr("col_state"),            120),
        "county":       (tr("col_county"),           100),
        "distance":     (tr("col_distance"),          75),
        "found":        (tr("col_found"),             55),
        "placed_by":    (tr("col_placed_by"),        120),
        "hidden_date":  (tr("col_hidden_date"),       90),
        "last_log":     (tr("col_last_log"),          90),
        "log_count":    (tr("col_log_count"),         70),
        "dnf":          (tr("col_dnf"),               45),
        "premium_only": (tr("col_premium"),           65),
        "archived":     (tr("col_archived"),          70),
        "favorite":     (tr("col_favorite"),          60),
        "corrected":    (tr("col_corrected"),         40),
        # ── Issue #84: Latitude og Longitude ──────────────────────────────
        "latitude":     (tr("col_latitude"),         110),
        "longitude":    (tr("col_longitude"),        110),
        # ── Issue #33: GSAK-compatible fields ─────────────────────────────
        "found_date":      (tr("col_found_date"),      90),
        "dnf_date":        (tr("col_dnf_date"),        90),
        "first_to_find":   (tr("col_first_to_find"),   45),
        "favorite_points": (tr("col_favorite_points"), 55),
        "user_flag":       (tr("col_user_flag"),    30),
        "bearing":         (tr("col_bearing"),           55),
        "user_sort":       (tr("col_user_sort"),       55),
        "user_data_1":     (tr("col_user_data_1"),    100),
        "user_data_2":     (tr("col_user_data_2"),    100),
        "user_data_3":     (tr("col_user_data_3"),    100),
        "user_data_4":     (tr("col_user_data_4"),    100),
    }


def _get_active_columns() -> list[str]:
    from opensak.gui.dialogs.column_dialog import get_visible_columns
    return get_visible_columns()


def _format_date(d: datetime) -> str:
    fmt = get_settings().date_format
    if fmt == DateFormat.DMY:
        return d.strftime("%d.%m.%Y")
    if fmt == DateFormat.MDY:
        return d.strftime("%m/%d/%Y")
    if fmt == DateFormat.YMD:
        return d.strftime("%Y-%m-%d")
    # LOCALE: ask Qt for the OS short-date pattern
    qd = QDate(d.year, d.month, d.day)
    return QLocale.system().toString(qd, QLocale.FormatType.ShortFormat)


def _gc_sort_key(gc_code: GcCode) -> str:
    """Return a zero-padded sort key so GC codes sort numerically.

    GC codes are alphanumeric (base-31), so pure alphabetical sorting gives
    wrong order, e.g. GC1DCA before GC1D.  Zero-padding the suffix to a fixed
    width produces correct ordering without needing a base-31 conversion.

    Examples:
        GC1D   → GC000000001D
        GC1DCA → GC0000001DCA   (correctly sorts after GC1D)
    """
    if not gc_code:
        return ""
    upper = gc_code.upper()
    suffix = upper[2:] if upper.startswith("GC") else upper
    return "GC" + suffix.zfill(10)


# ── Issue #90: Container size sort order ─────────────────────────────────────
#
# Container values are sorted by visual grouping:
#
#   Group 1: Physical containers (smallest → largest)
#            Micro → Small → Regular → Large
#
#   Group 2: Empty bars + letter (sorted alphabetically by the letter)
#            EarthCache  → 'E'
#            Lab Cache   → 'L'
#            Other       → 'O'
#            Virtual     → 'V'
#
#   Group 3: Empty bars, no letter
#            Not chosen / empty
#
# This grouping mirrors the visual layout: caches that show filled bars come
# first (sorted by physical size), then caches that show a letter (sorted
# alphabetically by the letter — predictable and easy to extend), then
# caches with no information at all.
#
# Non-physical cache types (Virtual, EarthCache, Lab) are detected via
# cache_type because Groundspeak GPX files typically set container='Other'
# or empty for these types.
#
# Note: "Nano" is not an official Geocaching.com container size. It is an
# informal community term for very small Micro caches (< 10 ml). Databases
# imported from GSAK may contain "Nano" — migration #7 converts these to
# "Micro" automatically.
#
_CONTAINER_PHYSICAL_ORDER = {
    "micro":    1,
    "small":    2,
    "regular":  3,
    "large":    4,
}

# Cache types that have no physical container — sort by their display letter
# in group 2 (the same letter shown in SizeBarDelegate). New non-physical
# types can be added here and they'll slot in alphabetically by their letter.
_NON_PHYSICAL_TYPE_LETTERS = {
    "earthcache":                   "E",
    "lab cache":                    "L",
    "virtual cache":                "V",
    "locationless (reverse) cache": "R",
}

# Empty / unknown markers — group 3 (sorts last)
_EMPTY_CONTAINERS = {"", "not chosen"}


def _container_sort_key(container: str | None, cache_type: str | None = None) -> tuple:
    """Return sort key tuple for the Container column.

    Returns a (group, sub_key) tuple where:
      group 1 = physical container, sub_key = size order (1-5)
      group 2 = empty bars + letter, sub_key = the letter ('E', 'L', 'O', 'V')
      group 3 = empty (no letter), sub_key = ''

    Within group 1 the sub_key gives smallest → largest. Within group 2 the
    sub_key gives alphabetic order. Within group 3 sub_key is empty so
    Python's stable sort preserves existing order (e.g. distance sort).

    Non-physical types (Virtual / Earth / Lab) are detected via cache_type
    since their container value is typically 'Other' or empty in GPX data.
    """
    # Group 2a: Non-physical cache types take precedence over container value
    # (a Virtual Cache with container='Other' should sort by 'V', not 'O')
    if cache_type:
        ct = cache_type.strip().lower()
        letter = _NON_PHYSICAL_TYPE_LETTERS.get(ct)
        if letter is not None:
            return (2, letter)

    # Normalise container value
    key = (container or "").strip().lower()

    # Group 1: Physical containers (smallest → largest)
    if key in _CONTAINER_PHYSICAL_ORDER:
        return (1, _CONTAINER_PHYSICAL_ORDER[key])

    # Group 3: Empty / not chosen — no letter, sorts last
    if key in _EMPTY_CONTAINERS:
        return (3, "")

    # Group 2b: Anything else with a container value = 'Other'-like (shows 'O')
    # Includes the literal 'other' value and any unknown future container types
    return (2, "O")


from PySide6.QtWidgets import QStyledItemDelegate, QApplication
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import QRect


class SizeBarDelegate(QStyledItemDelegate):
    """Tegner GSAK-stil segmenteret størrelsesindikator for container-kolonnen.

    5 firkantede segmenter fylder op fra venstre:
      Nano=1, Micro=2, Small=3, Regular=4, Large=5
    Special visning (tomme segmenter + bogstav i sidste segment):
      Other         → 'O'   (ukendt fysisk størrelse)
      Virtual Cache → 'V'   (ingen fysisk container — by cache_type)
      EarthCache    → 'E'   (ingen fysisk container — by cache_type)
      Lab Cache     → 'L'   (ingen fysisk container — by cache_type)
    Not chosen og tom → 5 tomme segmenter, intet bogstav
    """

    # Antal fyldte segmenter per størrelse (ud af 5)
    _SEGMENTS = {
        "micro":      1,
        "small":      2,
        "regular":    3,
        "large":      4,
        "other":      0,    # tom — bogstav vises i stedet (issue #90)
        "not chosen": 0,
        "":           0,
    }
    # Bogstaver vist i sidste segment for size-værdier (issue #90)
    _SIZE_LABELS = {
        "other": "O",
    }
    # Cache-typer der vises med tomt felt + bogstav (uanset container value)
    _LABEL_TYPES = {
        "virtual cache": "V",
        "earthcache":    "E",
        "lab cache":     "L",
    }

    _SEG_COUNT   = 5
    _SEG_GAP     = 2
    _BAR_COLOR   = QColor("#5b8dd9")   # GSAK-blå
    _EMPTY_COLOR = QColor("#c8d4ea")   # lys grå baggrund
    _LABEL_COLOR = QColor("#4a72b0")   # bogstav-farve (mørkere blå)

    def paint(self, painter: QPainter, option, index) -> None:
        from PySide6.QtWidgets import QStyle
        data = index.data(Qt.ItemDataRole.UserRole + 10) or {}
        size_key  = data.get("size", "").lower()  if isinstance(data, dict) else ""
        cache_type = data.get("type", "").lower() if isinstance(data, dict) else ""

        filled = self._SEGMENTS.get(size_key, 0)
        # cache_type label tager førsteret over size label (Virtual/Earth → V/E)
        # ellers fall back til size-baseret label (Other → O)
        label = self._LABEL_TYPES.get(cache_type, "") or self._SIZE_LABELS.get(size_key, "")

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Tegn standard baggrund (inkl. selection-highlight) inden vi tegner søjlerne
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if is_selected:
            painter.fillRect(option.rect, QColor("#3daee9"))
        else:
            # Lad standard delegate håndtere alternating rows mv.
            super().paint(painter, option, index)

        rect = option.rect
        margin_x, margin_y = 4, 3
        total_w = rect.width()  - 2 * margin_x
        total_h = rect.height() - 2 * margin_y
        x0 = rect.x() + margin_x
        y0 = rect.y() + margin_y

        seg_w = max(4, (total_w - self._SEG_GAP * (self._SEG_COUNT - 1)) // self._SEG_COUNT)

        for i in range(self._SEG_COUNT):
            sx = x0 + i * (seg_w + self._SEG_GAP)
            seg_rect = QRect(sx, y0, seg_w, total_h)

            is_filled = (i < filled) and not label
            is_last   = (i == self._SEG_COUNT - 1)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._BAR_COLOR if is_filled else self._EMPTY_COLOR)
            painter.drawRoundedRect(seg_rect, 1, 1)

            # Bogstav i det sidste segment for Virtual/Earth/Other
            if label and is_last:
                painter.setPen(self._LABEL_COLOR)
                font = painter.font()
                font.setPointSize(7)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(seg_rect, Qt.AlignmentFlag.AlignCenter, label)
                painter.setPen(Qt.PenStyle.NoPen)

        painter.restore()

    def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        sh.setHeight(max(sh.height(), 20))
        return sh


class GcCodeDelegate(QStyledItemDelegate):
    """Issue #117/#270: Tegner farvet baggrund i gc_code-kolonnen (GSAK-style).

    Farve-prioritet (dæmpede pastellfarver, sort tekst — farverne i issue #270
    er valgt af Allan efter sammenligning med en rigtig GSAK-installation):
      1. Archived / unavailable  → rødlig  (#f1948a) — delt farve for begge
      2. Placed (brugeren er CO) → grøn    (#7dcea0)
      3. Found                   → gul     (#f9e79f)
      4. Not found               → ingen   (standard rækkefarve)

    Valg: farvet baggrund frem for farvet tekst giver bedre læsbarhed
    og matcher GSAK's visuelle stil. Sort tekst bruges konsekvent på alle
    statusfarver, inkl. disabled — som tidligere brugte orange tekst på rød
    baggrund og var næsten ulæseligt (issue #270).

    Issue #272: owner_name sammenlignes via normalize_geocacher_name() i
    stedet for blot strip()/lower(), så caches stadig farves korrekt selv
    når GPX-filen indeholder dobbelte mellemrum eller non-breaking spaces
    (\\xa0) i ejernavnet — set i eksporter fra visse tredjeparts-værktøjer.
    """

    _COLOR_ARCHIVED = QColor("#f1948a")   # rød   — archived OG disabled
    _COLOR_PLACED   = QColor("#7dcea0")   # grøn  — egne caches
    _COLOR_FOUND    = QColor("#f9e79f")   # gul   — fundet

    def _bg_color(self, index) -> QColor | None:
        """Returnér baggrundsfarve for denne cache, eller None for default."""
        from opensak.gui.settings import get_settings
        cache = index.data(Qt.ItemDataRole.UserRole)
        if cache is None:
            return None
        if cache.archived or not cache.available:
            return self._COLOR_ARCHIVED
        gc_username = normalize_geocacher_name(get_settings().gc_username)
        if gc_username and normalize_geocacher_name(cache.owner_name) == gc_username:
            return self._COLOR_PLACED
        if cache.found:
            return self._COLOR_FOUND
        return None

    def paint(self, painter: QPainter, option, index) -> None:
        from PySide6.QtWidgets import QStyle
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        painter.save()

        if is_selected:
            # Valgt række: brug standard selection-farve
            painter.fillRect(option.rect, QColor("#3daee9"))
        else:
            bg = self._bg_color(index)
            if bg is not None:
                painter.fillRect(option.rect, bg)
            else:
                # Ingen statusfarve — lad standard delegate tegne baggrund
                # (alternating rows mv.)
                super().paint(painter, option, index)
                painter.restore()
                return

        # Tegn tekst — farven følger tema/palette undtagen på statusfarve-baggrunde
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        text_rect = option.rect.adjusted(4, 0, -4, 0)

        cache = index.data(Qt.ItemDataRole.UserRole)

        # Tekstfarve-prioritet (issue #270 — sort tekst på alle statusfarver,
        # inkl. disabled, der tidligere havde næsten ulæselig orange-på-rød):
        #   Valgt række    → highlightedText (hvid på blå)
        #   statusfarve bg → sort (pastels er altid lyse, sort er altid læsbart)
        #   ingen baggrund → palette.text() (følger light/dark tema)
        if is_selected:
            text_color = option.palette.highlightedText().color()
        elif bg is not None:
            # Statusfarve baggrund (rød/gul/grøn pastel) — sort er altid læsbart
            text_color = QColor(Qt.GlobalColor.black)
        else:
            text_color = option.palette.text().color()

        painter.setPen(text_color)

        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

        # Strikethrough for archived — tegn en linje hen over teksten
        if not is_selected and cache is not None and cache.archived:
            fm = painter.fontMetrics()
            text_width = fm.horizontalAdvance(text)
            mid_y = option.rect.center().y()
            x_start = text_rect.left()
            painter.setPen(QColor(Qt.GlobalColor.black))
            painter.drawLine(x_start, mid_y, x_start + text_width, mid_y)

        painter.restore()


class CacheTableModel(QAbstractTableModel):
    """Qt table model backed by a list of Cache objects."""

    flags_changed = Signal()          # emitteres når user_flag toggler
    sort_changed = Signal(str, bool)  # (col_id, ascending) når brugeren sorterer

    def __init__(self, parent=None):
        super().__init__(parent)
        self._caches: list[Cache] = []
        self._distances: dict[int, float] = {}
        self._bearings: dict[int, float] = {}
        self._columns: list[str] = _get_active_columns()

    def flags(self, index: QModelIndex | QPersistentModelIndex):
        base = super().flags(index)
        if index.isValid() and self._columns[index.column()] in ("user_flag", "first_to_find"):
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QModelIndex | QPersistentModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        """Toggle user_flag eller first_to_find når brugeren klikker på kolonnen."""
        if not index.isValid():
            return False
        col = self._columns[index.column()]
        if col not in ("user_flag", "first_to_find"):
            return False
        cache = self._caches[index.row()]
        from opensak.db.database import get_session
        from opensak.db.models import Cache as CacheModel
        if col == "user_flag":
            new_val = not bool(cache.user_flag)
            with get_session() as session:
                c = session.query(CacheModel).filter_by(gc_code=cache.gc_code).first()
                if c:
                    c.user_flag = new_val
            cache.user_flag = new_val
            self.flags_changed.emit()
        else:  # first_to_find
            new_val = not bool(cache.first_to_find)
            with get_session() as session:
                c = session.query(CacheModel).filter_by(gc_code=cache.gc_code).first()
                if c:
                    c.first_to_find = new_val
            cache.first_to_find = new_val
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
        return True

    def reload_columns(self) -> None:
        """Genindlæs kolonnedefinitioner fra indstillinger."""
        self.beginResetModel()
        self._columns = _get_active_columns()
        self.endResetModel()

    def load(self, caches: list[Cache]) -> None:
        self.beginResetModel()
        self._caches = caches
        self._update_distances()
        self.endResetModel()

    def _update_distances(self) -> None:
        # Vectorised: compute distance + bearing for every cache in one numpy
        # pass instead of a per-row Python loop. This runs on every table
        # refresh, so on large databases (100k+ caches) the loop was a real
        # per-keystroke cost; the batch form is ~negligible.
        settings = get_settings()
        self._distances = {}
        self._bearings = {}
        valid = [
            c for c in self._caches
            if c.latitude is not None and c.longitude is not None
        ]
        if not valid:
            return
        lats = [c.latitude for c in valid]
        lons = [c.longitude for c in valid]
        dists = haversine_km_batch(settings.home_lat, settings.home_lon, lats, lons)
        bears = _bearing_deg_batch(settings.home_lat, settings.home_lon, lats, lons)
        for i, c in enumerate(valid):
            self._distances[c.id] = float(dists[i])
            self._bearings[c.id] = float(bears[i])

    def cache_at(self, row: int) -> Optional[Cache]:
        if 0 <= row < len(self._caches):
            return self._caches[row]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._caches)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._columns)

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                col_id = self._columns[section]
                return get_column_defs().get(col_id, (col_id, 80))[0]
            if role == Qt.ItemDataRole.ToolTipRole:
                col_id = self._columns[section]
                if col_id == "corrected":
                    return tr("col_corrected_header_tooltip")
                if col_id == "user_flag":
                    return tr("col_user_flag_header_tooltip")
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignCenter
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        cache = self._caches[index.row()]
        col = self._columns[index.column()]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(cache, col)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in ("difficulty", "terrain", "distance", "found",
                       "dnf", "premium_only", "archived", "log_count",
                       "corrected", "first_to_find", "user_flag", "bearing",
                       "user_sort", "favorite_points",
                       "latitude", "longitude"):
                return Qt.AlignmentFlag.AlignCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.FontRole:
            if cache.found:
                font = QFont()
                font.setItalic(True)
                return font

        if role == Qt.ItemDataRole.ToolTipRole:
            if col == "cache_type":
                t = cache.cache_type or ""
                return t.replace("Unknown", "Mystery")
            if col == "corrected":
                note = cache.user_note
                if (note and note.is_corrected
                        and note.corrected_lat is not None
                        and note.corrected_lon is not None):
                    fmt = get_settings().coord_format
                    coords = format_coords(note.corrected_lat, note.corrected_lon, fmt)
                    return tr("col_corrected_tooltip", coords=coords)
            if col in ("latitude", "longitude"):
                # Vis tooltip der angiver om koordinaterne er korrigerede
                note = cache.user_note
                if note and note.is_corrected:
                    return tr("detail_corrected_coords")
                return tr("col_coord_tooltip_original")

        if role == Qt.ItemDataRole.DecorationRole:
            return self._decoration_value(cache, col)

        if role == Qt.ItemDataRole.UserRole + 10:
            # Dict med size + type til SizeBarDelegate
            return {
                "size": (cache.container or "").lower(),
                "type": (cache.cache_type or "").lower(),
            }

        if role == Qt.ItemDataRole.UserRole:
            return cache

        return None

    # ── Cache-type string → icon key ──────────────────────────────────────────

    @staticmethod
    def _type_icon_key(cache: Cache) -> str:
        """Map a Cache object to an icon_provider key (cache type only, ikke status)."""
        if cache.archived:
            return "archived"
        if not cache.available:
            return "disabled"
        t = (cache.cache_type or "").lower()
        mapping = {
            "traditional cache":            "traditional",
            "multi-cache":                  "multi",
            "mystery cache":                "mystery",
            "unknown cache":                "mystery",
            "letterbox hybrid":             "letterbox",
            "wherigo cache":                "wherigo",
            "earthcache":                   "earthcache",
            "virtual cache":                "virtual",
            "webcam cache":                 "webcam",
            "event cache":                  "event",
            "cache in trash out event":     "cito",
            "mega-event cache":             "mega_event",
            "giga-event cache":             "giga_event",
            "lab cache":                    "lab_cache",
            "community celebration event":  "community_celebration",
            "gps adventures maze":          "gps_adventures",
            "gps adventures maze exhibit":  "gps_adventures",
            "gps adventures exhibit":       "gps_adventures",
            "locationless (reverse) cache": "locationless",
            "project a.p.e. cache":         "project_ape",
            "groundspeak hq":               "geocaching_hq",
        }
        return mapping.get(t, "unknown")

    @staticmethod
    def _size_icon_key(cache: Cache) -> str:
        """Map container string to icon_provider size key."""
        mapping = {
            "micro":      "micro",
            "small":      "small",
            "regular":    "regular",
            "large":      "large",
            "other":      "other",
            "not chosen": "not_chosen",
            "virtual":    "not_chosen",
        }
        return mapping.get((cache.container or "").lower(), "other")

    def _decoration_value(self, cache: Cache, col: str):
        """Return QIcon for columns that show icons."""
        if col == "cache_type":
            return get_cache_type_icon(
                self._type_icon_key(cache),
                size=24,
            )
        if col == "container":
            return get_cache_size_icon(self._size_icon_key(cache), size=20)
        return None

    @staticmethod
    def _effective_coords(cache: Cache) -> tuple[float | None, float | None]:
        """Returnér de effektive koordinater (corrected hvis sat, ellers original).

        Bruges af latitude/longitude-kolonnerne så visningen matcher kortet,
        som også viser corrected hvis tilgængelige.
        """
        note = cache.user_note
        if (note and note.is_corrected
                and note.corrected_lat is not None
                and note.corrected_lon is not None):
            return note.corrected_lat, note.corrected_lon
        return cache.latitude, cache.longitude

    def _display_value(self, cache: Cache, col: str) -> str:
        if col == "gc_code":
            return cache.gc_code or ""
        if col == "name":
            return cache.name or ""
        if col == "cache_type":
            return ""   # ikon vises via DecorationRole — fuldt navn i tooltip
        if col == "difficulty":
            return f"{cache.difficulty:.1f}" if cache.difficulty else "?"
        if col == "terrain":
            return f"{cache.terrain:.1f}" if cache.terrain else "?"
        if col == "container":
            return ""   # ikon vises via DecorationRole
        if col == "country":
            return cache.country or ""
        if col == "state":
            return cache.state or ""
        if col == "county":
            return cache.county or ""
        if col == "distance":
            dist = self._distances.get(cache.id)
            if dist is None:
                return "?"
            if get_settings().use_miles:
                return f"{dist * 0.621371:.1f} mi"
            return f"{dist:.1f} km"
        if col == "bearing":
            deg = self._bearings.get(cache.id)
            if deg is None:
                return "?"
            return _bearing_compass(deg)
        if col == "found":
            return "✓" if cache.found else ""
        if col == "placed_by":
            return cache.placed_by or ""
        if col == "hidden_date":
            return _format_date(cache.hidden_date) if cache.hidden_date else ""
        if col == "last_log":
            return _format_date(cache.last_log_date) if cache.last_log_date else ""
        if col == "log_count":
            # Issue #87: use cached log_count column instead of len(cache.logs)
            # because logs are noload'ed for performance and would always be
            # an empty list here. log_count is maintained on import.
            return str(cache.log_count or 0)
        if col == "dnf":
            return "DNF" if cache.dnf else ""
        if col == "premium_only":
            return "P" if cache.premium_only else ""
        if col == "archived":
            return "✓" if cache.archived else ""
        if col == "favorite":
            return "★" if cache.favorite_point else ""
        if col == "corrected":
            note = cache.user_note
            return "📍" if (note and note.is_corrected) else ""
        # ── Issue #84: Latitude og Longitude (i brugerens valgte format) ──────
        if col == "latitude":
            lat, _ = self._effective_coords(cache)
            if lat is None:
                return ""
            fmt = get_settings().coord_format
            return format_lat(lat, fmt)
        if col == "longitude":
            _, lon = self._effective_coords(cache)
            if lon is None:
                return ""
            fmt = get_settings().coord_format
            return format_lon(lon, fmt)
        # ── Issue #33: GSAK-compatible fields ─────────────────────────────────
        if col == "found_date":
            return _format_date(cache.found_date) if cache.found_date else ""
        if col == "dnf_date":
            return _format_date(cache.dnf_date) if cache.dnf_date else ""
        if col == "first_to_find":
            return "FTF" if cache.first_to_find else ""
        if col == "favorite_points":
            return str(cache.favorite_points) if cache.favorite_points is not None else ""
        if col == "user_flag":
            return "🚩" if cache.user_flag else ""
        if col == "user_sort":
            return str(cache.user_sort) if cache.user_sort is not None else ""
        if col == "user_data_1":
            return cache.user_data_1 or ""
        if col == "user_data_2":
            return cache.user_data_2 or ""
        if col == "user_data_3":
            return cache.user_data_3 or ""
        if col == "user_data_4":
            return cache.user_data_4 or ""
        return ""

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        if column >= len(self._columns):
            return
        col = self._columns[column]
        reverse = (order == Qt.SortOrder.DescendingOrder)
        self.beginResetModel()
        if col == "difficulty":
            self._caches.sort(key=lambda c: c.difficulty or 0, reverse=reverse)
        elif col == "terrain":
            self._caches.sort(key=lambda c: c.terrain or 0, reverse=reverse)
        elif col == "distance":
            self._caches.sort(
                key=lambda c: self._distances.get(c.id, 99999), reverse=reverse
            )
        elif col == "bearing":
            self._caches.sort(
                key=lambda c: self._bearings.get(c.id, 999), reverse=reverse
            )
        elif col == "found":
            self._caches.sort(key=lambda c: int(c.found), reverse=reverse)
        elif col == "corrected":
            self._caches.sort(
                key=lambda c: int(
                    bool(c.user_note and c.user_note.is_corrected)
                ),
                reverse=reverse,
            )
        elif col == "log_count":
            # Issue #87: sort on cached log_count column (logs are noload'ed)
            self._caches.sort(
                key=lambda c: c.log_count or 0, reverse=reverse
            )
        elif col == "last_log":
            # Issue #186: sort on cached last_log_date column (logs are noload'ed)
            self._caches.sort(
                key=lambda c: c.last_log_date or datetime.min, reverse=reverse
            )
        elif col == "hidden_date":
            self._caches.sort(
                key=lambda c: c.hidden_date or datetime.min, reverse=reverse
            )
        elif col == "found_date":
            self._caches.sort(
                key=lambda c: c.found_date or datetime.min, reverse=reverse
            )
        elif col == "dnf_date":
            self._caches.sort(
                key=lambda c: c.dnf_date or datetime.min, reverse=reverse
            )
        elif col == "first_to_find":
            self._caches.sort(key=lambda c: int(c.first_to_find or False), reverse=reverse)
        elif col == "user_flag":
            self._caches.sort(key=lambda c: int(c.user_flag or False), reverse=reverse)
        elif col == "user_sort":
            self._caches.sort(key=lambda c: c.user_sort if c.user_sort is not None else 999999, reverse=reverse)
        elif col == "favorite_points":
            self._caches.sort(key=lambda c: c.favorite_points or 0, reverse=reverse)
        elif col == "container":
            # Issue #90: Sort by logical container size, not alphabetically
            self._caches.sort(
                key=lambda c: _container_sort_key(c.container, c.cache_type),
                reverse=reverse,
            )
        elif col == "latitude":
            # Numerisk sortering på rå float — ikke formateret tekst
            # Bruger effektive koordinater (corrected hvis sat)
            self._caches.sort(
                key=lambda c: (v if (v := self._effective_coords(c)[0]) is not None
                               else -999.0),
                reverse=reverse,
            )
        elif col == "longitude":
            self._caches.sort(
                key=lambda c: (v if (v := self._effective_coords(c)[1]) is not None
                               else -999.0),
                reverse=reverse,
            )
        elif col == "name":
            self._caches.sort(
                key=lambda c: (c.name or "").lower(), reverse=reverse
            )
        elif col == "gc_code":
            self._caches.sort(key=lambda c: _gc_sort_key(c.gc_code or ""), reverse=reverse)
        else:
            self._caches.sort(
                key=lambda c: (getattr(c, col, "") or "").lower()
                if isinstance(getattr(c, col, ""), str) else getattr(c, col, 0) or 0,
                reverse=reverse
            )
        self.endResetModel()
        self.sort_changed.emit(col, not reverse)


class CacheTableView(QTableView):
    """The main cache list widget."""

    cache_selected = Signal(object)
    flags_changed = Signal()          # videresendes fra model
    sort_changed = Signal(str, bool)  # (col_id, ascending) videresendes fra model
    location_updated = Signal()       # emitted after right-click location update
    edit_requested = Signal(object)   # emitted when user requests edit of a cache

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = CacheTableModel()
        self.setModel(self._model)
        self._model.flags_changed.connect(self.flags_changed)
        self._model.sort_changed.connect(self._on_model_sort_changed)
        self._last_sort_col: Optional[int] = None
        self._last_sort_asc: bool = True
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setSortingEnabled(True)
        self.verticalHeader().setVisible(False)
        self.setWordWrap(False)
        self.verticalHeader().setDefaultSectionSize(24)
        self._applying_widths = False
        self._apply_column_widths()
        self.horizontalHeader().setSortIndicatorShown(True)
        self.horizontalHeader().setSectionsMovable(True)
        self.horizontalHeader().sectionResized.connect(self._on_column_resized)
        self.horizontalHeader().sectionMoved.connect(self._on_column_moved)
        self.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.doubleClicked.connect(self._on_double_clicked)
        # Ensartet selection-farve på alle platforme (Windows bruger ellers grå)
        self.setStyleSheet("""
            QTableView {
                selection-background-color: #3daee9;
                selection-color: white;
            }
        """)

    def mousePressEvent(self, event) -> None:
        """Klik på user_flag- eller first_to_find-kolonnen toggler feltet direkte."""
        index = self.indexAt(event.pos())
        if index.isValid():
            col = self._model._columns[index.column()]
            if col in ("user_flag", "first_to_find") and event.button() == Qt.MouseButton.LeftButton:
                self._model.setData(index, None)
                return
        super().mousePressEvent(event)

    def _on_double_clicked(self, index) -> None:
        """Dobbeltklik på corrected-kolonnen åbner koordinatdialogen direkte."""
        if not index.isValid():
            return
        col = self._model._columns[index.column()]
        if col == "corrected":
            cache = self._model.cache_at(index.row())
            if cache:
                self._edit_corrected(cache)

    def _apply_column_widths(self) -> None:
        self._applying_widths = True
        try:
            header = self.horizontalHeader()
            columns = self._model._columns
            saved = get_column_widths()
            for i, col_id in enumerate(columns):
                default_width = get_column_defs().get(col_id, (col_id, 80))[1]
                width = saved.get(col_id, default_width)
                self.setColumnWidth(i, width)
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                if col_id == "container":
                    self._size_bar_delegate = SizeBarDelegate(self)
                    self.setItemDelegateForColumn(i, self._size_bar_delegate)
                elif col_id == "gc_code":
                    self._gc_code_delegate = GcCodeDelegate(self)
                    self.setItemDelegateForColumn(i, self._gc_code_delegate)
                else:
                    # None clears the column delegate (stub types it non-optional)
                    self.setItemDelegateForColumn(i, None)  # type: ignore[arg-type]
            if "name" in columns:
                name_idx = columns.index("name")
                header.setSectionResizeMode(
                    name_idx, QHeaderView.ResizeMode.Interactive
                )
            # Genskab visuel kolonne-rækkefølge fra opensak.json (issue #199).
            # `columns` er allerede i gemt rækkefølge — sørg for at header-sektionerne
            # følger samme rækkefølge visuelt (logisk indeks 0..n-1 i den orden).
            for visual_pos, _col_id in enumerate(columns):
                current_visual = header.visualIndex(visual_pos)
                if current_visual != visual_pos:
                    header.moveSection(current_visual, visual_pos)
        finally:
            self._applying_widths = False

    def _on_column_resized(self, logical_index: int, _old: int, new_size: int) -> None:
        if self._applying_widths:
            return
        columns = self._model._columns
        if logical_index >= len(columns):
            return
        col_id = columns[logical_index]
        widths = get_column_widths()
        widths[col_id] = new_size
        set_column_widths(widths)

    def _on_column_moved(self, _logical_index: int, old_visual: int, new_visual: int) -> None:
        """Gem ny kolonne-rækkefølge når brugeren trækker en kolonne (issue #199)."""
        if self._applying_widths:
            return
        from opensak.gui.dialogs.column_dialog import set_visible_columns
        header = self.horizontalHeader()
        columns = self._model._columns
        # Byg ny rækkefølge af col_id'er baseret på aktuel visuel position
        new_order = [
            columns[header.logicalIndex(visual_pos)]
            for visual_pos in range(len(columns))
        ]
        set_visible_columns(new_order)
        # Genindlæs model med ny logisk rækkefølge, og nulstil header til
        # identitets-rækkefølge (0..n-1) så fremtidige resizes/sorts matcher.
        self._applying_widths = True
        try:
            self._model.reload_columns()
            self._apply_column_widths()
        finally:
            self._applying_widths = False

    def reload_columns(self) -> None:
        """Opdatér kolonner fra indstillinger."""
        self._model.reload_columns()
        self._apply_column_widths()

    def _on_model_sort_changed(self, col_id: str, ascending: bool) -> None:
        """Store last sort so we can re-apply after reload."""
        cols = self._model._columns
        if col_id in cols:
            self._last_sort_col = cols.index(col_id)
            self._last_sort_asc = ascending
        self.sort_changed.emit(col_id, ascending)

    def apply_sort(self, col_id: str, ascending: bool) -> None:
        """Genanvend sortering - kaldes fra mainwindow ved opstart/db-skift."""
        cols = self._model._columns
        if col_id not in cols:
            return
        col_idx = cols.index(col_id)
        order = (Qt.SortOrder.AscendingOrder if ascending
                 else Qt.SortOrder.DescendingOrder)
        self._last_sort_col = col_idx
        self._last_sort_asc = ascending
        # Bloker sort_changed så apply_sort ikke trigger _save_sort_for_active_db
        # (dette er et internt gendan-kald, ikke en bruger-handling)
        self._model.blockSignals(True)
        self._model.sort(col_idx, order)
        self._model.blockSignals(False)
        self.horizontalHeader().blockSignals(True)
        self.horizontalHeader().setSortIndicator(col_idx, order)
        self.horizontalHeader().blockSignals(False)

    def load_caches(self, caches: list[Cache]) -> None:
        # Bloker row-changed signalet under load så første cache ikke
        # auto-selekteres og vises på kortet (Qt sætter current til række 0
        # efter beginResetModel/endResetModel)
        self.selectionModel().blockSignals(True)
        self._model.load(caches)
        # Genanvend sortering - beginResetModel() nulstiller Qt sort-indikatoren
        # Bloker sort_changed så load ikke trigger _save_sort_for_active_db
        if self._last_sort_col is not None:
            order = (Qt.SortOrder.AscendingOrder if self._last_sort_asc
                     else Qt.SortOrder.DescendingOrder)
            self._model.blockSignals(True)
            self._model.sort(self._last_sort_col, order)
            self._model.blockSignals(False)
            self.horizontalHeader().blockSignals(True)
            self.horizontalHeader().setSortIndicator(self._last_sort_col, order)
            self.horizontalHeader().blockSignals(False)
        self.clearSelection()
        self.setCurrentIndex(self._model.index(-1, -1))
        self.selectionModel().blockSignals(False)

    def _on_row_changed(self, current, previous) -> None:
        cache = self._model.cache_at(current.row())
        if cache:
            self.cache_selected.emit(cache)

    def _show_context_menu(self, pos: QPoint) -> None:
        """Vis højreklik kontekstmenu for den valgte cache."""
        cache = self._model.cache_at(self.indexAt(pos).row())
        if not cache:
            return

        menu = QMenu(self)

        # Åbn på geocaching.com
        act_open = menu.addAction(tr("ctx_open_geocaching"))
        act_open.triggered.connect(
            lambda: webbrowser.open(f"https://coord.info/{cache.gc_code}")
        )

        # Åbn i kortapp
        if cache.latitude and cache.longitude:
            from opensak.gui.settings import get_settings
            s = get_settings()
            map_name = "OpenStreetMap" if s.map_provider == "osm" else "Google Maps"
            act_maps = menu.addAction(tr("ctx_open_maps", map_name=map_name))
            lat, lon = cache.latitude, cache.longitude
            act_maps.triggered.connect(
                lambda checked=False, la=lat, lo=lon: webbrowser.open(
                    get_settings().get_maps_url(la, lo)
                )
            )

        menu.addSeparator()

        # Kopiér GC kode
        act_copy_gc = menu.addAction(tr("ctx_copy_gc"))
        act_copy_gc.triggered.connect(lambda: self._copy_to_clipboard(cache.gc_code))

        # Kopiér koordinater — i det valgte format
        if cache.latitude and cache.longitude:
            fmt = get_settings().coord_format
            coords = format_coords(cache.latitude, cache.longitude, fmt)
            act_copy_coords = menu.addAction(tr("ctx_copy_coords"))
            act_copy_coords.triggered.connect(
                lambda: self._copy_to_clipboard(coords)
            )

            # Åbn koordinatkonverter
            act_converter = menu.addAction(tr("ctx_coord_converter"))
            lat, lon = cache.latitude, cache.longitude
            act_converter.triggered.connect(
                lambda checked=False, la=lat, lo=lon: self._open_converter(la, lo)
            )

        menu.addSeparator()

        # Korrigerede koordinater
        note = cache.user_note
        has_corrected = note and note.is_corrected
        if has_corrected:
            act_edit_corrected = menu.addAction(tr("ctx_edit_corrected"))
        else:
            act_edit_corrected = menu.addAction(tr("ctx_add_corrected"))
        act_edit_corrected.triggered.connect(
            lambda checked=False, c=cache: self._edit_corrected(c)
        )
        if has_corrected:
            act_clear_corrected = menu.addAction(tr("ctx_clear_corrected"))
            act_clear_corrected.triggered.connect(
                lambda checked=False, c=cache: self._clear_corrected(c)
            )

        menu.addSeparator()

        # Rediger cache
        act_edit = menu.addAction(tr("ctx_edit_cache"))
        act_edit.triggered.connect(
            lambda checked=False, c=cache: self.edit_requested.emit(c)
        )

        menu.addSeparator()

        # Marker som fundet / ikke fundet
        if cache.found:
            act_found = menu.addAction(tr("ctx_mark_not_found"))
            act_found.triggered.connect(lambda: self._toggle_found(cache, False))
        else:
            act_found = menu.addAction(tr("ctx_mark_found"))
            act_found.triggered.connect(lambda: self._toggle_found(cache, True))

        from opensak.utils import flags
        if flags.update_location:
            menu.addSeparator()

            act_update_loc = menu.addAction(tr("ctx_update_location"))
            gc = cache.gc_code
            act_update_loc.triggered.connect(
                lambda checked=False, code=gc: self._update_location(code)
            )

        menu.exec(self.viewport().mapToGlobal(pos))

    def _edit_corrected(self, cache: Cache) -> None:
        """Åbn dialog til at sætte/redigere korrigerede koordinater."""
        from opensak.gui.dialogs.corrected_coords_dialog import CorrectedCoordsDialog
        note = cache.user_note
        cur_lat = note.corrected_lat if (note and note.is_corrected) else None
        cur_lon = note.corrected_lon if (note and note.is_corrected) else None
        dlg = CorrectedCoordsDialog(
            gc_code=cache.gc_code,
            orig_lat=cache.latitude,
            orig_lon=cache.longitude,
            corrected_lat=cur_lat,
            corrected_lon=cur_lon,
            parent=self,
        )
        if dlg.exec():
            lat, lon = dlg.get_coords()
            self._save_corrected(cache, lat, lon)

    def _clear_corrected(self, cache: Cache) -> None:
        """Slet korrigerede koordinater."""
        self._save_corrected(cache, None, None)

    def _save_corrected(self, cache: Cache, lat, lon) -> None:
        from opensak.db.database import get_session
        from opensak.db.models import UserNote, Cache as CacheModel
        from sqlalchemy.orm import joinedload
        with get_session() as session:
            cache_row = session.query(CacheModel).options(
                joinedload(CacheModel.user_note)
            ).filter_by(gc_code=cache.gc_code).first()
            if not cache_row:
                return
            note = cache_row.user_note
            if note is None:
                note = UserNote(cache_id=cache_row.id)
                session.add(note)
                session.flush()
            note.corrected_lat = lat
            note.corrected_lon = lon
            note.is_corrected = (lat is not None and lon is not None)

        # Reload det fulde cache-objekt fra DB med user_note eager-loaded,
        # og erstat det detachede objekt i modellen direkte.
        # Det undgår alle problemer med at skrive til detached ORM-relationer.
        with get_session() as session:
            fresh = session.query(CacheModel).options(
                joinedload(CacheModel.user_note),
                joinedload(CacheModel.waypoints),
                joinedload(CacheModel.attributes),
            ).filter_by(gc_code=cache.gc_code).first()
            if fresh is None:
                return

        # Erstat objektet i listen — find det via gc_code
        caches = self._model._caches
        for i, c in enumerate(caches):
            if c.gc_code == cache.gc_code:
                caches[i] = fresh
                break

        self._model.beginResetModel()
        self._model.endResetModel()

    def _open_converter(self, lat: float, lon: float) -> None:
        """Åbn koordinatkonverter popup."""
        from opensak.gui.dialogs.coord_converter_dialog import CoordConverterDialog
        dlg = CoordConverterDialog(lat, lon, parent=self)
        dlg.exec()

    def _copy_to_clipboard(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    def _toggle_found(self, cache, found: bool) -> None:
        from opensak.db.database import get_session
        from opensak.db.models import Cache as CacheModel
        with get_session() as session:
            c = session.query(CacheModel).filter_by(gc_code=cache.gc_code).first()
            if c:
                c.found = found
        cache.found = found
        self._model.beginResetModel()
        self._model.endResetModel()

    def _update_location(self, gc_code: str) -> None:
        """Open UpdateLocationDialog targeted at a single cache."""
        from opensak.gui.dialogs.update_location_dialog import UpdateLocationDialog
        dlg = UpdateLocationDialog(self, gc_codes=[gc_code])
        dlg.location_updated.connect(self.location_updated)
        dlg.exec()

    def selected_cache(self) -> Optional[Cache]:
        indexes = self.selectedIndexes()
        if indexes:
            return self._model.cache_at(indexes[0].row())
        return None

    def select_by_gc_code(self, gc_code: str) -> None:
        """Vælg og scroll til rækken med det givne gc_code. Bruges når
        brugeren klikker på en pin på kortet, så listen synkroniseres."""
        for row in range(self._model.rowCount()):
            cache = self._model.cache_at(row)
            if cache and cache.gc_code == gc_code:
                index = self._model.index(row, 0)
                self.setCurrentIndex(index)
                self.scrollTo(index, self.ScrollHint.PositionAtCenter)
                return

    def refresh_cache_row(self, gc_code: str) -> None:
        """Genindlæs ét cache-objekt fra DB og opdatér rækken i modellen.

        Bruges fx efter at korrigerede koordinater er sat via kortets
        højreklik-menu, så CC-ikonet vises uden at brugeren skal klikke Refresh.
        """
        from opensak.db.database import get_session
        from opensak.db.models import Cache as CacheModel
        from sqlalchemy.orm import joinedload

        with get_session() as session:
            fresh = session.query(CacheModel).options(
                joinedload(CacheModel.user_note),
                joinedload(CacheModel.waypoints),
                joinedload(CacheModel.attributes),
            ).filter_by(gc_code=gc_code).first()
            if fresh is None:
                return

        caches = self._model._caches
        for i, c in enumerate(caches):
            if c.gc_code == gc_code:
                caches[i] = fresh
                break
        else:
            return  # cachen er ikke i den aktuelle visning — intet at opdatere

        self._model.beginResetModel()
        self._model.endResetModel()

    def row_count(self) -> int:
        return self._model.rowCount()

    def get_all_caches(self) -> list[Cache]:
        """Returner alle caches i det aktive filter (som vist i tabellen)."""
        return [
            self._model.cache_at(i)
            for i in range(self._model.rowCount())
            if self._model.cache_at(i) is not None
        ]

    def get_flagged_caches(self) -> list[Cache]:
        """Returner alle flaggede caches i det aktive filter."""
        return [c for c in self.get_all_caches() if c.user_flag]
