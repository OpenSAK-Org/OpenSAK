# tests/unit-tests/test_container_sort.py — Container column sort key (issue #90).

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from opensak.gui.cache_table import _container_sort_key


@dataclass
class FakeCache:
    gc_code: str
    container: Optional[str]
    cache_type: Optional[str] = None


def _sort(caches, reverse=False):
    caches.sort(key=lambda c: _container_sort_key(c.container, c.cache_type), reverse=reverse)
    return [c.gc_code for c in caches]


@pytest.mark.parametrize("container,cache_type", [
    ("Micro", "Traditional Cache"), ("Other", "Mystery Cache"),
    ("", "Virtual Cache"), (None, "EarthCache"), ("XYZ", None),
])
def test_key_is_always_a_2_tuple(container, cache_type):
    result = _container_sort_key(container, cache_type)
    assert isinstance(result, tuple) and len(result) == 2


def test_physical_containers_sorted_smallest_to_largest_in_group_1():
    keys = [_container_sort_key(s) for s in ("Micro", "Small", "Regular", "Large")]
    assert keys == sorted(keys)
    assert {k[0] for k in keys} == {1}
    assert len(set(keys)) == 4


def test_non_physical_types_use_group_2_letter_alphabetically():
    # cache_type wins over container value: a Virtual with container 'Other' sorts as 'V'.
    assert _container_sort_key("Other", "EarthCache") == (2, "E")
    assert _container_sort_key("Other", "Lab Cache") == (2, "V")
    assert _container_sort_key("Other") == (2, "O")
    assert _container_sort_key("Other", "Virtual Cache") == (2, "V")


def test_unknown_container_falls_back_to_other():
    assert _container_sort_key("XYZ") == _container_sort_key("Other") == (2, "O")


def test_empty_and_not_chosen_are_group_3():
    for v in ("", "Not chosen", None):
        assert _container_sort_key(v) == (3, "")


def test_case_insensitive_and_whitespace_tolerant():
    assert _container_sort_key("MICRO") == _container_sort_key("micro") == _container_sort_key("  Micro  ")


def test_full_ascending_order_physical_then_letters_then_empty():
    caches = [
        FakeCache("LARGE", "Large"), FakeCache("MICRO", "Micro"),
        FakeCache("NOTCHOSEN", "Not chosen"), FakeCache("VIRT", "Other", "Virtual Cache"),
        FakeCache("REGULAR", "Regular"), FakeCache("OTHER", "Other"),
        FakeCache("EARTH", "", "EarthCache"), FakeCache("LAB", "Other", "Lab Cache"),
        FakeCache("SMALL", "Small"),
    ]
    assert _sort(caches) == [
        "MICRO", "SMALL", "REGULAR", "LARGE",       # group 1, smallest first
        "EARTH", "OTHER", "VIRT", "LAB",            # group 2, E < O < V (Lab sorts as V with Virtual)
        "NOTCHOSEN",                                # group 3
    ]


def test_descending_reverses_order():
    assert _sort([FakeCache("MICRO", "Micro"), FakeCache("LARGE", "Large")], reverse=True) \
        == ["LARGE", "MICRO"]


def test_stable_sort_preserves_secondary_order_within_a_group():
    # Pre-sorted by distance; container sort must keep that order within each size group.
    caches = [
        FakeCache("CLOSE_LARGE", "Large"), FakeCache("CLOSE_SMALL", "Small"),
        FakeCache("MID_LARGE", "Large"), FakeCache("MID_SMALL", "Small"),
        FakeCache("FAR_LARGE", "Large"),
    ]
    assert _sort(caches) == ["CLOSE_SMALL", "MID_SMALL", "CLOSE_LARGE", "MID_LARGE", "FAR_LARGE"]
