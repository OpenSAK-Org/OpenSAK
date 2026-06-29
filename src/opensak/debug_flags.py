"""
src/opensak/debug_flags.py — Per-modul debug-flag til logging-systemet.

Issue #232: kontrollerer hvilke moduler der producerer debug-output i
opensak.log uden at man skal ændre kode i selve modulet.

Arbejdsgang for nye funktioner:
  1. Ny feature tilføjet → sæt dets flag til True
  2. Kører stabilt over flere releases → sæt flaget til False
  3. Bug rapporteret → genaktiver flaget i næste release

Debug-linjer ligger permanent i koden, men er tavse indtil et flag aktiveres.
"""

from __future__ import annotations

# Hvert modul der ønsker debug-logging skal have en nøgle her.
# Nøglen bruges som logger-navn: logging.getLogger(f"opensak.{key}")
DEBUG_MODULES: dict[str, bool] = {
    "updater":          True,   # update checker — aktiveret for #204
    "importer":         False,  # GPX/PQ import
    "filter_engine":    False,  # filter beregning
    "map_widget":       False,  # Leaflet/kort
    "database":         False,  # SQLAlchemy queries
    "geo":              False,  # reverse geocoding (boundaries, packs, store)
}


def is_debug_enabled(module: str) -> bool:
    """
    Returner True hvis debug-logging er aktiveret for det givne modul.

    Ukendte moduler (ikke registreret i DEBUG_MODULES) returnerer altid
    False — undgår at en tastefejl i modulnavnet utilsigtet aktiverer
    debug-output et sted.
    """
    return DEBUG_MODULES.get(module, False)
