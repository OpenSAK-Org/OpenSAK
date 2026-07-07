"""
src/opensak/gui/icon_provider.py — OpenSAK cache icon provider

Leverer QIcon og QPixmap for cache-typer, størrelser og kort-pins.
Bruger SVG filer fra assets/icons/ mapperne hvis de findes,
med fallback til håndlavede SVG strenge.

Brug:
    from opensak.gui.icon_provider import (
        get_cache_type_icon,
        get_cache_size_icon,
        get_cache_type_pixmap,
        get_map_pin_html,
    )

    icon = get_cache_type_icon("traditional")   # QIcon 32x32
    icon = get_cache_type_icon("found")         # status-variant
    size = get_cache_size_icon("micro")         # QIcon 32x32
    pin  = get_map_pin_html("mystery")          # HTML til Leaflet divIcon
"""

from __future__ import annotations
from pathlib import Path
from functools import lru_cache

from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtSvg import QSvgRenderer


# ── Asset paths ───────────────────────────────────────────────────────────────

_ASSETS_DIR = Path(__file__).parent.parent / "assets" / "icons"
_CACHE_TYPES_DIR = _ASSETS_DIR / "cache_types"
_CACHE_FOUND_DIR = _ASSETS_DIR / "cache_found"

# Issue #519 follow-up: bundled offline HTML reference listing every custom
# icon file name, recommended canvas size and tips (Inkscape, Plain SVG
# export, etc.) — opened from Settings → Advanced via QDesktopServices, so
# it must be a real file on disk rather than generated at runtime.
_ICON_GUIDE_PATH = Path(__file__).parent.parent / "assets" / "icon_guide.html"


def get_icon_guide_path() -> Path:
    """Sti til den bundlede HTML icon-navngivnings-guide (issue #519 follow-up)."""
    return _ICON_GUIDE_PATH


# ── Mapping: intern nøgle → SVG filnavn (uden .svg) ──────────────────────────

_TYPE_FILE_MAP: dict[str, str] = {
    "traditional":           "traditional_cache",
    "multi":                 "multi_cache",
    "mystery":               "mystery_cache",
    "letterbox":             "letterbox_hybrid",
    "wherigo":               "wherigo_cache",
    "earthcache":            "earthcache",
    "virtual":               "virtual_cache",
    "webcam":                "webcam_cache",
    "event":                 "event_cache",
    "cito":                  "cache_in_trash_out_event",
    "mega_event":            "mega_event_cache",
    "giga_event":            "giga_event_cache",
    "lab_cache":             "adventure_lab",
    "gps_adventures":        "gps_adventures_maze",
    "community_celebration": "community_celebration_event",
    "locationless":          "locationless_cache",
    "project_ape":           "project_ape_cache",
    "geocaching_hq":         "geocaching_hq_cache",
    "geocaching_hq_celebration": "geocaching_hq_celebration",
    "geocaching_hq_block_party": "geocaching_hq_block_party",
}

# Mapping: intern nøgle → smiley farvenavn (svarer til filnavne i cache_found/)
_FOUND_COLOR_MAP: dict[str, str] = {
    "traditional":           "green",
    "multi":                 "orange",
    "mystery":               "dark_blue",
    "letterbox":             "brown",
    "wherigo":               "teal",
    "earthcache":            "dark_green",
    "virtual":               "purple",
    "webcam":                "gray",
    "event":                 "red",
    "cito":                  "red",
    "mega_event":            "red",
    "giga_event":            "gold",
    "lab_cache":             "light_blue",
    "gps_adventures":        "navy_blue",
    "community_celebration": "maroon",
    "locationless":          "brown",
    "project_ape":           "dark_green",
    "geocaching_hq":         "dark_green",
    "geocaching_hq_celebration": "dark_green",
    "geocaching_hq_block_party": "dark_green",
    "unknown":               "gray",
}


# ── Mapping: database cache_type streng → intern nøgle ───────────────────────

_DB_TYPE_KEY_MAP: dict[str, str] = {
    "traditional cache":             "traditional",
    "multi-cache":                   "multi",
    "mystery cache":                 "mystery",
    "unknown cache":                 "mystery",
    "letterbox hybrid":              "letterbox",
    "wherigo cache":                 "wherigo",
    "earthcache":                    "earthcache",
    "virtual cache":                 "virtual",
    "webcam cache":                  "webcam",
    "event cache":                   "event",
    "cache in trash out event":      "cito",
    "mega-event cache":              "mega_event",
    "giga-event cache":              "giga_event",
    "lab cache":                     "lab_cache",
    "community celebration event":   "community_celebration",
    "gps adventures maze":           "gps_adventures",
    "gps adventures maze exhibit":   "gps_adventures",
    "gps adventures exhibit":        "gps_adventures",
    "locationless (reverse) cache":  "locationless",
    "project a.p.e. cache":          "project_ape",
    "groundspeak hq":                "geocaching_hq",
    "geocaching hq cache":           "geocaching_hq",
    "geocaching hq celebration":     "geocaching_hq_celebration",
    "geocaching hq block party":     "geocaching_hq_block_party",
}


