# Changelog — OpenSAK
All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [1.15.0-beta.12] — 2026-07-13

> **Beta release** — a handful of small quality-of-life improvements
> (#471, #499, #546, #559).

### Added

- **Double-click a cache row to open it on geocaching.com** (#471) —
  matches GSAK's behaviour. Double-clicking the corrected-coordinates
  column still opens the coordinate editor as before; double-clicking the
  flag/first-to-find/locked columns (which already toggle on a single
  click) does not also open a browser window.
- **Option to show hints decoded by default** (#499) — new checkbox under
  Settings → Display. Off by default (hints still start hidden, unchanged
  behaviour) — turn it on to skip the extra click every time.

### Fixed

- **Dynamic map zoomed out to show the whole world for caches with
  hidden-coordinate waypoints** (#546) — waypoints with 0/0 coordinates
  (e.g. a finale left hidden after a GSAK import) are now ignored when
  auto-fitting the map to a cache's waypoints, instead of being included
  in the zoom bounds.
- **Clear-filter button (✕) didn't show a hover highlight** (#559) — it
  looked non-interactive compared to the rest of the toolbar. It now
  highlights on hover when active, same as before when inactive.

---

## [1.15.0-beta.11] — 2026-07-12

> **Beta release** — fixes the install/database folder migration (#562):
> changing folders via the setup wizard now actually moves your settings,
> databases, and custom icons instead of leaving them behind.

### Fixed

- **Changing the install/database folder via the setup wizard didn't move
  anything** (#562) — re-running the setup wizard (Settings → Advanced →
  "Run setup wizard again") with a different install and/or database folder
  only updated the stored *pointers*, never the actual files. Depending on
  what changed, this could silently reset all settings (opensak.json,
  including the database list, was left behind in the old install folder),
  or leave existing databases behind in the old database folder while the
  app pointed at an empty new one. Fixed:
  - Settings (opensak.json) now move with the install folder; if the
    destination already has one, a clear warning explains that nothing was
    moved instead of failing silently.
  - Changing the database folder now offers to move existing databases
    along — same "Move and keep / Move and delete / Skip" choice already
    used by Settings → Advanced's direct database-folder field.
  - Moving and deleting the active database used to leave it disposed but
    never reopened, crashing with "Database not initialised" the moment
    anything touched the database before the next restart (e.g. closing
    the Settings dialog). It's now reopened immediately at its new path.
  - The "New Database" dialog's default/browse folder pointed at the
    install folder instead of the configured database folder.
  - Choosing "Move and delete" (or moving the install folder) now also
    cleans up what's left behind: the old database folder, custom icon
    packs (#519) and the Geocaching.com OAuth token are moved along, and
    old folders (including nested ones, e.g. a "Data" folder inside the
    install folder) are removed once genuinely empty — never forced if
    anything couldn't be moved.

---

## [1.15.0-beta.10] — 2026-07-10

> **Beta release** — adds a way to support OpenSAK's ongoing costs (code
> signing, notarization) directly from the app, README, and website, now
> that the project is fiscally hosted by Open Source Collective.

### Added

- **"Support OpenSAK" menu item** — a new entry in the Help menu opens
  `opencollective.com/opensak` in the default browser, so donations are one
  click away without leaving the app. Localized across all 8 languages.
- **Open Collective links on README and opensak.com** — a backers badge and
  Support section in the README, plus a Sponsor link in the site nav,
  footer, and open-source section.

---

## [1.15.0-beta.9] — 2026-07-09

> **Beta release** — five fixes: three new bugs plus two carried over from
> beta.8 real-world testing.

### Fixed

- **Found count under the grid counted found caches, not found logs** (#552)
  — the footer's yellow "Found" count summed `Cache.found` (a boolean), so a
  relocatable/multi-visit cache found several times only ever contributed 1.
  Verified against Mike Wood's real 5,505-cache GSAK database: his own
  found-type logs (`Found it` + `Attended` + `Webcam Photo Taken`) total
  5,556, matching GSAK/geocaching.com exactly once the count switched to
  logs. A new cached `found_log_count` column (computed on import, migration
  backfills existing databases) drives the total; a `max(count, 1)` safety
  net means the total never regresses below the old behaviour for users
  without a configured GC username. Reported by Mike Wood.

- **Filter couldn't be cleared via the toolbar "None" dropdown or Escape**
  (#553) — a status-bar-triggered filter (e.g. clicking the yellow "Found"
  count) applies with a UI label that doesn't match any saved profile, so
  the toolbar dropdown fell back to showing "None" while the filter was
  still active behind the scenes. Re-selecting "None" from the dropdown
  didn't change its index, so the clearing signal never fired. Also adds a
  configurable Escape shortcut to clear the active filter, matching GSAK
  muscle memory. Reported by Mike Wood.

- **Large Text setting not applied consistently — GC Code column stood out**
  (#547) — cells coloured for found/owned/archived caches drew their text
  directly, bypassing the mechanism that applies the "Large" text-size
  setting, so those GC codes silently rendered at the wrong size while
  every other cell (and every other column) respected it. Reported by
  CheminerWill.

- **`new_database()` could silently reopen an orphaned file at the same
  path** (#539) — residual beta.8 feedback: creating a new database with a
  previously-used name could silently reopen the old file's caches and
  column settings instead of starting empty, since the guard rename()
  already had wasn't applied to database creation. Reported by GeePa67.

- **Premium status (`IsPremium`) not imported from GSAK DB files** (#541) —
  the direct-DB importer never read GSAK's `Caches.IsPremium` column, so
  every GSAK-DB-imported cache came in as non-premium regardless of source
  data; the GPX importer already handled this correctly.

---

## [1.15.0-beta.8] — 2026-07-08

> **Beta release** — one critical performance fix plus three GSAK Database
> Import fixes found during beta.7 real-world testing.

### Added

- **Trackables now imported from GSAK databases, with a new Trackables tab**
  (#538) — `CacheMemo.TravelBugs` was previously deliberately out of scope
  for the GSAK importer. Parses GSAK's one-line-per-trackable format
  (`Name (id = TrackableID, ref = Trackable Reference)`) into the same
  Trackable model already used by GPX import. Like Waypoints/Attributes/Logs,
  Trackables are now rebuilt on every re-import of a given cache, rather than
  being left untouched — a deliberate change from the previous behaviour,
  consistent with the importer's one-time-migration philosophy. The cache
  detail window also gets a new Trackables tab (between Attributes and
  Notes) listing each trackable with a clickable `coord.info` link, mirroring
  GSAK's own Trackables list. Reported by nagisml.

### Fixed

- **Critical: severe UI freeze switching to or filtering a large database**
  (#540) — every single icon lookup (cache-type icon + found-smiley, per
  cache row) resolved the user-replaceable icon-override folder (#519) from
  scratch: a file-existence check, a JSON read, and four `mkdir()` calls,
  every time. On a ~19,000-cache database that added up to 38,000+ avoidable
  filesystem calls per table refresh (switching databases, changing a
  filter, or plain Refresh) — individually fast, but each one commonly
  intercepted synchronously by antivirus real-time protection on Windows,
  compounding into a 45-60 second "Not Responding" freeze that scaled with
  the number of rows rendered rather than database size. The icon-override
  folder is now resolved once per app session instead of once per icon
  lookup per row. Reported via issue #540, confirmed against
  `v1.15.0-beta.5` (unaffected, predates #519) vs `beta.7` (affected).

- **GSAK Import: waypoints with the same name but different codes were
  silently dropped** (#536) — the unique constraint on waypoints was
  `(cache_id, prefix, name)`, but GSAK's own per-cache waypoint identity is
  its own code (`cCode`, stored as `wp_code`), not the name — real GSAK
  databases can have distinct waypoints on one cache sharing a name (e.g.
  several stages a user happened to name identically), and the second one
  was being treated as a duplicate and dropped. The constraint is now
  `(cache_id, wp_code)`; GPX-imported waypoints (which never set `wp_code`)
  are unaffected, since SQL treats multiple NULLs as distinct for
  uniqueness. Reported by nagisml.

- **Renaming a database didn't move the underlying file** (#539) — only the
  display name was updated; the physical `.db` file (and any `-shm`/`-wal`
  sidecars) stayed at its original path. Creating a new database under the
  now-freed old name silently reused that same never-moved file and "came
  back" with all its old content and column layout intact. Renaming now
  moves the actual file, rejects the rename if a file already exists at the
  target path, and migrates the saved column visibility/width settings
  (#199) to the new name. The toolbar dropdown and window title — which
  previously only refreshed on a database *switch*, not a rename — now
  update immediately too. Reported by GeePa67.

---



> **Beta release** — three fixes for the GSAK Database Import feature (#469),
> found during beta.6 real-world testing.

### Fixed

- **Leftover `favorite_point` column crashed inserts on some databases** (#530)
  — `favorite_point` (singular, Boolean NOT NULL) briefly existed in v1.14.0
  without a migration to add it to already-existing databases (#488). The fix
  for #488 removed the field from the model, filters, and UI entirely, but
  never added a migration to drop the physical column from databases that had
  already been created with it present during that window. On those
  databases, every insert into `caches` failed with `NOT NULL constraint
  failed: caches.favorite_point`, since current code has no knowledge of the
  column and never supplies a value for it. A new migration now detects and
  removes the leftover column. Reported independently by Bob Long and C3GPS.

- **GSAK databases with non-UTF-8 text fields aborted the entire import**
  — GSAK is a Windows desktop app and doesn't guarantee its `sqlite.db3`
  stores text as UTF-8; older or rarely-touched fields (e.g. `SmartName`,
  part of GSAK's "Smart Names" macro feature) may still hold whatever system
  codepage the row was written under, commonly Windows-1252 for Western
  European installs. The GSAK connection now falls back from UTF-8 to
  cp1252, then to a replacement-character decode as a last resort, so one
  oddly-encoded legacy field can no longer abort an otherwise-healthy
  import. Reported by Thomas Bang Christensen.

- **Adventure Lab caches imported as "Unknown Cache" instead of "Lab Cache"**
  (#532) — GSAK's own `CacheType` field uses single-letter codes; the
  importer's mapping table only covered 13 of the ~19 codes GSAK documents,
  and Adventure Lab caches (code `Q`) fell through to the "Unknown Cache"
  default. Six more codes with an unambiguous OpenSAK equivalent (Project
  A.P.E., Geocaching HQ Cache/Block Party, Giga-/Mega-Event, GPS Adventures
  Maze) are now mapped too. Reported by Véé X Péé on Facebook, and visible
  independently in C3GPS's #530 GSAK import log.

---

## [1.15.0-beta.6] — 2026-07-07

> **Beta release** — GSAK Database Import and user-replaceable icon packs,
> plus two fixes found during beta.5 testing.

### Added

- **Direct GSAK database import** (#469) — import an entire GSAK
  `sqlite.db3` file straight into an OpenSAK database, without going via
  GPX first. New "Import GSAK Database" dialog (mirrors the existing GPX
  import dialog's background-worker/progress-bar pattern) reads caches,
  waypoints, attributes, logs (full history, not capped like GPX/PQ
  exports), corrected coordinates, and personal notes directly from the
  GSAK schema. Confirmed against two independent real-world GSAK
  databases (one 12,600 caches, one 1.1M log rows) during development.
  Notes containing GSAK's embedded local image references (from GSAK's
  "GrabImages" macro) are replaced with `[image: filename.ext]`
  placeholders, since the referenced local files aren't portable between
  machines. GSAK's `FoundCount` field was confirmed to be identical to
  `Found` (a personal 0/1 flag) rather than a true community find count,
  so it is deliberately *not* imported into `find_count` — importing it
  would silently show a wrong number rather than the real GC.com count.
  GSAK custom fields, the Ignore list, and Travel Bugs are out of scope
  for this first pass (tracked separately in #473).

- **User-replaceable icon packs** (#519) — custom cache-type and found-
  smiley icons can now be dropped into a new `icons/` folder (Settings →
  Advanced → "Open icons folder") without touching any code or rebuilding
  the app. The folder lives alongside `opensak.json`, so it survives app
  updates/reinstalls — unlike files placed inside the install directory
  itself, which get wiped on every new release. File names match the
  bundled assets 1:1 (e.g. `cache_types/traditional_cache.svg`,
  `cache_found/found_cache_smiley_green.svg`), matching the community's
  OpenSAK Custom Icons Guide. Also covers the fixed, single-instance UI
  icons that have no bundled asset file of their own — Corrected
  coordinates, Premium, Fav. points and Trackables — via a new
  `icons/ui/` subfolder (`corrected_coords.svg`, `premium.svg`,
  `favorite_points.svg`, `trackable.svg`). Missing or invalid custom
  files fall back to the bundled/default icon automatically — never a
  crash. A new bundled, offline "View icon naming guide" button
  (Settings → Advanced) lists every file name, recommended canvas size
  and practical tips (Inkscape, Plain SVG export, avoiding unsupported
  SVG features) in one place.

  While wiring up the guide, found and fixed a packaging gap:
  `opensak.spec` never listed `assets/icons/cache_types/` or
  `assets/icons/cache_found/` in PyInstaller's `datas`, so packaged
  builds (Windows/macOS/AppImage) were silently falling back to the
  hand-coded placeholder SVGs for every cache type and found-smiley
  instead of the real bundled artwork — only source checkouts had the
  actual icons. Both folders (and the new guide file) are now bundled
  correctly.

### Fixed

- **County not imported from GSAK-exported GPX files** (#521) — the official
  Groundspeak GPX schema has no `<county>` element at all (only country and
  state), so GSAK adds its own `<gsak:County>` inside `wptExtension` to fill
  that gap when exporting with "Include GSAK fields" checked. The importer
  only ever looked for county on the standard `groundspeak:cache` block, so
  county was silently dropped even though it was present in the file. GSAK's
  own county value is now read as a fallback whenever the official schema
  doesn't provide one — which, for county, is always.

- **Distance column could show stale values after editing a home point** (#522)
  — center points switched via the toolbar dropdown always recalculated
  distances correctly, but editing a home point's own coordinates (or adding
  a new one that becomes active) from Settings updates the active
  coordinates via a different code path that never triggered a recalculation.
  The persisted distance column then stayed stale until the next manual
  center-point switch, which could make the Distance column and "sort by
  distance" silently disagree with the actual active center point. Settings
  now recalculates distances whenever the dialog is accepted.

---

## [1.15.0-beta.5] — 2026-07-05

> **Beta release** — four quality-of-life fixes found during beta.4 testing.
> No schema changes.

### Fixed

- **Deleting the active saved filter left it applied** (#491) — deleting a
  filter profile only refreshed the "Set filter" dialog's own list; if the
  deleted profile was the one currently active and the dialog was then
  closed without pressing Apply, the stale filter stayed applied to the
  waypoint list and the toolbar still showed the deleted name. Deleting a
  profile now immediately clears the active filter (and refreshes the cache
  table) if it was the one in use, regardless of how the dialog is
  subsequently closed. Deleting a different, non-active profile only
  refreshes the toolbar's profile list, and quick search text (GC code /
  name) is left untouched either way.

- **Flag and locked column icons distorted on found rows** (#509) — both
  columns rendered their "set" state as plain emoji text ("🚩" / "🔒"). The
  cache list italicizes every column on found-cache rows, and emoji glyphs
  generally have no real italic form — some platforms (Windows' Segoe UI
  Emoji in particular) synthesize one by shearing the glyph box, clipping
  or distorting it. Both columns are now icon-only in both states, the same
  treatment already used for Found/Premium/Corrected Coordinates: a new
  solid red flag icon and solid closed-padlock icon when set, the existing
  faint outline placeholders when unset. No text/font styling can touch
  them anymore.

- **File-mode GPS export silently overwrote existing files** (#501) —
  exporting to a regular file (as opposed to syncing to a connected Garmin
  device) reused the same default filename ("opensak") every time without
  warning, so re-exporting could silently overwrite a previous export. The
  export dialog now checks whether the target file already exists and, if
  so, prompts for a new filename — pre-filled with the next available
  suggestion (opensak, opensak1, opensak2, ...) so the common case is just
  pressing OK. Re-prompts if the new name also collides, and cancels
  cleanly without exporting if the user backs out. Device-mode exports are
  unaffected — they intentionally keep syncing to the same canonical
  Garmin/GPX path every time.

- **Filters with zero matches emptied the cache list** (#444) — applying a
  filter that didn't match any caches silently switched to an empty view,
  requiring the red "clear filter" button and reopening "Set filter" from
  scratch to recover. Matching GSAK's behavior, a filter matching zero
  caches is no longer applied at all: a warning is shown, and "Set filter"
  reopens with the same (rejected) criteria still filled in so they can be
  adjusted, while the previous view and active filter stay untouched. Only
  affects the advanced filter dialog — the quick-filter dropdown and name/
  GC-code search fields are separate mechanisms where an empty result is
  normal and unaffected.

---

## [1.15.0-beta.4] — 2026-07-04

> **Beta release** — a new Trackables column and GSAK-style icons for
> Found/Premium/Fav. points, plus a batch of bugs found and fixed during
> testing. Three of them were serious enough to also backport to stable
> as v1.14.1 — noted individually below.

### Added

- **Trackables (travel bugs / geocoins) column** (#489) — a new opt-in
  column (enable via Column Chooser) showing how many trackables are
  logged in each cache, with an insect icon in the header. This builds
  on the existing Trackable data model, which has quietly supported
  import parsing and the "Has trackables" filter since v1.14.0 — only
  the column itself was missing. The count is cached on the cache row
  (same approach as the Last log column's log count), so displaying
  and sorting it doesn't slow down large databases.

- **GSAK-style icons for Found, Premium and Fav. points** (#489) — Found
  and Premium now show an icon instead of plain text in the cache list
  (a gold smiley reused from the map pins, and a checkered-circle-with-
  cache icon for Premium), and Fav. points gets an emblem icon in its
  header. All four new icon-only column headers — plus Trackables — get
  the same icon-and-sort-arrow centering already introduced for
  Corrected Coordinates in beta.2, so the pair stays together as the
  column is resized.

### Fixed

- **Potential crash on databases with a "Favourite" (★) column enabled**
  (#488) — a manual per-cache favourite flag with no GSAK equivalent had
  shipped without a database migration, which could leave the column
  missing entirely on some databases. Removed — GSAK only tracks the
  community "Fav. points" count, which is unaffected. As a safety net,
  the column list now also ignores any other stale column ID left over
  from a future column removal, instead of rendering an empty,
  untranslated column.

- **"Has trackables" filter crashed on any database created before
  v1.14.0** (#491) — the Trackable table itself was missing its
  creation migration since the feature was first added. **Also included
  in v1.14.1.**

- **Map didn't update when correcting coordinates via the cache list's
  right-click menu** (#474) — unlike the same action from the cache
  detail panel, the context-menu path never told the map to refresh.
  Fixed in three parts: the map now refreshes at all; it reveals the
  pin from an unopened marker cluster when the new location is far from
  the current view (previously this only worked if the cache was
  already selected); and correcting a *different* cache than the one
  currently selected no longer shifts focus away from it. **Also
  included in v1.14.1.**

- **SMALL row-height setting silently ignored on some systems** (#490)
  — Qt's platform/font-derived minimum row height could be larger than
  our 20px SMALL setting, silently overriding it without any visible
  error. **Also included in v1.14.1.**

---

## [1.15.0-beta.3] — 2026-07-02

> **Beta release** — fixes two found-status bugs reported by the community
> while testing beta.2.

### Fixed

- **Found date missing for webcam caches and events on import** (#457) —
  `found_date` was derived only from "Found it" logs, so caches whose find
  is logged under a different log type ended up with no found date on
  import: webcam caches use "Webcam Photo Taken" and events use "Attended".
  Reproduced with a real My Finds PQ. The cross-database found-status sync
  tool had the same bug and is fixed too.

- **FTF detection flagged logs that only mentioned "first to find" in
  passing** (#458) — FTF was matched on free-text phrases (`ftf`,
  `first to find`, `first finder`, `første til at finde`) anywhere in the
  user's own found-log text, a heuristic introduced in 1.13.2. That flagged
  logs which merely mentioned the concept without claiming it, e.g. a log
  describing a *failed* attempt at being first finder. FTF is now matched
  exclusively against ProjectGC's official tags — `{FTF}`, `{*FTF*}`,
  `[FTF]` — the same tags most geocachers already use and that ProjectGC
  itself requires. A tooltip on the FTF column header explains this.

---

## [1.15.0-beta.2] — 2026-07-02

> **Beta release** — fixes for issues reported by the community while
> testing GGZ export in beta.1. Please keep testing, especially with
> larger databases.

### Fixed

- **GGZ export crash on databases with mixed dated/undated logs** (#348)
  — exporting a database where at least one cache had a log without a
  date crashed with `'<' not supported between instances of
  'datetime.datetime' and 'int'`. Logs are now sorted safely regardless
  of missing dates.

- **GGZ files written to the wrong folder on the device** (#348) — GGZ
  exports landed in `Garmin/GPX` instead of `Garmin/GGZ`, unlike GSAK's
  GarminExport macro and what Garmin devices expect.

- **Wrong dates inside exported .ggz files** (#348) — the ZIP directory
  entries (`data/`, `index/`, etc.) always showed 1980-01-01 due to a
  Python zipfile default; they now show the actual export time.

- **Severe slowdown on large GGZ exports** (#466) — exporting several
  thousand caches could take 50+ minutes and make the app unresponsive.
  The byte-offset calculation for Garmin's index was accidentally O(n²);
  it's now a single linear pass — 200–1000× faster depending on cache
  count, with the gap widening for larger databases.

- **Corrected Coordinates column icon inconsistency** (follow-up to
  #354) — the "Choose columns" dialog still showed the old red pin
  emoji instead of the warning-triangle icon used everywhere else in
  the app. The column header's icon and sort arrow are now centered
  together as a pair regardless of column width, and the per-row icon
  in the column itself is centered instead of left-aligned.

---

## [1.15.0-beta.1] — 2026-07-01

> **Beta release** — start of the 1.15.0 testing period. Feedback especially
> welcome on the new GGZ export.

### Added

- **Export to Garmin GGZ format** (closes #348) — the GPS export dialog now
  offers a GPX/GGZ format choice. GGZ packs the exported caches (unlimited
  count, unlike GPX-based transfers) directly into a ZIP structure Garmin
  devices read natively, matching GSAK's GGZ layout byte-for-byte.

### Fixed

- **Corrected Coordinates icon visibility** (closes #354) — the small red pin
  emoji used to mark caches with corrected coordinates was hard to see or
  missing entirely on some systems. Replaced everywhere (table column,
  column header, right-click menu, and the cache detail panel) with a
  consistent SVG warning-triangle icon, in the same style GSAK uses for the
  same purpose.

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
