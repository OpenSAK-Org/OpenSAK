"""
src/opensak/export/poi_export.py — export caches and their child waypoints
as Garmin POI files (.gpi).

Garmin devices do not show the child waypoints (parking, trailheads,
stages …) of a GPX/GGZ file, but they show POI files on the map. This
module turns caches and/or child waypoints into POIs whose name,
description and extra text come from templates such as
"{name} by {by} ({dif}/{ter})" (variables: POI_VARIABLES), and writes them
with opensak.export.gpi.

Qt-free (apart from loading the icon image), so the POI export dialog (on a
worker thread) and Lua macros (opensak.export_poi) share it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from opensak.export.file_export_settings import expand_file_name
from opensak.export.gpi import GpiPoint, load_gpi_icon, write_gpi
from opensak.export.poi_export_settings import DEFAULT_CATEGORY, PoiExportSettings

# Variables a POI template may contain, in the order they are listed to the
# user. For a child waypoint, code/name/smart/type/comment/lat/lon/coords
# describe the waypoint and the others its cache. Names are matched
# case-insensitively; unknown ones are kept as typed.
POI_VARIABLES = (
    "code", "name", "smart", "type", "comment", "lat", "lon", "coords",
    "cache_code", "cache_name", "by", "owner", "dif", "ter", "size", "hint",
    "fav", "hidden", "country", "state", "county", "note",
    "data1", "data2", "data3", "data4",
)

_VARIABLE_RE = re.compile(r"\{(\w+)\}")
_INVALID_FILE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Words {smart} drops first when a name is too long.
_FILLER_WORDS = {
    "a", "an", "the", "of", "and", "at", "in", "on", "to",
    "der", "die", "das", "dem", "den", "des", "ein", "eine", "und", "im", "am",
    "le", "la", "les", "de", "du", "et", "en",
}
_VOWELS = set("aeiouyäöüåæøéèêàáíóú")


def smart_name(text: str, length: int) -> str:
    """Shorten *text* to at most *length* characters, keeping it readable.

    Similar to GSAK's smart names: short texts are kept as they are; longer
    ones lose punctuation and filler words, are written in CamelCase, lose
    vowels from the end and finally are cut off.
    """
    text = " ".join(text.split())
    if len(text) <= length:
        return text
    words = re.sub(r"[^\w\s]", " ", text).split()
    kept = [w for w in words if w.lower() not in _FILLER_WORDS] or words
    chars = list("".join(w[:1].upper() + w[1:] for w in kept))
    # Drop lower-case vowels from the end, never the first letter of a word.
    i = len(chars) - 1
    while len(chars) > length and i > 0:
        if chars[i] in _VOWELS:
            del chars[i]
        i -= 1
    return "".join(chars[:length])


def _num(value) -> str:
    return "" if value is None else f"{value:g}"


def _text(value) -> str:
    return "" if value is None else str(value)


def _effective_coords(cache, use_corrected: bool) -> tuple[float, float]:
    note = getattr(cache, "user_note", None)
    if use_corrected and note is not None and getattr(note, "is_corrected", False):
        if note.corrected_lat is not None and note.corrected_lon is not None:
            return note.corrected_lat, note.corrected_lon
    return cache.latitude, cache.longitude


def _cache_values(cache) -> dict[str, str]:
    note = getattr(cache, "user_note", None)
    hidden = getattr(cache, "hidden_date", None)
    return {
        "cache_code": _text(cache.gc_code),
        "cache_name": _text(cache.name),
        "by": _text(getattr(cache, "placed_by", None)),
        "owner": _text(getattr(cache, "owner_name", None) or getattr(cache, "placed_by", None)),
        "dif": _num(getattr(cache, "difficulty", None)),
        "ter": _num(getattr(cache, "terrain", None)),
        "size": _text(getattr(cache, "container", None)),
        "hint": _text(getattr(cache, "encoded_hints", None)),
        "fav": _text(getattr(cache, "favorite_points", None)),
        "hidden": hidden.strftime("%Y-%m-%d") if hidden else "",
        "country": _text(getattr(cache, "country", None)),
        "state": _text(getattr(cache, "state", None)),
        "county": _text(getattr(cache, "county", None)),
        "note": _text(getattr(note, "note", None) if note is not None else None),
        "data1": _text(getattr(cache, "user_data_1", None)),
        "data2": _text(getattr(cache, "user_data_2", None)),
        "data3": _text(getattr(cache, "user_data_3", None)),
        "data4": _text(getattr(cache, "user_data_4", None)),
    }


def _point_values(code: str, name: str, type_: str, comment: str,
                  lat: float, lon: float, smart_length: int) -> dict[str, str]:
    from opensak.coords import format_coords
    from opensak.utils.types import CoordFormat

    return {
        "code": code,
        "name": name,
        "smart": smart_name(name, smart_length),
        "type": type_,
        "comment": comment,
        "lat": f"{lat:.5f}",
        "lon": f"{lon:.5f}",
        "coords": " ".join(format_coords(lat, lon, CoordFormat.DMM).split()),
    }


def expand_poi_template(template: str, values: dict[str, str]) -> str:
    """Fill the {variables} of *template* from *values* (see POI_VARIABLES).
    Unknown variables are kept as typed."""

    def _sub(match: re.Match) -> str:
        return values.get(match.group(1).lower(), match.group(0))

    return _VARIABLE_RE.sub(_sub, template).strip()


def waypoint_code(cache, waypoint) -> str:
    """The code of a child waypoint: its own code, or prefix + the cache's
    code without "GC" (as in the GPX export)."""
    if getattr(waypoint, "wp_code", None):
        return waypoint.wp_code
    gc_code = cache.gc_code or ""
    return (waypoint.prefix or "WP") + (gc_code[2:] if len(gc_code) > 2 else "")


@dataclass
class PoiGroup:
    """The POIs of one file: its category and, in a per-type export, the
    waypoint type ("" for the main file)."""

    category: str
    waypoint_type: str = ""
    points: list[GpiPoint] = field(default_factory=list)


def _make_point(values: dict[str, str], lat: float, lon: float,
                settings: PoiExportSettings, proximity_m: int) -> GpiPoint:
    name = " ".join(expand_poi_template(settings.name, values).split())
    extra = expand_poi_template(settings.extra, values)
    if settings.extra_field == "phone":
        # The phone number is a single line on every device.
        extra = " ".join(extra.split())
    return GpiPoint(
        lat=lat, lon=lon,
        name=name or values.get("code", "") or "POI",
        description=expand_poi_template(settings.description, values),
        phone=extra if settings.extra_field == "phone" else "",
        address=extra if settings.extra_field == "address" else "",
        proximity_m=proximity_m,
    )


def build_poi_groups(caches: list, settings: PoiExportSettings) -> list[PoiGroup]:
    """Turn *caches* (fully loaded, see reload_caches_full) into POIs.

    Each cache is followed by its child waypoints. The max_points limit
    counts the POIs of all files together. Returns the groups that have
    POIs: one, or with split_by_type the caches first and then one group
    per waypoint type in order of first appearance.
    """
    proximity_m = max(0, min(settings.proximity_meters(), 0xFFFF))
    category = settings.category.strip() or DEFAULT_CATEGORY
    groups: dict[str, PoiGroup] = {"": PoiGroup(category)}
    points: list[tuple[str, GpiPoint]] = []   # (group key, point)

    for cache in caches:
        if cache.latitude is None or cache.longitude is None:
            continue
        cache_values = _cache_values(cache)
        if not settings.waypoints_only:
            lat, lon = _effective_coords(cache, settings.use_corrected_coords)
            values = cache_values | _point_values(
                cache.gc_code or "", cache.name or "", cache.cache_type or "", "",
                lat, lon, settings.smart_length,
            )
            points.append(("", _make_point(values, lat, lon, settings, proximity_m)))
        if not (settings.include_waypoints or settings.waypoints_only):
            continue
        for wp in getattr(cache, "waypoints", None) or []:
            if wp.latitude is None or wp.longitude is None:
                continue
            flagged = bool(getattr(wp, "wp_flag", False))
            if settings.waypoint_flag == "flagged" and not flagged:
                continue
            if settings.waypoint_flag == "unflagged" and flagged:
                continue
            wp_type = wp.wp_type or "Waypoint"
            values = cache_values | _point_values(
                waypoint_code(cache, wp), wp.name or wp.description or wp_type,
                wp_type, wp.comment or "", wp.latitude, wp.longitude,
                settings.smart_length,
            )
            point = _make_point(values, wp.latitude, wp.longitude, settings, proximity_m)
            points.append((wp_type if settings.split_by_type else "", point))
        if settings.max_points and len(points) >= settings.max_points:
            break

    if settings.max_points:
        points = points[:settings.max_points]
    for key, point in points:
        if key not in groups:
            groups[key] = PoiGroup(key, key)
        groups[key].points.append(point)
    return [g for g in groups.values() if g.points]


def poi_file_name(base_name: str, group: PoiGroup) -> str:
    """File name (with extension) for *group* in an export named *base_name*."""
    if not group.waypoint_type:
        return f"{base_name}.gpi"
    suffix = _INVALID_FILE_NAME_CHARS.sub("_", group.waypoint_type).strip().rstrip(". ")
    return f"{base_name} - {suffix}.gpi"


@dataclass
class PoiFile:
    """A POI file an export is about to write."""

    path: Path
    group: PoiGroup


def plan_poi_export(
    caches: list,
    settings: PoiExportSettings,
    folder: Path,
    *,
    database: str = "",
    filter_name: str = "",
    center_name: str = "",
) -> list[PoiFile]:
    """The files exporting *caches* into *folder* writes, with their POIs.

    The caches are reloaded with their waypoints first (see
    reload_caches_full). The file name template is expanded with the file
    export variables, {format} being "gpi" and {count} the number of POIs.
    """
    from opensak.db.database import reload_caches_full

    caches = reload_caches_full([c for c in caches if c.latitude is not None])
    groups = build_poi_groups(caches, settings)
    if not groups:
        return []
    base_name = expand_file_name(
        settings.file_name,
        database=database,
        filter_name=filter_name,
        center_name=center_name,
        fmt="gpi",
        count=sum(len(g.points) for g in groups),
    )
    return [PoiFile(folder / poi_file_name(base_name, g), g) for g in groups]


def settings_icon_path(settings: PoiExportSettings) -> Optional[Path]:
    """The icon image of *settings*, or None for the device's default icon."""
    return Path(settings.icon.strip()).expanduser() if settings.icon.strip() else None


