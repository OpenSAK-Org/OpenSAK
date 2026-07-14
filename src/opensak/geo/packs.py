# src/opensak/geo/packs.py — baseline + on-demand county pack fetching from OpenSAK-Data.

from __future__ import annotations

import http.client
import json
import os
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from urllib.error import URLError

from opensak.logger import get_logger

log = get_logger("geo.packs")

# GitHub Releases base URL for the boundary dataset repository.
# County packs and boundaries.db are published as flat release assets here.
DATA_REPO_URL = "https://github.com/OpenSAK-Org/OpenSAK-Data/releases/latest/download"
MANIFEST_FILENAME = "manifest.json"
# Re-check remote at most once per week; override with force=True for manual checks.
THROTTLE_SECONDS = 7 * 24 * 3600
REQUEST_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60
# Baseline downloads are I/O-bound (network round-trip, not CPU) — urllib
# releases the GIL during the actual socket wait, so a higher worker count
# than a CPU-bound pool (e.g. the resolver's min(4, cpu_count)) is fine here.
BASELINE_FETCH_WORKERS = 16
# http.client.HTTPException (e.g. InvalidURL) is not an OSError/URLError
# subclass, so it isn't caught by a plain except (URLError, OSError) — a
# malformed URL (bad characters in a filename) would otherwise crash the
# whole download instead of degrading to "this one file failed".
_FETCH_ERRORS = (URLError, OSError, http.client.HTTPException)


def _asset_url(filename: str) -> str:
    # Filenames should always be pre-sanitized by the generator (no spaces or
    # other characters GitHub would rewrite on upload) — quote() defensively
    # anyway so a stray character degrades to a 404, not an unhandled
    # http.client.InvalidURL crashing the whole download.
    return f"{DATA_REPO_URL}/{urllib.parse.quote(filename)}"


def fetch_manifest(timeout: int = REQUEST_TIMEOUT) -> dict | None:
    """Download manifest.json from OpenSAK-Data. Returns parsed dict or None on error."""
    try:
        with urllib.request.urlopen(_asset_url(MANIFEST_FILENAME), timeout=timeout) as resp:
            return json.load(resp)  # type: ignore[no-any-return]
    except (*_FETCH_ERRORS, json.JSONDecodeError) as exc:
        log.debug("manifest fetch failed: %s", exc)
        return None


