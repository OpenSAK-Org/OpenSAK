"""
src/opensak/gps/garmin.py — Garmin GPS device detection og GPX/LOC/GGZ export.

Understøtter Garmin enheder der monteres som USB drev eller som MTP-lager
og accepterer GPX filer i /Garmin/GPX/ mappen.

Testet med: GPSMAP64s, Oregon750

Corrected coordinates: if a cache has user-corrected coordinates
(user_note.is_corrected), they are its waypoint position in the export.
"""

from __future__ import annotations

import io
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Optional, Sized, cast

from opensak.export.cache_text import NO_TEXT, ExportText
from opensak.export.gpx_content import (
    DEFAULT_CONTENT, USER_NOTE_LOG_FINDER, USER_NOTE_LOG_FINDER_ID, USER_NOTE_LOG_ID,
    USER_NOTE_LOG_TYPE, GpxContent, my_log_matcher, normalize_note, user_note_log_date,
)
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
            encoding="utf-8",
            errors="replace",
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
    return f"{value:.1f} GiB"


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


# lsblk --raw writes unsafe characters, spaces included, as \xHH;
# /proc/mounts writes space, tab, newline and backslash as octal (\040).
_MOUNT_ESCAPE = re.compile(rb"\\(x[0-9a-fA-F]{2}|[0-3][0-7]{2})")


def _unescape_mount(text: str) -> str:
    """A mount point as lsblk --raw or /proc/mounts print it, as a real path
    (#1094). Both escape single bytes, so a name with letters outside ASCII
    comes out as several escapes; the result is decoded as UTF-8."""
    if "\\" not in text:
        return text

    def _byte(m: re.Match[bytes]) -> bytes:
        code = m.group(1)
        return bytes([int(code[1:], 16) if code[:1] == b"x" else int(code, 8)])

    raw = text.encode("utf-8", "surrogateescape")
    return _MOUNT_ESCAPE.sub(_byte, raw).decode("utf-8", "replace")


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
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line == "/":
                continue
            mount = Path(_unescape_mount(line))
            if mount.is_dir() and _is_removable_path(mount):
                candidates.add(mount)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # ── Metode 2: /proc/mounts (fallback) ─────────────────────────────────────
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mountpoint, fstype = parts[0], parts[1], parts[2]
                mount = Path(_unescape_mount(mountpoint))
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
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=3,
        )
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if (len(parts) == 2 and parts[1] == "1"
                    and _unescape_mount(parts[0]) == path_str):
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


def _match_existing_folder(parent: Path) -> Optional[Path]:
    """
    Return the actual on-disk entry matching a Garmin folder name, matched
    case-insensitively against the real directory listing.

    Deliberately does NOT probe constructed candidate paths (e.g.
    `parent / "garmin"`, `parent / "GARMIN"`, `parent / "Garmin"`) and test
    each with `.is_dir()`: on a case-insensitive filesystem (macOS APFS,
    Windows NTFS by default) all three candidates resolve to the same
    physical folder once it exists, so which one "matches" first depends on
    iteration order over `_MTP_GARMIN_FOLDER_NAMES` — a set, whose order is
    randomised per-process by Python's string hash seed. That made
    `_get_garmin_folder()` return a different casing on every run. Scanning
    the real entries once and matching case-insensitively sidesteps the
    ambiguity entirely and returns the folder's true on-disk name.
    """
    wanted = {name.casefold() for name in _MTP_GARMIN_FOLDER_NAMES}
    try:
        # `parent / Path()` is a no-op for a plain Path, but for an MTPDevice
        # (which has no iterdir()/glob() of its own) it produces an MTPPath
        # rooted at the device root, which does support glob() — this keeps
        # a single code path working for both mass-storage and MTP devices.
        for entry in (parent / Path()).glob("*"):
            if entry.is_dir() and entry.name.casefold() in wanted:
                return entry
    except (AttributeError, OSError):
        pass
    return None


def _get_garmin_folder(device_root: Path) -> Path:
    """Resolve the Garmin folder for mass-storage and MTP device layouts."""
    match = _match_existing_folder(device_root)
    if match is not None:
        return match

    if is_mtp_device(device_root):
        garmin = _find_mtp_garmin_root(device_root)
        if garmin is not None:
            return garmin

    try:
        for storage_root in (device_root / Path()).glob("*"):
            if not storage_root.is_dir():
                continue
            match = _match_existing_folder(storage_root)
            if match is not None:
                return match
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

    lines.append(f"\nGarmin devices: {find_garmin_devices() or 'none found'}")
    return "\n".join(lines)


# ── GPX generator ─────────────────────────────────────────────────────────────

def _effective_coords(cache, use_corrected: bool = True) -> tuple[float, float]:
    """
    Returner de koordinater der skal bruges til GPX export.

    Hvis cachen har korrigerede koordinater (user_note.is_corrected),
    bruges disse. Ellers bruges de originale koordinater.
    use_corrected=False returnerer altid de originale koordinater.
    """
    note = getattr(cache, "user_note", None)
    if use_corrected and note and getattr(note, "is_corrected", False):
        lat = note.corrected_lat
        lon = note.corrected_lon
        if lat is not None and lon is not None:
            return lat, lon
    return cache.latitude, cache.longitude


# (minlat, minlon, maxlat, maxlon) — the GPX <bounds> (#700)
Bounds = tuple[float, float, float, float]

