"""
src/opensak/utils/constants.py — Centralised domain constants for OpenSAK.

Single source of truth for cache types, container sizes, attribute IDs,
waypoint prefixes, colour mappings, and shared numeric constants.
"""

import re

# ── Cache types (Groundspeak standard) ────────────────────────────────────────

CACHE_TYPES: list[str] = [
    # Common types
    "Traditional Cache",
    "Multi-cache",
    "Unknown Cache",
    "Letterbox Hybrid",
    "Wherigo Cache",
    "Earthcache",
    "Virtual Cache",
    "Webcam Cache",
    # Event types
    "Event Cache",
    "Cache In Trash Out Event",
    "Mega-Event Cache",
    "Giga-Event Cache",
    "Community Celebration Event",
    "Geocaching HQ Celebration",
    "Geocaching HQ Block Party",
    # Special / HQ types
    "Geocaching HQ Cache",
    "GPS Adventures Maze",
    "Lab Cache",
    "Project A.P.E. Cache",
    # Legacy / rare types
    "Locationless (Reverse) Cache",
    # Custom waypoint types (issue #141) — created manually by the user
    "Waypoint",
    "Hotel/POI",
]

# ── Valid D/T rating values (Groundspeak standard) ────────────────────────────
# D/T must be one of these — 1.7, 2.3 etc. are not valid geocache ratings.

VALID_DT: set[float] = {1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0}

# ── Custom waypoint types shown in the dialog when mode = "Custom Waypoint" ───
# Displayed in the Type dropdown; maps display-label → internal cache_type value.

CUSTOM_WP_TYPES: list[str] = [
    "Parking Area",
    "Trailhead",
    "Stage",
    "Final Location",
    "Reference Point",
    "Waypoint",
    "Hotel/POI",
    "Custom",
]

# ── Container sizes ───────────────────────────────────────────────────────────

CONTAINER_SIZES: list[str] = [
    "Micro",
    "Small",
    "Regular",
    "Large",
    "Other",
    "Not chosen",
]

# ── Geocache attribute IDs (Groundspeak) ──────────────────────────────────────
# Each tuple: (attribute_id, translation_key)
#
# Issue #615: the previous version of this table had 42 of 70 IDs mapped to the
# wrong attribute (e.g. id 31 showed "Food nearby" instead of "Camping
# available"). This directly broke attribute names on GSAK-database import
# (gsak_importer.py resolves GSAK's numeric-only aId via this table) and — a
# separate, wider-impact discovery — the attribute filter in filter_dialog.py,
# which builds its checkbox rows and filter values from this same table for
# *every* import source (GPX and GSAK alike).
#
# Rebuilt and verified July 2026 by cross-referencing real GPX exports from
# geocaching.com (which embed the official id + English name directly in each
# <groundspeak:attribute id="X">Name</groundspeak:attribute> element) against
# opencaching.eu's community-maintained "GC reference" cross-reference table
# (https://opencaching.eu/index.php/Cache_attributes), used for exactly this
# kind of third-party GPX compatibility work.
#
# 63 of the 70 IDs below were confirmed directly against real GPX exports
# (single-cache downloads plus several purpose-built test caches covering
# 15-16 attributes each). The remaining 7 — marked UNVERIFIED — are mostly
# HQ/program-controlled attributes (needs-maintenance flag, Lost & Found Tour,
# Partnership, GeoTour) that can't be set via the normal attribute picker, or
# require cache types we didn't build test caches for (Bonus, Challenge,
# Solution checker). These 7 are taken from the opencaching.eu reference only.
#
# attr_first_aid (previously wrongly at id 38, which is actually Campfires)
# has been dropped entirely: there is no "First Aid nearby" attribute in the
# official Groundspeak list, in either source used here.

