"""
src/opensak/gps/garmin.py — Garmin GPS device detection og GPX/LOC/GGZ export.

Understøtter alle Garmin enheder der monteres som USB drev og
accepterer GPX filer i /Garmin/GPX/ mappen.

Testet med: GPSMAP64s, Oregon750

Corrected coordinates: Hvis en cache har bruger-korrigerede koordinater
(user_note.is_corrected), bruges disse som waypoint-koordinater i GPX
filen. De originale koordinater gemmes i en groundspeak:original_coords
kommentar, så de ikke mistes.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Optional


# ── Garmin GPX mappe på enheden ───────────────────────────────────────────────

GARMIN_GPX_SUBPATH = Path("Garmin") / "GPX"
GARMIN_MARKERS = [
    Path("Garmin") / "GarminDevice.xml",
    Path("Garmin") / "GPX",
    Path(".is_garmin"),
]


# ── Enhed detektion ───────────────────────────────────────────────────────────

def find_garmin_devices() -> list[Path]:
    """
    Find alle monterede Garmin GPS enheder.
    Returnerer liste af rod-stier (mount points).

    Virker på Linux, Windows og macOS.
    """
    candidates = _get_mount_points()
    devices = []

    for mount in candidates:
        if _is_garmin(mount):
            devices.append(mount)

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


def get_garmin_gpx_path(device_root: Path) -> Path:
    """Returner stien til GPX mappen på en Garmin enhed."""
    return device_root / GARMIN_GPX_SUBPATH


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
    Generer GPX 1.1 indhold fra en liste af Cache objekter.
    Returnerer GPX som en streng klar til at skrive til fil.

    Caches med korrigerede koordinater eksporteres med de korrigerede
    koordinater som waypoint-position. De originale koordinater bevares
    i en cmt (comment) feltom muligt.

    progress_cb(done, total): valgfrit kald per cache, så GUI kan vise fremgang.
    """
    from xml.etree.ElementTree import Element, SubElement
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone

    # Root element
    gpx = Element("gpx")
    gpx.set("version", "1.1")
    gpx.set("creator", "OpenSAK")
    gpx.set("xmlns", "http://www.topografix.com/GPX/1/1")
    gpx.set("xmlns:groundspeak", "http://www.groundspeak.com/cache/1/0/1")
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

        sym_wpt = SubElement(wpt, "sym")
        sym_wpt.text = _cache_symbol(cache.cache_type or "")

        type_wpt = SubElement(wpt, "type")
        type_wpt.text = f"Geocache|{cache.cache_type or 'Traditional Cache'}"

        # Groundspeak extensions
        extensions = SubElement(wpt, "extensions")
        gs_cache = SubElement(extensions, "groundspeak:cache")
        gs_cache.set("id", str(cache.id))
        gs_cache.set("available", "True" if cache.available else "False")
        gs_cache.set("archived", "True" if cache.archived else "False")

        gs_name = SubElement(gs_cache, "groundspeak:name")
        gs_name.text = cache.name or ""

        gs_placed = SubElement(gs_cache, "groundspeak:placed_by")
        gs_placed.text = cache.placed_by or ""

        gs_type = SubElement(gs_cache, "groundspeak:type")
        gs_type.text = cache.cache_type or "Traditional Cache"

        gs_container = SubElement(gs_cache, "groundspeak:container")
        gs_container.text = cache.container or "Regular"

        gs_diff = SubElement(gs_cache, "groundspeak:difficulty")
        gs_diff.text = str(cache.difficulty or 1.0)

        gs_terr = SubElement(gs_cache, "groundspeak:terrain")
        gs_terr.text = str(cache.terrain or 1.0)

        if cache.country:
            gs_country = SubElement(gs_cache, "groundspeak:country")
            gs_country.text = cache.country

        if cache.encoded_hints:
            gs_hints = SubElement(gs_cache, "groundspeak:encoded_hints")
            gs_hints.text = cache.encoded_hints

        # Logs (max 5)
        if cache.logs:
            gs_logs = SubElement(gs_cache, "groundspeak:logs")
            for log in sorted(
                cache.logs,
                key=lambda l: l.log_date or 0,
                reverse=True,
            )[:5]:
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
                gs_finder.text = log.finder or ""
                gs_text = SubElement(gs_log, "groundspeak:text")
                gs_text.set("encoded", "False")
                gs_text.text = (log.text or "")[:500]

        # GSAK personal note
        note = getattr(cache, "user_note", None)
        note_text = getattr(note, "note", None) if note else None
        if note_text:
            gsak_ext = SubElement(extensions, "gsak:wptExtension")
            gsak_note = SubElement(gsak_ext, "gsak:UserNote")
            gsak_note.text = note_text

    _indent(gpx)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        gpx, encoding="unicode"
    )


