"""tests/unit-tests/test_macro_html.py — opensak.html.*: escaped tables
(GSAK's SqlToHtml), tiles, columns and a complete page for write_text()."""

import pytest

from opensak.macro import MacroError, MacroRuntime
from opensak.stats import html as report_html


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


def test_table_escapes_links_and_aligns_numbers():
    out = report_html.table(
        [{"code": "GC1", "n": 3.0, "url": "https://coord.info/GC1", "x": "<b>"},
         {"code": "GC2", "n": None, "url": "javascript:alert(1)", "x": None}],
        columns=["code", ["n", "Count"], "x"], link={"code": "url"})
    assert '<th class="num">Count</th>' in out and '<td class="num">3</td>' in out
    assert '<a href="https://coord.info/GC1">GC1</a>' in out
    assert "javascript" not in out and "<td>GC2</td>" in out
    assert "&lt;b&gt;" in out and "<b>" not in out
    default = report_html.table([{"b": 1, "a": 2}])
    assert default.index(">a<") < default.index(">b<")       # sorted field names


def test_table_bars():
    out = report_html.table([{"k": "x", "n": 2}, {"k": "y", "n": 8}],
                            columns=["k", "n"], bars=["n"], color="#ff0000")
    assert "width:25.0%;background:#ff0000" in out and "width:100.0%" in out
    with pytest.raises(report_html.HtmlError, match="color must be"):
        report_html.table([{"n": 1}], bars=["n"], color="#zzzzzz")


def test_tiles_columns_and_page():
    tiles = report_html.tiles([{"label": "finds", "value": 12}])
    assert '<div class="value">12</div><div class="label">finds</div>' in tiles
    assert report_html.columns(["<i>1</i>", "<i>2</i>"]) == \
        '<div class="columns"><div><i>1</i></div><div><i>2</i></div></div>'
    page = report_html.page(["<p>x</p>"], title="A & B", subtitle="<s>", footer="f")
    assert page.startswith("<!doctype html>") and "<title>A &amp; B</title>" in page
    assert "&lt;s&gt;" in page and "<p>x</p>" in page and "<footer>f</footer>" in page
    assert "prefers-color-scheme: dark" in page
    with pytest.raises(report_html.HtmlError, match="css"):
        report_html.page([], css="</style><script>")
    with pytest.raises(report_html.HtmlError, match="lang"):
        report_html.page([], lang='en"><script>')


@pytest.mark.parametrize("value, expected", [
    (None, ""), (True, "true"), (3.0, "3"), (1 / 3, "0.33"), (2.5, "2.5"), ("<&>", "&lt;&amp;&gt;"),
])
def test_escape(value, expected):
    assert report_html.escape(value) == expected


def test_html_functions_from_lua():
    out = _run("""
        local rows = {
            { code = "GC1", name = "A <1>", n = 2 },
            { code = "GC2", name = "B", n = 8, extra = { 1, 2 } },
        }
        print(opensak.html.escape("<a>"), opensak.html.escape(nil) == "", opensak.html.escape(2.0))
        local t = opensak.html.table(rows, { columns = { "code", {"n", "Finds"} }, bars = { "n" } })
        print(t:find("<td>GC1</td>", 1, true) ~= nil, t:find(">Finds</th>", 1, true) ~= nil,
              t:find("width:25.0%", 1, true) ~= nil, t:find("A &lt;1&gt;", 1, true) == nil)
        local all = opensak.html.table(rows)
        print(all:find("A &lt;1&gt;", 1, true) ~= nil)
        local page = opensak.html.page{ title = "R", body = "<p>b</p>",
            opensak.html.tiles{ { label = "x", value = 1 } },
            opensak.html.columns(t, "<i>2</i>") }
        print(page:find('<div class="tiles">', 1, true) < page:find("<p>b</p>", 1, true),
              page:find("<td>GC2</td>", 1, true) ~= nil, page:find("<i>2</i>", 1, true) ~= nil)
        print(opensak.html.columns({ "<a>", "<b>" }) == opensak.html.columns("<a>", "<b>"))
    """)
    assert out == [
        "&lt;a&gt;\ttrue\t2",
        "true\ttrue\ttrue\ttrue",
        "true",
        "true\ttrue\ttrue",
        "true",
    ]


@pytest.mark.parametrize("call, message", [
    ('opensak.html.escape({})', "expects a string"),
    ('opensak.html.table("x")', "expects a list of rows"),
    ('opensak.html.table({ 1 })', "row 1 is not a table"),
    ('opensak.html.table({}, { nope = 1 })', r"unknown option\(s\) \['nope'\]"),
    ('opensak.html.table({}, { link = { code = 1 } })', "link must be a table"),
    ('opensak.html.table({ {n = 1} }, { columns = { "n" }, bars = "n" })', "bars must be a list"),
    ('opensak.html.table({ {n = 1} }, { columns = { "n" }, bars = { "n" }, color = "red" })',
     "color must be"),
    ('opensak.html.table({ {n = 1} }, { columns = { 1 } })', "a column is a field name"),
    ('opensak.html.tiles("x")', "tiles expects a list"),
    ('opensak.html.columns({ 1 })', "columns must be a string or a list of strings"),
    ('opensak.html.page("x")', "expects a table"),
    ('opensak.html.page{ titel = "x" }', r"unknown key\(s\) \['titel'\]"),
    ('opensak.html.page{ css = "</style>" }', "css must be"),
])
def test_html_errors_from_lua(call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)
