# Development Plan — Offline Reverse Geocoding (County / State / Country)

Status: Phase 0 + Phase 1 DONE (merged into beta). Phase 3 DONE (merged into beta). Phase 4 DONE (merged into beta). Phase 5 DONE (merged into beta). Phase 2 DONE (branch `60-phase-2-packs`, unpushed — awaiting OK).
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
- [ ] Create the data repository **`AgreeDK/OpenSAK-Data`** (public) with its own `LICENSE` + attribution file (the polygons are ODbL, not public domain).

## Out of scope (follow-ups)

- Drawing boundary outlines on the Leaflet map (the GeoJSON makes this cheap later).
- Value **locking** and the macro hook — depend on the future custom-fields system.
- Maps and altimetry datasets — separate problems, separate plans (the `OpenSAK-Data` repo is built to host them later).

---

## Phase 0 — Data pipeline and the `OpenSAK-Data` repo ✓ DONE

**Goal:** a reproducible pipeline that turns raw polygon files into the artefacts the app consumes.

**What was built:** a single converter `tools/boundaries/gsak_to_opensak.py` (combines normalise + geojson + bbdb steps). Run: `python3 tools/boundaries/gsak_to_opensak.py --bb-path bb.db3`. `bb.db3` lives at **repo root**, not inside `data/`. Writes `data/boundaries.db` + `data/countries/world.geojson` + `data/states/<cc>.geojson` + `data/counties/<cc>/<pack>.geojson`. Output: 383 countries, 1931 states, 20026 counties.

**Key fixes inside the converter:**
- `_split_antimeridian()`: rings with |Δlon|>180° teleportation edges (Russia, Alaska, Fiji…) are split into independently-closed sub-rings → MultiPolygon. Also detects the synthetic implicit-closing-edge when ring[0] recurs mid-ring.
- Coordinate parser handles all 4 GSAK formats: tab, plain comma, trailing-comma (France/Italy), space-separated (Great Britain).

**Known data gaps (not bugs):** Portugal 0 states, Russia 0 counties — correct per GSAK's source data. 18 state zips + 8 county zips missing from dataset.

**Remaining tasks (deferred to when `OpenSAK-Data` repo exists):**
- [ ] Geometry simplification (Douglas–Peucker) for the baseline layer.
- [ ] `manifest.json` generation and publishing as Release assets to `AgreeDK/OpenSAK-Data`.
- [ ] Keep `tools/` out of the PyInstaller bundle.

**Acceptance:** ✓ pipeline runs reproducibly; `BoundaryStore` + `TerritoryResolver` resolve correctly against real data. `OpenSAK-Data` Release not yet created (blocked on repo creation).

---

## Phase 1 — Core engine (`src/opensak/geo/`) ✓ DONE

**Goal:** an offline `TerritoryResolver` that returns `GeoLocation(country, state, county)`.

**What was built:** `src/opensak/geo/store.py` (`BoundaryStore`) + `src/opensak/geo/boundaries.py` (`TerritoryResolver`). Pure-Python ray-cast PIP (shapely deferred). 14 unit tests in `test_geo.py`. mypy-clean. Nothing imports `geo` in production yet — that's Phase 4. `default_data_dir()` = `$OPENSAK_BOUNDARIES_DIR` or `<repo-root>/data` (parents[3]).

**Remaining tasks:**
- [ ] Seed `boundaries.db` + baseline GeoJSON into `<app-data>/opensak/boundaries/` on first run (Phase 5 packaging concern).

**Acceptance:** ✓ resolves correctly offline; 14 tests pass; mypy green.

---

## Phase 2 — On-demand packs and updates (`src/opensak/geo/packs.py`) ✓ DONE

**Goal:** fetch county packs lazily from `OpenSAK-Data`, cache them locally, and keep both the index and the packs current — all without a project-run server.

**What was built:** `geo/packs.py` — `fetch_manifest`, `fetch_pack` (atomic temp+rename), `fetch_all` (downloads all missing packs with progress callback), `check_update` (throttled weekly by manifest.json mtime, bypass with `force=True`), `apply_update` (only re-downloads locally-cached packs that changed; skips uncached packs; saves manifest). `BoundaryStore._load_pack` now triggers `fetch_pack` on a county pack miss; `TerritoryResolver` skips a candidate gracefully when its pack cannot be fetched (returns `None` for county rather than crashing). Two new Waypoint menu actions under the `update_location` flag guard: "Download boundary packs…" and "Check for boundary data updates…", backed by `BoundaryDownloadDialog` and `BoundaryCheckDialog` workers. 19 i18n keys added to all 8 lang files. 23 unit tests with mocked network covering all code paths.

**Remaining (blocked on AgreeDK/OpenSAK-Data repo):**
- [ ] Verify release asset URL pattern once the repo and its first release exist.
- [ ] Build pipeline step to publish `data/` as GitHub Release assets.

