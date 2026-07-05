"""
models.py — SQLAlchemy ORM models for OpenSAK.

Tables
------
Cache         — the main geocache record (mirrors GPX/Groundspeak schema)
Waypoint      — additional waypoints linked to a cache (parking, stages, etc.)
Log           — individual log entries (found it, didn't find it, etc.)
Attribute     — cache attributes (dog-friendly, night cache, etc.)
Trackable     — trackable items (Travel Bugs, geocoins) seen in a cache
UserNote      — personal notes attached to a cache (incl. corrected coordinates)
"""

from __future__ import annotations

from datetime import datetime, timezone


def _utcnow() -> datetime:
    """Return current UTC time as a timezone-aware datetime (Python 3.12+ safe)."""
    return datetime.now(timezone.utc)
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ── Base ─────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Cache ────────────────────────────────────────────────────────────────────

class Cache(Base):
    """
    One geocache.  The gc_code (e.g. GC12AB3) is the natural primary key
    but we also keep a surrogate integer id for fast FK references.
    """
    __tablename__ = "caches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identity
    gc_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    cache_type: Mapped[str] = mapped_column(String(64), nullable=False)   # Traditional, Multi, Mystery …
    container: Mapped[Optional[str]] = mapped_column(String(32))          # Nano, Micro, Small, Regular, Large, Other

    # Coordinates
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # Ratings
    difficulty: Mapped[Optional[float]] = mapped_column(Float)   # 1.0 – 5.0
    terrain: Mapped[Optional[float]] = mapped_column(Float)      # 1.0 – 5.0

    # Owner
    placed_by: Mapped[Optional[str]] = mapped_column(String(128))
    owner_name: Mapped[Optional[str]] = mapped_column(String(128))
    owner_id: Mapped[Optional[str]] = mapped_column(String(64))

    # Dates
    hidden_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Status flags
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    premium_only: Mapped[bool] = mapped_column(Boolean, default=False)

    # Cache content
    short_description: Mapped[Optional[str]] = mapped_column(Text)
    short_desc_html: Mapped[bool] = mapped_column(Boolean, default=False)
    long_description: Mapped[Optional[str]] = mapped_column(Text)
    long_desc_html: Mapped[bool] = mapped_column(Boolean, default=False)
    encoded_hints: Mapped[Optional[str]] = mapped_column(Text)

    # Country / state / county
    country: Mapped[Optional[str]] = mapped_column(String(64))
    state: Mapped[Optional[str]] = mapped_column(String(64))
    county: Mapped[Optional[str]] = mapped_column(String(64))

    # Personal data (per user)
    found: Mapped[bool] = mapped_column(Boolean, default=False)
    found_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    dnf: Mapped[bool] = mapped_column(Boolean, default=False)   # Did Not Find

    # ── Issue #33: GSAK-compatible user fields ────────────────────────────────

    # Derived from logs on import
    dnf_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    first_to_find: Mapped[bool] = mapped_column(Boolean, default=False)

    # Personal flags / sorting
    user_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    user_sort: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Four free-text user data fields (same as GSAK UserData1–4)
    user_data_1: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_data_2: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_data_3: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_data_4: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Computed distance and bearing from database centre point
    # Recalculated whenever the centre point changes (None until set)
    distance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # km
    bearing: Mapped[Optional[float]] = mapped_column(Float, nullable=True)    # degrees 0–360

    # Community favourite points count (API-only — None until API available)
    favorite_points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Issue #469: GSAK database import — schema additions ─────────────────
    # Added ahead of the GSAK importer so the mapping code can target the
    # final schema directly. See schema PR description for full rationale.

    # GC.com's own synced personal cache note (distinct from UserNote.note,
    # which covers GSAK's/OpenSAK's *local* note).
    gc_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Cache listing URL (not stored anywhere before this).
    url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Elevation in meters. None = not yet computed (0m is a real, valid
    # elevation at sea level, so it must stay distinguishable from "unknown").
    # Filled in later by Fabio's reverse-geocoding elevation mechanism.
    elevation: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)

    # User-assigned color tag (hex, e.g. "#FF5733"). Column only for now —
    # the set/display UI feature is tracked separately.
    color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, default=None)

    # GC.com listing GUID.
    guid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Watchlist status.
    watch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # GC.com's internal numeric cache ID (CacheId in GSAK).
    gc_cache_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # ── Issue #517: GC API field prep ────────────────────────────────────────
    # Total number of "Found it" logs on the cache (by anyone), as shown on
    # the listing page — the API's `findCount`. Distinct from our own
    # `log_count` (every log of every type). Also available directly from
    # GSAK (Caches.FoundCount), so populated by the GSAK importer already —
    # unlike premiumFavoriteScore/hasSolutionChecker/backgroundImageUrl,
    # which have no data source until GC API access exists.
    find_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Issue #87: Cached log count ──────────────────────────────────────────
    # Number of logs in this cache, cached as a column so the UI can display
    # the count without loading the logs relationship (which is noload'ed
    # for performance). Updated automatically on import in _upsert_cache().
    log_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Issue #489/#491: Cached trackable count ──────────────────────────────
    # Number of trackables (travel bugs / geocoins) in this cache, cached as
    # a column so the "Trackables" table column can display the count
    # without loading the trackables relationship for every row (mirrors
    # log_count above). Updated automatically on import in _upsert_cache().
    trackable_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Issue #186: Cached latest log date ───────────────────────────────────
    # Date of the most recent log entry, cached so the UI can display it
    # without loading the noload'ed logs relationship. Updated on import.
    last_log_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # ── Issue #377: Cached waypoint count ────────────────────────────────────
    # Number of child waypoints (parking, stages, etc.), cached so the grid
    # can show a visual cue without loading the noload'ed waypoints relation.
    # Updated on import in _insert_extra_wpts() and _link_extra_waypoints().
    waypoint_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Custom waypoint link (issue #141)
    # For CW... entries: optionally links to a parent geocache's gc_code.
    # NULL for all real geocaches imported from GPX/PQ.
    parent_gc_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)

    # ── Issue #202: Lock a cache ─────────────────────────────────────────────
    # When True, _upsert_cache() skips overwriting the scalar GPX-sourced
    # fields (name, type, container, coordinates, D/T, owner, status,
    # descriptions, hint, country/state/county) on re-import. Lets users
    # preserve a cache's data as it was when found, even if the listing
    # changes later (e.g. a difficulty rerate). Logs/attributes/waypoints are
    # not affected — they're rebuilt fresh on every import regardless.
    locked: Mapped[bool] = mapped_column(Boolean, default=False)

    # Location provenance (issue #60 — reverse-geocoding phase 3)
    # 'groundspeak' = taken from GPX import, 'computed' = offline boundary engine
    location_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # 'posted' = original coordinates used, 'corrected' = user-solved coords used
    location_basis: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    location_updated: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # boundaries dataset version string (e.g. "2025-06-01") used for the last resolve
    location_dataset: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Metadata
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    source_file: Mapped[Optional[str]] = mapped_column(String(512))  # which GPX this came from

    # Relationships
    waypoints: Mapped[List["Waypoint"]] = relationship(
        "Waypoint", back_populates="cache", cascade="all, delete-orphan"
    )
    logs: Mapped[List["Log"]] = relationship(
        "Log", back_populates="cache", cascade="all, delete-orphan",
        order_by="Log.log_date.desc()"
    )
    attributes: Mapped[List["Attribute"]] = relationship(
        "Attribute", back_populates="cache", cascade="all, delete-orphan"
    )
    trackables: Mapped[List["Trackable"]] = relationship(
        "Trackable", back_populates="cache", cascade="all, delete-orphan"
    )
    user_note: Mapped[Optional["UserNote"]] = relationship(
        "UserNote", back_populates="cache", cascade="all, delete-orphan",
        uselist=False
    )

    def __repr__(self) -> str:
        return f"<Cache {self.gc_code!r} {self.name!r}>"


