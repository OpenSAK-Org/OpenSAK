"""
src/opensak/export/gpx_content.py — what a GPX/GGZ export writes per cache
(#775): which child waypoints, how many logs, the user note as a first log,
and the GPX version.

Qt-free, so the GPX/GGZ writers (gps/garmin.py), the export settings, the
export dialogs, Lua macros and the GPX importer share one definition. The
defaults are the export's behaviour before these options existed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
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


@dataclass(frozen=True)
class GpxContent:
    """The per-cache content options of a GPX/GGZ export."""

    children: str = "all"          # one of CHILDREN_CHOICES
    children_only: bool = False    # write the child waypoints without their caches
    max_logs: int = 0              # logs per cache, newest first (0 = all)
    my_logs_always: bool = False   # the user's own logs on top of max_logs
    user_note_log: bool = False    # the user note as a synthetic first log
    version: str = "1.0"           # one of GPX_VERSIONS

    def __post_init__(self) -> None:
        if self.children not in CHILDREN_CHOICES:
            raise ValueError(f"children must be one of {CHILDREN_CHOICES}, got {self.children!r}")
        if self.version not in GPX_VERSIONS:
            raise ValueError(f"version must be one of {GPX_VERSIONS}, got {self.version!r}")
        if isinstance(self.max_logs, bool) or not isinstance(self.max_logs, int) \
                or self.max_logs < 0:
            raise ValueError(f"max_logs must be a whole number >= 0, got {self.max_logs!r}")

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
        return self.children != "none"

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
        those passing the children choice that have coordinates."""
        return [
            wp for wp in (getattr(cache, "waypoints", None) or [])
            if wp.latitude is not None and wp.longitude is not None
            and self.wants_child(wp)
        ]

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
