# tests/unit-tests/test_cache_detail.py — cache detail panel helpers.

from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.gui import cache_detail as cd
from opensak.utils.types import DateFormat


def _fake_settings(fmt: DateFormat) -> SimpleNamespace:
    return SimpleNamespace(date_format=fmt)


@pytest.mark.parametrize("fmt, expected", [
    (DateFormat.DMY, "25.12.2024"),
    (DateFormat.MDY, "12/25/2024"),
    (DateFormat.YMD, "2024-12-25"),
])
def test_format_date_respects_settings(monkeypatch, qapp, fmt, expected):
    # Regression for #322: dates in the detail panel were hardcoded to DMY.
    monkeypatch.setattr(cd, "get_settings", lambda: _fake_settings(fmt))
    result = cd._format_date(datetime(2024, 12, 25))
    assert result == expected
