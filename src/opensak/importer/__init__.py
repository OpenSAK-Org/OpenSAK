"""
src/opensak/importer/__init__.py — GPX + LOC importer for OpenSAK.

Supports:
- Single .gpx files (Groundspeak/Pocket Query format, GPX 1.0)
- Pocket Query .zip files (main GPX + companion -wpts.gpx file)
- .loc files (basic geocaching format with coordinates only)
- Duplicate handling: upserts existing caches by gc_code
- Windows \r\n line endings handled transparently by lxml
"""

from __future__ import annotations

import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lxml import etree
from sqlalchemy.orm import Session
import tempfile

from opensak.db.models import Attribute, Cache, Log, Trackable, UserNote, Waypoint


# ── XML namespace map used by Groundspeak Pocket Queries ─────────────────────
# Primary namespace map — uses /1/0/1 (newer PQ files)
NS = {
    "gpx": "http://www.topografix.com/GPX/1/0",
    "gs":  "http://www.groundspeak.com/cache/1/0/1",
}

# Older PQ files (including My Finds) use /1/0 without the trailing /1
_GS_NAMESPACES = [
    "http://www.groundspeak.com/cache/1/0/1",
    "http://www.groundspeak.com/cache/1/0",
]

# GSAK custom namespace — used in GPX files exported from GSAK
_GSAK_NAMESPACES = [
    "http://www.gsak.net/xmlv1/6",
    "http://www.gsak.net/xmlv1/5",
    "http://www.gsak.net/xmlv1/4",
]


def _make_ns(gs_uri: str) -> dict:
    """Return a namespace dict with the given Groundspeak URI."""
    return {"gpx": "http://www.topografix.com/GPX/1/0", "gs": gs_uri}


def _text(element, xpath: str, ns: dict = NS) -> Optional[str]:
    """Return stripped text of first XPath match, or None."""
    if element is None:
        return None
    nodes = element.xpath(xpath, namespaces=ns)
    if nodes:
        val = nodes[0] if isinstance(nodes[0], str) else nodes[0].text
        return val.strip() if val and val.strip() else None
    return None


def _float(element, xpath: str, ns: dict = NS) -> Optional[float]:
    """Return float of first XPath match, or None."""
    val = _text(element, xpath, ns)
    try:
        return float(val) if val is not None else None
    except ValueError:
        return None


def _bool_attr(element, xpath: str, ns: dict = NS) -> bool:
    """Return bool from an attribute value like 'True'/'False'."""
    if element is None:
        return False
    nodes = element.xpath(xpath, namespaces=ns)
    if nodes:
        return str(nodes[0]).strip().lower() == "true"
    return False


