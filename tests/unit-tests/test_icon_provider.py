# tests/unit-tests/test_icon_provider.py — cache icon/pixmap/pin provider.

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QIcon, QPixmap

from opensak.gui import icon_provider as ip


@pytest.fixture(autouse=True)
def _app(qapp):
    # QPainter/QPixmap need a QApplication.
    yield


@pytest.fixture(autouse=True)
def _clear_user_icons_dir_cache():
    # _user_icons_dir() is lru_cache'd (issue #540) — clear before AND after
    # every test so tests that monkeypatch get_icons_dir() don't leak a
    # stale cached value into unrelated tests (or vice versa).
    ip._user_icons_dir.cache_clear()
    yield
    ip._user_icons_dir.cache_clear()


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

    def test_get_found_overlay_svg(self):
        # Issue #593: found-smiley set simplified to gold (found) + dark_blue
        # (dnf) only — no more per-type coloring.
        assert ip._get_found_overlay_svg() is not None

    def test_get_dnf_overlay_svg(self):
        assert ip._get_dnf_overlay_svg() is not None


# ── Issue #540: _user_icons_dir() must be memoized ────────────────────────────
# Un-cached, every single icon lookup (type icon + found-smiley, per cache row)
# re-ran the full get_icons_dir() -> get_app_data_dir() -> get_install_dir()
# chain: a file-exists check, a JSON read+parse, and four separate mkdir()
# calls. On a normal filesystem that's fast, but with each individual
# filesystem call intercepted synchronously by antivirus real-time protection
# (common on Windows, especially for mkdir), tens of thousands of calls across
# a large cache list added up to the reported 45-60s freeze — with low CPU and
# low disk throughput the whole time, since each call is tiny but blocking.

class TestUserIconsDirCaching:
    def test_only_calls_get_icons_dir_once(self, monkeypatch, tmp_path):
        calls = []

        def fake_get_icons_dir():
            calls.append(1)
            return tmp_path

        monkeypatch.setattr("opensak.config.get_icons_dir", fake_get_icons_dir)
        ip._user_icons_dir()
        ip._user_icons_dir()
        ip._user_icons_dir()
        assert len(calls) == 1

    def test_icon_lookups_across_many_rows_only_resolve_dir_once(self, monkeypatch, tmp_path):
        # Mirrors the real freeze scenario: rendering many cache rows' type
        # and found-smiley icons must not re-resolve the icons dir per row.
        calls = []

        def fake_get_icons_dir():
            calls.append(1)
            return tmp_path

        monkeypatch.setattr("opensak.config.get_icons_dir", fake_get_icons_dir)
        for _ in range(500):
            ip._get_type_svg("traditional")
            ip._get_found_overlay_svg()
        assert len(calls) == 1

    def test_falls_back_to_none_and_stays_cached_on_error(self, monkeypatch):
        calls = []

        def raising_get_icons_dir():
            calls.append(1)
            raise OSError("disk full")

        monkeypatch.setattr("opensak.config.get_icons_dir", raising_get_icons_dir)
        assert ip._user_icons_dir() is None
        assert ip._user_icons_dir() is None
        assert len(calls) == 1


# ── Issue #519: user icon override actually takes priority ──────────────────

class TestUserIconOverride:
    def test_user_override_takes_priority_over_bundled(self, monkeypatch, tmp_path):
        (tmp_path / "cache_types").mkdir()
        (tmp_path / "cache_types" / "traditional_cache.svg").write_text(
            "<svg>CUSTOM</svg>", encoding="utf-8"
        )
        monkeypatch.setattr("opensak.config.get_icons_dir", lambda: tmp_path)
        assert ip._get_type_svg("traditional") == "<svg>CUSTOM</svg>"

    def test_falls_back_to_bundled_when_user_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("opensak.config.get_icons_dir", lambda: tmp_path)
        svg = ip._get_type_svg("traditional")
        assert svg is not None
        assert "CUSTOM" not in svg


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


# ── rendering ─────────────────────────────────────────────────────────────────

