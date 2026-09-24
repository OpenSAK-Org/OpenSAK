"""tests/unit-tests/test_personal_note_filter.py — PersonalNoteFilter (GSAK "Has user notes").

Keeps caches that do / don't have a non-blank UserNote.note. SQL pushdown must
agree with matches() for every shape of note: no UserNote row, a row with only
corrected coordinates, NULL / empty / whitespace-only text, and a real note —
on both apply_filters() and apply_filters_lightweight().
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, UserNote
from opensak.filters.engine import (
    FILTER_REGISTRY, FilterSet, PersonalNoteFilter, UserFlagFilter,
    apply_filters, apply_filters_lightweight,
)

# gc_code -> UserNote kwargs (None = no UserNote row at all)
_NOTES = {
    "GCPN01": None,
    "GCPN02": {"note": None, "is_corrected": True,
               "corrected_lat": 55.5, "corrected_lon": 12.5},
    "GCPN03": {"note": ""},
    "GCPN04": {"note": "  \t\r\n "},
    "GCPN05": {"note": "Final is behind the tree"},
    "GCPN06": {"note": "\n  padded  \n"},
}
_WITH_NOTE = {"GCPN05", "GCPN06"}


@pytest.fixture(scope="module", autouse=True)
def seed_note_data(tmp_db):
    with get_session() as s:
        for code, note in _NOTES.items():
            c = Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                      latitude=55.0, longitude=12.0, user_flag=code == "GCPN01")
            s.add(c)
            s.flush()
            if note is not None:
                s.add(UserNote(cache_id=c.id, **note))


def _run(fs):
    with get_session() as s:
        pushed = {c.gc_code for c in apply_filters(s, fs)}
        python = {c.gc_code for c in apply_filters(s, None) if fs.matches(c)}
        light = {c.gc_code for c in apply_filters_lightweight(s, fs)}
    assert pushed == python, (
        f"only in SQL-pushed: {pushed - python}\n"
        f"only in Python matches(): {python - pushed}"
    )
    assert light == pushed
    return pushed & set(_NOTES)


def test_has_note():
    assert _run(FilterSet().add(PersonalNoteFilter(True))) == _WITH_NOTE


def test_has_no_note():
    assert _run(FilterSet().add(PersonalNoteFilter(False))) == set(_NOTES) - _WITH_NOTE


def test_in_or_group_uses_python_path():
    fs = FilterSet().add(FilterSet(mode="OR")
                         .add(PersonalNoteFilter(True))
                         .add(UserFlagFilter(True)))
    assert _run(fs) == _WITH_NOTE | {"GCPN01"}


@pytest.mark.parametrize("has_note", [True, False])
def test_serialisation_roundtrip(has_note):
    data = PersonalNoteFilter(has_note).to_dict()
    assert data == {"filter_type": "personal_note", "has_note": has_note}
    restored = FILTER_REGISTRY["personal_note"].from_dict(data)
    assert restored.to_dict() == data