def write_poi_files(files: list[PoiFile], icon_path: Optional[Path] = None) -> list[tuple[Path, int]]:
    """Write *files* (see plan_poi_export), all with the icon *icon_path*.
    Returns (file, number of POIs) for each. Raises ValueError if the icon
    is no readable image (before anything is written)."""
    icon = load_gpi_icon(icon_path) if icon_path is not None else None
    for f in files:
        write_gpi(f.path, f.group.points, f.group.category, icon)
    return [(f.path, len(f.group.points)) for f in files]


def write_poi_export(
    caches: list,
    settings: PoiExportSettings,
    folder: Path,
    *,
    database: str = "",
    filter_name: str = "",
    center_name: str = "",
    should_write: Optional[Callable[[Path], bool]] = None,
    icon_path: Optional[Path] = None,
) -> list[tuple[Path, int]]:
    """Export *caches* as POI files into *folder* (plan_poi_export, then
    write_poi_files).

    *should_write(path)* is asked for every file that already exists; the
    file is left alone when it returns False (default: overwrite).
    *icon_path* overrides settings.icon (e.g. a path a macro checked).

    Returns (file, number of POIs) for every file written.
    """
    files = plan_poi_export(
        caches, settings, folder,
        database=database, filter_name=filter_name, center_name=center_name,
    )
    if should_write is not None:
        files = [f for f in files if not f.path.exists() or should_write(f.path)]
    if not files:
        return []
    return write_poi_files(files, icon_path or settings_icon_path(settings))
