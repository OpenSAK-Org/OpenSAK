# Plan — migrate boundary data from local `data/` to `OpenSAK-Data`

Status: Proposed (future work)
Relates to: GitHub issue #60 · [`architecture/reverse-geocoding.md`](../architecture/reverse-geocoding.md) · [`plans/reverse-geocoding.md`](./reverse-geocoding.md)

The engine currently reads from a dev-only, git-ignored `data/` folder at the
repo root ([`docs/reverse-geocoding-data.md`](../docs/reverse-geocoding-data.md)).
That was deliberate: it let Phase 1 (the engine) land and be tested before the
real dataset or its hosting existed. This plan retires that stand-in and points
the engine at the **published `AgreeDK/OpenSAK-Data`** dataset instead.

The whole point of the `default_data_dir()` + `BoundaryStore` seam is that this
migration touches **data plumbing only** — `TerritoryResolver` and the two-stage
lookup do not change.

## Where it plugs into the main plan

This is the data-sourcing slice of the existing phases, not a new track:
Phase 0 (pipeline + repo), Phase 2 (on-demand packs/updates) and Phase 5
(packaging) of [`plans/reverse-geocoding.md`](./reverse-geocoding.md). Listed
here as one migration story so the cut-over from `data/` is explicit.

## Steps

1. **Produce the real dataset (main-plan Phase 0).** Once Mike's polygon files
   arrive, run the `tools/boundaries/` pipeline to emit `boundaries.db`, the
   baseline GeoJSON (`countries/`, `states/`), the per-country county packs and
   `manifest.json`. Publish them as **GitHub Release assets** on
   `AgreeDK/OpenSAK-Data` (public, ODbL `LICENSE` + attribution). The artefacts
   must match the contract in [`docs/reverse-geocoding-data.md`](../docs/reverse-geocoding-data.md)
   so the engine reads them unchanged.

2. **Bundle the baseline in the install (main-plan Phase 5).** Ship
   `boundaries.db` + `countries/` + `states/` as package data via `opensak.spec`
   (`datas` / `collect_data_files`). On first run, seed them into
   `get_app_data_dir()/boundaries/` if absent — so country/state resolution works
   offline from first launch, with no network.

3. **Flip `default_data_dir()`.** Change its fallback from `<repo-root>/data` to
   `config.get_app_data_dir() / "boundaries"`. Keep the `OPENSAK_BOUNDARIES_DIR`
   override (tests and local datasets rely on it), so `test_geo.py` keeps working
   untouched.

4. **Add on-demand county packs (main-plan Phase 2): `geo/packs.py`.** On a
   county cache miss, download that country's pack from its `OpenSAK-Data`
   Release URL, write it under `counties/` with an **atomic** temp-then-swap, and
   serve locally thereafter. Add a "download all" pre-fetch and a throttled
   `manifest.json` update check (re-download only changed files; flag affected
   caches' `location_dataset` as stale rather than rewriting values). `BoundaryStore`
   gains a "pack missing" path that calls into `packs.py`; the resolver still just
   asks the store for geometry.

5. **Retire the stand-in.** Remove the `/data/` entry from `.gitignore` and the
   note from [`docs/reverse-geocoding-data.md`](../docs/reverse-geocoding-data.md)
   (or keep `OPENSAK_BOUNDARIES_DIR` documented purely as a dev override). The
   repo-root `data/` folder is no longer special.

6. **Tests stay offline.** Mock the network for `packs.py` (mirror `conftest.py`'s
   offline pattern): cache-miss → fetch → local second lookup; version compare;
   atomic swap; offline fallback returns the coarser cached layer, never a wrong
   guess. The existing synthetic-dataset tests in `test_geo.py` continue to cover
   the resolver via `OPENSAK_BOUNDARIES_DIR`.

## Acceptance

- A fresh install resolves country/state offline from the bundled baseline.
- A lookup for an uncached country fetches its pack once (mocked in tests) from
  `OpenSAK-Data`, then resolves locally.
- `default_data_dir()` no longer points at the repo; `data/` can be deleted with
  no effect on a normal run.
- `TerritoryResolver` and the two-stage lookup are unchanged from Phase 1.
