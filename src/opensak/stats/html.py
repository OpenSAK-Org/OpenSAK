"""
src/opensak/stats/html.py — small HTML building blocks for reports (Qt-free).

Every value is escaped; only the strings a macro passes as page body or as
the parts of html.columns() are put in as they are (they are HTML the macro
built, e.g. with these functions). page() brings a stylesheet with a light
and a dark theme, so a report needs no CSS of its own.
"""

from __future__ import annotations

import math
from html import escape as _escape
from typing import Any, Mapping, Optional, Sequence

# The accent colour (light, dark): links and the bars of html.table{ bars = }.
ACCENT_COLORS = ("#2a78d6", "#3987e5")
# html.table{ bars = }: inline styles only, so the HTML also works where no
# stylesheet is kept.
BAR_WIDTH = 120

PAGE_CSS = """\
:root { --bg: #fcfcfb; --fg: #0b0b0b; --muted: #52514e; --line: #e4e3df;
        --accent: %(light)s; color-scheme: light; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1a1a19; --fg: #f0efec; --muted: #c3c2b7; --line: #3a3a37;
          --accent: %(dark)s; color-scheme: dark; }
}
body { background: var(--bg); color: var(--fg); margin: 0 auto; max-width: 960px;
       padding: 24px 16px; font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { margin: 0 0 4px; font-size: 26px; }
h2 { margin: 36px 0 8px; font-size: 18px; }
p.sub { color: var(--muted); margin: 0 0 20px; }
a { color: var(--accent); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
.tile { border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
.tile .value { font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile .label { color: var(--muted); font-size: 13px; }
.columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 24px; }
.table { overflow-x: auto; }
table { border-collapse: collapse; font-variant-numeric: tabular-nums; margin-top: 8px; }
th, td { padding: 3px 10px; border-bottom: 1px solid var(--line); text-align: left; }
th { color: var(--muted); font-weight: 600; }
td.num, th.num { text-align: right; }
svg { display: block; margin: 8px 0; }
footer { margin-top: 40px; color: var(--muted); font-size: 13px; }
""" % {"light": ACCENT_COLORS[0], "dark": ACCENT_COLORS[1]}


class HtmlError(ValueError):
    """Arguments the HTML functions cannot use."""


def text(value: Any) -> str:
    """A cell value as text: nil → "", 3.0 → "3", 0.33333 → "0.33"."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def escape(value: Any) -> str:
    """*value* as text, with & < > " ' escaped."""
    return _escape(text(value), quote=True)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_url(value: Any) -> Optional[str]:
    url = text(value).strip()
    return url if url.lower().startswith(("https://", "http://")) else None


def _check_hex(color: Optional[str]) -> str:
    if color is None:
        return ACCENT_COLORS[0]
    if not isinstance(color, str) or len(color) not in (4, 7) or not color.startswith("#"):
        raise HtmlError(f'color must be "#rrggbb", got {color!r}')
    try:
        int(color[1:], 16)
    except ValueError:
        raise HtmlError(f'color must be "#rrggbb", got {color!r}') from None
    return color