class TestRendering:
    def test_svg_to_pixmap(self):
        px = ip._svg_to_pixmap(ip._FALLBACK_SVGS["traditional"], 32)
        assert isinstance(px, QPixmap)
        assert px.width() == 32 and px.height() == 32

    def test_cache_type_icon(self):
        assert isinstance(ip.get_cache_type_icon("Traditional Cache"), QIcon)

    def test_cache_size_icon_known(self):
        assert isinstance(ip.get_cache_size_icon("micro"), QIcon)

    def test_cache_size_icon_unknown_uses_other(self):
        assert isinstance(ip.get_cache_size_icon("ginormous"), QIcon)

    def test_cache_type_pixmap(self):
        assert isinstance(ip.get_cache_type_pixmap("Multi-cache"), QPixmap)


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


# ── Custom waypoint types (CUSTOM_WP_TYPES) get their own distinct icons ──────

class TestCustomWaypointIcons:
    # Raw strings as they appear in utils.constants.CUSTOM_WP_TYPES, and the
    # internal icon key each one should resolve to.
    _TYPES = {
        "Parking Area":    "parking_area",
        "Trailhead":       "trailhead",
        "Stage":           "stage",
        "Final Location":  "final_location",
        "Reference Point": "reference_point",
        "Waypoint":        "waypoint",
        "Hotel/POI":       "hotel_poi",
        "Custom":          "custom_wp",
    }

    def test_db_type_to_key_maps_each_custom_wp_type(self):
        for raw, key in self._TYPES.items():
            assert ip._db_type_to_key(raw) == key

    def test_each_custom_wp_type_has_a_fallback_svg(self):
        for key in self._TYPES.values():
            assert key in ip._FALLBACK_SVGS

    def test_each_custom_wp_type_has_a_file_map_entry(self):
        # So users can override it via icons/cache_types/<name>.svg like any
        # other cache type icon (issue #519 mechanism).
        for key in self._TYPES.values():
            assert key in ip._TYPE_FILE_MAP

    def test_custom_wp_icons_are_distinct_from_each_other_and_from_unknown(self):
        svgs = {key: ip._FALLBACK_SVGS[key] for key in self._TYPES.values()}
        svgs["unknown"] = ip._FALLBACK_SVGS["unknown"]
        assert len(set(svgs.values())) == len(svgs)

    def test_get_cache_type_icon_for_custom_wp_types(self):
        for raw in self._TYPES:
            assert isinstance(ip.get_cache_type_icon(raw), QIcon)

    def test_hotel_poi_slash_does_not_leak_into_key(self):
        # "Hotel/POI".lower() would normalize to "hotel/poi" (only spaces and
        # dashes are stripped) without an explicit _DB_TYPE_KEY_MAP entry.
        assert ip._db_type_to_key("Hotel/POI") == "hotel_poi"


# ── Issue #593: unused found-smiley colour variants removed ──────────────────
# Only "gold" (Found overlay + "Found" column) and "dark_blue" (DNF overlay)
# are ever actually rendered by the app — the other 12 colour variants and
# the per-type colour-selection machinery (_FOUND_COLOR_MAP,
# _get_found_svg/_get_found_svg_for_key, the `found=` param on
# get_cache_type_icon/get_cache_type_pixmap) were dead code.

class TestFoundIconSetSimplified:
    def test_removed_helpers_are_gone(self):
        assert not hasattr(ip, "_FOUND_COLOR_MAP")
        assert not hasattr(ip, "_get_found_svg")
        assert not hasattr(ip, "_get_found_svg_for_key")

    def test_get_cache_type_icon_has_no_found_param(self):
        import inspect
        assert "found" not in inspect.signature(ip.get_cache_type_icon).parameters

    def test_get_cache_type_pixmap_has_no_found_param(self):
        import inspect
        assert "found" not in inspect.signature(ip.get_cache_type_pixmap).parameters

    def test_only_gold_and_dark_blue_bundled(self):
        bundled = sorted(
            f.stem for f in ip._CACHE_FOUND_DIR.glob("found_cache_smiley_*.svg")
        )
        assert bundled == ["found_cache_smiley_dark_blue", "found_cache_smiley_gold"]

    def test_found_and_dnf_overlays_still_work(self):
        # The two colours that ARE used (found column, map/detail overlays)
        # must keep working after the cleanup.
        assert ip._get_found_overlay_svg() is not None
        assert ip._get_dnf_overlay_svg() is not None
        assert ip._get_found_overlay_svg() != ip._get_dnf_overlay_svg()
