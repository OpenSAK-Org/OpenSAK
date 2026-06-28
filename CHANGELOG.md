# Changelog — OpenSAK
All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [1.14.0-beta.16] — 2026-06-29

> **Beta release** — continuing the 1.14.0 testing period. Supersedes the
> `v1.14.0-beta.15` tag, which was cut from a commit where `__init__.py` had
> not actually been bumped yet (see Notes) and never got a published release
> as a result.

### Added

- **Lock a cache against import overwrites** (closes #202) — a long-requested
  GSAK feature. Locking a cache (via the new 🔒 column, or the checkbox in
  "Edit cache…") freezes its scalar fields — name, type, container,
  coordinates, D/T, owner, status, descriptions, hint, country/state/county —
  exactly as they are, so a later PQ/GPX re-import (e.g. after the listing
  gets a difficulty rerate) can't silently change data your stats already
  depend on. Logs, attributes and waypoints still refresh normally on
  re-import — locking only protects the cache record itself. Filterable via
  a new "Locked" Yes/No group in the filter dialog, sortable like any other
  column.

- **Personal notes, round-trippable with GSAK** (closes #389, #390, #391, #392) —
  the cache detail panel has a new "Notes" tab for your own free-text notes per
  cache, separate from the geocaching.com description and logs. Notes are
  parsed in on import from GSAK-exported GPX (`gsak:UserNote` in the
  `wptExtension` block) without ever overwriting an existing non-empty note on
  re-import, and are written back out the same way on export, so a note
  survives an export → GSAK → re-import round-trip. The tab is hidden when no
  cache is selected instead of showing an empty, clickable-looking editor.
  Closes another piece of the GSAK field-parity goal from issue #33.

- **Child waypoints are finally visible in the UI** (closes #376, #377, #378,
  #393) — waypoints were already imported and stored, but invisible unless you
  knew to look on the map. Cache names with waypoints now show in **bold** in
  the list, the detail panel has a new "Waypoints" tab (count shown in the tab
  title) listing each waypoint's prefix, type, name, coordinates, description
  and comment, and selecting it shows the waypoint markers on the map. Closes
  the "child waypoint gaps" item from the backlog.

- **Attributes tab in the cache detail panel** (closes #417) — a new tab lists
  every cache attribute with a green ✓ or red ✗ marker and the attribute name,
  with a "(No attributes)" placeholder when there are none. Tab title shows
  the attribute count, matching the pattern used for Waypoints and Notes.

- **Keyboard Shortcuts dialog** (closes #205) — Help → "Keyboard Shortcuts…"
  opens a searchable reference of every shortcut in the app. Shortcuts are now
  managed through a central registry, with any user overrides persisted in
  QSettings and reapplied on startup.

- **Full-text search filter** (closes #294) — a new "Text Search" tab in the
  filter dialog searches cache descriptions (short + long), logs, and personal
  notes (hint text off by default). Uses SQL `EXISTS`/`LIKE` pushdown rather
  than loading every cache into Python to search it, so it stays fast on large
  databases.

- **Cache type icon in the detail panel** (closes #286) — the cache title in
  the detail panel now shows its type icon to the left, scaling with the
  Small/Medium/Large text-size setting. While building this, the found/DNF
  smiley overlays on map pins were also corrected — found caches always get
  the gold smiley and DNF caches the dark-blue one, independent of cache type,
  matching GSAK's convention.

- **Type column display options, plus assorted visual fixes** (closes #413,
  #414, #415, #416) — the cache-type column can now show an icon only (default),
  the type name as text, or both, via a new setting in the column dialog.
  Alongside that: the column's default width was too narrow to show a type
  name comfortably (now 40px), its content wasn't centred, and in bar-mode
  (size bars) the old circular type badge briefly peeked out from behind the
  bar segments — all three fixed.

- **Distance calculation reworked: computed once per centre-point change
  instead of on every refresh** (closes #60) — distance and bearing used to be
  recalculated from scratch on every filter change, sort, search keystroke and
  import, which got noticeably slow on large databases. They're now stored on
  the cache row and recomputed only when the centre point actually changes, with
  sorting pushed into SQL. A new **Vincenty (WGS84)** method is also available
  alongside the existing Haversine default in Settings → Advanced → Distance
  Calculation, for up to ~0.3% more accurate distances over long ranges.

- **Active filter count in the info bar** (closes #373) — the info bar now
  shows e.g. "3 filters active" instead of a generic "Active" label, so you can
  tell at a glance how many filter conditions are currently applied without
  opening the dialog.

### Fixed

- **A companion `-wpts.gpx` file could be imported as a second, duplicate set
  of caches** (fixes #410) — GSAK and others export a cache's child waypoints
  to a separate file alongside the main GPX. Detection used to key off the
  `-wpts` filename suffix, so a renamed companion file slipped through and got
  imported as if it were its own set of caches. Detection now inspects the
  file's actual content instead of its name, so renaming doesn't fool it.

- **Container/size column sorted alphabetically instead of logically**
  (fixes #412) — a Virtual Cache (`container = "Other"`) sorted under "O", and
  physical sizes sorted as text ("Large" before "Micro") rather than by actual
  size. Container sort now follows the same micro→small→regular→large
  ordering already used for the on-screen size bars.

- **Favorites column showing on new databases despite always being empty**
  (fixes #418) — favourite point counts can only be populated via the
  Geocaching.com Live API, which OpenSAK doesn't have yet, so the column was
  guaranteed to be blank. It's now off by default for newly created databases.

- **Adventure Lab stages with `LB*` codes were silently dropped on import**
  (fixes #359) — `lab2gpx` exports Adventure Lab stages under several prefixes
  (`LB`, `LA`, …), but the importer only recognized `GC` and `LC`. Any other
  prefix fell through to the "extra waypoint" path and vanished with no error.
  The importer now also accepts any code whose `<type>` field identifies it as
  a Geocache, regardless of prefix.

- **Newly imported caches showed no distance or bearing until restart**
  (fixes #359) — `recalculate_distances()` only ran at startup and on
  centre-point change, so freshly imported caches sat with `NULL` distance
  until one of those happened to fire. Import now triggers a recalculation
  too, when a home point is configured.

- **GC Code text could be unreadable in dark mode** (fixes #366) — text colour
  in the GC Code column was hardcoded to black; it's now computed per
  background pastel using WCAG relative luminance, so it stays readable in
  both themes. The archived-cache strikethrough line follows the same rule.

- **Unset flag column had no visual indicator** (fixes #290) — clicking the
  flag column to set a flag had no affordance beyond a tooltip; a faint
  outlined flag icon now shows when the flag is unset.

- **Locale-aware dates weren't zero-padded consistently** (fixes #369) — a few
  call sites formatted dates manually instead of going through the shared
  locale-date helper, producing inconsistent day/month padding depending on
  where the date was shown. All call sites now go through one function.

- **Enter key in the filter dialog opened "Save profile" instead of applying
  the filter** (fixes #370) — the Apply button looked bold but wasn't Qt's
  actual default button, so Enter triggered Save instead. Apply is now wired
  as the real default.

- **Text/icon size setting didn't take effect until you reselected a cache**
  (fixes #371) — refreshing the cache list cleared the table selection as a
  side effect, so the code path that re-applies the new size never ran until
  you clicked a cache again. The previously-selected cache is now restored
  after refresh, and the empty-state placeholder picks up the new size too.

- **Import progress bar was indeterminate** (fixes #372) — GPX imports now
  pre-scan the waypoint count so the progress bar can show real progress
  instead of just spinning.

- **Small/Large text size options looked almost identical to Medium**
  (fixes #374, #375) — the size range has been widened so the difference is
  actually visible, and the setting now also applies to the cache grid's font
  and row height, not just the detail panel.

### Notes

- The `v1.14.0-beta.15` tag should be treated as void. It was cut from a
  commit where `src/opensak/__init__.py` still read `1.14.0-beta.14` — a local
  version bump that was never actually committed — while `CHANGELOG.md` and
  `site/user-guide.html` already claimed beta.15. That mismatch is what made
  `test_user_guide_changelog_link_pins_to_release_tag` fail across every CI
  matrix leg. `__init__.py` has since been corrected on `beta`, the full
  pipeline is green, and this entry (beta.16) is the one that actually ships.
  No public GitHub Release was ever published for beta.15, so nothing
  user-facing needs correcting.
- 11 unused translation keys were removed (#397) after a new CI test started
  detecting language keys with no remaining source reference — this should
  keep the language files from quietly accumulating dead entries going forward.
- All 8 language files were updated for the new Waypoints, Notes, Attributes,
  Keyboard Shortcuts and Locked strings.
- `pyproject.toml`'s version field is now sourced dynamically from
  `src/opensak/__init__.py` instead of being maintained by hand in two places
  — this had silently drifted (`pyproject.toml` still said beta.6 while the
  app reported beta.14).

---

## [1.14.0-beta.14] — 2026-06-27

> **Beta release** — continuing the 1.14.0 testing period.

### Fixed

- **Website never actually redeployed on a beta release** — `deploy-site.yml`
  listened for `release: published`, but `build.yml` creates that release
  using `secrets.GITHUB_TOKEN` (via `softprops/action-gh-release`), and
  GitHub's anti-recursion rule means events produced *by* `GITHUB_TOKEN`
  never trigger other workflows in the same repo. So that trigger had
  silently never fired for a single beta release — the June 25 fix to pin
  the checkout ref corrected *what* would be deployed if it ran, but not
  *whether* it ran at all. This is why opensak.com was still showing
  beta.12 after beta.13 shipped. The workflow now also triggers directly
  on the release tag push (`push: tags: ["v*"]`), which is a normal,
  non-`GITHUB_TOKEN` push and fires like any other.

- **Two copies of the User Guide could silently drift apart** (root cause
  of the beta.12 Facebook report, and the reason beta.13's fix needed a
  second pass) — `docs/opensak-user-guide.html`, `docs/CNAME`, and
  `docs/assets/screenshots/` were leftover duplicates from when GitHub
  Pages deployed from the `/docs` folder on `main`, before the switch to
  Actions-based deployment from `site/`. Nothing referenced them — not
  code, not the build, not even README — so they only ever got updated by
  hand, and were forgotten as often as not. All three are now removed;
  `site/user-guide.html` is the single source of truth, and a regression
  test (`test_no_duplicate_user_guide_copy`) fails loudly if a synced
  `docs/` copy ever reappears.

### Notes

- No app-facing change for end users — this release exists purely to fix
  opensak.com and the release pipeline behind it.

---

## [1.14.0-beta.13] — 2026-06-27

> **Beta release** — continuing the 1.14.0 testing period.

### Fixed

- **No way to actually run the setup wizard again** (fixes #358) — Settings →
  Advanced told users to "run the setup wizard again" to change the
  installation folder, but there was no way to actually do that — the
  wizard only ever opened automatically on first launch. A "Run setup
  wizard again" button has been added right below that note. While
  building it, a related latent bug was also fixed: the wizard's
  database-folder step defaulted to the installation folder instead of the
  actual current database folder, which would otherwise have silently
  suggested moving an already-configured database folder back on a re-run.

- **User Guide "Changelog" link pointed to `main` instead of the release
  tag** — a beta tester reported via Facebook that the Changelog link on
  the opensak.com User Guide page opened the stable changelog instead of
  the beta one the page actually documents. The link (present in both
  `site/user-guide.html` and `docs/opensak-user-guide.html`) now pins to
  the exact release tag instead of a branch name — the same fix already
  applied to the in-app update popup in beta.10. A regression test now
  checks both files on every CI run so this can't silently resurface
  somewhere else.

### Notes

- All 8 language files updated with the new wizard button label and
  related strings.

---

## [1.14.0-beta.12] — 2026-06-25

> **Beta release** — continuing the 1.14.0 testing period.

### Added

- **Regression test locking the update-popup changelog link to the release
  tag** — the link was previously fixed (beta.10) to point at
  `blob/<tag>/CHANGELOG.md` instead of a hardcoded branch, but nothing
  enforced that in code. Testers on older betas (≤beta.9) were still seeing
  the stale hardcoded link until they upgraded, which understandably read
  as a fresh bug report on Facebook. A test now fails immediately if a
  future change reintroduces a literal `main` or `beta` branch name in
  that link, so this can't silently regress again.

### Notes

- No functional change for anyone already on beta.10 or later — the
  changelog and download links in the update popup were already correct.
  This release exists to get everyone still on an older beta onto a build
  where both links are guaranteed right.

---

## [1.14.0-beta.11] — 2026-06-24

> **Beta release** — continuing the 1.14.0 testing period.

### Fixed

- **Beta update check could offer an older beta instead of the latest one** —
  `fetch_latest_prerelease()` picked the first pre-release entry returned by
  GitHub's releases list, assuming it was always the newest. GitHub actually
  sorts that list by the commit date behind each tag, not by when the
  release was created, so the order doesn't always match version order
  (observed in practice: beta.9 listed ahead of beta.10). The function now
  compares every pre-release in the list and picks the genuinely highest
  version, regardless of the order GitHub returns them in.

---

## [1.14.0-beta.10] — 2026-06-24

> **Beta release** — continuing the 1.14.0 testing period.

### Fixed

- **"See changelog" link in the update popup always pointed to `main`** —
  beta releases live on the `beta` branch and aren't merged into `main`
  until they go stable, so anyone notified about a new beta and clicking
  "See changelog" saw an older changelog that didn't mention what had
  actually changed. The link now points at the specific release tag
  instead of always `main`, so it always shows the right entry.

---

## [1.14.0-beta.9] — 2026-06-24

> **Beta release** — continuing the 1.14.0 testing period.

### Added

- **User Guide link in the Help menu** — a new "User Guide" item, between
  "About OpenSAK…" and "Check for updates…", opens the online User Guide
  (opensak.com/user-guide.html) directly in your default browser. No more
  hunting for the link on GitHub or the website — help is one click away
  from inside the app. Translated into all 8 supported languages.

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
