"""tools/demo_db/make_demo_db.py — a synthetic, anonymous OpenSAK database.

Dev-only: for trying macros, reports, screenshots and bug reproductions
without sharing a real database. Nothing comes from real data: GC codes,
names, owners and finders are made up. Coordinates are random points around
a centre (default Zürich): most caches close by, fewer the further away,
some anywhere in the world. Fixed seed, so every run gives the same database.

    python tools/demo_db/make_demo_db.py [target.db] [--caches 5000] [--found 2000]
        [--center LAT,LON] [--radius KM] [--seed N] [--boundaries DIR]

Needs the dev install (``pip install -e ".[dev]"``). Settings are redirected
to a temporary folder, so the real OpenSAK settings are never touched.
See tools/demo_db/README.md.
"""

import argparse
import bisect
import math
import os
import random
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

DEMO_DIR = Path(tempfile.gettempdir()) / "opensak-demo"
DEFAULT_TARGET = DEMO_DIR / "OpenSAK_Demo.db"
ZURICH = (47.3769, 8.5417)


def lat_lon(text: str) -> tuple[float, float]:
    try:
        lat, lon = (float(v) for v in text.replace(";", ",").split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected LAT,LON, got {text!r}")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise argparse.ArgumentTypeError(f"{text!r} is not a valid coordinate")
    return lat, lon


parser = argparse.ArgumentParser(description="Create a synthetic, anonymous OpenSAK database.")
parser.add_argument("target", nargs="?", type=Path, default=DEFAULT_TARGET)
parser.add_argument("--caches", type=int, default=5000)
parser.add_argument("--found", type=int, default=2000)
parser.add_argument("--center", type=lat_lon, default=ZURICH, metavar="LAT,LON",
                    help="home of the demo cacher, decimal degrees (default: Zürich). "
                         "Use --center=-33.92,18.42 for a negative latitude.")
parser.add_argument("--radius", type=float, default=25.0, metavar="KM",
                    help="half of the caches lie within this distance of the centre (default: 25)")
parser.add_argument("--seed", type=int, default=20261017)
parser.add_argument("--boundaries", type=Path, metavar="DIR",
                    help="OpenSAK boundaries folder (default: the one of your OpenSAK install)")
args = parser.parse_args()
if args.radius <= 0:
    parser.error("--radius must be positive")


def installed_boundaries() -> Path | None:
    # Ask a separate process, so this one never imports OpenSAK with the real settings.
    out = subprocess.run(
        [sys.executable, "-c",
         "from opensak.settings_store import get_install_dir; print(get_install_dir())"],
        capture_output=True, text=True,
    )
    lines = out.stdout.strip().splitlines()
    if out.returncode or not lines:
        return None
    path = Path(lines[-1]) / "boundaries"
    return path if (path / "boundaries.db").is_file() else None


boundaries_dir: Path | None = args.boundaries or installed_boundaries()

# Isolate OpenSAK's settings before importing it.
_iso = Path(tempfile.mkdtemp(prefix="opensak-demo-settings-"))
for var in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
    os.environ[var] = str(_iso)
os.environ["HOME"] = os.environ["USERPROFILE"] = str(_iso)

from opensak.db.database import dispose_engine, init_db, session_for  # noqa: E402
from opensak.db.models import Cache, Log  # noqa: E402
from opensak.geo.boundaries import TerritoryResolver  # noqa: E402
from opensak.geo.store import BoundaryStore, ensure_baseline_seeded  # noqa: E402

rng = random.Random(args.seed)
target: Path = args.target.resolve()
if target.exists():
    sys.exit(f"{target} exists already - delete it first or pass another path")
target.parent.mkdir(parents=True, exist_ok=True)

ME = "DemoCacher"
NOW = datetime(2026, 10, 1)

CENTER: tuple[float, float] = args.center
EARTH_KM = 6371.0
# Distance from the centre follows a Lomax (Pareto II) curve: dense near home,
# a long tail to the other side of the world. Smaller ALPHA = longer tail.
ALPHA = 0.9
LOMAX_SCALE = args.radius / (2 ** (1 / ALPHA) - 1)  # so that the median is --radius
MAX_KM = math.pi * EARTH_KM  # half the circumference: the antipode


def distance_km() -> float:
    while True:
        d = LOMAX_SCALE * ((1 - rng.random()) ** (-1 / ALPHA) - 1)
        if d <= MAX_KM:
            return d


def destination(lat: float, lon: float, d_km: float, bearing: float) -> tuple[float, float]:
    # Great-circle point d_km away from (lat, lon) in direction bearing (radians).
    phi1, lam1, delta = math.radians(lat), math.radians(lon), d_km / EARTH_KM
    phi2 = math.asin(math.sin(phi1) * math.cos(delta)
                     + math.cos(phi1) * math.sin(delta) * math.cos(bearing))
    lam2 = lam1 + math.atan2(math.sin(bearing) * math.sin(delta) * math.cos(phi1),
                             math.cos(delta) - math.sin(phi1) * math.sin(phi2))
    return math.degrees(phi2), (math.degrees(lam2) + 540) % 360 - 180


def open_resolver() -> TerritoryResolver | None:
    # Country/state names and the land check come from OpenSAK's own boundary
    # data. Without an install, download the baseline once into the demo folder.
    data_dir = boundaries_dir or DEMO_DIR / "boundaries"
    try:
        ensure_baseline_seeded(data_dir)
    except Exception as exc:  # no network etc.
        print(f"Boundary data unavailable ({exc})")
    store = BoundaryStore(data_dir)
    if not store.available():
        print("No boundary data: caches may lie in the sea and have no country/state.")
        return None
    return TerritoryResolver(store)


resolver = open_resolver()


def place() -> tuple[float, float, float, str | None, str | None]:
    """A random point on land: (lat, lon, km from centre, country, state)."""
    fallback: tuple[float, float, float, str | None, str | None] | None = None
    stateless = 0
    for _ in range(200):  # a centre far out at sea needs many tries
        d = distance_km()
        lat, lon = destination(CENTER[0], CENTER[1], d, rng.uniform(0, 2 * math.pi))
        lat, lon = round(lat, 5), round(lon, 5)
        if resolver is None:
            return lat, lon, d, None, None
        # Only country and state: resolving counties would download county packs.
        country = resolver._resolve_layer("country", lat, lon)
        if not country:
            continue
        state = resolver._resolve_layer("state", lat, lon)
        if state:
            return lat, lon, d, country, state
        # State polygons are coarser along coasts, and some countries have
        # none: take a country-only point when a few more tries find no state.
        fallback = fallback or (lat, lon, d, country, None)
        stateless += 1
        if stateless >= 10:
            return fallback
    return fallback or (lat, lon, d, None, None)


TYPES = [  # (type, weight, D mean, T mean, containers)
    ("Traditional Cache", 55, 1.7, 1.9, ("Micro", "Small", "Small", "Regular", "Regular", "Large", "Other")),
    ("Unknown Cache", 19, 3.0, 2.0, ("Micro", "Small", "Small", "Regular", "Other", "Not chosen")),
    ("Multi-cache", 10, 2.3, 2.4, ("Small", "Regular", "Regular", "Large", "Micro")),
    ("Earthcache", 4, 2.2, 2.2, ("Other",)),
    ("Letterbox Hybrid", 3, 2.4, 2.2, ("Small", "Regular")),
    ("Event Cache", 4, 1.0, 1.2, ("Other",)),
    ("Virtual Cache", 1.5, 1.8, 1.8, ("Other",)),
    ("Wherigo Cache", 1.5, 2.2, 2.0, ("Small", "Regular")),
    ("Cache In Trash Out Event", 0.6, 1.0, 1.8, ("Other",)),
    ("Mega-Event Cache", 0.2, 1.0, 1.0, ("Other",)),
    ("Webcam Cache", 0.2, 1.2, 1.2, ("Other",)),
]

SYL = ["ka", "lo", "mi", "ra", "ten", "bur", "gen", "sel", "wald", "berg", "tal", "see",
       "ho", "lin", "dorf", "brü", "ell", "fin", "ost", "ach", "rie", "mar", "zor", "wil"]
WORDS = ["Bridge", "Mill", "Forest", "View", "Chapel", "Tower", "Creek", "Rock", "Path",
         "Garden", "Quarry", "Ridge", "Lake", "Castle", "Ruin", "Oak", "Station", "Bench",
         "Fountain", "Meadow", "Cave", "Spring", "Bunker", "Lookout", "Orchard", "Crossing"]
THEMES = ["Puzzle", "Riddle", "Sudoku", "Cipher", "Night", "Kids", "History", "Nature",
          "Power Trail", "Bonus", "Series", "Quickie", "Picnic"]


def made_up_word() -> str:
    return "".join(rng.choice(SYL) for _ in range(rng.choice((2, 2, 3)))).capitalize()


def code_for(n: int) -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRTVWXYZ"
    n += 40_000  # GC codes start at 4 characters after "GC"
    out = ""
    while n:
        n, r = divmod(n, len(alphabet))
        out = alphabet[r] + out
    return "GC" + out


def dt_value(mean: float) -> float:
    v = rng.gauss(mean, 0.8)
    return min(5.0, max(1.0, round(v * 2) / 2))


def hidden_date() -> datetime:
    # More caches every year, from 2001 to 2026.
    while True:
        year = 2001 + int(25 * math.sqrt(rng.random()))
        if year <= 2026:
            break
    start = datetime(year, 1, 1)
    days = (min(datetime(year + 1, 1, 1), NOW) - start).days
    return start + timedelta(days=rng.randrange(days))


def owner_name() -> str:
    word = made_up_word()
    if rng.random() < 0.4:
        word += rng.choice(["", "_", "-"]) + str(rng.randrange(10, 99))
    return word


owners = sorted({owner_name() for _ in range(900)})
rng.shuffle(owners)
owner_weights = [1 / (i + 4) ** 0.8 for i in range(len(owners))]
others = [f"{made_up_word()}{rng.randrange(1, 999)}" for _ in range(400)]

km_from_home: dict[str, float] = {}  # gc_code -> distance from --center
type_weights = [t[1] for t in TYPES]


def make_cache(i: int) -> Cache:
    lat, lon, km_from_home[code_for(i)], country, state = place()
    ctype, _, dmean, tmean, containers = rng.choices(TYPES, type_weights)[0]
    hidden = hidden_date()
    event = "Event" in ctype
    name = rng.choice([
        f"{made_up_word()} {rng.choice(WORDS)}",
        f"{rng.choice(WORDS)} {made_up_word()}",
        f"{rng.choice(THEMES)}: {made_up_word()} #{rng.randrange(1, 40)}",
        f"{made_up_word()}er {rng.choice(WORDS)} {rng.randrange(1, 12)}",
    ])
    archived = rng.random() < (0.05 + 0.2 * (2026 - hidden.year) / 25 + (0.5 if event else 0))
    age_years = max((NOW - hidden).days / 365, 0.1)
    finds = 0 if event else int(rng.lognormvariate(math.log(8 * age_years ** 0.8), 0.9))
    favs = int(finds * rng.betavariate(1.2, 12) * (1.6 if ctype != "Traditional Cache" else 1))
    return Cache(
        gc_code=code_for(i), name=name, cache_type=ctype,
        container=rng.choice(containers),
        latitude=lat, longitude=lon,
        difficulty=1.0 if event else dt_value(dmean),
        terrain=1.0 if event else dt_value(tmean),
        owner_name=(owner := rng.choices(owners, owner_weights)[0]), placed_by=owner,
        owner_id=str(rng.randrange(10**6, 10**7)),
        hidden_date=hidden, last_updated=NOW, imported_at=NOW, last_gpx_update=NOW,
        available=archived or rng.random() > 0.04, archived=archived,
        premium_only=rng.random() < 0.15,
        short_description="Synthetic demo cache.", long_description="Synthetic demo cache — no real listing.",
        encoded_hints=rng.choice(["", "Magnetisch", "Unter dem Stein", "Baumhöhle", "Look up"]),
        country=country, state=state, county=None,
        favorite_points=favs, find_count=finds,
        elevation=round(min(4500.0, rng.lognormvariate(math.log(350), 0.8))),
        url=f"https://coord.info/{code_for(i)}",
        found=False,
    )


with_dates: list[Cache] = []
init_db(target)
with session_for(target) as s:
    caches = [make_cache(i) for i in range(args.caches)]
    # Finds: easier caches, the ones near home and non-archived ones more often.
    def find_weight(c: Cache) -> float:
        w = 1.0
        # Most finds near home, the others on trips.
        w /= 1 + km_from_home[c.gc_code] / (4 * args.radius)
        w *= 2.0 if c.cache_type == "Traditional Cache" else 1.0
        w *= 1.5 if c.cache_type.endswith("Event") or "Event" in c.cache_type else 1.0
        w /= 1 + (c.difficulty + c.terrain - 2) * 0.35
        w *= 0.6 if c.archived else 1.0
        return w

    pool = caches[:]
    weights = [find_weight(c) for c in pool]
    found: list[Cache] = []
    chosen = set()
    while len(found) < min(args.found, len(pool)):
        i = rng.choices(range(len(pool)), weights)[0]
        if i not in chosen:
            chosen.add(i)
            found.append(pool[i])

    # Find days: a caching career from 2009, more days on weekends, several
    # finds on most days.
    start = datetime(2009, 5, 1)
    days: list[datetime] = []
    d = start
    while d < NOW:
        weekend = d.weekday() >= 5
        growth = 0.4 + (d - start).days / (NOW - start).days
        if rng.random() < (0.32 if weekend else 0.06) * growth:
            days.append(d)
        d += timedelta(days=1)
    for c in found:
        # A find day after the cache was placed.
        first = bisect.bisect_left(days, c.hidden_date)
        day = rng.choice(days[first:]) if first < len(days) else days[-1]
        if day < c.hidden_date:
            day = c.hidden_date
        c.found = True
        c.found_date = day.replace(hour=rng.randrange(8, 20), minute=rng.randrange(60))
        c.first_to_find = rng.random() < 0.03
    for c in caches:
        if not c.found and rng.random() < 0.02:
            c.dnf, c.dnf_date = True, rng.choice(days)

    s.add_all(caches)
    s.flush()
    logs = 0
    for c in caches:
        entries = []
        for _ in range(min(c.find_count or 0, rng.randrange(0, 5))):
            when = c.hidden_date + timedelta(days=rng.randrange(1, max(2, (NOW - c.hidden_date).days)))
            entries.append(("Found it", when, rng.choice(others)))
        if c.found:
            entries.append(("Attended" if "Event" in c.cache_type else "Found it", c.found_date, ME))
        if c.dnf:
            entries.append(("Didn't find it", c.dnf_date, ME))
        if c.archived:
            entries.append(("Archive", NOW - timedelta(days=rng.randrange(1, 900)), c.owner_name))
        for log_type, when, finder in sorted(entries, key=lambda e: e[1]):
            s.add(Log(cache_id=c.id, log_type=log_type, log_date=when, finder=finder,
                      text="Synthetic demo log."))
            logs += 1
        c.log_count = len(entries)
        c.found_log_count = sum(1 for e in entries if e[0] in ("Found it", "Attended"))
        dates = [e[1] for e in entries]
        c.last_log_date = max(dates) if dates else None
        finds = [e[1] for e in entries if e[0] in ("Found it", "Attended")]
        c.last_found_date = max(finds) if finds else None
dispose_engine(target)
print(f"{len(caches)} caches ({len(found)} found by {ME}), {logs} logs -> {target}")
