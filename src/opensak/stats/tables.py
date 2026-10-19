"""
src/opensak/stats/tables.py — generic statistics on rows (Qt-free).

A row is a dict (a Lua table in opensak.stats.*), e.g. one cache from
opensak.caches(), one result row of opensak.sql() or of read_csv(). The
functions know nothing about geocaching: which rows to count and what to do
with the result is up to the macro.

Date fields are "YYYY-MM-DD" strings, optionally followed by a time (as in
Cache fields and SQLite), or seconds since the epoch (os.time()).
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

PERIODS = ("day", "week", "month", "year", "weekday")
FILL_PERIODS = ("day", "month", "year")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
GROUP_SORTS = ("key", "count", "sum")
# stats.fill() never adds more rows than this (e.g. a typo in `from`).
MAX_FILL = 100_000

Row = Mapping[str, Any]


class StatsError(ValueError):
    """Rows or options the statistics functions cannot use."""


# ── Dates ────────────────────────────────────────────────────────────────────

def to_date(value: Any, what: str = "date") -> date:
    """A "YYYY-MM-DD[...]" string or epoch seconds → date."""
    if isinstance(value, bool):
        raise StatsError(f"{what} must be a date, got {value!r}")
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value).date()
        except (OverflowError, OSError, ValueError):
            raise StatsError(f"{what}: {value!r} is not a valid time") from None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    raise StatsError(f'{what} must be a date like "2026-10-06", got {value!r}')


def days_between(a: Any, b: Any) -> int:
    """Whole days from *a* to *b* (negative if *b* is earlier)."""
    return (to_date(b, "second date") - to_date(a, "first date")).days


def period_key(value: Any, period: str) -> str:
    """The period a date falls in: "2026-10-06", "2026-W41", "2026-10",
    "2026" or "Tue"."""
    d = to_date(value)
    if period == "day":
        return d.isoformat()
    if period == "month":
        return f"{d.year:04d}-{d.month:02d}"
    if period == "year":
        return f"{d.year:04d}"
    if period == "week":
        year, week, _ = d.isocalendar()
        return f"{year:04d}-W{week:02d}"
    return WEEKDAYS[d.weekday()]


def _check_period(period: Any, allowed: Sequence[str], func: str) -> str:
    if period not in allowed:
        raise StatsError(f"{func}: period must be one of {', '.join(allowed)}, got {period!r}")
    return period


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sort_key(value: Any) -> tuple:
    """Order mixed values: numbers, then strings, then booleans; nil last."""
    if value is None:
        return (3, 0)
    if isinstance(value, bool):
        return (2, value)
    if isinstance(value, (int, float)):
        return (0, value)
    return (1, str(value))


def _number(value: Any, field: str, func: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise StatsError(f"{func}: {field} must be a number, got {value!r}")
    return value


def _tidy(value: float) -> float | int:
    """2.0 → 2, so integer counts and sums stay integers in Lua."""
    return int(value) if float(value).is_integer() else value


def _check_rows(rows: Any, func: str) -> Sequence[Row]:
    if not isinstance(rows, (list, tuple)):
        raise StatsError(f"{func} expects a list of rows")
    for i, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise StatsError(f"{func}: row {i} is not a table")
    return rows


# ── Functions ────────────────────────────────────────────────────────────────

def group(
    rows: Sequence[Row],
    by: str,
    period: Optional[str] = None,
    count_distinct: Optional[str] = None,
    sum: Optional[str] = None,
    sort: str = "key",
    top: Optional[int] = None,
    other: str = "Other",
) -> list[dict[str, Any]]:
    """Count the rows per value of field *by* → [{key, count[, distinct][, sum]}].

    *period* groups a date field by day, week, month, year or weekday.
    *count_distinct* also counts the different values of that field per
    group (`distinct`), *sum* adds up a number field (`sum`). Rows whose
    *by* is nil are left out; with period "weekday" all seven days are
    returned. *sort* = "key" (weekdays Mon–Sun) or "count" /
    "sum" (largest first). *top* keeps that many groups and puts the others
    together in one group called *other*.
    """
    func = "opensak.stats.group"
    _check_rows(rows, func)
    if period is not None:
        _check_period(period, PERIODS, func)
    if sort not in GROUP_SORTS or (sort == "sum" and sum is None):
        raise StatsError(f"{func}: sort must be key, count or sum (with sum =), got {sort!r}")
    if top is not None and (isinstance(top, bool) or not isinstance(top, int) or top < 1):
        raise StatsError(f"{func}: top must be a whole number >= 1, got {top!r}")
    groups: dict[Any, dict[str, Any]] = {}
    seen: dict[Any, set] = {}
    for row in rows:
        key = row.get(by)
        if key is None:
            continue
        if period is not None:
            key = period_key(key, period)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"key": key, "count": 0}
            if count_distinct is not None:
                seen[key] = set()
            if sum is not None:
                g["sum"] = 0
        g["count"] += 1
        if count_distinct is not None:
            value = row.get(count_distinct)
            if value is not None:
                seen[key].add(value)
        if sum is not None:
            value = _number(row.get(sum), sum, func)
            if value is not None:
                g["sum"] += value
    if period == "weekday":
        # All seven days, also those without rows.
        for day in WEEKDAYS:
            if day not in groups:
                groups[day] = {"key": day, "count": 0}
                if count_distinct is not None:
                    seen[day] = set()
                if sum is not None:
                    groups[day]["sum"] = 0
    result = list(groups.values())
    for g in result:
        if count_distinct is not None:
            g["distinct"] = len(seen[g["key"]])
        if sum is not None:
            g["sum"] = _tidy(g["sum"])
    if sort == "key":
        if period == "weekday":
            result.sort(key=lambda g: WEEKDAYS.index(g["key"]))
        else:
            result.sort(key=lambda g: _sort_key(g["key"]))
    else:
        result.sort(key=lambda g: (-g[sort], _sort_key(g["key"])))
    if top is not None and len(result) > top:
        rest = result[top:]
        merged: dict[str, Any] = {"key": other, "count": 0}
        for g in rest:
            merged["count"] += g["count"]
        if sum is not None:
            merged["sum"] = _tidy(math.fsum(g["sum"] for g in rest))
        result = result[:top] + [merged]
    return result


def _next_period(key: str, period: str) -> str:
    if period == "year":
        return f"{int(key) + 1:04d}"
    if period == "month":
        year, month = int(key[:4]), int(key[5:7])
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return f"{year:04d}-{month:02d}"
    return date.fromordinal(date.fromisoformat(key).toordinal() + 1).isoformat()


def _period_text(value: Any, period: str, what: str) -> str:
    """A key of a filled series in the canonical form of *period*."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and period == "year":
        return f"{int(value):04d}"
    text = str(value).strip() if value is not None else ""
    try:
        if period == "year" and len(text) == 4 and text.isdigit():
            return text
        if period == "month" and len(text) == 7:
            return period_key(text + "-01", "month")
        return period_key(text, period)
    except StatsError:
        raise StatsError(f"opensak.stats.fill: {what} {value!r} is not a {period}") from None


