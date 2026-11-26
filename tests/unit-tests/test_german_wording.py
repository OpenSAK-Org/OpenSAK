# tests/unit-tests/test_german_wording.py — German wording consistency.
#
# The German texts mixed the formal "Sie" with the informal "du" and used
# both " — " and " – " as the dash between parts of a sentence. de.py and
# de_CH.py now address the user as "Sie" throughout and use the German
# Gedankenstrich, the spaced en dash " – ".

import re

import pytest

from opensak.lang import de, de_CH

# Informal pronouns: "du", "dich", "dir", "dein…" as whole words. The
# capitalised forms need care, so "Deinstallation" etc. do not match.
INFORMAL = re.compile(
    r"\b(?:[dD]u|[dD]ich|[dD]ir|[dD]ein(?:e|en|em|er|es)?)\b"
)


@pytest.mark.parametrize("module", [de, de_CH], ids=["de", "de_CH"])
def test_user_is_addressed_formally(module):
    informal = {k: v for k, v in module.STRINGS.items() if INFORMAL.search(v)}
    assert informal == {}


@pytest.mark.parametrize("module", [de, de_CH], ids=["de", "de_CH"])
def test_dash_is_spaced_en_dash(module):
    em_dash = {k: v for k, v in module.STRINGS.items() if "—" in v}
    assert em_dash == {}


@pytest.mark.parametrize(
    "module, state", [(de, "Bundesland/Region"), (de_CH, "Kanton/Region")],
    ids=["de", "de_CH"],
)
def test_state_label_is_written_one_way(module, state):
    assert module.STRINGS["filter_state_label"] == state
    assert module.STRINGS["filter_where_note_state"] == state
    assert module.STRINGS["wp_label_state"] == state + ":"
