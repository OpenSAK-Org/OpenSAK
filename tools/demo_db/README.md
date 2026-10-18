# Demo database generator (dev only)

`make_demo_db.py` builds a synthetic, anonymous OpenSAK database. Use it to try
macros, reports and filters, take screenshots, or reproduce a bug without
sharing your own database. It is not shipped with OpenSAK.

## Usage

From the repository root, with the dev install (`pip install -e ".[dev]"`):

```bash
python tools/demo_db/make_demo_db.py                       # <temp>/opensak-demo/OpenSAK_Demo.db
python tools/demo_db/make_demo_db.py path/to/Demo.db       # your own target
python tools/demo_db/make_demo_db.py Demo.db --caches 20000 --found 5000 --seed 1
python tools/demo_db/make_demo_db.py Demo.db --center 52.52,13.40 --radius 40   # around Berlin
python tools/demo_db/make_demo_db.py Demo.db --center=-33.92,18.42              # negative latitude: use "="
```

| Option | Default | Meaning |
|---|---|---|
| `target` | `<temp>/opensak-demo/OpenSAK_Demo.db` | Database file to create. Must not exist yet. |
| `--caches` | 5000 | Number of caches. |
| `--found` | 2000 | How many of them the demo user (`DemoCacher`) has found. |
| `--center LAT,LON` | `47.3769,8.5417` (Zürich) | Home of the demo cacher, decimal degrees. Most caches and finds are around it. |
| `--radius KM` | 25 | Half of the caches lie within this distance of the centre. |
| `--seed` | 20261017 | Random seed. The same seed and boundary data give the same database. |
| `--boundaries DIR` | your install's `boundaries` folder | OpenSAK boundary data, used for the land check and country/state names. |

Open the result in OpenSAK with **File → Manage databases… → Open existing…**.

## What it contains

- **Caches** around the centre, worldwide: the further away, the fewer. The
  distance follows a long-tailed curve: with the defaults about half lie
  within 25 km, ~80 % within 100 km, ~4 % beyond 500 km and a few dozen on
  other continents. Realistic mixes of cache types, containers, D/T, hidden
  dates (2001–2026, more in recent years), archived/disabled state, premium
  flag, favourite points, find counts and elevation.
- **Finds** by `DemoCacher`: a caching career from 2009, mostly near the
  centre, the rest on trips, more on weekends, some FTFs and DNFs.
- **Logs**: a few `Found it` logs by made-up finders per cache, the demo
  user's own `Found it`/`Attended`/`Didn't find it` logs, and `Archive` logs.

## What is synthetic

Everything. GC codes, cache names, owner and finder names, owner IDs, hints,
descriptions and log texts are generated; coordinates are random points
around the centre. No real geocaching data is read or copied.

## Land check and country/state

Each point is looked up in OpenSAK's own boundary data (the same data the app
uses to fill country/state). Points in the sea are dropped and drawn again,
and country and state come from the boundaries. Counties are left empty —
looking them up would download county packs.

The boundary folder is taken from your OpenSAK install. If there is none, the
baseline is downloaded once to `<temp>/opensak-demo/boundaries`. Without
boundary data (offline, no install) the script still runs, but caches can lie
in the sea and have no country/state. With a centre far out at sea a few
caches can end up in the sea anyway.

## Safety

- The script refuses to overwrite an existing file.
- OpenSAK's settings directories (`APPDATA`, `XDG_*`, `HOME`) are pointed at a
  temporary folder before OpenSAK is imported, so your real settings and
  database list are never touched.
- The schema comes from `opensak.db.database.init_db`, so the database always
  matches the code you run it from. When the `Cache`/`Log` models change, the
  script may need updating.
