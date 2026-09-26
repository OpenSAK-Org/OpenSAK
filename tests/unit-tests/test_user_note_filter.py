"""tests/unit-tests/test_user_note_filter.py — UserNoteFilter (personal note text match).

Matches UserNote.note (whitespace-stripped) with the TextMatchFilter operators.
SQL pushdown must agree with matches() for every shape of note: no UserNote
row, a row with only corrected coordinates, NULL / empty / whitespace-only
text, and real notes — on both apply_filters() and apply_filters_lightweight().
Profiles saved with the old yes/no PersonalNoteFilter must migrate.
"""

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, UserNote
from opensak.filters.engine import (
    FILTER_REGISTRY, FilterSet, UserFlagFilter, UserNoteFilter,
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
    "GCPN07": {"note": "Solved: N 47° Zürich"},
}
_WITH_NOTE = {"GCPN05", "GCPN06", "GCPN07"}


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


def test_not_empty():
    assert _run(FilterSet().add(UserNoteFilter(op="not_empty"))) == _WITH_NOTE


def test_empty():
    assert _run(FilterSet().add(UserNoteFilter(op="empty"))) == set(_NOTES) - _WITH_NOTE


@pytest.mark.parametrize("text, op, expected", [
    ("TREE", "contains", {"GCPN05"}),
    ("tree", "not_contains", set(_NOTES) - {"GCPN05"}),
    ("padded", "equals", {"GCPN06"}),          # compared whitespace-stripped
    ("final", "starts_with", {"GCPN05"}),
    ("zürich", "contains", {"GCPN07"}),         # non-ASCII: pre-narrowed in SQL
    (r"N \d+°", "regex", {"GCPN07"}),           # Python-only
    ("", "contains", set(_NOTES)),              # no-op
])
def test_text_ops(text, op, expected):
    assert _run(FilterSet().add(UserNoteFilter(text, op))) == expected


def test_in_or_group_uses_python_path():
    fs = FilterSet().add(FilterSet(mode="OR")
                         .add(UserNoteFilter(op="not_empty"))
                         .add(UserFlagFilter(True)))
    assert _run(fs) == _WITH_NOTE | {"GCPN01"}


def test_serialisation_roundtrip():
    data = UserNoteFilter("tree", "not_contains").to_dict()
    assert data == {"filter_type": "user_note", "text": "tree", "op": "not_contains"}
    restored = FILTER_REGISTRY["user_note"].from_dict(data)
    assert restored.to_dict() == data


@pytest.mark.parametrize("has_note, op", [(True, "not_empty"), (False, "empty")])
def test_legacy_personal_note_profile_migrates(has_note, op):
    fs = FilterSet.from_dict({
        "mode": "AND",
        "filters": [{"filter_type": "personal_note", "has_note": has_note}],
    })
    (f,) = fs._filters
    assert isinstance(f, UserNoteFilter)
    assert f.op == op
    # Re-saving writes the new format.
    assert fs.to_dict()["filters"] == [{"filter_type": "user_note", "text": "", "op": op}]
    expected = _WITH_NOTE if has_note else set(_NOTES) - _WITH_NOTE
    assert _run(fs) == expected
