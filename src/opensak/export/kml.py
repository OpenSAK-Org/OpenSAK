"""
KML export for Google Maps / Google My Maps.

Produces a two-layer KML file:
  Layer 1 – Geocaches  (colour-coded by cache type)
  Layer 2 – Custom waypoints attached to those caches
"""

from __future__ import annotations

import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import IO, Iterable, Optional, Sized
from xml.sax.saxutils import escape

from ..db.models import Cache, Waypoint
from .cache_text import NO_TEXT, ExportText
from .file_export import Saver, save_file
from opensak.lang import tr

# ---------------------------------------------------------------------------
# Icon URLs (Google Maps built-in paddle icons)
# ---------------------------------------------------------------------------

_TYPE_ICONS: dict[str, str] = {
    "Traditional Cache":      "http://maps.google.com/mapfiles/kml/paddle/grn-circle.png",
    "Multi-cache":            "http://maps.google.com/mapfiles/kml/paddle/ylw-circle.png",
    "Mystery Cache":          "http://maps.google.com/mapfiles/kml/paddle/purple-circle.png",
    "Unknown Cache":          "http://maps.google.com/mapfiles/kml/paddle/purple-circle.png",
    "EarthCache":             "http://maps.google.com/mapfiles/kml/paddle/grn-diamond.png",
    "Virtual Cache":          "http://maps.google.com/mapfiles/kml/paddle/blu-diamond.png",
    "Letterbox Hybrid":       "http://maps.google.com/mapfiles/kml/paddle/org-circle.png",
    "Wherigo Cache":          "http://maps.google.com/mapfiles/kml/paddle/ltblu-circle.png",
    "Event Cache":            "http://maps.google.com/mapfiles/kml/paddle/red-circle.png",
    "Cache In Trash Out Event": "http://maps.google.com/mapfiles/kml/paddle/red-circle.png",
    "Mega-Event Cache":       "http://maps.google.com/mapfiles/kml/paddle/red-stars.png",
    "Giga-Event Cache":       "http://maps.google.com/mapfiles/kml/paddle/red-stars.png",
}
_DEFAULT_ICON  = "http://maps.google.com/mapfiles/kml/paddle/wht-circle.png"
_FOUND_ICON    = "http://maps.google.com/mapfiles/kml/paddle/go.png"
_WPT_ICON      = "http://maps.google.com/mapfiles/kml/shapes/placemark_square.png"

