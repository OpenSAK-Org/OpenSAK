"""
scripts/upgrade_fixture_generator.py — build an upgrade fixture with an OLD
OpenSAK version's own code (issue #997).

Not run directly: ``scripts/make_upgrade_fixture.py`` runs this file with the
old version's ``src`` on PYTHONPATH and HOME pointing into a throw-away
folder, so everything below writes data exactly the way that old version
does. It therefore only uses API that has been stable since v1.20.0
(DatabaseManager, the GPX importer, AppSettings, FilterProfile, the ORM
models and the plain settings store).

Usage (by make_upgrade_fixture.py):
    python upgrade_fixture_generator.py <root>

Writes into <root>:
    install/            the install folder (opensak.json, filters/, Default.db)
    databases/          the user's database folder
    backup/             one backup set of every database, as this version
                        writes it (only versions with backups, v1.21.0+)
    expected.json       what the upgraded data must look like afterwards
and bootstrap.json under $HOME as the old version would.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = ROOT / "work"
WORK.mkdir(parents=True, exist_ok=True)

# ── Test data ─────────────────────────────────────────────────────────────────

GS = 'xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1"'


def cache_wpt(code, name, lat, lon, ctype, d, t, *, logs=(), attrs=(),
              tbs=(), found=False, archived=False, country="Denmark"):
    log_xml = "".join(
        f'<groundspeak:log id="{lid}"><groundspeak:date>{date}</groundspeak:date>'
        f'<groundspeak:type>{ltype}</groundspeak:type>'
        f'<groundspeak:finder id="{lid}">{finder}</groundspeak:finder>'
        f'<groundspeak:text encoded="False">{text}</groundspeak:text></groundspeak:log>'
        for lid, date, ltype, finder, text in logs
    )
    attr_xml = "".join(
        f'<groundspeak:attribute id="{aid}" inc="{inc}">{aname}</groundspeak:attribute>'
        for aid, inc, aname in attrs
    )
    tb_xml = "".join(
        f'<groundspeak:travelbug id="{tid}" ref="{ref}">'
        f'<groundspeak:name>{tname}</groundspeak:name></groundspeak:travelbug>'
        for tid, ref, tname in tbs
    )
    sym = "Geocache Found" if found else "Geocache"
    return (
        f'<wpt lat="{lat}" lon="{lon}"><time>2020-05-01T00:00:00Z</time>'
        f"<name>{code}</name><desc>{name} by Owner</desc>"
        f"<url>https://coord.info/{code}</url><urlname>{name}</urlname>"
        f"<sym>{sym}</sym><type>Geocache|{ctype}</type>"
        f'<groundspeak:cache id="{int(code[4:]) + 1000}" '
        f'archived="{archived}" available="{not archived}" {GS}>'
        f"<groundspeak:name>{name}</groundspeak:name>"
        f"<groundspeak:placed_by>Owner</groundspeak:placed_by>"
        f'<groundspeak:owner id="42">Owner</groundspeak:owner>'
        f"<groundspeak:type>{ctype}</groundspeak:type>"
        f"<groundspeak:container>Small</groundspeak:container>"
        f"<groundspeak:attributes>{attr_xml}</groundspeak:attributes>"
        f"<groundspeak:difficulty>{d}</groundspeak:difficulty>"
        f"<groundspeak:terrain>{t}</groundspeak:terrain>"
        f"<groundspeak:country>{country}</groundspeak:country>"
        f"<groundspeak:state>Zealand</groundspeak:state>"
        f'<groundspeak:short_description html="False">Short {code}.</groundspeak:short_description>'
        f'<groundspeak:long_description html="True">&lt;p&gt;Long {code}.&lt;/p&gt;</groundspeak:long_description>'
        f"<groundspeak:encoded_hints>Hint {code}</groundspeak:encoded_hints>"
        f"<groundspeak:logs>{log_xml}</groundspeak:logs>"
        f"<groundspeak:travelbugs>{tb_xml}</groundspeak:travelbugs>"
        "</groundspeak:cache></wpt>"
    )


def child_wpt(code, lat, lon, kind="Parking Area", note="Park here."):
    return (
        f'<wpt lat="{lat}" lon="{lon}"><name>{code}</name><desc>{kind}</desc>'
        f"<sym>{kind}</sym><type>Waypoint|{kind}</type><cmt>{note}</cmt></wpt>"
    )


def gpx(*blocks):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<gpx version="1.0" creator="Groundspeak Pocket Query" '
        'xmlns="http://www.topografix.com/GPX/1/0">' + "".join(blocks) + "</gpx>"
    )


DATABASES = {
    "Denmark": {
        "gpx": gpx(
            cache_wpt("GCFX001", "Harbour Trad", 55.676, 12.568, "Traditional Cache", 1.5, 2.0,
                      logs=[(9001, "2026-01-15T10:30:00Z", "Found it", "Alice", "TFTC"),
                            (9002, "2025-12-01T09:00:00Z", "Didn't find it", "Bob", "No luck"),
                            (9003, "2025-06-01T09:00:00Z", "Found it", "Carol", "Nice")],
                      attrs=[(6, 1, "Recommended for kids"), (24, 0, "Wheelchair accessible")],
                      tbs=[(501, "TB1FX01", "Fixture Bug")]),
            cache_wpt("GCFX002", "Puzzle Park", 55.700, 12.500, "Unknown Cache", 4.0, 2.5,
                      logs=[(9004, "2025-10-10T12:00:00Z", "Found it", "Dave", "Solved")]),
            child_wpt("PKFX002", 55.701, 12.501),
            child_wpt("S1FX002", 55.702, 12.502, "Physical Stage", "Stage 1"),
            cache_wpt("GCFX003", "Locked Multi", 55.650, 12.600, "Multi-cache", 3.0, 3.0,
                      logs=[(9005, "2024-03-03T08:00:00Z", "Found it", "Eve", "Long walk")]),
            cache_wpt("GCFX004", "Found Earthcache", 55.600, 12.400, "Earthcache", 2.0, 1.5,
                      found=True),
            cache_wpt("GCFX005", "Archived Trad", 55.620, 12.450, "Traditional Cache", 1.0, 1.0,
                      archived=True),
        ),
        "notes": {"GCFX001": "Bring a pen", "GCFX002": "Final solved",
                  "GCFX003": "Kept as is"},
        "corrected": {"GCFX002": (55.7123, 12.5234)},
        "locked": ["GCFX003"],
        "flagged": ["GCFX001"],
        "home": (55.1111, 11.2222),
        "sort": ("difficulty", False, "Easy traditionals"),
        "columns": ["gc_code", "name", "difficulty", "terrain"],
    },
    "Malta Trip": {
        "gpx": gpx(
            cache_wpt("GCFX101", "Valletta View", 35.898, 14.514, "Traditional Cache", 1.5, 1.5,
                      logs=[(9101, "2026-02-02T10:00:00Z", "Found it", "Frank", "Sunny")],
                      country="Malta"),
            cache_wpt("GCFX102", "Gozo Mystery", 36.044, 14.251, "Unknown Cache", 3.5, 2.0,
                      country="Malta"),
            child_wpt("PKFX102", 36.045, 14.252),
        ),
        "notes": {"GCFX102": "Malta note"},
        "corrected": {"GCFX102": (36.0501, 14.2601)},
        "locked": [],
        "flagged": ["GCFX101", "GCFX102"],
        "home": (35.8989, 14.5146),
        "sort": ("name", True, ""),
        "columns": ["gc_code", "name", "country"],
    },
}

GLOBAL_SETTINGS = {
    "gc_username": "FixtureCacher",
    "gc_finder_id": "PRFIXTURE",
    "use_miles": True,
    "theme": "dark",
}

# ── Build it with the old version's code ──────────────────────────────────────

from opensak.settings_store import (  # noqa: E402
    get_store, migrate_from_qsettings, repair_corrupted_bool_keys, reset_store,
    set_install_dir,
)

install_dir = ROOT / "install"
db_dir = ROOT / "databases"
db_dir.mkdir(parents=True, exist_ok=True)
set_install_dir(install_dir)  # what the Welcome Wizard does
reset_store()
store = get_store()
# The start-up steps the old app runs before any database is opened. HOME
# points into ROOT, so the QSettings check finds nothing and only sets its
# "already migrated" flag, as on a real 1.20.0 installation.
migrate_from_qsettings(store)
repair_corrupted_bool_keys(store)
store.set_many({"databases.dir": str(db_dir), "_wizard_completed": True})

from opensak.db.manager import get_db_manager  # noqa: E402
from opensak.db.database import get_session  # noqa: E402
from opensak.db.models import Cache, UserNote  # noqa: E402
from opensak.importer import import_gpx  # noqa: E402
from opensak.filters.engine import (  # noqa: E402
    CacheTypeFilter, DifficultyFilter, FilterProfile, FilterSet, HasCorrectedFilter,
)
from opensak.gui.settings import get_settings  # noqa: E402

try:  # v1.21.0+: per-database settings live in the database file (#659)
    from opensak.db import db_settings  # noqa: E402
except ImportError:
    db_settings = None  # type: ignore[assignment]
from opensak.utils.types import CoordFormat, DateFormat  # noqa: E402

settings = get_settings()
settings.date_format = DateFormat.DMY
settings.coord_format = CoordFormat.DD
for key, value in GLOBAL_SETTINGS.items():
    setattr(settings, key, value)

manager = get_db_manager()
expected: dict = {"databases": {}, "profiles": [], "global": {}}

for name, spec in DATABASES.items():
    info = manager.new_database(name)
    manager.switch_to(info)

    gpx_file = WORK / f"{name}.gpx"
    gpx_file.write_text(spec["gpx"], encoding="utf-8")
    import_gpx(gpx_file)

    with get_session() as session:
        caches = {c.gc_code: c for c in session.query(Cache).all()}
        for code, text in spec["notes"].items():
            session.add(UserNote(cache_id=caches[code].id, note=text))
        session.flush()
        for code, (lat, lon) in spec["corrected"].items():
            note = session.query(UserNote).filter_by(cache_id=caches[code].id).one_or_none()
            if note is None:
                note = UserNote(cache_id=caches[code].id)
                session.add(note)
            note.corrected_lat, note.corrected_lon, note.is_corrected = lat, lon, True
        for code in spec["locked"]:
            caches[code].locked = True
        for code in spec["flagged"]:
            caches[code].user_flag = True
        session.commit()

    # Per-database settings, stored the way this release stores them: in
    # opensak.json up to v1.20.0, inside the database file from v1.21.0
    # (#659, opensak.db.db_settings).
    settings.home_lat, settings.home_lon = spec["home"]
    field, ascending, profile = spec["sort"]
    if db_settings is not None:
        db_settings.set_value("sort.field", None, field)
        db_settings.set_value("sort.ascending", None, ascending)
        if profile:
            db_settings.set_value("sort.filter_profile", None, profile)
        db_settings.set_value("columns.visible", None, spec["columns"])
    else:
        sort_key = f"sort.{info.path}"
        store.set_many({
            f"{sort_key}.field": field,
            f"{sort_key}.ascending": ascending,
            f"{sort_key}.filter_profile": profile,
        })
        from opensak.gui.dialogs.column_dialog import _col_key  # noqa: E402
        store.set(_col_key("visible"), spec["columns"])

    with get_session() as session:
        count = lambda sql: session.execute(__import__("sqlalchemy").text(sql)).scalar()  # noqa: E731
        expected["databases"][name] = {
            "file": info.path.name,
            "counts": {
                table: count(f"SELECT count(*) FROM {table}")
                for table in ("caches", "logs", "waypoints", "attributes",
                              "trackables", "user_notes")
            },
            "notes": spec["notes"],
            "corrected": {k: list(v) for k, v in spec["corrected"].items()},
            "locked": spec["locked"],
            "flagged": spec["flagged"],
            "db_settings": {
                "home_lat": spec["home"][0],
                "home_lon": spec["home"][1],
                "sort.field": field,
                "sort.ascending": ascending,
                **({"sort.filter_profile": profile} if profile else {}),
                "columns.visible": spec["columns"],
            },
        }

profiles = [
    FilterProfile("Easy traditionals", FilterSet(mode="AND")),
    FilterProfile("Solved mysteries", FilterSet(mode="AND")),
]
profiles[0].filterset.add(CacheTypeFilter(["Traditional Cache"]))
profiles[0].filterset.add(DifficultyFilter(1.0, 2.0))
profiles[1].filterset.add(HasCorrectedFilter())
for p in profiles:
    p.save()
    expected["profiles"].append(p.name)

# The database that is active when the user quits 1.20.0.
denmark = next(d for d in manager.databases if d.name == "Denmark")
manager.switch_to(denmark)

expected["active"] = denmark.name
expected["schema_version_before"] = __import__("sqlite3").connect(
    str(denmark.path)).execute("PRAGMA user_version").fetchone()[0]
expected["global"] = {
    "date_format": settings.date_format.value,
    "coord_format": settings.coord_format.value,
    **{key: getattr(settings, key) for key in GLOBAL_SETTINGS},
}
from opensak import __version__  # noqa: E402
expected["generated_by"] = __version__

from opensak.db.database import dispose_engine  # noqa: E402
dispose_engine()

# #1193: a backup set as this version writes it, so the tests can restore an
# old set with the current code. Backups exist since v1.21.0 (#942).
try:
    from opensak.backup.backupset import KIND_MANUAL, write_backup_set
except ImportError:
    pass
else:
    from datetime import datetime, timezone
    result = write_backup_set(
        manager.databases, KIND_MANUAL, folder=ROOT / "backup",
        now=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc), compress=False,
    )
    expected["backup"] = result.backup_set.path.name

(ROOT / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
print(json.dumps({"install_dir": str(install_dir), "db_dir": str(db_dir)}))
