# Changelog — OpenSAK
All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.19.0-beta.5] — 2026-09-12

### Added

- **Polish and Spanish UI languages** — OpenSAK now ships with `pl` and `es`
  translations, bringing the total to 10 supported languages. Aimed at
  reaching Poland and Spain, two of geocaching.com's largest user bases.
  Both translations are a machine-translated first pass; community
  review and corrections are welcome via the Facebook group or GitHub
  Discussions before promotion to stable.

---

## [1.19.0-beta.4] — 2026-09-11

### Added

- **Pocket Query e-mail retrieval (#443)** — OpenSAK can now check a
  configured mailbox for Pocket Query zip attachments and import them
  automatically, instead of requiring a manual download-and-import
  every time. Aimed in particular at Danish (and likely other
  countries') PQ-club/PQ-service setups, where a shared robot mails
  out a rotating set of PQs that together cover a whole country.
  - **Settings → PQ Email** — configure a plain IMAP mailbox (host,
    port, SSL, username, password). The password is stored securely
    in the OS keyring (Secret Service/KWallet on Linux, Credential
    Manager on Windows, Keychain on macOS) — never in plaintext
    config. A "Test connection" button verifies login separately from
    saving, with distinct messages for a login failure versus a
    network/server problem. Gmail and Outlook.com/Live.com are not
    supported yet — both require OAuth2, tracked separately as #697
    and #698.
  - **File → "Check for PQ Email…"** — a manual, on-demand check of
    the configured mailbox. Any e-mail with a `.zip` attachment is
    treated as a candidate (no assumption about sender or subject —
    Geocaching.com's own PQ-ready notification hasn't attached the
    zip directly since around 2014, so this looks for the attachment
    itself instead). Each zip is matched to a same-named database
    where one exists, falling back to the currently active database
    otherwise, and handed to the existing GPX/PQ-zip importer
    unchanged. An opt-in checkbox deletes the e-mail after a
    successful import; a failed import always leaves the e-mail in
    place so it can be retried.
  - Scheduled/background checking (repeating this automatically
    without opening the dialog) is tracked separately as #445 and
    intentionally not part of this first release — real-world
    feedback on the manual flow first.

---

## [1.19.0-beta.3] — 2026-09-10

### Added

- **Linux AppImage: self-administration, no terminal required (#824, #835,
  #836, #837)** — Replaces the originally planned external `uninstall.sh`
  + AppImageUpdate approach with self-integration/-update/-uninstall
  implemented directly in OpenSAK. Linux-only; a no-op everywhere else
  (Windows/macOS/source installs are unaffected, and the AppImage's own
  `scripts/install-opensak.sh` fallback path is unchanged).
  - **Self-integration on first run (#835)** — On first launch as an
    AppImage, OpenSAK offers to install itself into the application menu
    (Yes / No thanks / Don't ask again). On "Yes", the running
    `$APPIMAGE` file is copied to `~/.local/bin/OpenSAK.AppImage`, a
    `.desktop` entry and icon are installed into the standard XDG
    locations, and the chosen path is recorded so later self-update/
    -uninstall never has to guess it. If AppImageLauncher has already
    integrated the app (detected via an existing `X-AppImage-Identifier`
    `.desktop` entry), the prompt is skipped silently instead of
    double-integrating. A manual "Install in application menu" button was
    also added under Settings → Advanced → AppImage, so choosing "Don't
    ask again" is never an irreversible dead end.
  - **Self-update (#836)** — AppImage-integrated users now see an
    "Upgrade now" button (instead of "Open releases page") in the
    existing update-available dialog. Clicking it downloads the matching
    `OpenSAK-<tag>-Linux-x86_64.AppImage` release asset to a temp file
    next to the installed copy, validates its ELF magic bytes before
    accepting it, then atomically replaces the running installation
    (`os.replace`) — safe even while the old version is still running.
    No zsync/AppImageUpdate dependency; a full download every time, kept
    deliberately simple. Shows an indeterminate "Downloading…" indicator
    (no progress bar for v1) and, on success, asks the user to close and
    relaunch rather than attempting a fragile in-process restart.
  - **In-app uninstall (#837)** — A new "Uninstall OpenSAK" button
    (Settings → Advanced → AppImage) removes the `.desktop` file, icons,
    and the integrated AppImage copy, with a choice between "Remove
    program only" (keeps databases/settings) and "Remove program and all
    data" (the latter requires a second, explicit confirmation). Data
    removal uses the exact `settings_store.get_install_dir()`/
    `get_db_dir()` paths OpenSAK itself tracks — no guessing — with an
    explicit safety guard against ever deleting the user's home
    directory outright. Integration flags are always reset regardless of
    which option is chosen, so a later reinstalled AppImage is correctly
    re-offered integration instead of silently staying stuck.

---

## [1.18.1] — 2026-09-09

> Stable bugfix release on the 1.18.0 line. Replaces the `1.18.1-beta.1`
> build — see git history for the detailed commit-by-commit log if
> needed. Verified via a Partner Center package flight (real Microsoft
> Store install, not just sideload) before promotion to this general
> release. Deliberately skips the in-progress Garmin MTP GPS export
> feature (#453/#822), which remains on the `1.19.0` line — this release
> is bugfixes only.

### Fixed

- **macOS: default data path used Linux-style paths instead of
  `~/Library/Application Support` (#825)**.
- **MSIX/Microsoft Store builds could report the wrong database path,
  traced to confusion around Windows AppData file-system virtualization
  (#820)** — the Welcome wizard now defaults new MSIX installs to
  `Documents\opensak` instead of `AppData\Roaming\opensak` (confirmed on
  a real, certified Store install, both for the install folder and the
  database folder wizard steps), and the Database Manager's "physical
  path" note no longer shows a guessed path when it doesn't actually
  exist on disk. Real hardware testing found no evidence of AppData
  virtualization under either a self-signed sideload or a genuine
  Microsoft Store signing identity — the original virtualization
  root-cause theory remains unconfirmed, and the issue stays open pending
  more detail from the original reporter.
- **Changing the database folder could silently copy a corrupt or empty
  source database file, surfacing later as an unexplained
  `sqlite3.DatabaseError` on next launch instead of a clear message
  (#828)** — `move_databases_to()` now checks a source file's SQLite
  header before copying it; an invalid source is reported as a specific
  error and left untouched rather than copied forward. Confirmed on both
  a local dev build and a real, certified Microsoft Store install.
- **CI: a set of macOS/Linux-only path tests failed when run on the
  Windows CI runner** — a `pathlib` platform-dispatch limitation; these
  are now explicitly skipped on Windows.

### Notes

- **Test-suite isolation gap (#829)**: `pytest` runs could leak into the
  real `%APPDATA%`/`~/.config`/`~/Library/Application Support`
  installation on the machine running them. This was the root cause of
  the corrupted database file behind #828 above, reproduced live during
  #820 testing. `tests/conftest.py` now isolates `settings_store`,
  `logger`, and `db.manager`'s process-global singletons behind a
  `tmp_path`-backed fixture for every test.

---

## [1.18.1-beta.1] — 2026-09-09

> Targeted bugfix backport onto the 1.18.0 stable line, cherry-picked from
> the active 1.19.0 beta cycle onto `v1.18.0` — deliberately skips the
> in-progress Garmin MTP GPS export feature (#453/#822) so these fixes can
> reach Store/stable users faster as a pure bugfix set, without waiting on
> an unrelated feature to finish testing. Being verified via a Partner
> Center package flight before promotion to stable 1.18.1.

### Fixed

- **macOS: default data path used Linux-style paths instead of
  `~/Library/Application Support` (#825)**.
- **MSIX/Microsoft Store builds could report the wrong database path,
  traced to confusion around Windows AppData file-system virtualization
  (#820)** — the Welcome wizard now defaults new MSIX installs to
  `Documents\opensak` instead of `AppData\Roaming\opensak`, and the
  Database Manager's "physical path" note no longer shows a guessed path
  when it doesn't actually exist on disk. Real hardware testing (both a
  self-signed sideload and a genuine Microsoft Store install) found no
  evidence of AppData virtualization under either signing identity — the
  original virtualization root-cause theory remains unconfirmed, and the
  issue stays open pending more detail from the original reporter.
- **Changing the database folder could silently copy a corrupt or empty
  source database file, surfacing later as an unexplained
  `sqlite3.DatabaseError` on next launch instead of a clear message
  (#828)** — `move_databases_to()` now checks a source file's SQLite
  header before copying it; an invalid source is reported as a specific
  error and left untouched rather than copied forward. The exact failure
  mode was reproduced in the wild while testing #820: a corrupted
  database created by a test-suite isolation gap (see below) triggered
  precisely this crash the first time a real user hit it.
- **CI: a set of macOS/Linux-only path tests failed when run on the
  Windows CI runner** — a `pathlib` platform-dispatch limitation; these
  are now explicitly skipped on Windows.

### Notes

- **Test-suite isolation gap (#829)**: `pytest` runs could leak into the
  real `%APPDATA%`/`~/.config`/`~/Library/Application Support`
  installation on the machine running them, because `settings_store`,
  `logger`, and `db.manager`'s process-global singletons were only
  isolated per test-file rather than globally. This was the root cause of
  the corrupted database file behind #828 above. `tests/conftest.py` now
  isolates all three singletons behind a `tmp_path`-backed fixture for
  every test, layered underneath the existing per-file fixtures.

---

## [1.19.0-beta.2] — 2026-09-07

### Added

- **MTP support for newer Garmin devices, Windows (#453, #826)** —
  Extends the MTP fix from #822 to Windows. Windows doesn't assign
  MTP-connected devices a drive letter, so the existing mount-point
  scan couldn't see them either. A new `opensak.gps.mtp` module talks
  to the device through the same Shell "Folder" automation API File
  Explorer itself uses (via `pywin32`, a new Windows-only dependency),
  wrapped in a small `MTPDevice`/`MTPPath` adapter that mirrors
  `pathlib.Path`'s interface (`/`, `write_text`, `mkdir`, `unlink`,
  etc.). Because of that, the existing GPX/GGZ export code needed no
  changes at all — it already worked in terms of `Path`-like objects,
  so the new adapter just slots into the same code path a normal mount
  point uses. File copies go through `Folder.CopyHere`, deletions
  through `InvokeVerb("delete")`, both polled to confirm completion
  since neither is a synchronous, confirmable operation on Windows.
  Detection deliberately skips anything already exposed as a regular
  drive letter, to avoid double-listing the same device. With this,
  **#453 is now resolved on both Linux and Windows** — macOS is the
  one remaining unconfirmed platform (see below). Thanks again to
  Brian Anderson (@blazerat)!

### Fixed

- **Garmin folder lookup could resolve to a different casing on every
  run, on case-insensitive filesystems (macOS, Windows)** — a
  follow-up commit to #826 introduced `_get_garmin_folder()`, which
  probed `garmin`/`GARMIN`/`Garmin` as constructed candidate paths and
  tested each with `.is_dir()`. On a case-sensitive filesystem (Linux)
  only the real one matches, but on macOS' APFS and Windows' NTFS all
  three resolve to the same physical folder once it exists — so which
  one "matched" depended on the iteration order of the Python `set`
  they were stored in, itself randomised per-process by string hash
  seeding. `TestExportGgzToDevice::test_file_path_recorded` caught
  this via a genuinely flaky assertion on the macOS CI runner. Fixed
  by scanning the real directory listing once and matching
  case-insensitively against the actual on-disk name, instead of
  guessing candidate paths. Caught and fixed before this reached a
  tagged release — no user-facing impact.

---

## [1.19.0-beta.1] — 2026-09-07

### Added

- **MTP support for newer Garmin devices, Linux (#453, #822)** — Newer
  Garmin models (2020+) dropped USB mass-storage in favour of MTP
  (Media Transfer Protocol), which the mount-point-based device
  detection couldn't see, so "Send to GPS" silently found no device on
  Linux for these units. Device detection now also scans GVFS/MTP
  mounts (`/run/user/*/gvfs/mtp:...`) for a Garmin folder (handling the
  uppercase `GARMIN` naming and extra storage-root nesting MTP devices
  use), and both GPX and GGZ export/delete now go through `gio copy`/
  `gio remove` when the target is an MTP device instead of a direct
  filesystem write. An in-progress MTP transfer is cancelled cleanly if
  the export dialog is closed mid-copy. Requires `gio` (part of GLib/
  GVFS), present by default on most GNOME-based Linux desktops; falls
  back to a clear error message if it's missing. This fix is Linux-only
  — Windows and macOS were not affected by the original mount-point
  detection gap in the same way and are unchanged here. Thanks to Brian
  Anderson (@blazerat) for both the investigation and the fix!

---

## [1.18.0] — 2026-09-02

> First stable release of the 1.18.0 cycle, and the **first stable release
> distributed via the Microsoft Store** (product `9P4NBMM84H2D`) alongside
> the existing Windows `.exe`/ZIP, Linux AppImage, and macOS DMG builds.
> Replaces the `1.18.0-beta.1` … `1.18.0-beta.3` builds — see git history
> for the detailed beta-by-beta log if needed. Headline of this cycle: MSIX
> packaging for the Microsoft Store, plus a round of dialog high-DPI
> scaling fixes surfaced by Store certification.

### Added

- **MSIX / Microsoft Store packaging (#786)** — OpenSAK can now be
  packaged as an MSIX for Microsoft Store distribution (product
  `9P4NBMM84H2D`). Nothing about the existing Windows `.exe`/ZIP build
  changed — this is a side channel wrapping the same PyInstaller output.
- **MSIX CI build job** — a manual-trigger-only GitHub Actions workflow
  (`build-msix.yml`) builds and packages the `.msix` on `workflow_dispatch`.
- `scripts/derive_msix_version.py` — converts OpenSAK's version string
  (including any `-beta.N` suffix) into the strict 4-part numeric version
  MSIX/the Store requires.

### Fixed

- **Settings and 7 other dialogs could exceed screen height at high DPI
  scaling (#811, #815)** — caught by Microsoft Store certification
  (10.1.2.10 Functionality) on a 2560x1600 display at 200% scaling. Added
  a shared `clamp_dialog_height_to_screen()` helper (`widgets.py`) and
  applied it, with `QScrollArea` wrapping where needed, to: Settings, Trip
  planner, Column chooser, Welcome wizard, Import, GPS export (#811), and
  Waypoint/Custom WP, Update Location, and Database Manager (#815). A new
  `test_dialog_height_policy.py` tracks the remaining 14 lower-risk
  dialogs as explicit follow-up work (#816) rather than a silent gap.
- **"Close" button in the Download Boundary Packs dialog showed the raw
  translation key `btn_close` instead of "Close" (#804)**.
- **Advanced settings tab controls squashed on small/high-DPI screens
  (#805)** — the Advanced tab now scrolls like the General tab instead of
  squashing its controls together.
- **Hint/note text too low-contrast to read, especially in dark mode
  (#806)** — replaced a hardcoded low-contrast grey (duplicated across 42
  call sites) with a new theme-aware `hint_text_color()`/`hint_style()`
  helper.
- **GSAK `.db3` import: a genuine 0.0m/sea-level elevation displayed as
  blank (#794)** — the importer now reads GSAK's `Resolution` column to
  distinguish "no elevation assigned" from a real sea-level reading.
- **GSAK `.db3` import: plain-text descriptions containing a literal `<`
  were misdetected as HTML and lost all line breaks (#803)** — the
  importer now reads GSAK's own `ShortHtm`/`LongHtm` columns instead of
  guessing from a raw `<` check; plain-text descriptions are also now
  HTML-escaped before display.
- **Import dialog's Close button froze the UI mid-import or mid-geocode,
  and could crash after either finished (#802)**.

---

## [1.18.0-beta.3] — 2026-08-31

### Fixed

- **Settings and 5 other dialogs could exceed screen height at high DPI
  scaling (#811)** — caught by Microsoft Store certification
  (10.1.2.10 Functionality) on a 2560x1600 display at 200% scaling: the
  Settings dialog's "Geocaching profile" group was rendered off-screen
  with no way to reach it. Root cause: dialogs had no maximum-height
  constraint, so Qt grew them to fit all content unscrolled — DPI
  scaling shrinks the effective usable screen area, so on a small
  enough one that can exceed the available height. Added a shared
  `clamp_dialog_height_to_screen()` helper (`widgets.py`) and applied
  it, with `QScrollArea` wrapping where needed, to the 5 highest-content
  dialogs: Settings, Trip planner, Column chooser, Welcome wizard,
  Import, and GPS export. A new `test_dialog_height_policy.py` tracks
  the remaining 17 dialogs as explicit follow-up work rather than a
  silent gap — a new dialog (or a stripped protection call) without it
  now fails CI.

---

## [1.18.0-beta.2] — 2026-08-30

### Fixed

- **"Close" button in the Download Boundary Packs dialog showed the raw
  translation key `btn_close` instead of "Close" (#804)** — reported by
  Giles Percival on Facebook after a boundary-pack download completed.
  `BoundaryDownloadDialog._on_done()` called `tr("btn_close")`, a key that
  didn't exist in any of the 8 language files — `tr()` falls back to the
  raw key when a translation is missing. Fixed to reuse the existing
  `"close"` key (already used elsewhere, e.g. `file_export_dialog.py`); no
  language file changes needed.

- **Advanced settings tab controls squashed on small/high-DPI screens
  (#805)** — reported by Sean Flook on Facebook (v1.18.0-beta.1). The
  General tab wraps its content in a `QScrollArea` to stay usable on small
  screens/high DPI, but the Advanced tab — which has the most stacked
  group boxes of any tab (Folders, Search, Location refinement, Updates,
  Distance) — didn't, so Qt squashed the controls together instead of
  letting the user scroll when the dialog couldn't grow to fit everything.
  `_build_advanced_tab()` now wraps its content in a `QScrollArea` the
  same way `_build_general_tab()` does.

- **Hint/note text too low-contrast to read, especially in dark mode
  (#806)** — reported by Sean Flook (dark mode) and Robert Long (also hard
  to read in light mode for older eyes) on Facebook. A hardcoded
  `"color: gray; font-size: 10px;"` was duplicated across 42 call sites in
  16 files for secondary/hint text — a fixed, theme-independent colour
  with insufficient contrast in both themes. Replaced with a new
  theme-aware `hint_text_color()`/`hint_style()` helper in
  `src/opensak/gui/theme.py`: dark green (`#2e7d32`, ~4.7:1 contrast) in
  light mode, light green (`#66bb6a`, ~6:1 contrast) in dark mode.
  Centralised in one place so a future user-configurable hint colour only
  needs to change this one function. (One follow-up fix included: the map
  popup's cache-type/D/T line is built as a raw JavaScript template
  embedded in Python and resolved via string-token substitution, not an
  f-string — the initial colour-substitution accidentally called the
  Python helper *inside* the JS source itself, throwing `hint_text_color
  is not defined` in the browser console at runtime. Fixed by resolving a
  `HINT_TEXT_COLOR` template token in Python before the HTML reaches
  QtWebEngine, matching the existing `INIT_LAT`/`MAP_TIP_MAXIMIZE`-style
  tokens.)

- **GSAK `.db3` import: a genuine 0.0m/sea-level elevation displayed as
  blank (#794)** — reported by ianwok, root-caused with GSAK maintainer
  Mike Wood (lignumaqua). GSAK's `Elevation` column is always numeric
  (0.0 both before an elevation macro has run *and* for a real sea-level
  cache), so the two cases were indistinguishable from the value alone —
  every 0.0 elevation was nulled on import, including real ones. GSAK
  actually signals "no elevation assigned" via a blank (not `NULL`)
  `Resolution` column instead — metres of source resolution, or the
  literal text `"USER"` for a manually entered value, left blank only
  when unset. The importer now reads `Resolution` to make that
  distinction; when the column is absent entirely (older/partial GSAK
  exports), it trusts the raw `Elevation` value rather than guessing.

- **GSAK `.db3` import: plain-text descriptions containing a literal `<`
  were misdetected as HTML and lost all line breaks (#803)** — reported
  by nagisml, e.g. GC5K1DY. The importer guessed `short_desc_html`/
  `long_desc_html` by testing for any `<` character in the description
  text, instead of reading GSAK's own authoritative `ShortHtm`/`LongHtm`
  columns (which mirror geocaching.com's real `html=true/false` flag, the
  same source the GPX importer already reads). Now reads those columns
  directly; falls back to a real-tag-matching regex (not the old naive
  `<` check) only when they're entirely absent. Also fixed a related
  issue nagisml flagged in the same report: plain-text descriptions were
  written into the description view's `<pre>` block unescaped, so a
  stray `<`, `>` or `&` in genuinely plain text (e.g. "N < 5 caches",
  "R&D") could still be misinterpreted as markup — `cache_detail.py` now
  HTML-escapes plain-text descriptions before wrapping them.

- **Import dialog's Close button froze the UI mid-import or mid-geocode,
  and could crash after either finished (#802)** — found by Allan while
  reviewing the #800/#801 fix. `closeEvent()` blocked the GUI thread with
  a synchronous `worker.wait()`, so clicking Close while an import or the
  follow-up "Looking up missing location data" step was running froze the
  window until the background thread stopped — indistinguishable from a
  hang or crash on a large import. The import step has no cancellation
  support, so Close is now simply disabled while it runs; the geocode
  step *can* cancel responsively (`ReverseGeocodeWorker` checks a flag
  between rows), so Close relabels to "Cancel" there and routes to the
  non-blocking `request_cancel()` instead of closing. Also added
  `parent=self` to the `ReverseGeocodeWorker` construction, matching
  #801's fix in `update_location_dialog.py`. A related crash surfaced
  during manual testing: `self._worker`/`self._geo_worker` were only ever
  cleared in `closeEvent()`, never on normal completion, so a finished
  worker's already-`deleteLater()`'d C++ object could still be referenced
  by a later Close click ("RuntimeError: libshiboken: Internal C++ object
  already deleted"). Fixed by clearing both references on `QThread.finished`
  (mirroring `update_location_dialog.py`'s existing pattern), with a
  defensive `try`/`except RuntimeError` as a second safety net.

---

## [1.18.0-beta.1] — 2026-08-27

### Added

- **MSIX / Microsoft Store packaging prototype (#786)** — OpenSAK can now
  be packaged as an MSIX for a private, unlisted Microsoft Store listing
  (product `9P4NBMM84H2D`). A submission passed Store certification and
  was confirmed installable end-to-end via the Store app itself, clearing
  the risk that the bundled QtWebEngine/Chromium runtime would be
  rejected. Nothing about the existing Windows `.exe`/ZIP build changed —
  this is a side channel wrapping the same PyInstaller output.
- **MSIX CI build job** — a new manual-trigger-only GitHub Actions
  workflow (`build-msix.yml`) builds and packages the `.msix` on
  `workflow_dispatch`, so producing a new build for Partner Center no
  longer requires the Windows machine for that step. Not tag-triggered
  and not attached to public GitHub Releases yet, since the Store listing
  is still private/unlisted.
- `scripts/derive_msix_version.py` — converts OpenSAK's version string
  (including the `-beta.N` suffix) into the strict 4-part numeric version
  MSIX requires.

---

## [1.17.2] — 2026-08-26

### Fixed

- **Detail panel header showed large empty grey padding when the bottom
  panel was resized taller (#779)** — regression from the #755 fix shipped
  in v1.17.1. Reported by Mike via email with side-by-side screenshots.
  #755 gave the cache-detail `QTabWidget` a vertical `QSizePolicy` of
  `Ignored` so Qt would disregard the tab pages' minimum-size contribution
  when computing the panel's minimum height. `Ignored`, unlike the tab
  widget's default `Expanding`, doesn't carry `QSizePolicy.ExpandFlag` —
  so the tabs stopped being the preferred recipient of any leftover
  vertical space in the panel's `QVBoxLayout`, and Qt spread that space
  across every row instead, including the compact header/meta rows.
  Fixed by replacing the policy change with a small `QTabWidget` subclass
  in `src/opensak/gui/cache_detail.py` that overrides `minimumSizeHint()`
  to return `QSize(0, 0)` — keeping the #755 fix intact while the tabs
  keep their normal `Expanding` policy and vertical stretch priority, as
  in v1.17.0.

- **GSAK Mark As Found status not detected on GPX/GGZ import when GSAK
  encodes it via `<type>...|Found</type>` instead of `<sym>` or a personal
  log entry (#766)**

- **Found status from genuine GSAK GPX/GGZ exports not detected on import
  when GSAK's `<sym>` doesn't reflect found state (#766)**

### Known Issues

- **Boundary updates don't always recognize when they're already current
  (#781)** — the reverse-geocoding boundary refresh can reprocess polygon
  data that's already up to date. Under investigation.

- **A small number of caches reverse-geocode to the wrong region (#782)**
  — e.g. GCAJWVJ is assigned country/county values that don't match its
  position on the map. Not yet clear whether the cause is in the polygon
  data, the boundary-matching logic, or the map visualization. Under
  investigation.

---

## [1.17.1] — 2026-08-19

### Fixed

- **GPX export/import did not preserve "found" status (#766)** — reported by
  Allyn56 via a detailed multi-scenario comparison in #649. Two related bugs
  in the GPX bridge, both now fixed:
  1. **Export:** `_cache_symbol()` in `src/opensak/gps/garmin.py` always wrote
     `<sym>Geocache</sym>`, regardless of `cache.found`. The Groundspeak/GC.com
     Pocket Query convention — which GSAK and Garmin devices actually read
     found status from in a GPX — is `<sym>Geocache Found</sym>`. OpenSAK
     never wrote this, so found status was invisible to any tool reading an
     OpenSAK-exported GPX or GGZ, including OpenSAK itself on re-import (the
     log entries were present and correct, but nothing checks the log list
     for found status — only `<sym>`). Found caches now export with
     `<sym>Geocache Found</sym>` in both the file-export and send-to-device
     paths (GGZ reuses the same GPX generator). Lab Caches are unaffected —
     they have no found-symbol in the Garmin convention, so their distinct
     icon (`Flag, Blue`) is unchanged either way.
  2. **Import:** `found_by_me` in `src/opensak/importer/__init__.py` was
     derived exclusively from `sym == "Geocache Found"`, with no fallback —
     unlike the GSAK database importer, which reads GSAK's own `Found` column
     directly and was unaffected. Import now also checks the log list for a
     found-type log (`FOUND_LOG_TYPES`) matching the configured
     `gc_finder_id`/`gc_username`, same matching rules already used for
     `found_date`/FTF derivation, but **only when `<sym>` is entirely absent**
     from the source wpt — if `<sym>` is present at all (even a plain
     "Geocache", not just "Geocache Found"), it's trusted as authoritative.
     This matters because "Mark as Not Found" deliberately leaves old
     found-type Log rows in place rather than risk deleting real imported
     log history — without this guard, re-exporting such a cache (which
     correctly writes `sym="Geocache"` after the fix above) and re-importing
     it would have incorrectly resurrected found status from the stale log.

- **Vertical splitter couldn't be resized past roughly the middle of the
  window (#755)** — the cache detail panel's `QTabWidget` (Description,
  Hint, Logs, Waypoints, Attributes, Trackables, Notes) reported its own
  minimum size as the largest of all its tab pages' minimums, since any
  tab could be switched to at any time. That propagated up into the whole
  bottom panel's minimum height, blocking the main splitter from being
  dragged much further down — the panel could shrink, but never much past
  its content's natural minimum. A `QSizePolicy.Ignored` on the tab
  widget's vertical component stops that minimum-size propagation without
  affecting normal sizing or tab switching — each tab's own content
  already scrolls internally when squeezed, so nothing becomes
  unreachable. Verified the splitter can now shrink the bottom panel well
  under its old floor, even with a heavily populated cache (many
  attributes/logs/waypoints, long description).

- **Coordinate Converter gave wrong results (#751)** — two independent bugs
  in `src/opensak/coords.py`, both reported/diagnosed by pjacklam:
  1. `parse_coords()` had no dedicated branch for a plain decimal-degree
     value written with a hemisphere letter instead of a +/- sign (e.g.
     "N 59.99999 E 12.99999", no separate minutes component). It fell
     through to the DMM° regex branch instead, where backtracking let the
     degrees group give back digits to the minutes group whenever keeping
     them would leave an unparseable "." right after — silently
     reinterpreting "59.99999" as degrees=5, minutes=9.99999 (5.16667°
     instead of 59.99999°), with no error shown. A dedicated branch for
     this input shape, checked before the DMM° branch, fixes it.
  2. `_dd_to_dmm()`/`_dd_to_dms()` (and the cache list's `format_lat()`/
     `format_lon()`, which had the same bug independently) rounded minutes/
     seconds directly in an f-string without checking whether the rounded
     value hit 60 — e.g. 59.999999° displayed as "59° 60.000'" or "59° 59'
     60.00\"" instead of carrying into the next unit ("60° 00.000'"/"60°
     00' 00.00\""). Both formatters now share carry-aware helpers that
     roll 60 minutes into a degree and 60 seconds into a minute (which can
     itself cascade into a degree). Verified against both of the reporter's
     exact examples end-to-end.

### Added

- **"Mark as Found" now opens a dialog and creates a real log entry (#649)**
  — right-clicking a cache and choosing "Mark as Found" used to silently
  set `cache.found = True` with no found date and no Log row. Since GPX
  export only ever serializes existing `cache.logs`, a manually-found
  cache exported with no `groundspeak:finder`/"Found it" entry at all —
  GSAK (or any re-import) then didn't recognize it as found either, even
  though OpenSAK's own list showed it as found. This is a real workflow
  gap for Adventure Lab Caches specifically, where geocaching.com
  auto-logs the parent AD Lab as found with no real per-stage log to
  import, so manual marking is the only way in. "Mark as Found" now opens
  a dialog to pick the found date (default: today), and creates a matching
  Log row (type "Found it", or "Attended" for event-type caches, finder =
  your configured Geocaching.com username, date = the chosen date) —
  verified end-to-end that a manually-found cache now round-trips
  correctly through GPX export. Requires a Geocaching.com username
  configured under Settings first (used as the log's finder name); you'll
  be prompted if it isn't set yet. A new "Edit found date…" entry lets you
  change the date afterwards without unmarking first — it updates your
  existing found-type log in place rather than adding a duplicate.
  "Mark as Not Found" now also clears the found date (mirroring GSAK's own
  found/found-date linkage), so a cache marked not found no longer shows a
  stale date; it still doesn't touch existing log history.

### Fixed

- **Child waypoints missing from GPX/GGZ export and Send-to-GPS (#753)** —
  `generate_gpx()` only ever emitted the one `<wpt>` for a cache's own
  listing; `cache.waypoints` (parking areas, trailheads, stages, final
  locations, etc.) were silently dropped from every export path that uses
  it — File Export (GPX and GGZ) and Send-to-GPS alike. Each child waypoint
  now gets its own sibling `<wpt>` element, as a plain GPX waypoint (no
  `groundspeak:cache` block — same convention as custom-waypoint-type
  caches, #660) with its coordinates, comment, description, sym/type from
  its waypoint type, and a reconstructed GC-style `<name>` (prefix + this
  cache's own code suffix — the exact original geocaching.com-assigned
  code isn't retained on import, but a short unique code is all a
  re-import or device needs). Verified end-to-end against pjacklam's
  original GC1YB0C.gpx / opensak_GC1YB0C.gpx pair — the reconstructed
  child waypoint now matches the original byte-for-byte on every field
  except `<url>` (not retained by GPX import, so intentionally omitted
  rather than exported empty). Thanks to pjacklam for the report and the
  side-by-side GPX files, which made the diff straightforward.

- **Split-screen map: caches with corrected coordinates rendered outside
  the drawn circle, and stayed unfiltered when a cache was selected
  (#748, #743)** — the split-screen "nearby" map computed radius
  membership, sort order, and its own circle centre from each cache's
  *raw* latitude/longitude, but plots markers at *corrected* coordinates
  when set. A cache whose corrected coordinates diverged from its raw
  ones could therefore pass the radius check yet render outside the
  visible circle (or the reverse — genuinely nearby via its correction,
  but excluded). Separately, `get_nearby_caches()` only ever filtered by
  distance, ignoring whatever filter was currently active on the main
  list — selecting a cache while filtered silently reverted the
  split-screen map to showing every nearby cache. Both are fixed: radius
  membership/sorting/circle-centre now use each cache's effective
  (corrected-aware) coordinates, and the currently active filter (advanced
  + quick/search-box) is passed through and applied alongside the distance
  check. Thanks to GeePa67 for both reports.

- **Info Bar counts wrong when text-searching User Notes (#752)** —
  `apply_filters_lightweight()`'s fallback check for when it must defer to
  the full ORM query path never accounted for `TextSearchFilter.search_notes`
  (only description/logs/hint were checked). A text search scoped to *only*
  User Notes therefore took the fast lightweight path, where the notes
  `exists()` subquery — written for, and only ever exercised against, the
  full ORM query — raised a SQLAlchemy auto-correlation error instead of
  returning a result. Notes-only text search filters now correctly fall
  back to the full ORM path, matching how description/logs/hint scoping
  already behaved. Thanks to pjacklam for the report.

- **Community Celebration Event caches imported with wrong type (#756)** —
  geocaching.com's GPX export uses the raw type string
  `"Lost and Found Event Caches"` for CCE caches, not `"Event Cache"` as
  previously assumed (#591). Unrecognized, this string fell through to the
  generic gray "unknown type" icon on GPX import, and to the Mystery icon
  on GSAK import (GSAK code `F`, previously left deliberately unmapped).
  Both import paths now correctly classify these as Community Celebration
  Event. Verified end-to-end against a real-world 88-cache CCE export.
  Thanks to Veé X Péé for the detailed report and test file.

---

## [1.17.0] — 2026-08-17

> First stable release of the 1.17.0 cycle. Replaces the run of
> `1.17.0-beta.1` … `1.17.0-beta.14` builds — see git history for the
> detailed beta-by-beta log if needed. Headline of this cycle: full-screen
> and pop-out map support, offline reverse-geocoding enabled by default,
> and a fix for the long-standing GUI freeze on large-database switch/load.
>
> Summarises net changes since v1.16.3 (the beta.1/4/5 releases that folded
> the v1.16.1–v1.16.3 hotfixes back into `beta` are not repeated here, since
> those fixes already shipped as stable patch releases).

### Added

- **Full-screen / pop-out map (#696, #598)** — maximize the map within the
  main window (`F11`), or pop it out to its own floating window that can be
  moved to a second monitor (`Ctrl+Shift+M`). Thanks @blazerat!
- **14 new GSAK-compatible columns (#658)** — Cache Id, Changed date,
  Creation date, Elevation, Found count, GC.com note, Guid, Hints, Notes,
  Owner ID, Owner name, Source, Url, Watch.
- **Last found / Last GPX update / Last four logs columns (#716)** —
  closing out long-standing GSAK-parity requests #518, #542, #534. Thanks
  Allyn56 and ianwok.
- **Wait cursor during cache-list refresh (#647)** — including at startup
  with a large database, so a multi-second refresh is no longer
  indistinguishable from the app doing nothing.
- **Previous session's log preserved across restarts (#737)** — a new Help
  menu action, "Open log from last restart", makes it possible to inspect a
  crash or hang from the previous session.

### Changed

- **Offline reverse-geocoding (Country/State/County) enabled by default
  (#60)** — no longer requires manual opt-in.

### Fixed

- **GUI freeze / force-close on large-database switch, filter, or load
  (#740)** — the cache list and map now refresh on a background thread
  instead of blocking the GUI thread for up to 85+ seconds on very large
  databases.
- **App could hang or silently crash on startup for large or existing
  databases (#723)** — root cause was a missing index on `logs.cache_id`
  causing full table scans during startup migrations, plus a separate
  migration bug that could crash startup entirely on databases that had
  already run a later schema migration.
- **PQ import appeared to hang on Windows when county boundary packs
  weren't cached locally (#722)** — missing packs are now pre-fetched in
  parallel with progress feedback, instead of retried on demand for every
  affected cache.
- **Migration and database-switch failures could fail silently (#738)** —
  these now log the full error and show a clear dialog instead of leaving
  the app in a half-switched or broken state.
- **Reverse-geocode bulk write could crash on large databases (#710)** —
  fixed a "too many SQL variables" error by chunking the write.
- **Archived caches hidden by default in the Set Filter dialog, and the
  setting wasn't remembered (#576)** — now defaults to shown, matching
  GSAK, and the choice persists correctly.
- **Map didn't reliably pan to the selected cache, and the split-screen map
  didn't update for caches outside the overview map's display range
  (#718)**.
- **Toolbar title was hardcoded in Danish regardless of UI language
  (#683)** — thanks @urs-beeli.

---

## [1.17.0-beta.14] — 2026-08-16

### Fixed

- **GUI thread blocked 20-90s on large-database switch/filter/load, appearing
  as a freeze or force-close (#740)** — reported by Ron Radix (Facebook
  community) on a 232,149-cache database: switching to another database
  could make OpenSAK close itself, with the previous database reopening
  afterward. A submitted `opensak.log` ruled out both an uncaught exception
  and a migration (schema was already current throughout) — the log simply
  stopped, mid-flow, right after a successful database switch, with no
  traceback. Root cause: `apply_filters_auto()` → `cache_table.load_caches()`
  → `map_widget.load_caches()` ran synchronously on the GUI thread and
  consistently took 16-28s on 39k-232k cache databases, reaching 85+ seconds
  of continuous blocking when combined with a distance recalculation — long
  enough that Windows' "Not Responding" detection (or the user via Task
  Manager) would terminate the process. Confirmed via measured timings
  across three real databases (98k/198k/348k caches). The DB query now runs
  on a new background `RefreshWorker` (`QThread`); the GUI thread applies
  the result once ready. A generation counter discards results from a
  superseded refresh (e.g. two quick database switches) without needing to
  cancel the older worker. Verified against the same three databases:
  switching between them no longer freezes or force-closes, at the cost of
  a measured increase in total completion time on first (cold) access —
  tracked separately as a performance follow-up in #746, since the query's
  cost is largely GIL-bound ORM row hydration rather than I/O wait, so
  backgrounding it doesn't make it faster on its own.

### Added

- **Wait cursor during cache-list refresh (#647)** — a multi-second refresh
  on a large database (see #740 above) previously gave no visual feedback
  at all, indistinguishable from the app doing nothing. A wait cursor is now
  shown for the duration of any refresh, including at startup with a large
  database.

---

## [1.17.0-beta.13] — 2026-08-15

### Added

- **Previous session's log is preserved across restarts (#737)** — `setup_logging()`
  now renames `opensak.log` to `opensak.log.previous` at startup instead of deleting
  it, so a crash or hang from the last session can still be inspected after
  restarting. A new Help menu action, "Open log from last restart", opens it
  directly. Falls back to the old delete-and-recreate behaviour if rotation fails
  (e.g. a locked file) rather than blocking startup.

### Fixed

- **Migration and database-switch failures could still fail silently (#738)** —
  `switch_to()` now logs the full exception once at the source and re-raises,
  instead of the error being swallowed somewhere along the way. All three GUI call
  sites (toolbar database dropdown, Switch button, new-database auto-switch) catch
  it, show a clear error dialog, and avoid leaving the UI in a half-switched state.
  Startup (`ensure_active_initialised`, the original #723 code path) now shows a
  clear error and exits cleanly instead of continuing into a MainWindow with no
  valid database engine. This doesn't fix any specific unknown migration bug, but
  turns any future occurrence — known or unknown — into a visible error with a full
  traceback in the log, which is now preserved across restart thanks to #737.

---

## [1.17.0-beta.12] — 2026-08-14

### Fixed

- **App could still silently crash (appearing to hang) on startup for
  some existing databases (#723 follow-up)** — migration 2 checked for
  an outdated index name (`uq_waypoint_cache_prefix_name`) that
  migration 22 had long since replaced with `uq_waypoint_cache_wp_code`
  when it relaxed the constraint from (cache_id, prefix, name) to
  (cache_id, wp_code) (issue #536). Any database that had already run
  migration 22 didn't have the old index name, so migration 2 wrongly
  believed it had never run and tried to recreate its own old, stricter
  constraint — which real-world data can legitimately violate (that's
  exactly why migration 22 replaced it in the first place). The
  resulting error was never caught anywhere in the startup path, so the
  app crashed with no visible error message — indistinguishable from a
  hang. Root-caused with a local repro against a real 405MB/12,600-cache
  database; fixed by having migration 2 recognise either index name as
  evidence it can skip.

---

## [1.17.0-beta.11] — 2026-08-13

### Fixed

- **Split-screen map didn't update for caches outside the overview map's
  display limit (#718)** — reported by Mike Wood (lignumaqua): selecting a
  cache in the table sometimes left the map unchanged, or centred on the
  wrong area, depending on the table's current sort order. Root cause: the
  split-screen map reused the overview map's own capped/sorted marker set
  (the "Map Display" max-caches limit), so a selected cache outside that
  set simply had no marker to pan to — `panToCache()` silently did
  nothing. Selecting a cache now loads that cache's own neighbourhood
  (within a configurable radius, independent of the overview limit) and
  draws a circle on the map at that radius so it's always clear where the
  view ends; a small label appears only when a new safety cap actually
  limited the result in a dense area. Two new per-database settings
  (Settings → Map: split-screen map radius and cache limit) control this,
  defaulting to 2 km / 500 caches.

---

## [1.17.0-beta.10] — 2026-08-12

### Added

- **Startup and migration timing diagnostics (#723 follow-up)** — a report
  surfaced that the app could still appear to hang on startup for some
  large existing databases even after the beta.9 fix. To pin down exactly
  where, the full startup path now logs timestamped checkpoints to
  `opensak.log`: each phase in `app.py` (language load, database check,
  database load, main window build), every one of the 24 schema
  migrations individually, and — for the migrations most likely to be
  slow on a large database — each underlying query separately rather than
  the migration as a whole (the three backfills in migration 24, each of
  the index creations in migrations 6 and 12, and the waypoints table
  rebuild in migration 2). The one-off distance/bearing recalculation
  that can run on first launch after an upgrade is also broken down by
  phase (fetch / compute / write). No behaviour changes — this release is
  diagnostics only, so the next report comes with an exact trace instead
  of a stopwatch guess.

---

## [1.17.0-beta.9] — 2026-08-12

### Fixed

- **PQ import appeared to hang on Windows when county boundary packs
  aren't cached locally (#722)** — a missing county pack needed by the
  offline reverse-geocoding step was fetched on demand inside the resolve
  loop, but a *failed* fetch was never remembered: the same missing pack
  needed by a later cache in the same import batch retried the full
  network fetch (up to 60s) every single time. A PQ spanning many counties
  combined with slow/blocked outbound requests could stall for a very long
  time with no visible progress. Fixed with three changes: (1) failed
  fetches are now cached negatively for the rest of the run, so a missing
  pack is only attempted once; (2) a new pre-fetch phase collects every
  distinct county pack a batch will need and downloads them all in
  parallel with real progress *before* resolving starts, so the on-demand
  fetch is now a fallback rather than the common path; (3) a short
  reachability probe runs before the pre-fetch batch, so a fully blocked
  network fails fast instead of costing up to 60s per pack.
- **App appeared to hang on startup for large existing databases (#723)** —
  the startup migrations backfill several cached columns on `caches`
  (`log_count`, `last_log_date`, `last_found_date`, `last_gpx_update`,
  `last_four_logs`) using correlated subqueries against the `logs` table.
  On older databases without an index on `logs.cache_id`, each of these ran
  as a full table scan per row in `caches`, confirmed by benchmark on the
  issue. Added a migration that creates `ix_logs_cache_id` and
  `ix_logs_log_date`, placed before the three migrations that need it, so
  the index is guaranteed to exist before any of the heavy queries run.

---

## [1.17.0-beta.8] — 2026-08-12

### Fixed

- **Archived caches hidden by default in the Set Filter dialog (#576)** —
  the Availability tab's Archived checkbox defaulted to unchecked, silently
  hiding archived caches even with no other filter criteria set at all
  (GSAK, by contrast, always shows archived caches unless you explicitly
  filter them out). Also fixed a related persistence bug: explicitly
  checking Archived was silently forgotten the next time the filter dialog
  was reopened, because the "all three availability checkboxes checked"
  state was treated internally as "no filter needed" and nothing was saved
  to restore it from. Archived now defaults to checked, matching GSAK, and
  hiding archived caches is a deliberate, persisted choice like any other
  filter — it now also counts correctly toward the "N active" badge.
- **Map didn't pan to the selected cache ~60% of the time (#718)** —
  quickly selecting a different cache in the list updated the detail panel
  text but left the map pin unmoved. Leaflet.markercluster's
  `zoomToShowLayer()` reveal animation can silently drop its completion
  callback if a new cache is selected before the previous call's callback
  has fired — worse on macOS, where WebEngine's animation timing makes
  back-to-back selections more likely to overlap. Fixed with a sequence
  guard (a stale callback can no longer move the map to the wrong cache)
  and a timeout fallback for when the callback never fires at all.

Thanks to Mike for both reports.

---

## [1.17.0-beta.7] — 2026-08-11

### Added

- **14 new GSAK-compatible columns (#658)** — Cache Id, Changed date,
  Creation date, Elevation, Found count, GC.com note, Guid, Hints,
  Notes, Owner ID, Owner name, Source, Url, Watch. All backed by
  existing data — available immediately via the Column Chooser.
- **Last found / Last GPX update / Last four logs columns (#716)** —
  follow-up to #658 for the 3 remaining GSAK fields that needed new
  derived data rather than just exposing existing columns:
  - **Last found** — most recent "Found it"-type log by any finder
    (unlike the existing "Date found by me" column, which is
    specifically your own found date).
  - **Last GPX update** — local timestamp of the most recent import
    that touched a given cache.
  - **Last four logs** — shown as 4 GSAK-style colored squares (green
    = found, red = DNF, yellow = other, blank = no log), matching
    GSAK's own compact display.

  Existing databases backfill automatically on first launch after
  updating.

Thanks to Allyn56 and ianwok for the underlying GSAK-parity requests
this closes out (#518, #542, #534).

---

## [1.17.0-beta.6] — 2026-08-11

### Changed

- **Offline reverse-geocoding (Country/State/County) enabled by default (#60)** —
  the feature is now on out of the box instead of requiring manual opt-in.

### Fixed

- **Reverse-geocode bulk write could crash on large databases (#710)** — the
  bulk write hit SQLite's parameter limit on very large databases,
  producing a "too many SQL variables" error. The write is now chunked,
  so it works regardless of database size.

---

## [1.17.0-beta.5] — 2026-08-10

> Folds the v1.16.3 stable hotfix (#695 re-fix) back into `beta`. No
> other beta-only changes in this release.

---

## [1.17.0-beta.4] — 2026-08-10

> Folds the v1.16.2 stable hotfix (7 bugs) back into `beta`. No other
> beta-only changes in this release — see v1.16.2 below for details.

---

## [1.16.3] — 2026-08-10

> Corrects a mistake in v1.16.2: the #695 fix (GPX/GGZ export IDs) was
> investigated and tested during that cycle but never actually committed,
> despite the v1.16.2 release notes below stating it was fixed. Apologies
> for the confusion — it's properly fixed now, verified end-to-end
> against the original bug report's GPX file.

### Fixed

- **GPX/GGZ export: wrong/missing cache and finder IDs (#695)** — the
  exported `groundspeak:cache id` was the internal database row ID
  instead of the real Geocaching.com numeric cache ID (the GPX importer
  parsed this value but never stored it). The `groundspeak:owner` element
  and each log's `groundspeak:finder` id attribute were also missing
  entirely. All three now export correctly. Caches imported before this
  fix will need a re-import (GPX or Pocket Query) to pick up the correct
  ID — until then, export falls back to a visible `id="0"` placeholder.

Thanks to urs-beeli, pjacklam, and Allyn56 for the reports and for
re-testing after the first fix didn't actually land.

---

## [1.16.2] — 2026-08-10


> Bugfix release — no new features. Seven issues reported by urs-beeli and
> pjacklam, all fixed and verified.

### Fixed

- **GPX/GGZ export: wrong/missing cache and finder IDs (#695)** — the
  exported `groundspeak:cache id` was the internal database row ID
  instead of the real Geocaching.com numeric cache ID (the GPX importer
  parsed this value but never stored it). The `groundspeak:owner` element
  and each log's `groundspeak:finder` id attribute were also missing
  entirely, despite the underlying data already being present in the
  database. All three now export correctly. Caches imported before this
  fix will need a re-import (GPX or Pocket Query) to pick up the correct
  ID — until then, export falls back to a visible `id="0"` placeholder
  rather than the old misleading value.
- **Cache selection could take several seconds on large databases (#685)**
  — caches with many logs/attributes/waypoints/trackables (e.g. a
  well-logged cache with several trackables) triggered a SQLAlchemy query
  that joined multiple collections at once, multiplying out to tens of
  thousands of rows internally before deduplication. Fixed in four
  places: cache selection, corrected-coordinates reload, and bulk
  database moves. Up to ~500x faster in testing on worst-case data.
- **Ctrl+F did nothing (#678)** — the View-menu and toolbar filter
  actions both held the same keyboard shortcut, which Qt silently
  refuses to resolve when two actions on the same window collide. Also
  fixed a related sync issue in the customizable-shortcuts feature that
  could re-introduce the conflict after saving a custom shortcut.
- **Quick Filter dropdown not visible on Windows (#681)** — a combo box
  added directly to the menu bar rendered unreliably on Windows' native
  menu styling. Moved to the toolbar, next to the saved-filter dropdown,
  which doesn't have this problem.
- **Newly saved filter profile didn't appear in the toolbar dropdown
  until a filter was applied (#682)**.
- **Trip Planner could sometimes not be reopened after closing it (#676)**
  — an intermittent timing issue in Qt's deferred window cleanup.
- **GPS export status log showed Danish text regardless of UI language
  (#675)**.

Thanks to urs-beeli and pjacklam for the detailed bug reports that made
this release possible.

---

## [v1.17.0-beta.3] - 2026-08-09

### Fixed
- Fixed layout issue where docking the map back after using fullscreen/popout
  would leave the map view split incorrectly (#696)

### Added
- Full screen and popout map support — pop the map out to its own window or
  monitor, or maximize it within the app (#696, thanks @blazerat!)

---

## [1.17.0-beta.2] — 2026-08-02

### Fixed

- Toolbar title was hardcoded in Danish ("Værktøjslinje") regardless of the
  selected UI language, showing incorrectly in the toolbar's right-click
  context menu for all non-Danish users (#683, thanks @urs-beeli)

---

## [1.17.0-beta.1] — 2026-07-31

> Brings the `beta` line back in sync with `main`. Beta had drifted 12
> commits behind after the v1.15.1, v1.16.0, and v1.16.1 stable releases
> went out without being merged back — this release folds all of that in,
> including the #577 splitter fix below. No other beta-only changes.

### Fixed

- **Status bar / bottom panel could disappear with no way back (#577)** —
  ported from the v1.16.1 stable patch. Dragging the main vertical
  splitter all the way down let the bottom panel (info bar + detail/map)
  collapse fully to 0px, with the collapsed position then persisted and
  restored on every subsequent launch. Both the main and the detail/map
  splitter now refuse to collapse their panels completely
  (`setChildrenCollapsible(False)`), and restoring a previously-saved
  splitter ratio that would still leave either side below a small
  minimum size now falls back to the default layout instead of
  reproducing the stuck state.

---


---

## [1.16.1] — 2026-07-30

> Small patch release for a splitter/layout regression reported on Facebook
> right after 1.16.0.

### Fixed

- **Status bar could disappear with no way to bring it back (#577)** —
  dragging the main vertical splitter all the way down let the bottom
  panel (info bar + detail/map) collapse fully to 0px, with the collapsed
  position then persisted and restored on every subsequent launch. Both
  the main and the detail/map splitter now refuse to collapse their
  panels completely (`setChildrenCollapsible(False)`), and restoring a
  previously-saved splitter ratio that would still leave either side
  below a small minimum size now falls back to the default layout
  instead of reproducing the stuck state.

---

## [1.16.0] — 2026-07-29

> First stable release of the 1.16.0 cycle. Replaces the run of
> `1.16.0-beta.2` … `1.16.0-beta.16` builds — see git history for the
> detailed beta-by-beta log if needed. Headline of this cycle: a deep
> investigation into why exported caches weren't showing up correctly on
> Garmin handhelds ("Send to GPS"), plus a large-database performance
> pass and several data-integrity fixes for GSAK/GPX imports.

### Added

- **Garmin "Send to GPS" — full content and correct recognition (#656,
  #453, #454, #455, #502)** — a multi-stage investigation, working
  directly from GGZ/GPX byte comparisons against GSAK's own export of
  the same caches, found and fixed the actual root cause: OpenSAK
  exported GPX 1.1 with `groundspeak:cache` wrapped in an `<extensions>`
  element, while Garmin's on-device geocache parser is built around
  GSAK's GPX 1.0 format, where `groundspeak:cache` is a direct child of
  `<wpt>`. Export format now matches GSAK's exactly. Along the way,
  several content gaps were found and fixed: missing
  `groundspeak:attributes`, `short_description`/`long_description`, and
  `state`; logs hardcoded to the last 5 with text truncated at 500
  characters (now full history, no cap); and element order inside
  `groundspeak:cache` corrected to match the official `cache.xsd`
  sequence. Confirmed fixed by multiple testers across a GPSMAP 64s,
  66s, 66sr, and Montana 700 — description, hint, and previous logs all
  display correctly now, and exported files are correctly recognized as
  geocache files (not plain waypoints) on-device.
- **Custom Waypoints (Hotel/POI, Parking Area, Trailhead, etc.) get
  proper icons, in both the app and on Garmin devices (#593, #660)** —
  in-app, these now show their own distinct icon in the table, map, and
  detail panel instead of a generic "unknown" icon. For Garmin export,
  they're now written as plain GPX waypoints with a matching native
  Garmin icon (e.g. "Parking Area", "Lodging", "Trail Head") instead of
  showing up as an empty "fake geocache" with blank D/T stars.
- **"Use database name as filename" for GPS export** — new checkbox in
  the Send to GPS dialog (community suggestion — GSAK has an
  equivalent), on by default, pre-fills the export filename from the
  active database's name for both file-mode and device-mode exports.
  Its on/off state is remembered between exports.
- **Send to GPS collision handling** — device-mode exports ("Send to
  GPS") now prompt before silently overwriting a same-named file on the
  device, matching the protection file-mode export already had. The
  "delete old files before upload" option now also covers GGZ exports
  (previously GPX-only).
- **Large-database performance** (#627 and its follow-ups) — a new
  lightweight query path (`apply_filters_lightweight()`/
  `apply_filters_auto()`) avoids ORM row-hydration cost with an
  automatic, always-correct fallback to the full path whenever a filter
  needs it; SQL pushdown extended to every remaining filter type; map
  loading is dramatically faster via icon caching and bulk marker
  loading; a "Max caches shown on map" setting and a "disable map
  entirely" setting both target the same large-database load cost.
  Measured on a 250,000-cache benchmark database: total time to show
  all caches dropped by ~19% end-to-end, with individual steps (map
  load, filtered queries) 2–48x faster depending on the scenario. A new
  `scripts/benchmark_large_db.py` harness backs all of these numbers and
  is available for future performance work too.
- **Default Column View** (#607) — named, saveable column configurations
  (visible columns, widths, container/type display), with a toolbar
  quick-switch dropdown and a designated global default for new/
  unconfigured databases, replacing the previous implicit "last used"
  fallback.
- **Vertical gridlines in the cache table** (#463), and a **center point
  picker for the distance filter** (#511) — choose Home, a saved home
  point, the selected cache, or a manual coordinate as the filter's
  center, plus a matching right-click "Set as center point" action.

### Fixed

- **Cache distance not calculated after adding/editing a waypoint**
  (#662) — a newly added cache (including Custom Waypoints) or one with
  edited coordinates showed no distance and sorted to the bottom of the
  list until the center/home point was switched away and back. Distance
  and bearing are now recalculated immediately in both cases.
- **Wrong attribute mappings from GSAK import** (#615) — 42 of 70
  Groundspeak attribute IDs were mapped to the wrong attribute, also
  silently affecting the attribute filter regardless of import source.
- **Corrected-coordinate caches lost their original coordinates on GSAK
  database import** (#614) — the original (pre-solve) position is now
  read from GSAK's `Corrected` table instead of the already-corrected
  `Latitude`/`Longitude` columns.
- **Hidden date (and log dates) lost when importing Project-GC-style
  GPX** (#617) — an explicit UTC-offset timestamp format
  (`+00:00`) wasn't recognized by the old parser and silently came back
  as `None`.
- **Logs wiped on every re-import instead of accumulating** (#618) — a
  partial GPX/PQ re-import now merges logs (update-in-place for a
  matching ID, add new ones, keep existing ones not present in the
  current file) instead of deleting and rebuilding from that file alone.
- **Community Celebration Event caches imported as generic "Event
  Cache"** (#591) — narrow name-based fallback for this un-typed,
  time-limited Groundspeak program.
- **New database didn't inherit column settings** (#606) — falls back to
  the last-used configuration instead of hard-coded factory defaults.
- **Redundant distance recalculation on every startup** (#579) — skipped
  when nothing about the database or home point has changed since the
  last run.
- **Boolean filters bypassed their SQL indexes** (#628) — `IS true`/
  `IS false` changed to the index-usable `= true`/`= false` form.
- **Hint markup incorrectly ROT13-scrambled** (#595) — bracketed markup
  like `[br]` is now left untouched by the hint cipher.
- Several dark-theme/UI fixes: unreadable placeholder text (#624), an
  unreadable Where-filter SQL error box (#613), and Country/Region/
  County columns left-aligned instead of centered (#603).
- Database dropdown/lists not sorted alphabetically (#531, #601), and a
  misleading "database created" confirmation message (#464).

---

## [1.16.0-beta.16] — 2026-07-28

> **Beta release** — further "Send to GPS" polish following tester
> feedback on beta.15, plus proper Garmin icons for Custom Waypoints
> and Adventure Lab caches.

### Fixed

- **"Send to GPS" silently overwrote a same-named file on the device
  with no warning (#656)** — reported by CheminerWill after testing
  beta.15. The existing #501 fix only covered file-mode exports;
  device-mode ("Send to GPS") now runs the same collision check: if a
  file with the same name already exists in the device's Garmin/GPX or
  Garmin/GGZ folder, you're prompted for a new name (with an
  auto-suggested next-available name), unless "delete old files before
  upload" is checked, in which case the old files are cleared first.
  That checkbox now also works for GGZ exports (previously GPX-only).
- **Custom Waypoints (Hotel/POI, Parking Area, Trailhead, etc.) showed
  up on Garmin devices as empty "fake geocaches"** — with blank D/T
  stars and `Size: (Not Chosen)` — instead of looking like the simple
  waypoints they are. They're now exported as plain GPX waypoints with
  a proper native Garmin icon (e.g. "Parking Area", "Lodging",
  "Trail Head"). Confirmed fixed on a GPSMAP 64s via GPX import.

### Added

- **"Use database name as filename"** checkbox in the GPS export
  dialog (community suggestion — GSAK has an equivalent), checked by
  default, covering both file-mode and device-mode exports. Pre-fills
  the export filename with the currently active database's name
  instead of a fixed "opensak" default, reducing how often the
  collision prompt above gets triggered in normal day-to-day use.

### Notes

- Adventure Lab stages were also given a distinct icon attempt
  ("Flag, Blue" instead of the standard geocache icon) — this had no
  visible effect on the tested GPSMAP 64s (device firmware appears to
  always use its own icon for anything containing a full
  `groundspeak:cache` block, regardless of the `sym` field). Left in
  place since it's harmless and may help on other device/firmware
  combinations; Lab stages keep their full description/D-T/hint
  content either way, which was the more important fix.

---

## [1.16.0-beta.15] — 2026-07-28

> **Beta release** — a cluster of fixes to GPX/GGZ export for Garmin
> devices ("Send to GPS"), including the root cause of Garmin firmware
> not recognizing exported files as geocaches at all.

### Fixed

- **GPX/GGZ export missing attributes, descriptions, state, and full log
  history (#656)** — `generate_gpx()` (used by both GPX and GGZ export,
  since GGZ embeds a generated GPX internally) never wrote
  `groundspeak:attributes`, `groundspeak:short_description` /
  `long_description`, or `groundspeak:state` at all, and hardcoded log
  export to only the last 5 logs with text truncated to 500 characters.
  Confirmed via a controlled side-by-side comparison against GSAK's
  export of the same cache (GC.com direct download and GSAK both
  included full attributes, description, and complete log history for
  the same cache; OpenSAK's export was missing all of it). All of the
  above is now exported in full, with no artificial limits.

- **Garmin devices not recognizing OpenSAK's GPX/GGZ exports as geocaches
  (#656)** — the underlying root cause of the above and of related
  reports (#453, #454, #455, #502): OpenSAK exported GPX 1.1
  (`xmlns=".../GPX/1/1"`) with `groundspeak:cache` wrapped inside a
  GPX-1.1-style `<extensions>` element. GSAK exports GPX 1.0
  (`xmlns=".../GPX/1/0"`) with `groundspeak:cache` as a *direct child* of
  `<wpt>` — no `<extensions>` wrapper — which is what Garmin's on-device
  geocache parser is built around. Export format now matches GSAK's
  exactly, including declaring the `groundspeak` XML namespace locally
  on the `<groundspeak:cache>` element itself rather than at the GPX
  root. Confirmed fixed on a Garmin GPSMAP 64s: description, logs and
  hint all now display correctly for an exported cache, where previously
  only the hint displayed and description/logs did not.

---

## [1.16.0-beta.14] — 2026-07-26

> **Beta release** — vertical gridlines in the database grid (#463).

### Added

- **Vertical gridlines in the cache table** (#463) — columns in the main
  cache grid now have a thin vertical separator line at each column
  boundary, in addition to the existing alternating row colours. The
  line colour follows the active theme's palette (light/dark), so no
  additional theme handling is needed. User-configurable colours,
  independent show/hide toggles for horizontal vs. vertical lines, and
  the proposed new Appearance settings tab remain out of scope for now —
  basic functionality comes first, per the discussion on the issue.

---

## [1.16.0-beta.13] — 2026-07-26

> **Beta release** — a new Default Column View system (named, saveable
> column configurations with a toolbar quick-switch, replacing #606's
> implicit "last used" fallback), plus three small UI fixes: centered
> text columns, dark-theme placeholder text, and a readable Where-filter
> error box in dark mode.

### Fixed

- **Country/Region/County columns left-aligned instead of centered (#603)**
  — #431 centered "similar short-value columns" (Placed By, dates, etc.)
  but missed Country, Region (state) and County. Centered here for the
  same consistency #431 was going for.
- **Placeholder/hint text invisible in Dark theme (#624)** — `QPalette`'s
  `PlaceholderText` role wasn't set explicitly for either theme, so Qt
  fell back to a derived default that was unreadable in dark mode. Both
  palettes now set it explicitly to a legible, dimmed gray.
- **Where-filter SQL error box unreadable in Windows dark mode (#613)** —
  the error box used a hardcoded light-theme style (dark-red text on a
  transparent background); in dark mode that rendered as dark-red-on-dark
  gray. It now picks an explicit, theme-appropriate style via the
  existing `effective_theme()` helper.

### Added

- **Default Column View (#607)** — the "Choose columns" dialog now supports
  named, saveable "Column Views" (visible columns, widths, container/type
  display), parallel to saved filter profiles. A saved view can be picked
  from a dropdown, and one view can be marked as the global default via a
  new "Set as Default" button (shown with a ★ in the dropdown). Any
  database without its own explicit column configuration — including
  brand-new databases — now falls back to the designated default view
  instead of the hard-coded factory defaults. This replaces #606's
  implicit "last used" fallback, which silently changed on every save
  regardless of user intent; setting the default is now an explicit,
  deliberate action and does not retroactively affect databases that
  already have their own saved configuration. A quick-switch Column View
  dropdown has also been added to the main toolbar, next to the existing
  filter-profile dropdown, so a saved view can be applied to the active
  database with one click, without opening the dialog.

---

## [1.16.0-beta.12] — 2026-07-26

> **Beta release** — a cluster of GPX-import fixes reported by the
> community: a lost hidden date from Project-GC exports, logs being wiped
> on every re-import instead of accumulating, Community Celebration Event
> caches showing the wrong type, and column settings not carrying over to
> a new database.

### Fixed

- **Hidden date lost when importing a GPX file from Project-GC** (#617) —
  `_parse_datetime()` only handled a bare or `Z`-suffixed ISO 8601
  timestamp (geocaching.com's own format). Project-GC instead exports an
  explicit UTC offset (`2026-03-16T00:00:00+00:00`), which matched none
  of the old `strptime` patterns and silently came back as `None`,
  dropping the hidden date entirely. Now tries `datetime.fromisoformat()`
  first and converts properly to UTC (a `+02:00` offset is converted to
  the correct UTC instant, not just relabelled), falling back to the
  previous patterns for anything else. The same function also parses log
  dates, so this fixes any log-date loss from Project-GC-style exports
  too, not just hidden dates.

- **Logs removed from one GPX import to another** (#618) — every
  re-import (e.g. loading a new Pocket Query) deleted *all* of a cache's
  existing logs and rebuilt them from that file alone. Since a single
  GPX/PQ typically only carries a cache's most recent handful of logs,
  this meant older logs not present in that particular file were
  permanently lost on the next import — unlike GSAK, which lets logs
  accumulate over time. Logs are now merged instead: a matching log ID is
  updated in place (e.g. an edited log text), a new one is added, and any
  existing log absent from the current file is left untouched.
  `log_count`, `last_log_date`, and `dnf_date` are now derived from the
  full merged set rather than just the current file's logs, so a partial
  re-import can no longer move them backwards or silently clear them.

- **Community Celebration Event caches imported as generic "Event
  Cache"** (#591) — geocaching.com's own machine-readable
  `<groundspeak:type>` field has no distinct value for Community
  Celebration Events (a limited-run program, May 2020 – Dec 2021) — it's
  always exported as plain "Event Cache". The actual event type only
  survives in the free-text cache name (e.g. "Karlínská kasárna -
  Community Celebration Event"). Added a narrow fallback: when
  `groundspeak:type` is exactly "Event Cache" and the cache name contains
  the literal phrase "Community Celebration Event", the cache is now
  classified as such — using the dedicated type/icon that already existed
  but was never being reached. Verified against a real-world GPX export
  for the reported cache (GC8T83E); a plain Event Cache, or another type
  merely mentioning the phrase in its name, is correctly left unchanged.

- **Displayed field when importing .gpx not consistent with previous
  settings** (#606) — column visibility and widths are saved per database
  name, so a brand-new database had no saved key yet and fell straight
  back to the hard-coded defaults, silently reverting any customisation
  (e.g. an added Country/State/County column) already made in another
  database. Added a global "last used" fallback key, updated on every
  save, that a database with no settings of its own now falls back to
  before the hard-coded defaults — so a new database inherits whatever
  was last configured, anywhere.

---

## [1.16.0-beta.11] — 2026-07-22

> **Beta release** — a setting to disable the map panel entirely (and a
> visible "disabled" placeholder instead of an empty-looking map), plus a
> setting to cap the map to the nearest N caches from your home
> coordinate. Both target the same thing #627 already identified: map
> load is the largest remaining cost in "show me my caches" on a large
> database.

### Added

- **Limit map to nearest N caches from home coordinate** (#639) —
  combines @nagisml's "max caches shown on the map" suggestion (on #638)
  with sorting by distance from the active home coordinate, since a map
  with hundreds of thousands of pins isn't very readable at normal zoom
  anyway. New "Max caches shown on map" spinbox in the Map settings tab
  (0 = unlimited), default 2000. The map now gets its **own** fetch,
  independent of the table's — same active filterset, but sorted by
  distance and limited — so the table's own result set and sort order
  are completely unaffected either way.

  Added `push_limit` to `apply_filters_lightweight()`/`apply_filters_auto()`
  (default `False`, no behavior change for existing callers): when the
  whole filterset is SQL-pushed *and* the sort field is SQL-sortable, the
  limit is pushed into the SQL query itself (`LIMIT` after `ORDER BY`)
  instead of fetching every filtered row and slicing in Python. Measured
  directly (100,000-cache database, distance-sorted, no filter): the
  existing Python-slice `limit` took ~3.0s regardless of requested size
  (500 through 5000 all fetch and construct every row before slicing);
  pushing a real SQL `LIMIT` took 0.31-0.56s, correctly scaling with the
  requested size. Falls back to the existing (always-correct) Python-slice
  behavior whenever those two conditions aren't both met — a SQL `LIMIT`
  applied before a Python-only sort or Python-only filter pass would
  silently return the wrong N rows, so this is deliberately conservative;
  13 dedicated tests cover both the cases where it activates and the ones
  where it correctly must not.

  End-to-end confirmed (100,000-cache database, fetch + JSON payload
  build): 4.65s (unlimited) → 0.46s (default limit of 2000) — ~10x
  faster, with the JSON payload itself shrinking from ~195MB to ~3.9MB.

- **Setting to disable the map panel** (#638) — new "Map" tab in the
  Settings dialog (split out from General, so future map-specific
  settings have a natural shared home) with a "Show map" checkbox,
  defaulting to on (opt-out, zero behavior change for anyone who doesn't
  touch it). When off, all three `mainwindow.py` refresh paths skip
  building and loading the map's marker data entirely — since #627,
  map load has been the single largest remaining cost in "show me my
  caches" on a large database, bigger than the database query itself.
  Measured directly: the map-load step (Python-side payload build) drops
  from ~1.8s to effectively 0s on a 100,000-cache database when disabled.
  Toggling the setting back on mid-session needs no special handling —
  the existing Settings-dialog-close flow already calls
  `_refresh_cache_list()` unconditionally, which now correctly re-populates
  the map on the next call since the guard re-checks the setting fresh
  every time. Table contents and sort order are completely unaffected
  either way.

  **Follow-up:** the map's own base tiles/zoom controls rendered
  regardless of the setting — only the cache markers were actually
  skipped — which looked like a stuck or empty map rather than an
  intentional off state (reported after testing the initial version).
  A `QStackedWidget` now swaps in a plain "Map disabled" placeholder page
  instead, so the off state is visually unambiguous. Also skips the
  map's own page reload (`reload_map()`) while disabled, since there's
  no point refreshing tiles nobody's looking at — it reloads normally
  the next time the setting is re-enabled. Translated to all 8 languages.

---

## [1.16.0-beta.10] — 2026-07-22

> **Beta release** — SQL pushdown for the last group of filters that had
> none at all, following up on #627's lightweight query path.

### Added

- **SQL pushdown for remaining scalar-column filters** (#633) —
  `UserFlagFilter`, `LockedFilter`, `DnfFilter`, `FtfFilter`,
  `FavoritePointsFilter`, `HasCorrectedFilter`/`NoCorrectedFilter`,
  `FoundByMeDateFilter`, `DnfDateFilter`, and `LastLogDateFilter`
  previously had no `apply_to_query()` at all, always falling back to a
  full Python `matches()` scan. Under the ORM path this barely mattered
  (#631 found the Python pass was only ~2% of `apply_filters()`'s time —
  ORM hydration dominated regardless of whether a filter narrowed the SQL
  query or not). Under the lightweight query path (beta.9) the picture is
  different: without SQL pushdown, `apply_filters_lightweight()` must
  still construct a `LightweightCache` for every single row before
  Python-filtering it down, so a highly selective filter with no pushdown
  costs almost as much as fetching the whole table. Measured directly on
  a 100,000-cache database: `FtfFilter` (0.6% selectivity) went from
  2.42s to 0.05s (~48x faster); `UserFlagFilter` (~5%) from 2.49s to
  0.16s (~15x); `DnfFilter` (~7%) from 2.39s to 0.20s (~12x).

  Each `apply_to_query()` mirrors its `matches()` counterpart exactly,
  including NULL handling — `FoundByMeDateFilter`/`DnfDateFilter` treat a
  NULL date as "include" (found/DNF but undated), while
  `LastLogDateFilter` treats NULL as "exclude", and both are preserved
  precisely in SQL. `HasCorrectedFilter`/`NoCorrectedFilter` use a
  correlated `EXISTS`/`NOT EXISTS` against `user_notes`, which needed an
  explicit `.correlate(Cache)` — without it, `apply_filters_lightweight()`
  raised `InvalidRequestError` because its `select()` already outerjoins
  `user_notes` for corrected-coordinate display, confusing SQLAlchemy's
  auto-correlation. Only broke on the lightweight path, not the full ORM
  path — caught by testing both, not just one.

  25 new parity tests (including NULL edge cases for every field
  involved) confirm every filter's SQL and Python forms agree exactly;
  full unit-test suite (2136 tests) green, mypy clean.

---

## [1.16.0-beta.9] — 2026-07-22

> **Beta release** — the lightweight query path (#627): large databases
> load dramatically faster in both the cache table and the map, on top of
> #628-#631's smaller fixes from the last two betas. This is a
> default-behavior change for every install, not opt-in — see below for
> why that's safe.

### Added

- **Lightweight query path** (#627) — `apply_filters_lightweight()`, a new
  function in `filters/engine.py` alongside `apply_filters()`, fetches
  cache rows via a SQLAlchemy Core `select()` instead of
  `session.query(Cache)`, avoiding the ORM row-hydration cost already
  identified as `apply_filters()`'s dominant expense (#631). Results come
  back as `LightweightCache` objects — duck-typed to expose the same
  attribute names as a real `Cache` for every column the table and map
  actually use — with an automatic, transparent fallback to the existing
  `apply_filters()` ORM path whenever a filter needs a relationship or one
  of the three heavy/deferred text fields (`short_description`,
  `long_description`, `encoded_hints`). That fallback means this is always
  correct, never returning wrong or incomplete results — only sometimes
  slower than it could be.

  `mainwindow.py`'s table and map refresh now go through a single
  `apply_filters_auto()` entry point that always attempts the lightweight
  path. A thorough compatibility audit (`CacheTableModel`'s every column,
  sort key, and tooltip; `map_widget.py`'s `_do_load_caches()`,
  `_effective_coords()`, and pin-icon generation) found **zero** source
  changes were needed in either consumer — both already only touch scalar
  fields, cached count columns, or `.user_note`'s three attributes, never
  a relationship collection directly. Row selection already reloads a
  full `Cache` via the established `_load_full_cache(gc_code)` pattern
  regardless of what's currently in the table.

  Confirmed final numbers (250,000-cache database, via the real
  `apply_filters_auto()` wiring):

  | Scenario | `apply_filters` | `apply_filters_auto` | Speedup |
  |---|---|---|---|
  | No filter | 8.05s | 3.10s | ~2.6x faster |
  | Exclude archived | 11.02s | 3.81s | ~2.9x faster |
  | Within 50km | 1.52s | 0.97s | ~1.6x faster |
  | `CacheTableModel.load()` | 0.198s | 0.027s | ~7.3x faster |

  **Two real bugs were found and fixed during testing, before release:**
  a `LightweightCache` design that eagerly copied every one of its ~52
  fields at construction time fixed a table-load regression (delegating
  every attribute through `__getattr__` was costing more than the
  fetch-side win it was meant to complement) but overcorrected, nearly
  erasing the fetch-side win in the process — the final design only
  eagerly copies the three fields `CacheTableModel` touches
  unconditionally on every row (`id`, `distance`, `bearing`), leaving
  everything else lazy. Separately, `reload_caches_full()` — the helper
  GPX/LOC/GGZ export, KML export, GPS-device export, and the trip planner
  all use to reload full cache data before generating output — checked
  `isinstance(c, Cache)`, which silently excluded every `LightweightCache`
  row from its reload and would have crashed all four export paths the
  moment they touched a deferred field; fixed by recognizing both types as
  reloadable. Both were caught by the project's own test suite (the
  second one by the e2e suite specifically) before ever reaching a tagged
  release.

  Confidence for shipping this as default (not opt-in) behavior comes
  from: the lightweight path's own automatic per-filterset fallback to the
  exact same full-ORM code path used today; full parity test coverage
  (`test_filter_sql_parity.py`, `test_filter_lightweight.py`) proving
  `apply_filters_lightweight()` never diverges from `apply_filters()`'s
  result set across every filter type, NULL edge case, and composition;
  dedicated compatibility audits and test suites for both the table and
  the map with zero source changes needed in either; and a full pass of
  the unit suite (2111 tests), the e2e suite (244 tests), and a
  250,000-cache benchmark, all green.

- **Benchmark harness measures the lightweight query path** (#628) —
  `scripts/benchmark_large_db.py` now also runs its three `apply_filters`
  scenarios through `apply_filters_auto()`, and runs the map/table-load
  steps against both result sets, so a single report shows the full
  before/after picture instead of requiring a separate isolated A/B
  script. Fixed a measurement-fairness bug found while adding this: the
  icon HTML `@lru_cache` (#629) meant whichever "Map load" measurement ran
  first in the script paid the one-time cache-warming cost and the second
  one benefited "for free" — fixed with an explicit warmup pass before
  either timed measurement.

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