ATTRIBUTES: list[tuple[int, str]] = [
    # Permissions
    (1,  "attr_dogs"),
    (32, "attr_bicycles"),
    (33, "attr_motorcycles"),
    (34, "attr_atv"),                # Quads (ATV)
    (35, "attr_jeeps"),              # Off-road vehicles / Jeeps
    (36, "attr_snowmobile"),
    (37, "attr_horses"),
    (38, "attr_campfires"),
    (46, "attr_trucks"),             # Truck Driver/RV

    # Conditions
    (6,  "attr_kids"),
    (7,  "attr_onehour"),
    (8,  "attr_scenic"),
    (9,  "attr_hiking"),
    (10, "attr_climbing"),
    (11, "attr_wading"),
    (12, "attr_swimming"),
    (13, "attr_available"),
    (14, "attr_night"),
    (15, "attr_winter"),
    (40, "attr_stealth"),
    (42, "attr_needs_maintenance"),  # UNVERIFIED — HQ-set flag, not user-editable
    (43, "attr_cow"),                # Livestock nearby (Groundspeak: "cow" icon)
    (47, "attr_field_puzzle"),
    (52, "attr_nightcache"),
    (53, "attr_park_and_grab"),
    (54, "attr_abandoned_structure"),
    (55, "attr_short_hike"),
    (56, "attr_medium_hike"),
    (57, "attr_long_hike"),
    (62, "attr_seasonal"),
    (63, "attr_tourist"),
    (65, "attr_private"),            # Yard / Private property
    (66, "attr_teamwork"),
    (71, "attr_challenge"),          # UNVERIFIED — needs a Challenge Cache to confirm
    (70, "attr_power_trail"),
    (69, "attr_bonus"),              # UNVERIFIED — needs a Bonus cache to confirm

    # Special
    (45, "attr_lost_found_tour"),    # UNVERIFIED — HQ program, not user-editable
    (61, "attr_partnership"),        # UNVERIFIED — HQ program, not user-editable
    (67, "attr_geotour"),            # UNVERIFIED — HQ program, not user-editable
    (72, "attr_solution_checker"),   # UNVERIFIED — needs a configured GC Checker to confirm

    # Equipment
    (2,  "attr_fee"),
    (3,  "attr_rappelling"),
    (4,  "attr_boat"),
    (5,  "attr_scuba"),
    (44, "attr_flashlight"),
    (48, "attr_uv"),
    (49, "attr_snowshoes"),
    (50, "attr_ski"),
    (51, "attr_special_tool"),
    (64, "attr_tree_climbing"),
    (60, "attr_wireless_beacon"),

    # Hazards
    (17, "attr_poisonous_plants"),
    (18, "attr_dangerous_animals"),
    (19, "attr_ticks"),
    (20, "attr_mine"),
    (21, "attr_cliff"),
    (22, "attr_hunting"),
    (23, "attr_dangerous_area"),
    (39, "attr_thorns"),

    # Facilities
    (24, "attr_wheelchair"),
    (25, "attr_parking"),
    (26, "attr_public_transport"),
    (27, "attr_water"),              # Drinking water nearby
    (28, "attr_restrooms"),
    (29, "attr_telephone"),
    (30, "attr_picnic"),             # Picnic tables nearby
    (31, "attr_camping"),
    (41, "attr_stroller"),
    (58, "attr_fuel"),
    (59, "attr_food"),
]

# ── Waypoint prefix registries ────────────────────────────────────────────────

KNOWN_PREFIXES: dict[str, str] = {
    # Groundspeak standard
    "PK": "Parking Area",
    "TH": "Trailhead",
    "S1": "Stage", "S2": "Stage", "S3": "Stage", "S4": "Stage",
    "S5": "Stage", "S6": "Stage", "S7": "Stage", "S8": "Stage", "S9": "Stage",
    "FN": "Final Location",
    "RF": "Reference Point",
    "WP": "Waypoint",
    "SB": "Stages of a Multicache",
    "CM": "Custom",
    "CP": "Custom",
    "PP": "Physical Stage",
    "VX": "Virtual Stage",
    "QA": "Question to Answer",
    # GSAK-specific and extended prefixes
    "LC": "Listed Coordinates",
    "LB": "Listed By",
    "LA": "Listed Area",
    "PA": "Parking Area",
    "PG": "Parking",
    "PT": "Point",
    "PN": "Point",
    "PB": "Point",
    "RP": "Reference Point",
    "ST": "Stage",
    "SP": "Stage Point",
    "AA": "Additional Waypoint",
    "UL": "Additional Waypoint",
    "TE": "Additional Waypoint",
    "FK": "Additional Waypoint",
    # Extended user-file prefixes
    "BR": "Reference Point",
    "UA": "Additional Waypoint",
    "TW": "Additional Waypoint",
    "TU": "Additional Waypoint",
    "TO": "Additional Waypoint",
    "SX": "Stage",
    "SS": "Stage",
    "SM": "Stage",
    "SH": "Stage",
    "SE": "Stage",
}

