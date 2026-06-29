"""
src/opensak/gui/settings.py — Application settings.

Fra 1.14.0 (issue #209): al state gemmes i opensak.json via settings_store
i stedet for QSettings. API'et er identisk med det gamle for at undgå
ændringer i alle de steder der kalder get_settings().
"""

from __future__ import annotations
import json
import base64
from opensak.utils.types import CoordFormat, DateFormat, TextSize
from opensak.settings_store import get_store


# ── Hjemmepunkt dataklasse ────────────────────────────────────────────────────

class HomePoint:
    """Et navngivet hjemmepunkt."""

    def __init__(self, name: str, lat: float, lon: float):
        self.name = name
        self.lat  = lat
        self.lon  = lon

    def to_dict(self) -> dict:
        return {"name": self.name, "lat": self.lat, "lon": self.lon}

    @staticmethod
    def from_dict(d: dict) -> "HomePoint":
        return HomePoint(d["name"], float(d["lat"]), float(d["lon"]))

    def __repr__(self) -> str:
        return f"HomePoint({self.name!r}, {self.lat}, {self.lon})"


class AppSettings:
    """
    Typed getters/setters over settings_store.

    Drop-in erstatning for den gamle QSettings-baserede version.
    Bruger prik-separerede nøgler: "user.gc_username", "display.theme" osv.
    """

    # ── Per-database nøgle-prefix ─────────────────────────────────────────────

    def _db_prefix(self) -> str:
        """Returner en unik prefix per aktiv database til per-db settings."""
        try:
            from opensak.db.manager import get_db_manager
            manager = get_db_manager()
            if manager.active:
                safe = str(manager.active.path).replace("/", "_").replace("\\", "_")
                return f"db.{safe}"
        except Exception:
            pass
        return "db.default"

    def _db_key(self, key: str) -> str:
        return f"{self._db_prefix()}.{key}"

    # ── Home location (per database) ──────────────────────────────────────────

    @property
    def home_lat(self) -> float:
        s = get_store()
        val = s.get(self._db_key("home_lat"))
        if val is not None and val != "":
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
        try:
            return float(s.get("location.home_lat", 55.6761))
        except (TypeError, ValueError):
            return 55.6761

    @home_lat.setter
    def home_lat(self, value: float) -> None:
        s = get_store()
        s.set_many({
            self._db_key("home_lat"): value,
            "location.home_lat":      value,
        })

    @property
    def home_lon(self) -> float:
        s = get_store()
        val = s.get(self._db_key("home_lon"))
        if val is not None and val != "":
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
        try:
            return float(s.get("location.home_lon", 12.5683))
        except (TypeError, ValueError):
            return 12.5683

    @home_lon.setter
    def home_lon(self, value: float) -> None:
        s = get_store()
        s.set_many({
            self._db_key("home_lon"): value,
            "location.home_lon":      value,
        })

    # ── Globale hjemmepunkter (liste) ─────────────────────────────────────────

    @property
    def home_points(self) -> list[HomePoint]:
        """Global liste — ★ Home øverst, derefter User Locations."""
        raw = get_store().get("homepoints.list")
        user_points: list[HomePoint] = []
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                user_points = [HomePoint.from_dict(d) for d in data
                               if d.get("name") != "★ Home"]
            except Exception:
                pass
        home = self.get_gc_home_point()
        return ([home] + user_points) if home else user_points

    @home_points.setter
    def home_points(self, points: list[HomePoint]) -> None:
        filtered = [p for p in points if p.name != "★ Home"]
        get_store().set("homepoints.list", [p.to_dict() for p in filtered])

    @property
    def active_home_name(self) -> str:
        """Navn på det aktive hjemmepunkt (per database, med global fallback)."""
        s = get_store()
        per_db = s.get(self._db_key("active_home_name"))
        if per_db is not None:
            return str(per_db)
        return str(s.get("homepoints.active_name", ""))

    @active_home_name.setter
    def active_home_name(self, value: str) -> None:
        s = get_store()
        s.set_many({
            self._db_key("active_home_name"): value,
            "homepoints.active_name":         value,
        })

    def set_active_home(self, point: HomePoint) -> None:
        """Sæt aktivt hjemmepunkt."""
        self.active_home_name = point.name
        self.home_lat = point.lat
        self.home_lon = point.lon

    def get_active_home(self) -> HomePoint | None:
        """Returner det aktive hjemmepunkt fra listen, eller None."""
        name = self.active_home_name
        for p in self.home_points:
            if p.name == name:
                return p
        return None

    def add_or_update_home_point(self, point: HomePoint) -> None:
        """Tilføj nyt hjemmepunkt eller opdatér eksisterende med samme navn."""
        points = self.home_points
        for i, p in enumerate(points):
            if p.name == point.name:
                points[i] = point
                self.home_points = points
                return
        points.append(point)
        self.home_points = points

    def remove_home_point(self, name: str) -> None:
        """Fjern hjemmepunkt med det givne navn."""
        self.home_points = [p for p in self.home_points if p.name != name]
        if self.active_home_name == name:
            self.active_home_name = ""

    # ── Geocaching brugernavn ─────────────────────────────────────────────────

    @property
    def gc_username(self) -> str:
        return str(get_store().get("user.gc_username", ""))

    @gc_username.setter
    def gc_username(self, value: str) -> None:
        get_store().set("user.gc_username", value.strip())

    @property
    def gc_finder_id(self) -> str:
        return str(get_store().get("user.gc_finder_id", ""))

    @gc_finder_id.setter
    def gc_finder_id(self, value: str) -> None:
        get_store().set("user.gc_finder_id", str(value).strip())

    @property
    def gc_home_location(self) -> str:
        return str(get_store().get("user.gc_home_location", ""))

    @gc_home_location.setter
    def gc_home_location(self, value: str) -> None:
        get_store().set("user.gc_home_location", value.strip())

    def get_gc_home_point(self) -> "HomePoint | None":
        """Parse gc_home_location til HomePoint, eller None."""
        raw = self.gc_home_location
        if not raw:
            return None
        try:
            from opensak.coords import parse_coords
            coord = parse_coords(raw)
            if coord is None:
                return None
            lat, lon = coord
            return HomePoint("★ Home", lat, lon)
        except Exception:
            return None

    # ── Theme / appearance ────────────────────────────────────────────────────

    @property
    def theme(self) -> str:
        return str(get_store().get("display.theme", "auto"))

    @theme.setter
    def theme(self, value: str) -> None:
        get_store().set("display.theme", value)

    # ── Units ─────────────────────────────────────────────────────────────────

    @property
    def distance_method(self) -> str:
        """Distance calculation method — 'haversine' (default) or 'vincenty'.

        Haversine matches Groundspeak's behaviour; Vincenty WGS84 is more
        accurate (~0.3 % improvement for long distances).
        """
        val = get_store().get("computation.distance_method", "haversine")
        return val if val in ("haversine", "vincenty") else "haversine"

    @distance_method.setter
    def distance_method(self, value: str) -> None:
        get_store().set("computation.distance_method", value)

    @property
    def use_miles(self) -> bool:
        val = get_store().get("display.use_miles", False)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes")

    @use_miles.setter
    def use_miles(self, value: bool) -> None:
        get_store().set("display.use_miles", bool(value))

    # ── Koordinatformat ───────────────────────────────────────────────────────

    @property
    def coord_format(self) -> CoordFormat:
        raw = get_store().get("display.coord_format", CoordFormat.DMM.value)
        try:
            return CoordFormat(raw)
        except ValueError:
            return CoordFormat.DMM

    @coord_format.setter
    def coord_format(self, value: CoordFormat) -> None:
        get_store().set("display.coord_format", CoordFormat(value).value)

    # ── Datoformat ────────────────────────────────────────────────────────────

    @property
    def date_format(self) -> DateFormat:
        raw = get_store().get("display.date_format", DateFormat.LOCALE.value)
        try:
            return DateFormat(raw)
        except ValueError:
            return DateFormat.LOCALE

    @date_format.setter
    def date_format(self, value: DateFormat) -> None:
        get_store().set("display.date_format", DateFormat(value).value)

    @property
    def text_size(self) -> TextSize:
        raw = get_store().get("display.text_size", TextSize.MEDIUM.value)
        try:
            return TextSize(raw)
        except ValueError:
            return TextSize.MEDIUM

    @text_size.setter
    def text_size(self, value: TextSize) -> None:
        get_store().set("display.text_size", TextSize(value).value)

    # ── Kort udbyder ──────────────────────────────────────────────────────────


    @property
    def map_provider(self) -> str:
        return str(get_store().get("display.map_provider", "google"))

    @map_provider.setter
    def map_provider(self, value: str) -> None:
        get_store().set("display.map_provider", value)

    def get_maps_url(self, lat: float, lon: float) -> str:
        if self.map_provider == "osm":
            return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=16"
        else:
            return f"https://www.google.com/maps?q={lat},{lon}"

    # ── Display ───────────────────────────────────────────────────────────────

    # ── Window state ──────────────────────────────────────────────────────────

    @property
    def window_geometry(self):
        raw = get_store().get("window.geometry")
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                return base64.b64decode(raw)
            except Exception:
                return None
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        return None

    @window_geometry.setter
    def window_geometry(self, value) -> None:
        if value is None:
            get_store().delete("window.geometry")
        else:
            try:
                b = bytes(value)  # works for bytes, bytearray, QByteArray
                get_store().set("window.geometry", base64.b64encode(b).decode())
            except Exception:
                pass

    @property
    def window_state(self):
        raw = get_store().get("window.state")
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                return base64.b64decode(raw)
            except Exception:
                return None
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        return None

    @window_state.setter
    def window_state(self, value) -> None:
        if value is None:
            get_store().delete("window.state")
        else:
            try:
                b = bytes(value)
                get_store().set("window.state", base64.b64encode(b).decode())
            except Exception:
                pass

    @property
    def splitter_state(self):
        raw = get_store().get("window.splitter_state")
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                return base64.b64decode(raw)
            except Exception:
                return None
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        return None

    @splitter_state.setter
    def splitter_state(self, value) -> None:
        if value is None:
            get_store().delete("window.splitter_state")
        else:
            try:
                b = bytes(value)
                get_store().set("window.splitter_state", base64.b64encode(b).decode())
            except Exception:
                pass

    @property
    def bottom_splitter_state(self):
        raw = get_store().get("window.bottom_splitter_state")
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                return base64.b64decode(raw)
            except Exception:
                return None
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        return None

    @bottom_splitter_state.setter
    def bottom_splitter_state(self, value) -> None:
        if value is None:
            get_store().delete("window.bottom_splitter_state")
        else:
            try:
                b = bytes(value)
                get_store().set("window.bottom_splitter_state", base64.b64encode(b).decode())
            except Exception:
                pass

    @property
    def splitter_ratio_top(self) -> float:
        val = get_store().get("window.splitter_ratio_top", 0.49)
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.49

    @splitter_ratio_top.setter
    def splitter_ratio_top(self, value: float) -> None:
        get_store().set("window.splitter_ratio_top", float(value))

    @property
    def bottom_splitter_ratio_left(self) -> float:
        val = get_store().get("window.bottom_splitter_ratio_left", 0.51)
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.51

    @bottom_splitter_ratio_left.setter
    def bottom_splitter_ratio_left(self, value: float) -> None:
        get_store().set("window.bottom_splitter_ratio_left", float(value))

    # ── Search thresholds ─────────────────────────────────────────────────────

    @property
    def search_min_chars(self) -> int:
        val = get_store().get("search.min_chars", 0)
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    @search_min_chars.setter
    def search_min_chars(self, value: int) -> None:
        get_store().set("search.min_chars", int(value))

    @property
    def search_debounce_ms(self) -> int:
        val = get_store().get("search.debounce_ms", 0)
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    @search_debounce_ms.setter
    def search_debounce_ms(self, value: int) -> None:
        get_store().set("search.debounce_ms", int(value))

    # ── Location refinement ───────────────────────────────────────────────────

    @property
    def nominatim_enabled(self) -> bool:
        val = get_store().get("location.nominatim_enabled", False)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes")

    @nominatim_enabled.setter
    def nominatim_enabled(self, value: bool) -> None:
        get_store().set("location.nominatim_enabled", bool(value))

    # ── Last used paths ───────────────────────────────────────────────────────

    @property
    def last_import_dir(self) -> str:
        from pathlib import Path
        return str(get_store().get("paths.last_import_dir", str(Path.home())))

    @last_import_dir.setter
    def last_import_dir(self, value: str) -> None:
        get_store().set("paths.last_import_dir", value)

    # ── Updates ───────────────────────────────────────────────────────────────

    @property
    def updates_check_enabled(self) -> bool:
        val = get_store().get("updates.check_enabled", True)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes")

    @updates_check_enabled.setter
    def updates_check_enabled(self, value: bool) -> None:
        get_store().set("updates.check_enabled", bool(value))

    @property
    def updates_skipped_version(self) -> str:
        return str(get_store().get("updates.skipped_version", ""))

    @updates_skipped_version.setter
    def updates_skipped_version(self, value: str) -> None:
        get_store().set("updates.skipped_version", value)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def apply_default_center_for_new_db(self) -> None:
        """Sæt ★ Home som centerpoint for ny DB hvis tilgængeligt."""
        home = self.get_gc_home_point()
        if home:
            self.set_active_home(home)

    def is_setup_complete(self) -> bool:
        has_username = bool(self.gc_username.strip())
        has_home = bool(self.gc_home_location.strip()) or bool(self.home_points)
        return has_username and has_home

    def sync(self) -> None:
        """Flush til disk — drop-in for QSettings.sync()."""
        get_store().sync()


# Module-level singleton
_settings: AppSettings | None = None


def get_settings() -> AppSettings:
    global _settings
    if _settings is None:
        _settings = AppSettings()
    return _settings