# Waypoint type → icon  (covers all types from CUSTOM_WP_TYPES + KNOWN_PREFIXES)
_WPT_TYPE_ICONS: dict[str, str] = {
    "Parking Area":           "http://maps.google.com/mapfiles/kml/shapes/parking_lot.png",
    "Parking":                "http://maps.google.com/mapfiles/kml/shapes/parking_lot.png",
    "Trailhead":              "http://maps.google.com/mapfiles/kml/shapes/hiker.png",
    "Final Location":         "http://maps.google.com/mapfiles/kml/paddle/red-stars.png",
    "Reference Point":        "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png",
    "Stages of a Multicache": "http://maps.google.com/mapfiles/kml/paddle/ylw-blank.png",
    "Stage":                  "http://maps.google.com/mapfiles/kml/paddle/ylw-blank.png",
    "Stage Point":            "http://maps.google.com/mapfiles/kml/paddle/ylw-blank.png",
    "Physical Stage":         "http://maps.google.com/mapfiles/kml/paddle/ylw-circle.png",
    "Virtual Stage":          "http://maps.google.com/mapfiles/kml/paddle/blu-circle.png",
    "Question to Answer":     "http://maps.google.com/mapfiles/kml/shapes/question.png",
    "Hotel/POI":              "http://maps.google.com/mapfiles/kml/shapes/lodging.png",
    "Waypoint":               "http://maps.google.com/mapfiles/kml/shapes/placemark_square.png",
    "Custom":                 "http://maps.google.com/mapfiles/kml/shapes/placemark_square.png",
    "Additional Waypoint":    "http://maps.google.com/mapfiles/kml/shapes/placemark_square.png",
    "Listed Coordinates":     "http://maps.google.com/mapfiles/kml/shapes/target.png",
    "Point":                  "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(text: str | None) -> str:
    """Return empty string for None, otherwise str."""
    return str(text) if text is not None else ""


def _cache_description(cache: Cache) -> str:
    """Build an HTML description string shown in the Google Maps pop-up."""
    lines: list[str] = []

    dt = f"D{cache.difficulty or '?'} / T{cache.terrain or '?'}"
    size = cache.container or "?"
    lines.append(f"<b>{_esc(cache.cache_type)}</b> &bull; {dt} &bull; {size}")

    if cache.placed_by:
        lines.append(f"By: {_esc(cache.placed_by)}")

    if cache.short_description:
        lines.append(f"<br/>{_esc(cache.short_description)}")

    if cache.encoded_hints:
        from opensak.hint_detect import split_hint, render_hint_breaks
        try:
            plain, _cipher = split_hint(cache.encoded_hints)
        except Exception:
            plain = cache.encoded_hints or ""
        # render_hint_breaks() must run AFTER _esc() — otherwise the "<br/>"
        # we insert here would itself get HTML-escaped away (issue #595).
        hint_html = render_hint_breaks(_esc(plain), html=True)
        lines.append(f"<br/><i>Hint: {hint_html}</i>")

    gc_url = f"https://www.geocaching.com/geocache/{_esc(cache.gc_code)}"
    lines.append(f"<br/><a href='{gc_url}'>{_esc(cache.gc_code)}</a>")

    return "<br/>".join(lines)


def _waypoint_description(wpt: Waypoint, gc_code: str) -> str:
    parts = []
    if wpt.wp_type:
        parts.append(f"<b>{_esc(wpt.wp_type)}</b>")
    if wpt.description:
        parts.append(_esc(wpt.description))
    if wpt.comment:
        parts.append(f"<i>{_esc(wpt.comment)}</i>")
    parts.append(f"({_esc(gc_code)})")
    return "<br/>".join(parts)


# ---------------------------------------------------------------------------
# Style builders
# ---------------------------------------------------------------------------

def _make_style(parent: ET.Element, style_id: str, icon_url: str, scale: float = 1.1) -> None:
    style = ET.SubElement(parent, "Style", id=style_id)
    icon_style = ET.SubElement(style, "IconStyle")
    ET.SubElement(icon_style, "scale").text = str(scale)
    icon = ET.SubElement(icon_style, "Icon")
    ET.SubElement(icon, "href").text = icon_url
    label_style = ET.SubElement(style, "LabelStyle")
    ET.SubElement(label_style, "scale").text = "0.8"


def _style_id_for_cache(cache: Cache) -> str:
    if cache.found:
        return "style_found"
    ctype = (cache.cache_type or "").replace(" ", "_").replace("-", "_")
    return f"style_{ctype}" if ctype else "style_default"


def _style_id_for_waypoint(wpt: Waypoint) -> str:
    wtype = (wpt.wp_type or "").replace(" ", "_").replace("-", "_")
    return f"style_wpt_{wtype}" if wtype else "style_wpt_default"


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------

_KML_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
    "  <Document>"
)
_KML_FOOTER = "\n  </Document>\n</kml>\n"


def _text_element(tag: str, text: str) -> ET.Element:
    elem = ET.Element(tag)
    elem.text = text
    return elem


def _style_elements() -> list[ET.Element]:
    """The <Style> elements for every cache and waypoint type."""
    holder = ET.Element("Document")
    _make_style(holder, "style_found", _FOUND_ICON, scale=1.2)
    _make_style(holder, "style_default", _DEFAULT_ICON)
    for ctype, icon in _TYPE_ICONS.items():
        sid = "style_" + ctype.replace(" ", "_").replace("-", "_")
        _make_style(holder, sid, icon)

    _make_style(holder, "style_wpt_default", _WPT_ICON, scale=0.9)
    for wtype, icon in _WPT_TYPE_ICONS.items():
        sid = "style_wpt_" + wtype.replace(" ", "_").replace("-", "_")
        _make_style(holder, sid, icon, scale=0.9)
    return list(holder)


def _placemark(name: str, description: str, style_id: str, lon, lat) -> ET.Element:
    pm = ET.Element("Placemark")
    ET.SubElement(pm, "name").text = name
    ET.SubElement(pm, "description").text = description
    ET.SubElement(pm, "styleUrl").text = f"#{style_id}"
    pt = ET.SubElement(pm, "Point")
    ET.SubElement(pt, "coordinates").text = f"{lon},{lat},0"
    return pm


def _folder_start(name: str, is_open: bool) -> str:
    """The opening of a <Folder> (two levels deep) with its name and open
    flag; its placemarks follow at level 3, then "\\n    </Folder>"."""
    return (
        "\n    <Folder>"
        f"\n      <name>{escape(name)}</name>"
        f"\n      <open>{'1' if is_open else '0'}</open>"
    )