# ── SVG fil læsning ───────────────────────────────────────────────────────────

@lru_cache(maxsize=128)
def _read_svg_file(path: Path) -> str | None:
    """Læs SVG fil og returner indhold som streng. None hvis filen ikke findes."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def _user_icons_dir() -> Path:
    """Sti til brugerens custom-icons mappe (issue #519), eller None ved fejl."""
    try:
        from opensak.config import get_icons_dir
        return get_icons_dir()
    except Exception:
        return None


def _read_svg_with_user_override(filename: str, bundled_dir: Path, user_subdir: str) -> str | None:
    """
    Læs en SVG fil, med brugerens custom-icons mappe (issue #519) tjekket
    FØRST, og fald tilbage til det bundlede asset hvis filen mangler eller
    ikke kan parses. Filnavnet matcher 1:1 den bundlede asset-struktur, så
    det matcher OpenSAK Custom Icons Guide.
    """
    user_dir = _user_icons_dir()
    if user_dir is not None:
        svg = _read_svg_file(user_dir / user_subdir / f"{filename}.svg")
        if svg:
            return svg
    return _read_svg_file(bundled_dir / f"{filename}.svg")


def _get_ui_icon_svg(filename: str, default_svg: str) -> str:
    """
    Hent SVG for et af de faste, enkeltstående UI-ikoner (Corrected
    coordinates, Premium, Fav. points, Trackables — issue #519 follow-up).

    Disse har intet bundlet asset-fil at falde tilbage til — kun en
    hardkodet SVG-streng i denne fil — så opslags-rækkefølgen er:
    brugerens icons/ui/<filename>.svg, ellers default_svg.
    """
    user_dir = _user_icons_dir()
    if user_dir is not None:
        svg = _read_svg_file(user_dir / "ui" / f"{filename}.svg")
        if svg:
            return svg
    return default_svg


def _get_type_svg(key: str) -> str | None:
    """Hent SVG streng for en cache type fra disk (bruger-override først)."""
    filename = _TYPE_FILE_MAP.get(key)
    if not filename:
        return None
    return _read_svg_with_user_override(filename, _CACHE_TYPES_DIR, "cache_types")


def _get_found_svg(key: str) -> str | None:
    """Hent smiley SVG streng for en fundet cache type fra disk (bruger-override først)."""
    color = _FOUND_COLOR_MAP.get(key, "green")
    filename = f"found_cache_smiley_{color}"
    return _read_svg_with_user_override(filename, _CACHE_FOUND_DIR, "cache_found")


# ── Internal helpers (fallback SVG strenge) ───────────────────────────────────

def _size_circle(label: str, fill: str = "#3498db", stroke: str = "#2980b9") -> str:
    """Generate a circular size-badge SVG."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        f'<circle cx="16" cy="16" r="13" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
        f'<text x="16" y="21" text-anchor="middle" font-size="11" font-weight="700" '
        f'fill="white" font-family="sans-serif">{label}</text>'
        f'</svg>'
    )


def _box(fill: str, stroke: str, inner: str = "") -> str:
    """Standard cache box with lid SVG."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        f'<rect x="4" y="8" width="24" height="7" rx="2" fill="{stroke}" stroke="{stroke}" stroke-width="0.5"/>'
        f'<rect x="6" y="14" width="20" height="13" rx="2" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
        f'<rect x="13" y="17" width="6" height="4" rx="1" fill="white" opacity="0.75"/>'
        f'{inner}'
        f'</svg>'
    )