def fill(
    rows: Sequence[Row],
    key: str = "key",
    period: str = "month",
    start: Any = None,
    end: Any = None,
) -> list[dict[str, Any]]:
    """*rows* sorted by the period in field *key*, with a row added for
    every missing day, month or year between the first and the last (or
    *start* and *end*). Number fields of an added row are 0, the others
    nil — so a chart of the series is even in time."""
    func = "opensak.stats.fill"
    _check_rows(rows, func)
    _check_period(period, FILL_PERIODS, func)
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        k = _period_text(row.get(key), period, key)
        by_key[k] = dict(row)
        by_key[k][key] = k
    numbers = {
        name for row in by_key.values() for name, v in row.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool) and name != key
    }
    keys = sorted(by_key)
    first = _period_text(start, period, "from") if start is not None else (keys[0] if keys else None)
    last = _period_text(end, period, "to") if end is not None else (keys[-1] if keys else None)
    if first is None or last is None or first > last:
        return []
    result: list[dict[str, Any]] = []
    k = first
    while True:
        line = by_key.get(k)
        if line is None:
            line = {key: k, **{name: 0 for name in numbers}}
        result.append(line)
        if k >= last:
            break
        if len(result) >= MAX_FILL:
            raise StatsError(f"{func}: more than {MAX_FILL} periods from {first} to {last}")
        k = _next_period(k, period)
    return result


def running(rows: Sequence[Row], field: str = "count", into: str = "total") -> list[dict[str, Any]]:
    """Copies of *rows* with the running total of *field* in *into*."""
    func = "opensak.stats.running"
    _check_rows(rows, func)
    total: float = 0
    result = []
    for row in rows:
        value = _number(row.get(field), field, func)
        total += value or 0
        copy = dict(row)
        copy[into] = _tidy(total)
        result.append(copy)
    return result


def pivot(
    rows: Sequence[Row],
    row_field: str,
    col_field: str,
    rows_order: Optional[Sequence[Any]] = None,
    cols_order: Optional[Sequence[Any]] = None,
    sum: Optional[str] = None,
) -> dict[str, Any]:
    """Count the rows per pair of values → {rows, cols, cells, max, total, filled}.

    `rows`/`cols` are the values of the two fields (sorted, or the given
    lists, which also fix the order and leave out other values),
    `cells[i][j]` the count (or the sum of field *sum*) for rows[i] ×
    cols[j]; `filled` is the number of cells that are not 0. Rows with nil
    in either field are left out."""
    func = "opensak.stats.pivot"
    _check_rows(rows, func)
    counts: dict[tuple, float] = {}
    for row in rows:
        r, c = row.get(row_field), row.get(col_field)
        if r is None or c is None:
            continue
        value = 1.0 if sum is None else (_number(row.get(sum), sum, func) or 0)
        counts[(r, c)] = counts.get((r, c), 0) + value
    r_values = list(rows_order) if rows_order is not None else \
        sorted({r for r, _ in counts}, key=_sort_key)
    c_values = list(cols_order) if cols_order is not None else \
        sorted({c for _, c in counts}, key=_sort_key)
    cells = [[_tidy(counts.get((r, c), 0)) for c in c_values] for r in r_values]
    flat = [v for line in cells for v in line]
    return {
        "rows": r_values,
        "cols": c_values,
        "cells": cells,
        "max": max(flat, default=0),
        "total": _tidy(math.fsum(flat)),
        "filled": len([v for v in flat if v]),
    }