def export_kml(
    caches: Iterable[Cache],
    output_path: str | Path,
    *,
    include_waypoints: bool = True,
    include_found: bool = True,
    progress_cb=None,
    use_corrected: bool = True,
    text: ExportText = NO_TEXT,
    save: Saver = save_file,
    total: Optional[int] = None,
    count: Optional[int] = None,
) -> int:
    """
    Write a KML file to *output_path* (with *save*, see Saver).

    Returns the number of caches exported: those left after *include_found*,
    including caches without coordinates (which get no placemark).

    progress_cb(done, total): optional per-cache callback for GUI progress.
    use_corrected=False always places caches at their original coordinates.
    *text* replaces cache names and pop-up descriptions (Lua macros).

    #1078: the file is streamed one placemark at a time, so exporting
    100,000+ caches no longer holds them all, or the whole document, in
    memory. *caches* may be any iterable (e.g. iter_caches_full()), read
    only while writing: pass *total*, the number of caches in it (for
    progress), and *count*, the number left after *include_found* (shown in
    the document description, which comes before the caches). Without
    *count*, *caches* is read into a list first to count them. The child
    waypoints, which come after all caches, are buffered in a temporary
    file. The default *save* writes to a temporary file that replaces
    *output_path* only once complete, so a failed export keeps the
    previous file.
    """
    from opensak.gps.garmin import _xml_chunk

    if count is None:
        cache_list = list(caches)
        caches = cache_list
        if total is None:
            total = len(cache_list)
        count = sum(1 for c in cache_list if include_found or not c.found)
    elif total is None and isinstance(caches, Sized):
        total = len(caches)

    def _write(out: IO[str]) -> int:
        out.write(_KML_HEADER)
        out.write("\n    " + _xml_chunk(_text_element("name", "OpenSAK geocaches"), 2))
        out.write("\n    " + _xml_chunk(
            _text_element("description", tr("kml_export_description", count=count)), 2))
        for style in _style_elements():
            out.write("\n    " + _xml_chunk(style, 2))

        # -------------------------------------------------------------------
        # Folder 1 — Geocaches. Their waypoints go to a temporary file for
        # folder 2, which has to come after all caches.
        # -------------------------------------------------------------------
        out.write(_folder_start("Geocaches", True))
        exported = 0
        has_waypoints = False
        with tempfile.TemporaryFile("w+", encoding="utf-8", newline="") as wpt_file:
            for i, cache in enumerate(caches, 1):
                if progress_cb:
                    progress_cb(i, total or 0)
                if not include_found and cache.found:
                    continue
                exported += 1

                # Use corrected coordinates if available, fall back to originals
                note = cache.user_note
                if use_corrected and note and note.is_corrected and note.corrected_lat is not None:
                    lat = note.corrected_lat
                    lon = note.corrected_lon
                else:
                    lat = cache.latitude
                    lon = cache.longitude

                if lat is None or lon is None:
                    continue  # skip caches with no coordinates

                out.write("\n      " + _xml_chunk(_placemark(
                    f"{'✓ ' if cache.found else ''}{_esc(cache.gc_code)} {text.name(cache)}",
                    text.description(cache) or _cache_description(cache),
                    _style_id_for_cache(cache), lon, lat,
                ), 3))

                # Waypoints for folder 2: only their finished placemarks are
                # kept, never the ORM objects.
                if include_waypoints and getattr(cache, "waypoints", None):
                    for wpt in cache.waypoints:
                        has_waypoints = True
                        if wpt.latitude is None or wpt.longitude is None:
                            continue
                        label = wpt.name or wpt.wp_type or "Waypoint"
                        wpt_file.write("\n      " + _xml_chunk(_placemark(
                            f"{_esc(label)} ({_esc(cache.gc_code)})",
                            _waypoint_description(wpt, cache.gc_code),
                            _style_id_for_waypoint(wpt),
                            wpt.longitude, wpt.latitude,
                        ), 3))
            out.write("\n    </Folder>")

            # ---------------------------------------------------------------
            # Folder 2 — Custom waypoints
            # ---------------------------------------------------------------
            if has_waypoints:
                out.write(_folder_start("Waypoints", False))
                wpt_file.seek(0)
                shutil.copyfileobj(wpt_file, out)
                out.write("\n    </Folder>")

        out.write(_KML_FOOTER)
        return exported

    return save(Path(output_path), _write, False)