def _box_text(fill: str, stroke: str, text: str) -> str:
    """Box with text label over clasp."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        f'<rect x="4" y="8" width="24" height="7" rx="2" fill="{stroke}" stroke="{stroke}" stroke-width="0.5"/>'
        f'<rect x="6" y="14" width="20" height="13" rx="2" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
        f'<text x="16" y="24" text-anchor="middle" font-size="9" font-weight="700" '
        f'fill="white" font-family="sans-serif">{text}</text>'
        f'</svg>'
    )


def _calendar(badge: str, extra: str = "") -> str:
    """Red event calendar SVG with badge text and optional extra SVG elements."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        f'<rect x="4" y="9" width="24" height="19" rx="2" fill="#e74c3c" stroke="#c0392b" stroke-width="1"/>'
        f'<rect x="4" y="9" width="24" height="8" rx="2" fill="#c0392b"/>'
        f'<circle cx="11" cy="9" r="2.5" fill="#7f8c8d"/>'
        f'<circle cx="21" cy="9" r="2.5" fill="#7f8c8d"/>'
        f'<text x="16" y="24" text-anchor="middle" font-size="9" font-weight="700" '
        f'fill="white" font-family="sans-serif">{badge}</text>'
        f'{extra}'
        f'</svg>'
    )


# ── Fallback SVG data (bruges hvis SVG filer ikke findes) ─────────────────────

