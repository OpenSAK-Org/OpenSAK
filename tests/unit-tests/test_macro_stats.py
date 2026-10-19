"""tests/unit-tests/test_macro_stats.py — opensak.stats.*: group, fill,
running, pivot, every, summary, sort and streaks on rows, and html.matrix for
the matrix of stats.pivot."""

import re
from datetime import datetime

import pytest

from opensak.macro import MacroError, MacroRuntime
from opensak.stats import html as report_html
from opensak.stats import tables


class FakeHost:
    def end_macro(self):
        pass

    def __getattr__(self, name):
        # Bound into the opensak table at every run, never called here.
        return lambda *args: None


def _run(source):
    out: list[str] = []
    MacroRuntime(FakeHost(), output=out.append).run(source)
    return out


# ── opensak.stats.* (Python side) ───────────────────────────────────────────

FINDS = [
    {"code": "GC1", "d": "2024-03-04", "type": "Traditional", "fav": 2, "dt": 1.5},
    {"code": "GC2", "d": "2024-03-04 10:00:00", "type": "Multi", "fav": 5, "dt": 2.0},
    {"code": "GC3", "d": "2024-05-01", "type": "Traditional", "fav": None, "dt": 1.5},
    {"code": "GC4", "d": "2023-12-31", "type": "Mystery", "fav": 1, "dt": None},
    {"code": "GC5", "d": None, "type": "Traditional"},
]


