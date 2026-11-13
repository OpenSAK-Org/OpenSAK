"""
src/opensak/export/gpx_content.py — what a GPX/GGZ export writes per cache
(#775): which child waypoints, how many logs, the user note as a first log,
and the GPX version; whether it writes GSAK's own fields (#1169); and the
templates for waypoint names, descriptions and the extra-info log (#454,
#950; see special_tags.py); and a Final Location waypoint at the corrected
coordinates (#1303).

Qt-free, so the GPX/GGZ writers (gps/garmin.py), the export settings, the
export dialogs, Lua macros and the GPX importer share one definition. The
defaults are the export's behaviour before these options existed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional

# Which child waypoints an export writes: all of them, only those with or
# without their flag set (Waypoint.wp_flag), or none.
CHILDREN_CHOICES = ("all", "flagged", "unflagged", "none")
# What the export's record limit counts: caches, or every <wpt> in the file
# (a cache plus its children), as GSAK's "max waypoints" does.
MAX_COUNTS_CHOICES = ("caches", "waypoints")
# GPX versions the export writes. 1.0 is GSAK's layout, which Garmin's
# geocache parser expects (#656, #700); 1.1 is plain GPX 1.1.
GPX_VERSIONS = ("1.0", "1.1")

# The synthetic first log carrying the user note, in the form GSAK writes
# it with "Include user notes": log id -2, finder "GSAK" with id 0, type
# "Write note", dated (without a time zone) with the note's last change.
USER_NOTE_LOG_ID = "-2"
USER_NOTE_LOG_FINDER = "GSAK"
USER_NOTE_LOG_FINDER_ID = "0"
USER_NOTE_LOG_TYPE = "Write note"

# #454: the synthetic first log with extra information from a template
# (GSAK's "Extra information to show as first log", GarminExport's dummy
# log). Not id -2 or -3, which GSAK uses for its own notes: an id of its
# own, by "OpenSAK", so a re-import recognises it and doesn't store it.
EXTRA_INFO_LOG_ID = "-454"
EXTRA_INFO_LOG_FINDER = "OpenSAK"
EXTRA_INFO_LOG_FINDER_ID = "0"
EXTRA_INFO_LOG_TYPE = "Write note"

# #1303: the Final Location child an export adds at a cache's corrected
# coordinates. The comment is fixed (not translated): it marks the
# waypoint, so a re-import recognises it and doesn't store it as a real
# child (see is_corrected_final_wp()).
CORRECTED_FINAL_WP_TYPE = "Final Location"
CORRECTED_FINAL_WP_PREFIX = "FL"
CORRECTED_FINAL_WP_NAME = "Final Location"
CORRECTED_FINAL_WP_COMMENT = "Final location from corrected coordinates (OpenSAK)"


def normalize_note(text: Optional[str]) -> str:
    """A user note as the user-note log carries it: LF line ends, no
    surrounding whitespace (the GPX importer strips log texts too)."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def user_note_log_date(updated_at: Optional[datetime]) -> str:
    """The date of the user-note log: the note's last change (now when it
    is unknown), formatted as GSAK writes it, without a time zone."""
    return (updated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S")


def is_user_note_log(
    log_id: Optional[str], finder: Optional[str], finder_id: Optional[str],
    log_type: Optional[str], text: Optional[str], notes: Iterable[str],
) -> bool:
    """True if a GPX log is the synthetic user-note log of an export, so an
    import doesn't store it as a real log. It must have the user-note log's
    form *and* the text of the cache's user note (one of *notes*, already
    normalised): GSAK writes the same form for other notes of its own, which
    stay real logs as before."""
    return (
        (log_id or "").strip() == USER_NOTE_LOG_ID
        and (finder or "").strip() == USER_NOTE_LOG_FINDER
        and (finder_id or "").strip() in ("", USER_NOTE_LOG_FINDER_ID)
        and (log_type or "").strip() == USER_NOTE_LOG_TYPE
        and normalize_note(text) in notes
        and normalize_note(text) != ""
    )


def corrected_coords(cache: Any) -> Optional[tuple[float, float]]:
    """The corrected coordinates of *cache*, or None when it has none."""
    note = getattr(cache, "user_note", None)
    if not note or not getattr(note, "is_corrected", False):
        return None
    lat, lon = getattr(note, "corrected_lat", None), getattr(note, "corrected_lon", None)
    if lat is None or lon is None:
        return None
    return lat, lon


def same_position(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """True if two positions are the same as an export writes them (6
    decimals, about 0.1 m)."""
    return round(a[0], 6) == round(b[0], 6) and round(a[1], 6) == round(b[1], 6)


def is_corrected_final_wp(wp_type: Optional[str], comment: Optional[str]) -> bool:
    """True if a GPX child waypoint is the Final Location an OpenSAK
    export added at the corrected coordinates (#1303), which an import
    skips: the corrected coordinates come back with the cache itself."""
    return (
        (wp_type or "").strip() == CORRECTED_FINAL_WP_TYPE
        and (comment or "").strip() == CORRECTED_FINAL_WP_COMMENT
    )


def is_extra_info_log(log_id: Optional[str], finder: Optional[str]) -> bool:
    """True if a GPX log is the extra-info log of an OpenSAK export (#454),
    which an import skips."""
    return (
        (log_id or "").strip() == EXTRA_INFO_LOG_ID
        and (finder or "").strip() == EXTRA_INFO_LOG_FINDER
    )


@dataclass(frozen=True)
class GpxContent:
    """The per-cache content options of a GPX/GGZ export."""

    children: str = "all"          # one of CHILDREN_CHOICES
    children_only: bool = False    # write the child waypoints without their caches
    max_logs: int = 0              # logs per cache, newest first (0 = all)
    my_logs_always: bool = False   # the user's own logs on top of max_logs
    user_note_log: bool = False    # the user note as a synthetic first log
    version: str = "1.0"           # one of GPX_VERSIONS
    # #1169: GSAK's full field set in gsak:wptExtension (GSAK's "Include
    # GSAK extra fields"): user flag, lock, user data, custom fields, the
    # child waypoints' Parent/Child_* and the rest. Off writes only
    # UserFound and UserNote, as before, for files meant for other apps.
    gsak_fields: bool = True
    # #454/#950: special-tag templates (see special_tags.py). "" keeps the
    # default: the GC code as the waypoint name, "Name by Owner (D/T)" as
    # its description, no extra-info log.
    name_template: str = ""        # the cache's <wpt> <name>
    name_max_chars: int = 0        # cut <name> to this many characters (0 = no limit)
    desc_template: str = ""        # the cache's <wpt> <desc>
    desc_for_cache_name: bool = False  # desc_template also as the cache name
    remove_chars: str = ""         # characters removed from names and descriptions
    first_log: str = ""            # the extra-info log's text ("" = no such log)
    # #1303: a Final Location child at the corrected coordinates of each
    # cache that has them, written regardless of the children choice. Apps
    # that refresh a cache online lose its corrected coordinates, but keep
    # its child waypoints.
    corrected_final_wp: bool = False

    def __post_init__(self) -> None:
        if self.children not in CHILDREN_CHOICES:
            raise ValueError(f"children must be one of {CHILDREN_CHOICES}, got {self.children!r}")
        if self.version not in GPX_VERSIONS:
            raise ValueError(f"version must be one of {GPX_VERSIONS}, got {self.version!r}")
        if isinstance(self.max_logs, bool) or not isinstance(self.max_logs, int) \
                or self.max_logs < 0:
            raise ValueError(f"max_logs must be a whole number >= 0, got {self.max_logs!r}")
        if isinstance(self.name_max_chars, bool) or not isinstance(self.name_max_chars, int) \
                or self.name_max_chars < 0:
            raise ValueError(
                f"name_max_chars must be a whole number >= 0, got {self.name_max_chars!r}")
        for key in ("name_template", "desc_template", "remove_chars", "first_log"):
            if not isinstance(getattr(self, key), str):
                raise ValueError(f"{key} must be text, got {getattr(self, key)!r}")

    def for_format(self, fmt: str) -> "GpxContent":
        """The options as they apply to *fmt*. A GGZ always has the caches
        (its index points at each cache's <wpt>), so children_only doesn't
        apply; its GPX follows *version* like a GPX file. There is no
        public GGZ specification: GSAK's and Opencaching's (OKAPI) GGZ files
        carry GPX 1.0, hence the 1.0 default. LOC and KML have no logs or
        child options here."""
        if fmt == "ggz":
            return replace(self, children_only=False)
        if fmt == "gpx":
            return self
        return GpxContent()

    def writes_children(self) -> bool:
        return self.children != "none" or self.corrected_final_wp

    def wants_child(self, waypoint: Any) -> bool:
        """True if *waypoint* passes the children choice (coordinates
        aside)."""
        if self.children == "none":
            return False
        if self.children == "all":
            return True
        flagged = bool(getattr(waypoint, "wp_flag", False))
        return flagged if self.children == "flagged" else not flagged

    def child_waypoints(self, cache: Any) -> list:
        """The child waypoints of *cache* the export writes, in order:
        those passing the children choice that have coordinates, then the
        generated Final Location (#1303, see corrected_final_wp())."""
        children = [
            wp for wp in (getattr(cache, "waypoints", None) or [])
            if wp.latitude is not None and wp.longitude is not None
            and self.wants_child(wp)
        ]
        final = self.corrected_final_wp_for(cache, children)
        return children + [final] if final is not None else children

    def corrected_final_wp_for(self, cache: Any, children: list) -> Optional[Any]:
        """#1303: the Final Location waypoint to add to *cache*, whose
        written children are *children*, or None: when the option is off,
        the cache has no corrected coordinates, or one of *children* is
        already a Final Location there (a final the user added by hand).

        Its code is FL + the GC code's suffix, like GSAK's child codes; F1
        to F9 when a written child already has that code. It is "made by
        the user" (gsak:Child_ByGSAK), as a GSAK macro's child would be."""
        if not self.corrected_final_wp:
            return None
        position = corrected_coords(cache)
        if position is None:
            return None
        if any(
            (wp.wp_type or "") == CORRECTED_FINAL_WP_TYPE
            and same_position((wp.latitude, wp.longitude), position)
            for wp in children
        ):
            return None
        gc_code = getattr(cache, "gc_code", None) or ""
        suffix = gc_code[2:] if len(gc_code) > 2 else ""
        taken = {
            ((getattr(wp, "wp_code", None) or "").upper(), (wp.prefix or "").upper())
            for wp in children
        }
        used = {code for code, _ in taken} | {
            prefix + suffix.upper() for _, prefix in taken
        }
        prefix = next(
            (p for p in [CORRECTED_FINAL_WP_PREFIX] + [f"F{n}" for n in range(1, 10)]
             if (p + suffix).upper() not in used),
            CORRECTED_FINAL_WP_PREFIX,
        )
        return SimpleNamespace(
            prefix=prefix, wp_code=prefix + suffix if suffix else None,
            wp_type=CORRECTED_FINAL_WP_TYPE,
            name=CORRECTED_FINAL_WP_NAME, description=CORRECTED_FINAL_WP_NAME,
            comment=CORRECTED_FINAL_WP_COMMENT, url=None, wp_date=None,
            latitude=position[0], longitude=position[1],
            created_by_user=True, wp_flag=False,
        )

    def select_logs(self, logs: Iterable, is_mine: Optional[Callable[[Any], bool]] = None) -> list:
        """*logs* newest first, cut to max_logs. With my_logs_always the
        user's own logs (*is_mine*) that the limit cut are kept as well,
        still in date order."""
        min_dt = datetime.min.replace(tzinfo=timezone.utc)

        def _key(log) -> datetime:
            when = log.log_date
            if when is None:
                return min_dt
            return when if when.tzinfo else when.replace(tzinfo=timezone.utc)

        ordered = sorted(logs or [], key=_key, reverse=True)
        if not self.max_logs or len(ordered) <= self.max_logs:
            return ordered
        if not (self.my_logs_always and is_mine is not None):
            return ordered[:self.max_logs]
        kept = set(range(self.max_logs))
        kept.update(i for i, log in enumerate(ordered) if i >= self.max_logs and is_mine(log))
        return [log for i, log in enumerate(ordered) if i in kept]


DEFAULT_CONTENT = GpxContent()


def my_log_matcher() -> Optional[Callable[[Any], bool]]:
    """A test for the user's own logs, by the geocaching.com account in
    Settings (the same rule as for the user's own caches and finds), or
    None when no account is set."""
    from opensak.db.ownership import account_configured, owner_matches
    from opensak.gui.settings import get_settings

    settings = get_settings()
    finder_id, username = settings.gc_finder_id, settings.gc_username
    if not account_configured(finder_id, username):
        return None
    return lambda log: owner_matches(
        str(log.finder_id) if log.finder_id is not None else None,
        log.finder, finder_id, username,
    )
