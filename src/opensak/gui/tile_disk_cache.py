"""
tile_disk_cache.py — disk cache for tiles fetched through the tile proxy (#747).

Every layer is loaded through opensak://tiles/ (web_assets.py, #1361), and
Chromium does not cache custom-scheme responses: this cache is what keeps the
map working offline (#1111), and what Offline mode serves from. Layers whose
tiles must not be kept on the device (MapLayer.offline is False) never come
here.

Tiles are filed by layer id and z/x/y — never by the provider's URL, which
holds the key:

    <map_cache>/proxy_tiles/<layer>/<z>/<x>/<y>.tile

Each file is one JSON header line ({"mime", "expires", "stale_until"}) and
the tile bytes. Freshness follows the provider's Cache-Control header the way
Chromium's cache does: a tile is served without asking the provider until
max-age runs out, and after that only when the provider cannot be reached
(or Offline mode is on), for as long as stale-while-revalidate /
stale-if-error allow. "no-store" is never written. The folder is kept under
the size cap set in Settings → Map → tile cache; the tiles used least
recently go first. The cache keeps count of the bytes it holds, so the
folder is only walked once it may be over the cap.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from opensak.logger import get_logger

log = get_logger("tile_disk_cache")

# Prune after this many writes; walking the folder on every tile is wasteful.
_PRUNE_EVERY = 50
# Pruning stops once the folder is down to this share of the cap.
_PRUNE_TO = 0.9


@dataclass(frozen=True)
class CachedTile:
    data: bytes
    mime: str
    fresh: bool   # within max-age: no need to ask the provider


@dataclass(frozen=True)
class CachePolicy:
    store: bool
    max_age: int
    stale: int


_DIRECTIVE_RE = re.compile(r"([a-z-]+)\s*(?:=\s*\"?(\d+)\"?)?")


def parse_cache_control(header: str | None) -> CachePolicy:
    """What the provider's Cache-Control header allows. Without a header
    (or with max-age=0 and no stale allowance) nothing may be kept."""
    directives: dict[str, int | None] = {}
    for m in _DIRECTIVE_RE.finditer((header or "").lower()):
        directives[m.group(1)] = int(m.group(2)) if m.group(2) else None
    if "no-store" in directives:
        return CachePolicy(store=False, max_age=0, stale=0)
    max_age = directives.get("max-age") or 0
    if "no-cache" in directives:
        max_age = 0
    stale = max(directives.get("stale-while-revalidate") or 0,
                directives.get("stale-if-error") or 0)
    return CachePolicy(store=True, max_age=max_age, stale=stale)


class TileDiskCache:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self._writes = 0
        # Bytes in the folder, once counted (None: not counted yet).
        self._size: int | None = None

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0

    def _path(self, layer_id: str, z: int, x: int, y: int) -> Path:
        return self.root / layer_id / str(z) / str(x) / f"{y}.tile"

    def get(self, layer_id: str, z: int, x: int, y: int,
            now: float | None = None) -> CachedTile | None:
        if not self.enabled:
            return None
        path = self._path(layer_id, z, x, y)
        try:
            raw = path.read_bytes()
            header, _, data = raw.partition(b"\n")
            meta = json.loads(header)
            expires = float(meta["expires"])
            stale_until = float(meta["stale_until"])
            mime = str(meta["mime"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        now = time.time() if now is None else now
        if now > stale_until:
            path.unlink(missing_ok=True)
            if self._size is not None:
                self._size -= len(raw)
            return None
        try:
            path.touch()   # least recently used goes first when pruning
        except OSError:
            pass
        return CachedTile(data, mime, fresh=now < expires)

    def put(self, layer_id: str, z: int, x: int, y: int, data: bytes, mime: str,
            cache_control: str | None, now: float | None = None) -> None:
        if not self.enabled:
            return
        policy = parse_cache_control(cache_control)
        if not policy.store or policy.max_age + policy.stale <= 0:
            return
        now = time.time() if now is None else now
        expires = now + policy.max_age
        meta = {"mime": mime, "expires": expires, "stale_until": expires + policy.stale}
        path = self._path(layer_id, z, x, y)
        raw = json.dumps(meta).encode() + b"\n" + data
        try:
            old = path.stat().st_size if path.is_file() else 0
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(raw)
            tmp.replace(path)
        except OSError as exc:
            log.debug("tile not cached: %s", exc)
            return
        if self._size is not None:
            self._size += len(raw) - old
        self._writes += 1
        if self._writes % _PRUNE_EVERY == 0:
            self.prune()

    def size_bytes(self) -> int:
        return sum(size for _, size, _ in self._files())

    def prune(self) -> None:
        """Remove the least recently used tiles until under the cap."""
        if self._size is not None and self._size <= self.max_bytes:
            return
        files = self._files()
        total = sum(size for _, size, _ in files)
        self._size = total
        if total <= self.max_bytes:
            return
        target = self.max_bytes * _PRUNE_TO
        for path, size, _ in sorted(files, key=lambda f: f[2]):
            path.unlink(missing_ok=True)
            total -= size
            if total <= target:
                break
        self._size = total

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self._size = 0

    def _files(self) -> list[tuple[Path, int, float]]:
        out: list[tuple[Path, int, float]] = []
        if not self.root.is_dir():
            return out
        for path in self.root.rglob("*.tile"):
            try:
                st = path.stat()
            except OSError:
                continue
            out.append((path, st.st_size, st.st_mtime))
        return out