def _columns(columns: Any, rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    if columns is None:
        # Lua tables have no field order, so the fields are sorted.
        names = sorted({str(k) for row in rows for k in row})
        return [(n, n) for n in names]
    if not isinstance(columns, (list, tuple)) or not columns:
        raise HtmlError('columns must be a list, e.g. { "code", {"name", "Name"} }')
    result = []
    for col in columns:
        if isinstance(col, str):
            result.append((col, col))
        elif isinstance(col, (list, tuple)) and len(col) == 2 \
                and all(isinstance(c, str) for c in col):
            result.append((col[0], col[1]))
        else:
            raise HtmlError(f'a column is a field name or {{field, heading}}, got {col!r}')
    return result


def table(
    rows: Sequence[Mapping[str, Any]],
    columns: Any = None,
    link: Optional[Mapping[str, str]] = None,
    caption: Optional[str] = None,
    bars: Optional[Sequence[str]] = None,
    color: Optional[str] = None,
) -> str:
    """*rows* as an HTML table. *columns* lists the fields to show, each a
    field name or {field, heading} (default: all fields, sorted). Number
    columns are right-aligned. *link* = {code = "url"} makes the code cell a
    link to the row's url field (http/https only). *bars* lists number
    fields that get a bar next to their value, scaled to the column's
    largest value (in *color*)."""
    if not isinstance(rows, (list, tuple)) or not all(isinstance(r, Mapping) for r in rows):
        raise HtmlError("rows must be a list of tables")
    cols = _columns(columns, rows)
    link = dict(link or {})
    bar_fields = list(bars or [])
    if not all(isinstance(b, str) for b in bar_fields):
        raise HtmlError('bars must be a list of field names, e.g. { "count" }')
    bar_color = _check_hex(color)
    bar_max = {
        f: max((r.get(f) for r in rows if _is_number(r.get(f))), default=0) for f in bar_fields
    }
    numeric = {
        field for field, _ in cols
        if any(r.get(field) is not None for r in rows)
        and all(_is_number(r.get(field)) for r in rows if r.get(field) is not None)
    }
    out = ['<div class="table"><table>']
    if caption:
        out.append(f"<caption>{escape(caption)}</caption>")
    out.append("<thead><tr>")
    for field, heading in cols:
        cls = ' class="num"' if field in numeric else ""
        out.append(f"<th{cls}>{escape(heading)}</th>")
        if field in bar_max:
            out.append("<th></th>")
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        for field, _ in cols:
            cell = escape(row.get(field))
            if field in link:
                url = _safe_url(row.get(link[field]))
                if url:
                    cell = f'<a href="{_escape(url, quote=True)}">{cell}</a>'
            cls = ' class="num"' if field in numeric else ""
            out.append(f"<td{cls}>{cell}</td>")
            if field in bar_max:
                value = row.get(field)
                top = bar_max[field]
                share = max(value, 0) / top if _is_number(value) and top > 0 else 0
                out.append(
                    f'<td style="width:{BAR_WIDTH}px;padding-left:0">'
                    f'<span style="display:block;height:10px;width:{share * 100:.1f}%;'
                    f'background:{bar_color};border-radius:0 3px 3px 0"></span></td>'
                )
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def tiles(items: Sequence[Mapping[str, Any]]) -> str:
    """Key figures as tiles: a list of {label =, value =}."""
    if not isinstance(items, (list, tuple)):
        raise HtmlError('tiles expects a list, e.g. { {label = "finds", value = 42} }')
    out = ['<div class="tiles">']
    for item in items:
        if not isinstance(item, Mapping):
            raise HtmlError("each tile is a table {label =, value =}")
        out.append(f'<div class="tile"><div class="value">{escape(item.get("value"))}</div>'
                   f'<div class="label">{escape(item.get("label"))}</div></div>')
    out.append("</div>")
    return "".join(out)


def _html_parts(parts: Any, what: str) -> list[str]:
    if isinstance(parts, str):
        return [parts]
    if isinstance(parts, (list, tuple)) and all(isinstance(p, str) for p in parts):
        return list(parts)
    raise HtmlError(f"{what} must be a string or a list of strings")


def columns(parts: Sequence[str]) -> str:
    """Put the HTML *parts* side by side (one below the other on a narrow
    window)."""
    return '<div class="columns">' + "".join(
        f"<div>{p}</div>" for p in _html_parts(parts, "columns")
    ) + "</div>"


def page(
    body: Any,
    title: str = "Report",
    subtitle: Optional[str] = None,
    css: Optional[str] = None,
    lang: str = "en",
    footer: Optional[str] = None,
) -> str:
    """A complete HTML page: *title* as <title> and heading, *subtitle*
    below it, then *body* (HTML, a string or a list of strings), then
    *footer*. *css* is added after the built-in stylesheet."""
    if not isinstance(lang, str) or not lang.replace("-", "").isalnum():
        raise HtmlError(f"lang must be a language code like \"en\", got {lang!r}")
    if css is not None and (not isinstance(css, str) or "</" in css):
        raise HtmlError("css must be a string without </")
    parts = [
        "<!doctype html>",
        f'<html lang="{lang}"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(title)}</title>",
        f"<style>\n{PAGE_CSS}{css or ''}\n</style></head><body>",
        f"<h1>{escape(title)}</h1>",
    ]
    if subtitle:
        parts.append(f'<p class="sub">{escape(subtitle)}</p>')
    parts.extend(_html_parts(body, "body"))
    if footer:
        parts.append(f"<footer>{escape(footer)}</footer>")
    parts.append("</body></html>")
    return "\n".join(parts)
