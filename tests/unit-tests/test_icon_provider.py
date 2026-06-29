# tests/unit-tests/test_icon_provider.py — cache icon/pixmap/pin provider.

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QIcon, QPixmap

from opensak.gui import icon_provider as ip


@pytest.fixture(autouse=True)
def _app(qapp):
    # QPainter/QPixmap need a QApplication.
    yield


# ── disk SVG reading ──────────────────────────────────────────────────────────

class TestReadSvg:
    def test_reads_existing(self, tmp_path):
        f = tmp_path / "x.svg"
        f.write_text("<svg/>", encoding="utf-8")
        assert ip._read_svg_file(f) == "<svg/>"

    def test_missing_returns_none(self, tmp_path):
        assert ip._read_svg_file(tmp_path / "nope.svg") is None

    def test_get_type_svg_known(self):
        assert ip._get_type_svg("traditional") is not None

    def test_get_type_svg_unknown_key(self):
        assert ip._get_type_svg("found") is None  # status, not in type map

    def test_get_found_svg_known(self):
        assert ip._get_found_svg("traditional") is not None

    def test_get_found_svg_unknown_uses_green(self):
        # unknown key → default color "green" file exists
        assert ip._get_found_svg("totally_unknown") is not None


# ── key normalization / db mapping ────────────────────────────────────────────

class TestKeys:
    def test_normalize_spaces_and_dashes(self):
        assert ip._normalize_key("Multi-Cache Thing") == "multi_cache_thing"

    def test_normalize_none(self):
        assert ip._normalize_key(None) == ""

    def test_db_type_known(self):
        assert ip._db_type_to_key("Traditional Cache") == "traditional"

    def test_db_type_unknown_falls_back_to_normalize(self):
        assert ip._db_type_to_key("Some New Type") == "some_new_type"

    def test_get_all_type_keys_sorted_unique(self):
        keys = ip.get_all_type_keys()
        assert keys == sorted(set(keys))
        assert "traditional" in keys

    def test_get_all_size_keys(self):
        keys = ip.get_all_size_keys()
        assert "micro" in keys and "regular" in keys


# ── svg-for-key resolution (file vs fallback) ─────────────────────────────────

class TestSvgForKey:
    def test_uses_file_when_present(self):
        assert ip._get_svg_for_key("traditional") == ip._get_type_svg("traditional")

    def test_falls_back_for_status_key(self):
        # "found" has no type file → fallback dict entry
        assert ip._get_svg_for_key("found") == ip._FALLBACK_SVGS["found"]

    def test_falls_back_to_unknown_for_garbage(self):
        assert ip._get_svg_for_key("zzz") == ip._FALLBACK_SVGS["unknown"]

    def test_found_for_key_uses_file(self):
        assert ip._get_found_svg_for_key("traditional") == ip._get_found_svg("traditional")

    def test_found_for_key_fallback(self, monkeypatch):
        monkeypatch.setattr(ip, "_get_found_svg", lambda key: None)
        assert ip._get_found_svg_for_key("traditional") == ip._FALLBACK_SVGS["found"]


# ── rendering ─────────────────────────────────────────────────────────────────

class TestRendering:
    def test_svg_to_pixmap(self):
        px = ip._svg_to_pixmap(ip._FALLBACK_SVGS["traditional"], 32)
        assert isinstance(px, QPixmap)
        assert px.width() == 32 and px.height() == 32

    def test_cache_type_icon(self):
        assert isinstance(ip.get_cache_type_icon("Traditional Cache"), QIcon)

    def test_cache_type_icon_found(self):
        assert isinstance(ip.get_cache_type_icon("Traditional Cache", found=True), QIcon)

    def test_cache_size_icon_known(self):
        assert isinstance(ip.get_cache_size_icon("micro"), QIcon)

    def test_cache_size_icon_unknown_uses_other(self):
        assert isinstance(ip.get_cache_size_icon("ginormous"), QIcon)

    def test_cache_type_pixmap(self):
        assert isinstance(ip.get_cache_type_pixmap("Multi-cache"), QPixmap)

    def test_cache_type_pixmap_found(self):
        assert isinstance(ip.get_cache_type_pixmap("Multi-cache", found=True), QPixmap)


# ── map pin HTML ──────────────────────────────────────────────────────────────

class TestMapPin:
    def test_not_found_has_base_img_no_overlay(self):
        html = ip.get_map_pin_html("Traditional Cache")
        assert "data:image/svg+xml;base64," in html
        assert html.count("<img") == 1
        assert "position:relative" in html

    def test_found_has_overlay(self):
        html = ip.get_map_pin_html("Traditional Cache", found=True)
        assert html.count("<img") == 2
        assert "drop-shadow" in html

    def test_dnf_has_overlay(self):
        # Regression for #286: DNF caches show a dark-blue smiley overlay.
        html = ip.get_map_pin_html("Traditional Cache", dnf=True)
        assert html.count("<img") == 2

    def test_found_and_dnf_prefers_found(self):
        # found takes priority over dnf when both are set.
        html_found = ip.get_map_pin_html("Traditional Cache", found=True)
        html_both  = ip.get_map_pin_html("Traditional Cache", found=True, dnf=True)
        assert html_found == html_both


# ── composite pixmap ──────────────────────────────────────────────────────────

class TestCompositePixmap:
    def test_no_overlay_matches_plain(self):
        # Regression for #286: no found/dnf → same as get_cache_type_pixmap.
        composite = ip.get_cache_type_pixmap_composite("Traditional Cache", 32)
        plain = ip.get_cache_type_pixmap("Traditional Cache", 32)
        assert composite.size() == plain.size()

    def test_found_composite_returns_pixmap(self):
        pix = ip.get_cache_type_pixmap_composite("Traditional Cache", 28, found=True)
        assert isinstance(pix, QPixmap)
        assert not pix.isNull()

    def test_dnf_composite_returns_pixmap(self):
        pix = ip.get_cache_type_pixmap_composite("Multi-cache", 28, dnf=True)
        assert isinstance(pix, QPixmap)
        assert not pix.isNull()