_GPX_SCHEMA_LOCATION = (
    "http://www.topografix.com/GPX/1/0 http://www.topografix.com/GPX/1/0/gpx.xsd"
    " http://www.groundspeak.com/cache/1/0/1 http://www.groundspeak.com/cache/1/0/1/cache.xsd"
    " http://www.gsak.net/xmlv1/6 http://www.gsak.net/xmlv1/6/gsak.xsd"
)


def gpx_bounds(
    caches: Iterable, use_corrected: bool = True, child_waypoints: bool = True,
    content: GpxContent = DEFAULT_CONTENT,
) -> Optional[Bounds]:
    """The GPX <bounds> of an export of *caches*: (minlat, minlon, maxlat,
    maxlon) over every <wpt> _gpx_chunks() writes for them, i.e. each cache
    at its export position (unless *content* writes children only) and,
    with *child_waypoints*, the child waypoints *content* writes. None when
    no <wpt> would be written. A point at exactly 0/0 — GSAK's placeholder
    for a child waypoint without coordinates — is written but left out, as
    GSAK does, so it doesn't stretch the box to the equator.

    #700: GSAK writes <bounds> before the first <wpt>, but the export is
    streamed (#1075), so the bounds are computed up front without loading
    the caches in full. Database caches (which the export reloads anyway)
    are measured in the database, a chunk per query (export_bounds());
    other objects, such as test stand-ins, from their attributes.
    """
    from opensak.db.database import export_bounds, is_db_cache

    with_children = child_waypoints and content.writes_children()
    with_caches = not content.children_only
    box: Optional[Bounds] = None

    def _add(lat: float, lon: float) -> None:
        nonlocal box
        if lat == 0 and lon == 0:
            return   # GSAK's "no coordinates" placeholder; GSAK leaves it out too
        if box is None:
            box = (lat, lon, lat, lon)
        else:
            box = (min(box[0], lat), min(box[1], lon),
                   max(box[2], lat), max(box[3], lon))

    db_ids: list[int] = []
    for cache in caches:
        if is_db_cache(cache):
            db_ids.append(cache.id)
            continue
        if cache.latitude is None or cache.longitude is None:
            continue
        if with_caches:
            _add(*_effective_coords(cache, use_corrected))
        if not with_children:
            continue
        for wp in content.child_waypoints(cache):
            _add(wp.latitude, wp.longitude)
    if db_ids:
        db_box = export_bounds(db_ids, use_corrected, with_children,
                               children=content.children, caches=with_caches)
        if db_box is not None:
            _add(db_box[0], db_box[1])
            _add(db_box[2], db_box[3])
    return box


def generate_gpx(
    caches: list, filename: str = "opensak_export", progress_cb=None,
    use_corrected: bool = True,
    text: ExportText = NO_TEXT,
    attributes: bool = True,
    child_waypoints: bool = True,
    content: GpxContent = DEFAULT_CONTENT,
) -> str:
    """
    Generer GPX indhold fra en liste af Cache objekter.
    Returnerer GPX som en streng klar til at skrive til fil.

    Large exports should use write_gpx() instead, which streams the GPX to
    a file without ever holding the whole document in memory (#1075).
    See _gpx_chunks() for the format.
    """
    buf = io.StringIO()
    write_gpx(buf, caches, filename, progress_cb=progress_cb,
              use_corrected=use_corrected, text=text,
              attributes=attributes, child_waypoints=child_waypoints,
              content=content)
    return buf.getvalue()


def write_gpx(
    out, caches: Iterable, filename: str = "opensak_export", progress_cb=None,
    use_corrected: bool = True,
    text: ExportText = NO_TEXT,
    attributes: bool = True,
    child_waypoints: bool = True,
    total: Optional[int] = None,
    bounds: Optional[Bounds] = None,
    content: GpxContent = DEFAULT_CONTENT,
) -> int:
    """Write the GPX for *caches* to the text stream *out*, one cache at a
    time (#1075: building the whole document as one string ran out of memory
    for exports of 100,000+ caches).

    *caches* may be any iterable (e.g. iter_caches_full()); *total* is the
    count passed to progress_cb and defaults to len(caches). *bounds* is
    the GPX <bounds> (see gpx_bounds()): computed here when *caches* is a
    list, so a caller that streams an iterator passes it, or the file has
    no <bounds>. *content* holds the #775 options (children, logs, user
    note log, GPX version). Returns the number of caches written.
    """
    if bounds is None and isinstance(caches, Sized):
        bounds = gpx_bounds(caches, use_corrected, child_waypoints, content)
    count = 0
    for chunk, cache in _gpx_chunks(
        caches, filename, progress_cb, use_corrected, text,
        attributes, child_waypoints, total, bounds, content,
    ):
        out.write(chunk)
        if cache is not None:
            count += 1
    return count


def _xml_chunk(elem, level: int, short_empty: bool = True) -> str:
    """*elem* serialized exactly as it appears *level* deep in an _indent()ed
    document, without its tail — the caller writes the whitespace between
    elements, so a document can be streamed element by element.
    *short_empty*=False writes empty elements as <x></x> instead of <x />."""
    import xml.etree.ElementTree as ET

    _indent(elem, level)
    elem.tail = None
    return ET.tostring(elem, encoding="unicode", short_empty_elements=short_empty)