# ── Waypoint ─────────────────────────────────────────────────────────────────

class Waypoint(Base):
    """
    Additional waypoints belonging to a cache — parking areas, trailheads,
    multi-cache stages, final coordinates, etc.
    """
    __tablename__ = "waypoints"
    __table_args__ = (
        UniqueConstraint("cache_id", "prefix", "name", name="uq_waypoint_cache_prefix_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_id: Mapped[int] = mapped_column(ForeignKey("caches.id"), nullable=False, index=True)

    prefix: Mapped[str] = mapped_column(String(8))       # PK, SB, S1, S2 …
    wp_type: Mapped[str] = mapped_column(String(64))     # Parking Area, Trailhead, Stage …
    name: Mapped[Optional[str]] = mapped_column(String(256))
    description: Mapped[Optional[str]] = mapped_column(Text)
    comment: Mapped[Optional[str]] = mapped_column(Text)

    latitude: Mapped[Optional[float]] = mapped_column(Float)   # None = coordinates not yet known
    longitude: Mapped[Optional[float]] = mapped_column(Float)

    # GC code of the parent cache (issue #376 — mirrors cParent in GSAK)
    parent_gc_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)

    # ── Issue #469: GSAK database import — schema additions ─────────────────

    # The waypoint's own GC-style code (currently only prefix + name stored).
    wp_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    wp_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # True if the user created this waypoint locally rather than it coming
    # from a GPX/GSAK import.
    created_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Generic user flag on the waypoint (mirrors GSAK's per-waypoint flag).
    wp_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    cache: Mapped["Cache"] = relationship("Cache", back_populates="waypoints")

    def __repr__(self) -> str:
        return f"<Waypoint {self.prefix!r} for cache_id={self.cache_id}>"


# ── Log ──────────────────────────────────────────────────────────────────────

class Log(Base):
    """A single log entry on a cache."""
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_id: Mapped[int] = mapped_column(ForeignKey("caches.id"), nullable=False, index=True)

    log_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)  # gc.com log GUID
    log_type: Mapped[str] = mapped_column(String(64))    # Found it, Didn't find it, Note …
    log_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    finder: Mapped[Optional[str]] = mapped_column(String(128))
    finder_id: Mapped[Optional[str]] = mapped_column(String(64))
    text: Mapped[Optional[str]] = mapped_column(Text)
    text_encoded: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Issue #469: GSAK database import — schema additions ─────────────────
    # Some logs (notably Earthcache logs) carry their own coordinates,
    # distinct from the cache's coordinates.
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # True if this log was made by the cache owner (e.g. maintenance notes,
    # "Enable Listing" logs) — mirrors GSAK's LogIsOwner-derived flag.
    logged_by_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    cache: Mapped["Cache"] = relationship("Cache", back_populates="logs")

    def __repr__(self) -> str:
        return f"<Log {self.log_type!r} by {self.finder!r} on {self.log_date}>"


# ── Attribute ─────────────────────────────────────────────────────────────────

class Attribute(Base):
    """
    A single attribute tag on a cache (e.g. 'Dogs allowed', 'Night cache').
    The attribute_id matches the Groundspeak attribute ID scheme.
    """
    __tablename__ = "attributes"
    __table_args__ = (
        UniqueConstraint("cache_id", "attribute_id", name="uq_attribute_cache"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_id: Mapped[int] = mapped_column(ForeignKey("caches.id"), nullable=False, index=True)

    attribute_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(128))
    is_on: Mapped[bool] = mapped_column(Boolean, default=True)  # True = yes, False = no

    # Relationships
    cache: Mapped["Cache"] = relationship("Cache", back_populates="attributes")

    def __repr__(self) -> str:
        flag = "+" if self.is_on else "-"
        return f"<Attribute {flag}{self.name!r}>"


# ── Trackable ─────────────────────────────────────────────────────────────────

class Trackable(Base):
    """A trackable item (Travel Bug, geocoin) recorded as present in a cache."""
    __tablename__ = "trackables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_id: Mapped[int] = mapped_column(ForeignKey("caches.id"), nullable=False, index=True)

    tracking_code: Mapped[Optional[str]] = mapped_column(String(64))
    name: Mapped[Optional[str]] = mapped_column(String(256))
    ref: Mapped[Optional[str]] = mapped_column(String(64))   # TB ref e.g. TB1234

    # Relationships
    cache: Mapped["Cache"] = relationship("Cache", back_populates="trackables")

    def __repr__(self) -> str:
        return f"<Trackable {self.ref!r} {self.name!r}>"


# ── UserNote ──────────────────────────────────────────────────────────────────

class UserNote(Base):
    """
    Personal notes for a cache — one note per cache, owned by the local user.

    corrected_lat / corrected_lon store user-solved coordinates (e.g. for
    mystery caches). is_corrected is set to True when corrected coords are
    present so they can be filtered and displayed distinctly in the UI.
    """
    __tablename__ = "user_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_id: Mapped[int] = mapped_column(ForeignKey("caches.id"), nullable=False, unique=True)

    note: Mapped[Optional[str]] = mapped_column(Text)

    # Corrected coordinates (user-solved, e.g. mystery cache finals)
    corrected_lat: Mapped[Optional[float]] = mapped_column(Float)
    corrected_lon: Mapped[Optional[float]] = mapped_column(Float)
    is_corrected: Mapped[bool] = mapped_column(Boolean, default=False)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    cache: Mapped["Cache"] = relationship("Cache", back_populates="user_note")

    def __repr__(self) -> str:
        return f"<UserNote for cache_id={self.cache_id}>"
