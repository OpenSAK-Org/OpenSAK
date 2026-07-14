# Reverse-geocoding boundary data (`data/`)

The offline boundary engine (`src/opensak/geo/`, issue #60) reads its real
dataset from **`config.get_app_data_dir()/boundaries`** — seeded on first run
from either the PyInstaller-bundled baseline or a download from the published
**`OpenSAK-Org/OpenSAK-Data`** repository (`reverse-geocoding-v1` release), see
`geo/store.ensure_baseline_seeded()`.

A **local `data/` folder at the repo root** is still used as a dev/regeneration
workspace: it's where `tools/boundaries/gsak_to_opensak.py` reads its GSAK
source files (`bb.db3` + zips, gitignored) and writes its output, and it's
handy for hand-testing the engine against a throwaway dataset. It is **not**
an implicit runtime fallback any more — the app only reads it when
`OPENSAK_BOUNDARIES_DIR` explicitly points at it.

## Where the engine looks

`geo.store.default_data_dir()` resolves, in order:

1. `$OPENSAK_BOUNDARIES_DIR` if set (used by the tests, and by pointing it at
   the repo-root `data/` folder for local dev/hand-testing);
2. otherwise `config.get_app_data_dir() / "boundaries"` — the real per-user
   app-data directory.

So `OPENSAK_BOUNDARIES_DIR=/some/path python …` overrides it without touching
the tree.

## Expected layout

`data/` mirrors the real `<app-data>/boundaries/` layout from the
[architecture doc](../architecture/reverse-geocoding.md#53-on-disk-layout-and-caching):

```
data/
├── boundaries.db        # SQLite: per-layer R-Trees + region metadata
├── manifest.json        # dataset version; "baseline" (world.geojson + state packs)
│                         # and "packs" (county, on-demand) sections; every entry
│                         # also carries a sha256 + size for integrity checks
├── countries/
│   └── world.geojson    # baseline — one country FeatureCollection
├── states/
│   └── *.geojson        # baseline, one per country code (e.g. prt.geojson)
└── counties/
    └── <cc>_<pack>.geojson   # flat — one or more per country (e.g. prt_all_prt.geojson,
                                # usa_california.geojson); flat because GitHub Release
                                # assets can't hold subdirectories
```

## `boundaries.db` schema

One R-Tree plus one metadata table **per layer** (`country`, `state`, `county`),
joined by `id`, exactly as in [§5.2 of the architecture](../architecture/reverse-geocoding.md#52-the-bounding-box-database):

```sql
CREATE VIRTUAL TABLE rtree_<layer> USING rtree(id, min_lat, max_lat, min_lon, max_lon);

CREATE TABLE region_<layer> (
    id            INTEGER PRIMARY KEY,  -- == matching R-Tree id
    name          TEXT NOT NULL,        -- display value ('Lisboa')
    parent        TEXT,                 -- 'PT', 'US/TX' — links county→state→country
    pack          TEXT NOT NULL,        -- GeoJSON file under <layer-dir>/
    feature_index INTEGER NOT NULL,     -- which feature inside that pack
    poly_version  INTEGER NOT NULL,
    is_bundled    INTEGER NOT NULL      -- 1 baseline, 0 on-demand
);

CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER);
```

The engine resolves a point by: R-Tree query (`candidates`) → `region_<layer>`
row (`region`) → lazily load `<data-dir>/<layer-dir>/<pack>` and take
`features[feature_index].geometry` (`geometry`). The `<layer-dir>` is the plural
form: `country→countries`, `state→states`, `county→counties`.

## GeoJSON packs

One `FeatureCollection` per country, each feature carrying a standard `bbox`
member and `name` / `parent` / `version` / `source` / `licence` properties
([§5.1](../architecture/reverse-geocoding.md#51-polygon-files)):

```jsonc
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": { "layer": "county", "name": "Lisboa", "parent": "PT",
                      "version": 1, "source": "osm", "licence": "ODbL" },
      "bbox": [-9.5, 38.6, -9.0, 38.9],
      "geometry": { "type": "MultiPolygon", "coordinates": [ /* … */ ] }
    }
  ]
}
```

The `feature_index` in `region_<layer>` must match the feature's position in
this array. GeoJSON coordinates are `[lon, lat]`; the first ring is the outer
boundary, any further rings are holes.

## A minimal, runnable reference

[`tests/unit-tests/test_geo.py`](../tests/unit-tests/test_geo.py) builds a tiny
but complete `data/` directory (three counties, one state, one country) in a
temp dir via `_build_boundaries()`. It is the authoritative, executable example
of this contract — copy its output into `data/` for a working hand-made dataset.

## Verifying a release

[`tools/boundaries/verify_release.py`](../tools/boundaries/verify_release.py)
checks a boundary dataset end to end: every asset's `sha256`/`size` against
the manifest, `boundaries.db`'s row counts against its GeoJSON packs, every
pack's JSON/geometry validity, and a resolver smoke test against known
real-world coordinates. Run it against the published release
(`python tools/boundaries/verify_release.py`) or against a local pre-publish
directory before cutting a new release (`--data-dir <out-dir>`). A scheduled
run (`.github/workflows/data-integrity.yml`) verifies the published release
every 2 days and files a GitHub issue if it fails.
