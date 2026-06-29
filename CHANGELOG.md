# Changelog — OpenSAK
All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

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

For planned features and known issues see the [GitHub Issues list](https://github.com/AgreeDK/opensak/issues).

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
