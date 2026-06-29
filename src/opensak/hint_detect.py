"""
src/opensak/hint_detect.py — heuristik til at afgøre om en gemt hint-streng
allerede er klartekst eller ægte ROT13-ciffertekst.

Baggrund (issue #329 — "Hints: encode/decode buttons are reversed"):

Geocaching.com's Pocket Query-GPX leverer i dag hints i klartekst i
<groundspeak:encoded_hints>, på trods af at tag-navnet antyder kodet
indhold. Ældre GSAK-eksporterede GPX-filer kan derimod stadig indeholde
ægte ROT13-ciffertekst (GSAK's eget "Decode hints"-eksportvalg lægger
ROT13 ind ved eksport). OpenSAK's detalje-panel og KML-eksport har
historisk antaget at feltet ALTID var kodet — det er forkert for moderne
PQ-data og gav et hint der virkede "bagvendt" (se issue).

Vi vil vise hints skjult som standard (spoiler-beskyttelse, samme ånd som
GSAK's UI), uanset om kildedata reelt er klartekst eller ciffertekst.
Derfor skal vi GÆTTE hvilken af de to mulige udgaver der er den læsbare.

Metode: ROT13 flytter altid vokalerne a/e/i/o/u om til konsonanterne
n/r/v/b/h (og omvendt — afbildningen er sin egen invers). Naturligt
sprog har markant højere vokaltæthed end sin egen ROT13-transformation,
SÅ LÆNGE strengen er lang nok til at enkelte bogstaver ikke dominerer
statistikken — for korte hints (typisk under ~18 bogstaver, hvilket er
de fleste rigtige geocaching-hints) kan et par n'er eller r'er i et
dansk/engelsk ord nemt vippe et simpelt vokal-forhold den forkerte vej.

Tærsklerne herunder (MIN_LETTERS_FOR_HEURISTIC, MIN_MARGIN) er valideret
mod 24 rigtige hints fra en faktisk "My Finds"-PQ (alle klartekst) samt
syntetiske lange ROT13-eksempler, og giver 0 fejl på den moderne
klartekst-case og kun enkelte fejl på meget lange kunstige legacy-cases.
For korte hints (under tærsklen) falder vi simpelthen tilbage til "raw er
allerede klartekst" — det er langt den almindeligste case i dag, og det
er stadig brugerens eget valg at klikke "Encode"/"Decode" hvis gættet er
forkert.
"""

from __future__ import annotations

_ROT13_TABLE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
)

# Bredt sæt af vokaler inkl. accenttegn for alle 8 understøttede sprog
# (da, en, fr, nl, pt, cs, se, de). Bruges kun til klassificerings-
# heuristikken — påvirker IKKE selve ROT13-transformationen, som kun
# rører almindelige ASCII-bogstaver (samme som hidtil).
_VOWELS = set(
    "aeiouyAEIOUY"
    "æøåÆØÅ"
    "äöüÄÖÜß"
    "àâéèêëîïôùûÿçÀÂÉÈÊËÎÏÔÙÛŸÇ"
    "áéíóúñÁÉÍÓÚÑ"
    "ěůÚŮĚ"
)

# Under denne længde er vokal-forholdet for usikkert et signal (få
# bogstaver kan vippe det helt den forkerte vej) — se docstring ovenfor.
_MIN_LETTERS_FOR_HEURISTIC = 18

# Hvor meget højere vokaltætheden i den roterede udgave skal være, før vi
# tror på at den (og ikke raw) er klarteksten. En lille positiv margin
# undgår at vippe på rent tilfældige forskelle nær 50/50.
_MIN_MARGIN = 0.02


def rot13(text: str) -> str:
    """ROT13-transformér en streng. Kun ASCII A-Z/a-z påvirkes (uændret
    fra den oprindelige implementering i cache_detail.py)."""
    return text.translate(_ROT13_TABLE)


def _vowel_density(text: str) -> tuple[float | None, int]:
    """Returnér (vokaltæthed, antal bogstaver). Tæthed er None hvis der
    ingen bogstaver er at måle på."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None, 0
    vowels = sum(1 for c in letters if c in _VOWELS)
    return vowels / len(letters), len(letters)


def split_hint(raw_hint: str) -> tuple[str, str]:
    """
    Afgør hvilken af *raw_hint* og dens ROT13-transformation der er den
    menneskeligt læsbare klartekst, og hvilken der er den skjulte/kodede
    udgave.

    Returnerer (plain, cipher):
      - plain  — altid den læsbare udgave (vis efter klik på "Decode")
      - cipher — altid den skjulte udgave (vis som standard)

    Tom streng ind giver ("", "") ud.
    """
    if not raw_hint:
        return "", ""

    rotated = rot13(raw_hint)
    raw_density, raw_letters = _vowel_density(raw_hint)

    if raw_density is None or raw_letters < _MIN_LETTERS_FOR_HEURISTIC:
        # For kort/ingen bogstaver til at vokal-heuristikken er troværdig.
        # Antag klartekst — det er normen for moderne PQ-data, og
        # brugeren kan altid klikke knappen hvis gættet er forkert.
        return raw_hint, rotated

    rotated_density, _ = _vowel_density(rotated)
    if rotated_density is not None and (rotated_density - raw_density) > _MIN_MARGIN:
        # raw_hint var faktisk ciffertekst (ægte ROT13, fx fra en gammel
        # GSAK-eksport) — den roterede udgave er den rigtige klartekst.
        return rotated, raw_hint

    return raw_hint, rotated
