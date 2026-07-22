# Changelog — OpenSAK
All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added

- **Lightweight query path (`apply_filters_lightweight()`)** (#627 beta.9)
  — new function in `filters/engine.py` alongside `apply_filters()`,
  returning `LightweightCache` rows (backed by a SQLAlchemy Core `select()`)
  instead of full `Cache` ORM objects, for filtersets that don't need a
  relationship or deferred text field (falls back to the existing
  `apply_filters()` ORM path automatically otherwise — never returns wrong
  or incomplete results). Isolated benchmark on a 100,000-cache database:

  | Scenario | `apply_filters` | `apply_filters_lightweight` | Speedup |
  |---|---|---|---|
  | No filter (100k rows) | 8.0s | 1.46s | ~5.5x faster |
  | `AvailableFilter` (92k rows) | 5.9s | 1.30s | ~4.5x faster |
  | `ArchivedFilter` (5k rows) | 0.20s | 0.08s | ~2.5x faster |

  ORM row hydration was already identified as `apply_filters()`'s dominant
  cost (#631) — this confirms it directly: bypassing ORM entity
  construction for the common display case (table/map, simple filters)
  is a genuine multi-x win, not a rounding error like #628/#631's smaller
  fixes. Gated behind a new `lightweight-query-path` feature flag; **not
  yet wired into the table or map** — that's beta.10/beta.11.

- **Wire the cache table to the lightweight query path** (#627 beta.10) —
  `mainwindow.py`'s four `apply_filters()` call sites now go through a new
  `apply_filters_auto()` dispatcher, which uses `apply_filters_lightweight()`
  when the `lightweight-query-path` flag is on. An audit found
  `CacheTableModel` (and, as a side effect, `map_widget.py` too — every
  column/sort key/tooltip already only touches scalar fields or cached
  count columns, never a relationship collection directly) needed **zero**
  source changes to already support `LightweightCache` rows; row selection
  already reloads a full `Cache` via the existing `_load_full_cache()`
  pattern regardless of what's in the table. One real compatibility gap
  was found and fixed along the way: `CacheTableModel.setData()`'s
  flag/lock/FTF quick-toggle mutates the table's own row object in place
  for instant UI feedback, which would have raised on the otherwise-immutable
  `LightweightCache` — fixed with a small overrides mechanism scoped to
  exactly those three fields. Benchmark (full fetch + `CacheTableModel.load()`
  pipeline, 100,000 caches, no filter, via the real `apply_filters_auto()`
  wiring): 5.06s → 1.87s (~63% faster). Still behind the feature flag,
  default off.

- **Wire the map to the lightweight query path, and remove the feature
  flag** (#627 beta.11) — a dedicated compatibility audit and test suite
  (`tests/unit-tests/test_map_widget.py`, real `LightweightCache` rows
  from `apply_filters_lightweight()` end to end into `MapWidget._do_load_caches()`)
  confirmed what beta.10 found as a side effect: `map_widget.py` needed
  zero source changes — `_effective_coords()`, `_cache_pin_html()`, and the
  JSON payload build all already only touch scalar fields and
  `.user_note.is_corrected`/`.corrected_lat`/`.corrected_lon`. With both
  the table (beta.10) and map paths now confirmed stable across the full
  unit-test suite, the full e2e suite, and a 250,000-cache benchmark, the
  `lightweight-query-path` feature flag has been removed —
  `apply_filters_auto()` (what `mainwindow.py` calls for every table/map
  refresh) now unconditionally uses the lightweight path, still with its
  existing automatic fallback to the full ORM path for filters that need
  a relationship or deferred text field. This is a default-behavior
  change for everyone, not opt-in. Confirmed final numbers (full fetch +
  `CacheTableModel.load()` pipeline, 100,000 caches, no filter): 5.50s →
  1.92s (~65% faster) — consistent with beta.10's measurement.

---

## [1.16.0-beta.8] — 2026-07-22

> **Beta release** — a small, safe correctness fix in the filter engine's
> SQL pushdown, spun off from the #627 large-database investigation.

### Fixed

- **Boolean filters silently bypassed their indexes** (#628, part of #627)
  — `FoundFilter`, `ArchivedFilter`, `AvailableFilter`, `AvailabilityFilter`,
  `PremiumFilter`, and `NonPremiumFilter` used `Cache.<col>.is_(True)` /
  `.is_(False)` in their SQL pushdown, which compiles to `<col> IS true` /
  `IS false`. SQLite's query planner cannot use an index for that form —
  verified directly against SQLite 3.45 with `EXPLAIN QUERY PLAN` — even
  though the functionally identical `<col> = true` / `= false` (what
  `== True`/`== False` compiles to) is index-usable and the relevant
  indexes have existed since #214. `.is_(None)` (NULL checks, e.g.
  `DifficultyFilter`'s unknown-difficulty handling) was never affected and
  is unchanged. Real-world impact is small at current database sizes —
  isolated A/B testing showed ~0.11s either way for a selective filter on
  100,000 caches, since raw SQL execution is dwarfed by ORM row hydration
  (same finding as #631) — but this restores the indexing intent from
  #214 at zero cost and zero risk.

---

## [1.16.0-beta.7] — 2026-07-22

> **Beta release** — large-database performance work (see #627): map load
> is dramatically faster on big databases thanks to icon caching and bulk
> marker loading, plus a small, safe win in the filter engine. Includes a
> new benchmark harness so every step here — and future ones — can be
> measured instead of guessed at.
>
> Measured on a 250,000-cache synthetic benchmark database
> (`scripts/benchmark_large_db.py`): map load dropped from 11.34s to 4.45s
> (-61%), total time to show all caches dropped from 37.58s to 30.42s
> (-19%). Full before/after table in #628.

### Added

- **Large-database benchmark harness** (#628, part of #627) —
  `scripts/benchmark_large_db.py` generates a synthetic database at a
  configurable scale (default 250,000 caches) and measures distance
  recalculation, `apply_filters()`, map load, table load, and info-bar
  update, printing a table (optionally markdown) for pasting into GitHub
  issues. Every performance change in #627 is now measured against this
  harness rather than eyeballed.

### Improved

- **Cache map pin HTML generation** (#629, part of #627) — `get_map_pin_html()`
  now caches its output with `@lru_cache(maxsize=256)`. The HTML (including
  base64-encoded SVG) only depends on `(cache_type, found, dnf)`, a small
  bounded set of combinations, but was previously rebuilt from scratch for
  every visible cache on every map load. On a 100,000-cache benchmark
  database (see #628's `scripts/benchmark_large_db.py`), map load time
  dropped from ~10.1s to ~3.2s (~68%).

- **Bulk-load map markers with chunked clustering** (#630, part of #627) —
  the map's `loadCaches()` called Leaflet.markercluster's `addLayer()` once
  per cache, which rebuilds the library's spatial index on every single
  call. It now builds all markers first and adds them in one
  `addLayers()` bulk call, with `chunkedLoading: true` so the browser's UI
  thread stays responsive while a large marker set loads. The post-load
  pan/fit-bounds step is deferred until every chunk has actually been
  added (via `chunkProgress`), so it still reflects the complete marker
  set instead of a partially-loaded one.

- **Skip redundant Python filter pass when fully SQL-pushed** (#631, part
  of #627) — `apply_filters()` now skips its Python-level
  `filterset.matches()` re-scan when every filter in the filterset was
  already pushed into the SQL `WHERE` clause, since every row `query.all()`
  returns already satisfies it. Measured impact is modest — the Python pass
  itself is only ~2% of `apply_filters()`'s time even on a 100,000-cache
  database with a large result set (~6.8s total, ~0.13s of which was the
  redundant pass); ORM hydration dominates and is unaffected by this
  change. Still a safe, zero-cost win, and it required introducing a new
  `BaseFilter.sql_exact` flag: while implementing this, testing surfaced
  that `DistanceFilter`'s SQL pushdown is a bounding-box *pre-narrowing*
  only (not an exact translation — it ignores `min_km` entirely and
  doesn't have the true circle shape), so the naive "non-None
  `apply_to_query()` == fully handled" assumption would have silently
  dropped the `min_km` check for distance-filtered results. `sql_exact`
  lets a filter opt out of counting toward the skip decision while still
  contributing its SQL pre-narrowing; `DistanceFilter` is the only filter
  that needs it.

---

## [1.16.0-beta.6] — 2026-07-21

> **Beta release** — two data-integrity fixes for GSAK-database imports:
> attribute names and the attribute filter were often wrong, and corrected
> (solved-puzzle) caches lost their original coordinates on import.

### Fixed

- **Wrong attribute settings from GSAK database import** (#615) — 42 of the
  70 Groundspeak attribute IDs in OpenSAK's internal attribute table were
  mapped to the wrong attribute (e.g. id 31 resolved to "Food nearby"
  instead of "Camping available"). Beyond GSAK-database imports, this also
  affected the attribute filter, which built its checkbox labels and
  underlying filter values from the same table — so filtering by attribute
  could silently return the wrong caches regardless of import source.
  Rebuilt and verified against real GPX exports from geocaching.com.

- **Caches with corrected coordinates lose the original coordinates when
  importing GSAK database** (#614) — GSAK's own `Latitude`/`Longitude`
  columns reflect the *corrected* position once a cache has been solved,
  not the original/posted coordinates. OpenSAK imported these directly as
  the cache's primary position, silently discarding the true original
  location on every GSAK-database import of a solved cache. The original
  position is now read from GSAK's `Corrected` table instead.

---

## [1.16.0-beta.5] — 2026-07-16

> **Beta release** — startup no longer recalculates every cache's distance
> unnecessarily, which should noticeably speed up launch on large databases.

### Fixed

- **Redundant distance recalculation on every startup** (#579) — the app
  recalculated distance/bearing for every cache on every launch, even
  though nothing about the database or home point had changed since the
  last session. On large databases (100k+ caches) this made startup
  noticeably slow with no visual indication of what was happening.
  `recalculate_distances()` now persists the centre point and distance
  method it was run with, and on startup the app checks this — plus a
  cheap single-row spot-check against the database — before deciding
  whether a full recalculation is actually needed. Normal startup now
  skips it entirely; a database synced from another machine with a
  different home point (or otherwise modified outside this OpenSAK
  install) still triggers a full recalculation as before.

---

## [1.16.0-beta.4] — 2026-07-15

> **Beta release** — the database list/dropdown is now alphabetically
> sorted, plus a small message cleanup.

### Fixed

- **Database list/dropdown was not sorted alphabetically** (#531, #601) —
  the toolbar database dropdown, the Manage Databases dialog, and the
  database picker in Move Caches, GSAK import, and GPX/PQ import all
  listed databases in the order they were added/imported instead of
  alphabetically. All of these now show databases sorted alphabetically
  (case-insensitive) by name, matching GSAK's behaviour.
- **"Database created" message told the user to manually activate it**
  (#464) — creating a new database already switches to it automatically,
  but the confirmation dialog still said to click "Switch to this" to
  activate it. The message now simply confirms the database was created
  and is active.

---

## [1.16.0-beta.3] — 2026-07-15

> **Beta release** — pick any cache, saved home point, or coordinate as the
> distance filter's center (#511), plus two small bugfixes.

### Added

- **Center point picker for the distance filter** (#511) — the "Afstand"
  filter no longer always centers on Home. Choose Home, any saved home
  point, the currently selected cache, or a manually entered coordinate as
  the center, and set an optional minimum distance alongside the existing
  maximum (both were already supported by the filter engine; only the
  maximum was previously exposed in the dialog). Built as a standalone,
  reusable widget for future reuse (planned for #558).
- **"Set as center point" (right-click)** (#511) — right-click any cache or
  custom waypoint (e.g. a hotel added via Waypoint → Custom Waypoint) and
  choose "Sæt som centerpunkt" to recompute the Distance column for every
  cache from that point, exactly like switching Home. The chosen point's
  GC code/name is shown in the info bar's "Centerpunkt" field and in the
  Home dropdown until you pick a saved home point or another cache.

### Fixed

- **Hint markup was being ROT13-scrambled** (#595) — geocaching.com's own
  hint markup (`[br]` for a line break, place-name tags like `[Étape]` in
  French hints) was incorrectly rotated along with the rest of the hint
  text, so `[br]` showed up as its ROT13'd form `[oe]` instead of a line
  break. Bracketed markup is now left untouched by the ROT13
  encode/decode, and `[br]` renders as an actual line break in both the
  cache detail hint tab and KML export.
- **Website: corrected GSAK's freeware date** (#589) — the landing page's
  comparison table said GSAK became freeware in 2021; per research from a
  long-time GSAK user (French GSAK user since 2011), the free v9.0.0
  shipped in 2019, with the last forum-provided patch dating from 2022.

---

## [1.16.0-beta.2] — 2026-07-15

> **Beta release** — custom waypoint types get their own icons, and the
> found-smiley icon set is simplified (#593).

### Added

- **Custom waypoint types now have their own icons** — Parking Area,
  Trailhead, Stage, Final Location, Reference Point, Waypoint, Hotel/POI
  and Custom each get a distinct icon in the table, map and detail panel,
  instead of all sharing the generic "unknown" (?) icon. Overridable via
  the same `icons/cache_types/` user-icon mechanism as #519.

### Changed

- **Simplified the found-smiley icon set** (#593) — removed the 12 unused
  colour variants and the per-type colour-selection code behind them.
  Only `gold` (Found overlay + "Found" column) and `dark_blue` (DNF
  overlay) were ever actually shown in the app; the rest was dead
  code/assets. Reported by a community member in the OpenSAK Facebook
  group.

---

## [1.15.0] — 2026-07-14

> First stable release of the 1.15.0 cycle. Replaces the run of
> `1.15.0-beta.1` … `1.15.0-beta.16` builds — see git history for the
> detailed beta-by-beta log if needed.

### Added

- **Direct GSAK database import** (#469) — import an entire GSAK
  `sqlite.db3` file straight into an OpenSAK database, without going via
  GPX first. Reads caches, waypoints, attributes, logs (full history, not
  capped like GPX/PQ exports), corrected coordinates, personal notes and
  trackables directly from the GSAK schema. Confirmed against several
  independent real-world GSAK databases during development, including a
  1.1M-log-row one. GSAK custom fields, the Ignore list are out of scope
  for this first pass (tracked separately in #473).
- **Export to Garmin GGZ format** (#348) — the GPS export dialog now
  offers a GPX/GGZ format choice. GGZ packs the exported caches (unlimited
  count, unlike GPX-based transfers) directly into a ZIP structure Garmin
  devices read natively, matching GSAK's GGZ layout byte-for-byte.
- **User-replaceable icon packs** (#519) — custom cache-type and found-
  smiley icons can now be dropped into a new `icons/` folder (Settings →
  Advanced → "Open icons folder") without touching any code or rebuilding
  the app. The folder lives alongside `opensak.json`, so it survives app
  updates/reinstalls. Also covers the fixed, single-instance UI icons
  (Corrected coordinates, Premium, Fav. points, Trackables) via an
  `icons/ui/` subfolder. A bundled, offline "View icon naming guide"
  button lists every file name and recommended canvas size.
- **Trackables (travel bugs / geocoins) column and tab** (#489, #538) — an
  opt-in column showing how many trackables are logged in each cache, and
  a new Trackables tab on the cache detail panel listing each one with a
  clickable `coord.info` link.
- **GSAK-style icons for Found, Premium and Fav. points** (#489) — icons
  instead of plain text/numbers in the cache list, matching GSAK's own
  look.
- **Double-click a cache row to open it on geocaching.com** (#471) —
  matches GSAK's behaviour.
- **Option to show hints decoded by default** (#499) — new checkbox under
  Settings → Display. Off by default.
- **"Support OpenSAK"** — now that the project is fiscally hosted by Open
  Source Collective, a Help menu entry, README/website badges, and a
  button right on the update-available dialog all link to
  `opencollective.com/opensak`.

### Fixed

- **Changing the install/database folder via the setup wizard didn't
  move anything** (#562) — re-running the setup wizard with a different
  install and/or database folder only updated the stored *pointers*,
  never the actual files, which could silently reset all settings or
  leave existing databases behind. Settings, custom icon packs, and the
  Geocaching.com OAuth token now move with the install folder (with a
  clear warning on collision instead of failing silently); changing the
  database folder now offers to move existing databases along; moving/
  deleting the active database no longer crashes with "Database not
  initialised"; the "New Database" dialog now defaults to the right
  folder; and old, now-empty folders (including nested ones) are cleaned
  up automatically.
- **"Access is denied" crash saving settings on Windows** (#574) —
  happened right after a reboot or update, when antivirus/indexing/
  roaming-profile sync briefly held `opensak.json` open during the
  atomic save. The write now retries a few times before giving up.
- **Filter window always opened on the primary monitor** (#580) on
  multi-monitor setups, regardless of which screen OpenSAK itself was
  running on. Now opens on the same monitor as the main window.
- **A cleared filter silently came back when returning to a database** —
  clicking the red ✕, choosing "None" from the filter dropdown, pressing
  Escape, or clicking "All" in the status bar reset the filter in the
  current view but never persisted that per-database, so switching away
  and back reapplied the filter you'd just cleared.
- **Beta users never discovered a newer stable release** — the update
  checker only ever compared a running beta against other betas, so
  beta.16 users wouldn't have been notified that this stable release
  existed.
- **Critical: severe UI freeze switching to or filtering a large
  database** (#540) — the icon-override folder was being resolved from
  scratch (file check + JSON read + several `mkdir()` calls) for every
  single icon lookup, per row — commonly intercepted synchronously by
  antivirus on Windows, compounding into 45-60 second freezes on large
  databases. Now resolved once per session.
- **Dynamic map zoomed out to show the whole world** for caches with
  hidden-coordinate (0/0) waypoints (#546), e.g. a finale left hidden
  after a GSAK import.
- **Clear-filter button (✕) had no hover highlight** (#559), looking
  non-interactive compared to the rest of the toolbar.
- A batch of GSAK Database Import fixes found during real-world testing:
  waypoints sharing a name but not a code were silently dropped (#536);
  renaming a database didn't move the underlying file, so a new database
  under the freed name silently reopened the old one (#539); a leftover
  `favorite_point` column crashed inserts on some databases (#530);
  non-UTF-8 text fields aborted the entire import instead of falling
  back gracefully; Adventure Lab and five other cache types imported as
  "Unknown Cache" (#532); county wasn't imported from GSAK-exported GPX
  (#521); trackables and premium status weren't imported (#538, #541);
  and the "New Database" default folder pointed at the install folder
  instead of the configured database folder.
- **Distance column could show stale values after editing a home point**
  (#522) — only switching center points via the toolbar recalculated
  distances; editing a home point's coordinates in Settings didn't.
- **"Has trackables" filter crashed** on any database created before
  v1.14.0 (#491) — a missing table-creation migration.
- **Map didn't update when correcting coordinates via the cache list's
  right-click menu** (#474), unlike the same action from the detail panel.
- **SMALL row-height setting silently ignored** on some systems (#490).
- **Potential crash on databases with a "Favourite" (★) column enabled**
  (#488) — removed; GSAK only tracks community Fav. points.
- **Found date missing for webcam caches and events on import** (#457) —
  `found_date` was derived only from "Found it" logs.
- **FTF detection flagged logs that only mentioned "first to find" in
  passing** (#458) — now matched exclusively against ProjectGC's official
  tags.
- Several GGZ export bugs (#348): a crash on databases with mixed dated/
  undated logs; files written to the wrong device folder; wrong dates
  inside the exported ZIP; and a severe slowdown on large exports (#466),
  200-1000× faster after fixing an accidentally-quadratic offset
  calculation.
- **Corrected Coordinates icon inconsistency** (follow-up to #354) — a
  consistent SVG warning-triangle icon everywhere, replacing a hard-to-see
  emoji.
- **Found count under the grid counted found caches, not found logs**
  (#552) — a relocatable/multi-visit cache found several times only ever
  contributed 1 to the total.
- **Filter couldn't be cleared via the toolbar "None" dropdown**, plus a
  new configurable Escape shortcut to clear the active filter (#553).
- **Large Text setting not applied consistently** — the GC Code column
  stood out at the wrong size (#547).
- **Deleting the active saved filter left it applied** until the next
  unrelated action (#491).
- **Flag and locked column icons distorted on found-cache rows** (#509) —
  emoji glyphs don't have a real italic form on some platforms.
- **File-mode GPS export silently overwrote existing files** (#501) — now
  prompts for a new filename if the target already exists.
- **Filters with zero matches emptied the cache list** (#444) — now
  rejected with a warning instead, matching GSAK's behaviour.

---

## [1.14.0] — 2026-06-29

> First stable release of the 1.14.0 cycle. Replaces the run of
> `1.14.0-beta.1` … `1.14.0-beta.20` builds — see git history for the
> detailed beta-by-beta log if needed.

### Added

- **Lock a cache against import overwrites** (closes #202) — a long-requested
  GSAK feature. Locking a cache freezes its scalar fields (name, type,
  container, coordinates, D/T, owner, status, descriptions, hint,
  country/state/county) so a later PQ/GPX re-import can't silently change
  data your stats depend on. Logs, attributes and waypoints still refresh
  normally. Filterable and sortable like any other column.

- **Personal notes, round-trippable with GSAK** (closes #389, #390, #391, #392)
  — a new "Notes" tab on the cache detail panel for free-text notes per
  cache, separate from the geocaching.com description and logs. Imported
  from and exported back to GSAK's `gsak:UserNote` extension, so a note
  survives an export → GSAK → re-import round trip.

- **Child waypoints are now visible in the UI** (closes #376, #377, #378,
  #393) — cache names with waypoints show in bold in the list, a new
  "Waypoints" tab lists each one's prefix, type, name, coordinates and
  description, and selecting it shows the markers on the map.

- **Attributes tab in the cache detail panel** (closes #417) — lists every
  cache attribute with a green ✓ or red ✗ marker.

- **Keyboard Shortcuts dialog** (closes #205) — Help → "Keyboard
  Shortcuts…" opens a searchable reference of every shortcut. Shortcuts are
  managed through a central registry; user overrides persist across
  restarts.

- **Full-text search filter** (closes #294) — a new "Text Search" tab in the
  filter dialog searches cache descriptions, logs, and personal notes
  (hint text off by default), pushed down to SQL so it stays fast on large
  databases.

- **Cache type icon in the detail panel** (closes #286) — shown next to the
  cache title, scaling with the text-size setting. Found/DNF map-pin
  smileys now correctly use gold/dark-blue regardless of cache type,
  matching GSAK.

- **Type column display options** (closes #413, #414, #415, #416) — show
  icon only (default), name as text, or both, via a new column-dialog
  setting.

- **Distance calculation reworked** (closes #60) — now computed once per
  centre-point change instead of on every refresh, which kept large
  databases noticeably faster. A new Vincenty (WGS84) method is available
  alongside the existing Haversine default in Settings → Advanced.

- **Active filter count in the info bar** (closes #373) — shows e.g. "3
  filters active" instead of a generic label.

- **Welcome wizard for first-run setup** (closes #210) — walks new
  installations through language, installation folder, database folder,
  optional Geocaching.com profile, and a confirmation screen. A new "Run
  setup wizard again" button in Settings → Advanced (fixes #358) lets you
  re-run it later, e.g. to change folders.

- **JSON-based settings store** (closes #209) — replaces QSettings and the
  old `preferences.json` with a single `opensak.json` file. Existing
  installations migrate automatically and transparently on first launch of
  this version.

- **Database and installation folders manageable from Settings → Advanced**
  — view both folders, and move existing databases to a new folder (with
  the option to keep or delete the originals) without going through the
  setup wizard again.

- **Per-database column views with drag-to-reorder** (closes #199) — visible
  columns and widths are remembered separately per database; drag column
  headers to reorder them.

- **UI text and icon size is now adjustable** (closes #286, #287, #290) — a
  new Settings → Display option offers Small, Medium (default), and Large,
  affecting the cache list, detail panel, and tab labels.

- **GC Code colors and clickable status counts now match GSAK** (issue
  #270) — found caches show yellow, your own caches show green, and
  clicking a colored count in the info bar (Found / My caches / Inactive /
  All) filters the list to that status.

- **GSAK personal/user fields are now imported** (closes #269) — `UserFlag`,
  `IsPremium`, `UserSort`, `UserData`/`User2`/`User3`/`User4` and
  `FavPoints` from GSAK-exported GPX are imported without overwriting data
  on a later plain Pocket Query re-import.

- **Full log text shown without truncation** (fixes #218), and **links in
  logs are now clickable** (fixes #219), matching the existing behaviour of
  the cache description tab.

- **User Guide link in the Help menu** — opens the online User Guide
  directly in your default browser.

- **Debug logging system** — writes to `opensak.log` in the install
  directory (resets on startup, rotates at 1 MB). "Open log file" was added
  to the Help menu, making it easy to attach when reporting issues.

- **New "no corrected coordinates" filter** (fixes #274) — mirrors the
  existing Premium/Non-Premium filter pair; previously unchecking "has
  corrected coordinates" alone produced no filter at all.

### Changed

- **Owned-cache counting and coloring now use the `owner` field instead of
  `placed_by`** (issue #270) — an adopted cache is now attributed to its
  current owner, matching GSAK.

### Fixed

- **Hint encoding detection was reversed** (fixes #329) — geocaching.com PQ
  exports deliver hints as plaintext, not ROT13 ciphertext as previously
  assumed; OpenSAK was showing plaintext hints as gibberish and vice versa.
  Display defaults to obscured either way; "Decode hint" reveals it.
- **Google Maps link in the cache detail pane didn't open** (fixes #321).
- **GSAK GPX logs were capped at 20 entries** (fixes #266) — all logs are
  now shown.
- **A companion `-wpts.gpx` file could import as a duplicate set of caches**
  (fixes #410) — detection now inspects file content instead of filename.
- **Container/size column sorted alphabetically instead of by actual size**
  (fixes #412).
- **Favorites column showed on new databases despite always being empty**
  (fixes #418) — off by default now, since populating it requires the
  Geocaching.com Live API, which OpenSAK doesn't have yet.
- **Adventure Lab stages with non-`GC`/`LC` prefixes were silently dropped
  on import** (fixes #359).
- **Newly imported caches showed no distance or bearing until restart**
  (fixes #359).
- **GC Code text could be unreadable in dark mode** (fixes #366).
- **Unset flag column had no visual indicator** (fixes #290).
- **Locale-aware dates weren't zero-padded consistently** (fixes #369).
- **Enter key in the filter dialog triggered "Save profile" instead of
  Apply** (fixes #370).
- **Text/icon size setting didn't take effect until reselecting a cache**
  (fixes #371).
- **Import progress bar was indeterminate** (fixes #372) — now shows real
  progress based on a waypoint pre-scan.
- **Small/Large text size options looked almost identical to Medium**
  (fixes #374, #375) — range widened, and the setting now also applies to
  the cache grid's font and row height.
- **Cache detail panel could crash when sorting logs with some entries
  missing a date** (fixes #429).
- **Several cache-table columns weren't center-aligned like their
  neighbours** (fixes #431).
- **Update checker failed with SSL certificate errors on Windows** — the
  bundled `.exe` now explicitly uses `certifi`'s certificate bundle.
- **Setup wizard's database-folder step defaulted to the install folder
  instead of the actual database folder** on re-run.
- **Boolean settings could silently corrupt to base64 strings** in the new
  JSON settings store — existing corrupted values repair automatically on
  startup.

For planned features and known issues see the [GitHub Issues list](https://github.com/OpenSAK-Org/opensak/issues).

---

## [1.13.12] — 2026-06-15

### Added

- **Export progress shows how far it has reached** (closes #207) — the GPS, file (GPX/LOC/GGZ)
  and KML export dialogs now display a determinate progress bar with the number of caches
  processed and the percentage (e.g. `320 / 500 (64%)`) instead of an indeterminate "running"
  bar, giving a sense of how long the export will take. Suggested in issue #207.

### Fixed

- **Export no longer crashes with DetachedInstanceError** — the cache table loads rows with the
  description/hint text and logs/waypoints left out for speed, so exporting them straight from the
  table raised `DetachedInstanceError` (and would otherwise have dropped hints and logs from the
  output). Exports now reload the full cache data first, so GPX/LOC/GGZ/KML files always include
  hints, logs and waypoints.

- **Re-importing an exported GPX no longer imports 0 caches** — OpenSAK exports GPX 1.1 (with the
  Groundspeak data wrapped in `<extensions>`), but the importer only recognised GPX 1.0 with the
  Groundspeak block as a direct child, so importing an OpenSAK-exported file (or any GPX 1.1 file)
  found nothing. The importer now reads both GPX 1.0 and 1.1.

- **Reverse geocoding no longer crashes in released builds** (#215) — `reverse_geocoder` and
  `pycountry` were declared in `pyproject.toml` but missing from `requirements.txt`, which CI and the
  PyInstaller builds installed from; because they are imported lazily the app started fine but the
  Country/State/County lookup crashed in every shipped binary, undetected by CI. `pyproject.toml` is
  now the single source of truth — CI and builds install the project (`pip install -e ".[dev]"`),
  `requirements.txt` is removed, the bundles ship the libraries' data files (GeoNames CSV, ISO
  tables), and a smoke test exercises the real lookup so a missing dependency fails CI.

For planned features and known issues see the [GitHub Issues list](https://github.com/OpenSAK-Org/opensak/issues).

## [1.13.11] — 2026-05-29

### Fixed

- **Adventure Lab caches from lab2gpx can now be imported** — GPX files generated by
  [lab2gpx](https://gcutils.de/lab2gpx/) use `LC`-prefixed codes (e.g. `LC378B-2`) instead
  of the standard `GC` prefix. These were previously silently skipped during import. OpenSAK
  now accepts both `GC` and `LC` codes, so lab2gpx files import correctly. Cache type, name,
  coordinates and description are all parsed as expected, and Lab Cache entries are shown with
  the `L` label in the container column.

## [1.13.10] — 2026-05-09

### Added

- **Drag & drop to import GPX / ZIP files** (closes #181) — GPX, ZIP and LOC files can now be
  dragged from a file manager and dropped anywhere on the OpenSAK window. The import dialog opens
  immediately with the dropped files pre-loaded and ready to import. Multiple files can be dropped
  at once. Suggested by Fabio-A-Sa.

- **Target database selector in import dialog** — The import dialog now shows a database dropdown
  pre-filled with the currently active database. Any known database can be selected as the import
  target, making it possible to import a PQ directly into a specific database without switching
  the active database first. Works with both drag & drop and the normal Browse button.

## [1.13.9] — 2026-05-09

### Added

- **File → Export menu with GPX, LOC and GGZ support** (closes #203) — A new *Export* submenu
  has been added under the *File* menu with three file format options:
  - **GPX** — full Groundspeak GPX 1.1 with cache details, logs and attributes
  - **LOC** — lightweight waypoint format supported by most GPS apps and devices
  - **GGZ** — Garmin's ZIP-based container format that lifts the 10,000-cache limit on
    supported devices (e.g. GPSMAP 64/66, Oregon 700+). The GGZ file contains a full GPX
    file plus a Garmin index, identical in structure to GSAK's GGZ export.

  All three formats use corrected coordinates automatically when available. Export runs in
  a background thread so the UI stays responsive for large databases.

- **Export to Google Maps (KML) moved to File → Export** — The *Export to Google Maps (KML)…*
  item has been moved from the *GPS* menu to the new *File → Export* submenu, where it fits
  better alongside the other file export formats.

## [1.13.8] — 2026-05-08

### Added

- **Edit cache in right-click menu** (fixes #124) — A new *✏️ Edit cache…* item has been added
  to the right-click context menu in the cache list. It opens the same edit dialog as
  *Waypoint → Edit cache* in the menu bar, making it faster to edit a cache without leaving
  the list.

- **FTF checkbox in Edit Cache dialog** (fixes #123) — The *Status* tab in the Edit Cache dialog
  now includes a *FTF (First to Find)* checkbox, making it possible to set or clear the FTF flag
  manually directly from the dialog.

- **FTF toggle by clicking the FTF column** — Clicking directly on a cell in the FTF column
  toggles the First to Find flag on or off, the same way the User Flag column works.

- **FTF filter in filter dialog** — A new *FTF (First to Find) 🥇* filter group has been added
  to the *Other* tab in the filter dialog, allowing you to filter caches by their FTF status.

- **Double-click corrected coordinates cell** (fixes #200) — Double-clicking a cell in the
  *Corrected* column now opens the corrected coordinates dialog directly, without needing to
  use the right-click menu.

- **Enhanced corrected coordinates dialog** — The corrected coordinates dialog now shows the
  cache's original coordinates and the entered corrected coordinates in all three formats
  (DMM, DMS, DD), each with a copy-to-clipboard button for easy use in other applications.

### Fixed

- **Clear filter button is now red when active** (fixes #201) — The *✕* clear filter button
  in the toolbar is now displayed in red when a filter is active, making it immediately obvious
  that the cache list is filtered. The button turns gray and is disabled when no filter is applied.

- **Crash on exit during update check** — OpenSAK could crash with a core dump when closing
  the window while a background update check was still running. The update worker is now
  stopped cleanly when the main window closes.

## [1.13.7] — 2026-05-08

### Added

- **Filter profile dropdown in toolbar** — A new dropdown next to the 🔍 filter button lets you
  switch between saved filter profiles instantly without opening the filter dialog. Selecting a
  profile applies it immediately; selecting *None* clears the active filter. The active profile
  is remembered per database and restored automatically on startup and when switching databases.

- **New filter tab: Other** — A fifth tab has been added to the filter dialog with additional
  filter options:
  - **Country / State / County** — text contains search (case-insensitive)
  - **User Flag** — filter on whether the user flag is set or not
  - **DNF** — filter on Did Not Find status
  - **Favorite points** — filter by a minimum/maximum favorite point count

- **Extended Dates tab** — Two new date range filters have been added alongside the existing
  *Hidden date* and *Last log date* filters:
  - **Found by me date** — filter on when you personally found the cache
  - **DNF date** — filter on when a DNF was recorded

### Fixed

- **Filter profile not persisted across restarts** — Selecting a filter profile from the toolbar
  dropdown was not remembered when OpenSAK restarted. The active profile is now saved to
  QSettings per database alongside the sort order and restored on next launch.

- **Selecting "None" in filter dropdown did not update cache list** — Switching back to no filter
  via the toolbar dropdown now immediately refreshes the cache list.

- **Country / State / County filters returned no results** — These filters previously required
  an exact match against a list. They now use case-insensitive *contains* search, consistent
  with the Name and GC code filters.

---

## [1.13.6] — 2026-05-07

### Added

- **Export to Google Maps (KML)** — New menu item under *GPS → Export to Google Maps (KML)…*
  exports the currently filtered caches to a `.kml` file that can be imported directly into
  [Google My Maps](https://www.google.com/maps/d/). The file contains two layers: one for
  geocaches (colour-coded by cache type with paddle icons) and one for custom waypoints.
  Corrected coordinates are used automatically when available.
  Options: include/exclude custom waypoints and already-found caches.

### Fixed

- **Corrected coordinates crash** — Setting corrected coordinates via right-click now saves
  correctly without crashing. The cache list updates immediately to show the 📍 indicator
  without requiring a manual refresh.

---

### [1.13.5] - 2026-05-07

---

**Update notification improvements**

- Update popup now includes a **"See changelog"** link opening the full changelog on GitHub
- Added **"Skip this version"** button — suppresses the popup for that release until a newer version is available
- Manual update check (Help → Check for updates) always shows the popup, regardless of skipped version
- Added automatic update check toggle in Settings → Advanced

---

## [1.13.4] — 2026-05-07

### Added

- **Light / Dark / Automatic theme** — A new *Appearance* section in Settings lets you choose
  between a light theme, a dark theme, or *Automatic* which follows the operating system setting.
  The change takes effect immediately without restarting. Dark mode is detected natively on
  macOS (System Preferences), Windows 10/11 (registry) and modern Linux desktops (freedesktop
  portal / GTK theme).

### Fixed

- **Consistent look across Linux, Windows and macOS** — OpenSAK now forces Qt's *Fusion* style
  on all platforms, giving a uniform baseline appearance regardless of the desktop environment
  or OS theme. A platform-appropriate default font is applied automatically (Segoe UI on Windows,
  SF Pro on macOS, Ubuntu on Linux).

- **Cache list text invisible in dark mode** — The GC code column delegate used hardcoded black
  text in all cases. Rows without a status colour (archived / found / placed) now use
  `palette.text()` so the text is readable in both light and dark themes. Status-coloured rows
  (red / yellow / green pastels) keep black text since the pastel backgrounds are always light.

- **Strikethrough and colour confined to GC code column** (fixes #196) — Strikethrough for
  archived caches and the orange disabled colour were previously applied to the cache name and
  type icon columns as well. They are now shown exclusively in the GC code column, making the
  status easier to read at a glance without affecting the other columns.

- **Theme change did not update all open windows** — Switching theme in Settings left already-
  visible widgets (including the cache list) unchanged until restart. The theme engine now
  explicitly propagates the new palette to every open window and its child widgets, so the
  entire UI updates in one go when you click OK.

---

## [1.13.3] — 2026-05-06

### Added

- **Colour-coded GC codes** (fixes #117) — Cache type colours are now applied to the GC code
  column in the cache list, making it easy to spot cache types at a glance. The colours in the
  *Count:* summary bar have been updated to match.

### Fixed

- **Strikethrough for archived and disabled caches** (fixes #118) — Cache entries that are
  archived or temporarily disabled are now shown with strikethrough text in the cache list,
  giving a clear visual indication that the cache is not currently active.

- **Delete database — empty folder cleanup** (fixes #146) — After deleting a database, OpenSAK
  now checks whether the containing folder is empty. If it is, a prompt is shown offering to
  delete the folder as well, so no orphaned folders are left behind.

---

## [1.13.2] — 2026-05-05

### Added

- **Found status and date set automatically on PQ import** — When importing a standard Pocket
  Query, caches you have found are now automatically marked as found and given the correct found
  date. OpenSAK reads the `<sym>Geocache Found</sym>` flag that Geocaching.com sets in PQ files
  for the requesting user's own finds, then locates your log entry to extract the exact date.
  Your Geocaching username (configured in Settings) is used to match the log; the numeric finder
  ID is learned automatically on first import and stored for faster matching in future imports.

### Fixed

- **FTF false positives on PQ import** — The First To Find flag was incorrectly set on all
  found caches when importing a Pocket Query. The previous detection logic checked whether the
  user's log was the earliest of the five logs shown in the PQ — but Geocaching.com only includes
  the five *most recent* logs, so an old find would often appear first among those five even if
  hundreds of people had found the cache earlier. FTF is now detected exclusively from keywords
  in the user's own log text (`FTF`, `First to find`, `First finder`, `Første til at finde`),
  which is the only reliable signal available from a standard PQ.

---

## [1.13.1] — 2026-05-05

### Added

- **Home location in Geocaching profile** (fixes #183) — A dedicated *Home location* field
  has been added to the *Geocaching profile* section in Settings. This sets a permanent
  home coordinate that is used as the default center point for all new databases and as the
  ★ Home entry in the location dropdown.

- **User locations renamed** (fixes #183) — The *Home coordinates* group in Settings has
  been renamed to *User locations* to better reflect its purpose. The ★ Home entry (from
  Geocaching profile) always appears at the top and cannot be edited or deleted from this
  list — it is managed exclusively via the Geocaching profile section.

- **Welcome dialog on first launch** (fixes #183) — If username or home location is not
  configured, a welcome dialog is shown a few seconds after startup prompting the user to
  open Settings and complete the setup.

### Fixed

- **Map centers on correct location at startup** (fixes #183) — The map now starts at the
  active location for the current database instead of a hardcoded position in Denmark. The
  starting coordinates are injected directly into the Leaflet HTML before the page loads,
  so the correct location is visible from the very first render.

- **Location saved per database** (fixes #183) — Switching the active location via the
  toolbar dropdown now correctly saves the chosen location for that specific database.
  Switching to a different database and back restores each database's own last-used location.

- **Toolbar dropdown reflects active location after DB switch** (fixes #183) — The location
  dropdown in the toolbar now correctly updates to show the active location for the newly
  selected database when switching databases.

- **New database uses Home location as default center** (fixes #183) — When creating a new
  database, the center point is automatically set to the Home location from the Geocaching
  profile. If no Home location is configured, the last active location is used as a fallback.

- **First cache no longer auto-selected on load** — After loading or refreshing caches, the
  first entry in the list was automatically selected and shown on the map without any user
  action. The list now loads with no selection, so the map is not unintentionally panned.

- **test_db_manager match patterns** — Four unit tests used raw translation keys as match
  patterns in `pytest.raises()`. Since `tr()` returns translated text, the patterns never
  matched and the tests always failed. Updated to match on stable substrings present in
  the translated messages.

---

## [1.13.0] — 2026-05-05

### Added

- **Dutch translation** — OpenSAK is now available in Nederlands (Dutch). The translation
  was generated by Claude AI and has not yet been reviewed by a native speaker — feedback
  and corrections are welcome via GitHub issues or the Facebook group.
- **Last log date column** (fixes #186) — A new `Last log` column shows the date of the most
  recent log entry for each cache. The column can be sorted and is populated automatically for
  existing databases via a migration.
- **Enable / disable all cache types** (fixes #159) — The cache type filter now has an
  *Enable all / Disable all* toggle so you can quickly select or deselect every type at once.

### Improved

- **Search performance** (fixes #127) — Name and GC code searches are now pushed to SQL `LIKE`
  queries that exploit the existing B-tree index, making live search significantly faster on large
  databases. An adaptive debounce and minimum-character threshold reduce unnecessary queries while
  typing. Search settings (debounce delay and minimum characters) are available in the new
  *Advanced* tab in the Settings dialog.
