# tests/unit-tests/test_tile_disk_cache_747.py — #747: tiles of keyed layers
# come through the tile proxy (so the API key stays out of the page) and are
# cached by it on disk, filed by layer/z/x/y — never by the provider's URL,
# which holds the key — for as long as the provider's Cache-Control allows.

from __future__ import annotations

import os

import pytest

from opensak.gui.tile_disk_cache import TileDiskCache, parse_cache_control

T0 = 1_000_000.0


@pytest.fixture
def cache(tmp_path):
    return TileDiskCache(tmp_path / "proxy_tiles", 10 * 1024 * 1024)


class TestCacheControl:
    @pytest.mark.parametrize("header, store, max_age, stale", [
        ("public, max-age=86400", True, 86400, 0),
        ("max-age=3600, stale-while-revalidate=604800", True, 3600, 604800),
        ('max-age="60", stale-if-error=120', True, 60, 120),
        ("no-store", False, 0, 0),
        ("max-age=3600, no-cache", True, 0, 0),
        ("", True, 0, 0),
        (None, True, 0, 0),
    ])
    def test_parse(self, header, store, max_age, stale):
        policy = parse_cache_control(header)
        assert (policy.store, policy.max_age, policy.stale) == (store, max_age, stale)


class TestTileDiskCache:
    def test_fresh_then_stale_then_gone(self, cache):
        cache.put("maptiler_aerial", 3, 1, 2, b"IMG", "image/jpeg",
                  "max-age=100, stale-while-revalidate=50", now=T0)
        tile = cache.get("maptiler_aerial", 3, 1, 2, now=T0 + 10)
        assert tile is not None and tile.fresh and tile.data == b"IMG"
        assert tile.mime == "image/jpeg"
        tile = cache.get("maptiler_aerial", 3, 1, 2, now=T0 + 120)
        assert tile is not None and not tile.fresh   # only for when offline
        assert cache.get("maptiler_aerial", 3, 1, 2, now=T0 + 200) is None
        assert cache.size_bytes() == 0               # expired file removed

    def test_filed_by_layer_and_tile_only(self, cache):
        cache.put("maptiler_aerial", 3, 1, 2, b"IMG", "image/jpeg", "max-age=100", now=T0)
        files = [p.relative_to(cache.root).as_posix() for p in cache.root.rglob("*.tile")]
        assert files == ["maptiler_aerial/3/1/2.tile"]

    @pytest.mark.parametrize("header", ["no-store", "", "max-age=0"])
    def test_not_stored_when_not_allowed(self, cache, header):
        cache.put("maptiler_aerial", 3, 1, 2, b"IMG", "image/jpeg", header, now=T0)
        assert cache.get("maptiler_aerial", 3, 1, 2, now=T0) is None

    def test_disabled_with_zero_size(self, tmp_path):
        cache = TileDiskCache(tmp_path / "c", 0)
        cache.put("maptiler_aerial", 3, 1, 2, b"IMG", "image/jpeg", "max-age=100", now=T0)
        assert not (tmp_path / "c").exists()

    def test_corrupt_file_ignored(self, cache):
        path = cache.root / "maptiler_aerial" / "3" / "1" / "2.tile"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not json\nIMG")
        assert cache.get("maptiler_aerial", 3, 1, 2) is None

    def test_prune_drops_least_recently_used(self, tmp_path):
        cache = TileDiskCache(tmp_path / "c", 3000)
        for y in range(4):
            cache.put("maptiler_aerial", 3, 1, y, b"x" * 900, "image/png", "max-age=100")
            path = cache.root / "maptiler_aerial" / "3" / "1" / f"{y}.tile"
            os.utime(path, (T0 + y, T0 + y))
        cache.prune()
        left = sorted(p.stem for p in cache.root.rglob("*.tile"))
        assert "0" not in left and left[-2:] == ["2", "3"]
        assert cache.size_bytes() <= 3000 * 0.9

    def test_clear(self, cache):
        cache.put("maptiler_aerial", 3, 1, 2, b"IMG", "image/jpeg", "max-age=100")
        cache.clear()
        assert cache.size_bytes() == 0


def test_map_profile_counts_and_clears_proxy_tiles():
    from opensak.gui import map_profile
    proxy = map_profile.proxy_tile_cache()
    proxy.put("maptiler_aerial", 3, 1, 2, b"IMG" * 100, "image/jpeg", "max-age=100")
    assert map_profile.cache_size_bytes() >= 300
    map_profile.clear_cache()
    assert proxy.size_bytes() == 0
