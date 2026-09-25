"""
src/opensak/app_control.py — Genkend Windows' Smart App Control-blokeringer.

Issue #904: Windows 11's Smart App Control (SAC) nægter at indlæse usignerede
binærfiler, som Microsoft ikke har "omdømme" for. Det ramte den direkte
Windows-download, da SQLAlchemy 2.1's kompilerede moduler var helt nye:
Python rejste

    ImportError: DLL load failed while importing _cache_key_cy:
    An Application Control policy has blocked this file.

og OpenSAK meldte blot "kunne ikke åbne din database". Buildet undgår nu de
kompilerede SQLAlchemy-moduler, men enhver anden ny, usigneret fil kan i
princippet blive blokeret på samme måde — så ved opstart genkender vi
fejltypen og peger brugeren mod Microsoft Store-versionen, som er signeret
af Microsoft og ikke blokeres.

Genkendelsen kan ikke stole på fejlteksten alene: Windows' del af beskeden
er oversat til brugerens sprog ("En politik for programkontrol har blokeret
denne fil"). Kun CPythons præfiks "DLL load failed" er altid engelsk, så vi
kombinerer det med SAC's faktiske tilstand fra registreringsdatabasen.
Holdt fri af Qt, så den kan unit-testes på alle platforme.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

# WinError 4551 = ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION
# ("An Application Control policy has blocked this file").
APP_CONTROL_WINERROR = 4551

_SAC_KEY = r"SYSTEM\CurrentControlSet\Control\CI\Policy"
_SAC_VALUE = "VerifiedAndReputablePolicyState"
SAC_OFF, SAC_ON, SAC_EVALUATION = 0, 1, 2


def smart_app_control_state() -> int | None:
    """
    Smart App Control's tilstand: 0 = fra, 1 = til (blokerer), 2 = evaluering
    (blokerer ikke). None uden for Windows, eller hvis værdien ikke kan læses.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _SAC_KEY) as key:
            value, _type = winreg.QueryValueEx(key, _SAC_VALUE)
        return int(value)
    except (OSError, ValueError, TypeError):
        return None


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_app_control_block(exc: BaseException) -> bool:
    """
    True når *exc* (eller en årsag i dens kæde) er Windows App Control, der
    har blokeret en binærfil i OpenSAK.

    - OSError med winerror 4551 er entydigt.
    - "DLL load failed"-ImportError er det, hvis Windows' (engelske) tekst
      nævner Application Control, eller hvis Smart App Control står til Til —
      ellers kan en DLL-fejl have helt andre årsager (fx en manglende
      runtime), og så skal den almindelige fejlbesked vises.
    """
    if sys.platform != "win32":
        return False
    for e in _exception_chain(exc):
        if isinstance(e, OSError) and getattr(e, "winerror", None) == APP_CONTROL_WINERROR:
            return True
        if isinstance(e, ImportError) and str(e).startswith("DLL load failed"):
            if "Application Control" in str(e) or smart_app_control_state() == SAC_ON:
                return True
    return False
