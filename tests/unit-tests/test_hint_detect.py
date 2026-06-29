# tests/unit-tests/test_hint_detect.py — heuristik der afgør om en hint-
# streng allerede er klartekst eller ægte ROT13-ciffertekst (issue #329).

import pytest

from opensak.hint_detect import rot13, split_hint


def test_empty_hint_returns_empty():
    assert split_hint("") == ("", "")


def test_short_hint_defaults_to_plaintext():
    # Under MIN_LETTERS_FOR_HEURISTIC kan vokal-tæthed ikke skelne sikkert —
    # vi antager klartekst, da det er normen for moderne PQ-data.
    plain, cipher = split_hint("TV")
    assert plain == "TV"
    assert cipher == rot13("TV")


@pytest.mark.parametrize("hint", [
    # 24 ægte hints fra en rigtig "My Finds" PQ fra geocaching.com
    # (rapporteret af Allan/Fabio i issue #329) — alle er klartekst.
    "Brændeknude",
    "Ved træstamme (Geopinde)",
    "der er store hytter og der er små hytter",
    "Under rødt gulv",
    "I toppen af rør",
    "Gammel Træstub i jordhøjde",
    "Hænger i træ (petling)",
    "Bag elskab",
    "Info-tavle.",
    "Gå ikke over åen efter vand.Magnetisk",
    "Fiskeri forbudt",
    "Træ.",
    "Magnetisk (BYOP)",
    "TV",
    "Hvem var Sine Olsen?",
    "Mellem stammer",
    "Ved jorden",
    "Hæk",
    "Ved Hyl",
    "I træ",
    "Ved træ",
    "Tæt på de 3",
    "Petling Autoværn",
])
def test_real_world_plaintext_hints_not_misdetected_as_cipher(hint):
    # Regression for #329: geocaching.com leverer hints i klartekst, men
    # OpenSAK antog tidligere altid at feltet var ROT13-kodet. Disse 24
    # eksempler må IKKE blive fejlklassificeret som ciffertekst.
    plain, cipher = split_hint(hint)
    assert plain == hint
    assert cipher == rot13(hint)


@pytest.mark.parametrize("plaintext", [
    # Lange, syntetiske eksempler der simulerer ægte ROT13-kodede hints
    # fra en gammel GSAK-eksport (GSAK's "Decode hints"-eksportvalg).
    "Under a large rock formation near the old oak tree by the river",
    "Look behind the wooden fence post next to the abandoned barn",
    "Det ligger gemt bag den gamle egetræsstub ved skovkanten",
])
def test_long_legacy_rot13_hints_are_unscrambled(plaintext):
    # Lange nok hints til at vokal-heuristikken kan afgøre retningen
    # korrekt, selv når kildedata reelt er ægte ROT13-ciffertekst.
    stored_as_cipher = rot13(plaintext)
    plain, cipher = split_hint(stored_as_cipher)
    assert plain == plaintext
    assert cipher == stored_as_cipher


def test_split_hint_roundtrips_via_rot13():
    # plain og cipher skal altid være hinandens ROT13-transformation,
    # uanset hvilken vej heuristikken gætter.
    for hint in ["Under a rock.", "Haqre n ebpx", "Brændeknude", "TV"]:
        plain, cipher = split_hint(hint)
        assert rot13(plain) == cipher
        assert rot13(cipher) == plain
