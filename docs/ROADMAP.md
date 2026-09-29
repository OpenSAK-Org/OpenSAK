# OpenSAK Roadmap

*Last updated: 29 September 2026 (status notes added — priority order unchanged)*

*This reflects the current priority order for planned work. It's a living
document and will be updated as things progress — not a fixed release schedule
or a set of promises with dates attached.*

---

### 1. Installation & Uninstallation
Provide a clean install/uninstall path on every supported platform (Windows, macOS,
Linux). On uninstall, offer an explicit, opt-in option to also remove all setup files,
data files, and databases — this must never be the default, to avoid accidental data
loss. This is the first thing most new users experience, so it gets top priority.

> **Status: ✅ Done (1.19.0 and 1.20.0).** Windows: Microsoft Store (signed, updates
> itself; uninstall via Windows Settings) plus a direct download. macOS: signed,
> notarized `.dmg`, in-app update and in-app uninstall. Linux: AppImage that adds
> itself to the application menu, updates itself and uninstalls from within the
> app. Removing your data is always an explicit, confirmed choice.

### 2. Welcome Wizard Enhancements
Expand the first-run Welcome Wizard. Candidates to evaluate: language selection,
first-database creation, guided PQ/GPX import, Geocaching.com login, backup location
setup, and (on Windows) a prompt to exclude the database folder from antivirus
scanning.

### 3. Backup Support
Add built-in backup, copy, restore, and delete functionality for OpenSAK databases —
both manual, on-demand backups and optional scheduled/automatic backups. Since
OpenSAK is a local desktop application with no external storage dependency, cleanup
stays entirely the user's own choice.

*Status: not started. Until then, **Help → OpenSAK File Locations…** (1.20.0)
shows exactly which folders to back up.*

### 4. GPSBabel Integration
Integrate GPSBabel as both an import and export option, primarily to support legacy
and third-party formats (e.g. GDB) on the import side. Native GPX/GGZ export already
covers most current needs, so this mainly rounds out format compatibility for the
remaining edge cases.

*Status: under investigation (#795), with the focus on the import side.*

### 5. Full GSAK Field Compatibility
Extend the database to support the complete set of fields historically supported by
GSAK, establishing full GSAK parity as a foundation to build on.

*Status: not started. (Separate from the Filter dialog's GSAK parity, #821, which
largely landed in 1.19.0 and 1.20.0.)*

### 6. Email-Based PQ Import
Add support for reading Pocket Queries directly from the user's own mailbox into the
active database, with an opt-in option to delete the email afterward. Needs careful,
secure handling of mail credentials. High real-world impact for users.

> **Status: ✅ Done (1.19.0).** Password kept in the OS keyring; the mail server's
> certificate is verified (1.20.0). Gmail/Outlook.com need OAuth2 (#697/#698), and
> scheduled checking is tracked as #445.

### 7. Description & Hint Translation
Offer machine translation of cache descriptions and hints using Argos Translate
(offline, no external API dependency). Must preserve the original text, allow
restoring it, and never let a re-import/GPX refresh silently overwrite the
translation or the saved original.

### 8. Custom User Polygons
Allow users to supply and manage their own custom polygons for filtering and region
assignment. Enables a path for the community to contribute and share polygon data,
extending OpenSAK-Data coverage beyond what the project can maintain alone.

*Status: not started (#777). Region-specific data gaps are tracked separately.*

### 9. Customizable Menus & Toolbars
Give users control over toolbar/menu layout: icons-only mode, add/remove items,
additional pulldown menus, and automatic adaptation of the number of visible menu
items to the window size.

### 10. User Preferences & Theming
Add a user-facing settings/configuration system covering things like font size,
color scheme, and default layout — a foundation for broader personalization over
time.

### 11. Geo-Data Auto-Update
Automate the update process for boundary/region polygons in OpenSAK-Data.

### 12. Geocaching.com API Field Coverage
Extend database field support to cover data available via the Geocaching.com
Partner API. This depends on API access approval from Geocaching.com — included
here so the plans are visible while we wait.

*Status: waiting for Partner API approval.*

### 13. GSAK Macro Support
Explore support for running GSAK-style macros within OpenSAK. This is a deliberate,
longer-term item: it depends on both Geocaching.com API access and OpenSAK
maturing further first, so it's not near-term — but it's on our radar, and we
wanted it visible here rather than left unspoken.

*Status: design phase — community use cases collected in #808–#813.*

---

Have thoughts, questions, or something you think is missing? Join the discussion
on GitHub or in the Facebook group.
