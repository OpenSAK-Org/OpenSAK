"""
src/opensak/db/corrected_coords.py — Skriv korrigerede koordinater for en cache.

Fælles DB-skrivning for alle steder der gemmer korrigerede koordinater
med det samme (detaljepanel, cache-tabellens højreklik-menu, kortets
højreklik-menu), så UserNote-rækken altid opdateres ens.
"""

from __future__ import annotations
from typing import Optional

from opensak.db.database import get_session
from opensak.db.models import Cache, UserNote
from opensak.utils.types import GcCode


def set_corrected_coords(
    gc_code: GcCode, lat: Optional[float], lon: Optional[float],
    source: str = "user",
) -> bool:
    """Set (or clear, with lat/lon = None) corrected coordinates for gc_code.

    Creates the UserNote row if it is missing. is_corrected is only set when
    both lat and lon are given. *source* is recorded in corrected_source
    (#1140): "user" for the GUI, "macro" when a macro corrects; clearing
    the correction clears it. Returns False if the cache doesn't exist.
    """
    with get_session() as session:
        cache_row = session.query(Cache).filter_by(gc_code=gc_code).first()
        if cache_row is None:
            return False
        note = cache_row.user_note
        if note is None:
            note = UserNote(cache_id=cache_row.id)
            session.add(note)
        note.corrected_lat = lat
        note.corrected_lon = lon
        note.is_corrected = lat is not None and lon is not None
        note.corrected_source = source if note.is_corrected else None
    return True
