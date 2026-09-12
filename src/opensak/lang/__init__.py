"""
src/opensak/lang/__init__.py — Internationalisering (i18n) for OpenSAK.

Brug:
    from opensak.lang import tr
    label = tr("search_label")   # → "Søg:" eller "Search:" afhængig af sprog

Tilføj nyt sprog:
    1. Opret src/opensak/lang/xx.py  (kopiér da.py og oversæt)
    2. Registrér sproget i AVAILABLE_LANGUAGES herunder
    3. Det dukker automatisk op i indstillinger
"""

from __future__ import annotations
from typing import Optional
from pathlib import Path

# ── Tilgængelige sprog ────────────────────────────────────────────────────────
# Nøgle: sprogkode (ISO 639-1), Værdi: navn vist i UI
AVAILABLE_LANGUAGES: dict[str, str] = {
    "da": "Dansk",
    "en": "English",
    "fr": "Français",
    "nl": "Nederlands",
    "pt": "Português",
    "cs": "Čeština",
    "se": "Svenska",
    "de": "Deutsch",
    "pl": "Polski",
    "es": "Español",
}

# ── Aktiv oversættelses-dict ──────────────────────────────────────────────────
_translations: dict[str, str] = {}
_current_lang: str = "da"


def load_language(lang_code: str) -> None:
    """
    Indlæs sproget med den givne kode.
    Kaldes fra app.py ved opstart.
    Falder tilbage til dansk hvis sproget ikke findes.
    """
    global _translations, _current_lang

    lang_dir = Path(__file__).parent
    lang_file = lang_dir / f"{lang_code}.py"

    if not lang_file.exists():
        lang_file = lang_dir / "da.py"
        lang_code = "da"

    # Indlæs sprogfilen som et Python-modul og hent STRINGS dict
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"opensak.lang.{lang_code}", lang_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    _translations = getattr(module, "STRINGS", {})
    _current_lang = lang_code


def tr(key: str, **kwargs) -> str:
    """
    Slå en oversættelsesnøgle op.
    Returnerer den oversatte streng, eller nøglen selv hvis ikke fundet.

    Understøtter formatering:
        tr("cache_added", gc_code="GC12345")
        → "Cache GC12345 tilføjet"
    """
    text = _translations.get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


def current_language() -> str:
    """Returner den aktuelle sprogkode, f.eks. 'da' eller 'en'."""
    return _current_lang


def language_name(lang_code: Optional[str] = None) -> str:
    """Returner det menneskelig-læsbare navn for et sprog."""
    code = lang_code or _current_lang
    return AVAILABLE_LANGUAGES.get(code, code)
