"""
src/opensak/stats/charts.py — bar and line charts as plain SVG strings.

No dependency and no script: the SVG can be put inline into an HTML page or
saved as a .svg file. Text and grid lines use currentColor, so they follow
the colour of the page around them (also in dark mode); the series colour is
the CSS variable --chart-color with *color* as fallback, so a page can theme
every chart at once. Each bar and point has a <title>, which browsers show
as tooltip. All labels are escaped.

One series per chart and one y axis: two measures of a different scale
belong in two charts.
"""

from __future__ import annotations

import math
from html import escape
from typing import Any, Mapping, Optional, Sequence

DEFAULT_COLOR = "#2a78d6"
# Categorical order for stacked series (light theme; the page stylesheet of
# html.page sets the dark steps as --chart-color-2 …). Never cycled: at most
# this many series.
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300",
                 "#6250d6", "#e34948")
DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 280
MIN_SIZE, MAX_SIZE = 120, 4000
# More rows than this is not a chart any more but a table.
MAX_POINTS = 5000
# Value labels are written on the bars up to this many bars.
MAX_VALUE_LABELS = 16
# Rough width of one character of the 12px label font.
_CHAR_W = 7.0
_FONT = 12
_TITLE_H = 26
# Longest category label on a horizontal bar chart; the full text stays in
# the tooltip.
_MAX_LABEL_CHARS = 28


class ChartError(ValueError):
    """Rows or options that cannot be drawn."""


