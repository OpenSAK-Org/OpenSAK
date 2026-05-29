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

import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import time
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
    nodes = element.xpath(xpath, namespaces=ns)
    if nodes:
        return str(nodes[0]).strip().lower() == "true"
    return False


def _parse_datetime(raw: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 datetime strings from GPX files into UTC datetimes."""
    if not raw:
        return None
    raw = raw.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


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
        name_el = wpt.find("{%s}n" % ns["gpx"])
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

    # GC code: <n> in newer PQ files, <n> in older format
    gc_code = (
        _text(wpt_el, "gpx:name", NS) or
        _text(wpt_el, "gpx:n", NS)
    )
    # Accept GC codes (standard caches) and LC codes (Adventure Lab / lab2gpx)
    if not gc_code or not (gc_code.startswith("GC") or gc_code.startswith("LC")):
        return None

    # Cache type from <type>Geocache|Traditional Cache</type>
    type_raw = _text(wpt_el, "gpx:type", NS) or ""
    cache_type_full = type_raw.split("|")[-1].strip() if "|" in type_raw else type_raw

    hidden_raw = _text(wpt_el, "gpx:time", NS)

    # ── Found by me — detekteret via <sym>Geocache Found</sym> ───────────────
    # Groundspeak sætter sym til "Geocache Found" for caches fundet af PQ-ejeren.
    sym = _text(wpt_el, "gpx:sym", NS) or ""
    found_by_me = sym.strip().lower() == "geocache found"

    # ── Groundspeak extension block ───────────────────────────────────────────
    # Detect which Groundspeak namespace this file actually uses (/1/0 or /1/0/1)
    gs_cache = None
    active_ns = NS  # default
    for gs_uri in _GS_NAMESPACES:
        gs_cache = wpt_el.find(f"{{{gs_uri}}}cache")
        if gs_cache is not None:
            active_ns = _make_ns(gs_uri)
            break

    gs_id        = gs_cache.get("id")                    if gs_cache is not None else None
    available    = _bool_attr(gs_cache, "@available")    if gs_cache is not None else True
    archived     = _bool_attr(gs_cache, "@archived")     if gs_cache is not None else False

    name         = _text(gs_cache, "gs:name",              active_ns) or _text(wpt_el, "gpx:urlname", NS) or gc_code
    placed_by    = _text(gs_cache, "gs:placed_by",         active_ns)
    owner        = _text(gs_cache, "gs:owner",             active_ns)
    owner_id     = gs_cache.find("gs:owner", active_ns).get("id") if gs_cache is not None and gs_cache.find("gs:owner", active_ns) is not None else None
    cache_type   = _text(gs_cache, "gs:type",              active_ns) or cache_type_full
    if cache_type.lower() in ("gps adventures maze exhibit", "gps adventures exhibit"):
        cache_type = "GPS Adventures Maze"
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
    for gsak_uri in _GSAK_NAMESPACES:
        gsak_ext = wpt_el.find(f"{{{gsak_uri}}}wptExtension")
        if gsak_ext is not None:
            ftf_el = gsak_ext.find(f"{{{gsak_uri}}}FirstToFind")
            if ftf_el is not None and ftf_el.text:
                gsak_ftf = ftf_el.text.strip().lower() == "true"

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
        "county":            county,
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
        "found_by_me":       found_by_me,
    }


# ── Extra waypoint parser (GSAK single-file GPX) ─────────────────────────────

from opensak.utils.constants import KNOWN_PREFIXES as _KNOWN_PREFIXES
from opensak.utils.constants import KNOWN_SINGLE_PREFIXES as _KNOWN_SINGLE_PREFIXES


def _parse_extra_wpt(wpt_el) -> Optional[dict]:
    """Parse a non-GC <wpt> as an extra waypoint (parking, stage, etc.)."""
    try:
        lat = float(wpt_el.get("lat"))
        lon = float(wpt_el.get("lon"))
    except (TypeError, ValueError):
        return None

    raw_name = (
        _text(wpt_el, "gpx:name", NS) or
        _text(wpt_el, "gpx:n",    NS) or ""
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
        type_raw_check = _text(wpt_el, "gpx:type", NS) or ""
        if "|" in type_raw_check and type_raw_check.startswith("Waypoint"):
            # Acceptér som waypoint med generisk prefix
            prefix = raw_name[:2].upper()
            suffix = raw_name[-6:] if len(raw_name) >= 6 else raw_name
            wp_type_fallback = type_raw_check.split("|")[-1].strip()
        else:
            return None

    type_raw = _text(wpt_el, "gpx:type", NS) or ""
    if "|" in type_raw:
        wp_type = type_raw.split("|")[-1].strip()
    elif type_raw:
        wp_type = type_raw
    else:
        wp_type = wp_type_fallback

    desc    = _text(wpt_el, "gpx:desc",    NS)
    comment = _text(wpt_el, "gpx:cmt",     NS)
    name    = _text(wpt_el, "gpx:urlname", NS) or desc or raw_name

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
    """Insert/update extra waypoints.

    Bygger ét suffix→cache_id lookup i RAM for at undgå 11.000 LIKE-queries.
    Committer til disk for hver commit_every caches.
    """
    t0 = time.time()

    # Hent alle gc_codes på én gang og byg suffix→cache_id dict i RAM
    all_caches = session.query(Cache.id, Cache.gc_code).all()
    suffix_to_cache_id: dict[str, int] = {}
    for cache_id, gc_code in all_caches:
        if gc_code and len(gc_code) > 2:
            suffix_to_cache_id[gc_code[2:]] = cache_id

    # Grupper waypoints per suffix
    wpts_by_suffix: dict[str, list] = {}
    for wp in extra_wpts:
        wpts_by_suffix.setdefault(wp["suffix"], []).append(wp)

    count = 0
    batch = 0

    for suffix, wps in wpts_by_suffix.items():
        cache_id = suffix_to_cache_id.get(suffix)
        if cache_id is None:
            continue

        session.query(Waypoint).filter_by(cache_id=cache_id).delete(synchronize_session="fetch")

        for wp in wps:
            session.add(Waypoint(
                cache_id=cache_id,
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
            t1 = time.time()
            session.commit()
            session.expunge_all()
    
    t1 = time.time()
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

def _upsert_cache(session: Session, data: dict, source_file: str) -> tuple[Cache, bool]:
    """
    Insert or update a Cache row from parsed GPX data.
    Returns (cache_object, created: bool).
    """
    existing = session.query(Cache).filter_by(gc_code=data["gc_code"]).first()
    created  = existing is None

    if created:
        cache = Cache(gc_code=data["gc_code"])
        session.add(cache)
    else:
        cache = existing
        # Clear old child records so they are rebuilt fresh.
        # flush() is required immediately after delete so SQLite sees the
        # deletions before the new rows are added in the same batch —
        # otherwise the UNIQUE constraint on logs.log_id will fire.
        session.query(Log).filter_by(cache_id=cache.id).delete(synchronize_session="fetch")
        session.query(Attribute).filter_by(cache_id=cache.id).delete(synchronize_session="fetch")
        session.query(Trackable).filter_by(cache_id=cache.id).delete(synchronize_session="fetch")
        session.query(Waypoint).filter_by(cache_id=cache.id).delete(synchronize_session="fetch")
        session.flush()

    # Scalar fields
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
    DUMMY_LOG_IDS = {"0", None, ""}
    seen_log_ids: set[str] = set()
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
        if log_id in seen_log_ids:
            continue
        seen_log_ids.add(log_id)
        session.add(Log(
            cache=cache,
            log_id=log_id,
            log_type=lg["log_type"],
            log_date=lg["log_date"],
            finder=lg["finder"],
            finder_id=lg["finder_id"],
            text=lg["text"],
            text_encoded=lg["text_encoded"],
        ))

    # ── Issue #87: Cache log count for fast UI display ──────────────────────
    # Old logs were deleted at the start of this function (re-import), and
    # seen_log_ids holds the de-duplicated set of logs we just added — so
    # its length equals the new total count of logs for this cache.
    cache.log_count = len(seen_log_ids)

    # ── Issue #186: Cache latest log date for fast UI display ────────────────
    log_dates = [lg["log_date"] for lg in data.get("logs", []) if lg.get("log_date")]
    cache.last_log_date = max(log_dates) if log_dates else None

    # Trackables
    for tb in data.get("trackables", []):
        session.add(Trackable(cache=cache, ref=tb["ref"], name=tb["name"]))

    # ── Found by me (sym=Geocache Found) + found_date fra brugerens log ────────
    # Vi sætter kun found=True hvis GPX'en eksplicit markerer cachen som fundet
    # (sym="Geocache Found"). found=False sætter vi IKKE ved re-import — det ville
    # overskrive manuelle markeringer. Undtagelse: hvis found_by_me er False og
    # cachen ikke er fundet, lader vi den eksisterende found-værdi stå uændret.
    #
    # found_date hentes fra brugerens egen log-entry:
    #   1. Søg efter log med finder_id der matcher gc_finder_id i Settings.
    #   2. Fallback: søg på gc_username (case-insensitive).
    #   3. Hvis ingen match og cachen er found: brug ældste "Found it" dato.
    #
    # Auto-lær finder_id: første gang vi ser en log der matcher gc_username
    # gemmer vi det numeriske finder_id i Settings — så næste import er hurtigere.

    found_by_me = data.get("found_by_me", False)
    logs_data = data.get("logs", [])

    if found_by_me:
        cache.found = True
        from opensak.gui.settings import get_settings
        _sett = get_settings()
        gc_username  = (_sett.gc_username  or "").strip().lower()
        gc_finder_id = (_sett.gc_finder_id or "").strip()

        found_log = None

        # Trin 1: match på numerisk finder_id (hurtigst + mest præcist)
        if gc_finder_id:
            for lg in logs_data:
                if (lg.get("log_type") == "Found it"
                        and str(lg.get("finder_id", "")).strip() == gc_finder_id):
                    found_log = lg
                    break

        # Trin 2: match på brugernavn (case-insensitive)
        if found_log is None and gc_username:
            for lg in logs_data:
                if (lg.get("log_type") == "Found it"
                        and (lg.get("finder") or "").strip().lower() == gc_username):
                    found_log = lg
                    # Auto-lær finder_id fra denne log
                    detected_id = str(lg.get("finder_id", "")).strip()
                    if detected_id and not gc_finder_id:
                        _sett.gc_finder_id = detected_id
                    break

        # Trin 3: fallback — ældste "Found it" log (f.eks. ingen brugernavn sat)
        if found_log is None:
            found_logs = [lg for lg in logs_data
                          if lg.get("log_type") == "Found it" and lg.get("log_date")]
            if found_logs:
                found_log = min(found_logs, key=lambda lg: lg["log_date"])

        if found_log and found_log.get("log_date"):
            cache.found_date = found_log["log_date"]

    # ── Derive dnf_date, first_to_find from logs (issue #33, #58, #114) ────────
    # These are only set/updated when a fresh import brings logs in.
    # We only touch them if we actually have log data to derive from.
    if logs_data:
        # dnf_date: date of the most recent "Didn't find it" log by any finder
        # (GSAK stores the last DNF date regardless of who logged it)
        dnf_dates = [
            lg["log_date"]
            for lg in logs_data
            if lg.get("log_type") == "Didn't find it" and lg.get("log_date")
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
            # Kun keyword-match i brugerens egen log-tekst er sikker.
            from opensak.gui.settings import get_settings
            gc_username  = get_settings().gc_username.strip().lower()
            gc_finder_id = get_settings().gc_finder_id.strip()

            ftf_keywords = ("ftf", "first to find", "first finder",
                            "første til at finde")

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
                    any(kw in (lg.get("text") or "").lower() for kw in ftf_keywords)
                    for lg in user_found_logs
                )
            else:
                # Intet brugernavn konfigureret — kan ikke detektere FTF sikkert
                cache.first_to_find = False
        else:
            # Brugeren har IKKE fundet denne cache — FTF ikke relevant
            cache.first_to_find = False

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


def _link_extra_waypoints(
    session: Session,
    extra: dict[str, list[dict]],
) -> int:
    """
    Match parsed extra waypoints to caches already in the DB and insert them.
    Returns number of waypoints inserted.
    """
    count = 0
    for suffix, wpts in extra.items():
        # Find any cache whose gc_code ends with this suffix
        cache = (
            session.query(Cache)
            .filter(Cache.gc_code.like(f"%{suffix}"))
            .first()
        )
        if cache is None:
            continue

        # Remove stale extra waypoints for this cache (keep DB clean on re-import)
        session.query(Waypoint).filter_by(cache_id=cache.id).delete()

        for wp in wpts:
            session.add(Waypoint(
                cache=cache,
                prefix=wp["prefix"],
                wp_type=wp["wp_type"],
                name=wp["name"],
                description=wp["description"],
                comment=wp["comment"],
                latitude=wp["latitude"],
                longitude=wp["longitude"],
            ))
            count += 1

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
        context = etree.iterparse(str(gpx_path), events=("end",), tag=None)

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
            wpts_tree = etree.parse(str(wpts_path))
            companion_data = _parse_extra_waypoints(wpts_tree)
        except Exception as e:
            errors.append(f"Waypoints file error: {e}")

    return caches, extra_wpts, companion_data, errors


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

    try:
        # Stream the XML using iterparse to avoid loading the entire file into RAM
        context = etree.iterparse(
            str(gpx_path), 
            events=("end",), 
            tag=None  
        )

        for event, elem in context:
            # Handle tags regardless of namespace version
            local_tag = etree.QName(elem).localname

            if local_tag == "wpt":
                try:
                    data = _parse_wpt(elem)
                    
                    if data is not None:
                        # Use a SAVEPOINT so a failure in one cache does NOT
                        # roll back the entire batch — only this single cache.
                        nested = db_session.begin_nested()
                        try:
                            _, created = _upsert_cache(db_session, data, source)
                            nested.commit()
                            if created:
                                result.created += 1
                            else:
                                result.updated += 1
                        except Exception as e:
                            nested.rollback()
                            result.errors.append(f"Error in {source}: {e}")
                            result.skipped += 1
                        
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
                            
                    # Batch commit to disk and clear session to save RAM
                    if processed_count % batch_size == 0:
                        db_session.commit()
                        db_session.expunge_all()

                except Exception as e:
                    result.errors.append(f"Error in {source}: {e}")
                    result.skipped += 1
                finally:
                    # CRITICAL: Clear the element and its preceding siblings from memory
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]

        # Final commit for remaining caches
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
                wpts_tree = etree.parse(str(wpts_path))
                extra_data = _parse_extra_waypoints(wpts_tree)
                result.waypoints += _link_extra_waypoints(db_session, extra_data)
                db_session.commit()
            except Exception as e:
                result.errors.append(f"Waypoints file error: {e}")

    except Exception as e:
        db_session.rollback()
        result.errors.append(f"Fatal error importing {source}: {e}")
    finally:
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

        gpx_files = sorted(f for f in tmp.glob("*.gpx") if "-wpts" not in f.name.lower())

        if not gpx_files:
            overall_result.errors.append("No .gpx file found in zip")
            return overall_result

        # Step 1: Parse all GPX files in parallel (pure CPU, no DB)
        parsed_files = []
        with ThreadPoolExecutor(max_workers=min(8, len(gpx_files))) as executor:
            futures = {}
            for gpx_path in gpx_files:
                wpts_candidate = tmp / f"{gpx_path.stem}-wpts.gpx"
                wpts = wpts_candidate if wpts_candidate.exists() else None
                futures[executor.submit(_parse_gpx_to_data, gpx_path, wpts)] = gpx_path

            for future in futures:
                try:
                    caches, extra_wpts, companion_data, errors = future.result()
                    parsed_files.append((futures[future], caches, extra_wpts, companion_data))
                    overall_result.errors.extend(errors)
                except Exception as e:
                    overall_result.errors.append(f"Parse error: {str(e)}")

        # Step 2: Write all parsed data sequentially (single session, no contention)
        db_session = make_session()
        try:
            for gpx_path, caches, extra_wpts, companion_data in parsed_files:
                source = gpx_path.name

                for data in caches:
                    nested = db_session.begin_nested()
                    try:
                        _, created = _upsert_cache(db_session, data, source)
                        nested.commit()
                        if created:
                            overall_result.created += 1
                        else:
                            overall_result.updated += 1
                    except Exception as e:
                        nested.rollback()
                        overall_result.errors.append(f"DB error for {data.get('gc_code', '?')}: {e}")
                        overall_result.skipped += 1

                db_session.commit()
                db_session.expunge_all()

                # Deduplicate and insert extra waypoints from main GPX
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

                # Link companion waypoints file data
                if companion_data:
                    overall_result.waypoints += _link_extra_waypoints(db_session, companion_data)
                    db_session.commit()
        except Exception:
            db_session.rollback()
            raise
        finally:
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
        tree = etree.parse(str(loc_path))
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
