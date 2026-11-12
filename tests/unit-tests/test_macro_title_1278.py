"""Regression test for #1278: macro window titles no longer say "proof of concept".

``macro_title`` is the title of the macro editor, the ``opensak.progress``
dialog and the fallback title of every macro form and message box. It used to
read "Macro (Lua) — proof of concept" (translated in each language).
"""

import importlib

import pytest

from opensak.lang import AVAILABLE_LANGUAGES

# Proof-of-concept wording as it appeared in each language's macro_title.
_POC_WORDS = (
    "proof of concept",
    "ověření konceptu",
    "prueba de concepto",
    "preuve de concept",
    "weryfikacja koncepcji",
    "prova de conceito",
    "konceptbevis",
)


@pytest.mark.parametrize("code", sorted(AVAILABLE_LANGUAGES))
def test_macro_title_has_no_proof_of_concept_suffix(code):
    title = importlib.import_module(f"opensak.lang.{code}").STRINGS["macro_title"]
    assert "(Lua)" in title
    assert "—" not in title
    assert not any(word in title.lower() for word in _POC_WORDS), title