def _rating(value) -> str:
    """A difficulty/terrain rating as GSAK writes it in <desc>: 1, 1.5, 2."""
    return f"{float(value or 1.0):g}"


def _child_code(child_wp, gc_code: Optional[str]) -> str:
    """The <name> of a child waypoint.

    #700: GSAK writes the waypoint's own code (e.g. P0BWND7), which the GSAK
    import keeps in wp_code. It is used when it ends with the cache's
    GC-code suffix, since that is how an import attaches the waypoint to
    its cache. Otherwise (GPX-imported and user-created waypoints have no
    wp_code) the code is rebuilt as prefix + suffix (#753), which is
    unique and attaches the same way.
    """
    suffix = gc_code[2:] if gc_code and len(gc_code) > 2 else ""
    wp_code = getattr(child_wp, "wp_code", None)
    if wp_code and suffix and len(wp_code) > len(suffix) \
            and wp_code.upper().endswith(suffix.upper()):
        return wp_code
    return (child_wp.prefix or "WP") + suffix


def _gpx_chunks(
    caches: Iterable, filename: str, progress_cb, use_corrected: bool,
    text: ExportText, attributes: bool, child_waypoints: bool,
    total: Optional[int], bounds: Optional[Bounds] = None,
    content: GpxContent = DEFAULT_CONTENT,
) -> Iterator[tuple[str, Any]]:
    """
    Yield the GPX document as (text, cache) pieces. *cache* is set only on
    the piece holding that cache's own <wpt> element (GGZ needs its byte
    offset) — or, when *content* writes children only, on the first child
    of each cache that has one, so the caller can count them; every other
    piece has None.

    *content* (#775) chooses the child waypoints (all, flagged, unflagged,
    none; or only the children, without their caches), cuts the logs to
    max_logs per cache (newest first; the user's own logs kept on top with
    my_logs_always), adds the user note as a synthetic first log, and
    writes GPX 1.1 instead of 1.0 (see _to_gpx_1_1()).

    The file follows GSAK's own GPX 1.0 export (#700): the header elements
    (desc, author, time, keywords, bounds) sit directly under <gpx> — the
    <metadata> element is GPX 1.1 — and each <wpt> has GSAK's element order:
    ele, time, name, desc, url, urlname, sym, type, gsak:wptExtension,
    groundspeak:cache. groundspeak:cache is a direct child of <wpt>, not
    wrapped in <extensions> (GPX 1.1 style): Garmin's geocache parser reads
    the elements in sequence and expects GSAK's layout (#656). The elements
    GSAK always writes (attributes, short_description, long_description,
    encoded_hints, logs, travelbugs, and a child's cmt and url) are written
    even when empty.
    Each child waypoint follows its cache as a plain <wpt>.

    Caches with corrected coordinates are written at the corrected position
    unless use_corrected=False; like GSAK, the file doesn't repeat the
    original coordinates in a <cmt>.

    progress_cb(done, total): optional call per cache for GUI progress.
    *text* replaces cache names and waypoint descriptions (Lua macros);
    attributes / child_waypoints=False leave those out. *bounds* is
    written as <bounds> when given (see gpx_bounds()).
    """
    from xml.etree.ElementTree import Element, SubElement
    from datetime import datetime, timezone

    gpx_1_1 = content.version == "1.1"
    with_children = child_waypoints and content.writes_children()
    with_caches = not content.children_only
    # #775: the user's own logs, kept on top of the log limit.
    is_mine = (
        my_log_matcher() if content.my_logs_always and content.max_logs else None
    )

    # Root element. GPX 1.0 matches GSAK's own export format. The
    # groundspeak namespace is deliberately NOT declared here; it's declared
    # locally on each <groundspeak:cache> element instead (see below),
    # mirroring GSAK's exact convention.
    gpx_ns = _GPX_1_1_NS if gpx_1_1 else "http://www.topografix.com/GPX/1/0"
    yield (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<gpx version="{content.version}" creator="OpenSAK"'
        f' xmlns="{gpx_ns}"'
        ' xmlns:gsak="http://www.gsak.net/xmlv1/6"'
        ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        f' xsi:schemaLocation="{_GPX_1_1_SCHEMA_LOCATION if gpx_1_1 else _GPX_SCHEMA_LOCATION}">'
    ), None

    # Header. GSAK has no <name>; it is kept for the file name, which GPX
    # allows first. "(HasChildren)" is how GSAK marks a file written with
    # child waypoints. GPX 1.0 has these directly under <gpx>; GPX 1.1 in
    # <metadata>, with the author as <author><name>.
    header = [
        ("name", filename),
        ("desc", "Geocache file generated by OpenSAK"
                 + (" (HasChildren)" if with_children else "")),
        ("author", "OpenSAK"),
        ("time", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("keywords", "cache, geocache, groundspeak"),
    ]
    header_els = []
    for tag, value in header:
        el = Element(tag)
        if gpx_1_1 and tag == "author":
            SubElement(el, "name").text = value
        else:
            el.text = value
        header_els.append(el)
    if bounds is not None:
        bounds_el = Element("bounds")
        for key, coord in zip(("minlat", "minlon", "maxlat", "maxlon"), bounds):
            bounds_el.set(key, f"{coord:.6f}")
        header_els.append(bounds_el)
    if gpx_1_1:
        metadata = Element("metadata")
        metadata.extend(header_els)
        yield "\n  " + _xml_chunk(metadata, 1), None
    else:
        for el in header_els:
            yield "\n  " + _xml_chunk(el, 1), None

    if total is None:
        total = len(cast(Sized, caches))
    for i, cache in enumerate(caches, 1):
        if progress_cb:
            progress_cb(i, total)
        if cache.latitude is None or cache.longitude is None:
            continue

        # Brug korrigerede koordinater hvis de findes
        export_lat, export_lon = _effective_coords(cache, use_corrected)

        # Custom Waypoints (issue #141: Hotel/POI, Parking Area, Trailhead,
        # etc.) are stored as Cache rows for simplicity, but they aren't
        # real geocaches — issue #660: exporting them wrapped in a full
        # groundspeak:cache block made them show up on Garmin devices as a
        # "fake geocache" with empty D/T stars and Size: (Not Chosen).
        # Export them instead as a plain GPX waypoint with a proper native
        # Garmin icon symbol, and skip the groundspeak:cache block entirely.
        is_custom_wp = (cache.cache_type or "") in CUSTOM_WP_TYPES

        wpt = Element("wpt")
        wpt.set("lat", f"{export_lat:.6f}")
        wpt.set("lon", f"{export_lon:.6f}")

        # #700: elevation, when known (0.0 is a real elevation).
        elevation = getattr(cache, "elevation", None)
        if elevation is not None:
            SubElement(wpt, "ele").text = str(float(elevation))

        if cache.hidden_date:
            time_wpt = SubElement(wpt, "time")
            time_wpt.text = cache.hidden_date.strftime("%Y-%m-%dT%H:%M:%SZ")

        name_wpt = SubElement(wpt, "name")
        name_wpt.text = cache.gc_code or ""

        # #700: GSAK's summary "Name by Owner (D/T)" in <desc> and
        # "Name by Owner" in <urlname>; a custom waypoint has neither.
        cache_name = text.name(cache)
        if is_custom_wp:
            title = summary = cache_name
        else:
            title = f"{cache_name} by {cache.placed_by}" if cache.placed_by else cache_name
            summary = f"{title} ({_rating(cache.difficulty)}/{_rating(cache.terrain)})"

        desc_wpt = SubElement(wpt, "desc")
        desc_wpt.text = text.description(cache) or summary

        url_wpt = SubElement(wpt, "url")
        url_wpt.text = f"https://coord.info/{cache.gc_code}"

        SubElement(wpt, "urlname").text = title

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

        # GSAK personal note — direct child of <wpt>, before
        # groundspeak:cache as in GSAK's own files (#700; the importer
        # searches descendants for it, so it accepts either placement on
        # the way in). Applies regardless of custom-waypoint status.
        note = getattr(cache, "user_note", None)
        note_text = getattr(note, "note", None) if note else None
        # Issue #1145: the found-by-me date, as GSAK writes it
        # (<gsak:UserFound>2026-06-04</gsak:UserFound>, date only). Without
        # it GSAK re-imports a found cache with a blank Found date whenever
        # the user's own found log isn't in the file (logs trimmed, or the
        # cache was marked found by hand). Only for found caches: unmarking
        # clears found_date, but a stale one must never mark a cache found.
        found_date = getattr(cache, "found_date", None) if getattr(cache, "found", False) else None
        if note_text or found_date:
            gsak_ext = SubElement(wpt, "gsak:wptExtension")
            if found_date:
                SubElement(gsak_ext, "gsak:UserFound").text = found_date.strftime("%Y-%m-%d")
            if note_text:
                gsak_note = SubElement(gsak_ext, "gsak:UserNote")
                gsak_note.text = note_text

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
            gs_name.text = cache_name

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
            # encoded_hints, logs, travelbugs). #700: written even when
            # empty, like GSAK; attributes=False still leaves them out.
            if attributes:
                gs_attrs = SubElement(gs_cache, "groundspeak:attributes")
                for attr in cache.attributes or []:
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
            # the source text contains HTML markup.) #700: GSAK always
            # writes both, as a single space when empty (the importer
            # strips it back to nothing).
            gs_short = SubElement(gs_cache, "groundspeak:short_description")
            gs_short.set("html", "True" if cache.short_description and cache.short_desc_html else "False")
            gs_short.text = cache.short_description or " "

            gs_long = SubElement(gs_cache, "groundspeak:long_description")
            gs_long.set("html", "True" if cache.long_description and cache.long_desc_html else "False")
            gs_long.text = cache.long_description or " "

            # encoded_hints must come AFTER the descriptions per cache.xsd —
            # previously written before them, which likely broke strict
            # sequential parsers partway through the cache block (issue
            # #656: hint displayed correctly on-device, but
            # description/logs did not, exactly as expected if a device
            # parser gave up at this point). #700: always written.
            gs_hints = SubElement(gs_cache, "groundspeak:encoded_hints")
            gs_hints.text = cache.encoded_hints or ""

            # Logs (issue #656 follow-up — previously hardcoded to only the
            # last 5, unlike GC.com/GSAK which include the full history.
            # All logs by default, sorted newest-first to match GC.com/GSAK
            # ordering; #775: cut to max_logs, the user's own kept with
            # my_logs_always.) #700: always written, empty without logs,
            # like GSAK.
            gs_logs = SubElement(gs_cache, "groundspeak:logs")
            # #775: the user note as a synthetic first log, as GSAK writes
            # it ("Include user notes"). Not counted against max_logs; the
            # importer recognises it and doesn't store it as a log.
            note_log_text = normalize_note(note_text) if content.user_note_log else ""
            if note_log_text:
                gs_log = SubElement(gs_logs, "groundspeak:log")
                gs_log.set("id", USER_NOTE_LOG_ID)
                SubElement(gs_log, "groundspeak:date").text = user_note_log_date(
                    getattr(note, "updated_at", None))
                SubElement(gs_log, "groundspeak:type").text = USER_NOTE_LOG_TYPE
                gs_finder = SubElement(gs_log, "groundspeak:finder")
                gs_finder.set("id", USER_NOTE_LOG_FINDER_ID)
                gs_finder.text = USER_NOTE_LOG_FINDER
                gs_text = SubElement(gs_log, "groundspeak:text")
                gs_text.set("encoded", "False")
                gs_text.text = note_log_text
            if cache.logs:
                for log in content.select_logs(cache.logs, is_mine):
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

            # #700: the trackables in the cache, always written (empty when
            # there are none) like GSAK. The id is the trackable's numeric
            # id, kept in tracking_code; written only when known, like the
            # owner and finder ids above.
            gs_tbs = SubElement(gs_cache, "groundspeak:travelbugs")
            for tb in getattr(cache, "trackables", None) or []:
                gs_tb = SubElement(gs_tbs, "groundspeak:travelbug")
                if tb.tracking_code:
                    gs_tb.set("id", str(tb.tracking_code))
                gs_tb.set("ref", tb.ref or "")
                SubElement(gs_tb, "groundspeak:name").text = tb.name or ""

        if with_caches:
            if gpx_1_1:
                _to_gpx_1_1(wpt)
            yield "\n  ", None
            yield _xml_chunk(wpt, 1, short_empty=False), cache

        # Issue #753: child waypoints (parking areas, trailheads, stages,
        # final locations, etc.) were never exported at all — the loop
        # above only ever emits the ONE <wpt> for the cache's own listing.
        # Each child Waypoint gets its own sibling <wpt> element, mirroring
        # GSAK's plain-waypoint convention (a <wpt> with no
        # groundspeak:cache block, sym/type set from the waypoint's own
        # type). #700: like GSAK, <name> is the waypoint's own code (see
        # _child_code), <desc> and <urlname> its name, and <cmt> and <url>
        # are always written. #775: only the children the content options
        # choose; with children_only the first one carries the cache, so
        # the caller still counts the caches written.
        children = content.child_waypoints(cache) if with_children else []
        for child_index, child_wp in enumerate(children):
            cwpt = Element("wpt")
            cwpt.set("lat", f"{child_wp.latitude:.6f}")
            cwpt.set("lon", f"{child_wp.longitude:.6f}")

            if child_wp.wp_date:
                cwpt_time = SubElement(cwpt, "time")
                cwpt_time.text = child_wp.wp_date.strftime("%Y-%m-%dT%H:%M:%SZ")

            code = _child_code(child_wp, cache.gc_code)
            child_name = child_wp.name or child_wp.description or code
            SubElement(cwpt, "name").text = code
            SubElement(cwpt, "cmt").text = child_wp.comment or ""
            SubElement(cwpt, "desc").text = child_name
            SubElement(cwpt, "url").text = child_wp.url or ""
            SubElement(cwpt, "urlname").text = child_name

            cwpt_sym = SubElement(cwpt, "sym")
            cwpt_sym.text = child_wp.wp_type or "Waypoint"

            cwpt_type = SubElement(cwpt, "type")
            cwpt_type.text = f"Waypoint|{child_wp.wp_type or 'Waypoint'}"

            if gpx_1_1:
                _to_gpx_1_1(cwpt)
            marker = cache if not with_caches and child_index == 0 else None
            yield "\n  " + _xml_chunk(cwpt, 1, short_empty=False), marker

    yield "\n</gpx>\n", None


_GPX_1_1_NS = "http://www.topografix.com/GPX/1/1"
_GPX_1_1_SCHEMA_LOCATION = (
    "http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd"
    " http://www.groundspeak.com/cache/1/0/1 http://www.groundspeak.com/cache/1/0/1/cache.xsd"
    " http://www.gsak.net/xmlv1/6 http://www.gsak.net/xmlv1/6/gsak.xsd"
)


def _to_gpx_1_1(wpt) -> None:
    """Turn a <wpt> built in GPX 1.0 layout into GPX 1.1 (#775), in place.

    GPX 1.1 has no <url>/<urlname>: they become one <link href><text>. As
    in GSAK's own GPX 1.1 export, the link is written also without a URL
    (href="", a valid anyURI), so a child waypoint keeps its name as the
    link text. The GSAK and Groundspeak elements go into <extensions>,
    which GPX 1.1 requires to be the last child. Every other element keeps
    its place, since the 1.0 and 1.1 orders agree on them (ele, time, name,
    cmt, desc, link, sym, type).
    """
    from xml.etree.ElementTree import Element, SubElement

    url_el = wpt.find("url")
    urlname_el = wpt.find("urlname")
    if url_el is not None:
        index = list(wpt).index(url_el)
        wpt.remove(url_el)
        link = Element("link")
        link.set("href", url_el.text or "")
        if urlname_el is not None and urlname_el.text:
            SubElement(link, "text").text = urlname_el.text
        wpt.insert(index, link)
    if urlname_el is not None:
        wpt.remove(urlname_el)

    foreign = [el for el in wpt if el.tag.startswith(("gsak:", "groundspeak:"))]
    if foreign:
        extensions = SubElement(wpt, "extensions")
        for el in foreign:
            wpt.remove(el)
            extensions.append(el)


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

def generate_loc(
    caches: list, progress_cb=None, use_corrected: bool = True, text: ExportText = NO_TEXT,
) -> str:
    """
    Generate LOC 1.0 XML content from a list of Cache objects.
    Returns the LOC content as a string ready to write to file.

    LOC is a simple waypoint format supported by many GPS apps and devices.
    It includes GC code, name, coordinates, difficulty, terrain and container.
    Corrected coordinates are used when available, unless use_corrected=False.

    progress_cb(done, total): optional per-cache callback for GUI progress.
    Large exports should use write_loc() instead (#1075).
    """
    buf = io.StringIO()
    write_loc(buf, caches, progress_cb=progress_cb, use_corrected=use_corrected, text=text)
    return buf.getvalue()


def write_loc(
    out, caches: Iterable, progress_cb=None, use_corrected: bool = True,
    text: ExportText = NO_TEXT, total: Optional[int] = None,
) -> int:
    """Write the LOC for *caches* to the text stream *out*, one waypoint at a
    time (#1075). *caches*, *total* and the return value are as for
    write_gpx()."""
    from xml.etree.ElementTree import Element, SubElement
    import re

    # ET escapes CDATA — replace back the name content with proper CDATA
    # by re-building the name tags with raw CDATA sections
    def _fix_cdata(m: re.Match) -> str:
        gc_id = m.group(1)
        inner = m.group(2)
        # Unescape what ET escaped
        inner = inner.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        # Strip the literal CDATA wrapper text that we embedded as plain text
        inner = inner.replace("<![CDATA[", "").replace("]]>", "")
        return f'<name id="{gc_id}"><![CDATA[{inner}]]></name>'

    cdata_name = re.compile(r'<name id="([^"]*)">&lt;!\[CDATA\[([^\]]*)\]\]&gt;</name>')

    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    root_tag = '<loc version="1.0" src="OpenSAK"'
    count = 0

    if total is None:
        total = len(cast(Sized, caches))
    for i, cache in enumerate(caches, 1):
        if progress_cb:
            progress_cb(i, total)
        if cache.latitude is None or cache.longitude is None:
            continue

        export_lat, export_lon = _effective_coords(cache, use_corrected)

        wp = Element("waypoint")

        name_el = SubElement(wp, "name")
        name_el.set("id", cache.gc_code or "")
        # GSAK format: "Cache name by Owner (D/T)"
        diff = cache.difficulty or 1.0
        terr = cache.terrain or 1.0
        diff_str = f"{diff:g}"
        terr_str = f"{terr:g}"
        label = text.description(cache) or (
            f"{text.name(cache)} by {cache.placed_by or ''} ({diff_str}/{terr_str})"
        )
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

        if not count:
            out.write(root_tag + ">")
        out.write("\n  " + cdata_name.sub(_fix_cdata, _xml_chunk(wp, 1)))
        count += 1

    # An empty <loc> is self-closing, as ElementTree writes it.
    out.write("\n</loc>\n" if count else root_tag + " />")
    return count


# ── GGZ generator ─────────────────────────────────────────────────────────────

def generate_ggz(
    caches: list, filename: str = "opensak_export", progress_cb=None,
    use_corrected: bool = True,
    text: ExportText = NO_TEXT,
    attributes: bool = True,
    child_waypoints: bool = True,
    content: GpxContent = DEFAULT_CONTENT,
) -> bytes:
    """
    Generate a GGZ file (ZIP archive) from a list of Cache objects.
    Returns the GGZ content as bytes ready to write to file.

    GGZ structure:
      data/<filename>.gpx          — full GPX file with all cache data
      index/com/garmin/geocaches/v0/index.xml — lightweight index for Garmin

    The format allows Garmin devices to load more than the usual 10,000
    cache limit by using the GGZ container instead of plain GPX files.
    Corrected coordinates are used when available, unless use_corrected=False.

    progress_cb(done, total): optional per-cache callback for GUI progress.
    Large exports should use write_ggz() instead (#1075).
    """
    buf = io.BytesIO()
    write_ggz(buf, caches, filename, progress_cb=progress_cb,
              use_corrected=use_corrected, text=text,
              attributes=attributes, child_waypoints=child_waypoints,
              content=content)
    return buf.getvalue()


def write_ggz(
    out, caches: Iterable, filename: str = "opensak_export", progress_cb=None,
    use_corrected: bool = True,
    text: ExportText = NO_TEXT,
    attributes: bool = True,
    child_waypoints: bool = True,
    total: Optional[int] = None,
    bounds: Optional[Bounds] = None,
    content: GpxContent = DEFAULT_CONTENT,
) -> int:
    """Write a GGZ (see generate_ggz) to the seekable binary stream *out*,
    one cache at a time (#1075). *caches*, *total*, *bounds*, *content* and
    the return value are as for write_gpx(), except that a GGZ always has
    the caches in it (see GpxContent.for_format()).

    The GPX and the index are streamed into temporary files first: the
    index needs the GPX's CRC and each cache's byte offset in it, and
    zipfile needs the GPX's size up front to decide on ZIP64, exactly as
    writestr() did when the GPX was one big string.
    """
    import binascii
    import zipfile
    from xml.etree.ElementTree import Element, SubElement
    from datetime import datetime, timezone

    gpx_filename = f"{filename}.gpx"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _CONTAINER_SIZE = {
        "Micro": 2.0, "Small": 3.0, "Regular": 4.0,
        "Large": 5.0, "Not chosen": 3.0, "Other": 3.0,
    }

    def _index_entry(cache, file_pos: int, file_len: int) -> str:
        export_lat, export_lon = _effective_coords(cache, use_corrected)

        gch_el = Element("gch")

        code_el = SubElement(gch_el, "code")
        code_el.text = cache.gc_code or ""

        cname_el = SubElement(gch_el, "name")
        cname_el.text = text.name(cache)

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
            size_val = _CONTAINER_SIZE.get(cache.container)
            if size_val:
                size_el = SubElement(ratings_el, "size")
                size_el.text = str(size_val)

        terr_el = SubElement(ratings_el, "terrain")
        terr_el.text = str(cache.terrain or 1.0)

        if getattr(cache, "found", False):
            found_el = SubElement(gch_el, "found")
            found_el.text = "true"

        return "\n    " + _xml_chunk(gch_el, 2)

    # zipfile.ZipFile.mkdir() with a plain string defaults date_time to
    # (1980, 1, 1, 0, 0, 0) — the earliest date the ZIP format supports —
    # unlike writestr(), which defaults to the current time. That made every
    # directory entry in the .ggz show up as "1980-01-01" / "1979-12-31 23:00"
    # depending on timezone. Build explicit ZipInfo objects with the current
    # time so directories get a sensible date too.
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

    def _add_file(zf: "zipfile.ZipFile", name: str, src) -> None:
        # Like writestr(): the size is known before the entry is opened.
        zi = _file_zipinfo(name)
        zi.file_size = src.seek(0, os.SEEK_END)
        src.seek(0)
        with zf.open(zi, "w") as dst:
            shutil.copyfileobj(src, dst)

    content = content.for_format("ggz")
    if bounds is None and isinstance(caches, Sized):
        bounds = gpx_bounds(caches, use_corrected, child_waypoints, content)
    count = 0
    with tempfile.TemporaryFile() as gpx_tmp, tempfile.TemporaryFile() as index_tmp:
        # ── GPX, collecting the index entries as each <wpt> is written ────────
        # Each cache's byte offset is counted as its <wpt> is written (#466:
        # this used to be a regex pass over the finished GPX text). The first
        # <wpt> of a GC code wins, as with the old lookup; a cache without a
        # GC code gets 0/0.
        crc = 0
        pos = 0
        offsets_by_gc_code: dict = {}
        for chunk, cache in _gpx_chunks(
            caches, filename, progress_cb, use_corrected, text,
            attributes, child_waypoints, total, bounds, content,
        ):
            data = chunk.encode("utf-8")
            if cache is not None:
                gc_code = cache.gc_code or ""
                if gc_code:
                    offsets_by_gc_code.setdefault(gc_code, (pos, len(data)))
                file_pos, file_len = offsets_by_gc_code.get(gc_code, (0, 0))
                index_tmp.write(_index_entry(cache, file_pos, file_len).encode("utf-8"))
                count += 1
            crc = binascii.crc32(data, crc)
            gpx_tmp.write(data)
            pos += len(data)

        # ── index.xml around the collected entries ────────────────────────────
        time_el = Element("time")
        time_el.text = now_str
        fname_el = Element("name")
        fname_el.text = gpx_filename
        crc_el = Element("crc")
        crc_el.text = f"{crc & 0xFFFFFFFF:08X}"
        ftime_el = Element("time")
        ftime_el.text = now_str

        index_tmp.seek(0)
        with tempfile.TemporaryFile() as index_xml:
            index_xml.write((
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                '<ggz xmlns="http://www.opencaching.com/xmlschemas/ggz/1/0">'
                "\n  " + _xml_chunk(time_el, 1)
                + "\n  <file>"
                + "\n    " + _xml_chunk(fname_el, 2)
                + "\n    " + _xml_chunk(crc_el, 2)
                + "\n    " + _xml_chunk(ftime_el, 2)
            ).encode("utf-8"))
            shutil.copyfileobj(index_tmp, index_xml)
            index_xml.write(b"\n  </file>\n</ggz>\n")

            # ── Pack into ZIP ─────────────────────────────────────────────────
            with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.mkdir(_dir_zipinfo("data/"))
                _add_file(zf, f"data/{gpx_filename}", gpx_tmp)
                zf.mkdir(_dir_zipinfo("index/"))
                zf.mkdir(_dir_zipinfo("index/com/"))
                zf.mkdir(_dir_zipinfo("index/com/garmin/"))
                zf.mkdir(_dir_zipinfo("index/com/garmin/geocaches/"))
                zf.mkdir(_dir_zipinfo("index/com/garmin/geocaches/v0/"))
                _add_file(zf, "index/com/garmin/geocaches/v0/index.xml", index_xml)

    return count


def write_export(path: Path, write, binary: bool = False):
    """Call *write(f)* with a file open on a temporary file next to *path*,
    which replaces *path* only once *write* has finished; returns what it
    returns.

    #1075: exports stream into the file as they go, so a failure halfway
    must neither truncate the previous file nor leave a partial one behind.
    Text is written with the platform's newlines, as Path.write_text() did.
    """
    part = path.with_name(path.name + ".part")
    try:
        with (open(part, "wb") if binary else open(part, "w", encoding="utf-8")) as f:
            result = write(f)
        os.replace(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return result


def export_ggz_to_device(
    caches: Iterable,
    device_root: Path,
    filename: str = "opensak",
    progress_cb=None,
    total: Optional[int] = None,
    bounds: Optional[Bounds] = None,
    content: GpxContent = DEFAULT_CONTENT,
    use_corrected: bool = True,
) -> ExportResult:
    """
    Eksportér caches som GGZ fil direkte til en Garmin GPS enhed.
    Understøtter både normale mount points og MTP-enheder (via gio copy).

    *caches* may be any iterable (e.g. iter_caches_full()), streamed to the
    file one by one (#1075); *total*, *bounds*, *content* and
    *use_corrected* are as for write_ggz().
    """
    result = ExportResult()
    result.device = device_root

    def _write(f) -> int:
        return write_ggz(f, caches, filename, progress_cb=progress_cb, total=total,
                         bounds=bounds, content=content, use_corrected=use_corrected)

    try:
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
                with open(tmp_path, "wb") as f:
                    count = _write(f)
                _gio_copy_with_replace(tmp_path, output_path)
        else:
            ggz_dir = get_garmin_ggz_path(device_root)
            ggz_dir.mkdir(parents=True, exist_ok=True)
            output_path = ggz_dir / f"{filename}.ggz"
            count = write_export(output_path, _write, binary=True)

        result.file_path   = output_path
        result.cache_count = count

    except PermissionError:
        result.error = tr("gps_error_permission")
    except OSError as e:
        result.error = tr("gps_error_file", error=str(e))
    except Exception as e:
        result.error = tr("gps_error_unexpected", error=str(e))

    return result


def export_ggz_to_file(
    caches: Iterable,
    output_path: Path,
    progress_cb=None,
    total: Optional[int] = None,
    bounds: Optional[Bounds] = None,
    content: GpxContent = DEFAULT_CONTENT,
    use_corrected: bool = True,
) -> ExportResult:
    """
    Eksportér caches som GGZ fil til en valgfri placering.
    Bruges når GPS ikke er tilsluttet direkte.
    *caches*, *total*, *bounds*, *content* and *use_corrected* are as for
    export_ggz_to_device().
    """
    result = ExportResult()

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.cache_count = write_export(
            output_path,
            lambda f: write_ggz(f, caches, output_path.stem, progress_cb=progress_cb,
                                total=total, bounds=bounds, content=content,
                                use_corrected=use_corrected),
            binary=True,
        )
        result.file_path   = output_path
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
    caches: Iterable,
    device_root: Path,
    filename: str = "opensak",
    progress_cb=None,
    total: Optional[int] = None,
    bounds: Optional[Bounds] = None,
    content: GpxContent = DEFAULT_CONTENT,
    use_corrected: bool = True,
) -> ExportResult:
    """
    Eksportér caches til en Garmin GPS enhed.
    Understøtter både normale mount points og MTP-enheder (via gio copy).
    *caches*, *total*, *bounds*, *content* and *use_corrected* are as for
    export_ggz_to_device().
    """
    result = ExportResult()
    result.device = device_root

    def _write(f) -> int:
        return write_gpx(f, caches, filename, progress_cb=progress_cb, total=total,
                         bounds=bounds, content=content, use_corrected=use_corrected)

    try:
        if is_mtp_device(device_root):
            gpx_dir = _find_mtp_gpx_dir(device_root)
            if gpx_dir is None:
                result.error = tr("gps_error_file", error="Garmin/GPX folder not found on MTP device")
                return result
            output_path = gpx_dir / f"{filename}.gpx"
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir) / output_path.name
                with open(tmp_path, "w", encoding="utf-8") as f:
                    count = _write(f)
                _gio_copy_with_replace(tmp_path, output_path)
        else:
            gpx_dir = get_garmin_gpx_path(device_root)
            gpx_dir.mkdir(parents=True, exist_ok=True)
            output_path = gpx_dir / f"{filename}.gpx"
            count = write_export(output_path, _write)

        result.file_path   = output_path
        result.cache_count = count

    except PermissionError:
        result.error = tr("gps_error_permission")
    except OSError as e:
        result.error = tr("gps_error_file", error=str(e))
    except Exception as e:
        result.error = tr("gps_error_unexpected", error=str(e))

    return result


def export_to_file(
    caches: Iterable,
    output_path: Path,
    progress_cb=None,
    total: Optional[int] = None,
    bounds: Optional[Bounds] = None,
    content: GpxContent = DEFAULT_CONTENT,
    use_corrected: bool = True,
) -> ExportResult:
    """
    Eksportér caches til en GPX fil (valgfri placering).
    Bruges når GPS ikke er tilsluttet.
    *caches*, *total*, *bounds*, *content* and *use_corrected* are as for
    export_ggz_to_device().
    """
    result = ExportResult()

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.cache_count = write_export(
            output_path,
            lambda f: write_gpx(f, caches, output_path.stem, progress_cb=progress_cb,
                                total=total, bounds=bounds, content=content,
                                use_corrected=use_corrected),
        )
        result.file_path   = output_path
    except Exception as e:
        result.error = str(e)

    return result