_FALLBACK_SVGS: dict[str, str] = {
    "traditional":           _box("#2ecc71", "#27ae60"),
    "multi":                 _box_text("#e67e22", "#ca6f1e", "2+"),
    "mystery":               _box_text("#3498db", "#2980b9", "?"),
    "letterbox": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="4" y="8" width="24" height="7" rx="2" fill="#7b3f00" stroke="#7b3f00" stroke-width="0.5"/>'
        '<rect x="6" y="14" width="20" height="13" rx="2" fill="#a0522d" stroke="#7b3f00" stroke-width="1"/>'
        '<circle cx="12" cy="21" r="3.5" fill="#d4a76a" opacity="0.9"/>'
        '<circle cx="20" cy="21" r="3.5" fill="#d4a76a" opacity="0.9"/>'
        '</svg>'
    ),
    "wherigo": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="4" y="8" width="24" height="7" rx="2" fill="#17a589" stroke="#17a589" stroke-width="0.5"/>'
        '<rect x="6" y="14" width="20" height="13" rx="2" fill="#1abc9c" stroke="#17a589" stroke-width="1"/>'
        '<polygon points="16,16 21,26 16,23 11,26" fill="white" opacity="0.9"/>'
        '</svg>'
    ),
    "earthcache": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<circle cx="16" cy="18" r="12" fill="#27ae60" stroke="#1e8449" stroke-width="1"/>'
        '<ellipse cx="12" cy="15" rx="5" ry="7" fill="#f0e68c" opacity="0.85"/>'
        '<ellipse cx="20" cy="20" rx="4" ry="5" fill="#f0e68c" opacity="0.85"/>'
        '</svg>'
    ),
    "virtual": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="4" y="8" width="24" height="7" rx="2" fill="none" stroke="#8e44ad" '
        'stroke-width="2" stroke-dasharray="3,2"/>'
        '<rect x="6" y="14" width="20" height="13" rx="2" fill="none" stroke="#8e44ad" '
        'stroke-width="1.5" stroke-dasharray="3,2"/>'
        '<text x="16" y="24" text-anchor="middle" font-size="10" font-weight="700" '
        'fill="#8e44ad" font-family="sans-serif">V</text>'
        '</svg>'
    ),
    "webcam": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="3" y="11" width="20" height="14" rx="3" fill="#7f8c8d" stroke="#616a6b" stroke-width="1"/>'
        '<rect x="23" y="14" width="7" height="5" rx="1" fill="#95a5a6" stroke="#616a6b" stroke-width="0.8"/>'
        '<circle cx="13" cy="18" r="5" fill="#95a5a6" stroke="#616a6b" stroke-width="0.8"/>'
        '<circle cx="13" cy="18" r="3" fill="#2c3e50"/>'
        '<circle cx="13" cy="18" r="1.5" fill="#3498db"/>'
        '</svg>'
    ),
    "lab_cache": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="12" y="3" width="8" height="7" rx="1.5" fill="#9b59b6" stroke="#7d3c98" stroke-width="1"/>'
        '<path d="M12,9 L6,24 Q6,29 16,29 Q26,29 26,24 L20,9 Z" '
        'fill="#d7bde2" stroke="#9b59b6" stroke-width="1.2"/>'
        '<path d="M8,20 Q8,29 16,29 Q24,29 24,20 Z" fill="#9b59b6" opacity="0.8"/>'
        '<circle cx="13" cy="17" r="2" fill="white" opacity="0.7"/>'
        '<circle cx="19" cy="22" r="1.5" fill="white" opacity="0.7"/>'
        '</svg>'
    ),
    "gps_adventures": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="3" y="3" width="26" height="26" rx="3" fill="#16a085" stroke="#0e6655" stroke-width="1"/>'
        '<polyline points="7,7 7,16 13,16 13,10 19,10 19,20 25,20 25,25 16,25" '
        'fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="7" cy="7" r="2.5" fill="#f1c40f"/>'
        '<circle cx="16" cy="25" r="2.5" fill="#e74c3c"/>'
        '</svg>'
    ),
    "event":                 _calendar("E"),
    "cito":                  _calendar("C"),
    "mega_event":            _calendar("M"),
    "giga_event":            _calendar(
        "G",
        '<polygon points="16,2 17.2,6 21,6 18,8.5 19.2,12.5 16,10 12.8,12.5 14,8.5 11,6 14.8,6" '
        'fill="#f1c40f" stroke="#e67e22" stroke-width="0.5"/>',
    ),
    "community_celebration": _calendar(
        "CC",
        '<circle cx="5" cy="7" r="2" fill="#f1c40f"/>'
        '<circle cx="27" cy="7" r="2" fill="#2ecc71"/>'
        '<circle cx="3" cy="19" r="2" fill="#3498db"/>'
        '<circle cx="29" cy="19" r="2" fill="#9b59b6"/>',
    ),
    "found": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="4" y="8" width="24" height="7" rx="2" fill="#27ae60" stroke="#27ae60" stroke-width="0.5"/>'
        '<rect x="6" y="14" width="20" height="13" rx="2" fill="#2ecc71" stroke="#27ae60" stroke-width="1"/>'
        '<rect x="13" y="17" width="6" height="4" rx="1" fill="white" opacity="0.75"/>'
        '<circle cx="25" cy="9" r="7" fill="#f39c12" stroke="white" stroke-width="1.5"/>'
        '<polyline points="21,9 24,12 29,6" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"/>'
        '</svg>'
    ),
    "dnf": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="4" y="8" width="24" height="7" rx="2" fill="#922b21" stroke="#922b21" stroke-width="0.5"/>'
        '<rect x="6" y="14" width="20" height="13" rx="2" fill="#c0392b" stroke="#922b21" stroke-width="1"/>'
        '<rect x="13" y="17" width="6" height="4" rx="1" fill="white" opacity="0.75"/>'
        '<circle cx="25" cy="9" r="7" fill="#e74c3c" stroke="white" stroke-width="1.5"/>'
        '<line x1="21" y1="5" x2="29" y2="13" stroke="white" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="29" y1="5" x2="21" y2="13" stroke="white" stroke-width="2" stroke-linecap="round"/>'
        '</svg>'
    ),
    "disabled": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="4" y="8" width="24" height="7" rx="2" fill="#7f8c8d" stroke="#616a6b" stroke-width="0.5"/>'
        '<rect x="6" y="14" width="20" height="13" rx="2" fill="#95a5a6" stroke="#7f8c8d" stroke-width="1"/>'
        '<line x1="4" y1="6" x2="28" y2="30" stroke="#e74c3c" stroke-width="2.5" stroke-linecap="round"/>'
        '</svg>'
    ),
    "archived": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="4" y="8" width="24" height="7" rx="2" fill="#616a6b" stroke="#4a4a4a" stroke-width="0.5"/>'
        '<rect x="6" y="14" width="20" height="13" rx="2" fill="#7f8c8d" stroke="#616a6b" stroke-width="1"/>'
        '<line x1="10" y1="18" x2="22" y2="18" stroke="white" stroke-width="1.5" opacity="0.8"/>'
        '<line x1="10" y1="21" x2="22" y2="21" stroke="white" stroke-width="1.5" opacity="0.8"/>'
        '<line x1="10" y1="24" x2="22" y2="24" stroke="white" stroke-width="1.5" opacity="0.8"/>'
        '</svg>'
    ),
    "unknown": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
        '<rect x="4" y="8" width="24" height="7" rx="2" fill="#95a5a6" stroke="#7f8c8d" stroke-width="0.5"/>'
        '<rect x="6" y="14" width="20" height="13" rx="2" fill="#bdc3c7" stroke="#95a5a6" stroke-width="1"/>'
        '<text x="16" y="24" text-anchor="middle" font-size="10" font-weight="700" '
        'fill="#7f8c8d" font-family="sans-serif">?</text>'
        '</svg>'
    ),
}

_CACHE_SIZE_SVGS: dict[str, str] = {
    "nano":       _size_circle("N"),
    "micro":      _size_circle("Mi"),
    "small":      _size_circle("S"),
    "regular":    _size_circle("R"),
    "large":      _size_circle("L"),
    "other":      _size_circle("?",  "#95a5a6", "#7f8c8d"),
    "not_chosen": _size_circle("—",  "#bdc3c7", "#95a5a6"),
}

# ── Map pin farver (til Leaflet divIcon pins) ─────────────────────────────────

