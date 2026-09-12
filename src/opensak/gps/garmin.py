"""
src/opensak/gps/garmin.py — Garmin GPS device detection og GPX/LOC/GGZ export.

Understøtter Garmin enheder der monteres som USB drev eller som MTP-lager
og accepterer GPX filer i /Garmin/GPX/ mappen.

Testet med: GPSMAP64s, Oregon750

Corrected coordinates: Hvis en cache har bruger-korrigerede koordinater
(user_note.is_corrected), bruges disse som waypoint-koordinater i GPX
filen. De originale koordinater gemmes i en groundspeak:original_coords
kommentar, så de ikke mistes.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from opensak.lang import tr
from opensak.utils.constants import CUSTOM_WP_TYPES

if TYPE_CHECKING:
    from opensak.gps.mtp import MTPDevice


# ── Garmin GPX/GGZ mapper på enheden ──────────────────────────────────────────

GARMIN_GPX_SUBPATH = Path("Garmin") / "GPX"
GARMIN_GGZ_SUBPATH = Path("Garmin") / "GGZ"
GARMIN_MARKERS = [
    Path("Garmin") / "GarminDevice.xml",
    Path("Garmin") / "GPX",
    Path(".is_garmin"),
]

# MTP Garmin devices use uppercase folder names and have a storage root
# (e.g. "Internal Storage") between the GVFS mount and the GARMIN folder.
# Tuple (not set) so iteration order is deterministic — "Garmin" first to
# avoid returning a lowercase path on case-insensitive filesystems (macOS).
_MTP_GARMIN_FOLDER_NAMES = ("Garmin", "GARMIN", "garmin")
_MTP_GARMIN_MARKER_NAMES = {"GarminDevice.xml", "GPX"}


# ── Enhed detektion ───────────────────────────────────────────────────────────

def find_garmin_devices() -> list[Path | MTPDevice]:
    """
    Find Garmin GPS-enheder.
    Søger først efter normale writable mount points og derefter efter GVFS/MTP
    Garmin-monteringer på Linux.

    Virker på Linux, Windows og macOS.
    """
    devices: list[Path | MTPDevice] = []
    seen: set[Path] = set()

    for candidate in _get_mount_points():
        if candidate in seen:
            continue
        if not _is_writable_directory(candidate):
            seen.add(candidate)
            continue
        seen.add(candidate)
        if _is_garmin(candidate):
            devices.append(candidate)

    for candidate in _linux_mtp_mounts():
        if candidate in seen:
            continue
        seen.add(candidate)
        if _is_garmin_mtp_mount(candidate):
            devices.append(candidate)

    # MTP devices do not receive a Windows drive letter. Keep this optional
    # and Windows-only so mass-storage support remains dependency-free.
    if platform.system() == "Windows":
        try:
            from opensak.gps.mtp import find_mtp_devices
            devices.extend(find_mtp_devices())
        except (ImportError, OSError):
            pass

    return devices


def _is_garmin(path: Path) -> bool:
    """Tjek om en mappe er en Garmin enhed."""
    for marker in GARMIN_MARKERS:
        try:
            if (path / marker).exists():
                return True
        except OSError:
            continue
    return False


def _is_writable_directory(path: Path) -> bool:
    """Returnerer True for en faktisk skrivbar mount-point."""
    try:
        return path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


def _linux_mtp_mounts() -> list[Path]:
    """Returner GVFS/MTP-mounts på Linux."""
    candidates: list[Path] = []
    gvfs_roots = [Path("/run/user")]

    for root in gvfs_roots:
        if not root.exists():
            continue
        for sub in root.iterdir():
            gvfs_dir = sub / "gvfs"
            if not gvfs_dir.exists():
                continue
            for mount in gvfs_dir.iterdir():
                if "mtp:" in str(mount):
                    candidates.append(mount)

    return sorted(set(candidates))


def _is_garmin_mtp_mount(path: Path) -> bool:
    """Tjek om en GVFS/MTP-mount faktisk er en Garmin-enhed."""
    if not path or not path.is_dir():
        return False
    return _find_mtp_garmin_root(path) is not None


def _find_mtp_garmin_root(mtp_mount: Path) -> Optional[Path]:
    """
    Find the actual Garmin folder inside an MTP mount.

    MTP devices have a storage root (e.g. "Internal Storage") and the
    Garmin folder may be uppercase (GARMIN) or mixed case. Returns the
    path to the folder that contains GarminDevice.xml or a GPX subfolder,
    or None if not found.
    """
    try:
        for storage in mtp_mount.iterdir():
            if not storage.is_dir():
                continue
            for child in storage.iterdir():
                if not child.is_dir():
                    continue
                if child.name not in _MTP_GARMIN_FOLDER_NAMES:
                    continue
                for marker_name in _MTP_GARMIN_MARKER_NAMES:
                    if (child / marker_name).exists():
                        return child
    except OSError:
        pass
    return None


def _find_mtp_gpx_dir(mtp_mount: Path) -> Optional[Path]:
    """Find the GPX folder inside an MTP-mounted Garmin."""
    garmin = _find_mtp_garmin_root(mtp_mount)
    if garmin is None:
        return None
    gpx = garmin / "GPX"
    if gpx.is_dir():
        return gpx
    for child in garmin.iterdir():
        if child.is_dir() and child.name.upper() == "GPX":
            return child
    return None


def _find_mtp_ggz_dir(mtp_mount: Path) -> Optional[Path]:
    """Find the GGZ folder inside an MTP-mounted Garmin."""
    garmin = _find_mtp_garmin_root(mtp_mount)
    if garmin is None:
        return None
    ggz = garmin / "GGZ"
    if ggz.is_dir():
        return ggz
    for child in garmin.iterdir():
        if child.is_dir() and child.name.upper() == "GGZ":
            return child
    return None


def is_mtp_device(path: Path) -> bool:
    """Returnerer True hvis path er en GVFS/MTP-mount."""
    return "mtp:" in str(path)


# Active gio subprocess — stored so it can be killed on cancel.
_active_gio_proc: Optional[subprocess.Popen] = None


def _gio_subprocess_env() -> dict[str, str]:
    """Return an environment suitable for launching the host gio binary."""
    env = os.environ.copy()
    original_ld_library_path = env.get("APPIMAGE_ORIGINAL_LD_LIBRARY_PATH")

    # Packaged builds can prepend bundled GLib libraries to LD_LIBRARY_PATH.
    # Host gio must use the matching host GLib, otherwise symbol lookup can fail.
    env.pop("LD_LIBRARY_PATH", None)
    if original_ld_library_path:
        env["LD_LIBRARY_PATH"] = original_ld_library_path

    for key in ("GI_TYPELIB_PATH", "GIO_EXTRA_MODULES", "GIO_MODULE_DIR"):
        env.pop(key, None)
    return env


def cancel_mtp_transfer() -> None:
    """Afbryd en igangværende MTP-overførsel (gio copy)."""
    global _active_gio_proc
    proc = _active_gio_proc
    if proc is not None:
        try:
            proc.kill()
        except OSError:
            pass
        _active_gio_proc = None


def _gio_copy(local_file: Path, dest_path: Path) -> None:
    """
    Kopiér en lokal fil til en MTP-sti via gio copy.
    Raises OSError on failure.
    """
    global _active_gio_proc
    proc = subprocess.Popen(
        ["gio", "copy", str(local_file), str(dest_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_gio_subprocess_env(),
    )
    _active_gio_proc = proc
    try:
        stdout, stderr = proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise OSError("MTP transfer timed out (300s)")
    finally:
        _active_gio_proc = None
    if proc.returncode != 0:
        msg = (stderr or b"").decode(errors="replace").strip()
        if not msg:
            msg = f"gio copy failed (exit {proc.returncode})"
        raise OSError(msg)


def _gio_remove(path: Path) -> bool:
    """Slet en fil på en MTP-sti via gio remove. Returnerer True ved succes."""
    try:
        result = subprocess.run(
            ["gio", "remove", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
            env=_gio_subprocess_env(),
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _wait_until_missing(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _path_exists(path):
            return True
        time.sleep(0.2)
    return not _path_exists(path)


def _gio_remove_and_wait(path: Path, timeout: float = 10.0) -> bool:
    if not _path_exists(path):
        return True
    return _gio_remove(path) and _wait_until_missing(path, timeout=timeout)


def _gio_copy_with_replace(local_file: Path, dest_path: Path) -> None:
    if _path_exists(dest_path) and not _gio_remove_and_wait(dest_path):
        raise OSError(f"Could not remove existing MTP file: {dest_path.name}")
    dest_folder = dest_path.parent
    _ensure_mtp_space(local_file, dest_folder)
    try:
        _gio_copy(local_file, dest_folder)
    except OSError as error:
        if "Could not send object info" not in str(error):
            raise
        if _path_exists(dest_path) and not _gio_remove_and_wait(dest_path):
            raise
        time.sleep(1.0)
        _gio_copy(local_file, dest_folder)
    if not _path_exists(dest_path):
        raise OSError(f"MTP copy did not create expected file: {dest_path.name}")


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


def _ensure_mtp_space(local_file: Path, dest_folder: Path) -> None:
    try:
        needed = local_file.stat().st_size
        free = shutil.disk_usage(dest_folder).free
    except OSError:
        return
    if free < needed:
        raise OSError(
            f"Not enough free space on MTP device for {local_file.name}: "
            f"need {_format_bytes(needed)}, available {_format_bytes(free)}"
        )


def _get_mount_points() -> list[Path]:
    """Returner liste af mulige mount points afhængig af OS."""
    system = platform.system()

    if system == "Linux":
        return _linux_mounts()
    elif system == "Windows":
        return _windows_drives()
    elif system == "Darwin":
        return _macos_volumes()
    return []


def _linux_mounts() -> list[Path]:
    """
    Find USB mount points på Linux.

    Strategi (i prioriteret rækkefølge):
    1. lsblk — den mest pålidelige metode (returnerer kun faktiske mount points)
    2. /proc/mounts — fallback med korrekt operator-prioritet
    3. Direkte scanning af /media og /run/media — kun ét niveau dybt
    """
    candidates: set[Path] = set()

    # ── Metode 1: lsblk (bedst) ───────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["lsblk", "--output", "MOUNTPOINT", "--raw", "--noheadings"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line == "/":
                continue
            mount = Path(line)
            if mount.is_dir() and _is_removable_path(mount):
                candidates.add(mount)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # ── Metode 2: /proc/mounts (fallback) ─────────────────────────────────────
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mountpoint, fstype = parts[0], parts[1], parts[2]
                mount = Path(mountpoint)
                if mount.is_dir() and (
                    device.startswith("/dev/sd")
                    or device.startswith("/dev/sdb")
                    or fstype in ("vfat", "exfat", "ntfs", "msdos")
                ):
                    if _is_removable_path(mount):
                        candidates.add(mount)
    except (OSError, PermissionError):
        pass

    # ── Metode 3: Direkte scanning — kun ét niveau dybt ───────────────────────
    import getpass
    username = getpass.getuser()

    for base in [
        Path("/media") / username,
        Path("/run/media") / username,
        Path("/media"),
        Path("/mnt"),
    ]:
        if not base.exists():
            continue
        for item in base.iterdir():
            if item.is_dir() and item != base:
                candidates.add(item)
        for sub in base.glob("*/"):
            if sub.is_dir():
                candidates.add(sub)

    return list(candidates)


def _is_removable_path(path: Path) -> bool:
    """Returner True hvis stien ser ud til at være et flytbart medie."""
    path_str = str(path)
    removable_prefixes = (
        "/media/",
        "/run/media/",
        "/mnt/",
    )
    system_paths = ("/", "/boot", "/home", "/usr", "/var", "/etc", "/tmp",
                    "/proc", "/sys", "/dev", "/run/user")
    if path_str in system_paths:
        return False
    if any(path_str.startswith(p) for p in removable_prefixes):
        return True
    try:
        result = subprocess.run(
            ["lsblk", "--output", "MOUNTPOINT,RM", "--raw", "--noheadings"],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) == 2 and parts[0] == path_str and parts[1] == "1":
                return True
    except Exception:
        pass
    return False


def _windows_drives() -> list[Path]:
    """Find alle drevbogstaver på Windows."""
    import string
    drives = []
    for letter in string.ascii_uppercase:
        p = Path(f"{letter}:\\")
        if p.exists():
            drives.append(p)
    return drives


def _macos_volumes() -> list[Path]:
    """Find monterede volumes på macOS."""
    volumes = Path("/Volumes")
    if not volumes.exists():
        return []
    return [v for v in volumes.iterdir() if v.is_dir()]


def _get_garmin_folder(device_root: Path) -> Path:
    """Resolve the Garmin folder for mass-storage and MTP device layouts."""
    for folder_name in _MTP_GARMIN_FOLDER_NAMES:
        candidate = device_root / folder_name
        if candidate.is_dir():
            return candidate

    if is_mtp_device(device_root):
        garmin = _find_mtp_garmin_root(device_root)
        if garmin is not None:
            return garmin

    try:
        storage_roots = (device_root / Path()).glob("*")
        for storage_root in storage_roots:
            if not storage_root.is_dir():
                continue
            for folder_name in _MTP_GARMIN_FOLDER_NAMES:
                candidate = storage_root / folder_name
                if candidate.is_dir():
                    return candidate
    except (AttributeError, OSError):
        pass

    return device_root / "Garmin"


def get_garmin_gpx_path(device_root: Path) -> Path:
    """Returner stien til GPX mappen på en Garmin enhed."""
    return _get_garmin_folder(device_root) / "GPX"


def get_garmin_ggz_path(device_root: Path) -> Path:
    """Returner stien til GGZ mappen på en Garmin enhed."""
    return _get_garmin_folder(device_root) / "GGZ"


# ── Debug hjælper ─────────────────────────────────────────────────────────────

def debug_scan() -> str:
    """
    Returnerer en tekststreng med debug-info om hvad der scannes.
    Bruges fra GUI til at vise fejlsøgnings-info.
    """
    lines = ["=== Garmin scan debug ===", f"OS: {platform.system()}"]

    mounts = _get_mount_points()
    lines.append(f"\nFundne mount points ({len(mounts)}):")
    for m in sorted(mounts):
        is_g = _is_garmin(m)
        lines.append(f"  {'✓ GARMIN' if is_g else '○'} {m}")

    lines.append(f"\nGarmin enheder: {find_garmin_devices() or 'ingen fundet'}")
    return "\n".join(lines)


# ── GPX generator ─────────────────────────────────────────────────────────────

def _effective_coords(cache) -> tuple[float, float]:
    """
    Returner de koordinater der skal bruges til GPX export.

    Hvis cachen har korrigerede koordinater (user_note.is_corrected),
    bruges disse. Ellers bruges de originale koordinater.
    """
    note = getattr(cache, "user_note", None)
    if note and getattr(note, "is_corrected", False):
        lat = note.corrected_lat
        lon = note.corrected_lon
        if lat is not None and lon is not None:
            return lat, lon
    return cache.latitude, cache.longitude


def generate_gpx(caches: list, filename: str = "opensak_export", progress_cb=None) -> str:
    """
    Generer GPX indhold fra en liste af Cache objekter.
    Returnerer GPX som en streng klar til at skrive til fil.

    Bruger GPX 1.0 med groundspeak:cache som DIREKTE child af <wpt> — ikke
    pakket ind i et <extensions>-element (GPX 1.1-stil). Dette matcher GSAK's
    og de facto-standarden for geocaching-GPX-filer, som Garmin-enheders
    geocache-parser er bygget til at forvente (issue #656: en enhedstest
    viste hint blev vist korrekt, men description/logs blev ikke, hvilket
    passer med en firmware-parser der ikke finder groundspeak:cache når den
    ligger i <extensions> og derfor falder tilbage til kun at vise
    almindelig waypoint-info for de øvrige felter).

    Caches med korrigerede koordinater eksporteres med de korrigerede
    koordinater som waypoint-position. De originale koordinater bevares
    i en cmt (comment) feltom muligt.

    progress_cb(done, total): valgfrit kald per cache, så GUI kan vise fremgang.
    """
    from xml.etree.ElementTree import Element, SubElement
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone

    # Root element — GPX 1.0, matching GSAK's own export format. The
    # groundspeak namespace is deliberately NOT declared here; it's declared
    # locally on each <groundspeak:cache> element instead (see below),
    # mirroring GSAK's exact convention byte-for-byte.
    gpx = Element("gpx")
    gpx.set("version", "1.0")
    gpx.set("creator", "OpenSAK")
    gpx.set("xmlns", "http://www.topografix.com/GPX/1/0")
    gpx.set("xmlns:gsak", "http://www.gsak.net/xmlv1/6")
    gpx.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")

    # Metadata
    metadata = SubElement(gpx, "metadata")
    name_el = SubElement(metadata, "name")
    name_el.text = filename
    time_el = SubElement(metadata, "time")
    time_el.text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    total = len(caches)
    for i, cache in enumerate(caches, 1):
        if progress_cb:
            progress_cb(i, total)
        if cache.latitude is None or cache.longitude is None:
            continue

        # Brug korrigerede koordinater hvis de findes
        export_lat, export_lon = _effective_coords(cache)
        has_corrected = (export_lat != cache.latitude or export_lon != cache.longitude)

        wpt = SubElement(gpx, "wpt")
        wpt.set("lat", f"{export_lat:.6f}")
        wpt.set("lon", f"{export_lon:.6f}")

        if cache.hidden_date:
            time_wpt = SubElement(wpt, "time")
            time_wpt.text = cache.hidden_date.strftime("%Y-%m-%dT%H:%M:%SZ")

        name_wpt = SubElement(wpt, "name")
        name_wpt.text = cache.gc_code or ""

        desc_wpt = SubElement(wpt, "desc")
        desc_wpt.text = cache.name or ""

        # Gem originale koordinater i comment-feltet hvis vi bruger korrigerede
        if has_corrected:
            cmt_wpt = SubElement(wpt, "cmt")
            cmt_wpt.text = (
                f"Original: {cache.latitude:.6f}, {cache.longitude:.6f} | "
                f"Corrected coordinates used for export"
            )

        url_wpt = SubElement(wpt, "url")
        url_wpt.text = f"https://coord.info/{cache.gc_code}"

        # Custom Waypoints (issue #141: Hotel/POI, Parking Area, Trailhead,
        # etc.) are stored as Cache rows for simplicity, but they aren't
        # real geocaches — issue #660: exporting them wrapped in a full
        # groundspeak:cache block made them show up on Garmin devices as a
        # "fake geocache" with empty D/T stars and Size: (Not Chosen).
        # Export them instead as a plain GPX waypoint with a proper native
        # Garmin icon symbol, and skip the groundspeak:cache block entirely.
        is_custom_wp = (cache.cache_type or "") in CUSTOM_WP_TYPES

        sym_wpt = SubElement(wpt, "sym")
        sym_wpt.text = (
            _custom_wp_symbol(cache.cache_type or "")
            if is_custom_wp
            else _cache_symbol(cache.cache_type or "", getattr(cache, "found", False))
        )

        type_wpt = SubElement(wpt, "type")
        if is_custom_wp:
            type_wpt.text = cache.cache_type or "Waypoint"
        else:
            type_wpt.text = f"Geocache|{cache.cache_type or 'Traditional Cache'}"

        if not is_custom_wp:
            # groundspeak:cache — direct child of <wpt>, no <extensions>
            # wrapper (see docstring above). The xmlns:groundspeak
            # declaration is local to this element, matching GSAK's exact
            # convention.
            gs_cache = SubElement(wpt, "groundspeak:cache")
            # Issue #695: this used to be cache.id — the internal SQLAlchemy
            # auto-increment primary key, not the real Geocaching.com
            # numeric cache ID. cache.gc_cache_id holds the actual ID
            # (parsed from the source GPX/GSAK database). Caches imported
            # before this fix won't have it populated yet until re-imported
            # — "0" is used as a visibly-fake placeholder in that case,
            # rather than risk exporting another cache's (or an arbitrary
            # small) real-looking-but-wrong ID.
            gs_cache.set("id", str(cache.gc_cache_id) if cache.gc_cache_id else "0")
            gs_cache.set("available", "True" if cache.available else "False")
            gs_cache.set("archived", "True" if cache.archived else "False")
            gs_cache.set("xmlns:groundspeak", "http://www.groundspeak.com/cache/1/0/1")

            gs_name = SubElement(gs_cache, "groundspeak:name")
            gs_name.text = cache.name or ""

            gs_placed = SubElement(gs_cache, "groundspeak:placed_by")
            gs_placed.text = cache.placed_by or ""

            # Issue #695: groundspeak:owner was missing entirely from the
            # export, even though cache.owner_name/owner_id are already
            # populated by the importer. Only written when we actually
            # have an owner id, matching the source GPX's own convention
            # (the id attribute is how GSAK/geocaching.com distinguish
            # this from placed_by, which is free text and always present).
            if cache.owner_id:
                gs_owner = SubElement(gs_cache, "groundspeak:owner")
                gs_owner.set("id", str(cache.owner_id))
                gs_owner.text = cache.owner_name or cache.placed_by or ""

            gs_type = SubElement(gs_cache, "groundspeak:type")
            gs_type.text = cache.cache_type or "Traditional Cache"

            gs_container = SubElement(gs_cache, "groundspeak:container")
            gs_container.text = cache.container or "Regular"

            # Attributes (issue #656 follow-up — previously not exported at
            # all). Written here, right after container, to match the
            # official Groundspeak cache.xsd element sequence (name,
            # placed_by, type, container, attributes, difficulty, terrain,
            # country, state, short_description, long_description,
            # encoded_hints, logs).
            if cache.attributes:
                gs_attrs = SubElement(gs_cache, "groundspeak:attributes")
                for attr in cache.attributes:
                    gs_attr = SubElement(gs_attrs, "groundspeak:attribute")
                    gs_attr.set("id", str(attr.attribute_id))
                    gs_attr.set("inc", "1" if attr.is_on else "0")
                    gs_attr.text = attr.name or ""

            gs_diff = SubElement(gs_cache, "groundspeak:difficulty")
            gs_diff.text = str(cache.difficulty or 1.0)

            gs_terr = SubElement(gs_cache, "groundspeak:terrain")
            gs_terr.text = str(cache.terrain or 1.0)

            if cache.country:
                gs_country = SubElement(gs_cache, "groundspeak:country")
                gs_country.text = cache.country

            if cache.state:
                gs_state = SubElement(gs_cache, "groundspeak:state")
                gs_state.text = cache.state

            # Descriptions (issue #656 follow-up — these were missing
            # entirely, which is likely why some Garmin firmware doesn't
            # recognise OpenSAK's GPX/GGZ output as a geocaching file at
            # all, falling back to a plain waypoint. html reflects whether
            # the source text contains HTML markup.)
            if cache.short_description:
                gs_short = SubElement(gs_cache, "groundspeak:short_description")
                gs_short.set("html", "True" if cache.short_desc_html else "False")
                gs_short.text = cache.short_description

            if cache.long_description:
                gs_long = SubElement(gs_cache, "groundspeak:long_description")
                gs_long.set("html", "True" if cache.long_desc_html else "False")
                gs_long.text = cache.long_description

            # encoded_hints must come AFTER the descriptions per cache.xsd —
            # previously written before them, which likely broke strict
            # sequential parsers partway through the cache block (issue
            # #656: hint displayed correctly on-device, but
            # description/logs did not, exactly as expected if a device
            # parser gave up at this point).
            if cache.encoded_hints:
                gs_hints = SubElement(gs_cache, "groundspeak:encoded_hints")
                gs_hints.text = cache.encoded_hints

            # Logs (issue #656 follow-up — previously hardcoded to only the
            # last 5, unlike GC.com/GSAK which include the full history.
            # Export all logs; sorted newest-first to match GC.com/GSAK
            # ordering.)
            if cache.logs:
                gs_logs = SubElement(gs_cache, "groundspeak:logs")
                _min_dt = datetime.min.replace(tzinfo=timezone.utc)
                for log in sorted(
                    cache.logs,
                    key=lambda l: l.log_date or _min_dt,
                    reverse=True,
                ):
                    gs_log = SubElement(gs_logs, "groundspeak:log")
                    gs_log.set("id", log.log_id or "0")
                    gs_date = SubElement(gs_log, "groundspeak:date")
                    gs_date.text = (
                        log.log_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                        if log.log_date else "2000-01-01T00:00:00Z"
                    )
                    gs_ltype = SubElement(gs_log, "groundspeak:type")
                    gs_ltype.text = log.log_type or ""
                    gs_finder = SubElement(gs_log, "groundspeak:finder")
                    # Issue #695: the id attribute was missing entirely,
                    # even though log.finder_id is already populated by
                    # the importer (and used elsewhere to auto-detect the
                    # user's own account). Only written when known, same
                    # reasoning as groundspeak:owner above.
                    if log.finder_id:
                        gs_finder.set("id", str(log.finder_id))
                    gs_finder.text = log.finder or ""
                    gs_text = SubElement(gs_log, "groundspeak:text")
                    gs_text.set("encoded", "False")
                    gs_text.text = log.text or ""

        # GSAK personal note — direct child of <wpt>, matching GSAK's own
        # convention (the importer already searches descendants for this,
        # so it accepts either placement on the way in). Applies regardless
        # of custom-waypoint status.
        note = getattr(cache, "user_note", None)
        note_text = getattr(note, "note", None) if note else None
        if note_text:
            gsak_ext = SubElement(wpt, "gsak:wptExtension")
            gsak_note = SubElement(gsak_ext, "gsak:UserNote")
            gsak_note.text = note_text

        # Issue #753: child waypoints (parking areas, trailheads, stages,
        # final locations, etc.) were never exported at all — the loop
        # above only ever emits the ONE <wpt> for the cache's own listing.
        # Each child Waypoint gets its own sibling <wpt> element, mirroring
        # geocaching.com's own plain-waypoint convention (a <wpt> with no
        # groundspeak:cache block, sym/type set from the waypoint's own
        # type). Reconstructing <name> exactly as geocaching.com originally
        # assigned it isn't possible — the importer only keeps the prefix
        # (see _parse_extra_wpt above), not the original suffix — but
        # prefix + this cache's own GC-code suffix produces a short, unique,
        # GPX/GSAK/Garmin-compatible code, which is all a re-import or
        # device needs; it need not be byte-identical to the original.
        gc_suffix = cache.gc_code[2:] if cache.gc_code and len(cache.gc_code) > 2 else ""
        for child_wp in getattr(cache, "waypoints", None) or []:
            if child_wp.latitude is None or child_wp.longitude is None:
                continue

            cwpt = SubElement(gpx, "wpt")
            cwpt.set("lat", f"{child_wp.latitude:.6f}")
            cwpt.set("lon", f"{child_wp.longitude:.6f}")

            if child_wp.wp_date:
                cwpt_time = SubElement(cwpt, "time")
                cwpt_time.text = child_wp.wp_date.strftime("%Y-%m-%dT%H:%M:%SZ")

            cwpt_name = SubElement(cwpt, "name")
            cwpt_name.text = (child_wp.prefix or "WP") + gc_suffix

            if child_wp.comment:
                cwpt_cmt = SubElement(cwpt, "cmt")
                cwpt_cmt.text = child_wp.comment

            if child_wp.description:
                cwpt_desc = SubElement(cwpt, "desc")
                cwpt_desc.text = child_wp.description

            if child_wp.url:
                cwpt_url = SubElement(cwpt, "url")
                cwpt_url.text = child_wp.url

            cwpt_urlname = SubElement(cwpt, "urlname")
            cwpt_urlname.text = child_wp.name or child_wp.description or cwpt_name.text

            cwpt_sym = SubElement(cwpt, "sym")
            cwpt_sym.text = child_wp.wp_type or "Waypoint"

            cwpt_type = SubElement(cwpt, "type")
            cwpt_type.text = f"Waypoint|{child_wp.wp_type or 'Waypoint'}"

    _indent(gpx)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        gpx, encoding="unicode"
    )


def _cache_symbol(cache_type: str, found: bool = False) -> str:
    """Returner Garmin symbol navn for en cache type.

    Issue #766: found-status skal signaleres via <sym>, ikke kun via
    groundspeak:logs — det er den konvention GSAK og Garmin-enheder rent
    faktisk læser found-status fra i en GPX (Groundspeak/GC.com Pocket
    Query-konventionen). Uden dette sætter OSAK aldrig found-status i GPX'en
    overhovedet, hverken til andre værktøjer eller til sig selv ved
    re-import — kun log-listen blev skrevet, som ingen af importørerne
    bruger til at afgøre found_by_me.

    Kun almindelige "Geocache"-symboler får found-varianten. Lab Caches
    ("Flag, Blue") har intet found-symbol i Garmins konvention, så deres
    symbol er uændret uanset found-status.
    """
    symbols = {
        "Traditional Cache": "Geocache",
        "Multi-cache":        "Geocache",
        "Unknown Cache":      "Geocache",
        "Letterbox Hybrid":   "Geocache",
        "Wherigo Cache":      "Geocache",
        "Event Cache":        "Geocache",
        "Earthcache":         "Geocache",
        "Virtual Cache":      "Geocache",
        # Issue #660: Adventure Lab stages aren't real geocaches — give them
        # a visually distinct built-in Garmin icon instead of the plain
        # "Geocache" pin, so they stand out from regular caches on the map.
        # Full groundspeak:cache content (description/D-T/logs) is kept —
        # confirmed on a GPSMAP 64s that this already displays correctly;
        # this is a cosmetic-only change.
        "Lab Cache":          "Flag, Blue",
    }
    symbol = symbols.get(cache_type, "Geocache")
    if found and symbol == "Geocache":
        return "Geocache Found"
    return symbol


def _custom_wp_symbol(cache_type: str) -> str:
    """Returner et passende indbygget Garmin ikon-navn for en Custom
    Waypoint-type (issue #141 / #660). Disse er ikke rigtige geocaches, så
    de får et rigtigt waypoint-ikon i stedet for det generiske
    geocache-ikon.

    Garmin-enheder genkender et fast, indbygget bibliotek af symbolnavne
    (samme vokabular som BaseCamp/GSAK bruger) — falder tilbage til det
    generiske "Waypoint"-symbol for typer, der ikke har en oplagt match.
    """
    symbols = {
        "Parking Area":    "Parking Area",
        "Trailhead":       "Trail Head",
        "Hotel/POI":       "Lodging",
        "Reference Point": "Flag, Green",
        "Stage":           "Flag, Blue",
        "Final Location":  "Flag, Red",
        "Waypoint":        "Waypoint",
        "Custom":          "Waypoint",
    }
    return symbols.get(cache_type, "Waypoint")


def _indent(elem, level: int = 0) -> None:
    """Tilføj indrykning til XML elementet for læsbarhed."""
    indent = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        for child in elem:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


# ── LOC generator ─────────────────────────────────────────────────────────────

def generate_loc(caches: list, progress_cb=None) -> str:
    """
    Generate LOC 1.0 XML content from a list of Cache objects.
    Returns the LOC content as a string ready to write to file.

    LOC is a simple waypoint format supported by many GPS apps and devices.
    It includes GC code, name, coordinates, difficulty, terrain and container.
    Corrected coordinates are used when available.

    progress_cb(done, total): optional per-cache callback for GUI progress.
    """
    from xml.etree.ElementTree import Element, SubElement
    import xml.etree.ElementTree as ET

    root = Element("loc")
    root.set("version", "1.0")
    root.set("src", "OpenSAK")

    total = len(caches)
    for i, cache in enumerate(caches, 1):
        if progress_cb:
            progress_cb(i, total)
        if cache.latitude is None or cache.longitude is None:
            continue

        export_lat, export_lon = _effective_coords(cache)

        wp = SubElement(root, "waypoint")

        name_el = SubElement(wp, "name")
        name_el.set("id", cache.gc_code or "")
        # GSAK format: "Cache name by Owner (D/T)"
        diff = cache.difficulty or 1.0
        terr = cache.terrain or 1.0
        diff_str = f"{diff:g}"
        terr_str = f"{terr:g}"
        label = f"{cache.name or ''} by {cache.placed_by or ''} ({diff_str}/{terr_str})"
        name_el.text = f"<![CDATA[{label}]]>"

        coord_el = SubElement(wp, "coord")
        coord_el.set("lat", f"{export_lat:.6f}")
        coord_el.set("lon", f"{export_lon:.6f}")

        type_el = SubElement(wp, "type")
        type_el.text = "Geocache"

        link_el = SubElement(wp, "link")
        link_el.set("text", "Waypoint Details")
        link_el.text = f"http://coord.info/{cache.gc_code}"

        diff_el = SubElement(wp, "difficulty")
        diff_el.text = str(diff)

        terr_el = SubElement(wp, "terrain")
        terr_el.text = str(terr)

        container_el = SubElement(wp, "container")
        container_el.text = cache.container or "Unknown"

    _indent(root)
    xml_str = ET.tostring(root, encoding="unicode")

    # ET escapes CDATA — replace back the name content with proper CDATA
    # by re-building the name tags with raw CDATA sections
    import re

    def _fix_cdata(m: re.Match) -> str:
        gc_id = m.group(1)
        inner = m.group(2)
        # Unescape what ET escaped
        inner = inner.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        # Strip the literal CDATA wrapper text that we embedded as plain text
        inner = inner.replace("<![CDATA[", "").replace("]]>", "")
        return f'<name id="{gc_id}"><![CDATA[{inner}]]></name>'

    xml_str = re.sub(
        r'<name id="([^"]*)">&lt;!\[CDATA\[([^\]]*)\]\]&gt;</name>',
        _fix_cdata,
        xml_str,
    )

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str


# ── GGZ generator ─────────────────────────────────────────────────────────────

def generate_ggz(caches: list, filename: str = "opensak_export", progress_cb=None) -> bytes:
    """
    Generate a GGZ file (ZIP archive) from a list of Cache objects.
    Returns the GGZ content as bytes ready to write to file.

    GGZ structure:
      data/<filename>.gpx          — full GPX file with all cache data
      index/com/garmin/geocaches/v0/index.xml — lightweight index for Garmin

    The format allows Garmin devices to load more than the usual 10,000
    cache limit by using the GGZ container instead of plain GPX files.
    Corrected coordinates are used when available.

    progress_cb(done, total): optional per-cache callback for GUI progress;
    reported over the index-building pass (the slow part of GGZ).
    """
    import io
    import zipfile
    from xml.etree.ElementTree import Element, SubElement
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone

    gpx_filename = f"{filename}.gpx"
    gpx_content  = generate_gpx(caches, filename).encode("utf-8")

    # ── CRC32 of the GPX content (hex, uppercase, 8 chars) ────────────────────
    import binascii
    crc_val = binascii.crc32(gpx_content) & 0xFFFFFFFF
    crc_hex = f"{crc_val:08X}"

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Build index.xml ───────────────────────────────────────────────────────
    ggz_root = Element("ggz")
    ggz_root.set("xmlns", "http://www.opencaching.com/xmlschemas/ggz/1/0")

    time_el = SubElement(ggz_root, "time")
    time_el.text = now_str

    file_el = SubElement(ggz_root, "file")

    fname_el = SubElement(file_el, "name")
    fname_el.text = gpx_filename

    crc_el = SubElement(file_el, "crc")
    crc_el.text = crc_hex

    ftime_el = SubElement(file_el, "time")
    ftime_el.text = now_str

    # Track byte offset into the GPX for each cache entry
    gpx_text = gpx_content.decode("utf-8")

    # ── Precompute byte offsets for every waypoint in ONE linear pass ──────────
    # Previously this ran a fresh `re.search()` over the ENTIRE gpx_text for
    # EVERY cache, which is O(n²): with thousands of caches, each search had
    # to scan further and further into the text, causing exports to start
    # fast and then slow down dramatically (see #466). Instead, walk the text
    # once with finditer() and build a gc_code -> (file_pos, file_len) map.
    import re
    _wpt_pattern  = re.compile(r'<wpt\b[^>]*>.*?</wpt>', re.DOTALL)
    _name_pattern = re.compile(r'<name>([^<]*)</name>')

    offsets_by_gc_code: dict = {}
    _byte_pos  = 0
    _prev_end  = 0
    for _m in _wpt_pattern.finditer(gpx_text):
        # Advance by the byte length of the gap since the previous match,
        # so we never re-encode text we've already accounted for.
        _byte_pos += len(gpx_text[_prev_end:_m.start()].encode("utf-8"))
        _wpt_text  = _m.group(0)
        _wpt_len   = len(_wpt_text.encode("utf-8"))
        _name_m    = _name_pattern.search(_wpt_text)
        if _name_m:
            # First occurrence wins, matching the old re.search() behaviour
            # for the (unlikely) case of a duplicate GC code in the export.
            offsets_by_gc_code.setdefault(_name_m.group(1), (_byte_pos, _wpt_len))
        _byte_pos += _wpt_len
        _prev_end  = _m.end()

    total = len(caches)
    for i, cache in enumerate(caches, 1):
        if progress_cb:
            progress_cb(i, total)
        if cache.latitude is None or cache.longitude is None:
            continue

        export_lat, export_lon = _effective_coords(cache)
        gc_code = cache.gc_code or ""

        file_pos, file_len = offsets_by_gc_code.get(gc_code, (0, 0))

        gch_el = SubElement(file_el, "gch")

        code_el = SubElement(gch_el, "code")
        code_el.text = gc_code

        cname_el = SubElement(gch_el, "name")
        cname_el.text = cache.name or ""

        ctype_el = SubElement(gch_el, "type")
        ctype_el.text = cache.cache_type or "Traditional Cache"

        clat_el = SubElement(gch_el, "lat")
        clat_el.text = str(export_lat)

        clon_el = SubElement(gch_el, "lon")
        clon_el.text = str(export_lon)

        fpos_el = SubElement(gch_el, "file_pos")
        fpos_el.text = str(file_pos)

        flen_el = SubElement(gch_el, "file_len")
        flen_el.text = str(file_len)

        ratings_el = SubElement(gch_el, "ratings")

        awe_el = SubElement(ratings_el, "awesomeness")
        awe_el.text = "3.0"

        diff_el = SubElement(ratings_el, "difficulty")
        diff_el.text = str(cache.difficulty or 1.0)

        if cache.container:
            _CONTAINER_SIZE = {
                "Micro": 2.0, "Small": 3.0, "Regular": 4.0,
                "Large": 5.0, "Not chosen": 3.0, "Other": 3.0,
            }
            size_val = _CONTAINER_SIZE.get(cache.container)
            if size_val:
                size_el = SubElement(ratings_el, "size")
                size_el.text = str(size_val)

        terr_el = SubElement(ratings_el, "terrain")
        terr_el.text = str(cache.terrain or 1.0)

        if getattr(cache, "found", False):
            found_el = SubElement(gch_el, "found")
            found_el.text = "true"

    _indent(ggz_root)
    index_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        + ET.tostring(ggz_root, encoding="unicode")
    )

    # ── Pack into ZIP ─────────────────────────────────────────────────────────
    # zipfile.ZipFile.mkdir() with a plain string defaults date_time to
    # (1980, 1, 1, 0, 0, 0) — the earliest date the ZIP format supports —
    # unlike writestr(), which defaults to the current time. That made every
    # directory entry in the .ggz show up as "1980-01-01" / "1979-12-31 23:00"
    # depending on timezone. Build explicit ZipInfo objects with the current
    # time so directories get a sensible date too.
    buf = io.BytesIO()
    _zip_date_time = datetime.now().timetuple()[:6]

    def _dir_zipinfo(name: str) -> "zipfile.ZipInfo":
        if not name.endswith("/"):
            name += "/"
        zi = zipfile.ZipInfo(name, date_time=_zip_date_time)
        zi.compress_size = 0
        zi.CRC = 0
        zi.file_size = 0
        zi.external_attr = (0o777 << 16) | 0x10
        return zi

    def _file_zipinfo(name: str) -> "zipfile.ZipInfo":
        zi = zipfile.ZipInfo(name, date_time=_zip_date_time)
        zi.compress_type = zipfile.ZIP_DEFLATED
        return zi

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.mkdir(_dir_zipinfo("data/"))
        zf.writestr(_file_zipinfo(f"data/{gpx_filename}"), gpx_content)
        zf.mkdir(_dir_zipinfo("index/"))
        zf.mkdir(_dir_zipinfo("index/com/"))
        zf.mkdir(_dir_zipinfo("index/com/garmin/"))
        zf.mkdir(_dir_zipinfo("index/com/garmin/geocaches/"))
        zf.mkdir(_dir_zipinfo("index/com/garmin/geocaches/v0/"))
        zf.writestr(
            _file_zipinfo("index/com/garmin/geocaches/v0/index.xml"),
            index_xml.encode("utf-8"),
        )

    return buf.getvalue()


def export_ggz_to_device(
    caches: list,
    device_root: Path,
    filename: str = "opensak",
    progress_cb=None,
) -> ExportResult:
    """
    Eksportér caches som GGZ fil direkte til en Garmin GPS enhed.
    Understøtter både normale mount points og MTP-enheder (via gio copy).
    """
    result = ExportResult()
    result.device = device_root

    try:
        ggz_content = generate_ggz(caches, filename, progress_cb=progress_cb)

        if is_mtp_device(device_root):
            ggz_dir = _find_mtp_ggz_dir(device_root)
            if ggz_dir is None:
                garmin = _find_mtp_garmin_root(device_root)
                if garmin is None:
                    result.error = tr("gps_error_file", error="Garmin folder not found on MTP device")
                    return result
                ggz_dir = garmin / "GGZ"
            output_path = ggz_dir / f"{filename}.ggz"
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir) / output_path.name
                tmp_path.write_bytes(ggz_content)
                _gio_copy_with_replace(tmp_path, output_path)
        else:
            ggz_dir = get_garmin_ggz_path(device_root)
            ggz_dir.mkdir(parents=True, exist_ok=True)
            output_path = ggz_dir / f"{filename}.ggz"
            output_path.write_bytes(ggz_content)

        result.file_path   = output_path
        result.cache_count = len([c for c in caches if c.latitude is not None])

    except PermissionError:
        result.error = tr("gps_error_permission")
    except OSError as e:
        result.error = tr("gps_error_file", error=str(e))
    except Exception as e:
        result.error = tr("gps_error_unexpected", error=str(e))

    return result


def export_ggz_to_file(
    caches: list,
    output_path: Path,
    progress_cb=None,
) -> ExportResult:
    """
    Eksportér caches som GGZ fil til en valgfri placering.
    Bruges når GPS ikke er tilsluttet direkte.
    """
    result = ExportResult()

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ggz_content = generate_ggz(caches, output_path.stem, progress_cb=progress_cb)
        output_path.write_bytes(ggz_content)
        result.file_path   = output_path
        result.cache_count = len([c for c in caches if c.latitude is not None])
    except Exception as e:
        result.error = str(e)

    return result


# ── Slet GPX filer fra enhed ──────────────────────────────────────────────────

class DeleteResult:
    """Resultat af sletning af eksisterende eksport-filer (GPX eller GGZ) fra GPS enhed."""

    def __init__(self):
        self.device:        Optional[Path] = None
        self.deleted_files: list[Path]     = []
        self.failed_files:  list[Path]     = []
        self.error:         Optional[str]  = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_files)

    @property
    def failed_count(self) -> int:
        return len(self.failed_files)

    def __str__(self) -> str:
        if not self.success:
            return f"✗ {tr('error')}: {self.error}"
        if self.deleted_count == 0:
            return f"ℹ️  {tr('gps_result_no_files')}"
        lines = [f"🗑️  {tr('gps_result_deleted', count=self.deleted_count)}"]
        for f in self.deleted_files:
            lines.append(f"   - {f.name}")
        if self.failed_count:
            lines.append(f"⚠️  {tr('gps_result_delete_failed', count=self.failed_count)}")
            for f in self.failed_files:
                lines.append(f"   - {f.name}")
        return "\n".join(lines)


def delete_gpx_files(
    device_root: Path,
    pattern: str = "*.gpx",
    folder: Optional[Path] = None,
) -> DeleteResult:
    """
    Slet alle filer der matcher 'pattern' i en mappe på enheden.
    Understøtter både normale mount points og MTP-enheder (via gio remove).
    """
    result = DeleteResult()
    result.device = device_root

    try:
        if is_mtp_device(device_root) and folder is None:
            target_dir = _find_mtp_gpx_dir(device_root)
        elif is_mtp_device(device_root) and folder is not None:
            target_dir = folder
        else:
            target_dir = folder if folder is not None else get_garmin_gpx_path(device_root)

        if target_dir is None or not target_dir.exists():
            return result

        matched_files = list(target_dir.glob(pattern))
        use_gio = is_mtp_device(device_root)

        for f in matched_files:
            if not f.is_file():
                continue
            try:
                if use_gio:
                    if _gio_remove_and_wait(f):
                        result.deleted_files.append(f)
                    else:
                        result.failed_files.append(f)
                else:
                    f.unlink()
                    result.deleted_files.append(f)
            except (PermissionError, OSError):
                result.failed_files.append(f)

    except PermissionError:
        result.error = tr("gps_error_permission")
    except OSError as e:
        result.error = tr("gps_error_file", error=str(e))
    except Exception as e:
        result.error = tr("gps_error_unexpected", error=str(e))

    return result


# ── Export til enhed ──────────────────────────────────────────────────────────

class ExportResult:
    """Resultat af en GPS export."""

    def __init__(self):
        self.device:      Optional[Path] = None
        self.file_path:   Optional[Path] = None
        self.cache_count: int = 0
        self.error:       Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None

    def __str__(self) -> str:
        if self.success:
            lines = [tr("gps_result_exported", count=self.cache_count)]
            if self.device is not None:
                lines.append(f"  {tr('gps_result_device')}: {self.device}")
            lines.append(
                f"  {tr('gps_result_file')}: {self.file_path.name if self.file_path else ''}"
            )
            return "\n".join(lines)
        return f"✗ {tr('error')}: {self.error}"


def export_to_device(
    caches: list,
    device_root: Path,
    filename: str = "opensak",
    progress_cb=None,
) -> ExportResult:
    """
    Eksportér caches til en Garmin GPS enhed.
    Understøtter både normale mount points og MTP-enheder (via gio copy).
    """
    result = ExportResult()
    result.device = device_root

    try:
        gpx_content = generate_gpx(caches, filename, progress_cb=progress_cb)

        if is_mtp_device(device_root):
            gpx_dir = _find_mtp_gpx_dir(device_root)
            if gpx_dir is None:
                result.error = tr("gps_error_file", error="Garmin/GPX folder not found on MTP device")
                return result
            output_path = gpx_dir / f"{filename}.gpx"
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir) / output_path.name
                tmp_path.write_text(gpx_content, encoding="utf-8")
                _gio_copy_with_replace(tmp_path, output_path)
        else:
            gpx_dir = get_garmin_gpx_path(device_root)
            gpx_dir.mkdir(parents=True, exist_ok=True)
            output_path = gpx_dir / f"{filename}.gpx"
            output_path.write_text(gpx_content, encoding="utf-8")

        result.file_path   = output_path
        result.cache_count = len([c for c in caches if c.latitude is not None])

    except PermissionError:
        result.error = tr("gps_error_permission")
    except OSError as e:
        result.error = tr("gps_error_file", error=str(e))
    except Exception as e:
        result.error = tr("gps_error_unexpected", error=str(e))

    return result


def export_to_file(
    caches: list,
    output_path: Path,
    progress_cb=None,
) -> ExportResult:
    """
    Eksportér caches til en GPX fil (valgfri placering).
    Bruges når GPS ikke er tilsluttet.
    """
    result = ExportResult()

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        gpx_content = generate_gpx(caches, output_path.stem, progress_cb=progress_cb)
        output_path.write_text(gpx_content, encoding="utf-8")
        result.file_path   = output_path
        result.cache_count = len([c for c in caches if c.latitude is not None])
    except Exception as e:
        result.error = str(e)

    return result