KNOWN_SINGLE_PREFIXES: dict[str, str] = {
    "T": "Trailhead",
    "V": "Virtual Stage",
    "P": "Parking Area",
    "S": "Stage",
    "F": "Final Location",
    "R": "Reference Point",
}

# ── Colour mappings ───────────────────────────────────────────────────────────

CACHE_COLOURS: dict[str, str] = {
    "Traditional Cache":  "#2e7d32",
    "Multi-cache":        "#e65100",
    "Unknown Cache":      "#1565c0",
    "Letterbox Hybrid":   "#6a1b9a",
    "Wherigo Cache":      "#00838f",
    "Event Cache":        "#ad1457",
    "Mega-Event Cache":   "#ad1457",
    "Giga-Event Cache":   "#ad1457",
    "Earthcache":         "#558b2f",
    "Virtual Cache":      "#f57f17",
}
DEFAULT_CACHE_COLOUR = "#757575"

LOG_COLOURS: dict[str, str] = {
    "Found it":          "#2e7d32",
    "Didn't find it":    "#c62828",
    "Write note":        "#1565c0",
    "Owner Maintenance": "#6a1b9a",
}

# ── "Found" log types (issue #457) ────────────────────────────────────────────
# Single source of truth for which Groundspeak log types count as evidence the
# user actually found/attended a cache. Used when deriving Cache.found_date and
# Cache.first_to_find from a cache's logs during import (importer/__init__.py)
# and when syncing found status from a reference database (db/found_updater.py).
#
# "Found it"            — regular/multi/virtual/unknown/etc. caches
# "Attended"            — Event Cache, Mega-Event, CITO, ... (no "Found it" log)
# "Webcam Photo Taken"  — old-style Webcam Caches, before webcam logging was
#                          switched over to "Found it". Still present in many
#                          users' older My Finds history.
#
# Bug #457: found_date was previously derived from "Found it" logs only, so
# webcam caches and events silently ended up with no found date on import.
FOUND_LOG_TYPES: frozenset[str] = frozenset({
    "Found it",
    "Attended",
    "Webcam Photo Taken",
})

# ── FTF (First to Find) tag pattern (issue #114 follow-up) ───────────────────
# ProjectGC (the de-facto FTF stats provider most geocachers use) only credits
# an FTF when the log contains one of these exact tags — see
# https://project-gc.com/w/First_to_Find and https://project-gc.com/Home/FAQ:
#   {FTF}   {*FTF*}   [FTF]
#
# The previous implementation matched free-text phrases ("ftf", "first to
# find", "first finder", "første til at finde") anywhere in the user's own
# found-log text. That produced false positives whenever a log merely
# mentioned the concept without claiming it, e.g. "...forgæves forsøg på at
# blive first finder..." (a log about NOT getting FTF) got flagged as FTF.
#
# FTF_TAG_PATTERN matches only the tags above, case-insensitively:
#   {FTF} / {ftf}, {*FTF*} / {*ftf*}, [FTF] / [ftf]
FTF_TAG_PATTERN = re.compile(r"\{\*?ftf\*?\}|\[ftf\]", re.IGNORECASE)

# ── Shared numeric constants ──────────────────────────────────────────────────

EARTH_RADIUS_M = 6_371_000.0  # WGS-84 mean radius in metres
