"""tests/unit-tests/test_sort_sql.py — SQL-pushed sort parity.

apply_filters(sort=field) must match the equivalent stable Python sort — ties and NULLs included — for every SQL-pushed field.
"""

from datetime import datetime

import pytest

from opensak.db.database import get_session, init_db, make_session
from opensak.db.models import Cache
from opensak.filters.engine import apply_filters, SortSpec, SORT_FIELDS, _sql_order_expr


# ── Seed: deliberate ties (for stability) + NULLs (for coalesce/NULL order) ───

@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    rows = [
        # gc_code, difficulty, terrain, found, archived, premium, ftf,
        #          user_flag, fav_points, user_sort, hidden, found_date
        dict(gc_code="GCS001", difficulty=1.5, terrain=2.0, found=True,  archived=False,
             premium_only=False,  first_to_find=False, user_flag=True,
             favorite_points=10, user_sort=5,    hidden_date=datetime(2020, 1, 1)),
        dict(gc_code="GCS002", difficulty=1.5, terrain=2.0, found=True,  archived=False,
             premium_only=True, first_to_find=None,  user_flag=None,
             favorite_points=10, user_sort=None, hidden_date=datetime(2020, 1, 1)),
        dict(gc_code="GCS003", difficulty=None, terrain=None, found=False, archived=True,
             premium_only=False, first_to_find=True,  user_flag=False,
             favorite_points=None, user_sort=5,  hidden_date=None),
        dict(gc_code="GCS004", difficulty=5.0, terrain=1.0, found=False, archived=False,
             premium_only=False,  first_to_find=False, user_flag=True,
             favorite_points=3,  user_sort=1,    hidden_date=datetime(2022, 6, 15)),
        dict(gc_code="GCS005", difficulty=3.0, terrain=3.0, found=True,  archived=True,
             premium_only=True, first_to_find=None,  user_flag=None,
             favorite_points=0,  user_sort=None, hidden_date=datetime(2019, 3, 3)),
        dict(gc_code="GCS006", difficulty=3.0, terrain=3.0, found=False, archived=False,
             premium_only=False, first_to_find=True,  user_flag=True,
             favorite_points=None, user_sort=1,  hidden_date=datetime(2022, 6, 15)),
    ]
    s = make_session()
    for r in rows:
        s.add(Cache(name=f"N{r['gc_code']}", cache_type="Traditional Cache",
                    latitude=55.0, longitude=12.0, **r))
    s.commit()
    s.close()


# ── (a) Indexes exist ─────────────────────────────────────────────────────────

def test_filter_sort_indexes_created(tmp_db):
    with get_session() as s:
        names = {
            row[0]
            for row in s.execute(
                __import__("sqlalchemy").text(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='caches'"
                )
            )
        }
    for expected in (
        "ix_caches_cache_type", "ix_caches_difficulty", "ix_caches_terrain",
        "ix_caches_hidden_date", "ix_caches_found_date", "ix_caches_last_log_date",
        "ix_caches_found", "ix_caches_archived_available",
    ):
        assert expected in names, f"missing index {expected}"
    # country/state/county filters use LIKE '%x%' (non-sargable) — not indexed.
    for not_expected in ("ix_caches_country", "ix_caches_state", "ix_caches_county"):
        assert not_expected not in names


# ── (b) SQL sort == stable Python sort, per field ─────────────────────────────

NUMERIC_BOOL_FIELDS = [
    "difficulty", "terrain", "favorite_points", "user_sort",
    "found", "archived", "dnf", "premium_only",
    "first_to_find", "user_flag",
]
DATE_FIELDS = ["hidden_date", "found_date", "dnf_date"]


def _id_ordered_rows(s):
    return s.query(Cache).order_by(Cache.id).all()


@pytest.mark.parametrize("field", NUMERIC_BOOL_FIELDS)
@pytest.mark.parametrize("ascending", [True, False])
def test_sql_sort_matches_python_numeric_bool(tmp_db, field, ascending):
    assert _sql_order_expr(field) is not None, f"{field} should be SQL-sortable"
    with get_session() as s:
        rows = _id_ordered_rows(s)
        # Old behaviour: stable Python sort over id-ordered rows.
        expected = [c.gc_code for c in sorted(
            rows, key=SORT_FIELDS[field], reverse=not ascending
        )]
        actual = [c.gc_code for c in apply_filters(s, sort=SortSpec(field, ascending))]
    assert actual == expected


@pytest.mark.parametrize("field", DATE_FIELDS)
@pytest.mark.parametrize("ascending", [True, False])
def test_sql_sort_matches_dates_with_nulls(tmp_db, field, ascending):
    # SQLite orders NULLs as the smallest value (first ascending / last
    # descending). Mirror that with a datetime.min sentinel + stable id order.
    assert _sql_order_expr(field) is not None
    with get_session() as s:
        rows = _id_ordered_rows(s)
        expected = [c.gc_code for c in sorted(
            rows,
            key=lambda c: getattr(c, field) or datetime.min,
            reverse=not ascending,
        )]
        actual = [c.gc_code for c in apply_filters(s, sort=SortSpec(field, ascending))]
    assert actual == expected


def test_stability_preserved_on_ties(tmp_db):
    # GCS001/GCS002 share difficulty=1.5; GCS005/GCS006 share difficulty=3.0.
    # Ascending must keep the id-ascending order within each tie group.
    with get_session() as s:
        order = [c.gc_code for c in apply_filters(s, sort=SortSpec("difficulty", True))]
    assert order.index("GCS001") < order.index("GCS002")
    assert order.index("GCS005") < order.index("GCS006")
    # Descending: primary key reversed, but ties still id-ascending (stable).
    with get_session() as s:
        order_desc = [c.gc_code for c in apply_filters(s, sort=SortSpec("difficulty", False))]
    assert order_desc.index("GCS001") < order_desc.index("GCS002")
    assert order_desc.index("GCS005") < order_desc.index("GCS006")


# ── Text fields stay in Python and remain correct ─────────────────────────────

def test_text_field_not_sql_pushed_but_still_sorted(tmp_db):
    assert _sql_order_expr("name") is None  # text → Python
    with get_session() as s:
        names = [c.name for c in apply_filters(s, sort=SortSpec("name", True))]
    assert names == sorted(names, key=str.lower)