def every(
    rows: Sequence[Row],
    step: int,
    first: bool = True,
    last: bool = True,
    date_field: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Every *step*-th row (e.g. every 1000th find) as a copy with its
    number in `n`; also the first and the last row unless switched off.
    With *date_field*, `days` holds the days since the previous row picked
    (nil for the first)."""
    func = "opensak.stats.every"
    _check_rows(rows, func)
    if isinstance(step, bool) or not isinstance(step, int) or step < 1:
        raise StatsError(f"{func}: step must be a whole number >= 1, got {step!r}")
    picked = [n for n in range(1, len(rows) + 1)
              if n % step == 0 or (first and n == 1) or (last and n == len(rows))]
    result = []
    previous = None
    for n in picked:
        copy = dict(rows[n - 1])
        copy["n"] = n
        if date_field is not None:
            value = copy.get(date_field)
            if value is not None and previous is not None:
                copy["days"] = days_between(previous, value)
            if value is not None:
                previous = value
        result.append(copy)
    return result


def summary(rows: Sequence[Row], field: str) -> dict[str, Any]:
    """{count, distinct, min, max} of field *field* (nil values left out),
    plus {sum, mean, median} when all values are numbers."""
    func = "opensak.stats.summary"
    _check_rows(rows, func)
    values: list[Any] = [row.get(field) for row in rows if row.get(field) is not None]
    result: dict[str, Any] = {"count": len(values), "distinct": len(set(values))}
    if not values:
        return result
    try:
        result["min"] = min(values, key=_sort_key)
        result["max"] = max(values, key=_sort_key)
    except TypeError:
        pass
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        total = math.fsum(values)
        result["sum"] = _tidy(total)
        result["mean"] = total / len(values)
        result["median"] = _tidy(statistics.median(values))
    return result


def sort_order(rows: Sequence[Row], fields: Iterable[str]) -> list[int]:
    """Indexes of *rows* sorted by *fields* in turn; "-field" sorts that
    field largest first. nil sorts last either way."""
    func = "opensak.stats.sort"
    _check_rows(rows, func)
    keys: list[tuple[str, bool]] = []
    for f in fields:
        if not isinstance(f, str) or not f.lstrip("-"):
            raise StatsError(f"{func}: fields must be field names, got {f!r}")
        keys.append((f.lstrip("-"), f.startswith("-")))
    order = list(range(len(rows)))
    # Stable sorts from the last key to the first.
    for name, descending in reversed(keys):
        present = [i for i in order if rows[i].get(name) is not None]
        missing = [i for i in order if rows[i].get(name) is None]
        present.sort(key=lambda i: _sort_key(rows[i].get(name)), reverse=descending)
        order = present + missing
    return order



def period(value: Any, length: str) -> str:
    """opensak.date.period(): the period a date falls in, as stats.group
    names it ("2026", "2026-10", "2026-W41", "2026-10-06", "Tue")."""
    _check_period(length, PERIODS, "opensak.date.period")
    return period_key(value, length)


def _run(first: date, last: date) -> dict[str, Any]:
    return {"days": (last - first).days + 1, "first": first.isoformat(), "last": last.isoformat()}


def streaks(rows: Sequence[Row], field: str) -> dict[str, Any]:
    """Runs of days in date field *field*: {days (different days),
    longest (most days in a row with at least one row), gap (most days in
    a row without, between the first and the last day), last (the run that
    ends on the last day)} — each run {days, first, last}; nil if none."""
    func = "opensak.stats.streaks"
    _check_rows(rows, func)
    days = sorted({to_date(row.get(field), field) for row in rows if row.get(field) is not None})
    result: dict[str, Any] = {"days": len(days)}
    if not days:
        return result
    longest = current = (days[0], days[0])
    gap: Optional[tuple[date, date]] = None
    for previous, day in zip(days, days[1:]):
        if (day - previous).days == 1:
            current = (current[0], day)
        else:
            between = (date.fromordinal(previous.toordinal() + 1),
                       date.fromordinal(day.toordinal() - 1))
            if gap is None or (between[1] - between[0]) > (gap[1] - gap[0]):
                gap = between
            current = (day, day)
        if (current[1] - current[0]) > (longest[1] - longest[0]):
            longest = current
    result["longest"] = _run(*longest)
    result["last"] = _run(*current)
    if gap is not None:
        result["gap"] = _run(*gap)
    return result
