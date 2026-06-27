# tests/unit-tests/test_text_search_filter.py — TextSearchFilter tests.

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache, Log, UserNote
from opensak.filters.engine import FilterSet, TextSearchFilter, apply_filters


# ── Seed data ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        c1 = Cache(
            gc_code="GCTS001", name="Alpha",
            cache_type="Traditional Cache", latitude=55.0, longitude=12.0,
            short_description="A waterfall nearby",
            long_description="Long text about bridge crossing",
            encoded_hints="Look under the rock",
        )
        c2 = Cache(
            gc_code="GCTS002", name="Beta",
            cache_type="Traditional Cache", latitude=55.1, longitude=12.1,
            short_description=None, long_description=None, encoded_hints=None,
        )
        c3 = Cache(
            gc_code="GCTS003", name="Gamma",
            cache_type="Traditional Cache", latitude=55.2, longitude=12.2,
            short_description="Nothing special here",
        )
        s.add_all([c1, c2, c3])
        s.flush()

        s.add(Log(cache_id=c1.id, log_type="Found it", text="Great cache, loved the waterfall"))
        s.add(Log(cache_id=c2.id, log_type="Found it", text="Hidden near the bridge"))
        s.add(Log(cache_id=c3.id, log_type="Found it", text="Easy find"))

        s.add(UserNote(cache_id=c1.id, note="My personal note about this cache"))
        s.add(UserNote(cache_id=c2.id, note="Bring a pen"))


# ── matches() unit tests ───────────────────────────────────────────────────────

def _cache_with_note(gc, description, log_text, note_text, hint=None):
    # Build a detached Cache with related objects for matches() tests.
    c = Cache(
        gc_code=gc, name=gc,
        cache_type="Traditional Cache", latitude=0.0, longitude=0.0,
        short_description=description, encoded_hints=hint,
    )
    if log_text:
        c.logs = [Log(log_type="Found it", text=log_text)]
    else:
        c.logs = []
    if note_text:
        c.user_note = UserNote(note=note_text)
    return c


def test_matches_description():
    f = TextSearchFilter("waterfall", search_description=True, search_logs=False,
                         search_notes=False, search_hint=False)
    c = _cache_with_note("GC1", "Near a waterfall", None, None)
    assert f.matches(c)


def test_matches_description_case_insensitive():
    f = TextSearchFilter("WATERFALL", search_description=True, search_logs=False,
                         search_notes=False, search_hint=False)
    c = _cache_with_note("GC1", "Near a waterfall", None, None)
    assert f.matches(c)


def test_no_match_wrong_field():
    # "waterfall" is in description but we only search logs
    f = TextSearchFilter("waterfall", search_description=False, search_logs=True,
                         search_notes=False, search_hint=False)
    c = _cache_with_note("GC1", "Near a waterfall", "Easy find", None)
    assert not f.matches(c)


def test_matches_log_text():
    f = TextSearchFilter("bridge", search_description=False, search_logs=True,
                         search_notes=False, search_hint=False)
    c = _cache_with_note("GC2", "Nothing", "Hidden near the bridge", None)
    assert f.matches(c)


def test_matches_user_note():
    f = TextSearchFilter("personal", search_description=False, search_logs=False,
                         search_notes=True, search_hint=False)
    c = _cache_with_note("GC3", "Nothing", None, "My personal note")
    assert f.matches(c)


def test_matches_hint():
    f = TextSearchFilter("rock", search_description=False, search_logs=False,
                         search_notes=False, search_hint=True)
    c = _cache_with_note("GC4", "Nothing", None, None, hint="Under the rock")
    assert f.matches(c)


def test_no_match_hint_not_enabled():
    f = TextSearchFilter("rock", search_description=True, search_logs=True,
                         search_notes=True, search_hint=False)
    c = _cache_with_note("GC4", "Nothing", "Easy", None, hint="Under the rock")
    assert not f.matches(c)


def test_empty_text_matches_all():
    f = TextSearchFilter("", search_description=True, search_logs=True,
                         search_notes=True, search_hint=True)
    c = _cache_with_note("GC5", None, None, None)
    assert f.matches(c)


