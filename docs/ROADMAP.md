# OpenSAK Roadmap

*Last updated: 29 October 2026 (1.22.0: Macro & Scripting Support and Full GSAK Field Compatibility done)*

*This reflects the current priority order for planned work. It's a living
document and will be updated as things progress — not a fixed release schedule
or a set of promises with dates attached.*

---

### 1. Reverse-Geocoding Boundary Data
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

*Status: planned (#968). The first fix (#781) is targeted for an upcoming
release; contributions from the community are very welcome.*

### 2. Welcome Wizard Enhancements
Expand the first-run Welcome Wizard. Candidates to evaluate: language selection,
first-database creation, guided PQ/GPX import, Geocaching.com login, and (on
Windows) a prompt to exclude the database folder from antivirus scanning. (The
backup folder is already chosen in the wizard as of 1.21.0.)

### 3. GPSBabel Integration
Integrate GPSBabel as both an import and export option, primarily to support legacy
and third-party formats (e.g. GDB) on the import side. Native GPX/GGZ export already
covers most current needs, so this mainly rounds out format compatibility for the
remaining edge cases.

*Status: under investigation (#795), with the focus on the import side.*

### 4. Description & Hint Translation
Offer machine translation of cache descriptions and hints using Argos Translate
(offline, no external API dependency). Must preserve the original text, allow
restoring it, and never let a re-import/GPX refresh silently overwrite the
translation or the saved original.

### 5. Customizable Menus & Toolbars
Give users control over toolbar/menu layout: icons-only mode, add/remove items,
additional pulldown menus, and automatic adaptation of the number of visible menu
items to the window size.

### 6. User Preferences & Theming
Add a user-facing settings/configuration system covering things like font size,
color scheme, and default layout — a foundation for broader personalization over
time.

### 7. Geo-Data Auto-Update
Keep the boundary data current automatically, pulling updates from
OpenStreetMap or a versioned central repository instead of relying on manual
rebuilds. Builds on the contribution path in item 1.

### 8. Geocaching.com API Field Coverage
Extend database field support to cover data available via the Geocaching.com
Partner API. This depends on API access approval from Geocaching.com — included
here so the plans are visible while we wait.

*Status: waiting for Partner API approval.*

---

## Done

### Macro & Scripting Support
Give users a way to automate OpenSAK, as a modern successor to the GSAK Macro
Language: an embedded scripting language (Lua) with a documented OpenSAK macro
API, so existing GSAK macros are ported rather than run unchanged.

> **✅ Done (1.22.0).** A **Macros** menu with a macro editor (form designer,
> API reference, autocomplete for VS Code, Notepad++ and UltraEdit), a Macro
> manager with keyboard shortcuts, and example macros. Macros filter, change,
> import and export caches, show dialogs and keep their own settings and
> tables. They run in a sandbox and ask before they change your caches or use
> a folder. Macro API 4 is the first stable API: macros written for it keep
> working in later releases, and anything renamed or removed goes through a
> deprecation period. Shared macros are collected in
> [OpenSAK-Macros](https://github.com/OpenSAK-Org/OpenSAK-Macros). GPX and GSAK
> imports can also run from the command line, for scheduled imports (#937).
> Macros contacting web sites (`opensak.get_url()`) stay in beta builds for one
> more cycle; that, headless macros and more API follow in #1195.

### Full GSAK Field Compatibility
Support the fields GSAK users rely on, so GSAK databases, filters and ported
macros carry over.

> **✅ Done (1.22.0).** Custom fields as in GSAK — local and global, Yes/No,
> number, text and date, with display expressions — defined in
> **Tools → Custom fields…**, brought along by the GSAK import, shown as
> columns and on the detail panel, edited in **Edit cache**, filtered on the
> Filter dialog's **Custom** tab and in Where clauses, and read and written by
> macros. They live in a companion file next to the database, which is copied,
> moved and backed up with it. The design and the GSAK field inventory are in
> [`docs/architecture/gsak-fields.md`](architecture/gsak-fields.md).

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