def _fmt(value: float) -> str:
    """1234 → "1234", 2.5 → "2.5", 0.3333 → "0.33"."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _ticks(top: float) -> list[float]:
    """0 and 4–6 round steps up to at least *top*."""
    if top <= 0:
        return [0.0, 1.0]
    raw = top / 5
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    count = math.ceil(top / step - 1e-9)
    return [i * step for i in range(count + 1)]


def _points(rows: Sequence[Mapping[str, Any]], x: str, y: str) -> list[tuple[str, float]]:
    if not isinstance(rows, (list, tuple)):
        raise ChartError("rows must be a list of tables, e.g. { {key = 2024, count = 12}, ... }")
    if len(rows) > MAX_POINTS:
        raise ChartError(f"at most {MAX_POINTS} rows can be drawn, got {len(rows)}")
    points = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise ChartError(f"row {i} is not a table")
        label = row.get(x)
        value = row.get(y)
        if value is None:
            value = 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ChartError(f"row {i}: {y} must be a number, got {value!r}")
        if label is None:
            label = ""
        elif isinstance(label, float) and label.is_integer():
            label = int(label)
        points.append((str(label), float(value)))
    return points


def _check_size(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not MIN_SIZE <= value <= MAX_SIZE:
        raise ChartError(f"{name} must be a number from {MIN_SIZE} to {MAX_SIZE}, got {value!r}")
    return int(value)


def _check_color(color: Optional[str]) -> str:
    if color is None:
        return DEFAULT_COLOR
    # Goes into a style attribute: a colour name or #hex only.
    if not isinstance(color, str) or not color or len(color) > 32 \
            or not all(c.isalnum() or c == "#" for c in color):
        raise ChartError(f'color must be a colour name or "#rrggbb", got {color!r}')
    return color


def _svg_open(width: int, height: int, title: Optional[str]) -> list[str]:
    label = escape(title or "Chart")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{label}" '
        f'font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="{_FONT}" '
        f'style="max-width:100%;height:auto;color:inherit">'
    ]
    if title:
        parts.append(
            f'<text x="0" y="17" font-size="15" font-weight="600" fill="currentColor">{label}</text>'
        )
    return parts


def _series_style(color: str) -> str:
    return f"var(--chart-color, {color})"


def _label_step(count: int, space: float, longest: int) -> int:
    """Show every n-th category label so the labels do not overlap."""
    if count == 0:
        return 1
    needed = longest * _CHAR_W + 8
    return max(1, math.ceil(needed / max(space / count, 1)))


def _shown_labels(keys: Sequence[str], centers: Sequence[float]) -> set[int]:
    """Indexes of the category labels to write: each one unless it would
    overlap the label written before it."""
    shown: set[int] = set()
    right = -math.inf
    for i, (key, center) in enumerate(zip(keys, centers)):
        half = len(key) * _CHAR_W / 2
        if center - half >= right + 6:
            shown.add(i)
            right = center + half
    return shown


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _bar_path(x: float, y: float, w: float, h: float, horizontal: bool) -> str:
    """A bar with a 4 px rounded data end (top, or right when horizontal)
    and a square end on the baseline."""
    if h <= 0 or w <= 0:
        return ""
    if horizontal:
        r = min(4.0, h / 2, w)
        return (f"M{x:.1f},{y:.1f}H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}"
                f"V{y + h - r:.1f}Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f}"
                f"H{x:.1f}Z")
    r = min(4.0, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
            f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z")


def _series_colors(color: str, count: int) -> list[str]:
    """Fill of each series: the first one --chart-color (or *color*), the
    others the fixed categorical order (--chart-color-2 …), never cycled."""
    return [_series_style(color)] + [
        f"var(--chart-color-{i + 1}, {SERIES_COLORS[i]})" for i in range(1, count)
    ]


def _legend_row(parts: list[str], names: Sequence[str], fills: Sequence[str], y: float) -> None:
    x = 0.0
    for name, fill in zip(names, fills):
        parts.append(f'<rect x="{x:.1f}" y="{y - 10:.1f}" width="12" height="12" rx="2" '
                     f'style="fill:{fill}"/>')
        parts.append(f'<text x="{x + 16:.1f}" y="{y:.1f}" fill="currentColor">{escape(name)}</text>')
        x += 30 + len(name) * _CHAR_W


def _stack_rows(rows: Sequence[Mapping[str, Any]], x: str, ys: Sequence[str]) -> list[tuple[str, list[float]]]:
    per_series = [_points(rows, x, y) for y in ys]
    return [(per_series[0][i][0], [s[i][1] for s in per_series]) for i in range(len(rows))]


def bar(
    rows: Sequence[Mapping[str, Any]],
    x: str = "key",
    y: str | Sequence[str] = "count",
    title: Optional[str] = None,
    width: int = DEFAULT_WIDTH,
    height: Optional[int] = None,
    color: Optional[str] = None,
    horizontal: bool = False,
    labels: Optional[bool] = None,
    names: Optional[Sequence[str]] = None,
) -> str:
    """Bar chart of *rows*: one bar per row, *x* the category, *y* the value
    (nil counts as 0; negative values are drawn as 0). With a list of
    fields as *y*, the bars are stacked, one segment per field, with a
    legend (*names* instead of the field names). *horizontal* draws the
    bars from the left, for long category names; its default height grows
    with the number of rows. *labels* writes the value (the total of a
    stack) on each bar (default: up to MAX_VALUE_LABELS bars)."""
    ys = [y] if isinstance(y, str) else list(y)
    if not ys or len(ys) > len(SERIES_COLORS) or not all(isinstance(f, str) for f in ys):
        raise ChartError(f"y must be a field name or a list of 1 to {len(SERIES_COLORS)} field names")
    legend_names = list(names) if names is not None else ys
    if len(legend_names) != len(ys) or not all(isinstance(n, str) for n in legend_names):
        raise ChartError("names must be a list of strings, one per field in y")
    bars = _stack_rows(rows, x, ys)
    width = _check_size("width", width)
    color = _check_color(color)
    fills = _series_colors(color, len(ys))
    stacked = len(ys) > 1
    if labels is None:
        labels = len(bars) <= MAX_VALUE_LABELS
    if height is None:
        height = (len(bars) * 24 + 40 + (_TITLE_H if title else 0)) if horizontal \
            else DEFAULT_HEIGHT
        height += 22 if stacked else 0
        height = max(MIN_SIZE, min(MAX_SIZE, height))
    height = _check_size("height", height)
    totals = [sum(max(v, 0) for v in values) for _, values in bars]
    ticks = _ticks(max(totals, default=0))
    top_value = ticks[-1]
    parts = _svg_open(width, height, title)
    plot_top = (_TITLE_H if title else 0) + 8
    if stacked:
        _legend_row(parts, legend_names, fills, plot_top + 10)
        plot_top += 22

    def tip(key: str, values: list[float], k: int) -> str:
        if not stacked:
            return escape(f"{key}: {_fmt(values[0])}")
        return escape(f"{key} · {legend_names[k]}: {_fmt(values[k])} (total {_fmt(sum(values))})")

    if horizontal:
        longest = max((len(_truncate(k, _MAX_LABEL_CHARS)) for k, _ in bars), default=1)
        left = min(width * 0.4, longest * _CHAR_W + 12)
        right, bottom = 16 + (len(_fmt(top_value)) * _CHAR_W if labels else 0), 24
        plot_w = max(width - left - right, 10)
        plot_h = max(height - plot_top - bottom, 10)
        band = plot_h / max(len(bars), 1)
        for t in ticks:
            gx = left + plot_w * t / top_value
            parts.append(f'<line x1="{gx:.1f}" y1="{plot_top}" x2="{gx:.1f}" '
                         f'y2="{plot_top + plot_h:.1f}" stroke="currentColor" stroke-opacity="0.12"/>')
            parts.append(f'<text x="{gx:.1f}" y="{height - 6}" text-anchor="middle" '
                         f'fill="currentColor" fill-opacity="0.7">{_fmt(t)}</text>')
        for i, (key, values) in enumerate(bars):
            by = plot_top + i * band + band * 0.15
            bh = band * 0.7
            cy = by + bh / 2 + 4
            parts.append(f'<text x="{left - 6:.1f}" y="{cy:.1f}" text-anchor="end" '
                         f'fill="currentColor">{escape(_truncate(key, _MAX_LABEL_CHARS))}</text>')
            segments = [(k, plot_w * max(v, 0) / top_value) for k, v in enumerate(values) if v > 0]
            x0: float = left
            for n, (k, bw) in enumerate(segments):
                last = n == len(segments) - 1
                w = bw if last else max(bw - 1, 0)       # 1 px gap between segments
                path = _bar_path(x0, by, w, bh, True) if last else \
                    f"M{x0:.1f},{by:.1f}h{w:.1f}v{bh:.1f}h{-w:.1f}Z"
                parts.append(f'<g><title>{tip(key, values, k)}</title>'
                             f'<path d="{path}" style="fill:{fills[k]}"/></g>')
                x0 += bw
            if not segments:
                parts.append(f'<g><title>{tip(key, values, 0)}</title><rect x="{left:.1f}" '
                             f'y="{by:.1f}" width="4" height="{bh:.1f}" fill="transparent"/></g>')
            if labels:
                parts.append(f'<text x="{x0 + 4:.1f}" y="{cy:.1f}" '
                             f'fill="currentColor">{_fmt(totals[i])}</text>')
        parts.append(f'<line x1="{left:.1f}" y1="{plot_top}" x2="{left:.1f}" '
                     f'y2="{plot_top + plot_h:.1f}" stroke="currentColor" stroke-opacity="0.5"/>')
    else:
        if labels:
            plot_top += 14
        left = len(_fmt(top_value)) * _CHAR_W + 12
        right, bottom = 8, 24
        plot_w = max(width - left - right, 10)
        plot_h = max(height - plot_top - bottom, 10)
        base = plot_top + plot_h
        band = plot_w / max(len(bars), 1)
        shown = _shown_labels([k for k, _ in bars],
                              [left + (i + 0.5) * band for i in range(len(bars))])
        for t in ticks:
            gy = base - plot_h * t / top_value
            parts.append(f'<line x1="{left:.1f}" y1="{gy:.1f}" x2="{width - right}" '
                         f'y2="{gy:.1f}" stroke="currentColor" stroke-opacity="0.12"/>')
            parts.append(f'<text x="{left - 6:.1f}" y="{gy + 4:.1f}" text-anchor="end" '
                         f'fill="currentColor" fill-opacity="0.7">{_fmt(t)}</text>')
        for i, (key, values) in enumerate(bars):
            bx = left + i * band + band * 0.15
            bw = max(band * 0.7, 1)
            cx = left + (i + 0.5) * band
            segments = [(k, plot_h * max(v, 0) / top_value) for k, v in enumerate(values) if v > 0]
            y0: float = base
            if not stacked:
                # One hit area over the whole column, as before.
                bh = segments[0][1] if segments else 0
                parts.append(f'<g><title>{tip(key, values, 0)}</title>'
                             f'<rect x="{left + i * band:.1f}" y="{plot_top}" width="{band:.1f}" '
                             f'height="{plot_h:.1f}" fill="transparent"/>'
                             f'<path d="{_bar_path(bx, base - bh, bw, bh, False)}" '
                             f'style="fill:{fills[0]}"/></g>')
                y0 = base - bh
            else:
                for n, (k, bh) in enumerate(segments):
                    last = n == len(segments) - 1
                    h = bh if last else max(bh - 1, 0)
                    path = _bar_path(bx, y0 - h, bw, h, False) if last else \
                        f"M{bx:.1f},{y0 - h:.1f}h{bw:.1f}v{h:.1f}h{-bw:.1f}Z"
                    parts.append(f'<g><title>{tip(key, values, k)}</title>'
                                 f'<path d="{path}" style="fill:{fills[k]}"/></g>')
                    y0 -= bh
            if labels:
                parts.append(f'<text x="{cx:.1f}" y="{y0 - 4:.1f}" text-anchor="middle" '
                             f'fill="currentColor">{_fmt(totals[i])}</text>')
            if i in shown:
                parts.append(f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle" '
                             f'fill="currentColor" fill-opacity="0.7">{escape(key)}</text>')
        parts.append(f'<line x1="{left:.1f}" y1="{base:.1f}" x2="{width - right}" '
                     f'y2="{base:.1f}" stroke="currentColor" stroke-opacity="0.5"/>')
    parts.append("</svg>")
    return "".join(parts)


def matrix_rows(matrix: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """A matrix (stats.pivot) as bar rows: one row per matrix row with a
    field per column → (rows, fields, names), for a stacked bar chart."""
    rows_, cols, cells = _matrix_parts(matrix)
    fields = [f"_{j}" for j in range(len(cols))]
    out = []
    for r, line_ in zip(rows_, cells):
        row: dict[str, Any] = {"key": r}
        row.update(zip(fields, line_))
        out.append(row)
    return out, fields, [_axis_label(c) for c in cols]


def _matrix_parts(matrix: Mapping[str, Any]) -> tuple[list, list, list]:
    if not isinstance(matrix, Mapping):
        raise ChartError("matrix must be a table {rows =, cols =, cells =}, e.g. from stats.pivot")
    rows_, cols, cells = matrix.get("rows"), matrix.get("cols"), matrix.get("cells")
    rows_ = [] if rows_ in (None, {}) else rows_
    cols = [] if cols in (None, {}) else cols
    cells = [] if cells in (None, {}) else cells
    if not isinstance(rows_, (list, tuple)) or not isinstance(cols, (list, tuple)) \
            or not isinstance(cells, (list, tuple)) or len(cells) != len(rows_):
        raise ChartError("matrix needs rows, cols and one line of cells per row")
    lines = []
    for i, line_ in enumerate(cells, 1):
        line_ = [] if line_ == {} else line_
        if not isinstance(line_, (list, tuple)) or len(line_) != len(cols):
            raise ChartError(f"cells line {i} must have one value per column")
        lines.append(list(line_))
    return list(rows_), list(cols), lines


def rose(
    rows: Sequence[Mapping[str, Any]],
    x: str = "key",
    y: str = "count",
    title: Optional[str] = None,
    width: int = 360,
    color: Optional[str] = None,
    labels: bool = True,
) -> str:
    """A rose (polar area) chart: one sector per row, in row order
    clockwise from the top (north), e.g. finds per compass direction. The
    area of a sector grows with its value."""
    points = _points(rows, x, y)
    width = _check_size("width", width)
    fill = _series_style(_check_color(color))
    top = (_TITLE_H if title else 0) + 4
    height = width + top
    parts = _svg_open(width, height, title)
    cx, cy = width / 2, top + width / 2
    radius = width / 2 - 28
    top_value = max((v for _, v in points), default=0)
    ticks = [t for t in _ticks(top_value) if t > 0] or [1.0]
    scale = ticks[-1]

    def r_of(v: float) -> float:
        return radius * math.sqrt(max(v, 0) / scale)

    for t in ticks:
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r_of(t):.1f}" fill="none" '
                     f'stroke="currentColor" stroke-opacity="0.12"/>')
        parts.append(f'<text x="{cx + 3:.1f}" y="{cy - r_of(t) + 11:.1f}" font-size="10" '
                     f'fill="currentColor" fill-opacity="0.6">{_fmt(t)}</text>')
    n = max(len(points), 1)
    step = 2 * math.pi / n
    for i, (key, value) in enumerate(points):
        a0, a1 = i * step - step / 2 + 0.02, i * step + step / 2 - 0.02
        r = r_of(value)
        tip = escape(f"{key}: {_fmt(value)}")
        if r > 0:
            x0, y0 = cx + r * math.sin(a0), cy - r * math.cos(a0)
            x1, y1 = cx + r * math.sin(a1), cy - r * math.cos(a1)
            large = 1 if a1 - a0 > math.pi else 0
            parts.append(f'<path d="M{cx:.1f},{cy:.1f}L{x0:.1f},{y0:.1f}A{r:.1f},{r:.1f} 0 {large} 1 '
                         f'{x1:.1f},{y1:.1f}Z" style="fill:{fill}" fill-opacity="0.85">'
                         f'<title>{tip}</title></path>')
        if labels:
            lx, ly = cx + (radius + 14) * math.sin(i * step), cy - (radius + 14) * math.cos(i * step)
            parts.append(f'<text x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="middle" '
                         f'fill="currentColor" fill-opacity="0.8"><title>{tip}</title>'
                         f'{escape(_truncate(key, 10))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def line(
    rows: Sequence[Mapping[str, Any]],
    x: str = "key",
    y: str = "count",
    title: Optional[str] = None,
    width: int = DEFAULT_WIDTH,
    height: Optional[int] = None,
    color: Optional[str] = None,
    area: bool = False,
    labels: Optional[bool] = None,
) -> str:
    """Line chart of *rows* in their order: *x* the label on the x axis
    (evenly spaced, e.g. months), *y* the value (nil counts as 0). *area*
    fills below the line. *labels* writes the value of the last point
    (default true)."""
    points = _points(rows, x, y)
    width = _check_size("width", width)
    height = _check_size("height", DEFAULT_HEIGHT if height is None else height)
    color = _check_color(color)
    if labels is None:
        labels = True
    values = [v for _, v in points]
    low = min(values + [0.0])
    ticks = _ticks(max(values, default=0) - low)
    ticks = [t + low for t in ticks]
    span = (ticks[-1] - ticks[0]) or 1
    parts = _svg_open(width, height, title)
    stroke = _series_style(color)
    plot_top = (_TITLE_H if title else 0) + 10
    left = max(len(_fmt(t)) for t in ticks) * _CHAR_W + 12
    last_label = _fmt(values[-1]) if values and labels else ""
    right, bottom = 12 + len(last_label) * _CHAR_W, 24
    plot_w = max(width - left - right, 10)
    plot_h = max(height - plot_top - bottom, 10)
    base = plot_top + plot_h
    gap = plot_w / max(len(points) - 1, 1)

    def px(i: int) -> float:
        return left + (i * gap if len(points) > 1 else plot_w / 2)

    def py(v: float) -> float:
        return base - plot_h * (v - ticks[0]) / span

    for t in ticks:
        gy = py(t)
        parts.append(f'<line x1="{left:.1f}" y1="{gy:.1f}" x2="{left + plot_w:.1f}" '
                     f'y2="{gy:.1f}" stroke="currentColor" stroke-opacity="0.12"/>')
        parts.append(f'<text x="{left - 6:.1f}" y="{gy + 4:.1f}" text-anchor="end" '
                     f'fill="currentColor" fill-opacity="0.7">{_fmt(t)}</text>')
    step = _label_step(len(points), plot_w, max((len(k) for k, _ in points), default=1))
    for i, (key, _) in enumerate(points):
        if i % step == 0:
            parts.append(f'<text x="{px(i):.1f}" y="{height - 6}" text-anchor="middle" '
                         f'fill="currentColor" fill-opacity="0.7">{escape(key)}</text>')
    parts.append(f'<line x1="{left:.1f}" y1="{base:.1f}" x2="{left + plot_w:.1f}" '
                 f'y2="{base:.1f}" stroke="currentColor" stroke-opacity="0.5"/>')
    if points:
        coords = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, (_, v) in enumerate(points))
        if area and len(points) > 1:
            parts.append(f'<polygon points="{px(0):.1f},{py(ticks[0]):.1f} {coords} '
                         f'{px(len(points) - 1):.1f},{py(ticks[0]):.1f}" '
                         f'style="fill:{stroke}" fill-opacity="0.15"/>')
        parts.append(f'<polyline points="{coords}" fill="none" style="stroke:{stroke}" '
                     f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        markers = len(points) <= 40
        hit = max(min(gap / 2, 12), 3)
        for i, (key, value) in enumerate(points):
            tip = escape(f"{key}: {_fmt(value)}")
            dot = (f'<circle cx="{px(i):.1f}" cy="{py(value):.1f}" r="4" style="fill:{stroke}" '
                   f'stroke="white" stroke-width="2" stroke-opacity="0.8"/>') if markers else ""
            parts.append(f'<g><title>{tip}</title><rect x="{px(i) - hit:.1f}" y="{plot_top}" '
                         f'width="{2 * hit:.1f}" height="{plot_h:.1f}" fill="transparent"/>{dot}</g>')
        if last_label:
            parts.append(f'<text x="{px(len(points) - 1) + 6:.1f}" y="{py(values[-1]) + 4:.1f}" '
                         f'fill="currentColor" font-weight="600">{last_label}</text>')
    parts.append("</svg>")
    return "".join(parts)



def _axis_label(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def heatmap(
    matrix: Mapping[str, Any],
    title: Optional[str] = None,
    width: int = DEFAULT_WIDTH,
    color: Optional[str] = None,
    labels: bool = True,
    row_title: Optional[str] = None,
    col_title: Optional[str] = None,
) -> str:
    """A grid of *matrix* ({rows, cols, cells} as from stats.pivot): one
    cell per row × column value, the darker the larger. *labels* writes
    the value into each cell that is not 0; *row_title* / *col_title* name
    the two fields (e.g. "D" and "T")."""
    rows, cols, cells = _matrix_parts(matrix)
    if len(rows) * len(cols) > MAX_POINTS:
        raise ChartError(f"at most {MAX_POINTS} cells can be drawn")
    values = [[_points([{"v": v}], "v", "v")[0][1] for v in line_] for line_ in cells]
    width = _check_size("width", width)
    color = _check_color(color)
    top_value = max((v for line_ in values for v in line_), default=0) or 1
    left = max([len(_axis_label(r)) for r in rows] + [len(row_title or "")] + [1]) * _CHAR_W + 14
    plot_top = (_TITLE_H if title else 0) + 22
    cell = max(min((width - left - 4) / max(len(cols), 1), 56), 12)
    height = int(plot_top + cell * len(rows) + 8)
    height = max(MIN_SIZE, min(MAX_SIZE, height))
    parts = _svg_open(width, height, title)
    fill = _series_style(color)
    if row_title or col_title:
        corner = escape(" \\ ".join(t for t in (row_title, col_title) if t))
        parts.append(f'<text x="{left - 6:.1f}" y="{plot_top - 8}" text-anchor="end" '
                     f'fill="currentColor" fill-opacity="0.7">{corner}</text>')
    for j, c in enumerate(cols):
        parts.append(f'<text x="{left + (j + 0.5) * cell:.1f}" y="{plot_top - 8}" '
                     f'text-anchor="middle" fill="currentColor" fill-opacity="0.7">'
                     f'{escape(_axis_label(c))}</text>')
    for i, r in enumerate(rows):
        y = plot_top + i * cell
        parts.append(f'<text x="{left - 6:.1f}" y="{y + cell / 2 + 4:.1f}" text-anchor="end" '
                     f'fill="currentColor" fill-opacity="0.7">{escape(_axis_label(r))}</text>')
        for j, c in enumerate(cols):
            v = values[i][j]
            x = left + j * cell
            tip = escape(f"{_axis_label(r)} / {_axis_label(c)}: {_fmt(v)}")
            if v > 0:
                alpha = 0.12 + 0.88 * v / top_value
                rect = (f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{cell - 2:.1f}" '
                        f'height="{cell - 2:.1f}" rx="3" style="fill:{fill}" '
                        f'fill-opacity="{alpha:.2f}"/>')
            else:
                rect = (f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{cell - 2:.1f}" '
                        f'height="{cell - 2:.1f}" rx="3" fill="none" stroke="currentColor" '
                        f'stroke-opacity="0.12"/>')
            label = ""
            if labels and v > 0 and cell >= 22:
                ink = 'fill="white"' if v / top_value > 0.5 else 'fill="currentColor"'
                size = _FONT if cell >= 34 else 10
                label = (f'<text x="{x + cell / 2:.1f}" y="{y + cell / 2 + size / 3:.1f}" '
                         f'text-anchor="middle" font-size="{size}" {ink}>{_fmt(v)}</text>')
            parts.append(f"<g><title>{tip}</title>{rect}{label}</g>")
    parts.append("</svg>")
    return "".join(parts)
