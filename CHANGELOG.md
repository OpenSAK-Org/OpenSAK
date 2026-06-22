# Changelog — OpenSAK
All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [1.14.0-beta.8] — 2026-06-22

> **Beta release** — continuing the 1.14.0 testing period.

### Fixed

- **Hint encoding detection reversed** (fixes #329) — geocaching.com Pocket Query
  GPX files deliver hints as **plaintext** in the `<groundspeak:encoded_hints>`
  tag, but OpenSAK assumed the field always contained ROT13-ciphertext. The
  display logic was backwards: OpenSAK would show plaintext hints as gibberish
  and vice versa. A new heuristic `split_hint()` function analyzes vowel
  density to detect whether the hint is human-readable plaintext or encrypted
  ROT13, and displays accordingly. Default display is always **obscured**
  (ciphertext); pressing "Decode hint" reveals the plaintext. The same fix
  was applied to KML export. Regression tested against 24 real Danish hints.

- **Google Maps link didn't open** (fixes #321) — the cache detail pane has an
  option to open a cache's coordinates in an external map app (Google Maps or
  OpenStreetMap). The settings dialog stored the choice as `"google"`, but
  the code checked for `"googlemaps"`, so the condition never matched and
  clicking the link did nothing. Fixed by aligning the constant.

- **Cache detail panel custom delegate segfault** (related to #286/#287) —
  when implementing text/icon size scaling, the `SizeBarDelegate` (which
  renders the size bar in the cache list) was calling `get_settings()` during
  every `paint()` event, which occurs hundreds of times per second as rows
  are rendered. This caused a segmentation fault in the test suite. Fixed by
  caching the icon size in the delegate and updating it only when settings
  actually change.

### Changed

- **UI text and icon sizes are now adjustable** (fixes #286, #287, #290) —
  a new dropdown in Settings → Display: "Text and icon size" offers three
  choices: Small, Medium (default), and Large. Affects the cache type icon
  size in the list view, the cache detail panel title and metadata labels,
  and the tab labels. Useful for users with reduced visual acuity. The
  setting persists across restarts and is per-database.

### Added

- **Debug logging for reverse geocoding module** (closes #232) — the `geo`
  module (boundaries, packs, store) now supports the same debug-flag system
  as the other modules. Enable via `debug_flags.py` to see logs of when the
  boundary database opens, packs are loaded, and coordinates are resolved to
  country/state/county. Logs appear in `opensak.log` when the flag is active.

---

## [1.14.0-beta.7] — 2026-06-19

> **Beta release** — continuing the 1.14.0 testing period.

### Fixed

- **Filter ignored the "no corrected coordinates" case** (fixes #274) — the
  filter engine only had a `HasCorrectedFilter`; with no negative
  counterpart, unchecking "has corrected coordinates" while leaving only
  "no corrected coordinates" checked in the filter dialog silently produced
  no filter at all, so unsolved mystery caches weren't excluded as expected.
  A new `NoCorrectedFilter` was added, mirroring the existing
  Premium/Non-Premium filter pair.

- **Some owner-placed caches weren't colored or counted correctly**
  (fixes #272) — caches whose `owner` field came from a GSAK "statistics
  macro" export (e.g. `Cheminer Will (F=1361 H=54)`) failed to match the
  configured GC username, because the trailing `(F=N H=N)`-style suffix —
  and irregular whitespace, including non-breaking spaces — was never
  stripped before comparing. A new `normalize_geocacher_name()` helper now
  handles both cases, and is used consistently for the GC Code coloring,
  the info-bar owned count, and the "owned" filter.

### Changed

- **Owned-cache counting and coloring now use the `owner` field instead of
  `placed_by`** (issue #270) — matches GSAK's behavior, where an adopted
  cache (placed by one person, currently owned by another) is attributed to
  its current owner rather than the original placer. Clicking the
  "My caches" tile in the info bar now filters by owner as well.

### Added

- **Full log text is now shown without truncation** (fixes #218) — the Logs
  tab previously cut log text off at a fixed length with "…"; since the log
  viewer already scrolls, the full text is now always shown.

- **Links in logs are now clickable** (fixes #219) — Markdown-style links
  (`[text](url)`), as used in Geocaching.com Pocket Query exports, are now
  converted to real clickable links that open in your system's default
  browser, matching the existing behavior of the cache description tab.
  Plain `[square brackets]` not followed by `(url)` are left untouched, and
  search highlighting in logs continues to work correctly alongside links.

---

## [1.14.0-beta.6] — 2026-06-18

> **Beta release** — continuing the 1.14.0 testing period.

### Added

- **GSAK personal/user fields are now imported** (closes #269) — GPX files
  exported from GSAK itself (not standard Geocaching.com Pocket Queries) now
  have their `UserFlag`, `IsPremium`, `UserSort`, `UserData`/`User2`/`User3`/
  `User4`, and `FavPoints` fields imported into the matching `Cache` columns
  (added back in #33, previously left unused). A field is only overwritten
  when GSAK actually supplies a value, so a later plain Pocket Query
  re-import won't wipe data carried in from a GSAK-sourced import.
  `gsak:County` was not added separately, since `county` is already
  populated from the standard Groundspeak `gs:county` field.

### Fixed

- **GSAK GPX logs were capped at 20 entries** (fixes #266) —
  `_render_log_html()` had a hardcoded `[:20]` slice that silently dropped
  any logs beyond the first 20 (and any matching logs beyond 20 when
  searching), even though the importer itself had no such limit. All logs —
  and all matching logs when searching — are now shown. An outdated comment
  referencing "show up to 10 most recent" was also corrected to match the
  actual behavior.

---

## [1.14.0-beta.5] — 2026-06-17

> **Beta release** — continuing the 1.14.0 testing period.

### Changed

- **GC Code colors now match GSAK** (issue #270) — found caches now show on
  a yellow background (previously green), and your own caches show on a
  green background (previously yellow), matching the colors long-time GSAK
  users already know. Disabled caches now use black text on the red
  background instead of orange-on-red, which was nearly unreadable.
- **The count panel in the info bar now uses the same colors as the GC Code
  column** — black text on a colored background instead of colored numbers
  on a plain background.

### Added

- **Clicking a colored count in the info bar filters the cache list** to
  that status — click "Found" to see only found caches, "My caches" for
  your own, "Inactive" for archived/disabled caches, or "All" to clear the
  filter. Mirrors GSAK's clickable status counts.

---

## [1.14.0-beta.4] — 2026-06-17

> **Beta release** — continuing the 1.14.0 testing period.

### Fixed

- **Update checker failed with SSL certificate errors on Windows** — the
  bundled `.exe` could not verify HTTPS connections to the GitHub API
  (`CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`),
  because `certifi`'s root certificate bundle was not included in the
  PyInstaller build and the code relied on the system's certificate store,
  which isn't always reliably accessible from a bundled executable. The
  updater now explicitly uses `certifi`'s certificate bundle via a
  dedicated SSL context, and the bundle is packaged with the build.

---

## [1.14.0-beta.3] — 2026-06-17

> **Beta release** — continuing the 1.14.0 testing period.

### Added

- **Database and installation folders are now visible in Settings → Advanced**
  — a new "Folders" section shows the current installation folder
  (read-only) and the database folder. The database folder can be changed
  directly from Settings, not just during the initial setup wizard.

- **Moving existing databases to a new folder** — when changing the database
  folder in Settings, if existing databases are found, you're now asked
  whether to move them along: "Move and keep originals", "Move and delete
  originals", or leave them where they are (only new databases will use the
  new folder). Moving correctly handles SQLite WAL/SHM sidecar files and
  protects against accidentally overwriting an existing file at the
  destination.

---

## [1.14.0-beta.2] — 2026-06-17

> **Beta release** — continuing the 1.14.0 testing period.

### Added

- **Beta-aware update notifications** — users running a beta version are now
  checked against the full GitHub releases list to find a newer beta, and see
  a distinct "new beta version available" message instead of the normal
  update prompt. Users running a stable (main) release are unaffected and
  continue to only ever be offered stable updates, as before.

---

## [1.14.0-beta.1] — 2026-06-17

> **Beta release** — testing the new 1.14.0 settings/database architecture before
> it becomes the stable release. Please report any issues found while testing.

### Added

- **JSON-based settings store** (closes #209) — replaces QSettings and the old
  `preferences.json` with a single `opensak.json` file in the install directory.
  A small `bootstrap.json` on the platform-standard config path points to the
  install directory. Existing installations are migrated automatically and
  transparently on first launch of this version.

- **Welcome wizard for first-run setup** (closes #210) — new installations now
  walk through a 5-step wizard: language, installation folder (settings/logs),
  database folder, optional Geocaching.com profile, and a final confirmation
  screen. Existing installations skip the wizard automatically.

- **Per-database column views with drag-to-reorder** (closes #199) — visible
  columns and their widths are now remembered separately for each database.
  Column headers can be dragged to reorder them, and the new order is saved
  and restored automatically, including across restarts.

- **Debug logging system** (closes #232) — a lightweight, always-on logging
  system writes to `opensak.log` in the install directory. The log resets on
  every startup and rotates at 1 MB. Per-module debug flags (currently only
  the update checker) can be toggled in `debug_flags.py` without touching the
  calling code. "Open log file" was added to the Help menu so the file is easy
  to find and attach when reporting issues.

### Fixed

- **Boolean settings could silently corrupt to base64 strings** — a bug in the
  new JSON settings store caused `True`/`False` values (e.g. "automatically
  check for updates") to occasionally be written as base64-encoded byte
  strings instead of real booleans, because `bool` is an `int` subclass and
  was caught by a `bytes()` coercion meant only for Qt's `QByteArray`. Existing
  corrupted values are repaired automatically on startup.

- **Update checker mishandled pre-release version tags** — `_parse_version`
  previously fell back to a sentinel value for any tag with a `-beta.N` /
  `-alpha.N` / `-rc.N` suffix, which would have made this very beta appear
  older than any released version. Pre-release tags now compare correctly
  against both stable releases and other pre-releases of the same version.

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

For planned features and known issues see the [GitHub Issues list](https://github.com/AgreeDK/opensak/issues).

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
