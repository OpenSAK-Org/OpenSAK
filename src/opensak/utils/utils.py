"""
src/opensak/utils/types.py — Utility functions for data validation and file type identification.

Provides regex-based validation for Geocaching GC codes and format constraints.
Maps file system paths to the internal ImportType Enum for secure file processing.
"""

import re
from pathlib import Path
from opensak.utils.types import GcCode, ImportType

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