_PIN_COLORS: dict[str, str] = {
    "traditional":           "#2ecc71",
    "multi":                 "#e67e22",
    "mystery":               "#3498db",
    "letterbox":             "#a0522d",
    "wherigo":               "#1abc9c",
    "earthcache":            "#27ae60",
    "virtual":               "#8e44ad",
    "webcam":                "#7f8c8d",
    "event":                 "#e74c3c",
    "cito":                  "#e74c3c",
    "mega_event":            "#e74c3c",
    "giga_event":            "#e74c3c",
    "lab_cache":             "#9b59b6",
    "gps_adventures":        "#16a085",
    "community_celebration": "#e74c3c",
    "found":                 "#f39c12",
    "dnf":                   "#c0392b",
    "disabled":              "#95a5a6",
    "archived":              "#616a6b",
    "unknown":               "#bdc3c7",
}

_PIN_LABELS: dict[str, str] = {
    "traditional":           "",
    "multi":                 "2+",
    "mystery":               "?",
    "letterbox":             "LB",
    "wherigo":               "W",
    "earthcache":            "E",
    "virtual":               "V",
    "webcam":                "",
    "event":                 "E",
    "cito":                  "C",
    "mega_event":            "M",
    "giga_event":            "G",
    "lab_cache":             "L",
    "gps_adventures":        "GPS",
    "community_celebration": "CC",
    "found":                 "✓",
    "dnf":                   "✗",
    "disabled":              "—",
    "archived":              "A",
    "unknown":               "?",
}


# ── Qt rendering ──────────────────────────────────────────────────────────────

