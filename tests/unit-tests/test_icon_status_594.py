"""
tests/unit-tests/test_icon_status_594.py — issue #594.

Disabled and archived caches showed one generic grey icon in the cache list,
whatever their type. They now keep their own type icon, drawn in grey
(disabled) or grey and faded (archived).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QColor, QImage

from opensak.gui import cache_table as ct
from opensak.gui import icon_provider as ip


def _image(icon, size=32) -> QImage:
    return icon.pixmap(size, size).toImage().convertToFormat(QImage.Format.Format_ARGB32)


def _visible_pixels(img: QImage):
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor(img.pixel(x, y))
            c.setAlpha(QColor.fromRgba(img.pixel(x, y)).alpha())
            if c.alpha() > 0:
                yield c


def _max_alpha(img: QImage) -> int:
    return max((c.alpha() for c in _visible_pixels(img)), default=0)


class TestStatusIcons:
    @pytest.mark.parametrize("status", [ip.STATUS_DISABLED, ip.STATUS_ARCHIVED])
    def test_status_icon_is_grey(self, qapp, status):
        img = _image(ip.get_cache_type_icon("Traditional Cache", status=status))
        pixels = list(_visible_pixels(img))
        assert pixels, "icon has no visible pixels"
        # Greyscale: red, green and blue (nearly) equal everywhere.
        assert all(abs(c.red() - c.green()) <= 2 and abs(c.green() - c.blue()) <= 2
                   for c in pixels)

    def test_normal_icon_is_in_colour(self, qapp):
        img = _image(ip.get_cache_type_icon("Traditional Cache"))
        assert any(abs(c.red() - c.green()) > 10 or abs(c.green() - c.blue()) > 10
                   for c in _visible_pixels(img))

    def test_transparency_is_kept(self, qapp):
        normal = _image(ip.get_cache_type_icon("Traditional Cache"))
        disabled = _image(ip.get_cache_type_icon("Traditional Cache", status=ip.STATUS_DISABLED))
        assert QColor.fromRgba(disabled.pixel(0, 0)).alpha() == \
            QColor.fromRgba(normal.pixel(0, 0)).alpha()
        assert _max_alpha(disabled) == _max_alpha(normal)

    def test_archived_is_faded(self, qapp):
        disabled = _image(ip.get_cache_type_icon("Traditional Cache", status=ip.STATUS_DISABLED))
        archived = _image(ip.get_cache_type_icon("Traditional Cache", status=ip.STATUS_ARCHIVED))
        assert _max_alpha(archived) < _max_alpha(disabled) * 0.6

    def test_types_stay_different(self, qapp):
        # The whole point: a disabled Multi doesn't look like a disabled Traditional.
        trad = _image(ip.get_cache_type_icon("Traditional Cache", status=ip.STATUS_DISABLED))
        multi = _image(ip.get_cache_type_icon("Multi-cache", status=ip.STATUS_DISABLED))
        assert trad != multi

    def test_unknown_status_draws_the_normal_icon(self, qapp):
        normal = _image(ip.get_cache_type_icon("Traditional Cache"))
        other = _image(ip.get_cache_type_icon("Traditional Cache", status="found"))
        assert normal == other

    def test_status_pixmaps_are_cached(self, qapp):
        ip._status_pixmap.cache_clear()
        for _ in range(3):
            ip.get_cache_type_icon("Multi-cache", size=24, status=ip.STATUS_ARCHIVED)
        info = ip._status_pixmap.cache_info()
        assert info.misses == 1 and info.hits == 2


class TestCacheTable:
    def test_table_passes_type_and_status(self, qapp, monkeypatch):
        calls = []
        monkeypatch.setattr(ct, "get_cache_type_icon",
                            lambda key, size=32, status=None: calls.append((key, status)))
        monkeypatch.setattr(ct, "get_type_display", lambda: "icon")
        s = SimpleNamespace(text_size=ct.TextSize.MEDIUM)
        monkeypatch.setattr(ct, "get_settings", lambda: s)
        model = ct.CacheTableModel.__new__(ct.CacheTableModel)
        cache = SimpleNamespace(cache_type="Mystery Cache", archived=False, available=False)
        model._decoration_value(cache, "cache_type")
        cache = SimpleNamespace(cache_type="Earthcache", archived=True, available=True)
        model._decoration_value(cache, "cache_type")
        assert calls == [("mystery", "disabled"), ("earthcache", "archived")]
