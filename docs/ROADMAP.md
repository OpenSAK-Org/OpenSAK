# OpenSAK Roadmap

*Last updated: 6 October 2026 (Backup Support moved to "Done"; macro status
updated; reverse-geocoding boundary data moved up and expanded)*

*This reflects the current priority order for planned work. It's a living
document and will be updated as things progress — not a fixed release schedule
or a set of promises with dates attached.*

---

### 1. Macro & Scripting Support
Give users a way to automate OpenSAK, as a modern successor to the GSAK Macro
Language. The concept is an embedded scripting language (Lua) with a documented
OpenSAK macro API, so existing GSAK macros are ported rather than run unchanged.
This does not depend on Geocaching.com API access: macros can work with
everything OpenSAK already stores, and only the extra fields that come from the
API (item 10) will be missing until that access exists.

This is a large piece of work, so it arrives in steps rather than all at once:
- **Already in the 1.21.0 betas:** a **Macros** menu with **Run macro (Lua)…**,
  macros that filter the cache list and apply saved filter profiles, and macros
  that set corrected coordinates from a CSV file (with a ready-made example
  under **Macros → Open example**). Macros run in a sandbox: they are stopped if
  they loop endlessly, they can only use the folders listed under
  **Settings → Folder permissions**, and they can never reach OpenSAK's own
  settings, login or database files. The functions are documented in
  `docs/macros/api.md`, with autocompletion in VS Code.
- **Next:** command-line import of GPX files and GSAK databases, so a refresh
  can be scheduled overnight (#937), and a read-only SQL query tool for ad hoc
  queries (#810).
- **Then:** dialogs and forms that macros can show (#982), and per-macro saved
  settings (#983). Most GSAK macros rely on forms, so these come before the
  rest.
- **Later:** a wider core API (cache access, notes, import/export, database
  management), a macro manager, help with porting GSAK macros, and further API
  driven by real-world macros.

*Status: in progress (#938). Macros are **beta builds only** for now and the
commands will change while the API settles, so treat macros written today as
experiments. Community use cases are collected in #808–#813 — more are
welcome.*

### 2. Reverse-Geocoding Boundary Data
OpenSAK fills in a cache's country, state and county from its coordinates using
offline boundary data (OpenSAK-Data). It is one of the most requested areas for
improvement: some regions are missing or out of date (Switzerland, Mexico,
Norway and Great Britain among them), a cache near a border can end up in the
wrong state or with a blank field, and today only someone holding the original
GSAK source files can rebuild the data — so polygons people have offered to
contribute have no way in.

The plan, in steps:
- **Fix what's broken:** the boundary update being offered again after it has
  been applied (#781), small gaps between neighbouring states that leave
  caches without a state (#782), and missing source data going unnoticed
  (#789, #814).
- **A contribution path:** one documented format for boundary files, automatic
  checks on every contribution, and builds anyone can reproduce. OpenStreetMap
  is the preferred source, aligned with Project-GC where possible; wrong
  borders are fixed in OpenStreetMap rather than patched locally, and every
  contribution's source and licence is recorded (#776).
- **First contributions:** Great Britain (#839), Norway (#814), Bermuda,
  Switzerland (#788) and Mexico (#783).
- **Your own polygons:** load a polygon file of your own for a database, in the
  same format as contributions, filling in regions the shared data doesn't
  cover (#777).

*Status: planned (#968). The first fix (#781) is targeted for 1.21.0;
contributions from the community are very welcome.*

### 3. Full GSAK Field Compatibility
Extend the database to support the complete set of fields historically supported by
GSAK, establishing full GSAK parity as a foundation to build on. This also matters
for macros (ported GSAK macros read these fields) and for a Custom tab in the
Filter dialog, the largest remaining gap in the filter parity work.

*Status: not started. (Separate from the Filter dialog's GSAK parity, #821, which
largely landed in 1.19.0 and 1.20.0.)*

### 4. Welcome Wizard Enhancements
Expand the first-run Welcome Wizard. Candidates to evaluate: language selection,
first-database creation, guided PQ/GPX import, Geocaching.com login, and (on
Windows) a prompt to exclude the database folder from antivirus scanning. (The
backup folder is already chosen in the wizard as of 1.21.0.)

### 5. GPSBabel Integration
Integrate GPSBabel as both an import and export option, primarily to support legacy
and third-party formats (e.g. GDB) on the import side. Native GPX/GGZ export already
covers most current needs, so this mainly rounds out format compatibility for the
remaining edge cases.

*Status: under investigation (#795), with the focus on the import side.*

### 6. Description & Hint Translation
Offer machine translation of cache descriptions and hints using Argos Translate
(offline, no external API dependency). Must preserve the original text, allow
restoring it, and never let a re-import/GPX refresh silently overwrite the
translation or the saved original.

### 7. Customizable Menus & Toolbars
Give users control over toolbar/menu layout: icons-only mode, add/remove items,
additional pulldown menus, and automatic adaptation of the number of visible menu
items to the window size.

### 8. User Preferences & Theming
Add a user-facing settings/configuration system covering things like font size,
color scheme, and default layout — a foundation for broader personalization over
time.

### 9. Geo-Data Auto-Update
Keep the boundary data current automatically, pulling updates from
OpenStreetMap or a versioned central repository instead of relying on manual
rebuilds. Builds on the contribution path in item 2.

### 10. Geocaching.com API Field Coverage
Extend database field support to cover data available via the Geocaching.com
Partner API. This depends on API access approval from Geocaching.com — included
here so the plans are visible while we wait.

*Status: waiting for Partner API approval.*

---

## Done

### Backup Support
Built-in backup and restore for OpenSAK databases, so every user's data is
protected — and a safety net for the automation coming with macros and
scheduled imports.

> **✅ Done (1.21.0).** **File → Back up now…** and **File → Restore from
> backup…**; an offer to back up when you close OpenSAK (Ask / Always / Never),
> keeping the last five automatic backups while manual backups are never
> deleted; a backup folder chosen in the Welcome Wizard or Settings; optional
> compressed backups; restoring your settings as well as databases; and an
> automatic copy of a database before a new OpenSAK version updates its format.
> A restore always adds a new database and never overwrites an existing one,
> and passwords and logins are never stored in a backup. Scheduled backups
> while OpenSAK runs are planned as a follow-up (#942).

### Installation & Uninstallation
Provide a clean install/uninstall path on every supported platform (Windows, macOS,
Linux). On uninstall, offer an explicit, opt-in option to also remove all setup files,
data files, and databases — this must never be the default, to avoid accidental data
loss.

> **✅ Done (1.19.0 and 1.20.0).** Windows: Microsoft Store (signed, updates
> itself; uninstall via Windows Settings) plus a direct download. macOS: signed,
> notarized `.dmg`, in-app update and in-app uninstall. Linux: AppImage that adds
> itself to the application menu, updates itself and uninstalls from within the
> app. Removing your data is always an explicit, confirmed choice.

### Email-Based PQ Import
Add support for reading Pocket Queries directly from the user's own mailbox into the
active database, with an opt-in option to delete the email afterward. Needs careful,
secure handling of mail credentials.

> **✅ Done (1.19.0).** Password kept in the OS keyring; the mail server's
> certificate is verified (1.20.0). Gmail/Outlook.com need OAuth2 (#697/#698), and
> scheduled checking is tracked as #445.

---

Have thoughts, questions, or something you think is missing? Join the discussion
on GitHub or in the Facebook group.