def fetch_pack(filename: str, dest_dir: Path, timeout: int = DOWNLOAD_TIMEOUT) -> bool:
    """
    Download a county pack to dest_dir/filename atomically.
    Returns True on success, False on network or I/O error.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(_asset_url(filename), timeout=timeout) as resp:
            data = resp.read()
    except _FETCH_ERRORS as exc:
        log.debug("pack fetch failed (%s): %s", filename, exc)
        return False
    return _atomic_write(dest_dir, filename, data)


def fetch_all(
    data_dir: Path,
    progress_cb: Callable[[int, int], None] | None = None,
) -> int:
    """
    Download every county pack not yet present in data_dir/counties/.
    progress_cb(done, total) is called after each attempted fetch.
    Returns count of packs successfully downloaded.
    """
    manifest = fetch_manifest()
    pack_names: list[str] = list((manifest or {}).get("packs", {}).keys())
    counties_dir = data_dir / "counties"
    to_fetch = [fn for fn in pack_names if not (counties_dir / fn).is_file()]
    total = len(to_fetch)
    downloaded = 0
    for fn in to_fetch:
        if fetch_pack(fn, counties_dir):
            downloaded += 1
        if progress_cb:
            progress_cb(downloaded, total)
    return downloaded


def fetch_baseline(data_dir: Path, manifest: dict | None = None) -> bool:
    """
    Download boundaries.db + the country/state baseline (manifest["baseline"])
    into data_dir. Used on first run when no bundled baseline is present —
    e.g. a pip install, or a build where data/ wasn't available at package
    time. Returns True if boundaries.db was fetched (baseline usable); an
    individual state-pack failure is skipped rather than failing the whole
    call, since country-level resolution still works without every state.
    """
    manifest = manifest or fetch_manifest()
    if manifest is None:
        return False
    if not _fetch_file_atomic("boundaries.db", data_dir):
        return False

    baseline = list(manifest.get("baseline", {}))
    if not baseline:
        return True

    def _fetch_one(filename: str) -> None:
        dest_dir = data_dir / ("countries" if filename == "world.geojson" else "states")
        fetch_pack(filename, dest_dir)

    with ThreadPoolExecutor(max_workers=min(BASELINE_FETCH_WORKERS, len(baseline))) as executor:
        list(executor.map(_fetch_one, baseline))
    return True


def check_update(data_dir: Path, force: bool = False) -> tuple[bool, dict | None]:
    """
    Return (newer_available, manifest).
    Throttled to at most once per THROTTLE_SECONDS unless force=True.
    A network failure returns (False, None).
    """
    manifest_local = data_dir / MANIFEST_FILENAME
    if not force and manifest_local.is_file():
        age = time.time() - manifest_local.stat().st_mtime
        if age < THROTTLE_SECONDS:
            return False, None

    manifest = fetch_manifest()
    if manifest is None:
        return False, None

    local_ver = _local_dataset_version(data_dir)
    remote_ver = str(manifest.get("dataset_version", ""))
    return (bool(remote_ver) and remote_ver != local_ver), manifest


def apply_update(
    data_dir: Path,
    manifest: dict,
    progress_cb: Callable[[str], None] | None = None,
) -> list[str]:
    """
    Atomically apply a data update from manifest.
    Downloads new boundaries.db and any locally-cached county packs that changed.
    Returns list of filenames that were updated.
    Caches not yet downloaded are skipped (they will fetch the new version on demand).
    """
    updated: list[str] = []
    packs_info: dict = manifest.get("packs", {})
    counties_dir = data_dir / "counties"

    if _fetch_file_atomic("boundaries.db", data_dir):
        updated.append("boundaries.db")
        if progress_cb:
            progress_cb("boundaries.db")

    for filename, info in packs_info.items():
        local = counties_dir / filename
        if not local.is_file():
            continue  # not locally cached — will fetch fresh on next demand
        remote_ver = str(info.get("version", ""))
        if remote_ver and remote_ver != _local_pack_version(local):
            if fetch_pack(filename, counties_dir):
                updated.append(filename)
                if progress_cb:
                    progress_cb(filename)

    _save_manifest(data_dir, manifest)
    return updated


# ── Internal helpers ──────────────────────────────────────────────────────────

def _atomic_write(dest_dir: Path, filename: str, data: bytes) -> bool:
    dest = dest_dir / filename
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        return True
    except OSError as exc:
        log.debug("atomic write failed (%s): %s", filename, exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def _fetch_file_atomic(filename: str, dest_dir: Path) -> bool:
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(_asset_url(filename), timeout=DOWNLOAD_TIMEOUT) as resp:
            data = resp.read()
    except _FETCH_ERRORS as exc:
        log.debug("file fetch failed (%s): %s", filename, exc)
        return False
    return _atomic_write(dest_dir, filename, data)


def _local_dataset_version(data_dir: Path) -> str:
    db_path = data_dir / "boundaries.db"
    if not db_path.is_file():
        return ""
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.execute("SELECT version FROM file_version WHERE layer = 'dataset'")
        row = cur.fetchone()
        con.close()
        return str(row[0]) if row else ""
    except (sqlite3.Error, OSError):
        return ""


def _local_pack_version(pack_path: Path) -> str:
    try:
        fc = json.loads(pack_path.read_text(encoding="utf-8"))
        features = fc.get("features", [])
        if features:
            return str(features[0].get("properties", {}).get("version", ""))
    except (json.JSONDecodeError, OSError):
        pass
    return ""


def _save_manifest(data_dir: Path, manifest: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=data_dir, prefix=".tmp_manifest_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        os.replace(tmp, data_dir / MANIFEST_FILENAME)
    except OSError as exc:
        log.debug("manifest save failed: %s", exc)
