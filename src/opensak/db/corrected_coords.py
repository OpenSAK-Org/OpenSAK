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
    gc_code: GcCode, lat: Optional[float], lon: Optional[float]
) -> bool:
    """Sæt (eller ryd, med lat/lon = None) korrigerede koordinater for gc_code.

    Opretter UserNote-rækken hvis den mangler. is_corrected sættes kun når
    både lat og lon er angivet. Returnerer False hvis cachen ikke findes.
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
    return True