def _svg_to_pixmap(svg_data: str, size: int = 32) -> QPixmap:
    """Render SVG string to QPixmap at given pixel size."""
    renderer = QSvgRenderer(QByteArray(svg_data.encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def _normalize_key(raw: str) -> str:
    return (raw or "").lower().replace(" ", "_").replace("-", "_")


def _db_type_to_key(cache_type: str) -> str:
    """Konverter database cache_type streng til intern nøgle."""
    return _DB_TYPE_KEY_MAP.get((cache_type or "").lower(), _normalize_key(cache_type))


def _get_svg_for_key(key: str) -> str:
    """Hent SVG for en intern type-nøgle. Prøver fil først, derefter fallback."""
    svg = _get_type_svg(key)
    if svg:
        return svg
    return _FALLBACK_SVGS.get(key, _FALLBACK_SVGS["unknown"])


def _get_found_svg_for_key(key: str) -> str:
    """Hent smiley SVG for en fundet cache type. Prøver fil først, derefter fallback."""
    svg = _get_found_svg(key)
    if svg:
        return svg
    return _FALLBACK_SVGS.get("found", _FALLBACK_SVGS["unknown"])


def _get_found_overlay_svg() -> str:
    """Gold smiley used as the fixed overlay for found caches (all types)."""
    svg = _read_svg_with_user_override("found_cache_smiley_gold", _CACHE_FOUND_DIR, "cache_found")
    return svg if svg else _FALLBACK_SVGS.get("found", _FALLBACK_SVGS["unknown"])


def _get_dnf_overlay_svg() -> str:
    """Dark-blue smiley used as the fixed overlay for DNF caches (all types)."""
    svg = _read_svg_with_user_override("found_cache_smiley_dark_blue", _CACHE_FOUND_DIR, "cache_found")
    return svg if svg else _FALLBACK_SVGS.get("found", _FALLBACK_SVGS["unknown"])


# ── Public API ────────────────────────────────────────────────────────────────

def get_cache_type_icon(cache_type: str, size: int = 32, found: bool = False) -> QIcon:
    """
    Return QIcon for en cache type.

    cache_type kan være enten:
    - En database streng som "Traditional Cache", "Multi-cache" osv.
    - En intern nøgle som "traditional", "multi" osv.
    - En status som "found", "dnf", "disabled", "archived"

    Hvis found=True returneres smiley-ikonet for typen.
    """
    key = _db_type_to_key(cache_type)
    if found:
        svg = _get_found_svg_for_key(key)
    else:
        svg = _get_svg_for_key(key)
    return QIcon(_svg_to_pixmap(svg, size))


def get_cache_size_icon(cache_size: str, size: int = 32) -> QIcon:
    """
    Return QIcon for en cache container størrelse.

    Kendte nøgler: nano, micro, small, regular, large, other, not_chosen.
    Falder tilbage til "other" for ukendte størrelser.
    """
    svg = _CACHE_SIZE_SVGS.get(_normalize_key(cache_size), _CACHE_SIZE_SVGS["other"])
    return QIcon(_svg_to_pixmap(svg, size))


def get_cache_type_pixmap(cache_type: str, size: int = 32, found: bool = False) -> QPixmap:
    """Return QPixmap for en cache type — nyttigt til composite map overlays."""
    key = _db_type_to_key(cache_type)
    if found:
        svg = _get_found_svg_for_key(key)
    else:
        svg = _get_svg_for_key(key)
    return _svg_to_pixmap(svg, size)


def get_map_pin_html(cache_type: str, found: bool = False, dnf: bool = False) -> str:
    """
    Return HTML streng til en Leaflet divIcon map pin.
    Viser cache type ikon som base med smiley overlay i øverste højre hjørne:
      found=True → gold smiley; dnf=True (og ikke found) → dark-blue smiley.
    Bruger base64 <img> tag så SVG farver bevares korrekt i browser.
    """
    import base64

    key = _db_type_to_key(cache_type)

    svg = _get_svg_for_key(key)
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    img_html = (
        f'<img src="data:image/svg+xml;base64,{b64}" '
        f'width="32" height="32" style="display:block;"/>'
    )

    if found:
        overlay_svg = _get_found_overlay_svg()
    elif dnf:
        overlay_svg = _get_dnf_overlay_svg()
    else:
        overlay_svg = None

    if overlay_svg:
        overlay_b64 = base64.b64encode(overlay_svg.encode("utf-8")).decode("ascii")
        overlay = (
            f'<img src="data:image/svg+xml;base64,{overlay_b64}" '
            f'width="16" height="16" '
            f'style="position:absolute;top:-4px;right:-4px;display:block;'
            f'filter:drop-shadow(0 1px 1px rgba(0,0,0,0.5));"/>'
        )
    else:
        overlay = ""

    return (
        f'<div style="position:relative;width:32px;height:32px;'
        f'filter:drop-shadow(0 1px 3px rgba(0,0,0,0.4));">'
        f'{img_html}'
        f'{overlay}'
        f'</div>'
    )


def get_cache_type_pixmap_composite(
    cache_type: str,
    size: int = 32,
    found: bool = False,
    dnf: bool = False,
) -> QPixmap:
    """
    Return QPixmap with the cache type icon and, if found or dnf, a smiley
    overlay centred on the icon's top-right corner (half inside, half outside)
    — matching the map pin appearance.
      found → gold smiley; dnf (and not found) → dark-blue smiley.
    """
    key = _db_type_to_key(cache_type)
    base = _svg_to_pixmap(_get_svg_for_key(key), size)

    if found:
        overlay_svg = _get_found_overlay_svg()
    elif dnf:
        overlay_svg = _get_dnf_overlay_svg()
    else:
        return base

    overlay_size = max(8, size // 2)
    half = overlay_size // 2

    # Canvas is enlarged by `half` on the right and top so the smiley can
    # straddle the icon's top-right corner (center of smiley = corner of icon).
    canvas_w = size + half
    canvas_h = size + half
    canvas = QPixmap(canvas_w, canvas_h)
    canvas.fill(Qt.GlobalColor.transparent)

    overlay_px = _svg_to_pixmap(overlay_svg, overlay_size)

    painter = QPainter(canvas)
    painter.drawPixmap(0, half, base)                        # icon shifted down
    painter.drawPixmap(size - half, 0, overlay_px)          # smiley at top-right
    painter.end()
    return canvas


def get_all_type_keys() -> list[str]:
    """Return sorteret liste af alle kendte cache type nøgler."""
    return sorted(set(list(_FALLBACK_SVGS.keys()) + list(_TYPE_FILE_MAP.keys())))


def get_all_size_keys() -> list[str]:
    """Return sorteret liste af alle kendte cache størrelse nøgler."""
    return sorted(_CACHE_SIZE_SVGS.keys())


_FLAG_PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    '<line x1="4" y1="2" x2="4" y2="14" stroke="#aaaaaa" stroke-width="1.5"'
    ' stroke-linecap="round"/>'
    '<polygon points="4,3 13,6 4,9" fill="none" stroke="#aaaaaa" stroke-width="1"'
    ' stroke-linejoin="round"/>'
    '</svg>'
)


_LOCK_PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    '<rect x="3.5" y="7" width="9" height="6.5" rx="1" fill="none"'
    ' stroke="#aaaaaa" stroke-width="1.3"/>'
    '<path d="M5.5 7 V5 a2.5 2.5 0 0 1 5 0 V7" fill="none"'
    ' stroke="#aaaaaa" stroke-width="1.3"/>'
    '</svg>'
)


@lru_cache(maxsize=8)
def get_flag_placeholder_icon(size: int = 16) -> QIcon:
    """Faint outlined flag QIcon shown in the user_flag column when flag is unset."""
    return QIcon(_svg_to_pixmap(_FLAG_PLACEHOLDER_SVG, size))


# Issue #509: solid red flag QIcon shown when user_flag is set.
#
# The column previously rendered a "🚩" emoji as plain DisplayRole text.
# The cache_table FontRole handler italicizes every column for found caches
# (cache.found), and emoji glyphs generally have no real italic glyph —
# Qt/the OS font fallback synthesizes one by shearing the glyph box, which
# on Windows' Segoe UI Emoji clips/distorts the flag. Same class of bug as
# the old "📍"/"✓" text glyphs fixed in #354/#489: replaced with a proper
# icon-only column (DecorationRole) so no font style is ever applied to it.
_FLAG_SET_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    '<line x1="4" y1="2" x2="4" y2="14" stroke="#8a1c1c" stroke-width="1.5"'
    ' stroke-linecap="round"/>'
    '<polygon points="4,3 13,6 4,9" fill="#e53935" stroke="#8a1c1c" stroke-width="1"'
    ' stroke-linejoin="round"/>'
    '</svg>'
)


