# Development Plan — Offline Reverse Geocoding (County / State / Country)

Status: all phases DONE and merged into beta, including every item this doc previously listed as blocked on the `OpenSAK-Data` repo. The repo exists, hosts a real published release (`reverse-geocoding-v1`), and shipped builds now actually seed from it. Gated behind the `reverse-geocoding` feature flag (off by default in release builds) — see "Feature flag" below.
Relates to: GitHub issue #60 · design in [`architecture/reverse-geocoding.md`](../architecture/reverse-geocoding.md)
Scope: full implementation of the offline boundary engine — data pipeline, runtime engine, on-demand packs, schema, GUI, packaging/CI.

This plan turns the architecture document into shippable work. It is ordered **bottom-up**: the data has to exist before the engine can resolve, the engine before the GUI, the GUI before the old code can be retired. Each phase is independently testable and ships on its own branch.

## How this ships

- **One branch per phase**, named `60-phase-N-<desc>` (e.g. `60-phase-1-engine`), off the synced `beta`.
- **PRs target `beta`** (per issue #257), not `main`.
- **Commit every logical step**, Conventional Commits, no co-author trailer.
- New user-visible strings go through `tr()` with keys added to **every** `lang/*.py`.
- Long work stays on a `QThread`; the main thread never blocks.

## Prerequisites

- [x] Receive the full polygon-file set from Mike (*Lignumaqua*) — full `bb.db3` + all country/state/county zips are now in the worktree (gitignored). Phase 0 is unblocked and done.
- [x] Create the data repository **`OpenSAK-Org/OpenSAK-Data`** (public) with its own `LICENSE` + attribution file (the polygons are ODbL, not public domain). Docs live under `reverse-geocoding/` since the repo will host more datasets later (maps, altimetry).

## Feature flag

A single flag, `flags.reverse_geocoding` (`utils/flags.py`, default `False` in release builds — see `features.json` / `--feature reverse-geocoding=true` to enable locally), gates the entire feature: the Update Location menu action, its right-click context-menu entry, auto-geocode on GPX import, the boundary-packs download/update actions, and the Settings section. There used to be a second flag, `update-location`, gating the same GUI surface for historical reasons (predating the boundary engine) — collapsed into one flag so enabling the feature for a release is a single toggle, not two that have to be kept in sync.

`geo.store.ensure_baseline_seeded()` is called from `ReverseGeocodeWorker.run()` (a `QThread`, not app startup — see Phase 5), and is only reachable through the flag-gated menu/dialog, so no separate explicit flag check is needed there. If a *new* runtime side effect (network call, file write, background work) is ever added somewhere NOT reachable exclusively through that gated entry point — e.g. something wired into `app.py` startup again — it needs its own explicit `if flags.reverse_geocoding:` check. Being reachable only through an already-gated menu entry is not sufficient for code that runs regardless of what menu items exist.

## Out of scope (follow-ups)

- Drawing boundary outlines on the Leaflet map (the GeoJSON makes this cheap later).
- Value **locking** and the macro hook — depend on the future custom-fields system.
- Maps and altimetry datasets — separate problems, separate plans (the `OpenSAK-Data` repo is built to host them later).

---

## Phase 0 — Data pipeline and the `OpenSAK-Data` repo ✓ DONE

**Goal:** a reproducible pipeline that turns raw polygon files into the artefacts the app consumes.

**What was built:** a single converter `tools/boundaries/gsak_to_opensak.py` (combines normalise + geojson + bbdb steps). Run: `.venv/bin/python3 tools/boundaries/gsak_to_opensak.py --bb-path bb.db3` (must be the project's own venv, see below). `bb.db3` lives at **repo root**, not inside `data/`. Writes `data/boundaries.db` + `data/manifest.json` + `data/countries/world.geojson` + `data/states/<cc>.geojson` + `data/counties/<cc>_<pack>.geojson` (flat). Output: 383 countries, 1931 states, 20026 counties.

**Key fixes inside the converter:**
- `_split_antimeridian()`: rings with |Δlon|>180° teleportation edges (Russia, Alaska, Fiji…) are split into independently-closed sub-rings → MultiPolygon. Also detects the synthetic implicit-closing-edge when ring[0] recurs mid-ring.
- Coordinate parser handles all 4 GSAK formats: tab, plain comma, trailing-comma (France/Italy), space-separated (Great Britain).

**Known data gaps (not bugs):** Portugal 0 states, Russia 0 counties — correct per GSAK's source data. 18 state zips + 8 county zips missing from dataset.

**Real bugs found running the full pipeline end to end (not caught until the actual publish attempt):**
- County pack output was nested (`counties/<cc>/<pack>.geojson`, with `/` baked into the DB `pack` column) — incompatible with flat GitHub Release assets and `packs.py`'s fetch contract. Flattened to `counties/<cc>_<pack>.geojson` everywhere.
- `manifest.json` was never generated at all; each feature's `version` property was hardcoded to `1` instead of the real per-pack GSAK version. Both fixed — `_write_manifest()` now writes real dataset + per-pack versions, split into `"baseline"` (world.geojson + state packs) and `"packs"` (county, on-demand).
- Real GSAK county rings are self-intersecting for ~6% of counties (1168/20026) straight out of the raw parse — nothing to do with simplification. `shapely.contains()` doesn't raise on invalid geometry, it silently returns wrong answers, so this was a live resolver correctness bug. Fixed with a `buffer(0)` repair, applied unconditionally regardless of simplification tolerance.
- Running the converter with system `python3` (3.8 here) instead of the project's own `.venv` (3.11+) silently undercounts counties — legacy `zipfile` CP437 filename decoding mangles non-ASCII names in zips that don't set the UTF-8 flag bit, with zero error output. **Always use `.venv/bin/python3`.**
- 685MB dataset was far above the ~70MB estimate; the 227MB country+state baseline (bundled in every install) needed Douglas-Peucker simplification (tolerance 0.0005°≈55m via shapely) — cut to 57MB. Counties stay full-resolution, fetched on demand.

**Remaining tasks:** none — all done.

**Acceptance:** ✓ pipeline runs reproducibly; `BoundaryStore` + `TerritoryResolver` resolve correctly against real data; 0/22340 invalid geometries across all layers. `OpenSAK-Data` release `reverse-geocoding-v1` is live with 242 assets.

---

## Phase 1 — Core engine (`src/opensak/geo/`) ✓ DONE

**Goal:** an offline `TerritoryResolver` that returns `GeoLocation(country, state, county)`.

**What was built:** `src/opensak/geo/store.py` (`BoundaryStore`) + `src/opensak/geo/boundaries.py` (`TerritoryResolver`). Pure-Python ray-cast PIP (shapely deferred). 14 unit tests in `test_geo.py`. mypy-clean. Nothing imports `geo` in production yet — that's Phase 4. `default_data_dir()` = `$OPENSAK_BOUNDARIES_DIR`, else `config.get_app_data_dir()/boundaries` — the real persistent per-user app-data dir (flipped from an earlier repo-root/ephemeral-frozen-path fallback, see Phase 5).

**Remaining tasks:** none — first-run seeding landed as `ensure_baseline_seeded()`, see Phase 5.

**Acceptance:** ✓ resolves correctly offline; 14 tests pass; mypy green.

---

## Phase 2 — On-demand packs and updates (`src/opensak/geo/packs.py`) ✓ DONE

**Goal:** fetch county packs lazily from `OpenSAK-Data`, cache them locally, and keep both the index and the packs current — all without a project-run server.

**What was built:** `geo/packs.py` — `fetch_manifest`, `fetch_pack` (atomic temp+rename), `fetch_all` (downloads all missing packs with progress callback), `check_update` (throttled weekly by manifest.json mtime, bypass with `force=True`), `apply_update` (only re-downloads locally-cached packs that changed; skips uncached packs; saves manifest). `BoundaryStore._load_pack` now triggers `fetch_pack` on a county pack miss; `TerritoryResolver` skips a candidate gracefully when its pack cannot be fetched (returns `None` for county rather than crashing). Two new Waypoint menu actions under the `update_location` flag guard: "Download boundary packs…" and "Check for boundary data updates…", backed by `BoundaryDownloadDialog` and `BoundaryCheckDialog` workers. 19 i18n keys added to all 8 lang files. 23 unit tests with mocked network covering all code paths.

**Also added:** `fetch_baseline()` — downloads `boundaries.db` + the country/state baseline (manifest's `"baseline"` list) for first-run seeding when nothing is bundled (see Phase 5). Real bug found+fixed here: `_fetch_file_atomic` assumed its destination directory already existed (true for its only prior caller, `apply_update`, always invoked on an already-initialized dir) — `fetch_baseline` is the first caller that can hit a genuinely nonexistent directory, the real first-run case, and crashed with `FileNotFoundError`.

**Remaining tasks:** none — verified live against the real `reverse-geocoding-v1` release, not just mocked: `fetch_manifest`/`fetch_pack`/`fetch_baseline` all confirmed working end to end, including a full first-run simulation (empty local dir → on-demand county fetch → correct resolution).

**Acceptance:** ✓ 23+ tests pass; throttle, force, atomic write, selective re-download, graceful degradation all covered.

---

## Phase 3 — Schema and provenance ✓ DONE

**Goal:** store where each territory value came from, so results are auditable and re-runnable.

**What was built:** four nullable columns on `Cache` (`location_source`, `location_basis`, `location_updated`, `location_dataset`). `SCHEMA_VERSION` bumped 11 → 12; migration 12 is idempotent — uses the existing `PRAGMA table_info` pattern, skips each column if already present. 3 tests: default-NULL, writable, migration-on-old-schema (DROP COLUMN + version rewind).

**Acceptance:** ✓ old databases migrate cleanly; the four provenance columns exist and are writable; 1377 unit tests pass.

---

## Phase 4 — GUI integration and retiring the old engine ✓ DONE

**Goal:** wire the engine into the existing Update Location flow, surface provenance and updates, and remove the nearest-neighbour geocoder.

**What was built:** `ReverseGeocodeWorker` now uses `TerritoryResolver` instead of the GeoNames KD-tree; writes all four provenance columns (`location_source="boundary"`, `location_basis`, `location_updated`, `location_dataset`) per cache. `_CacheRow` gained a `basis` field (default `"posted"`) so the import dialog and the manual dialog both record which coordinate type was used. `OnlineLookupWorker` and the Nominatim path removed entirely. "Use corrected coordinates" now defaults **off** (posted coordinates are the default). `geocoder.py` deleted; `reverse_geocoder` and `pycountry` removed from runtime deps and the PyInstaller spec. Lang files updated: GeoNames references replaced, online/ETA keys removed, `update_loc_no_boundaries` added. `BoundaryStore.dataset_version()` reads the version from `file_version` in `boundaries.db`.

**Deferred:**
- ~~Menu actions: **Download all boundary packs** and **Check for boundary updates**~~ — done, see Phase 2.
- Stale indicator when `location_dataset` trails the current dataset — still genuinely open, not part of this round of work.

**Acceptance:** ✓ dialog resolves via boundary engine; provenance columns written; import auto-fills with basis; `reverse_geocoder`/`pycountry` gone; all 1355 unit tests pass; all language key tests pass.
**Risk:** medium (UI wiring + dep removal). Size: L.

---

## Phase 5 — Packaging and CI ✓ DONE

**Goal:** the shipped binaries resolve offline on all three OSes, and CI proves it.

**What was built:**
- `shapely>=2.0` added to runtime deps in `pyproject.toml`; `boundaries.py` uses `shapely.geometry.shape().contains(Point)` for the stage-2 PIP when shapely is present, pure-Python ray-cast as fallback.
- `tests/unit-tests/test_geo_deps.py` — 5 smoke tests: shapely importable, `_HAS_SHAPELY` is True, point-in-polygon (Polygon + MultiPolygon) via shapely path.
- ODbL attribution added to `about_text` in all 8 lang files.
- `default_data_dir()` in `store.py` flipped from a `sys.frozen`-then-`sys._MEIPASS/data` / repo-root fallback to the real persistent `config.get_app_data_dir()/boundaries` — neither prior fallback was a writable, persistent location a real install could use. New `ensure_baseline_seeded()`: copies `boundaries.db` + `countries/` + `states/` from the frozen PyInstaller bundle if present, else downloads them via `geo/packs.fetch_baseline()` (e.g. a pip install with nothing bundled). Counties are never seeded — always fetched on demand, per country. Wired into `app.py` startup, gated behind `flags.reverse_geocoding` (see "Feature flag" above — a review catch: this was initially wired unconditionally).
- `opensak.spec` bundles `data/boundaries.db` + `data/countries/` + `data/states/` only — **not** `counties/`, which was being bundled unconditionally whenever present at build time (a few hundred MB, the opposite of the on-demand design). Fixed.
- All 4 `build.yml` jobs run `scripts/fetch_boundary_baseline.py` (fetches the real published baseline via `fetch_baseline`) before invoking `pyinstaller` — best-effort, a failure just warns and lets the build continue since the app can self-seed at first run regardless.

**Remaining tasks:** none.

**Acceptance:** ✓ shapely integrated; frozen-bundle-then-app-data-dir seeding verified for real (not just mocked) against the live release; smoke tests pass; ODbL in About; CI fetches real data before every build.
**Risk:** medium (native wheels are the usual culprit). Size: M.

---

## Phase Extra — Speed (parallel resolve + bulk write)

**Goal:** cut wall-clock time for large batches (Pocket Query of 1000+ caches) without changing the public API or adding dependencies.

**Problem with Phase 4's serial worker:**
- Three R-Tree queries + an optional polygon check per cache, all sequential.
- The write loop does `session.query(Cache).filter_by(gc_code=...).first()` per row — N individual SELECT statements before the single commit.

**What is built:**

`ReverseGeocodeWorker.run()` splits into two phases:

1. **Parallel resolve** — a `ThreadPoolExecutor` (up to `min(4, cpu_count)` workers) resolves rows concurrently. Each worker thread gets its own `BoundaryStore` instance (separate read-only SQLite connection to `boundaries.db`, separate GeoJSON pack cache). R-Tree queries release the GIL; with shapely installed the stage-2 polygon check also releases the GIL (C extension). Progress is emitted via `row_done` as each resolve arrives through `as_completed`, so the log fills up live.

2. **Bulk write** — after all resolves complete, a single `session.query(Cache).filter(Cache.gc_code.in_([...]))` loads every target cache in one round-trip; all updates happen inside the existing one-transaction `get_session()` block.

Dialog: progress bar switches from indeterminate to determinate (total = `len(rows)`) and advances per resolved row.

**Why 4 threads, not more / multiprocessing:**
- The GIL still gates pure-Python fallback polygon math; 4 threads is a good balance between concurrency on the SQLite I/O path and not flooding the read connections.
- `multiprocessing` would require pickling every `_CacheRow` and shipping results across a process boundary with no throughput advantage for the Python fallback path.

**Cancel:** `self._cancel` is checked at the start of each `_resolve_one` call and again after the executor exits. Breaking out of `as_completed` lets the context manager drain running futures (each < ~5 ms) before the thread returns.

**Tests added:** `test_run_updates_multiple_caches` — two caches resolved in parallel, both written in the bulk pass.

**Acceptance:** all existing and new tests pass; bulk write replaces N per-row SELECTs; parallel resolve runs with one `BoundaryStore` per thread; progress bar is determinate.

---

## Sequencing

```
Phase 0 (data)  ──►  Phase 1 (engine)  ──►  Phase 2 (packs/updates)  ──►  Phase 4 (GUI) ──► Phase 5 (CI)
                                   Phase 3 (schema) ───────────────────────┘
                                                                     Phase Extra (speed) ──────┘
```

Phase 3 is small and can be done early, but Phase 4 depends on it. Phase 1 needs Phase 0's `boundaries.db` + baseline to test against. Phase 5 closes out packaging once the engine and GUI are real. Phase Extra is independent of Phase 2 and can be merged at any time after Phase 4.

## Risks and open questions

- **Data delivery and quality.** Everything hinges on receiving the polygon files; coverage gaps and stale boundaries are inherited from the source (mitigated by the refresh pipeline and provenance).
- **Simplification vs accuracy.** The baseline budget trades polygon detail for size; counties stay full-resolution because they are fetched on demand.
- **`shapely` on all platforms.** The main packaging risk; the pure-Python fallback is the safety net.
- **Behaviour change.** Default flips to posted coordinates and the nearest-neighbour engine is removed — call this out in the release notes for existing users.