@pytest.mark.parametrize("period, keys", [
    ("year", ["2023", "2024"]),
    ("month", ["2023-12", "2024-03", "2024-05"]),
    ("week", ["2023-W52", "2024-W10", "2024-W18"]),
    ("day", ["2023-12-31", "2024-03-04", "2024-05-01"]),
    ("weekday", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
])
def test_group_by_period(period, keys):
    result = tables.group(FINDS, "d", period=period)
    assert [g["key"] for g in result] == keys
    assert sum(g["count"] for g in result) == 4        # nil date left out


def test_group_distinct_sum_sort_and_top():
    result = tables.group(FINDS, "type", count_distinct="d", sum="fav", sort="count")
    assert result == [
        {"key": "Traditional", "count": 3, "distinct": 2, "sum": 2},
        {"key": "Multi", "count": 1, "distinct": 1, "sum": 5},
        {"key": "Mystery", "count": 1, "distinct": 1, "sum": 1},
    ]
    top = tables.group(FINDS, "type", sort="count", top=1, other="Rest")
    assert top == [{"key": "Traditional", "count": 3}, {"key": "Rest", "count": 2}]
    assert [g["key"] for g in tables.group(FINDS, "fav", sort="key")] == [1, 2, 5]


def test_fill_running_and_every():
    months = tables.fill(tables.group(FINDS, "d", period="month"))
    assert [(m["key"], m["count"]) for m in months] == [
        ("2023-12", 1), ("2024-01", 0), ("2024-02", 0), ("2024-03", 2),
        ("2024-04", 0), ("2024-05", 1)]
    years = tables.fill([{"key": 2020, "n": 1}, {"key": "2022", "n": 2}], period="year",
                        end="2023")
    assert [(y["key"], y["n"]) for y in years] == [
        ("2020", 1), ("2021", 0), ("2022", 2), ("2023", 0)]
    total = tables.running(months)
    assert [m["total"] for m in total] == [1, 1, 1, 3, 3, 4]
    assert "total" not in months[0]                    # copies
    ordered = [FINDS[i] for i in tables.sort_order(FINDS, ["d", "code"])]
    picked = tables.every(ordered[:4], 2, date_field="d")
    assert [(p["n"], p["code"], p.get("days")) for p in picked] == [
        (1, "GC4", None), (2, "GC1", 64), (4, "GC3", 58)]
    assert [p["n"] for p in tables.every(ordered, 2, first=False, last=False)] == [2, 4]


def test_sort_order_descending_nil_last_and_stable():
    order = tables.sort_order(FINDS, ["-fav"])
    assert [FINDS[i]["code"] for i in order] == ["GC2", "GC1", "GC4", "GC3", "GC5"]
    order = tables.sort_order(FINDS, ["type", "-code"])
    assert [FINDS[i]["code"] for i in order] == ["GC2", "GC4", "GC5", "GC3", "GC1"]


def test_pivot_and_summary():
    m = tables.pivot(FINDS, "type", "dt")
    assert m["rows"] == ["Multi", "Traditional"] and m["cols"] == [1.5, 2.0]
    assert m["cells"] == [[0, 1], [2, 0]]
    assert (m["max"], m["total"], m["filled"]) == (2, 3, 2)
    fixed = tables.pivot(FINDS, "type", "dt", rows_order=["Traditional"], cols_order=[1, 1.5, 2])
    assert fixed["cells"] == [[0, 2, 0]]
    assert tables.summary(FINDS, "fav") == {
        "count": 3, "distinct": 3, "min": 1, "max": 5, "sum": 8, "mean": 8 / 3, "median": 2}
    dates = tables.summary(FINDS, "d")
    assert (dates["count"], dates["min"], dates["max"]) == (4, "2023-12-31", "2024-05-01")
    assert "sum" not in dates
    assert tables.summary([], "x") == {"count": 0, "distinct": 0}


def test_dates_and_option_errors():
    assert tables.days_between("2024-02-28", "2024-03-01") == 2     # leap year
    assert tables.days_between("2024-03-01 23:59", "2024-02-28") == -2
    assert tables.days_between(datetime(2024, 1, 1).timestamp(), "2024-01-31") == 30
    with pytest.raises(tables.StatsError, match="must be a date"):
        tables.days_between("soon", "2024-01-01")
    with pytest.raises(tables.StatsError, match="period must be one of"):
        tables.group(FINDS, "d", period="decade")
    with pytest.raises(tables.StatsError, match="more than"):
        tables.fill([{"key": "1000-01-01"}], period="day", end="9999-01-01")


def test_streaks():
    rows = [{"d": d} for d in ("2024-01-01", "2024-01-02", "2024-01-03", "2024-01-10",
                               "2024-01-11", "2024-01-11 18:00", None)]
    assert tables.streaks(rows, "d") == {
        "days": 5,
        "longest": {"days": 3, "first": "2024-01-01", "last": "2024-01-03"},
        "gap": {"days": 6, "first": "2024-01-04", "last": "2024-01-09"},
        "last": {"days": 2, "first": "2024-01-10", "last": "2024-01-11"},
    }
    assert tables.streaks([{"d": "2024-02-28"}, {"d": "2024-03-01"}], "d")["gap"] == \
        {"days": 1, "first": "2024-02-29", "last": "2024-02-29"}
    assert tables.streaks([], "d") == {"days": 0}
    assert "gap" not in tables.streaks([{"d": "2024-01-01"}], "d")


def test_html_matrix():
    m = {"rows": [1, 2], "cols": ["a", "b"], "cells": [[1, 0], [3, 4]]}
    out = report_html.matrix(m, totals=True, row_title="D", col_title="T")
    assert "D \\ T" in out and ">Total</th>" in out
    cells = re.findall(r"<td style=\"([^\"]*)\">([^<]*)</td>", out)
    assert [c[1] for c in cells] == ["1", "", "1", "3", "4", "7", "4", "4", "8"]
    assert "rgba(42,120,214,1.00);color:#fff" in out           # the largest cell
    percent = report_html.matrix(m, percent=True, shade=False)
    assert "13 %" in percent and "50 %" in percent and "38 %" in percent
    assert "rgba(42,120,214" not in percent
    assert "rgba(255,0,0," in report_html.matrix(m, color="#f00")
    with pytest.raises(report_html.HtmlError, match="one line of cells per row"):
        report_html.matrix({"rows": [1], "cols": [1], "cells": []})
    with pytest.raises(report_html.HtmlError, match="color must be"):
        report_html.matrix(m, color="#+1f")


# ── opensak.stats.* / html.matrix from Lua ─────────────────────────────────

def test_stats_functions_from_lua():
    out = _run("""
        local rows = {
            { code = "GC1", d = "2024-03-04", t = "A", n = 2 },
            { code = "GC2", d = "2024-03-04", t = "B", n = 1 },
            { code = "GC3", d = "2024-05-01", t = "A" },
            { code = "GC4", d = "2023-12-31", t = "A", extra = { 1, 2 } },
        }
        local s = opensak.stats
        for _, g in ipairs(s.group(rows, "d", { period = "month", count_distinct = "d", sum = "n" })) do
            print(g.key, g.count, g.distinct, g.sum)
        end
        print(#s.group(rows, "d", { period = "weekday" }))
        local months = s.running(s.fill(s.group(rows, "d", { period = "month" })))
        print(#months, months[2].key, months[2].count, months[#months].total)
        local sorted = s.sort(rows, "d", "-code")
        print(sorted[1].code, sorted[2].code, sorted[1].extra[2], sorted[1] == rows[4])
        local m = s.every(sorted, 2, { date = "d" })
        print(#m, m[2].n, m[2].code, m[2].days, m[1].extra[1])
        local p = s.pivot(rows, "t", "d")
        print(#p.rows, #p.cols, p.cells[1][2], p.filled)
        local f = s.summary(rows, "n")
        print(f.count, f.sum, f.median)
        local html = opensak.html.matrix(p, { totals = true, row_title = "T" })
        print(html:find(">Total</th>", 1, true) ~= nil, html:find(">T</th>", 1, true) ~= nil)
    """)
    assert out == [
        "2023-12\t1\t1\t0", "2024-03\t2\t1\t3", "2024-05\t1\t1\t0",
        "7",
        "6\t2024-01\t0\t4",
        "GC4\tGC2\t2\ttrue",
        "3\t2\tGC2\t64\t1",
        "2\t3\t1\t4",
        "2\t3\t1.5",
        "true\ttrue",
    ]


def test_streaks_from_lua():
    out = _run("""
        local s = opensak.stats.streaks({ {d = "2024-01-01"}, {d = "2024-01-02"}, {d = "2024-01-05"} }, "d")
        print(s.days, s.longest.days, s.longest.first, s.gap.days, s.last.first)
    """)
    assert out == ["3\t2\t2024-01-01\t2\t2024-01-05"]


def test_date_diff_and_period_from_lua():
    out = _run("""
        print(opensak.date.diff("2026-01-01", "2026-10-06"), opensak.date.diff("2024-03-01 23:59", "2024-02-28"))
        print(opensak.date.period("2026-10-06", "week"), opensak.date.period("2026-10-06 23:00", "weekday"),
              opensak.date.period("2026-10-06", "month"))
    """)
    assert out == ["278\t-2", "2026-W41\tTue\t2026-10"]


@pytest.mark.parametrize("call, message", [
    ('opensak.stats.group("x", "d")', "expects a list of rows"),
    ('opensak.stats.group({ 1 }, "d")', "row 1 is not a table"),
    ('opensak.stats.group({}, nil)', "by must be a field name"),
    ('opensak.stats.group({}, "d", { nope = 1 })', r"unknown option\(s\) \['nope'\]"),
    ('opensak.stats.group({}, "d", { sort = "x" })', "sort must be"),
    ('opensak.stats.group({}, "d", { top = 0 })', "top must be"),
    ('opensak.stats.group({ {d = "x"} }, "d", { period = "year" })', "must be a date"),
    ('opensak.stats.fill({}, { period = "week" })', "period must be one of day, month, year"),
    ('opensak.stats.running({ {count = "x"} })', "count must be a number"),
    ('opensak.stats.every({}, 0)', "step must be"),
    ('opensak.stats.sort({})', "needs at least one field"),
    ('opensak.stats.pivot({}, "a", "b", { rows = 1 })', "rows must be a list"),
    ('opensak.stats.summary({}, 3)', "field must be a field name"),
    ('opensak.stats.streaks({ {d = true} }, "d")', "opensak.stats.streaks:"),
    ('opensak.date.diff("x", "2024-01-01")', "opensak.date.diff: first date must be a date"),
    ('opensak.date.period("2026-10-06", "decade")', "period must be one of"),
    ('opensak.date.period({}, "day")', "opensak.date.period: date must be a date"),
    ('opensak.html.matrix({ 1 })', "expects a matrix"),
    ('opensak.html.matrix({ rows = {1}, cols = {1}, cells = {} })', "one line of cells"),
    ('opensak.html.matrix(opensak.stats.pivot({}, "a", "b"), { colour = "#fff" })',
     r"unknown option\(s\) \['colour'\]"),
])
def test_stats_errors_from_lua(call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)