def test_none_fields_do_not_crash():
    f = TextSearchFilter("anything")
    c = _cache_with_note("GC6", None, None, None)
    assert not f.matches(c)


def test_no_fields_selected_never_matches():
    f = TextSearchFilter("waterfall", search_description=False, search_logs=False,
                         search_notes=False, search_hint=False)
    c = _cache_with_note("GC7", "Near a waterfall", "Loved it", "My note", "Hint here")
    assert not f.matches(c)


# ── to_dict / from_dict round-trip ────────────────────────────────────────────

def test_round_trip_defaults():
    f = TextSearchFilter("bridge")
    d = f.to_dict()
    assert d["filter_type"] == "text_search"
    assert d["text"] == "bridge"
    assert d["search_description"] is True
    assert d["search_logs"] is True
    assert d["search_notes"] is True
    assert d["search_hint"] is False

    f2 = TextSearchFilter.from_dict(d)
    assert f2.text == "bridge"
    assert f2.search_hint is False


def test_round_trip_hint_enabled():
    f = TextSearchFilter("rock", search_hint=True, search_notes=False)
    f2 = TextSearchFilter.from_dict(f.to_dict())
    assert f2.search_hint is True
    assert f2.search_notes is False


def test_from_dict_missing_keys_uses_defaults():
    f = TextSearchFilter.from_dict({"text": "test"})
    assert f.search_description is True
    assert f.search_hint is False


# ── apply_filters integration (SQL pushdown) ──────────────────────────────────

def test_apply_filters_description_match():
    fs = FilterSet().add(TextSearchFilter("waterfall", search_logs=False,
                                          search_notes=False, search_hint=False))
    with get_session() as s:
        results = apply_filters(s, fs)
    gc_codes = {c.gc_code for c in results}
    assert "GCTS001" in gc_codes
    assert "GCTS002" not in gc_codes
    assert "GCTS003" not in gc_codes


def test_apply_filters_log_match():
    fs = FilterSet().add(TextSearchFilter("bridge", search_description=False,
                                          search_notes=False, search_hint=False))
    with get_session() as s:
        results = apply_filters(s, fs)
    gc_codes = {c.gc_code for c in results}
    assert "GCTS002" in gc_codes  # log says "bridge"
    assert "GCTS001" not in gc_codes


def test_apply_filters_note_match():
    fs = FilterSet().add(TextSearchFilter("personal", search_description=False,
                                          search_logs=False, search_hint=False))
    with get_session() as s:
        results = apply_filters(s, fs)
    gc_codes = {c.gc_code for c in results}
    assert "GCTS001" in gc_codes
    assert "GCTS002" not in gc_codes


def test_apply_filters_hint_match():
    fs = FilterSet().add(TextSearchFilter("rock", search_description=False,
                                          search_logs=False, search_notes=False,
                                          search_hint=True))
    with get_session() as s:
        results = apply_filters(s, fs)
    gc_codes = {c.gc_code for c in results}
    assert "GCTS001" in gc_codes
    assert "GCTS002" not in gc_codes


def test_apply_filters_empty_text_returns_all():
    fs = FilterSet().add(TextSearchFilter(""))
    with get_session() as s:
        all_results = apply_filters(s, fs)
        all_codes = {c.gc_code for c in all_results}
    assert {"GCTS001", "GCTS002", "GCTS003"}.issubset(all_codes)


def test_apply_filters_no_match():
    fs = FilterSet().add(TextSearchFilter("zzznomatch"))
    with get_session() as s:
        results = apply_filters(s, fs)
    gc_codes = {c.gc_code for c in results}
    assert "GCTS001" not in gc_codes
    assert "GCTS002" not in gc_codes
    assert "GCTS003" not in gc_codes


def test_apply_filters_sql_python_parity():
    # Verify SQL pushdown and Python matches() agree on the same result set.
    for text in ("waterfall", "bridge", "personal", "rock", "easy"):
        fs = FilterSet().add(TextSearchFilter(text))
        with get_session() as s:
            sql_codes = {c.gc_code for c in apply_filters(s, fs)}
            all_rows  = s.query(Cache).all()
            py_codes  = {c.gc_code for c in all_rows if fs.matches(c)}
        assert sql_codes == py_codes, f"parity failed for {text!r}"