def _cache_symbol(cache_type: str) -> str:
    """Returner Garmin symbol navn for en cache type."""
    symbols = {
        "Traditional Cache": "Geocache",
        "Multi-cache":        "Geocache",
        "Unknown Cache":      "Geocache",
        "Letterbox Hybrid":   "Geocache",
        "Wherigo Cache":      "Geocache",
        "Event Cache":        "Geocache",
        "Earthcache":         "Geocache",
        "Virtual Cache":      "Geocache",
    }
    return symbols.get(cache_type, "Geocache")


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

    total = len(caches)
    for i, cache in enumerate(caches, 1):
        if progress_cb:
            progress_cb(i, total)
        if cache.latitude is None or cache.longitude is None:
            continue

        export_lat, export_lon = _effective_coords(cache)
        gc_code = cache.gc_code or ""

        # Find byte offset of this waypoint in the GPX
        search_str = f'<name>{gc_code}</name>'
        # Look for the <wpt ...> tag that contains this GC code
        import re
        pattern = rf'(<wpt\b[^>]*>(?:(?!</wpt>).)*?<name>{re.escape(gc_code)}</name>)'
        m = re.search(pattern, gpx_text, re.DOTALL)
        if m:
            file_pos = gpx_text[:m.start()].encode("utf-8").__len__()
            file_len = len(m.group(0).encode("utf-8"))
            # Approximate: include the closing </wpt> tag
            wpt_end = gpx_text.find("</wpt>", m.start())
            if wpt_end >= 0:
                file_len = len(gpx_text[m.start():wpt_end + 6].encode("utf-8"))
        else:
            file_pos = 0
            file_len = 0

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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.mkdir("data/")
        zf.writestr(f"data/{gpx_filename}", gpx_content)
        zf.mkdir("index/")
        zf.mkdir("index/com/")
        zf.mkdir("index/com/garmin/")
        zf.mkdir("index/com/garmin/geocaches/")
        zf.mkdir("index/com/garmin/geocaches/v0/")
        zf.writestr(
            "index/com/garmin/geocaches/v0/index.xml",
            index_xml.encode("utf-8"),
        )

    return buf.getvalue()


# ── Slet GPX filer fra enhed ──────────────────────────────────────────────────

class DeleteResult:
    """Resultat af sletning af GPX filer fra GPS enhed."""

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
            return f"✗ Fejl ved sletning: {self.error}"
        if self.deleted_count == 0:
            return "ℹ️  Ingen GPX filer fundet på enheden"
        lines = [f"🗑️  {self.deleted_count} GPX fil(er) slettet fra enheden"]
        for f in self.deleted_files:
            lines.append(f"   - {f.name}")
        if self.failed_count:
            lines.append(f"⚠️  {self.failed_count} fil(er) kunne ikke slettes:")
            for f in self.failed_files:
                lines.append(f"   - {f.name}")
        return "\n".join(lines)


def delete_gpx_files(
    device_root: Path,
    pattern: str = "*.gpx",
) -> DeleteResult:
    """
    Slet alle GPX filer i Garmin/GPX mappen på enheden.
    """
    result = DeleteResult()
    result.device = device_root

    try:
        gpx_dir = get_garmin_gpx_path(device_root)

        if not gpx_dir.exists():
            return result

        gpx_files = list(gpx_dir.glob(pattern))

        for gpx_file in gpx_files:
            if not gpx_file.is_file():
                continue
            try:
                gpx_file.unlink()
                result.deleted_files.append(gpx_file)
            except (PermissionError, OSError):
                result.failed_files.append(gpx_file)

    except PermissionError:
        result.error = "Adgang nægtet — er GPS enheden skrivebeskyttet?"
    except OSError as e:
        result.error = f"Fil fejl: {e}"
    except Exception as e:
        result.error = f"Uventet fejl: {e}"

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
            return (
                f"✓ {self.cache_count} caches eksporteret\n"
                f"  Enhed: {self.device}\n"
                f"  Fil: {self.file_path.name if self.file_path else ''}"
            )
        return f"✗ Fejl: {self.error}"


def export_to_device(
    caches: list,
    device_root: Path,
    filename: str = "opensak",
    progress_cb=None,
) -> ExportResult:
    """
    Eksportér caches til en Garmin GPS enhed.
    Caches med korrigerede koordinater eksporteres med disse.
    """
    result = ExportResult()
    result.device = device_root

    try:
        gpx_dir = get_garmin_gpx_path(device_root)
        gpx_dir.mkdir(parents=True, exist_ok=True)

        gpx_content = generate_gpx(caches, filename, progress_cb=progress_cb)

        output_path = gpx_dir / f"{filename}.gpx"
        output_path.write_text(gpx_content, encoding="utf-8")

        result.file_path   = output_path
        result.cache_count = len([c for c in caches if c.latitude is not None])

    except PermissionError:
        result.error = "Adgang nægtet — er GPS enheden skrivebeskyttet?"
    except OSError as e:
        result.error = f"Fil fejl: {e}"
    except Exception as e:
        result.error = f"Uventet fejl: {e}"

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