@lru_cache(maxsize=8)
def get_flag_icon(size: int = 16) -> QIcon:
    """Solid red flag QIcon shown in the user_flag column when flag is set (issue #509)."""
    return QIcon(_svg_to_pixmap(_FLAG_SET_SVG, size))


@lru_cache(maxsize=8)
def get_lock_placeholder_icon(size: int = 16) -> QIcon:
    """Faint outlined open-padlock QIcon shown in the locked column when unset (issue #202)."""
    return QIcon(_svg_to_pixmap(_LOCK_PLACEHOLDER_SVG, size))


# Issue #509 (follow-up): solid closed-padlock QIcon shown when locked is set.
#
# Same rationale as the flag fix above — the column previously rendered a
# "🔒" emoji as plain DisplayRole text, which the FontRole handler could
# italicize on found rows with the same synthetic-italic clipping risk on
# platforms without a real italic emoji glyph (e.g. Windows). Replaced with
# a proper icon-only column so no font style is ever applied to it.
_LOCK_SET_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    '<rect x="3.5" y="7" width="9" height="6.5" rx="1" fill="#555555"'
    ' stroke="#2b2b2b" stroke-width="1"/>'
    '<path d="M5.5 7 V5 a2.5 2.5 0 0 1 5 0 V7" fill="none"'
    ' stroke="#2b2b2b" stroke-width="1.3"/>'
    '<circle cx="8" cy="9.8" r="1" fill="#eeeeee"/>'
    '</svg>'
)


@lru_cache(maxsize=8)
def get_lock_icon(size: int = 16) -> QIcon:
    """Solid closed-padlock QIcon shown in the locked column when set (issue #509)."""
    return QIcon(_svg_to_pixmap(_LOCK_SET_SVG, size))


# ── Corrected-coordinates warning icon (issue #354) ────────────────────────────
#
# A generic amber warning-triangle with "!" — the universal caution symbol used
# across road signage, operating systems and web standards. It is not unique to
# any single application, so it carries no third-party IP concerns.
#
# Replaces the plain "📍" emoji previously used in the "corrected" table column,
# which rendered too small / low-contrast on some platforms (missing or
# monochrome emoji fonts on certain Linux distros in particular).

_CORRECTED_COORDS_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    '<polygon points="8,1.3 15.3,14.5 0.7,14.5" '
    'fill="#ffc107" stroke="#7a5200" stroke-width="1" stroke-linejoin="round"/>'
    '<rect x="7.25" y="5.4" width="1.5" height="5.1" rx="0.65" fill="#3a2600"/>'
    '<rect x="7.25" y="11.4" width="1.5" height="1.5" rx="0.65" fill="#3a2600"/>'
    '</svg>'
)


@lru_cache(maxsize=8)
def get_corrected_coords_icon(size: int = 16) -> QIcon:
    """Amber warning-triangle QIcon shown in the 'corrected' column when set (issue #354).
    Overridable via icons/ui/corrected_coords.svg (issue #519 follow-up)."""
    return QIcon(_svg_to_pixmap(_get_ui_icon_svg("corrected_coords", _CORRECTED_COORDS_SVG), size))


