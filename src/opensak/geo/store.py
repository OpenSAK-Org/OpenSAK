# src/opensak/geo/store.py — open boundaries.db and load GeoJSON packs.

from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opensak.logger import get_logger
from opensak.debug_flags import is_debug_enabled

log = get_logger("geo.store")

# Boundary layers, coarse to fine. A county hit can fill all three fields.
LAYERS = ("country", "state", "county")

# layer -> on-disk subdirectory holding that layer's GeoJSON packs
_LAYER_DIR = {"country": "countries", "state": "states", "county": "counties"}


def default_data_dir() -> Path:
    # Overridable via env var (dev + CI). Otherwise the per-user app-data dir —
    # writable, shared by the bundled-baseline seed and on-demand county
    # fetches (see ensure_baseline_seeded below and geo/packs.py).
    override = os.environ.get("OPENSAK_BOUNDARIES_DIR")
    if override:
        return Path(override)
    from opensak.config import get_app_data_dir
    return get_app_data_dir() / "boundaries"


def _frozen_bundle_dir() -> Path | None:
    # Read-only baseline bundled by PyInstaller (see opensak.spec), if any.
    # Frozen builds re-extract to sys._MEIPASS on every launch — never write here.
    if not getattr(sys, "frozen", False):
        return None
    bundled = Path(sys._MEIPASS) / "data"  # type: ignore[attr-defined]
    return bundled if (bundled / "boundaries.db").is_file() else None


def ensure_baseline_seeded(data_dir: Path | None = None) -> None:
    """
    Seed boundaries.db + countries/ + states/ into data_dir on first run, if
    not already present. Prefers the bundled baseline (frozen builds); falls
    back to downloading from OpenSAK-Data when nothing is bundled (e.g. a pip
    install, or a build that didn't have data/ available at package time).
    Counties are never seeded here — always fetched on demand, per country.
    """
    data_dir = data_dir or default_data_dir()
    if (data_dir / "boundaries.db").is_file():
        return

    bundled = _frozen_bundle_dir()
    if bundled is not None:
        _copy_baseline(bundled, data_dir)
        return

    from opensak.geo import packs
    packs.fetch_baseline(data_dir)


def _copy_baseline(src: Path, dst: Path) -> None:
    import shutil
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "boundaries.db", dst / "boundaries.db")
    for sub in ("countries", "states"):
        src_sub = src / sub
        if not src_sub.is_dir():
            continue
        dst_sub = dst / sub
        dst_sub.mkdir(exist_ok=True)
        for f in src_sub.glob("*.geojson"):
            shutil.copy2(f, dst_sub / f.name)


@dataclass(frozen=True)
class Region:
    id: int
    name: str
    parent: str | None  # 'PT' or 'US/TX' — links county -> state -> country
    pack: str
    feature_index: int


class BoundaryStore:
    """Read-only access to boundaries.db and the GeoJSON packs beside it."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self._db: sqlite3.Connection | None = None
        self._packs: dict[tuple[str, str], Any] = {}  # (layer, pack filename) -> parsed FeatureCollection
        if is_debug_enabled("geo"):
            log.debug("BoundaryStore initialized with data_dir=%s", self.data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "boundaries.db"

    def available(self) -> bool:
        return self.db_path.is_file()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        self._packs.clear()

    def candidates(self, layer: str, lat: float, lon: float) -> list[int]:
        # Stage 1: ids of every R-Tree box that contains the point.
        cur = self._conn().execute(
            f"SELECT id FROM rtree_{self._layer(layer)} "
            "WHERE min_lat <= ? AND max_lat >= ? AND min_lon <= ? AND max_lon >= ?",
            (lat, lat, lon, lon),
        )
        return [int(row["id"]) for row in cur.fetchall()]

    def region(self, layer: str, region_id: int) -> Region | None:
        cur = self._conn().execute(
            f"SELECT id, name, parent, pack, feature_index "
            f"FROM region_{self._layer(layer)} WHERE id = ?",
            (region_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return Region(
            id=int(row["id"]),
            name=str(row["name"]),
            parent=row["parent"],
            pack=str(row["pack"]),
            feature_index=int(row["feature_index"]),
        )

    def geometry(self, layer: str, region: Region) -> dict[str, Any]:
        # Stage 2 input: the region's GeoJSON geometry, lazily loaded and cached.
        pack = self._load_pack(layer, region.pack)
        feature = pack["features"][region.feature_index]
        return feature["geometry"]

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            if is_debug_enabled("geo"):
                log.debug("opening boundaries.db at %s", self.db_path)
            self._db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            self._db.row_factory = sqlite3.Row
        return self._db

    def _load_pack(self, layer: str, pack: str) -> dict[str, Any]:
        cache_key = (layer, pack)
        cached = self._packs.get(cache_key)
        if cached is None:
            path = self.data_dir / _LAYER_DIR[self._layer(layer)] / pack
            if not path.is_file() and layer == "county":
                # On-demand fetch: county packs are not bundled, downloaded lazily.
                if is_debug_enabled("geo"):
                    log.debug("fetching county pack: %s", pack)
                from opensak.geo import packs as _packs
                _packs.fetch_pack(pack, path.parent)
            if is_debug_enabled("geo"):
                log.debug("loading pack: %s", path)
            cached = json.loads(path.read_text(encoding="utf-8"))
            self._packs[cache_key] = cached
        return cached

    def dataset_version(self) -> str:
        cur = self._conn().execute(
            "SELECT version FROM file_version WHERE layer = 'dataset'"
        )
        row = cur.fetchone()
        return str(row["version"]) if row else "unknown"

    @staticmethod
    def _layer(layer: str) -> str:
        if layer not in _LAYER_DIR:
            raise ValueError(f"unknown boundary layer: {layer!r}")
        return layer
