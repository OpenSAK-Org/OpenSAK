"""
KML export for Google Maps / Google My Maps.

Produces a two-layer KML file:
  Layer 1 – Geocaches  (colour-coded by cache type)
  Layer 2 – Custom waypoints attached to those caches
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable
from xml.dom import minidom

from ..db.models import Cache, Waypoint

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
        from opensak.hint_detect import split_hint
        try:
            plain, _cipher = split_hint(cache.encoded_hints)
        except Exception:
            plain = cache.encoded_hints or ""
        lines.append(f"<br/><i>Hint: {_esc(plain)}</i>")

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

def export_kml(
    caches: Iterable[Cache],
    output_path: str | Path,
    *,
    include_waypoints: bool = True,
    include_found: bool = True,
    progress_cb=None,
) -> int:
    """
    Write a KML file to *output_path*.

    Returns the number of caches written.

    progress_cb(done, total): optional per-cache callback for GUI progress.
    """
    cache_list = list(caches)
    if not include_found:
        cache_list = [c for c in cache_list if not c.found]

    # -----------------------------------------------------------------------
    # Root
    # -----------------------------------------------------------------------
    kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    doc = ET.SubElement(kml, "Document")
    ET.SubElement(doc, "name").text = "OpenSAK geocaches"
    ET.SubElement(doc, "description").text = (
        f"Eksporteret fra OpenSAK — {len(cache_list)} cache(s)"
    )

    # -----------------------------------------------------------------------
    # Styles — caches
    # -----------------------------------------------------------------------
    _make_style(doc, "style_found", _FOUND_ICON, scale=1.2)
    _make_style(doc, "style_default", _DEFAULT_ICON)
    for ctype, icon in _TYPE_ICONS.items():
        sid = "style_" + ctype.replace(" ", "_").replace("-", "_")
        _make_style(doc, sid, icon)

    # Styles — waypoints
    _make_style(doc, "style_wpt_default", _WPT_ICON, scale=0.9)
    for wtype, icon in _WPT_TYPE_ICONS.items():
        sid = "style_wpt_" + wtype.replace(" ", "_").replace("-", "_")
        _make_style(doc, sid, icon, scale=0.9)

    # -----------------------------------------------------------------------
    # Folder 1 — Geocaches
    # -----------------------------------------------------------------------
    folder_caches = ET.SubElement(doc, "Folder")
    ET.SubElement(folder_caches, "name").text = "Geocaches"
    ET.SubElement(folder_caches, "open").text = "1"

    waypoints_to_export: list[tuple] = []  # (Waypoint, gc_code)

    total = len(cache_list)
    for i, cache in enumerate(cache_list, 1):
        if progress_cb:
            progress_cb(i, total)
        # Use corrected coordinates if available, fall back to originals
        note = cache.user_note
        if note and note.is_corrected and note.corrected_lat is not None:
            lat = note.corrected_lat
            lon = note.corrected_lon
        else:
            lat = cache.latitude
            lon = cache.longitude

        if lat is None or lon is None:
            continue  # skip caches with no coordinates

        pm = ET.SubElement(folder_caches, "Placemark")
        ET.SubElement(pm, "name").text = (
            f"{'✓ ' if cache.found else ''}{_esc(cache.gc_code)} {_esc(cache.name)}"
        )
        ET.SubElement(pm, "description").text = _cache_description(cache)
        ET.SubElement(pm, "styleUrl").text = f"#{_style_id_for_cache(cache)}"

        pt = ET.SubElement(pm, "Point")
        ET.SubElement(pt, "coordinates").text = f"{lon},{lat},0"

        # Collect waypoints for layer 2
        if include_waypoints and hasattr(cache, "waypoints") and cache.waypoints:
            for wpt in cache.waypoints:
                waypoints_to_export.append((wpt, cache.gc_code))

    # -----------------------------------------------------------------------
    # Folder 2 — Custom waypoints
    # -----------------------------------------------------------------------
    if include_waypoints and waypoints_to_export:
        folder_wpts = ET.SubElement(doc, "Folder")
        ET.SubElement(folder_wpts, "name").text = "Waypoints"
        ET.SubElement(folder_wpts, "open").text = "0"

        for wpt, gc_code in waypoints_to_export:
            if wpt.latitude is None or wpt.longitude is None:
                continue
            pm = ET.SubElement(folder_wpts, "Placemark")
            label = wpt.name or wpt.wp_type or "Waypoint"
            ET.SubElement(pm, "name").text = f"{_esc(label)} ({_esc(gc_code)})"
            ET.SubElement(pm, "description").text = _waypoint_description(wpt, gc_code)
            ET.SubElement(pm, "styleUrl").text = f"#{_style_id_for_waypoint(wpt)}"
            pt = ET.SubElement(pm, "Point")
            ET.SubElement(pt, "coordinates").text = f"{wpt.longitude},{wpt.latitude},0"

    # -----------------------------------------------------------------------
    # Write — pretty-printed XML
    # -----------------------------------------------------------------------
    raw = ET.tostring(kml, encoding="unicode")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pretty)

    return len(cache_list)
