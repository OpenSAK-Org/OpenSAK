# tests/unit-tests/test_log_preview.py — log preview rendering (issues #218, #219).
#
# #218: log-tekst blev tidligere trunkeret til 500 tegn med "…" — nu vises hele teksten.
# #219: markdown-links [tekst](url) i logge vises i dag som rå tekst — skal være klikbare <a> tags.

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QUrl

from opensak.gui import cache_detail as cd
from opensak.gui.cache_detail import CacheDetailPanel, _convert_markdown_links


def _log(text: str, log_type="Found it", finder="Tester", date=None) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        log_type=log_type,
        finder=finder,
        log_date=date or datetime(2026, 1, 15, 10, 30),
    )


# ── _convert_markdown_links (pure function) ──────────────────────────────────


def test_convert_markdown_links_basic():
    text = "Check the [Giga Event in Prague](https://coord.info/GCAGGGG) page."
    result = _convert_markdown_links(text)
    assert '<a href="https://coord.info/GCAGGGG">Giga Event in Prague</a>' in result
    assert "[Giga Event in Prague]" not in result


def test_convert_markdown_links_multiple():
    text = (
        "See [day 1](https://coord.info/GCB2HVN) and "
        "[day 2](https://coord.info/GCAGGGG) for details."
    )
    result = _convert_markdown_links(text)
    assert '<a href="https://coord.info/GCB2HVN">day 1</a>' in result
    assert '<a href="https://coord.info/GCAGGGG">day 2</a>' in result


def test_convert_markdown_links_ignores_plain_brackets():
    text = "TFTC [not a link] thanks!"
    result = _convert_markdown_links(text)
    assert result == text  # uændret — intet (url) efter de firkantede parenteser


def test_convert_markdown_links_no_links_unchanged():
    text = "Just a normal log entry with no links at all."
    assert _convert_markdown_links(text) == text


def test_convert_markdown_links_requires_http_scheme():
    # Kun http/https links konverteres — undgå falske positiver på fx [foo](bar)
    text = "[label](bar)"
    assert _convert_markdown_links(text) == text


# ── _render_log_html via CacheDetailPanel ────────────────────────────────────


def test_long_log_text_not_truncated(qtbot):
    # issue #218 — tidligere blev tekst > 500 tegn klippet med "…"
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)

    long_text = "A" * 600
    panel._render_log_html([_log(long_text)])

    html = panel._log_browser.toHtml()
    assert long_text in html
    assert "…" not in html


def test_short_log_text_unaffected(qtbot):
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)

    panel._render_log_html([_log("TFTC! Great hide.")])
    html = panel._log_browser.toHtml()
    assert "TFTC! Great hide." in html


def test_markdown_link_in_log_becomes_clickable(qtbot):
    # issue #219 — [tekst](url) skal vises som rigtigt <a href> link
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)

    text = "Read the [trip report](https://coord.info/GCAGGGG) here."
    panel._render_log_html([_log(text)])

    html = panel._log_browser.toHtml()
    assert "coord.info/GCAGGGG" in html
    assert "href=" in html.lower()
    assert "[trip report]" not in html


def test_log_browser_does_not_navigate_links_itself(qtbot):
    # Links skal åbnes i systemets browser via _open_http_link, ikke af
    # QTextBrowser selv (som ville åbne alle skemaer)
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)
    assert panel._log_browser.openLinks() is False
    assert panel._log_browser.openExternalLinks() is False


def test_html_in_log_text_is_escaped(qtbot):
    # Logtekst er tredjeparts-indhold — rå <a>-tags må ikke blive til links
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)

    text = 'Click <a href="file:///C:/evil.exe">here</a>'
    panel._render_log_html([_log(text)])

    assert 'href="file:' not in panel._log_browser.toHtml()
    assert text in panel._log_browser.toPlainText()


def test_html_in_log_finder_and_type_is_escaped(qtbot):
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)

    panel._render_log_html([_log("TFTC", log_type="<i>Found</i>", finder='<a href="smb://x/y">Bob</a>')])

    html = panel._log_browser.toHtml()
    assert 'href="smb:' not in html
    assert '<a href="smb://x/y">Bob</a>' in panel._log_browser.toPlainText()
    assert "<i>Found</i>" in panel._log_browser.toPlainText()


def test_markdown_link_with_query_survives_escaping(qtbot):
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)

    panel._render_log_html([_log("See [map](https://example.com/?a=1&b=2)")])

    assert "https://example.com/?a=1&amp;b=2" in panel._log_browser.toHtml()
    assert "[map]" not in panel._log_browser.toPlainText()


@pytest.mark.parametrize("url, opened", [
    ("https://coord.info/GCAGGGG", True),
    ("http://example.com", True),
    ("file:///C:/evil.exe", False),
    ("smb://host/share", False),
    ("ms-settings:", False),
])
def test_open_http_link_only_opens_http(monkeypatch, url, opened):
    calls = []
    monkeypatch.setattr(cd.webbrowser, "open", calls.append)
    cd._open_http_link(QUrl(url))
    assert bool(calls) is opened


def test_markdown_link_rendered_in_log_html(qtbot):
    # Markdown links in log text are converted to HTML anchors
    panel = CacheDetailPanel()
    qtbot.addWidget(panel)

    text = "Great day, see [trip report](https://coord.info/GCAGGGG) for more."
    panel._render_log_html([_log(text)])

    html = panel._log_browser.toHtml()
    assert "coord.info/GCAGGGG" in html