def _parse_datetime(raw: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 datetime strings from GPX files into UTC datetimes.

    Geocaching.com's own GPX exports use a bare or 'Z'-suffixed timestamp
    ("2026-03-16T00:00:00Z"), which the old strptime-only version below
    handled fine. Third-party generators such as Project-GC instead emit an
    explicit numeric UTC offset ("2026-03-16T00:00:00+00:00") — none of the
    strptime patterns match that, so the whole field silently came back as
    None (issue #617, e.g. hidden_date and log dates disappearing on
    import). Try the offset-aware ISO 8601 parse first and convert properly
    to UTC; fall back to the older strptime patterns for anything else.
    """
    if not raw:
        return None
    raw = raw.strip()

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    raw = raw.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ── Illegal-character sanitizing reader ───────────────────────────────────────

# XML 1.0 forbids some characters even as character references, but GPX
# generators emit them anyway — e.g. geocaching.com descriptions pasted from
# Word carry ``font-family:&#xFFFF;``. lxml then rejects the whole file with
# "xmlParseCharRef: invalid xmlChar value 65535". Strip them before parsing.
_CHAR_REF_RE = re.compile(rb"&#(?:x([0-9A-Fa-f]+)|([0-9]+));")
# Raw C0 controls (never part of a UTF-8 multibyte sequence) and raw U+FFFE/U+FFFF.
_RAW_ILLEGAL_RE = re.compile(rb"[\x00-\x08\x0B\x0C\x0E-\x1F]|\xEF\xBF[\xBE\xBF]")
# A character reference longer than this is not held back across chunk borders.
_MAX_CHAR_REF_LEN = 32


def _is_xml_char(cp: int) -> bool:
    return (
        cp in (0x9, 0xA, 0xD)
        or 0x20 <= cp <= 0xD7FF
        or 0xE000 <= cp <= 0xFFFD
        or 0x10000 <= cp <= 0x10FFFF
    )


def _drop_illegal_char_ref(m: re.Match) -> bytes:
    hex_digits, dec_digits = m.groups()
    cp = int(hex_digits, 16) if hex_digits else int(dec_digits)
    return m.group(0) if _is_xml_char(cp) else b""


def _sanitize_xml_bytes(data: bytes) -> bytes:
    return _RAW_ILLEGAL_RE.sub(b"", _CHAR_REF_RE.sub(_drop_illegal_char_ref, data))


def _incomplete_tail_start(buf: bytes) -> int:
    """Index where a possibly unfinished char ref / U+FFFx sequence begins."""
    cut = len(buf)
    amp = buf.rfind(b"&", max(0, cut - _MAX_CHAR_REF_LEN))
    if amp != -1 and b";" not in buf[amp:]:
        cut = amp
    if buf.endswith(b"\xef\xbf"):
        cut = min(cut, len(buf) - 2)
    elif buf.endswith(b"\xef"):
        cut = min(cut, len(buf) - 1)
    return cut


class _SanitizedXmlReader:
    """Binary file-like object that strips XML-illegal characters while streaming.

    Reads in chunks so iterparse keeps its flat memory profile; a partial
    character reference at a chunk border is held back until the next chunk.
    UTF-16 files (BOM or NUL bytes up front) are passed through untouched,
    since byte-level filtering would corrupt them.
    """

    def __init__(self, path: Path, chunk_size: int = 1 << 16):
        self._fh = open(path, "rb")
        self._chunk_size = chunk_size
        self._pending = b""   # sanitized, but may end in an unfinished sequence
        self._ready = b""     # sanitized and safe to hand out
        self._eof = False
        head = self._fh.peek(4)[:4]
        self._passthrough = head.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in head

    def _fill(self) -> bool:
        if self._eof:
            return False
        chunk = self._fh.read(self._chunk_size)
        if not chunk:
            self._eof = True
            self._ready += _sanitize_xml_bytes(self._pending)
            self._pending = b""
            return False
        buf = _sanitize_xml_bytes(self._pending + chunk)
        cut = _incomplete_tail_start(buf)
        self._ready += buf[:cut]
        self._pending = buf[cut:]
        return True

    def read(self, size: int = -1) -> bytes:
        if self._passthrough:
            return self._fh.read(size)
        if size is None or size < 0:
            while self._fill():
                pass
            data, self._ready = self._ready, b""
            return data
        while len(self._ready) < size and self._fill():
            pass
        data, self._ready = self._ready[:size], self._ready[size:]
        return data

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "_SanitizedXmlReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _iterparse(path: Path, **kwargs):
    """etree.iterparse over a sanitized stream; the file closes with the generator."""
    with _SanitizedXmlReader(path) as src:
        yield from etree.iterparse(src, **kwargs)


def _parse_xml(path: Path):
    """etree.parse over a sanitized stream."""
    with _SanitizedXmlReader(path) as src:
        return etree.parse(src)


# ── Companion file detector ───────────────────────────────────────────────────

def _is_companion_gpx(path: Path) -> bool:
    """Return True if the GPX contains only extra waypoints, not cache entries.

    Reads up to the first <wpt> element only — fast enough to call at dialog
    time without blocking the UI.  A companion file has wpt entries with
    type "Waypoint|…"; a main cache GPX has type "Geocache|…" or a
    Groundspeak cache extension block.
    """
    try:
        for _, elem in _iterparse(path, events=("end",), tag=None):
            if etree.QName(elem).localname != "wpt":
                elem.clear()
                continue
            gpx_ns = {"gpx": etree.QName(elem).namespace or NS["gpx"]}
            type_raw = _text(elem, "gpx:type", gpx_ns) or ""
            if type_raw.startswith("Geocache"):
                return False
            for gs_uri in _GS_NAMESPACES:
                if elem.find(f".//{{{gs_uri}}}cache") is not None:
                    return False
            # First wpt is not a cache entry — companion file
            return True
    except Exception:
        pass
    return False


# ── Waypoint (extra) parser ───────────────────────────────────────────────────

def _parse_extra_waypoints(tree) -> dict[str, list[dict]]:
    """
    Parse a -wpts.gpx companion file.

    Returns a dict keyed by GC code (derived from waypoint name prefix rules):
      - Waypoint names like '04BDQBF' → GC prefix is 'GC' + name[2:]  → 'GCBDQBF'
      - But Groundspeak actually stores the parent GC code in <desc> or we
        derive it from the numeric prefix + remaining chars.

    In practice the companion file does NOT embed the parent GC code directly.
    The link is: wpt <n> characters from position 2 onward == gc_code chars
    from position 2 onward.  E.g. '04BDQBF'[2:] == 'BDQBF', parent == 'GC??BDQBF'
    — but the exact digits differ.

    The reliable approach: build a lookup by the suffix (chars [2:]) and match
    against caches in the DB after import.  We store the raw name here and
    resolve during DB write.
    """
    result: dict[str, list[dict]] = {}  # keyed by wpt_suffix (name[2:])

    root = tree.getroot()
    # Strip default namespace for plain XPath
    ns = {"gpx": root.nsmap.get(None, "http://www.topografix.com/GPX/1/0")}

    for wpt in root.findall("{%s}wpt" % ns["gpx"]):
        name_el = (
            wpt.find("{%s}n" % ns["gpx"]) or
            wpt.find("{%s}name" % ns["gpx"])
        )
        name = name_el.text.strip() if name_el is not None and name_el.text else ""
        if len(name) < 3:
            continue

        suffix = name[2:]   # e.g. 'BDQBF' from '04BDQBF'

        desc_el = wpt.find("{%s}desc" % ns["gpx"])
        cmt_el  = wpt.find("{%s}cmt" % ns["gpx"])
        type_el = wpt.find("{%s}type" % ns["gpx"])

        wp_type_raw = type_el.text.strip() if type_el is not None and type_el.text else ""
        wp_type = wp_type_raw.split("|")[-1] if "|" in wp_type_raw else wp_type_raw

        entry = {
            "prefix":      name[:2],
            "name":        name,
            "wp_type":     wp_type,
            "description": desc_el.text.strip() if desc_el is not None and desc_el.text else None,
            "comment":     cmt_el.text.strip()  if cmt_el  is not None and cmt_el.text  else None,
            "latitude":    float(wpt.get("lat")) if wpt.get("lat") else None,
            "longitude":   float(wpt.get("lon")) if wpt.get("lon") else None,
        }
        result.setdefault(suffix, []).append(entry)

    return result


# ── Main cache parser ─────────────────────────────────────────────────────────

def _parse_wpt(wpt_el) -> Optional[dict]:
    """
    Parse a single <wpt> element from a Pocket Query GPX file.
    Returns a dict of fields ready to construct/update a Cache, or None on error.
    """
    try:
        lat = float(wpt_el.get("lat"))
        lon = float(wpt_el.get("lon"))
    except (TypeError, ValueError):
        return None

    # Use the file's actual GPX namespace (1/0 for Pocket Queries, 1/1 for
    # OpenSAK's own export and many third-party tools) so the gpx: lookups match.
    gpx_ns = {"gpx": etree.QName(wpt_el).namespace or NS["gpx"]}

    # GC code: <name> in newer PQ files, <n> in older format
    gc_code = (
        _text(wpt_el, "gpx:name", gpx_ns) or
        _text(wpt_el, "gpx:n", gpx_ns)
    )
    if not gc_code:
        return None

    # Cache type from <type>Geocache|Traditional Cache</type>. A real GSAK
    # GPX export appends a trailing "|Found" segment for caches the account
    # owner has personally marked as found, e.g.
    # <type>Geocache|Traditional Cache|Found</type> — confirmed against a
    # real GSAK-exported before/after pair from Allyn56 (issue #766). Strip
    # that segment off before deriving the cache type, so it isn't mistaken
    # for the cache type itself.
    type_raw = _text(wpt_el, "gpx:type", gpx_ns) or ""
    _type_parts = [p.strip() for p in type_raw.split("|")] if type_raw else []
    type_says_found = bool(_type_parts) and _type_parts[-1].lower() == "found"
    if type_says_found:
        _type_parts = _type_parts[:-1]
    cache_type_full = _type_parts[-1] if len(_type_parts) > 1 else type_raw.split("|")[0].strip()

    # Accept GC codes (standard caches) unconditionally, and any other code
    # (including LC / Adventure Lab / lab2gpx prefixes such as LB, LA, etc.)
    # only when the type field actually identifies it as a Geocache.
    #
    # Issue #741: lab2gpx reuses the LC-prefix for BOTH the lab cache itself
    # AND its individual stage waypoints (e.g. <type>Waypoint|Parking Area</type>,
    # <type>Waypoint|Virtual Stage</type>). Those stage entries have no
    # <groundspeak:cache> block, so previously they slipped past this check,
    # were treated as caches, and crashed downstream when gs_cache was None.
    # They should fall through to _parse_extra_wpt() as ordinary waypoints.
    if not gc_code.startswith("GC"):
        if not type_raw.startswith("Geocache"):
            return None

    hidden_raw = _text(wpt_el, "gpx:time", gpx_ns)

    # ── Found by me — detekteret via <sym>Geocache Found</sym> ELLER via
    # <type>...|Found</type> ─────────────────────────────────────────────
    # Groundspeak/GC.com Pocket Query sætter sym til "Geocache Found" for
    # caches fundet af PQ-ejeren. GSAK bruger IKKE den konvention i sin egen
    # GPX-eksport — <sym> afspejler der altid bare cache-type-ikonet uanset
    # found-status — men tilføjer i stedet et "|Found"-segment til <type>
    # (se type_says_found ovenfor). Begge signaler er lige autoritative og
    # kræver ikke at gc_username/gc_finder_id er sat i Settings, da de er
    # eksplicitte "jeg har fundet denne" markører fra selve kilde-værktøjet
    # — bekræftet mod et ægte GSAK before/after-eksportpar fra Allyn56
    # (issue #766).
    sym_raw = _text(wpt_el, "gpx:sym", gpx_ns)
    sym = sym_raw or ""
    found_by_me = sym.strip().lower() == "geocache found" or type_says_found

    # ── Groundspeak extension block ───────────────────────────────────────────
    # Detect which Groundspeak namespace this file actually uses (/1/0 or /1/0/1)
    # groundspeak:cache sits directly under <wpt> in GPX 1.0 Pocket Queries but
    # inside an <extensions> wrapper in GPX 1.1 (incl. OpenSAK's own export), so
    # search descendants rather than only direct children.
    gs_cache = None
    active_ns = NS  # default
    for gs_uri in _GS_NAMESPACES:
        gs_cache = wpt_el.find(f".//{{{gs_uri}}}cache")
        if gs_cache is not None:
            active_ns = _make_ns(gs_uri)
            break

    gs_id        = gs_cache.get("id")                    if gs_cache is not None else None
    available    = _bool_attr(gs_cache, "@available")    if gs_cache is not None else True
    archived     = _bool_attr(gs_cache, "@archived")     if gs_cache is not None else False

    name         = _text(gs_cache, "gs:name",              active_ns) or _text(wpt_el, "gpx:urlname", gpx_ns) or gc_code
    placed_by    = _text(gs_cache, "gs:placed_by",         active_ns)
    owner        = _text(gs_cache, "gs:owner",             active_ns)
    owner_id     = gs_cache.find("gs:owner", active_ns).get("id") if gs_cache is not None and gs_cache.find("gs:owner", active_ns) is not None else None
    cache_type   = _text(gs_cache, "gs:type",              active_ns) or cache_type_full
    if cache_type.lower() in ("gps adventures maze exhibit", "gps adventures exhibit"):
        cache_type = "GPS Adventures Maze"
    # Issue #591 / #756: Community Celebration Events (a limited-run
    # program, May 2020 - Dec 2021) never got their own value in
    # Groundspeak's machine-readable <groundspeak:type> field. #591
    # originally assumed geocaching.com's GPX always exports these as
    # plain "Event Cache", detected only via "Community Celebration
    # Event" appearing in the free-text cache name. Real-world exports
    # (#756) show geocaching.com actually uses the raw type string
    # "Lost and Found Event Caches" for these — a legacy internal name
    # unrelated to the cache's public name (confirmed against 88 CCE
    # caches, only 16 of which mention "CCE"/"community celebration" in
    # the name at all). That raw type is unambiguous on its own, so it's
    # matched directly; the #591 name-text fallback is kept as a
    # secondary check in case a different export path still uses plain
    # "Event Cache" for some CCE caches.
    elif cache_type.lower() == "lost and found event caches":
        cache_type = "Community Celebration Event"
    elif cache_type == "Event Cache" and "community celebration event" in name.lower():
        cache_type = "Community Celebration Event"
    container    = _text(gs_cache, "gs:container",         active_ns)
    difficulty   = _float(gs_cache, "gs:difficulty",       active_ns)
    terrain      = _float(gs_cache, "gs:terrain",          active_ns)
    country      = _text(gs_cache, "gs:country",           active_ns)
    state        = _text(gs_cache, "gs:state",             active_ns)
    county       = _text(gs_cache, "gs:county",            active_ns)
    short_desc   = _text(gs_cache, "gs:short_description", active_ns)
    long_desc    = _text(gs_cache, "gs:long_description",  active_ns)
    hints        = _text(gs_cache, "gs:encoded_hints",     active_ns)

    short_html = False
    long_html  = False
    if gs_cache is not None:
        sd_el = gs_cache.find("gs:short_description", active_ns)
        ld_el = gs_cache.find("gs:long_description",  active_ns)
        if sd_el is not None:
            short_html = (sd_el.get("html", "False").lower() == "true")
        if ld_el is not None:
            long_html  = (ld_el.get("html", "False").lower() == "true")

    # ── Attributes ────────────────────────────────────────────────────────────
    attributes = []
    if gs_cache is not None:
        for attr_el in gs_cache.findall("gs:attributes/gs:attribute", active_ns):
            try:
                attr_id = int(attr_el.get("id", 0))
                is_on   = attr_el.get("inc", "1") == "1"
                attr_name = attr_el.text.strip() if attr_el.text else ""
                attributes.append({"attribute_id": attr_id, "name": attr_name, "is_on": is_on})
            except (ValueError, AttributeError):
                continue

    # ── Logs ─────────────────────────────────────────────────────────────────
    logs = []
    if gs_cache is not None:
        for log_el in gs_cache.findall("gs:logs/gs:log", active_ns):
            log_id   = log_el.get("id")
            log_type = _text(log_el, "gs:type",   active_ns)
            log_date = _parse_datetime(_text(log_el, "gs:date", active_ns))
            finder   = _text(log_el, "gs:finder", active_ns)
            finder_el = log_el.find("gs:finder", active_ns)
            finder_id = finder_el.get("id") if finder_el is not None else None
            text_el  = log_el.find("gs:text", active_ns)
            log_text = text_el.text.strip() if text_el is not None and text_el.text else None
            encoded  = (text_el.get("encoded", "False").lower() == "true") if text_el is not None else False

            if log_type:
                logs.append({
                    "log_id":       log_id,
                    "log_type":     log_type,
                    "log_date":     log_date,
                    "finder":       finder,
                    "finder_id":    finder_id,
                    "text":         log_text,
                    "text_encoded": encoded,
                })

    # ── Issue #766 continued (Allyn56 round-trip testing): detect genuine
    # GSAK-origin files ────────────────────────────────────────────────────
    # Real-world testing shows GSAK's own GPX/GGZ export does NOT reliably
    # set sym="Geocache Found" for found caches the way the GC.com Pocket
    # Query convention does — <sym> from a genuine GSAK export still just
    # reflects the cache TYPE icon (e.g. "Geocache", "Multi-cache"),
    # regardless of found state. So for a file that actually came from GSAK,
    # <sym> can't be trusted as a "not found" signal either, and the
    # sym-is-absent guard below (which exists to protect OpenSAK's own
    # re-exports) wrongly blocks the log-based fallback.
    #
    # OpenSAK's own export (garmin.py) only ever writes <gsak:UserNote>
    # inside <gsak:wptExtension> — never any of GSAK's other per-cache
    # fields. So the presence of any of those OTHER fields (already parsed
    # a little further down for issue #269/#521/#129/#73) is a reliable
    # fingerprint that this wpt genuinely came from GSAK itself, as opposed
    # to OpenSAK's own re-export or a raw geocaching.com Pocket Query
    # (neither of which ever populate them).
    _GSAK_ONLY_TAGS = (
        "UserFlag", "FirstToFind", "UserSort", "IsPremium",
        "UserData", "User2", "User3", "User4", "FavPoints", "County",
        "LatN", "LongE", "LatBeforeCorrect", "LonBeforeCorrect",
    )
    is_gsak_source = False
    for gsak_uri in _GSAK_NAMESPACES:
        _gsak_ext_probe = wpt_el.find(f".//{{{gsak_uri}}}wptExtension")
        if _gsak_ext_probe is not None and any(
            _gsak_ext_probe.find(f"{{{gsak_uri}}}{tag}") is not None
            for tag in _GSAK_ONLY_TAGS
        ):
            is_gsak_source = True
            break

    # ── Issue #766: found-by-me fallback via log list ───────────────────────
    # sym="Geocache Found" is the Groundspeak/GC.com Pocket Query convention,
    # but not every source GPX is guaranteed to set it. Fall back to checking
    # the log list for the configured user's own found-type log, using the
    # same finder_id/username matching rules already used for
    # found_date/FTF derivation further down — so found status is detected
    # regardless of which convention the source tool follows.
    #
    # The fallback runs when <sym> is entirely ABSENT from the wpt, OR when
    # the wpt is confirmed GSAK-origin (see is_gsak_source above), since in
    # that case <sym> is known not to encode found state at all. For any
    # other case where <sym> IS present, it's trusted as authoritative
    # either way — including for "not found". This matters because
    # OpenSAK's own export always writes a <sym> (see garmin.py) reflecting
    # the current found state, but never deletes old found-type Log rows
    # when a cache is manually unmarked as found (a deliberate choice — see
    # cache_table.py's Mark as Not Found handler, to avoid destroying real
    # imported log history). Without this guard, a cache that was unmarked
    # but still has a stale found-type log would be incorrectly resurrected
    # as found on any GPX round-trip.
    if not found_by_me and (sym_raw is None or is_gsak_source) and logs:
        from opensak.gui.settings import get_settings
        _sett = get_settings()
        _gc_username  = (_sett.gc_username  or "").strip().lower()
        _gc_finder_id = (_sett.gc_finder_id or "").strip()
        if _gc_finder_id or _gc_username:
            found_by_me = any(
                lg.get("log_type") in FOUND_LOG_TYPES
                and (
                    (_gc_finder_id and str(lg.get("finder_id", "")).strip() == _gc_finder_id)
                    or (_gc_username and (lg.get("finder") or "").strip().lower() == _gc_username)
                )
                for lg in logs
            )

    # ── Trackables ────────────────────────────────────────────────────────────
    trackables = []
    if gs_cache is not None:
        for tb_el in gs_cache.findall("gs:travelbugs/gs:travelbug", active_ns):
            trackables.append({
                "ref":  tb_el.get("ref"),
                "name": _text(tb_el, "gs:name", NS),
            })

    # ── GSAK extensions (issue #58, #129, #73) ────────────────────────────────
    # GSAK exports extra data inside <gsak:wptExtension>.
    #
    # GSAK supports two CC export formats depending on version:
    #
    # Format A — older GSAK versions:
    #   <gsak:LatN>   — corrected latitude  (decimal degrees)
    #   <gsak:LongE>  — corrected longitude (decimal degrees)
    #   The <wpt lat lon> attributes contain the ORIGINAL coordinates.
    #
    # Format B — newer GSAK versions (confirmed from RigaCC.gpx export):
    #   <gsak:LatBeforeCorrect>  — original latitude  (decimal degrees)
    #   <gsak:LonBeforeCorrect>  — original longitude (decimal degrees)
    #   The <wpt lat lon> attributes contain the CORRECTED coordinates.
    #   LatBeforeCorrect is only present when CC has been set in GSAK.
    #
    # We detect Format B first (LatBeforeCorrect present and differs from wpt),
    # then fall back to Format A (LatN/LongE).
    gsak_ftf: Optional[bool] = None
    gsak_corrected_lat: Optional[float] = None
    gsak_corrected_lon: Optional[float] = None
    gsak_original_lat: Optional[float] = None
    gsak_original_lon: Optional[float] = None

    # ── Issue #269: remaining GSAK "standard" waypoint extension fields ───────
    # These mirror GSAK's own per-cache user fields (UserFlag, UserSort,
    # UserData/User2-4, IsPremium, FavPoints). All are optional and only
    # present when the GPX was exported FROM GSAK itself (not from a raw
    # geocaching.com Pocket Query) — so None here simply means "not GSAK".
    gsak_user_flag: Optional[bool] = None
    gsak_is_premium: Optional[bool] = None
    gsak_user_sort: Optional[int] = None
    gsak_user_data_1: Optional[str] = None
    gsak_user_data_2: Optional[str] = None
    gsak_user_data_3: Optional[str] = None
    gsak_user_data_4: Optional[str] = None
    gsak_fav_points: Optional[int] = None
    gsak_user_note: Optional[str] = None

    # Issue #521: the official Groundspeak GPX schema has no <county> element
    # at all (only country/state) — GSAK adds its own <gsak:County> inside
    # wptExtension to fill that gap. Only present when the GPX was exported
    # FROM GSAK with "Include GSAK fields" checked.
    gsak_county: Optional[str] = None

    for gsak_uri in _GSAK_NAMESPACES:
        # Search descendants: gsak:wptExtension may be a direct <wpt> child (GSAK
        # format) or nested inside <extensions> (OpenSAK GPX 1.1 export).
        gsak_ext = wpt_el.find(f".//{{{gsak_uri}}}wptExtension")
        if gsak_ext is not None:
            ftf_el = gsak_ext.find(f"{{{gsak_uri}}}FirstToFind")
            if ftf_el is not None and ftf_el.text:
                gsak_ftf = ftf_el.text.strip().lower() == "true"

            flag_el = gsak_ext.find(f"{{{gsak_uri}}}UserFlag")
            if flag_el is not None and flag_el.text:
                gsak_user_flag = flag_el.text.strip().lower() == "true"

            premium_el = gsak_ext.find(f"{{{gsak_uri}}}IsPremium")
            if premium_el is not None and premium_el.text:
                gsak_is_premium = premium_el.text.strip().lower() == "true"

            sort_el = gsak_ext.find(f"{{{gsak_uri}}}UserSort")
            if sort_el is not None and sort_el.text:
                try:
                    parsed_user_sort = int(sort_el.text.strip())
                except ValueError:
                    pass
                else:
                    # Issue #830: GSAK does not allow a UserSort value of 0
                    # — a blank field in GSAK's UI is exported as "0" in
                    # this extension element, not omitted. Treat 0 as
                    # "no value" so it round-trips as blank in OpenSAK.
                    if parsed_user_sort != 0:
                        gsak_user_sort = parsed_user_sort

            ud1_el = gsak_ext.find(f"{{{gsak_uri}}}UserData")
            if ud1_el is not None and ud1_el.text and ud1_el.text.strip():
                gsak_user_data_1 = ud1_el.text.strip()

            ud2_el = gsak_ext.find(f"{{{gsak_uri}}}User2")
            if ud2_el is not None and ud2_el.text and ud2_el.text.strip():
                gsak_user_data_2 = ud2_el.text.strip()

            ud3_el = gsak_ext.find(f"{{{gsak_uri}}}User3")
            if ud3_el is not None and ud3_el.text and ud3_el.text.strip():
                gsak_user_data_3 = ud3_el.text.strip()

            ud4_el = gsak_ext.find(f"{{{gsak_uri}}}User4")
            if ud4_el is not None and ud4_el.text and ud4_el.text.strip():
                gsak_user_data_4 = ud4_el.text.strip()

            # FavPoints: only trustworthy when GSAK itself pulled it from the
            # Geocaching.com API — but if it's present in the GPX, GSAK has
            # already done that work for us, so we take it as-is.
            fav_el = gsak_ext.find(f"{{{gsak_uri}}}FavPoints")
            if fav_el is not None and fav_el.text:
                try:
                    gsak_fav_points = int(fav_el.text.strip())
                except ValueError:
                    pass

            note_el = gsak_ext.find(f"{{{gsak_uri}}}UserNote")
            if note_el is not None and note_el.text and note_el.text.strip():
                gsak_user_note = note_el.text.strip()

            county_el = gsak_ext.find(f"{{{gsak_uri}}}County")
            if county_el is not None and county_el.text and county_el.text.strip():
                gsak_county = county_el.text.strip()

            # Format B: LatBeforeCorrect/LonBeforeCorrect (newer GSAK)
            # The mere PRESENCE of LatBeforeCorrect means CC has been set in GSAK —
            # even when the corrected coords equal the original (e.g. user marks a
            # cache as "want to find" without changing the location).
            before_lat_el = gsak_ext.find(f"{{{gsak_uri}}}LatBeforeCorrect")
            before_lon_el = gsak_ext.find(f"{{{gsak_uri}}}LonBeforeCorrect")
            if before_lat_el is not None and before_lon_el is not None:
                try:
                    orig_lat = float(before_lat_el.text.strip())
                    orig_lon = float(before_lon_el.text.strip())
                    # wpt lat/lon are the corrected coordinates
                    gsak_corrected_lat = lat
                    gsak_corrected_lon = lon
                    # Only store originals if they actually differ from corrected
                    # (if same, no need to overwrite cache.latitude/longitude)
                    if abs(orig_lat - lat) > 1e-6 or abs(orig_lon - lon) > 1e-6:
                        gsak_original_lat = orig_lat
                        gsak_original_lon = orig_lon
                except (ValueError, AttributeError):
                    pass

            # Format A: LatN/LongE (older GSAK) — only if Format B didn't fire
            if gsak_corrected_lat is None:
                lat_el = gsak_ext.find(f"{{{gsak_uri}}}LatN")
                lon_el = gsak_ext.find(f"{{{gsak_uri}}}LongE")
                if lat_el is not None and lon_el is not None:
                    try:
                        parsed_lat = float(lat_el.text.strip())
                        parsed_lon = float(lon_el.text.strip())
                        # GSAK writes 0.0/0.0 when no corrected coords are set
                        if parsed_lat != 0.0 or parsed_lon != 0.0:
                            gsak_corrected_lat = parsed_lat
                            gsak_corrected_lon = parsed_lon
                    except (ValueError, AttributeError):
                        pass
            break

    return {
        "gc_code":           gc_code,
        "gc_cache_id":       gs_id,
        "name":              name,
        "cache_type":        cache_type,
        "container":         container,
        "latitude":          lat,
        "longitude":         lon,
        "difficulty":        difficulty,
        "terrain":           terrain,
        "placed_by":         placed_by,
        "owner_name":        owner,
        "owner_id":          owner_id,
        "hidden_date":       _parse_datetime(hidden_raw),
        "available":         available,
        "archived":          archived,
        "country":           country,
        "state":             state,
        "county":            county or gsak_county,
        "short_description": short_desc,
        "short_desc_html":   short_html,
        "long_description":  long_desc,
        "long_desc_html":    long_html,
        "encoded_hints":     hints,
        "attributes":        attributes,
        "logs":              logs,
        "trackables":        trackables,
        "gsak_ftf":          gsak_ftf,
        "gsak_corrected_lat": gsak_corrected_lat,
        "gsak_corrected_lon": gsak_corrected_lon,
        "gsak_original_lat": gsak_original_lat,
        "gsak_original_lon": gsak_original_lon,
        "gsak_user_flag":    gsak_user_flag,
        "gsak_is_premium":   gsak_is_premium,
        "gsak_user_sort":    gsak_user_sort,
        "gsak_user_data_1":  gsak_user_data_1,
        "gsak_user_data_2":  gsak_user_data_2,
        "gsak_user_data_3":  gsak_user_data_3,
        "gsak_user_data_4":  gsak_user_data_4,
        "gsak_fav_points":   gsak_fav_points,
        "gsak_user_note":    gsak_user_note,
        "found_by_me":       found_by_me,
    }


# ── Extra waypoint parser (GSAK single-file GPX) ─────────────────────────────

from opensak.utils.constants import KNOWN_PREFIXES as _KNOWN_PREFIXES
from opensak.utils.constants import KNOWN_SINGLE_PREFIXES as _KNOWN_SINGLE_PREFIXES
from opensak.utils.constants import FOUND_LOG_TYPES
from opensak.utils.constants import FTF_TAG_PATTERN


def _parse_extra_wpt(wpt_el) -> Optional[dict]:
    """Parse a non-GC <wpt> as an extra waypoint (parking, stage, etc.)."""
    try:
        lat = float(wpt_el.get("lat"))
        lon = float(wpt_el.get("lon"))
    except (TypeError, ValueError):
        return None

    gpx_ns = {"gpx": etree.QName(wpt_el).namespace or NS["gpx"]}

    raw_name = (
        _text(wpt_el, "gpx:name", gpx_ns) or
        _text(wpt_el, "gpx:n",    gpx_ns) or ""
    )
    if len(raw_name) < 2:
        return None

    # Forsøg 2-bogstavs prefix først, derefter 1-bogstavs med tal (P0, P1, 01, 02 osv.)
    if len(raw_name) >= 3 and raw_name[:2].upper() in _KNOWN_PREFIXES:
        # Kendt 2-bogstavs prefix: PK, FN, TH osv.
        prefix = raw_name[:2].upper()
        suffix = raw_name[2:]
        wp_type_fallback = _KNOWN_PREFIXES[prefix]
    elif raw_name[0].upper() in _KNOWN_SINGLE_PREFIXES and len(raw_name) >= 2:
        # Single bogstav + suffix: T27A2JF, V363R36 osv.
        # Men kun hvis andet tegn ikke er et tal (P0, P1 håndteres nedenfor)
        if not raw_name[1].isdigit():
            prefix = raw_name[0].upper()
            suffix = raw_name[1:]
            wp_type_fallback = _KNOWN_SINGLE_PREFIXES[prefix]
        else:
            # Bogstav + tal prefix: P0, P1, P2, T0, T1, R0, R1 osv.
            # Brug type-feltet til at bestemme wp_type
            prefix = raw_name[:2].upper()
            suffix = raw_name[2:]
            # Map første bogstav til type via single-prefix map
            wp_type_fallback = _KNOWN_SINGLE_PREFIXES.get(raw_name[0].upper(), "Waypoint")
    elif raw_name[:2].isdigit() and len(raw_name) >= 3:
        # Rent numerisk prefix: 01, 02, 03 osv. — brug type-feltet
        prefix = raw_name[:2]
        suffix = raw_name[2:]
        wp_type_fallback = "Waypoint"
    else:
        # Ukendt prefix-format — tjek om type-feltet angiver et gyldigt waypoint-type
        # Eksempler: 'JJ28J63' type='Waypoint|Final Location'
        # Suffix er altid de sidste 6 tegn (Groundspeak standard)
        type_raw_check = _text(wpt_el, "gpx:type", gpx_ns) or ""
        if "|" in type_raw_check and type_raw_check.startswith("Waypoint"):
            # Acceptér som waypoint med generisk prefix
            prefix = raw_name[:2].upper()
            suffix = raw_name[-6:] if len(raw_name) >= 6 else raw_name
            wp_type_fallback = type_raw_check.split("|")[-1].strip()
        else:
            return None

    type_raw = _text(wpt_el, "gpx:type", gpx_ns) or ""
    if "|" in type_raw:
        wp_type = type_raw.split("|")[-1].strip()
    elif type_raw:
        wp_type = type_raw
    else:
        wp_type = wp_type_fallback

    desc    = _text(wpt_el, "gpx:desc",    gpx_ns)
    comment = _text(wpt_el, "gpx:cmt",     gpx_ns)
    name    = _text(wpt_el, "gpx:urlname", gpx_ns) or desc or raw_name

    return {
        "prefix":      prefix,
        "suffix":      suffix,
        "wp_type":     wp_type,
        "name":        name,
        "description": desc,
        "comment":     comment,
        "latitude":    lat,
        "longitude":   lon,
    }


def _insert_extra_wpts(session: Session, extra_wpts: list, commit_every: int = 500) -> int:
    """Insert/update extra (inline) waypoints.

    Builds one suffix→cache_id lookup in RAM to avoid a LIKE query per waypoint,
    and deletes all stale waypoints for the affected caches in a single batched
    ``DELETE ... WHERE cache_id IN (...)`` rather than one fetch-sync DELETE per
    suffix. Commits to disk every *commit_every* caches to keep RAM flat.
    """
    # Build suffix→cache_id and suffix→gc_code once.
    suffix_to_cache_id: dict[str, int] = {}
    suffix_to_gc_code: dict[str, str] = {}
    for cid, gc_code in session.query(Cache.id, Cache.gc_code):
        if gc_code and len(gc_code) > 2:
            suffix_to_cache_id[gc_code[2:]] = cid
            suffix_to_gc_code[gc_code[2:]] = gc_code

    # Group waypoints per suffix.
    wpts_by_suffix: dict[str, list] = {}
    for wp in extra_wpts:
        wpts_by_suffix.setdefault(wp["suffix"], []).append(wp)

    # One batched delete of every affected cache's stale waypoints, chunked to
    # stay under SQLite's bound-parameter limit.
    target_ids = list({
        suffix_to_cache_id[s] for s in wpts_by_suffix if s in suffix_to_cache_id
    })
    for i in range(0, len(target_ids), 500):
        chunk = target_ids[i:i + 500]
        session.query(Waypoint).filter(
            Waypoint.cache_id.in_(chunk)
        ).delete(synchronize_session=False)
    session.flush()

    count = 0
    batch = 0
    for suffix, wps in wpts_by_suffix.items():
        cache_id = suffix_to_cache_id.get(suffix)
        if cache_id is None:
            continue

        parent_gc = suffix_to_gc_code.get(suffix)
        for wp in wps:
            session.add(Waypoint(
                cache_id=cache_id,
                parent_gc_code=parent_gc,
                prefix=wp["prefix"],
                wp_type=wp["wp_type"],
                name=wp["name"],
                description=wp["description"],
                comment=wp["comment"],
                latitude=wp["latitude"],
                longitude=wp["longitude"],
            ))
            count += 1

        batch += 1
        if batch % commit_every == 0:
            session.commit()
            session.expunge_all()

    session.commit()

    # Refresh waypoint_count for every affected cache in one SQL pass.
    if target_ids:
        for i in range(0, len(target_ids), 500):
            chunk = target_ids[i:i + 500]
            placeholders = ", ".join(str(cid) for cid in chunk)
            session.execute(_sa_text(f"""
                UPDATE caches
                SET waypoint_count = (
                    SELECT COUNT(*) FROM waypoints WHERE waypoints.cache_id = caches.id
                )
                WHERE id IN ({placeholders})
            """))
        session.commit()

    return count


# ── LOC parser ────────────────────────────────────────────────────────────────

def _parse_loc_waypoint(wpt_el) -> Optional[dict]:
    """
    Parse a single <waypoint> element from a .loc file.

    .loc files only contain GC code, name, and coordinates.
    All other fields are set to None/defaults.
    """
    name_el = wpt_el.find("name")
    if name_el is None:
        return None

    gc_code = name_el.get("id", "").strip()
    if not gc_code.startswith("GC"):
        return None

    # Cache display name is the CDATA text content of <name>
    cache_name = name_el.text.strip() if name_el.text else gc_code

    coord_el = wpt_el.find("coord")
    if coord_el is None:
        return None

    try:
        lat = float(coord_el.get("lat"))
        lon = float(coord_el.get("lon"))
    except (TypeError, ValueError):
        return None

    return {
        "gc_code":           gc_code,
        "gc_cache_id":       None,   # .loc files carry no Groundspeak data
        "name":              cache_name,
        "cache_type":        "Traditional Cache",   # .loc has no type info
        "container":         None,
        "latitude":          lat,
        "longitude":         lon,
        "difficulty":        None,
        "terrain":           None,
        "placed_by":         None,
        "owner_name":        None,
        "owner_id":          None,
        "hidden_date":       None,
        "available":         True,
        "archived":          False,
        "country":           None,
        "state":             None,
        "short_description": None,
        "short_desc_html":   False,
        "long_description":  None,
        "long_desc_html":    False,
        "encoded_hints":     None,
        "attributes":        [],
        "logs":              [],
        "trackables":        [],
    }


# ── DB upsert ─────────────────────────────────────────────────────────────────

def _load_existing_gc_map(session: Session) -> dict[str, int]:
    """Return ``{gc_code: cache.id}`` for every cache already in the database.

    Loaded once at the start of an import so :func:`_upsert_cache` can tell new
    from existing caches without a ``SELECT`` per row. Only two indexed columns
    are fetched, so this is a single light scan even on a large database.
    """
    return {gc: cid for gc, cid in session.query(Cache.gc_code, Cache.id)}


from sqlalchemy import text as _sa_text


def _enter_bulk_import_pragmas(session: Session) -> None:
    """Relax SQLite durability on the import connection for speed.

    With WAL enabled the database defaults to ``synchronous=FULL``, which fsyncs
    on every batch commit — the dominant cost of a large import. ``NORMAL`` under
    WAL is crash-safe (it can only lose the last transaction on an OS/power loss,
    never corrupt the file) and removes those fsyncs. A larger page cache cuts
    B-tree page churn while thousands of rows are inserted.

    These are connection-level settings and the connection is pooled and reused,
    so :func:`_exit_bulk_import_pragmas` MUST restore them once the import ends.
    """
    session.execute(_sa_text("PRAGMA synchronous=NORMAL"))
    session.execute(_sa_text("PRAGMA cache_size=-65536"))  # ~64 MB page cache
    session.commit()


def _exit_bulk_import_pragmas(session: Session) -> None:
    """Restore full durability on the import connection (see above).

    Called from the importer's ``finally`` block, so the session may have just
    committed or been rolled back. We roll back first to guarantee a clean state,
    then re-apply the defaults; any failure here is swallowed so it never masks a
    real import error.
    """
    try:
        session.rollback()
        session.execute(_sa_text("PRAGMA synchronous=FULL"))
        session.execute(_sa_text("PRAGMA cache_size=-2000"))  # SQLite default
        session.commit()
    except Exception:
        pass


def _upsert_cache(
    session: Session,
    data: dict,
    source_file: str,
    existing_ids: dict[str, int] | None = None,
) -> tuple[Cache, bool]:
    """
    Insert or update a Cache row from parsed GPX data.
    Returns (cache_object, created: bool).

    *existing_ids* is an optional ``{gc_code: cache.id}`` map preloaded once per
    import (see :func:`_load_existing_gc_map`). When supplied, a brand-new cache
    is detected by a dict miss — no per-cache ``SELECT`` is issued, which is the
    dominant cost on a fresh import of a large Pocket Query (every lookup would
    otherwise return ``None``). Existing caches are loaded by primary key. When
    omitted the function falls back to the original per-cache lookup.
    """
    gc_code = data["gc_code"]

    if existing_ids is not None:
        cid = existing_ids.get(gc_code)
        existing = session.get(Cache, cid) if cid is not None else None
    else:
        existing = session.query(Cache).filter_by(gc_code=gc_code).first()
    created = existing is None

    if existing is None:
        cache = Cache(gc_code=gc_code)
        session.add(cache)
    else:
        cache = existing
        # Clear old child records so they are rebuilt fresh.
        # synchronize_session=False skips the SELECT-then-evaluate the ORM would
        # otherwise run to keep in-memory objects in sync — we never load these
        # collections during import, so there is nothing in the identity map to
        # synchronise. The DELETEs still execute immediately. flush() afterwards
        # forces them to disk before the new rows are added in the same batch,
        # otherwise the UNIQUE constraint on logs.log_id would fire.
        #
        # Issue #618: Logs are deliberately NOT deleted here any more. Unlike
        # attributes/trackables/waypoints (which a GPX always describes in
        # full), a single GPX/PQ typically only carries a cache's most recent
        # handful of logs — deleting-then-reinserting on every import meant
        # older logs not present in that particular file were silently lost.
        # Logs are now merged further down: matching log_id's are updated in
        # place, new ones are added, and anything already in the database
        # that isn't in this file is left untouched.
        session.query(Attribute).filter_by(cache_id=cache.id).delete(synchronize_session=False)
        session.query(Trackable).filter_by(cache_id=cache.id).delete(synchronize_session=False)
        session.query(Waypoint).filter_by(cache_id=cache.id).delete(synchronize_session=False)
        cache.waypoint_count = 0
        session.flush()

    # Scalar fields
    # ── Issue #202: Lock a cache ──────────────────────────────────────────
    # A locked cache keeps its current scalar values exactly as they were
    # the last time it was unlocked/imported — re-imports (PQ/GPX) no longer
    # touch name, type, container, coordinates, D/T, owner, status,
    # descriptions, hint or country/state/county. New caches can't be locked
    # yet (the checkbox lives on existing caches only), so this only applies
    # when updating an existing row.
    if existing is None or not cache.locked:
        for field in (
            "name", "cache_type", "container", "latitude", "longitude",
            "difficulty", "terrain", "placed_by", "owner_name", "owner_id",
            "hidden_date", "available", "archived",
            "country", "state", "county",
            "short_description", "short_desc_html",
            "long_description",  "long_desc_html",
            "encoded_hints",
        ):
            setattr(cache, field, data.get(field))

    # Issue #695: the real Geocaching.com numeric cache ID (used e.g. in
    # GPX/GGZ export). Historically parsed from the GPX but never stored,
    # so every OpenSAK GPX export wrote the internal DB row id instead of
    # the real ID. Not user-editable data, so backfilled regardless of the
    # locked flag — but only when the source actually provided one, so we
    # never clobber an existing value (e.g. from an earlier GSAK import,
    # which already populates this same field) with an empty one from a
    # source that doesn't carry it (.loc files, some GPX variants).
    if data.get("gc_cache_id"):
        cache.gc_cache_id = data["gc_cache_id"]

    cache.source_file = source_file

    # Attributes
    for a in data.get("attributes", []):
        session.add(Attribute(
            cache=cache,
            attribute_id=a["attribute_id"],
            name=a["name"],
            is_on=a["is_on"],
        ))

    # Logs
    # GSAK bruger dummy log_id '-2' for autogenererede noter (Certitude m.fl.).
    # Disse er ikke unikke på tværs af caches, så vi genererer et unikt ID
    # baseret på cache GC-kode + log indeks når log_id er en kendt dummy-værdi.
    # GSAK bruger negative tal som dummy log IDs (-2, -3 osv.) samt "0" og tom streng.
    # Alle negative log IDs og kendte dummy-værdier får genereret et unikt ID.
    # Nogle GPX filer fra geocaching.com indeholder duplikate logs med samme id —
    # vi springer dubletter over så UNIQUE constraint ikke fyrer.
    #
    # Issue #618: logs accumulate across imports instead of being wiped and
    # rebuilt each time. existing_logs_by_id holds every Log row already in
    # the database for this cache (empty for a brand-new cache); a log_id
    # match updates that row in place, anything new is added, and rows for
    # log_id's simply absent from *this* file are left exactly as they were.
    existing_logs_by_id: dict[Optional[str], Log] = {}
    if existing is not None:
        existing_logs_by_id = {
            lg.log_id: lg
            for lg in session.query(Log).filter_by(cache_id=cache.id).all()
        }

    DUMMY_LOG_IDS = {"0", None, ""}
    seen_log_ids_this_file: set[str] = set()
    for idx, lg in enumerate(data.get("logs", [])):
        raw_id = lg["log_id"]
        is_dummy = raw_id in DUMMY_LOG_IDS
        if not is_dummy and raw_id is not None:
            try:
                is_dummy = int(raw_id) < 0
            except (ValueError, TypeError):
                pass
        if is_dummy:
            log_id = f"gen_{data['gc_code']}_{idx}"
        else:
            log_id = raw_id
        if log_id in seen_log_ids_this_file:
            continue
        seen_log_ids_this_file.add(log_id)

        existing_log = existing_logs_by_id.get(log_id)
        if existing_log is not None:
            existing_log.log_type = lg["log_type"]
            existing_log.log_date = lg["log_date"]
            existing_log.finder = lg["finder"]
            existing_log.finder_id = lg["finder_id"]
            existing_log.text = lg["text"]
            existing_log.text_encoded = lg["text_encoded"]
        else:
            new_log = Log(
                cache=cache,
                log_id=log_id,
                log_type=lg["log_type"],
                log_date=lg["log_date"],
                finder=lg["finder"],
                finder_id=lg["finder_id"],
                text=lg["text"],
                text_encoded=lg["text_encoded"],
            )
            session.add(new_log)
            existing_logs_by_id[log_id] = new_log

    # ── Issue #87: Cache log count for fast UI display ──────────────────────
    # existing_logs_by_id now holds the full merged set of logs for this
    # cache (untouched + updated + newly added), so its length is the total.
    cache.log_count = len(existing_logs_by_id)

    # ── Issue #186: Cache latest log date for fast UI display ────────────────
    # Issue #618: computed from the merged set, not just this file's logs —
    # otherwise re-importing a file with older logs than what's already
    # stored could move last_log_date backwards.
    # Issue #618: existing_logs_by_id mixes freshly-parsed dates (always
    # UTC-aware, from _parse_datetime) with dates read back from rows already
    # in the database (SQLite/SQLAlchemy returns those naive, since SQLite
    # itself doesn't store tzinfo). Comparing naive and aware datetimes
    # directly raises TypeError, so normalize to aware-UTC before max().
    def _as_aware_utc(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    log_dates = [_as_aware_utc(lg.log_date) for lg in existing_logs_by_id.values() if lg.log_date]
    cache.last_log_date = max(log_dates) if log_dates else None

    # ── Issue #716: last_found_date (most recent found-type log by ANY
    # finder, unlike found_date which is the current user's own log) ───────
    found_log_dates = [
        _as_aware_utc(lg.log_date)
        for lg in existing_logs_by_id.values()
        if lg.log_type in FOUND_LOG_TYPES and lg.log_date
    ]
    cache.last_found_date = max(found_log_dates) if found_log_dates else None

    # ── Issue #716: last_four_logs (cached summary — logs relationship is
    # not loaded in the grid, same reasoning as last_log_date above) ──────
    _recent = sorted(
        (lg for lg in existing_logs_by_id.values() if lg.log_date),
        key=lambda lg: _as_aware_utc(lg.log_date), reverse=True,
    )[:4]
    cache.last_four_logs = "\n".join(
        f"{_as_aware_utc(lg.log_date).strftime('%Y-%m-%dT%H:%M:%S')}\t{lg.log_type}\t{lg.finder or ''}"
        for lg in _recent
    ) or None

    # ── Issue #716: last_gpx_update — local timestamp of this import pass,
    # set unconditionally (even for locked caches — it reflects import
    # activity, not listing content, same as source_file above) ───────────
    cache.last_gpx_update = datetime.now(timezone.utc)

    # Trackables
    for tb in data.get("trackables", []):
        session.add(Trackable(cache=cache, ref=tb["ref"], name=tb["name"]))

    # ── Issue #489/#491: Cache trackable count for fast UI display ──────────
    # Mirrors log_count above: old trackables were deleted at the start of
    # this function (re-import), so the length of the list we just added
    # from the GPX equals the new total trackable count for this cache.
    cache.trackable_count = len(data.get("trackables", []))

    # ── Found by me (sym=Geocache Found) + found_date fra brugerens log ────────
    # Vi sætter kun found=True hvis GPX'en eksplicit markerer cachen som fundet
    # (sym="Geocache Found"). found=False sætter vi IKKE ved re-import — det ville
    # overskrive manuelle markeringer. Undtagelse: hvis found_by_me er False og
    # cachen ikke er fundet, lader vi den eksisterende found-værdi stå uændret.
    #
    # found_date hentes fra brugerens egen log-entry:
    #   1. Søg efter log med finder_id der matcher gc_finder_id i Settings.
    #   2. Fallback: søg på gc_username (case-insensitive).
    #   3. Hvis ingen match og cachen er found: brug ældste dato blandt
    #      FOUND_LOG_TYPES (se opensak.utils.constants).
    #
    # Bug #457: trin 1-3 kiggede tidligere kun på log_type == "Found it", så
    # webcam-caches ("Webcam Photo Taken") og events ("Attended") endte uden
    # found_date selvom de var markeret som fundet (sym="Geocache Found").
    # Nu matches mod FOUND_LOG_TYPES i stedet for en enkelt hardkodet streng.
    #
    # Auto-lær finder_id: første gang vi ser en log der matcher gc_username
    # gemmer vi det numeriske finder_id i Settings — så næste import er hurtigere.

    found_by_me = data.get("found_by_me", False)
    logs_data = data.get("logs", [])

    if found_by_me:
        cache.found = True
        from opensak.gui.settings import get_settings
        from opensak.utils.utils import count_own_found_logs
        _sett = get_settings()
        gc_username  = (_sett.gc_username  or "").strip().lower()
        gc_finder_id = (_sett.gc_finder_id or "").strip()

        # Issue #552: count of the user's own found-type logs on this cache
        # (not just whether it's been found at least once) — see
        # count_own_found_logs() for the matching rules. Only updated
        # when found_by_me is True, mirroring the found=True-only-not-False
        # re-import rule above so a PQ re-import that doesn't include this
        # cache's found logs can't silently reset a known count to 0.
        cache.found_log_count = count_own_found_logs(logs_data, gc_finder_id, gc_username)

        found_log = None

        # Trin 1: match på numerisk finder_id (hurtigst + mest præcist)
        if gc_finder_id:
            for lg in logs_data:
                if (lg.get("log_type") in FOUND_LOG_TYPES
                        and str(lg.get("finder_id", "")).strip() == gc_finder_id):
                    found_log = lg
                    break

        # Trin 2: match på brugernavn (case-insensitive)
        if found_log is None and gc_username:
            for lg in logs_data:
                if (lg.get("log_type") in FOUND_LOG_TYPES
                        and (lg.get("finder") or "").strip().lower() == gc_username):
                    found_log = lg
                    # Auto-lær finder_id fra denne log
                    detected_id = str(lg.get("finder_id", "")).strip()
                    if detected_id and not gc_finder_id:
                        _sett.gc_finder_id = detected_id
                    break

        # Trin 3: fallback — ældste "found"-log, men KUN når hverken
        # gc_username eller gc_finder_id er sat i Settings overhovedet. Hvis
        # et brugernavn ER sat, men bare ikke matcher nogen log i denne fil
        # (fx en ægte GSAK-eksport af en manuelt MAF-markeret cache, hvor
        # brugerens egen "Found it"-log slet ikke er med i filen — se issue
        # #766, Allyn56's rigtige GSAK-eksport), ville denne fallback ellers
        # fejlagtigt tilskrive en HELT ANDEN persons fund-dato som var det
        # brugerens egen. I det tilfælde er det korrekte at lade found_date
        # stå urørt (bevarer evt. eksisterende værdi ved re-import) frem for
        # at gætte forkert.
        if found_log is None and not gc_finder_id and not gc_username:
            found_logs = [lg for lg in logs_data
                          if lg.get("log_type") in FOUND_LOG_TYPES and lg.get("log_date")]
            if found_logs:
                found_log = min(found_logs, key=lambda lg: lg["log_date"])

        if found_log and found_log.get("log_date"):
            cache.found_date = found_log["log_date"]

    # ── Derive dnf_date, first_to_find from logs (issue #33, #58, #114) ────────
    # These are only set/updated when a fresh import brings logs in.
    # We only touch them if we actually have log data to derive from.
    if logs_data:
        # dnf_date: date of the most recent "Didn't find it" log by any finder
        # (GSAK stores the last DNF date regardless of who logged it).
        # Issue #618: derived from the merged log set (existing_logs_by_id),
        # not just this file's logs_data — otherwise a re-import that simply
        # doesn't happen to include a DNF log would reset dnf_date to None
        # even though the DNF log itself is still sitting in the database.
        dnf_dates = [
            _as_aware_utc(lg.log_date)
            for lg in existing_logs_by_id.values()
            if lg.log_type == "Didn't find it" and lg.log_date
        ]
        cache.dnf_date = max(dnf_dates) if dnf_dates else None

        # ── FTF detection (fixes #114, implements #58) ────────────────────────
        # Priority order:
        #   1. GSAK GPX flag (<gsak:FirstToFind>) — explicit user-set flag
        #   2. Log-based detection — only for the CURRENT USER's own logs
        #
        # Issue #114 fix: The old code checked ALL logs for FTF keywords,
        # which caused false positives when OTHER finders wrote things like
        # "Congrats on FTF!" in their logs.  Now we only check the current
        # user's own "Found it" logs, and only if the user has actually
        # found the cache.

        gsak_ftf = data.get("gsak_ftf")

        if gsak_ftf is not None:
            # GSAK flag is authoritative — user set it manually in GSAK
            cache.first_to_find = gsak_ftf
        elif cache.found:
            # Log-baseret FTF-detektion: kun brugerens egne log-tekster tjekkes.
            #
            # VIGTIGT: "rækkefølge-baseret" detektion (er brugerens log ældst?)
            # er IKKE pålidelig fra en PQ — PQ viser kun de 5 NYESTE logs,
            # ikke alle logs. En gammel found-log fra brugeren vil tit være
            # den ældste af de 5 viste, selvom hundredvis fandt den først.
            # Kun tag-match i brugerens egen log-tekst er sikker.
            #
            # Bug: log fandtes tidligere ved fritekst-match på "ftf",
            # "first to find", "first finder" m.fl. — det gav falske
            # positiver når en log blot NÆVNTE first-to-find-konceptet uden
            # at gøre krav på det, fx "...forgæves forsøg på at blive first
            # finder..." (en log om IKKE at få FTF). ProjectGC — som de
            # fleste geocachere bruger til FTF-statistik — kræver et af de
            # eksplicitte tags {FTF}, {*FTF*} eller [FTF] i loggen, så vi
            # matcher nu udelukkende mod FTF_TAG_PATTERN i stedet.
            from opensak.gui.settings import get_settings
            gc_username  = get_settings().gc_username.strip().lower()
            gc_finder_id = get_settings().gc_finder_id.strip()

            if gc_username or gc_finder_id:
                user_found_logs = [
                    lg for lg in logs_data
                    if lg.get("log_type") == "Found it"
                    and (
                        (gc_finder_id and str(lg.get("finder_id", "")).strip() == gc_finder_id)
                        or
                        (gc_username and (lg.get("finder") or "").strip().lower() == gc_username)
                    )
                ]
                cache.first_to_find = any(
                    FTF_TAG_PATTERN.search(lg.get("text") or "")
                    for lg in user_found_logs
                )
            else:
                # Intet brugernavn konfigureret — kan ikke detektere FTF sikkert
                cache.first_to_find = False
        else:
            # Brugeren har IKKE fundet denne cache — FTF ikke relevant
            cache.first_to_find = False

    # ── GSAK personal/user fields (issue #269) ────────────────────────────────
    # UserFlag, IsPremium, UserSort, UserData1-4 and FavPoints are only ever
    # present when the GPX came FROM GSAK (not from a raw Geocaching.com
    # Pocket Query). We only touch a field when GSAK actually supplied a
    # value, so a plain PQ re-import never wipes data the user entered
    # manually in OpenSAK or in a previous GSAK-sourced import.
    if data.get("gsak_user_flag") is not None:
        cache.user_flag = data["gsak_user_flag"]
    if data.get("gsak_is_premium") is not None:
        cache.premium_only = data["gsak_is_premium"]
    if data.get("gsak_user_sort") is not None:
        cache.user_sort = data["gsak_user_sort"]
    if data.get("gsak_user_data_1") is not None:
        cache.user_data_1 = data["gsak_user_data_1"]
    if data.get("gsak_user_data_2") is not None:
        cache.user_data_2 = data["gsak_user_data_2"]
    if data.get("gsak_user_data_3") is not None:
        cache.user_data_3 = data["gsak_user_data_3"]
    if data.get("gsak_user_data_4") is not None:
        cache.user_data_4 = data["gsak_user_data_4"]
    if data.get("gsak_fav_points") is not None:
        cache.favorite_points = data["gsak_fav_points"]

    # ── GSAK personal note (issue #389) ──────────────────────────────────────
    # Write only when GSAK supplied a non-empty note, and never overwrite an
    # existing non-empty note so that manually entered notes survive re-import.
    gsak_note_text = data.get("gsak_user_note")
    if gsak_note_text:
        if cache.user_note is None:
            session.flush()
            note = UserNote(cache_id=cache.id)
            session.add(note)
            session.flush()
            cache.user_note = note
        if not cache.user_note.note:
            cache.user_note.note = gsak_note_text

    # ── GSAK corrected coordinates (issue #129, #73) ──────────────────────────
    # Corrected coords are stored in UserNote (user data), not on Cache itself.
    # Only write if GSAK actually exported them (non-zero values).
    # We never overwrite existing corrected coords with None so that manually
    # entered corrections survive a re-import.
    #
    # Format B (newer GSAK): wpt lat/lon were the corrected coords and
    # gsak_original_lat/lon hold the true cache location. In this case we
    # must restore the Cache's lat/lon to the original values so the cache
    # appears at the correct map position before the puzzle is solved.
    gsak_clat = data.get("gsak_corrected_lat")
    gsak_clon = data.get("gsak_corrected_lon")
    gsak_orig_lat = data.get("gsak_original_lat")
    gsak_orig_lon = data.get("gsak_original_lon")
    if gsak_clat is not None and gsak_clon is not None:
        # Format B: restore original coordinates on the Cache row
        if gsak_orig_lat is not None and gsak_orig_lon is not None:
            cache.latitude  = gsak_orig_lat
            cache.longitude = gsak_orig_lon
        if cache.user_note is None:
            # Flush so cache gets a PK before we reference it in UserNote
            session.flush()
            note = UserNote(cache_id=cache.id)
            session.add(note)
            session.flush()
            cache.user_note = note
        cache.user_note.corrected_lat = gsak_clat
        cache.user_note.corrected_lon = gsak_clon
        cache.user_note.is_corrected  = True

    return cache, created


def _flush_cache_batch(
    session: Session,
    batch: list[dict],
    source: str,
    existing_ids: dict[str, int],
    result: "ImportResult",
) -> None:
    """Persist a batch of parsed cache dicts, updating *result* counts in place.

    Fast path: the whole batch runs under a single ``SAVEPOINT``. A per-cache
    ``begin_nested()`` is the single most expensive part of a large import (the
    SAVEPOINT/RELEASE round-trip plus SQLAlchemy's per-savepoint state machine),
    so collapsing it to one savepoint per batch roughly halves import time.

    Correctness is preserved by a fallback: if anything in the batch fails (a
    malformed cache, a UNIQUE collision, an in-file duplicate gc_code), the batch
    savepoint is rolled back and the batch is replayed cache-by-cache under
    per-cache savepoints, so only the offending cache is skipped — exactly the
    isolation the old per-cache loop gave, but paid for only when it is needed.
    """
    if not batch:
        return

    sp = session.begin_nested()
    try:
        # Defer result/existing_ids mutations until the savepoint commits, so a
        # mid-batch failure leaves both untouched before the replay.
        pending: list[tuple[Cache, bool]] = []
        for data in batch:
            cache, created = _upsert_cache(session, data, source, existing_ids)
            pending.append((cache, created))
        sp.commit()  # RELEASE — flushes, assigning new primary keys
    except Exception:
        sp.rollback()
        _flush_cache_batch_isolated(session, batch, source, existing_ids, result)
        return

    for cache, created in pending:
        if created:
            result.created += 1
            existing_ids[cache.gc_code] = cache.id
        else:
            result.updated += 1


def _flush_cache_batch_isolated(
    session: Session,
    batch: list[dict],
    source: str,
    existing_ids: dict[str, int],
    result: "ImportResult",
) -> None:
    """Replay a batch one cache at a time so a single bad cache only skips itself."""
    for data in batch:
        cell = session.begin_nested()
        try:
            cache, created = _upsert_cache(session, data, source, existing_ids)
            cell.commit()
            if created:
                result.created += 1
                existing_ids[cache.gc_code] = cache.id
            else:
                result.updated += 1
        except Exception as e:
            cell.rollback()
            result.errors.append(f"DB error for {data.get('gc_code', '?')} in {source}: {e}")
            result.skipped += 1


def _link_extra_waypoints(
    session: Session,
    extra: dict[str, list[dict]],
) -> int:
    """
    Match parsed companion (-wpts.gpx) waypoints to caches already in the DB and
    insert them. The link is "gc_code ends with suffix" (e.g. waypoint 'PK2345'
    → cache 'GC12345'). Returns number of waypoints inserted.

    Instead of a ``LIKE '%suffix'`` full-table scan per suffix, the caches are
    loaded once and indexed by their trailing characters per suffix-length, so
    each suffix resolves in O(1). Stale waypoints for the matched caches are
    cleared in a single batched DELETE.
    """
    if not extra:
        return 0

    # Build an ends-with index: for each suffix length present, map the cache's
    # trailing chars of that length → (cache_id, gc_code).
    lengths = {len(s) for s in extra if s}
    tail_index: dict[int, dict[str, tuple[int, str]]] = {n: {} for n in lengths}
    for cid, gc in session.query(Cache.id, Cache.gc_code):
        if not gc:
            continue
        for n in lengths:
            if len(gc) >= n:
                tail_index[n].setdefault(gc[-n:], (cid, gc))

    # Resolve each suffix to a (cache_id, gc_code) pair once.
    resolved: dict[str, int] = {}
    resolved_gc: dict[str, str] = {}
    for suffix in extra:
        pair = tail_index.get(len(suffix), {}).get(suffix)
        if pair is not None:
            resolved[suffix] = pair[0]
            resolved_gc[suffix] = pair[1]

    if not resolved:
        return 0

    # Batched delete of stale waypoints for every matched cache.
    target_ids = list(set(resolved.values()))
    for i in range(0, len(target_ids), 500):
        chunk = target_ids[i:i + 500]
        session.query(Waypoint).filter(
            Waypoint.cache_id.in_(chunk)
        ).delete(synchronize_session=False)
    session.flush()

    count = 0
    for suffix, wpts in extra.items():
        cache_id = resolved.get(suffix)
        if cache_id is None:
            continue
        parent_gc = resolved_gc.get(suffix)
        for wp in wpts:
            session.add(Waypoint(
                cache_id=cache_id,
                parent_gc_code=parent_gc,
                prefix=wp["prefix"],
                wp_type=wp["wp_type"],
                name=wp["name"],
                description=wp["description"],
                comment=wp["comment"],
                latitude=wp["latitude"],
                longitude=wp["longitude"],
            ))
            count += 1

    # Refresh waypoint_count for every matched cache in one SQL pass.
    # flush() is required because autoflush=False; the subquery must see the
    # newly added Waypoint rows before the UPDATE executes.
    if target_ids:
        session.flush()
        for i in range(0, len(target_ids), 500):
            chunk = target_ids[i:i + 500]
            placeholders = ", ".join(str(cid) for cid in chunk)
            session.execute(_sa_text(f"""
                UPDATE caches
                SET waypoint_count = (
                    SELECT COUNT(*) FROM waypoints WHERE waypoints.cache_id = caches.id
                )
                WHERE id IN ({placeholders})
            """))
        session.commit()

    return count


# ── Public API ────────────────────────────────────────────────────────────────

class ImportResult:
    """Summary of a GPX/LOC import operation."""

    def __init__(self):
        self.created:   int = 0
        self.updated:   int = 0
        self.skipped:   int = 0
        self.waypoints: int = 0
        self.errors:    list[str] = []
        self.warnings:  list[str] = []

    @property
    def total(self) -> int:
        return self.created + self.updated

    def __str__(self) -> str:
        lines = [
            f"  Caches created : {self.created}",
            f"  Caches updated : {self.updated}",
            f"  Waypoints added: {self.waypoints}",
            f"  Skipped        : {self.skipped}",
        ]
        if self.warnings:
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        if self.errors:
            lines.append(f"  Errors         : {len(self.errors)}")
            for e in self.errors[:5]:
                lines.append(f"    - {e}")
        return "\n".join(lines)


def _parse_gpx_to_data(
    gpx_path: Path,
    wpts_path: Optional[Path] = None,
) -> tuple[list[dict], list[dict], dict | None, list[str]]:
    """
    Parse a GPX file into raw data structures without touching the database.

    Returns (caches, extra_wpts, companion_wpts_data, errors).
    Used by import_zip to separate CPU-bound parsing from IO-bound DB writes.
    """
    caches: list[dict] = []
    extra_wpts: list[dict] = []
    errors: list[str] = []

    try:
        context = _iterparse(gpx_path, events=("end",), tag=None)

        for event, elem in context:
            local_tag = etree.QName(elem).localname

            if local_tag == "wpt":
                try:
                    data = _parse_wpt(elem)
                    if data is not None:
                        caches.append(data)
                    else:
                        extra = _parse_extra_wpt(elem)
                        if extra is not None:
                            extra_wpts.append(extra)
                except Exception as e:
                    errors.append(f"Parse error in {gpx_path.name}: {e}")
                finally:
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
    except Exception as e:
        errors.append(f"Fatal parse error in {gpx_path.name}: {e}")

    # Parse companion waypoints file
    companion_data = None
    if wpts_path and wpts_path.exists():
        try:
            wpts_tree = _parse_xml(wpts_path)
            companion_data = _parse_extra_waypoints(wpts_tree)
        except Exception as e:
            errors.append(f"Waypoints file error: {e}")

    return caches, extra_wpts, companion_data, errors


def _count_wpts(gpx_path: Path) -> int:
    count = 0
    for _, elem in _iterparse(gpx_path, events=("end",)):
        if etree.QName(elem).localname == "wpt":
            count += 1
        elem.clear()
    return count


def import_gpx(
    gpx_path: Path,
    session: Session | None = None,
    wpts_path: Optional[Path] = None,
    progress_cb=None,
    batch_size: int = 200
) -> ImportResult:
    """
    Import a single GPX file into the database using streaming for high performance.

    Uses etree.iterparse to handle files of any size without RAM exhaustion.
    The session parameter is kept for compatibility but a new session is managed internally.
    """
    from opensak.db.database import make_session

    result = ImportResult()
    source = gpx_path.name
    db_session = make_session()

    extra_wpts: list = []
    processed_count = 0

    # Preload existing gc_codes once so new caches skip the per-cache SELECT.
    existing_ids = _load_existing_gc_map(db_session)
    _enter_bulk_import_pragmas(db_session)

    try:
        # Stream the XML using iterparse to avoid loading the entire file into RAM
        context = _iterparse(gpx_path, events=("end",), tag=None)

        # Parsed cache dicts are buffered and written one batch per SAVEPOINT
        # (see _flush_cache_batch). Each dict holds plain Python strings, so the
        # streaming element-clearing below still keeps RAM flat — only up to
        # batch_size dicts are held at a time.
        batch: list[dict] = []

        for event, elem in context:
            # Handle tags regardless of namespace version
            local_tag = etree.QName(elem).localname

            if local_tag == "wpt":
                try:
                    data = _parse_wpt(elem)

                    if data is not None:
                        batch.append(data)
                        processed_count += 1
                        if progress_cb:
                            progress_cb(processed_count)
                    else:
                        # Attempt to parse as an additional waypoint
                        extra = _parse_extra_wpt(elem)
                        if extra is not None:
                            extra_wpts.append(extra)
                        else:
                            result.skipped += 1
                except Exception as e:
                    result.errors.append(f"Error in {source}: {e}")
                    result.skipped += 1
                finally:
                    # CRITICAL: Clear the element and its preceding siblings from memory
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]

                # Flush a full batch to disk and clear the session to save RAM.
                if len(batch) >= batch_size:
                    _flush_cache_batch(db_session, batch, source, existing_ids, result)
                    batch.clear()
                    db_session.commit()
                    db_session.expunge_all()

        # Flush the trailing partial batch and commit the remainder.
        _flush_cache_batch(db_session, batch, source, existing_ids, result)
        batch.clear()
        db_session.commit()

        # Handle Extra Waypoints (Deduplication and Linking)
        if extra_wpts:
            seen = set()
            unique_wpts = []
            for wp in extra_wpts:
                key = (wp.get("suffix"), wp.get("prefix"), wp.get("name"))
                if key not in seen:
                    seen.add(key)
                    unique_wpts.append(wp)
            
            result.waypoints += _insert_extra_wpts(db_session, unique_wpts)
            db_session.commit()

        # Process companion waypoint files (_wpts.gpx) if present
        if wpts_path and wpts_path.exists():
            try:
                wpts_tree = _parse_xml(wpts_path)
                extra_data = _parse_extra_waypoints(wpts_tree)
                result.waypoints += _link_extra_waypoints(db_session, extra_data)
                db_session.commit()
            except Exception as e:
                result.errors.append(f"Waypoints file error: {e}")

    except Exception as e:
        db_session.rollback()
        result.errors.append(f"Fatal error importing {source}: {e}")
    finally:
        _exit_bulk_import_pragmas(db_session)
        db_session.close()

    return result

def import_zip(zip_path: Path, session: Session | None = None, progress_cb=None) -> ImportResult:
    """
    High-performance parallel ZIP import.

    Strategy:
    1. Parse all GPX files in parallel threads (CPU-bound, no DB access).
    2. Write all parsed data to the database sequentially (IO-bound, single session).
    """
    from concurrent.futures import ThreadPoolExecutor
    from opensak.db.database import make_session
    import zipfile

    overall_result = ImportResult()

    if not zipfile.is_zipfile(zip_path):
        overall_result.errors.append(f"{zip_path.name} is not a valid zip file")
        return overall_result

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        # Classify GPX files by content, not by name.
        all_gpx = sorted(tmp.glob("*.gpx"))
        companions = [f for f in all_gpx if _is_companion_gpx(f)]
        gpx_files  = [f for f in all_gpx if f not in set(companions)]

        if not gpx_files:
            overall_result.errors.append("No .gpx file found in zip")
            return overall_result

        # Step 1: Parse all main GPX files in parallel (pure CPU, no DB).
        # Companions are processed in step 3 after all caches are written so
        # _link_extra_waypoints can match them against the full cache table.
        parsed_files = []
        with ThreadPoolExecutor(max_workers=min(8, len(gpx_files))) as executor:
            futures = {
                executor.submit(_parse_gpx_to_data, gpx_path, None): gpx_path
                for gpx_path in gpx_files
            }
            for future in futures:
                try:
                    caches, extra_wpts, _, errors = future.result()
                    parsed_files.append((futures[future], caches, extra_wpts))
                    overall_result.errors.extend(errors)
                except Exception as e:
                    overall_result.errors.append(f"Parse error: {str(e)}")

        # Step 2: Write all parsed cache data sequentially (single session).
        db_session = make_session()
        existing_ids = _load_existing_gc_map(db_session)
        _enter_bulk_import_pragmas(db_session)
        try:
            for gpx_path, caches, extra_wpts in parsed_files:
                source = gpx_path.name

                for i in range(0, len(caches), 200):
                    _flush_cache_batch(
                        db_session, caches[i:i + 200], source,
                        existing_ids, overall_result,
                    )
                    db_session.commit()
                db_session.expunge_all()

                if extra_wpts:
                    seen: set = set()
                    unique_wpts: list = []
                    for wp in extra_wpts:
                        key = (wp.get("suffix"), wp.get("prefix"), wp.get("name"))
                        if key not in seen:
                            seen.add(key)
                            unique_wpts.append(wp)
                    overall_result.waypoints += _insert_extra_wpts(db_session, unique_wpts)
                    db_session.commit()

            # Step 3: Link all companion files against the now-complete cache table.
            for companion_path in companions:
                try:
                    wpts_tree = _parse_xml(companion_path)
                    companion_data = _parse_extra_waypoints(wpts_tree)
                    overall_result.waypoints += _link_extra_waypoints(db_session, companion_data)
                    db_session.commit()
                except Exception as e:
                    overall_result.errors.append(f"Waypoints file error ({companion_path.name}): {e}")

        except Exception:
            db_session.rollback()
            raise
        finally:
            _exit_bulk_import_pragmas(db_session)
            db_session.close()

    return overall_result

def import_loc(loc_path: Path, session: Session) -> ImportResult:
    """
    Import a .loc file into the database.

    .loc files only contain GC code, name, and coordinates.
    A warning is added to the result informing the user about missing data.
    """
    result = ImportResult()
    source = loc_path.name

    result.warnings.append(
        ".loc filer indeholder kun koordinater og navn — "
        "importér en GPX fil for at få fuld cacheinformation"
    )

    try:
        tree = _parse_xml(loc_path)
    except etree.XMLSyntaxError as e:
        result.errors.append(f"XML parse error i {loc_path.name}: {e}")
        return result

    root = tree.getroot()

    for wpt_el in root.iter("waypoint"):
        try:
            data = _parse_loc_waypoint(wpt_el)
        except Exception as e:
            result.errors.append(f"Parse error: {e}")
            result.skipped += 1
            continue

        if data is None:
            result.skipped += 1
            continue

        try:
            _, created = _upsert_cache(session, data, source)
            if created:
                result.created += 1
            else:
                result.updated += 1
        except Exception as e:
            result.errors.append(f"DB fejl for {data.get('gc_code', '?')}: {e}")
            result.skipped += 1

    return result
