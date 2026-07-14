# Plan — migrate boundary data from local `data/` to `OpenSAK-Data`

Status: DONE. `OpenSAK-Data` hosts a real published release (`reverse-geocoding-v1`); `default_data_dir()` no longer falls back to the repo automatically; shipped builds bundle the baseline and seed it into the real app-data dir on first run, with a network-fetch fallback when nothing's bundled. Gated behind `flags.reverse_geocoding`.
Relates to: GitHub issue #60 · [`architecture/reverse-geocoding.md`](../architecture/reverse-geocoding.md) · [`plans/reverse-geocoding.md`](./reverse-geocoding.md)

The engine currently reads from a dev-only, git-ignored `data/` folder at the
repo root ([`docs/reverse-geocoding-data.md`](../docs/reverse-geocoding-data.md)).
That was deliberate: it let Phase 1 (the engine) land and be tested before the
real dataset or its hosting existed. This plan retires that stand-in and points
the engine at the **published `OpenSAK-Org/OpenSAK-Data`** dataset instead.

The whole point of the `default_data_dir()` + `BoundaryStore` seam is that this
migration touches **data plumbing only** — `TerritoryResolver` and the two-stage
lookup do not change.

## Where it plugs into the main plan

This is the data-sourcing slice of the existing phases, not a new track:
Phase 0 (pipeline + repo), Phase 2 (on-demand packs/updates) and Phase 5
(packaging) of [`plans/reverse-geocoding.md`](./reverse-geocoding.md). Listed
here as one migration story so the cut-over from `data/` is explicit.

## Steps

1. **Produce the real dataset (main-plan Phase 0). ✓ DONE.** `tools/boundaries/gsak_to_opensak.py`
   emits `boundaries.db`, the baseline GeoJSON (`countries/`, `states/`), the
   per-country county packs and `manifest.json`. Published as **GitHub Release
   assets** on `OpenSAK-Org/OpenSAK-Data`, tag `reverse-geocoding-v1` (242
   assets, public, ODbL `LICENSE` + attribution under `reverse-geocoding/`).

2. **Bundle the baseline in the install (main-plan Phase 5). ✓ DONE.** `opensak.spec`
   ships `boundaries.db` + `countries/` + `states/` as package data — **not**
   `counties/`, which was being bundled unconditionally before this fix (a few
   hundred MB, the opposite of the on-demand design). `ensure_baseline_seeded()`
   seeds `get_app_data_dir()/boundaries/` on first run from the bundle if
   present, else downloads via `fetch_baseline()`.

3. **Flip `default_data_dir()`. ✓ DONE.** Fallback is now
   `config.get_app_data_dir() / "boundaries"`. `OPENSAK_BOUNDARIES_DIR` override
   kept, `test_geo.py` untouched.

4. **Add on-demand county packs (main-plan Phase 2): `geo/packs.py`. ✓ DONE.**
   County cache miss → `fetch_pack` downloads that pack, atomic temp+rename,
   served locally thereafter. `fetch_all` ("download all"), `check_update`/
   `apply_update` (throttled manifest check), and now `fetch_baseline` (the
   country/state wholesale download for step 2) all verified live against the
   real release, not just mocked.

5. **Retire the stand-in. ✓ DONE, in spirit.** `default_data_dir()` no longer
   implicitly falls back to repo-root `data/` — it now only appears via the
   explicit `OPENSAK_BOUNDARIES_DIR` override. `/data/` stays in `.gitignore`
   deliberately: it's still the working directory for regenerating and
   hand-testing the dataset locally (see `docs/reverse-geocoding-data.md`), just
   no longer an implicit runtime fallback.

6. **Tests stay offline. ✓ DONE.** `packs.py`/`fetch_baseline` fully covered
   with mocked network (`test_packs.py`); `test_geo.py` continues to cover the
   resolver via `OPENSAK_BOUNDARIES_DIR`. A real, unmocked end-to-end run was
   also done once, against the live release, to prove the mocks weren't lying.

7. **Publisher-side integrity check. ✓ DONE.** Steps 1-6 cover the *client*
   consuming the release; nothing previously verified that what's actually
   published stays intact. `tools/boundaries/verify_release.py` validates
   manifest checksums, `boundaries.db` structure, GeoJSON/geometry validity,
   and a resolver smoke test — run every 2 days
   (`.github/workflows/data-integrity.yml`) against the live release, filing
   a GitHub issue on failure. See
   [`docs/reverse-geocoding-data.md`](../docs/reverse-geocoding-data.md#verifying-a-release).
   Regenerating the dataset itself from a fresh source (e.g. pulling updated
   polygons from OSM directly, instead of another manual `bb.db3` handoff)
   is a separate, unbuilt follow-up.

## Acceptance

- ✓ A fresh install resolves country/state offline from the bundled (or
  first-run-seeded) baseline — verified live, not just mocked.
- ✓ A lookup for an uncached country fetches its pack once from `OpenSAK-Data`,
  then resolves locally — verified live.
- ✓ `default_data_dir()` no longer points at the repo by default.
- ✓ `TerritoryResolver` and the two-stage lookup are unchanged from Phase 1.
- Whole feature gated behind `flags.reverse_geocoding` (default off in release
  builds) — including the first-run seed call itself, not just the GUI menu
  entries.