# ── Issue #489: GSAK-style icons for Found / Premium / Fav. points / Trackables columns ──
#
# All four are icon-only column headers (matching the existing "corrected"
# column pattern) with per-cell icons for Found/Premium (boolean toggles)
# and plain numeric text for Fav. points/Trackables (counts) — see
# cache_table.py's headerData()/_decoration_value() for how each is used.
#
# The Premium and Trackable icons below are original, generic designs (a
# checkered circle and a ladybug-style insect silhouette respectively) —
# they do not reproduce Geocaching.com's actual trademarked Premium badge
# or Travel Bug dog-tag artwork.

@lru_cache(maxsize=8)
def get_found_icon(size: int = 16) -> QIcon:
    """Gold smiley QIcon for the 'found' column — reuses the same smiley
    asset already used for the map pin found-overlay, instead of a plain
    checkmark (issue #489)."""
    return QIcon(_svg_to_pixmap(_get_found_overlay_svg(), size))


_PREMIUM_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    '<circle cx="8" cy="8" r="7" fill="#ffffff" stroke="#333333" stroke-width="1"/>'
    '<path d="M 8 8 L 8 1 A 7 7 0 0 1 15 8 Z" fill="#222222"/>'
    '<path d="M 8 8 L 8 15 A 7 7 0 0 1 1 8 Z" fill="#222222"/>'
    '<rect x="4.7" y="6.2" width="6.6" height="4.6" rx="0.7" '
    'fill="#c8860d" stroke="#5c3d00" stroke-width="0.6"/>'
    '<rect x="4.7" y="5.2" width="6.6" height="1.9" rx="0.6" fill="#5c3d00"/>'
    '</svg>'
)


@lru_cache(maxsize=8)
def get_premium_icon(size: int = 16) -> QIcon:
    """Checkered circle with a small cache box — Premium-member-only marker
    for the 'premium_only' column (issue #489).
    Overridable via icons/ui/premium.svg (issue #519 follow-up)."""
    return QIcon(_svg_to_pixmap(_get_ui_icon_svg("premium", _PREMIUM_SVG), size))


_FAVORITE_POINTS_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    '<circle cx="8" cy="6.3" r="5.2" fill="#f5c400" stroke="#a67c00" stroke-width="1"/>'
    '<path d="M8 3.2 L9.1 5.5 L11.6 5.9 L9.8 7.6 L10.2 10.1 L8 8.9 L5.8 10.1 '
    'L6.2 7.6 L4.4 5.9 L6.9 5.5 Z" fill="#ffffff" stroke="#a67c00" stroke-width="0.4"/>'
    '<path d="M5.4 10.6 L3.6 15 L8 13.2 L12.4 15 L10.6 10.6 Z" '
    'fill="#c0392b" stroke="#7a2318" stroke-width="0.5"/>'
    '</svg>'
)


@lru_cache(maxsize=8)
def get_favorite_points_icon(size: int = 14) -> QIcon:
    """Gold star-medal emblem for the 'Fav. points' column header (issue #489).
    Overridable via icons/ui/favorite_points.svg (issue #519 follow-up)."""
    return QIcon(_svg_to_pixmap(_get_ui_icon_svg("favorite_points", _FAVORITE_POINTS_SVG), size))


_TRACKABLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    '<ellipse cx="8" cy="9.2" rx="5.2" ry="5.8" fill="#c0392b" stroke="#7a2318" stroke-width="0.8"/>'
    '<line x1="8" y1="3.6" x2="8" y2="14.8" stroke="#3a1a10" stroke-width="0.8"/>'
    '<circle cx="6" cy="6.5" r="1" fill="#1a1a1a"/>'
    '<circle cx="10" cy="6.5" r="1" fill="#1a1a1a"/>'
    '<circle cx="5.5" cy="10.5" r="1" fill="#1a1a1a"/>'
    '<circle cx="10.5" cy="10.5" r="1" fill="#1a1a1a"/>'
    '<circle cx="8" cy="12.5" r="1" fill="#1a1a1a"/>'
    '<circle cx="8" cy="4" r="2.6" fill="#2b1a12" stroke="#000000" stroke-width="0.4"/>'
    '<path d="M6.5 2.5 L5 0.8" stroke="#2b1a12" stroke-width="0.7" stroke-linecap="round"/>'
    '<path d="M9.5 2.5 L11 0.8" stroke="#2b1a12" stroke-width="0.7" stroke-linecap="round"/>'
    '</svg>'
)


@lru_cache(maxsize=8)
def get_trackable_icon(size: int = 14) -> QIcon:
    """Ladybug-style insect icon for the Trackables column header (issue #489/#491).
    Overridable via icons/ui/trackable.svg (issue #519 follow-up)."""
    return QIcon(_svg_to_pixmap(_get_ui_icon_svg("trackable", _TRACKABLE_SVG), size))