**Acceptance:** ✓ 23 tests pass; throttle, force, atomic write, selective re-download, graceful degradation all covered; 1385 unit tests pass.

---

## Phase 3 — Schema and provenance ✓ DONE

**Goal:** store where each territory value came from, so results are auditable and re-runnable.

**What was built:** four nullable columns on `Cache` (`location_source`, `location_basis`, `location_updated`, `location_dataset`). `SCHEMA_VERSION` bumped 11 → 12; migration 12 is idempotent — uses the existing `PRAGMA table_info` pattern, skips each column if already present. 3 tests: default-NULL, writable, migration-on-old-schema (DROP COLUMN + version rewind).

**Acceptance:** ✓ old databases migrate cleanly; the four provenance columns exist and are writable; 1377 unit tests pass.

---

## Phase 4 — GUI integration and retiring the old engine ✓ DONE

**Goal:** wire the engine into the existing Update Location flow, surface provenance and updates, and remove the nearest-neighbour geocoder.

**What was built:** `ReverseGeocodeWorker` now uses `TerritoryResolver` instead of the GeoNames KD-tree; writes all four provenance columns (`location_source="boundary"`, `location_basis`, `location_updated`, `location_dataset`) per cache. `_CacheRow` gained a `basis` field (default `"posted"`) so the import dialog and the manual dialog both record which coordinate type was used. `OnlineLookupWorker` and the Nominatim path removed entirely. "Use corrected coordinates" now defaults **off** (posted coordinates are the default). `geocoder.py` deleted; `reverse_geocoder` and `pycountry` removed from runtime deps and the PyInstaller spec. Lang files updated: GeoNames references replaced, online/ETA keys removed, `update_loc_no_boundaries` added. `BoundaryStore.dataset_version()` reads the version from `file_version` in `boundaries.db`.

**Deferred to Phase 2 (blocked on `OpenSAK-Data` repo):**
- Menu actions: **Download all boundary packs** and **Check for boundary updates**
- Stale indicator when `location_dataset` trails the current dataset

**Acceptance:** ✓ dialog resolves via boundary engine; provenance columns written; import auto-fills with basis; `reverse_geocoder`/`pycountry` gone; all 1355 unit tests pass; all language key tests pass.
**Risk:** medium (UI wiring + dep removal). Size: L.

---

## Phase 5 — Packaging and CI ✓ DONE

**Goal:** the shipped binaries resolve offline on all three OSes, and CI proves it.

**What was built:**
- `shapely>=2.0` added to runtime deps in `pyproject.toml`; `boundaries.py` uses `shapely.geometry.shape().contains(Point)` for the stage-2 PIP when shapely is present, pure-Python ray-cast as fallback.
- `default_data_dir()` in `store.py` now checks `sys.frozen` first — frozen bundles resolve to `sys._MEIPASS/data/`.
- `opensak.spec` bundles `data/boundaries.db` + `data/countries/` + `data/states/` + `data/counties/` conditionally (skipped if `data/boundaries.db` is absent at build time); shapely added to `hiddenimports`.
- `tests/unit-tests/test_geo_deps.py` — 5 smoke tests: shapely importable, `_HAS_SHAPELY` is True, point-in-polygon (Polygon + MultiPolygon) via shapely path.
- ODbL attribution added to `about_text` in all 8 lang files.

**Remaining (blocked on AgreeDK/OpenSAK-Data repo):**
- [ ] Build pipeline step to fetch/generate `data/` before `pyinstaller opensak.spec` runs (currently converter must be run manually with the GSAK source files).
- [ ] Verify install/footprint delta stays within Phase 0 budget.

**Acceptance:** ✓ shapely integrated; frozen path correct; smoke tests pass; ODbL in About.
**Risk:** medium (native wheels are the usual culprit). Size: M.

---

## Sequencing

```
Phase 0 (data)  ──►  Phase 1 (engine)  ──►  Phase 2 (packs/updates)  ──►  Phase 4 (GUI) ──► Phase 5 (CI)
                                   Phase 3 (schema) ───────────────────────┘
```

Phase 3 is small and can be done early, but Phase 4 depends on it. Phase 1 needs Phase 0's `boundaries.db` + baseline to test against. Phase 5 closes out packaging once the engine and GUI are real.

## Risks and open questions

- **Data delivery and quality.** Everything hinges on receiving the polygon files; coverage gaps and stale boundaries are inherited from the source (mitigated by the refresh pipeline and provenance).
- **Simplification vs accuracy.** The baseline budget trades polygon detail for size; counties stay full-resolution because they are fetched on demand.
- **`shapely` on all platforms.** The main packaging risk; the pure-Python fallback is the safety net.
- **Behaviour change.** Default flips to posted coordinates and the nearest-neighbour engine is removed — call this out in the release notes for existing users.
