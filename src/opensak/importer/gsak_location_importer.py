"""
src/opensak/importer/gsak_location_importer.py — import GSAK's user
locations as OpenSAK user locations (issue #1001).

GSAK keeps its whole location list as one row in ``gsak.db3``::

    Settings(Type, Description, Data)
        Type        'LO'
        Description 'Location'
        Data        the list as plain text, one location per line

Every line of Data is ``<name>,<coordinate>``: the name ends at the first
comma, the rest is the coordinate in any format GSAK accepts — which may
itself contain a comma (``Zurich,47.371722, 8.537466``). Lines starting
with ``#`` are comments, blank lines are ignored. The coordinate is read with
the same ``parse_coords()`` the single-entry field in Settings uses, so what
can be typed there can be imported here. GSAK also takes a decimal comma
(``N47,1395 E7,243``, ``47,03555 8,25546``): when parse_coords() rejects a
coordinate, it is tried once more with those commas read as decimal points.

The parsed list is shown for review first (``GsakLocationImportDialog``);
``apply_locations()`` then writes the chosen ones into OpenSAK's global
location list.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# OpenSAK's own home point: built from the Geocaching profile, never stored
# in the location list, so an imported location can't take its name.
HOME_NAME = "★ Home"

# Why a line can't be imported (see GsakLocation.error)
ERROR_NO_NAME = "no_name"
ERROR_NO_COORD = "no_coord"
ERROR_BAD_COORD = "bad_coord"
ERROR_RESERVED = "reserved"
ERROR_DUPLICATE = "duplicate"


# A comma between two digits is a decimal comma ("47,03555"); the comma that
# separates latitude from longitude always has whitespace or a letter next to it.
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")


class GsakLocationSourceError(Exception):
    """gsak.db3 could not be read."""


@dataclass
class GsakLocation:
    """One non-comment line of GSAK's location list."""
    line_no: int
    text: str                       # the line as GSAK has it (trimmed)
    name: str = ""
    coord_text: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    error: Optional[str] = None     # ERROR_*; None when the line can be imported

    @property
    def valid(self) -> bool:
        return self.error is None


@dataclass
class LocationImportResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # name already existed


def load_gsak_locations(db_path: Path) -> str:
    """The location list stored in gsak.db3 (read-only); "" when there is none."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    try:
        # closing(): sqlite3's own context manager leaves the file open —
        # which on Windows blocks removing an unpacked gsak.db3.
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
            tables = {name.lower() for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "settings" not in tables:
                return ""
            rows = conn.execute(
                "SELECT Data FROM Settings WHERE Type = 'LO'").fetchall()
    except sqlite3.Error as exc:
        raise GsakLocationSourceError(str(exc)) from exc
    return "\n".join(str(data) for (data,) in rows if data)


def parse_location_coords(text: str) -> Optional[tuple[float, float]]:
    """parse_coords(), falling back to reading decimal commas as points.

    The fallback only runs when parse_coords() fails, so a coordinate it
    already accepts (``47.1,8.5``) is never reinterpreted.
    """
    from opensak.coords import parse_coords

    coords = parse_coords(text)
    if coords is None and _DECIMAL_COMMA.search(text):
        coords = parse_coords(_DECIMAL_COMMA.sub(".", text))
    return coords


def parse_gsak_locations(text: str) -> list[GsakLocation]:
    """Parse GSAK's location list: one entry per non-blank, non-comment line.

    Lines that can't be imported are kept, with ``error`` saying why, so they
    can be reported line by line. A name that occurs more than once keeps its
    first line; later ones are flagged ERROR_DUPLICATE.
    """
    locations: list[GsakLocation] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        loc = GsakLocation(line_no=line_no, text=line)
        name, _comma, coord = line.partition(",")
        loc.name, loc.coord_text = name.strip(), coord.strip()
        if not loc.name:
            loc.error = ERROR_NO_NAME
        elif loc.name == HOME_NAME:
            loc.error = ERROR_RESERVED
        elif loc.name in seen:
            loc.error = ERROR_DUPLICATE
        elif not loc.coord_text:
            loc.error = ERROR_NO_COORD
        else:
            coords = parse_location_coords(loc.coord_text)
            if coords is None:
                loc.error = ERROR_BAD_COORD
            else:
                loc.lat, loc.lon = coords
        if loc.name:
            seen.add(loc.name)
        locations.append(loc)
    return locations


def existing_location_names() -> set[str]:
    """Names of OpenSAK's stored user locations (★ Home isn't one of them)."""
    from opensak.gui.settings import get_settings
    return {p.name for p in get_settings().home_points if p.name != HOME_NAME}


def apply_locations(locations: list[GsakLocation], overwrite: bool) -> LocationImportResult:
    """Add *locations* to OpenSAK's location list in one write.

    A location whose name already exists (case-sensitive, like the Settings
    dialog) replaces the stored one when *overwrite* is set, otherwise it is
    skipped. Invalid entries are ignored. When the active location is
    overwritten, its new coordinates become the active centre.
    """
    from opensak.gui.settings import HomePoint, get_settings

    settings = get_settings()
    stored = [p for p in settings.home_points if p.name != HOME_NAME]
    index = {p.name: i for i, p in enumerate(stored)}
    result = LocationImportResult()
    active: Optional[HomePoint] = None
    for loc in locations:
        if not loc.valid or loc.lat is None or loc.lon is None:
            continue
        point = HomePoint(loc.name, loc.lat, loc.lon)
        if loc.name in index:
            if not overwrite:
                result.skipped.append(loc.name)
                continue
            stored[index[loc.name]] = point
            result.updated.append(loc.name)
            if settings.active_home_name == loc.name:
                active = point
        else:
            index[loc.name] = len(stored)
            stored.append(point)
            result.added.append(loc.name)
    if result.added or result.updated:
        settings.home_points = stored
        if active is not None:
            settings.set_active_home(active)
        settings.sync()
    return result
