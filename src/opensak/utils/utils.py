"""
src/opensak/utils/types.py — Utility functions for data validation and file type identification.

Provides regex-based validation for Geocaching GC codes and format constraints.
Maps file system paths to the internal ImportType Enum for secure file processing.
"""

import re
from pathlib import Path
from opensak.utils.types import GcCode, ImportType
from opensak.utils.constants import FOUND_LOG_TYPES

def validate_gc_code(gc_code: GcCode) -> None:
    """Validate geocache code format (GC prefix, 3-7 chars, restricted letters)."""
    if not re.match(r'^GC[0-9A-NP-RT-Z]{1,7}$', gc_code.upper()):
        raise ValueError(
            f"Invalid gc_code format: {gc_code}. Expected GC prefix + 1-7 chars "
            "with letters A-Z excluding O, L, S, and digits 0-9."
        )

def get_import_type(path: Path) -> ImportType:
    """Identifies the ImportType based on file extension."""
    suffix = path.suffix.lower()
    mapping = {
        ".gpx": ImportType.GPX,
        ".zip": ImportType.ZIP,
    }
    
    if suffix not in mapping:
        raise ValueError(f"Unsupported file format: {suffix}")
        
    return mapping[suffix]


# Issue #272: users who run a GSAK "statistics" macro (e.g. FindStatGen) get
# found/hide counts appended to the owner field on export, e.g.
# "Cheminer Will (F=1361 H=54)". The trailing "(Key=N ...)" block is not
# part of the actual name and must be stripped before comparing against the
# plain username configured in Settings.
_GSAK_STATS_SUFFIX_RE = re.compile(r"\s*\([A-Za-z]+=\d+(?:\s+[A-Za-z]+=\d+)*\)\s*$")


def normalize_geocacher_name(name: str | None) -> str:
    """Normalize a geocacher display name for case/whitespace-insensitive matching.

    Handles two real-world GPX export quirks (issue #272):
      1. GSAK statistics-macro decoration — a trailing "(F=1361 H=54)"-style
         block appended to the owner name — is stripped.
      2. Irregular whitespace (incl. non-breaking spaces, \\xa0) inside the
         name is collapsed to single regular spaces.

    The result is stripped and lowercased. Used to compare an imported
    'owner'/'placed_by' name against the user's configured GC username.
    """
    if not name:
        return ""
    cleaned = _GSAK_STATS_SUFFIX_RE.sub("", name)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def count_own_found_logs(
    logs_data: list[dict], gc_finder_id: str | None, gc_username: str | None
) -> int:
    """Count how many of the logs in *logs_data* are the CURRENT USER's own
    found-type logs (issue #552).

    `Cache.found` is a boolean ("have I found this cache at least once"),
    which undercounts relocatable/multi-visit caches where the same user can
    legitimately log a found-type entry more than once — geocaching.com and
    GSAK both count found LOGS, not found CACHES.

    Matches the same way found_date/FTF are already derived elsewhere
    (importer/__init__.py): numeric finder_id first (fastest, most precise),
    falling back to a normalized username comparison. A log counts if
    EITHER signal matches, so relocatable caches are correctly counted once
    per found-type log rather than once per cache.
    """
    gc_finder_id = (gc_finder_id or "").strip()
    gc_username_norm = normalize_geocacher_name(gc_username)
    if not gc_finder_id and not gc_username_norm:
        return 0
    count = 0
    for lg in logs_data:
        if lg.get("log_type") not in FOUND_LOG_TYPES:
            continue
        if gc_finder_id and str(lg.get("finder_id", "")).strip() == gc_finder_id:
            count += 1
        elif gc_username_norm and normalize_geocacher_name(lg.get("finder")) == gc_username_norm:
            count += 1
    return count