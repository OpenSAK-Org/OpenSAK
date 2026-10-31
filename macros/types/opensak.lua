---@meta
-- Generated from src/opensak/macro/runtime.py by scripts/generate_macro_api_docs.py — do not edit by hand.
-- OpenSAK Lua macro API, version 4. Reference: docs/macros/api.md

---Keys understood by `opensak.filter{}`, combined with AND (or OR with `mode`).
---@class opensak.FilterSpec
---@field type? string|string[] Cache type(s); the " Cache" suffix may be left out.
---@field container? string|string[] Container size(s).
---@field difficulty? number|number[] Exact value or {min, max}.
---@field terrain? number|number[] Exact value or {min, max}.
---@field found? boolean Only found or only unfound caches.
---@field available? boolean Only available caches (not disabled or archived).
---@field archived? boolean Only archived caches, or only caches that are not archived.
---@field corrected? boolean With or without corrected coordinates.
---@field user_flag? boolean That flag set, or not set.
---@field locked? boolean That flag set, or not set.
---@field macro_flag? boolean That flag set, or not set.
---@field dnf? boolean That flag set, or not set.
---@field ftf? boolean That flag set, or not set.
---@field premium? boolean That flag set, or not set.
---@field has_trackables? boolean With or without trackables in the cache.
---@field owned? boolean Owned by you, or not: the `owned` cache field (owner ID = your finder ID, or owner name = your username in Settings).
---@field near? table Within `km` of a coordinate or of a saved centre point.
---@field distance? number|number[] Km from the active centre point: at most a number, or {min, max} where nil leaves a side open.
---@field bearing? number[] Bearing from the active centre point, clockwise from the first to the second value.
---@field favorites? number|number[] Favourite points / elevation in metres: exact value, or {min, max} where nil leaves a side open.
---@field elevation? number|number[] Favourite points / elevation in metres: exact value, or {min, max} where nil leaves a side open.
---@field hidden? string[] Date range {from, to}, both inclusive, "YYYY-MM-DD"; nil leaves a side open. `last_gpx_update` and `last_user_update` also take a time, "2026-09-01T18:00", in local time.
---@field found_date? string[] Date range {from, to}, both inclusive, "YYYY-MM-DD"; nil leaves a side open. `last_gpx_update` and `last_user_update` also take a time, "2026-09-01T18:00", in local time.
---@field last_gpx_update? string[] Date range {from, to}, both inclusive, "YYYY-MM-DD"; nil leaves a side open. `last_gpx_update` and `last_user_update` also take a time, "2026-09-01T18:00", in local time.
---@field last_user_update? string[] Date range {from, to}, both inclusive, "YYYY-MM-DD"; nil leaves a side open. `last_gpx_update` and `last_user_update` also take a time, "2026-09-01T18:00", in local time.
---@field attributes? string|string[] Attributes the cache must have (all of them): English name or Groundspeak id; a leading `-` means the attribute's "no" form (e.g. "-Dogs" = no dogs allowed).
---@field name? string "Contains" match on that field (`note` = your local note).
---@field code? string "Contains" match on that field (`note` = your local note).
---@field owner? string "Contains" match on that field (`note` = your local note).
---@field placed_by? string "Contains" match on that field (`note` = your local note).
---@field country? string "Contains" match on that field (`note` = your local note).
---@field state? string "Contains" match on that field (`note` = your local note).
---@field county? string "Contains" match on that field (`note` = your local note).
---@field user_data1? string "Contains" match on that field (`note` = your local note).
---@field user_data2? string "Contains" match on that field (`note` = your local note).
---@field user_data3? string "Contains" match on that field (`note` = your local note).
---@field user_data4? string "Contains" match on that field (`note` = your local note).
---@field gc_note? string "Contains" match on that field (`note` = your local note).
---@field note? string "Contains" match on that field (`note` = your local note).
---@field text? string Full-text search in description, logs and notes.
---@field polygon? string|table Inside a polygon: a file (as for opensak.coords.inside) or a table of points.
---@field codes? string[] Exactly these GC codes, e.g. from an opensak.sql() result.
---@field where? string Raw clause against the caches table.
---@field mode? "AND"|"OR" How the criteria are combined (default AND).
---@field label? string Shown in the toolbar (optional, default "Macro").

---Filter keys plus `fields`, `all` and `database`, understood by `opensak.caches{}`.
---@class opensak.CachesSpec: opensak.FilterSpec
---@field all? boolean Every cache of the database, whatever the active filter shows.
---@field fields? string[] Cache fields to load (`code` is always included).
---@field database? string Read this database instead of the active one.

---Options of the read functions (`opensak.cache()`, `opensak.sql()`, ...).
---@class opensak.ReadOptions
---@field database? string Read this database instead of the active one.

---The table of `opensak.set_companion_table{}`.
---@class opensak.CompanionTableSpec
---@field name string A table or view of the running macro in the companion database.
---@field linked? boolean Cache-linked: its rows follow their caches.
---@field code_column? string The column with the GC code (default `gc_code`); implies `linked`.
---@field description? string Shown with the table in OpenSAK.

---A table or view in the companion database, from `opensak.companion_tables()`.
---@class opensak.CompanionTable
---@field name string Table or view name, used as `companion.<name>`.
---@field type "table"|"view"
---@field owner? string The owner's key: the macro file's path, or `opensak`; nil without an owner.
---@field owner_name? string The owner's name, for display.
---@field mine boolean The running macro owns it, so it can change it.
---@field kind "cache"|"free" `cache`: cache-linked, its rows follow their caches.
---@field code_column? string For a cache-linked table: the column with the GC code.
---@field description? string
---@field rows? integer Number of rows (tables only).

---A custom field, from `opensak.custom_fields()`.
---@class opensak.CustomField
---@field name string The field's name, the key in a cache's `custom` table.
---@field type "string"|"integer"|"real"|"date"|"boolean"
---@field default any The value of a cache nobody set the field for.
---@field scope "local"|"global" `global`: defined for every database.
---@field origin "opensak"|"gsak" Where the definition came from.
---@field position integer Display order.
---@field expression? string GSAK's display expression, if any.

---The table of `opensak.define_custom_field{}`.
---@class opensak.CustomFieldDefinition
---@field name string Letters, digits and `_`; not a cache column or an SQL keyword.
---@field type "string"|"integer"|"real"|"date"|"boolean"
---@field default? any Shown for every cache nobody set the field for.
---@field scope? "local"|"global" Default "local" (this database only).
---@field expression? string SQL shown in the cache list instead of the value, e.g. `nullif(Finds, -1)`.

---Options of `opensak.get_url()`.
---@class opensak.GetUrlOptions
---@field timeout? number Seconds without an answer, 1-120 (default 30).
---@field caption? string Show a progress window with this title.
---@field binary? boolean Return the bytes as they are, without decoding (default false).
---@field max_bytes? integer The largest body in bytes (default 10 MB, at most 50 MB).

---What `opensak.get_url()` got besides the body.
---@class opensak.UrlInfo
---@field status integer The HTTP status, e.g. 200 or 404.
---@field content_type string The Content-Type header ("" if none).
---@field url string The URL after redirects.

---Options of `opensak.move_caches()` and `opensak.copy_caches()`.
---@class opensak.TransferOptions
---@field codes? string[] GC codes to transfer (default: the caches of the active filter).
---@field if_exists? "newer"|"replace"|"skip" When the cache exists in the target (default "newer").

---A matrix from `opensak.stats.pivot()`, for `opensak.html.matrix()`.
---@class opensak.Matrix
---@field rows any[] Values of the row field.
---@field cols any[] Values of the column field.
---@field cells number[][] `cells[i][j]`: the count for rows[i] and cols[j].
---@field max number The largest cell.
---@field total number Sum of all cells.
---@field filled integer Number of cells that are not 0.

---A run of days from `opensak.stats.streaks()`.
---@class opensak.Run
---@field days integer Number of days.
---@field first string First day, "YYYY-MM-DD".
---@field last string Last day, "YYYY-MM-DD".
---Options of `opensak.import_gsak()`.
---@class opensak.GsakImportOptions
---@field databases? string|string[] GSAK databases to import, by name (default: all of them).
---@field exclude? string|string[] GSAK databases to leave out.
---@field target? string|table<string, string> One OpenSAK database for all, or GSAK name = OpenSAK database (default: the GSAK name).
---@field if_exists? "skip"|"merge"|"overwrite" When the OpenSAK database exists (default "skip").
---@field create? boolean Create a missing OpenSAK database (default true).
---@field filters? boolean|"skip"|"overwrite" Import the saved filters of gsak.db3 (default false).
---@field locations? boolean|"skip"|"overwrite" Import the user locations of gsak.db3 (default false).
---@field quiet? boolean Don't print the summary (default false).

---What one GSAK database of `opensak.import_gsak()` did.
---@class opensak.GsakImportDatabase
---@field gsak string GSAK database name.
---@field target string OpenSAK database.
---@field action "created"|"merged"|"overwritten"|"skipped"|"failed"|"cancelled"
---@field reason? string Why it was skipped, failed or cancelled.
---@field cleared integer Caches removed first (overwrite).
---@field created? integer New caches.
---@field updated? integer Updated caches.
---@field skipped? integer Caches that could not be imported.
---@field waypoints? integer
---@field logs? integer
---@field attributes? integer
---@field notes? integer
---@field trackables? integer
---@field corrected? integer
---@field note_images? integer Embedded note images replaced by placeholders.
---@field warnings? integer
---@field errors? string[] The first cache errors.

---The report of `opensak.import_gsak()`.
---@class opensak.GsakImportReport
---@field ok boolean Nothing failed and the run was not cancelled.
---@field cancelled boolean
---@field source string
---@field kind "backup"|"folder"|"file"|"settings"
---@field created integer New caches, all databases.
---@field updated integer Updated caches, all databases.
---@field skipped integer Caches that could not be imported.
---@field errors integer Failed caches, databases and settings imports.
---@field databases opensak.GsakImportDatabase[]
---@field new_databases string[] OpenSAK databases created or added to the list.
---@field locations? {added?: integer, updated?: integer, skipped?: integer, invalid?: integer, error?: string}
---@field filters? {imported?: integer, skipped?: integer, failed?: integer, error?: string}
---@field notes string[]

---Options of `opensak.import_gpx()`.
---@class opensak.GpxImportOptions
---@field database? string OpenSAK database to import into (default: the setting's database, else the active one).
---@field quiet? boolean Don't print the summary (default false).

---What one file of `opensak.import_gpx()` did.
---@class opensak.GpxImportFile
---@field path string
---@field error? string Why the file (or a refused file or folder of the setting) failed.
---@field created? integer New caches.
---@field updated? integer Updated caches.
---@field skipped? integer Caches that could not be imported.
---@field waypoints? integer
---@field warnings? integer
---@field errors? string[] The first cache errors.

---The report of `opensak.import_gpx()`.
---@class opensak.GpxImportReport
---@field ok boolean Nothing failed and the run was not cancelled.
---@field cancelled boolean
---@field source string
---@field kind "setting"|"folder"|"file"
---@field database string OpenSAK database imported into.
---@field created integer New caches, all files.
---@field updated integer Updated caches, all files.
---@field skipped integer Caches that could not be imported.
---@field waypoints integer
---@field errors integer Failed caches and files.
---@field files opensak.GpxImportFile[]
---@field missing string[] Files and folders of the setting that no longer exist.

---What `opensak.export_gpx{}` writes.
---@class opensak.ExportSpec
---@field path? string File to write; the name may use {database}, {filter}, {date}, ... (default "{database}" for a device).
---@field format? "gpx"|"ggz"|"loc"|"kml" Default: from the extension of `path`, else "gpx" (a device takes gpx or ggz).
---@field corrected? boolean Use corrected coordinates where set (default true).
---@field max? integer At most this many caches (default 0 = all).
---@field rename? fun(c: opensak.Cache): string? Name to write instead of the cache name.
---@field description? fun(c: opensak.Cache): string? Waypoint description to write (GPX desc, LOC label, KML pop-up).
---@field pois? {attributes?: boolean, child_waypoints?: boolean} Leave out attributes (GPX/GGZ) or child waypoints (GPX/GGZ/KML); both default true.
---@field if_exists? "overwrite"|"skip"|"ask" When the file exists (default "overwrite").
---@field target? "file"|"device" "device": the GPX/GGZ folder of the connected Garmin (default "file").
---@field device? string The device's folder, when several Garmin devices are connected.

---The table passed to `opensak.form{}`.
---@class opensak.FormSpec
---@field title? string Window title.
---@field fields? opensak.FormField[] The fields, top to bottom (see Form fields).
---@field buttons? opensak.FormButton[] The buttons at the bottom; default OK and Cancel. `{}` for none, e.g. when the fields include buttons.
---@field layout? "auto"|"absolute" "auto" (default) stacks the fields with their labels on the left; "absolute" places each field at its `x`, `y` like GSAK.
---@field width? integer Width of the field area in pixels (absolute layout: default fits the fields).
---@field height? integer Height of the field area in pixels.
---@field remember? boolean Start with the values the user entered last time (stored per macro file, next to opensak.settings; the macro must be saved). Fields with `remember = false` and passwords are not kept. Only values are kept: when the form opens, `on_change` is called for each field that got a remembered value (in form order, before `on_open`), so what it enables, hides or relabels is set again.
---@field id? string Name the remembered values are kept under (default: the title), for macros with several forms.
---@field timeout? number Close the form after this many seconds; it then returns the values and "timeout".
---@field on_open? fun(f: opensak.Form) Called once when the form opens, after the `on_change` calls for remembered values.
---@field validate? fun(f: opensak.Form, button: string): string? Called before the form closes with a button other than a cancel button; return a message to show it and keep the form open.

---A field of `opensak.form{}` (see Forms in the API reference).
---@class opensak.FormField
---@field type "label"|"separator"|"text"|"number"|"memo"|"checkbox"|"choice"|"radio"|"checklist"|"date"|"file"|"folder"|"group"|"button"|"image"|"html" The field type.
---@field id? string Key of the value in the result; needed for fields with a value, buttons, and fields a callback changes.
---@field label? string Caption: on the left in the auto layout, next to a checkbox, the title of a group, the text of a label or button.
---@field default? any Initial value (for a button: the button Enter presses).
---@field tooltip? string Shown when the mouse rests on the field.
---@field enabled? boolean false greys the field out (default true).
---@field visible? boolean false hides the field (default true).
---@field x? integer Absolute layout: pixels from the left of the form or group.
---@field y? integer Absolute layout: pixels from the top of the form or group.
---@field w? integer Width in pixels (auto layout: minimum width).
---@field h? integer Height in pixels (auto layout: minimum height).
---@field bold? boolean Bold caption.
---@field italic? boolean Italic caption.
---@field color? string Caption colour, "#rrggbb" or a name such as "red".
---@field font_size? integer Caption font size in points.
---@field on_change? fun(f: opensak.Form, value: any, id: string) Called when the user changes the value — a text or number field when it loses focus or Enter is pressed — and, with `remember = true`, when the form opens with a remembered value. The form stays open.
---@field exit_on_change? boolean Close the form when the value changes (after on_change); it returns the values and the field's id. Like GSAK's ExitOnChange.
---@field remember? boolean false: never remember this field's value.
---@field on_click? fun(f: opensak.Form, id: string) button: called on a click; the form stays open (f:close() closes it).
---@field cancel? boolean button: closes the form, which returns nil (also Esc).
---@field options? (string|number)[] choice, radio, checklist: the options.
---@field editable? boolean choice: also accept typed text.
---@field columns? integer radio, checklist: number of columns (default 1).
---@field placeholder? string Grey hint shown while the field is empty.
---@field password? boolean text: hide the characters (never remembered).
---@field readonly? boolean text, memo: can be selected and copied, not changed.
---@field required? boolean The form cannot be confirmed while the field is empty.
---@field min? number|string number: smallest value; date: earliest date.
---@field max? number|string number: largest value; date: latest date.
---@field decimals? integer number: digits after the decimal point (default 0).
---@field step? number number: change per arrow click (default 1).
---@field suffix? string number: unit shown after the value, e.g. " km".
---@field wrap? boolean memo: wrap long lines (default true).
---@field monospace? boolean memo: fixed-width font.
---@field filter? string file: file types, e.g. "CSV files (*.csv)".
---@field mode? "open"|"save" file: pick a file to read (default) or to write.
---@field align? "left"|"center"|"right" label: text alignment.
---@field fields? opensak.FormField[] group: the fields inside it.
---@field path? string image: the picture file.
---@field data? string image: the picture as base64 text instead of a file.
---@field scale? boolean image: stretch the picture to w × h.
---@field html? string html: the HTML to show.

---A button at the bottom of a form.
---@class opensak.FormButton
---@field id? string Key of the value in the result; needed for fields with a value, buttons, and fields a callback changes.
---@field label? string Caption: on the left in the auto layout, next to a checkbox, the title of a group, the text of a label or button.
---@field default? any Initial value (for a button: the button Enter presses).
---@field tooltip? string Shown when the mouse rests on the field.
---@field enabled? boolean false greys the field out (default true).
---@field visible? boolean false hides the field (default true).
---@field bold? boolean Bold caption.
---@field italic? boolean Italic caption.
---@field color? string Caption colour, "#rrggbb" or a name such as "red".
---@field font_size? integer Caption font size in points.
---@field on_click? fun(f: opensak.Form, id: string) button: called on a click; the form stays open (f:close() closes it).
---@field cancel? boolean button: closes the form, which returns nil (also Esc).

---The open form, passed to form callbacks.
---@class opensak.Form
---@field get fun(self: opensak.Form, id: string): any The current value of a field.
---@field set fun(self: opensak.Form, id: string, value: any) Change a field's value (on_change is not called).
---@field values fun(self: opensak.Form): table<string, any> All values, as the form would return them.
---@field set_options fun(self: opensak.Form, id: string, options: (string|number)[]) Replace the options of a choice, radio or checklist field. The value is kept if it is still an option.
---@field set_label fun(self: opensak.Form, id: string, text: string) Change a caption: the label of a field, a label's text, a button's text, a group's title.
---@field set_enabled fun(self: opensak.Form, id: string, on: boolean) Enable or grey out a field.
---@field set_visible fun(self: opensak.Form, id: string, on: boolean) Show or hide a field.
---@field set_title fun(self: opensak.Form, text: string) Change the window title.
---@field focus fun(self: opensak.Form, id: string) Move the keyboard focus to a field.
---@field close fun(self: opensak.Form, id?: string) Close the form: opensak.form() returns the values and *id* (default "ok"). `validate` is not called.
---@field cancel fun(self: opensak.Form) Close the form like Cancel: opensak.form() returns nil.

---The window returned by `opensak.progress()`.
---@class opensak.Progress
---@field update fun(self: opensak.Progress, i?: integer, text?: string): boolean Show step *i* and *text*; false once the user has pressed Cancel.
---@field close fun(self: opensak.Progress) Close the window.

---A cache as returned by `opensak.cache()` and `opensak.caches()` (a snapshot).
---@class opensak.Cache
---@field code string GC code, e.g. "GC12345".
---@field name string Cache name.
---@field type string Cache type, e.g. "Traditional Cache".
---@field container string? Container size, e.g. "Small".
---@field lat number Posted latitude (decimal degrees).
---@field lon number Posted longitude (decimal degrees).
---@field difficulty number? Difficulty 1–5.
---@field terrain number? Terrain 1–5.
---@field owner string? Owner name.
---@field placed_by string? Placed-by name as shown on the listing.
---@field owned boolean Owned by you: the owner matches your geocaching.com account in Settings (GSAK's IsOwner).
---@field hidden string? Hidden date, "YYYY-MM-DD".
---@field found boolean Found by you.
---@field found_date string? Your find date, "YYYY-MM-DD".
---@field dnf boolean Your latest log is a Didn't find it.
---@field dnf_date string? Date of your DNF, "YYYY-MM-DD".
---@field ftf boolean You were first to find.
---@field available boolean Not disabled.
---@field archived boolean Archived.
---@field premium boolean Premium-member only.
---@field country string? Country.
---@field state string? State / region.
---@field county string? County.
---@field distance number? Distance from the active centre point in km.
---@field bearing number? Bearing from the active centre point in degrees.
---@field elevation number? Elevation in metres.
---@field favorite_points integer? Favourite points (nil until known).
---@field find_count integer? Number of Found it logs by anyone.
---@field user_flag boolean User flag.
---@field user_sort integer? User sort value.
---@field user_data string[] User data fields 1–4; "" when empty.
---@field color string? Colour tag, e.g. "#FF5733".
---@field locked boolean Locked against import changes.
---@field watch boolean On the watchlist.
---@field macro_flag boolean Flag for macros' own use, separate from the user flag (GSAK's MacroFlag).
---@field macro_sort string? Text for macros' own use, e.g. to sort by (GSAK's MacroSort).
---@field note string? Your local note.
---@field gc_note string? Personal note from geocaching.com.
---@field hint string? Hint text as imported.
---@field url string? Listing URL.
---@field symbol string? GPX waypoint symbol, e.g. "Geocache Found" (GSAK's Symbol).
---@field corrected {lat: number, lon: number}? Corrected coordinates; nil if the cache is not solved.
---@field corrected_source string? Who corrected the coordinates: "user", "macro" or "import" (from GSAK); nil if not corrected or not known.
---@field waypoint_count integer Number of additional waypoints.
---@field log_count integer Number of logs stored.
---@field trackable_count integer Number of trackables in the cache.
---@field last_log_date string? Date of the latest log, "YYYY-MM-DD".
---@field last_gpx_update string? When an import last touched the cache, "YYYY-MM-DDTHH:MM:SS" in UTC; finds caches the latest Pocket Query did not refresh.
---@field last_user_update string? When a user field (user flag, user sort, user data, lock, note or correction) last changed, "YYYY-MM-DDTHH:MM:SS" in UTC (GSAK's LastUserDate).
---@field custom table<string, any> The cache's custom fields, name → value: a boolean as true/false, numbers as numbers, text and dates ("YYYY-MM-DD") as strings; a field nobody set has its default. Changed with opensak.update(code, { custom = { … } }).
---@field [string] any

---The fields `opensak.update()` may change; `false` clears a field that is not a boolean.
---@class opensak.CacheUpdate
---@field name? string Cache name.
---@field type? string Cache type, e.g. "Traditional Cache".
---@field container? string|false Container size, e.g. "Small".
---@field lat? number Posted latitude (decimal degrees).
---@field lon? number Posted longitude (decimal degrees).
---@field difficulty? number|false Difficulty 1–5.
---@field terrain? number|false Terrain 1–5.
---@field owner? string|false Owner name.
---@field placed_by? string|false Placed-by name as shown on the listing.
---@field hidden? string|false Hidden date, "YYYY-MM-DD".
---@field found? boolean Found by you.
---@field found_date? string|false Your find date, "YYYY-MM-DD".
---@field dnf? boolean Your latest log is a Didn't find it.
---@field dnf_date? string|false Date of your DNF, "YYYY-MM-DD".
---@field ftf? boolean You were first to find.
---@field available? boolean Not disabled.
---@field archived? boolean Archived.
---@field premium? boolean Premium-member only.
---@field country? string|false Country.
---@field state? string|false State / region.
---@field county? string|false County.
---@field elevation? number|false Elevation in metres.
---@field favorite_points? integer|false Favourite points (nil until known).
---@field find_count? integer|false Number of Found it logs by anyone.
---@field user_flag? boolean User flag.
---@field user_sort? integer|false User sort value.
---@field user_data? table<integer, string|false> User data fields 1–4; "" when empty.
---@field color? string|false Colour tag, e.g. "#FF5733".
---@field locked? boolean Locked against import changes.
---@field watch? boolean On the watchlist.
---@field macro_flag? boolean Flag for macros' own use, separate from the user flag (GSAK's MacroFlag).
---@field macro_sort? string|false Text for macros' own use, e.g. to sort by (GSAK's MacroSort).
---@field note? string|false Your local note.
---@field gc_note? string|false Personal note from geocaching.com.
---@field hint? string|false Hint text as imported.
---@field url? string|false Listing URL.
---@field symbol? string|false GPX waypoint symbol, e.g. "Geocache Found" (GSAK's Symbol).
---@field corrected? {lat: number, lon: number}|string|false Corrected coordinates; nil if the cache is not solved.
---@field custom? table<string, any> Custom fields to change, name → value (see Custom fields).

---A new cache for `opensak.insert{}`.
---@class opensak.CacheInsert: opensak.CacheUpdate
---@field code string Cache code, e.g. "GC12345".
---@field name string Cache name.
---@field type string Cache type, e.g. "Traditional Cache".
---@field lat number Posted latitude (decimal degrees).
---@field lon number Posted longitude (decimal degrees).

---A log as returned by `opensak.logs()` (a snapshot).
---@class opensak.Log
---@field id integer Identifies the log, for `opensak.update_log()`.
---@field type string Log type, e.g. "Found it", "Write note".
---@field date string? Log date "YYYY-MM-DDTHH:MM:SS" (a date alone is midnight).
---@field finder string? Name of the logger.
---@field finder_id string? geocaching.com id of the logger.
---@field text string? Log text.
---@field encoded boolean The log text is encoded (ROT13).
---@field lat number? Coordinates of the log (e.g. Earthcache logs).
---@field lon number? Coordinates of the log.
---@field by_owner boolean Logged by the cache owner.

---The fields `opensak.update_log()` may change; `false` clears a field that may be nil.
---@class opensak.LogUpdate
---@field type? string Log type, e.g. "Found it", "Write note".
---@field date? string|false Log date "YYYY-MM-DDTHH:MM:SS" (a date alone is midnight).
---@field finder? string|false Name of the logger.
---@field finder_id? string|false geocaching.com id of the logger.
---@field text? string|false Log text.
---@field encoded? boolean The log text is encoded (ROT13).
---@field lat? number|false Coordinates of the log (e.g. Earthcache logs).
---@field lon? number|false Coordinates of the log.
---@field by_owner? boolean Logged by the cache owner.

---A new log for `opensak.insert_log()`.
---@class opensak.LogInsert: opensak.LogUpdate
---@field type string Log type, e.g. "Found it", "Write note".

---A waypoint as returned by `opensak.waypoints()` (a snapshot).
---@class opensak.Waypoint
---@field id integer Identifies the waypoint, for `opensak.update_waypoint()`.
---@field prefix string Waypoint prefix, e.g. "PK", "S1".
---@field type string Waypoint type, e.g. "Parking Area", "Final Location".
---@field name string? Name.
---@field code string? Waypoint code, unique within the cache.
---@field lat number? Latitude; nil if not known yet.
---@field lon number? Longitude; nil if not known yet.
---@field description string? Description.
---@field comment string? Comment.
---@field url string? URL.
---@field date string? Date "YYYY-MM-DDTHH:MM:SS".
---@field flag boolean User flag of the waypoint.

---The fields `opensak.update_waypoint()` may change; `false` clears a field that may be nil.
---@class opensak.WaypointUpdate
---@field prefix? string Waypoint prefix, e.g. "PK", "S1".
---@field type? string Waypoint type, e.g. "Parking Area", "Final Location".
---@field name? string|false Name.
---@field code? string|false Waypoint code, unique within the cache.
---@field lat? number|false Latitude; nil if not known yet.
---@field lon? number|false Longitude; nil if not known yet.
---@field description? string|false Description.
---@field comment? string|false Comment.
---@field url? string|false URL.
---@field date? string|false Date "YYYY-MM-DDTHH:MM:SS".
---@field flag? boolean User flag of the waypoint.

---A new waypoint for `opensak.insert_waypoint()`.
---@class opensak.WaypointInsert: opensak.WaypointUpdate
---@field prefix string Waypoint prefix, e.g. "PK", "S1".
---@field type string Waypoint type, e.g. "Parking Area", "Final Location".

---A attribute as returned by `opensak.attributes()` (a snapshot).
---@class opensak.Attribute
---@field id integer Identifies the attribute, for `opensak.update_attribute()`.
---@field attribute_id integer Groundspeak attribute id, unique within the cache.
---@field name string? Name, e.g. "Dogs allowed".
---@field on boolean true: yes, false: no (e.g. no dogs).

---The fields `opensak.update_attribute()` may change; `false` clears a field that may be nil.
---@class opensak.AttributeUpdate
---@field attribute_id? integer Groundspeak attribute id, unique within the cache.
---@field name? string|false Name, e.g. "Dogs allowed".
---@field on? boolean true: yes, false: no (e.g. no dogs).

---A new attribute for `opensak.insert_attribute()`.
---@class opensak.AttributeInsert: opensak.AttributeUpdate
---@field attribute_id integer Groundspeak attribute id, unique within the cache.

---A trackable as returned by `opensak.trackables()` (a snapshot).
---@class opensak.Trackable
---@field id integer Identifies the trackable, for `opensak.update_trackable()`.
---@field ref string? Reference, e.g. "TB1234".
---@field name string? Name.
---@field tracking_code string? Tracking code.

---The fields `opensak.update_trackable()` may change; `false` clears a field that may be nil.
---@class opensak.TrackableUpdate
---@field ref? string|false Reference, e.g. "TB1234".
---@field name? string|false Name.
---@field tracking_code? string|false Tracking code.

---A new trackable for `opensak.insert_trackable()`.
---@class opensak.TrackableInsert: opensak.TrackableUpdate

---The OpenSAK API, available as a global in every macro.
opensak = {}

---The API version of this OpenSAK build. Each function lists the version it was added in.
---
---Since API version 1.
---
---```lua
---if opensak.api_version() < 1 then
---    error("this macro needs a newer OpenSAK")
---end
---```
---@return integer # The API version.
function opensak.api_version() end

---Build a filter from the given keys (see Filter keys; combined with AND unless `mode = "OR"`) and apply it. Usually called with table syntax: `opensak.filter{ ... }`. When nothing matches, the view is left unchanged.
---
---Since API version 1. Changed in API version 4: added the keys `last_user_update` and `macro_flag`; `owned` uses the `owned` cache field.
---
---```lua
---local n = opensak.filter{ type = "Traditional", difficulty = {1, 2}, found = false }
---print("Easy unfound traditionals: " .. n)
---```
---@param spec opensak.FilterSpec The filter keys.
---@return integer # Number of matching caches (0 = view unchanged).
function opensak.filter(spec) end

---The name of the active filter: the `label` of opensak.filter{}, a profile name, or "" when no filter is active. The same as the {filter} variable of an export file name.
---
---Since API version 2.
---
---```lua
---print("Filter: " .. opensak.filter_name())
---```
---@return string # The filter name; "" for none.
function opensak.filter_name() end

---Sort the cache list, like a click on a column header. opensak.caches() and opensak.codes() then return the caches in this order. Fields: `name`, `code`, `type`, `container`, `difficulty`, `terrain`, `hidden`, `placed_by`, `country`, `state`, `county`, `found`, `found_date`, `dnf`, `dnf_date`, `ftf`, `archived`, `premium`, `distance`, `bearing`, `favorite_points`, `trackable_count`, `user_flag`, `locked`, `user_sort`, `user_data1`, `user_data2`, `user_data3`, `user_data4`, `macro_sort`. `macro_sort` has no column in the cache list, so no column header shows the sort; it compares as text, so pad numbers ("0042").
---
---Since API version 2. Changed in API version 4: added `macro_sort`.
---
---```lua
---opensak.sort("difficulty", "desc")
---```
---@param field string A cache field, e.g. "difficulty".
---@param direction? "asc"|"desc" "asc" if omitted.
function opensak.sort(field, direction) end

---Apply a saved filter profile.
---
---Since API version 1.
---
---```lua
---local n = opensak.filter_profile("Unfound nearby")
---```
---@param name string Name of the saved profile.
---@return integer # Number of matching caches.
function opensak.filter_profile(name) end

---Remove the active filter, so all caches are shown again.
---
---Since API version 1.
---
---```lua
---opensak.clear_filter()
---```
function opensak.clear_filter() end

---The active filter (with its name, and any quick filter or search box) as a string, to put back later with opensak.restore_filter() — e.g. to leave the user's filter as it was after the macro filtered for its own work. Like GSAK's SaveFilter(). The sort order is not included. The string can be stored with opensak.settings.save(); don't build or change it.
---
---Since API version 3.
---
---```lua
---local saved = opensak.save_filter()
---opensak.filter{ found = false }
----- ... work on the unfound caches ...
---opensak.restore_filter(saved)
---```
---@return string # The saved filter; when no filter is active, restoring it clears the filter.
function opensak.save_filter() end

---Apply a filter saved with opensak.save_filter(), like GSAK's RestoreFilter(). As with opensak.filter{}, the view is left unchanged when nothing matches. A filter saved while none was active clears the filter.
---
---Since API version 3.
---
---```lua
---local saved = opensak.save_filter()
---opensak.filter{ found = false }
---opensak.restore_filter(saved)
---```
---@param saved string A string from opensak.save_filter().
---@return integer # Number of matching caches (0 = view unchanged, unless the saved filter was empty).
function opensak.restore_filter(saved) end

---The number of caches matching the active filter.
---
---Since API version 1.
---
---```lua
---print(opensak.count() .. " caches shown")
---```
---@return integer # Number of caches shown.
function opensak.count() end

---The names of all saved filter profiles.
---
---Since API version 1.
---
---```lua
---for _, name in ipairs(opensak.profiles()) do
---    print(name)
---end
---```
---@return string[] # Profile names.
function opensak.profiles() end

---One cache as a table (see Cache fields). It is a snapshot: changing it changes nothing in the database.
---
---Since API version 2. Changed in API version 4: added the fields `owned`, `macro_flag`, `macro_sort`, `symbol`, `corrected_source` and `last_user_update`.
---
---```lua
---local c = opensak.cache("GC12345")
---if c and c.corrected then print(c.name, c.corrected.lat, c.corrected.lon) end
---local found = opensak.cache("GC12345", { database = "Found" })
---```
---@param code string GC code, e.g. "GC12345".
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return opensak.Cache? # The cache, or nil if it is not in the database.
function opensak.cache(code, options) end

---Iterate over caches, one table per cache (see Cache fields), in a generic `for`. Without arguments: the caches of the active filter, in grid order. With filter keys (see Filter keys): the caches matching them, sorted by name; the view and the active filter stay unchanged. `all = true`: every cache of the database, sorted by name, whatever the active filter shows. `database` reads another database, without switching: its caches matching the filter keys, or all of them, sorted by name. `fields` limits the fields loaded (`code` is always included), which makes loops over many caches faster. Caches are loaded in chunks, so large databases do not hit the memory limit.
---
---Since API version 2. Changed in API version 3: added `all`. Changed in API version 4: added the fields `owned`, `macro_flag`, `macro_sort`, `symbol`, `corrected_source` and `last_user_update`.
---
---```lua
---for c in opensak.caches() do print(c.code, c.name) end
---for c in opensak.caches{ found = true, country = "Switzerland",
---                         fields = {"difficulty", "terrain"} } do
---    print(c.code, c.difficulty, c.terrain)
---end
---for c in opensak.caches{ database = "Found", fields = {"corrected"} } do
---    print(c.code, c.corrected and c.corrected.lat)
---end
---```
---@param spec? opensak.CachesSpec Filter keys, `all`, `database` and/or `fields`; nothing = the active filter.
---@return fun(): opensak.Cache? # Iterator for a generic `for`.
function opensak.caches(spec) end

---The cache selected in the grid.
---
---Since API version 2.
---
---```lua
---local c = opensak.current()
---if c then print(c.code .. " " .. c.name) end
---```
---@return opensak.Cache? # The cache, or nil if no row is selected.
function opensak.current() end

---The GC codes of the rows selected in the grid.
---
---Since API version 2.
---
---```lua
---for _, code in ipairs(opensak.selected()) do print(code) end
---```
---@return string[] # GC codes; empty if nothing is selected.
function opensak.selected() end

---The GC codes of the caches of the active filter, in grid order. Cheaper than opensak.caches() when only the codes are needed.
---
---Since API version 2.
---
---```lua
---print(table.concat(opensak.codes(), ", "))
---```
---@return string[] # GC codes.
function opensak.codes() end

---The listing description of a cache. Not part of the cache table because it can be large.
---
---Since API version 2.
---
---```lua
---local d = opensak.description("GC12345")
---if d and d.long and d.long:find("bonus") then print("bonus cache") end
---```
---@param code string GC code, e.g. "GC12345".
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return {short: string?, long: string?, html: boolean}? # Short and long description and whether they are HTML; nil if the cache is not in the database.
function opensak.description(code, options) end

---Run a read-only SQL query (SQLite) against the active database (or the one named by the `database` option) and return all rows. Only reading statements are allowed, except on TEMP tables: a macro may create, fill, change and drop its own TEMP tables, views and indexes (`CREATE TEMP TABLE ...`) and use them in any query, opensak.sql_write() included. They are never stored in the database and are gone when the macro ends or calls opensak.switch_database(); changes to them inside opensak.transaction() are undone with it. A TEMP table may not take the name of a table of the database. The tables and columns macros may rely on, and the format of their values, are listed in Database tables for macros; opensak.tables() and opensak.columns() list everything there is. Use `AS` to name computed columns. NULL values are nil. At most 100,000 rows; use opensak.sql_each() for more. A query is aborted after 60 s.
---
---Since API version 2. Changed in API version 3: TEMP tables, views and indexes.
---
---```lua
---local rows = opensak.sql(
---  "SELECT country, COUNT(*) AS n FROM caches WHERE found = ? GROUP BY country", { 1 })
---for _, r in ipairs(rows) do print(r.country, r.n) end
---```
---@param query string One SQL statement.
---@param params? table Values for `?` placeholders ({ v1, v2 }) or for `:name` placeholders ({ name = v }); `nil` or `{}` for none.
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return table<string, any>[] # One table per row, keyed by column name.
function opensak.sql(query, params, options) end

---Like opensak.sql(), but returns an iterator for a generic `for` that fetches the rows in chunks — for results of any size. The tables and columns to rely on are listed in Database tables for macros.
---
---Since API version 2.
---
---```lua
---for r in opensak.sql_each("SELECT gc_code, name FROM caches WHERE found = 0") do
---    print(r.gc_code, r.name)
---end
---```
---@param query string One SQL statement.
---@param params? table As for opensak.sql().
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return fun(): table<string, any>? # Iterator for a generic `for`.
function opensak.sql_each(query, params, options) end

---The tables and views of the active database (or the one named by the `database` option), for use with opensak.sql(). The macro's TEMP tables and views are included.
---
---Since API version 2. Changed in API version 3: lists the macro's TEMP tables and views, the tables of opensak.macro_db() as `macro.<name>` and those of opensak.attach() as `<schema>.<name>`. Changed in API version 4: lists the companion database's tables as `companion.<name>`.
---
---```lua
---print(table.concat(opensak.tables(), ", "))
---```
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return string[] # Table and view names, sorted.
function opensak.tables(options) end

---The columns of a table or view of the active database (or the one named by the `database` option): all of them, also those Database tables for macros leaves out, which may change in any version.
---
---Since API version 2. Changed in API version 3: added `required`.
---
---```lua
---for _, c in ipairs(opensak.columns("caches")) do print(c.name, c.type) end
---```
---@param table string Table or view name.
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return {name: string, type: string, required: boolean}[] # Column names and SQL types, in table order; `required`: NOT NULL without a default, so an INSERT must give it.
function opensak.columns(table, options) end

---Attach the macro's own SQLite database — a file named after the macro file with the extension .db3, in the macro's folder, created if missing — to the SQL functions as schema `macro`. In it the macro has full access through opensak.sql(), sql_each() and sql_write(): CREATE, DROP, ALTER, INSERT, UPDATE and DELETE on `macro.<name>` tables, indexes, views and triggers, and `PRAGMA macro.user_version`. Its data stays between runs. One statement can read and write the active database and the macro database together, e.g. `INSERT INTO macro.seen SELECT gc_code FROM caches`; the rules for the active database stay the same. Unqualified names mean the active database first, so create with `macro.` in front. opensak.tables() lists its tables as `macro.<name>`. The macro's folder must be writable for macros, like any file a macro writes (Settings → Folder permissions; the macros folder is by default); otherwise OpenSAK asks the user to allow it, and a denial makes the call fail. The first call creates the file, even if nothing is written to it, and prints its path in the macro output. OpenSAK remembers the file, and its backups include it in `macro_databases/`, wherever it is. If the folder cannot be written the call fails; a read-only file can be read but not changed. Unlike opensak.update() it needs no approval to change databases (see Changing caches). Changes inside opensak.transaction() are kept or undone together with those of the active database. The SQL statement ATTACH itself stays refused. Cannot be called inside opensak.transaction(); stays attached after opensak.switch_database(). The macro must be saved, as the file is named after it. In a macro started with opensak.run_macro() it attaches the called macro's own file instead; the caller's file is attached again when the called macro returns.
---
---Since API version 3.
---
---```lua
---opensak.macro_db()
---opensak.sql("CREATE TABLE IF NOT EXISTS macro.seen (code TEXT PRIMARY KEY, at TEXT)")
---opensak.sql("INSERT OR IGNORE INTO macro.seen SELECT gc_code, date('now') FROM caches WHERE found = 1")
---for _, r in ipairs(opensak.sql("SELECT c.gc_code, s.at FROM caches c "
---        .. "JOIN macro.seen s ON s.code = c.gc_code LIMIT 5")) do
---    print(r.gc_code, r.at)
---end
---```
---@return string # The path of the macro database file.
function opensak.macro_db() end

---Attach another OpenSAK database read-only to the SQL functions, under a schema name, so one opensak.sql() or sql_write() statement can read it together with the active database and opensak.macro_db(): `SELECT … FROM caches c JOIN found.caches f ON f.gc_code = c.gc_code`. Like GSAK's SQL `ATTACH`, which stays refused. Nothing can write to an attached database: the file is opened read-only, and SQL that would change it is refused. Without `schema`, the name is made from the database name: lower case, `_` for anything but letters and digits ("Found 2020" → `found_2020`). The schema name must be letters, digits and `_`; `main`, `temp`, `macro`, `companion` and names starting with `opensak_` are reserved. The active database cannot be attached, and neither can a database twice. Attaching the same database under the same name again does nothing. SQLite allows 10 attached databases at once, opensak.macro_db() included. The database is not updated to the current schema, so one last opened by an older OpenSAK version may lack columns; `opensak.columns("<schema>.caches")` shows what is there. Each attach prints a line in the macro output. Cannot be called inside opensak.transaction(). Stays attached after opensak.switch_database(), unless the database becomes the active one. Everything is detached when the macro ends. A macro started with opensak.run_macro() sees its caller's attached databases; what it attaches or detaches is undone when it returns. The database's companion database (opensak.companion()) is attached with it, read-only, as `<schema>_companion`, if it has one, and detached with it. See Several databases in one query.
---
---Since API version 3. Changed in API version 4: also attaches the database's companion database.
---
---```lua
---local found = opensak.attach("Found 2020", "found")
---for _, r in ipairs(opensak.sql("SELECT c.gc_code, c.name FROM caches c "
---        .. "JOIN found.caches f ON f.gc_code = c.gc_code")) do
---    print(r.gc_code, r.name)
---end
---```
---@param name string Database name, as in opensak.databases().
---@param schema? string Schema name to use in SQL; default: made from the database name.
---@return string # The schema name.
function opensak.attach(name, schema) end

---Detach a database attached with opensak.attach(). Optional: everything is detached when the macro ends. Cannot be called inside opensak.transaction().
---
---Since API version 3.
---
---```lua
---opensak.detach("found")
---```
---@param schema string The schema name opensak.attach() returned.
function opensak.detach(schema) end

---The databases attached with opensak.attach(), in the order they were attached. `opensak` is always true: only OpenSAK databases can be attached so far.
---
---Since API version 3. Changed in API version 4: added `companion`.
---
---```lua
---for _, a in ipairs(opensak.attached()) do print(a.schema, a.name) end
---```
---@return {schema: string, name: string, path: string, opensak: boolean, companion: boolean}[] # One table per attached database. `companion`: it is the companion database of the database attached as `name`.
function opensak.attached() end

---Make sure the active database has a companion database — a second SQLite file next to it, `Found.db` → `Found.companion.db3`, created if missing — attached to the SQL functions as schema `companion`. It is shared by all macros: every macro reads every table in it, and creates, changes and drops its own tables, indexes and views with opensak.sql(), sql_each() and sql_write() (`CREATE TABLE companion.<name> …`). Like opensak.macro_db() this needs no approval to change databases, but opensak.sql_write() still asks for it, as it may change caches. OpenSAK records the macro's file as the owner of every table and view it creates; the tables of other macros, those of OpenSAK (`opensak_*`) and tables without an owner are read-only. A moved or renamed macro is a new owner. A table with a `gc_code` column is cache-linked: its rows belong to caches, several per cache if needed, and follow them — they are deleted with the cache, and copied or moved with it to another database (opensak.set_companion_table() changes that). Triggers are refused. The file follows its database when it is renamed, copied, moved, deleted or backed up. Once it exists, every macro run has it attached without this call. The first call creates the file and prints its path in the macro output; it cannot do that inside opensak.transaction(), and the macro must be saved, as its tables belong to its file. See The companion database.
---
---Since API version 4.
---
---```lua
---opensak.companion()
---opensak.sql("CREATE TABLE IF NOT EXISTS companion.ftf_info "
---    .. "(gc_code TEXT PRIMARY KEY, ftf_date TEXT, ftf_by TEXT)")
---opensak.sql("INSERT OR REPLACE INTO companion.ftf_info VALUES (?, ?, ?)",
---    { "GC12345", "2024-05-01", "someone" })
---```
---@return string # The path of the companion database file.
function opensak.companion() end

---Change how OpenSAK treats one of the macro's own tables or views in the companion database (see opensak.companion()). `linked = false`: the rows stay where they are when caches are deleted, copied or moved, even with a `gc_code` column. `linked = true` or `code_column`: the table is cache-linked by that column (default `gc_code`), which must exist. `description` is shown with the table in OpenSAK. Keys left out stay as they are. Create the table first.
---
---Since API version 4.
---
---```lua
---opensak.set_companion_table{ name = "ftf_info", description = "FTF data from ftf.lua" }
---opensak.set_companion_table{ name = "ftf_options", linked = false }
---```
---@param spec opensak.CompanionTableSpec `name` (required, with or without `companion.`), `linked`, `code_column`, `description`.
---@return opensak.CompanionTable # The table, as opensak.companion_tables() lists it.
function opensak.set_companion_table(spec) end

---The tables and views in the active database's companion database (see opensak.companion()), sorted by name, with their owner. Empty while it has none.
---
---Since API version 4.
---
---```lua
---for _, t in ipairs(opensak.companion_tables()) do
---    print(t.name, t.owner_name, t.kind, t.rows, t.mine and "(mine)" or "")
---end
---```
---@return opensak.CompanionTable[] # One table per table or view.
function opensak.companion_tables() end

---The custom fields of the active database (or of `database`), by position: user-defined fields per cache, as in GSAK, with `name`, `type` ("string", "integer", "real", "date" or "boolean"), `default`, `scope` ("local": this database; "global": every database), `origin` ("gsak" when imported from GSAK, else "opensak") and `expression` (GSAK's display expression, if any). Their values are the `custom` field of a cache. See Custom fields.
---
---Since API version 4.
---
---```lua
---for _, f in ipairs(opensak.custom_fields()) do
---    print(f.name, f.type, f.default, f.scope)
---end
---```
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return opensak.CustomField[] # One table per field; empty while there are none.
function opensak.custom_fields(options) end

---Define a custom field in the active database: `name` (letters, digits and `_`, not a cache column or an SQL keyword), `type`, optionally `default` (shown for every cache nobody set), `scope` ("local", the default, or "global" for every database) and `expression`: SQL the cache list shows instead of the value, e.g. `nullif(Finds, -1)` (filters and macros keep using the value). A field of that name and type already there is returned unchanged, so a macro can define its fields on every run; another type is an error. Needs the user's permission like opensak.update(). See Custom fields.
---
---Since API version 4.
---
---```lua
---opensak.define_custom_field{ name = "Parking", type = "string" }
---opensak.define_custom_field{ name = "Angel", type = "boolean", default = false }
---opensak.update("GC12345", { custom = { Parking = "Lot A", Angel = true } })
---```
---@param field opensak.CustomFieldDefinition The field.
---@return opensak.CustomField # The field, as opensak.custom_fields() lists it.
function opensak.define_custom_field(field) end

---Delete a custom field of the active database; a global one is deleted from every database. Its values are kept until the user removes them in Tools → Custom fields, so defining it again (same name and type) does not bring them back. Needs the user's permission like opensak.update().
---
---Since API version 4.
---
---```lua
---opensak.delete_custom_field("Parking")
---```
---@param name string The field's name (any case).
---@return boolean # false if there is no such field.
function opensak.delete_custom_field(name) end

---All databases in OpenSAK's database list, sorted by name.
---
---Since API version 2.
---
---```lua
---for _, db in ipairs(opensak.databases()) do
---    print(db.name, db.active and "(active)" or "", db.size_mb .. " MB")
---end
---```
---@return {name: string, path: string, active: boolean, size_mb: number}[] # One table per database.
function opensak.databases() end

---The name of the active database.
---
---Since API version 2.
---
---```lua
---print("Working on " .. opensak.database())
---```
---@return string # Database name.
function opensak.database() end

---Whether a database with this name is in the database list (exact, case-sensitive match).
---
---Since API version 2.
---
---```lua
---if not opensak.database_exists("CH_Zurich") then
---    opensak.create_database("CH_Zurich")
---end
---```
---@param name string Database name.
---@return boolean # true if it exists.
function opensak.database_exists(name) end

---Create a new, empty database in the default database folder and add it to the list. The active database does not change. Fails if the name is taken or its file already exists. The macro needs permission to change databases.
---
---Since API version 2.
---
---```lua
---local name = opensak.create_database("CH_Zurich")
---```
---@param name string Name of the new database.
---@return string # The name actually used (surrounding spaces removed).
function opensak.create_database(name) end

---The active centre point, which distances and bearings are measured from. `name` is the saved centre point's name, the label of a centre set from coordinates or a cache, or "" when none was chosen; `saved` tells whether it is one of opensak.centers().
---
---Since API version 3.
---
---```lua
---local c = opensak.center()
---print(c.name, opensak.coords.format(c.lat, c.lon))
---```
---@return {name: string, lat: number, lon: number, saved: boolean} # The active centre point.
function opensak.center() end

---The saved centre points (Settings → User locations, with ★ Home from your geocaching.com home location first), as in the centre drop-down. `filter{ near = { point = name, km = … } }` filters by one.
---
---Since API version 3.
---
---```lua
---for _, p in ipairs(opensak.centers()) do
---    print(p.active and "*" or " ", p.name)
---end
---```
---@return {name: string, lat: number, lon: number, active: boolean}[] # One table per saved centre point.
function opensak.centers() end

---Change the active centre point, like the centre drop-down in the toolbar (GSAK's Centre): a saved centre point by name, or coordinates with an optional name to show. Coordinates are not saved as a user location. Distance and bearing of every cache in the active database are recalculated and stored, so the macro needs permission to change the database, and it cannot be used inside opensak.transaction(). The centre stays when the macro ends.
---
---Since API version 3.
---
---```lua
---opensak.set_center("★ Home")
---local c = opensak.cache("GC12345")
---opensak.set_center(c.lat, c.lon, c.code)
---```
---@param name string A saved centre point (see opensak.centers()).
---@return {name: string, lat: number, lon: number, saved: boolean} # The new centre point, as opensak.center() returns it.
---@overload fun(lat: number, lon: number, name?: string): {name: string, lat: number, lon: number, saved: boolean}
---@overload fun(coords: string, name?: string): {name: string, lat: number, lon: number, saved: boolean}
function opensak.set_center(name) end

---Make another database the active one, as the toolbar dropdown does. The active filter is cleared.
---
---Since API version 2.
---
---```lua
---opensak.switch_database("CH_Zurich")
---print(opensak.count() .. " caches in " .. opensak.database())
---```
---@param name string Database name.
function opensak.switch_database(name) end

---Move caches from the active database to another one, with all logs, waypoints, attributes, trackables and notes (like Database → Move caches). By default the caches of the active filter. When a cache already exists in the target, `if_exists` decides: `"newer"` (default) replaces it only if the active database's copy was imported later, `"replace"` always, `"skip"` never. A cache not written to the target stays in the active database. The macro needs permission to change databases.
---
---Since API version 2.
---
---```lua
---local n = opensak.move_caches("CH_Zurich")
---print(n .. " caches moved")
---```
---@param target string Name of the target database (not the active one).
---@param options? opensak.TransferOptions codes, if_exists.
---@return integer # Number of caches moved.
function opensak.move_caches(target, options) end

---Like opensak.move_caches(), but the caches stay in the active database.
---
---Since API version 2.
---
---```lua
---local n = opensak.copy_caches("CH_Zurich", { codes = {"GC1", "GC2"}, if_exists = "replace" })
---```
---@param target string Name of the target database (not the active one).
---@param options? opensak.TransferOptions codes, if_exists.
---@return integer # Number of caches copied.
function opensak.copy_caches(target, options) end

---Import GSAK databases without any question (like File → Import GSAK database, with the answers given up front) and return what was done. *source* is a GSAK backup .zip, a GSAK folder (the install / data folder with `gsak.db3` and `data/<name>/sqlite.db3`, its `data` folder, or one database's folder), a single `.db3` cache database (just that database), or `gsak.db3` (only user locations and saved filters). A relative path is resolved against the macro file's folder, and environment variables such as `%APPDATA%` are expanded; the folder needs read permission. Each GSAK database goes into the OpenSAK database of the same name, created if missing, unless `target` says otherwise. For an OpenSAK database that exists, `if_exists` decides: `"skip"` (default), `"merge"` (new caches added, existing ones updated, locked caches kept) or `"overwrite"` (emptied first). `filters` and `locations` also import the saved filters and user locations of `gsak.db3`; `true` skips the ones whose name exists when `if_exists` is `"skip"` and replaces them otherwise. Embedded note images are replaced by placeholders without asking. A database that fails is reported and the others are still imported. The summary is printed (unless `quiet`) and written to the log file.
---
---Since API version 3.
---
---```lua
---local r = opensak.import_gsak("C:/Backups/GSAKBackup.zip", {
---  databases = {"Found", "Holidays"}, if_exists = "merge",
---  locations = true })
---for _, db in ipairs(r.databases) do
---  print(db.gsak, db.target, db.action, db.created or 0, db.updated or 0)
---end
---if not r.ok then opensak.exit(1) end
---```
---@param source string GSAK backup .zip, GSAK folder or .db3 file.
---@param options? opensak.GsakImportOptions databases, exclude, target, if_exists, create, filters, locations, quiet.
---@return opensak.GsakImportReport # What was imported, per database.
function opensak.import_gsak(source, options) end

---Import GPX files and Pocket Query zips without any question (like File → Import GPX / PQ zip, then Import) and return what was done. *source* is the name of an import setting saved in that dialog — its files, folders and target database are used — or else the path of a `.gpx` / `.zip` file or of a folder. A folder imports every GPX / zip file directly in it (not in subfolders), looked up at each run, so the folder Pocket Queries are downloaded to can be imported every night. A GPX file's waypoints file next to it is imported with it. A relative path is resolved against the macro file's folder, and environment variables such as `%USERPROFILE%` are expanded; the files need read permission. The caches go into `database` if given, else the setting's database, else the active one. Files and folders of a setting that no longer exist are left out and listed in `missing`; a file that fails is reported and the others are still imported. Cancel stops after the current file. Unlike the dialog, country and state are not looked up afterwards (reverse geocoding). The summary is printed (unless `quiet`) and written to the log file.
---
---Since API version 3.
---
---```lua
---local r = opensak.import_gpx("Nightly PQs")
----- or a folder: opensak.import_gpx("%USERPROFILE%/Downloads/PQ", { database = "PQ" })
---print(r.created .. " new, " .. r.updated .. " updated")
---if not r.ok then opensak.exit(1) end
---```
---@param source string Name of a saved import setting, or a .gpx / .zip file or a folder.
---@param options? opensak.GpxImportOptions database, quiet.
---@return opensak.GpxImportReport # What was imported, per file.
function opensak.import_gpx(source, options) end

---Set corrected coordinates, either as decimal degrees or as one coordinate string in any format OpenSAK understands (DMM, DMS, decimal degrees).
---
---Since API version 1.
---
---```lua
---opensak.set_corrected("GC12345", 47.36872, 8.54093)
---opensak.set_corrected("GC12345", "N47 22.123 E008 32.456")
---```
---@param code string GC code, e.g. "GC12345".
---@param lat number|string Latitude in decimal degrees.
---@param lon number|string Longitude in decimal degrees.
---@return boolean # false if the cache is not in the database.
---@overload fun(code: string, coords: string): boolean
function opensak.set_corrected(code, lat, lon) end

---Remove the corrected coordinates of a cache.
---
---Since API version 1.
---
---```lua
---opensak.clear_corrected("GC12345")
---```
---@param code string GC code, e.g. "GC12345".
---@return boolean # false if the cache is not in the database.
function opensak.clear_corrected(code) end

---Change fields of a cache in the active database. The keys are cache field names (see Changing caches); only the given fields change. `false` clears a field that is not a boolean. Fields OpenSAK maintains itself, such as `code`, `distance` or `log_count`, cannot be written. Locked caches can be changed too: the lock only protects from imports. `custom` changes custom fields: a table of field name → value (see Custom fields). Before a macro's first change, OpenSAK asks the user to allow that macro (see Changing caches).
---
---Since API version 3. Changed in API version 4: `custom` changes custom fields.
---
---```lua
---opensak.update("GC12345", { user_flag = true, user_data = { [2] = "solved" } })
---for c in opensak.caches{ found = true, fields = {"color"} } do
---    if not c.color then opensak.update(c.code, { color = "#00AA00" }) end
---end
---```
---@param code string GC code, e.g. "GC12345".
---@param fields opensak.CacheUpdate The fields to change.
---@return boolean # false if the cache is not in the database.
function opensak.update(code, fields) end

---Set, clear or swap the macro flag (field `macro_flag`) of many caches at once, like GSAK's MACROFLAG: the caches of the active filter, the given GC code(s), or `{ all = true }` for every cache of the active database. The macro flag is the macros' own, separate from the user flag; filter with `filter{ macro_flag = true }`. Needs the user's permission like opensak.update().
---
---Since API version 4.
---
---```lua
---opensak.macro_flag("clear", { all = true })
---for c in opensak.caches{ fields = {"difficulty"} } do
---    if c.difficulty and c.difficulty >= 4 then opensak.macro_flag("set", c.code) end
---end
---opensak.filter{ macro_flag = true }
---```
---@param action "set"|"clear"|"swap" What to do with the flag.
---@param caches? string|string[]|{all: true} A GC code, a list of codes or { all = true }; default: the caches of the active filter.
---@return integer # The number of caches whose flag changed.
function opensak.macro_flag(action, caches) end

---Add a new cache to the active database. `code`, `name`, `type`, `lat` and `lon` are required; any other writable field may be given too (see Changing caches), and `custom` for custom fields. Fails if the code is already in the database. Needs the user's permission like opensak.update().
---
---Since API version 3. Changed in API version 4: `custom` sets custom fields.
---
---```lua
---opensak.insert{ code = "GC12345", name = "My bonus", type = "Unknown",
---                lat = 47.36872, lon = 8.54093, user_flag = true }
---```
---@param fields opensak.CacheInsert The new cache's fields.
---@return string # The cache code as stored (upper case).
function opensak.insert(fields) end

---Delete caches from the active database, each with its logs, waypoints, attributes, trackables and user note, like Waypoint → Delete cache. Takes one GC code, or a list of them, e.g. opensak.codes() for the caches of the active filter. Deleted caches cannot be restored from inside OpenSAK (only from a backup); at the end of the run the macro output says how many caches were deleted. The cache list and map drop them after the macro. Locked caches are deleted too: the lock only protects from imports. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---for c in opensak.caches{ archived = true, fields = {"code"} } do
---    opensak.delete(c.code)
---end
----- all caches of the active filter at once:
---print(opensak.delete(opensak.codes()) .. " caches deleted")
---```
---@param code string GC code, e.g. "GC12345".
---@return boolean|integer # For one code: false if the cache is not in the database. For a list: the number of caches deleted.
---@overload fun(codes: string[]): boolean|integer
function opensak.delete(code) end

---The logs of a cache, each with its `id` for opensak.update_log() and the fields listed under Logs, waypoints, attributes and trackables. Missing data is nil.
---
---Since API version 3.
---
---```lua
---for _, l in ipairs(opensak.logs("GC12345") or {}) do
---    print(l.date, l.type, l.finder)
---end
---```
---@param code string GC code, e.g. "GC12345".
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return opensak.Log[]? # nil if the cache is not in the database.
function opensak.logs(code, options) end

---Add a log to a cache in the active database; `type` required. OpenSAK updates the cache's log counts and dates. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---local id = opensak.insert_log("GC12345", { type = "Write note", date = "2026-10-11",
---                                          finder = "me", text = "Final solved" })
---```
---@param code string GC code, e.g. "GC12345".
---@param fields opensak.LogInsert The new log's fields.
---@return integer? # The new log's id; nil if the cache is not in the database.
function opensak.insert_log(code, fields) end

---Change fields of a log in the active database; only the given fields change, and `false` clears a field that is not required or a boolean. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---for _, l in ipairs(opensak.logs("GC12345")) do
---    if l.type == "Write note" then opensak.update_log(l.id, { text = "checked" }) end
---end
---```
---@param id integer The log's id from opensak.logs().
---@param fields opensak.LogUpdate The fields to change.
---@return boolean # false if there is no log with this id.
function opensak.update_log(id, fields) end

---Delete a log from the active database. OpenSAK updates the cache's log counts and dates. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---for _, l in ipairs(opensak.logs("GC12345")) do
---    if l.type == "Write note" and l.finder == "me" then opensak.delete_log(l.id) end
---end
---```
---@param id integer The log's id from opensak.logs().
---@return boolean # false if there is no log with this id.
function opensak.delete_log(id) end

---The waypoints of a cache, each with its `id` for opensak.update_waypoint() and the fields listed under Logs, waypoints, attributes and trackables. Missing data is nil.
---
---Since API version 3.
---
---```lua
---for _, w in ipairs(opensak.waypoints("GC12345") or {}) do
---    print(w.prefix, w.type, w.lat, w.lon)
---end
---```
---@param code string GC code, e.g. "GC12345".
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return opensak.Waypoint[]? # nil if the cache is not in the database.
function opensak.waypoints(code, options) end

---Add a waypoint to a cache in the active database; `prefix`, `type` required. OpenSAK updates the cache's waypoint count. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---opensak.insert_waypoint("GC12345", { prefix = "FN", type = "Final Location",
---                                     name = "Final", lat = 47.36872, lon = 8.54093 })
---```
---@param code string GC code, e.g. "GC12345".
---@param fields opensak.WaypointInsert The new waypoint's fields.
---@return integer? # The new waypoint's id; nil if the cache is not in the database.
function opensak.insert_waypoint(code, fields) end

---Change fields of a waypoint in the active database; only the given fields change, and `false` clears a field that is not required or a boolean. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---for _, w in ipairs(opensak.waypoints("GC12345")) do
---    if w.prefix == "PK" then opensak.update_waypoint(w.id, { comment = "free parking" }) end
---end
---```
---@param id integer The waypoint's id from opensak.waypoints().
---@param fields opensak.WaypointUpdate The fields to change.
---@return boolean # false if there is no waypoint with this id.
function opensak.update_waypoint(id, fields) end

---Delete a waypoint from the active database. OpenSAK updates the cache's waypoint count. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---for _, w in ipairs(opensak.waypoints("GC12345")) do
---    if w.prefix == "FN" then opensak.delete_waypoint(w.id) end
---end
---```
---@param id integer The waypoint's id from opensak.waypoints().
---@return boolean # false if there is no waypoint with this id.
function opensak.delete_waypoint(id) end

---The attributes of a cache, each with its `id` for opensak.update_attribute() and the fields listed under Logs, waypoints, attributes and trackables. Missing data is nil.
---
---Since API version 3.
---
---```lua
---for _, a in ipairs(opensak.attributes("GC12345") or {}) do
---    print(a.attribute_id, a.name, a.on)
---end
---```
---@param code string GC code, e.g. "GC12345".
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return opensak.Attribute[]? # nil if the cache is not in the database.
function opensak.attributes(code, options) end

---Add a attribute to a cache in the active database; `attribute_id` required. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---opensak.insert_attribute("GC12345", { attribute_id = 1, name = "Dogs", on = false })
---```
---@param code string GC code, e.g. "GC12345".
---@param fields opensak.AttributeInsert The new attribute's fields.
---@return integer? # The new attribute's id; nil if the cache is not in the database.
function opensak.insert_attribute(code, fields) end

---Change fields of a attribute in the active database; only the given fields change, and `false` clears a field that is not required or a boolean. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---for _, a in ipairs(opensak.attributes("GC12345")) do
---    if a.attribute_id == 1 then opensak.update_attribute(a.id, { on = true }) end
---end
---```
---@param id integer The attribute's id from opensak.attributes().
---@param fields opensak.AttributeUpdate The fields to change.
---@return boolean # false if there is no attribute with this id.
function opensak.update_attribute(id, fields) end

---Delete a attribute from the active database. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---for _, a in ipairs(opensak.attributes("GC12345")) do
---    if a.attribute_id == 1 then opensak.delete_attribute(a.id) end
---end
---```
---@param id integer The attribute's id from opensak.attributes().
---@return boolean # false if there is no attribute with this id.
function opensak.delete_attribute(id) end

---The trackables of a cache, each with its `id` for opensak.update_trackable() and the fields listed under Logs, waypoints, attributes and trackables. Missing data is nil.
---
---Since API version 3.
---
---```lua
---for _, t in ipairs(opensak.trackables("GC12345") or {}) do print(t.ref, t.name) end
---```
---@param code string GC code, e.g. "GC12345".
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return opensak.Trackable[]? # nil if the cache is not in the database.
function opensak.trackables(code, options) end

---Add a trackable to a cache in the active database. OpenSAK updates the cache's trackable count. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---opensak.insert_trackable("GC12345", { ref = "TB1234", name = "My travel bug" })
---```
---@param code string GC code, e.g. "GC12345".
---@param fields opensak.TrackableInsert The new trackable's fields.
---@return integer? # The new trackable's id; nil if the cache is not in the database.
function opensak.insert_trackable(code, fields) end

---Change fields of a trackable in the active database; only the given fields change, and `false` clears a field that is not required or a boolean. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---for _, t in ipairs(opensak.trackables("GC12345")) do
---    if t.ref == "TB1234" then opensak.update_trackable(t.id, { name = "Renamed" }) end
---end
---```
---@param id integer The trackable's id from opensak.trackables().
---@param fields opensak.TrackableUpdate The fields to change.
---@return boolean # false if there is no trackable with this id.
function opensak.update_trackable(id, fields) end

---Delete a trackable from the active database. OpenSAK updates the cache's trackable count. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---for _, t in ipairs(opensak.trackables("GC12345")) do
---    if t.ref == "TB1234" then opensak.delete_trackable(t.id) end
---end
---```
---@param id integer The trackable's id from opensak.trackables().
---@return boolean # false if there is no trackable with this id.
function opensak.delete_trackable(id) end

---Run one INSERT, UPDATE or DELETE statement (SQLite) against the active database — the power tool for bulk work, changing many rows with one statement. For a single cache or row, prefer `opensak.update()`, `insert{}`, `delete()`, `insert_<x>()`, `update_<x>()` and `delete_<x>()`, which check each field and need no knowledge of the schema (see Changing caches). Only the cache tables can be changed (`attributes`, `caches`, `logs`, `trackables`, `user_notes`, `waypoints`), and an UPDATE may not set the keys or the columns OpenSAK maintains itself (see Changing caches). Deleting a cache also deletes its logs, waypoints, attributes, trackables and user note, as opensak.delete() does. Locked caches are changed and deleted like any other (add `AND locked = 0` to leave them alone). REPLACE (`INSERT OR REPLACE`, `REPLACE INTO`, …) is refused, as it would delete those too; to update or insert, use `INSERT … ON CONFLICT (gc_code) DO UPDATE SET …`. OpenSAK recalculates distances, counts and log dates of the caches the statement touched — inside opensak.transaction() once at its end (see opensak.resync()). An INSERT only needs the real data: the columns opensak.columns() marks as `required`, e.g. `gc_code`, `name`, `cache_type`, `latitude` and `longitude` for a cache (see Changing caches); every other column gets its default. Each statement is committed on its own (or with the surrounding opensak.transaction()), or not at all on an error. BEGIN, COMMIT and the like are refused; use opensak.transaction(). Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---```lua
---local n = opensak.sql_write(
---  "UPDATE caches SET user_data_1 = ? WHERE country = ? AND found = 0",
---  { "todo", "Switzerland" })
---print(n .. " caches marked")
----- a found log: only cache_id and log_type are required
---opensak.sql_write([[
---  INSERT INTO logs (cache_id, log_type, log_date, finder, text)
---  SELECT id, 'Found it', ?, ?, ? FROM caches WHERE gc_code = ?]],
---  { "2026-10-10 14:30:00.000000", "myUsername", "TFTC", "GC12345" })
----- keep only the 5 newest logs of each cache
---opensak.sql_write([[DELETE FROM logs WHERE id NOT IN (
---  SELECT l2.id FROM logs l2 WHERE l2.cache_id = logs.cache_id
---  ORDER BY l2.log_date DESC LIMIT 5)]])
---```
---@param query string One INSERT, UPDATE or DELETE statement.
---@param params? table As for opensak.sql().
---@return integer # Number of rows inserted, updated or deleted (without the rows deleted along with a cache).
function opensak.sql_write(query, params) end

---Run *fn* as one transaction: every change it makes to the active database (opensak.update, insert, delete, sql_write, set_corrected, clear_corrected and the functions for logs, waypoints, ...) is kept when *fn* returns, and all of it is undone if *fn* raises an error, which opensak.transaction() then raises again. Many changes in one transaction are also faster than the same changes committed one by one. Inside *fn*, opensak.cache, caches, sql and the like see the changes already made, except the columns OpenSAK maintains (counts, log dates, distance), which are recalculated once before the commit or when opensak.resync() is called; everything else in OpenSAK, exports included, sees them only afterwards. A transaction inside another one is undone on its own on an error, and kept with the outer one otherwise. opensak.switch_database, move_caches and copy_caches cannot be used inside. Needs the user's permission like opensak.update(). A macro that ends inside a transaction (e.g. cancelled) keeps none of it.
---
---Since API version 3.
---
---```lua
---local n = opensak.transaction(function()
---    local n = 0
---    for c in opensak.caches{ found = true, fields = {"code"} } do
---        opensak.update(c.code, { user_flag = true })
---        n = n + 1
---    end
---    return n
---end)
---print(n .. " caches flagged")
----- error() inside the function undoes everything it changed:
---local ok, err = pcall(opensak.transaction, function()
---    opensak.update("GC12345", { user_flag = true })
---    error("changed my mind")
---end)
---```
---@param fn fun() The function to run.
---@return any ... # What *fn* returns.
function opensak.transaction(fn) end

---Whether the macro is inside opensak.transaction().
---
---Since API version 3.
---
---```lua
---if not opensak.in_transaction() then print("each change is committed on its own") end
---```
---@return boolean # true inside opensak.transaction().
function opensak.in_transaction() end

---Recalculate now the columns OpenSAK maintains itself (distance and bearing, log, waypoint and trackable counts, log dates) for the caches changed so far in the open opensak.transaction(). Inside a transaction they are recalculated only once, just before the commit, which makes many writes much faster; until then opensak.cache, sql and the like still see the old values. Call resync() when a later read inside the transaction depends on them. Outside a transaction it does nothing, as every write is recalculated before its own commit.
---
---Since API version 3.
---
---```lua
---opensak.transaction(function()
---    for i = 1, 3 do opensak.insert_log("GC12345", { type = "Write note" }) end
---    opensak.resync()
---    print(opensak.cache("GC12345").log_count)
---end)
---```
---@return integer # Number of caches recalculated (0 outside a transaction).
function opensak.resync() end

---Read a CSV file (UTF-8) into an array of rows keyed by the header line. A relative path is resolved against the macro file's folder. If the file's folder has no read permission (Settings → Folder permissions), OpenSAK asks the user to allow it for this run or always. The file may be at most 10 MB.
---
---Since API version 1.
---
---```lua
---for _, row in ipairs(opensak.read_csv("solved.csv")) do
---    opensak.set_corrected(row.code, row.coords)
---end
---```
---@param path string The CSV file.
---@param sep? string Separator character; detected among , ; and tab if omitted.
---@return table<string, string>[] # One table per data row, keyed by header.
function opensak.read_csv(path, sep) end

---Write *text* to a file as UTF-8 and return the file written. A relative path is resolved against the macro file's folder. The folder needs write permission (the OpenSAK temp folder, opensak.temp_dir(), has it by default); for an unapproved one the user is asked first. Only these file types can be written: .txt .csv .tsv .html .htm .svg .md .json. An existing file is replaced, or extended with `append = true`. At most 20 MB.
---
---Since API version 3.
---
---```lua
---local path = opensak.write_text(opensak.temp_dir() .. "/report.html",
---  "<h1>" .. opensak.count() .. " caches</h1>")
---print("written: " .. path)
---```
---@param path string The file to write.
---@param text string The content.
---@param options? {append?: boolean} `append`: add *text* at the end of an existing file.
---@return string # The file written (full path).
function opensak.write_text(path, text, options) end

---Open a file in its default app, e.g. an HTML report in the browser, so a report macro can end by showing its result. A relative path is resolved against the macro file's folder. A file this run wrote (opensak.write_text, opensak.export_*, opensak.copy_file / move_file) or one in opensak.temp_dir() opens directly; for any other file the user is asked first. Only these file types: .html .htm .svg .txt .csv .tsv .md .json .gpx .kml .pdf — never a program, script or shortcut — and no URLs. At most 5 files per run. Needs the user interface.
---
---Since API version 3.
---
---```lua
---local path = opensak.write_text(opensak.temp_dir() .. "/report.html",
---  opensak.html.page{ title = "Report", opensak.html.table(rows) })
---opensak.open(path)
---```
---@param path string The file to open.
function opensak.open(path) end

---Download a web page or file (GSAK's GetUrl), e.g. to check a puzzle answer online or fetch a list before working with it. GET only, over http or https; no cookies, no custom headers, and no user name or password in the URL. Before a macro first contacts a web site (host), the user is asked: this time only, always (for this macro, listed in Settings → Folder permissions) or deny. Each redirect (at most 5) is checked the same way. A host on the local network (a router, a NAS) is asked about separately. geocaching.com, coord.info and Groundspeak's other sites (waymarking.com, wherigo.com, ...) can never be contacted: their terms of use don't allow automated access. List the hosts in the macro's `Network:` header so users see them before running it; the header approves nothing by itself. An HTTP error status (404, 500) is no error: check `info.status`. A failed request (no answer, too large, refused) raises an error, which `pcall` catches. At most 200 requests per run, and at least 1 s between two requests to the same host.
---
---Since API version 3.
---
---Beta: only available while the `lua-macro-net` feature flag is on, as it is in beta builds; check with `if opensak.get_url then`. Each web site needs the user's approval, given per macro (see Web sites).
---
---```lua
---local ok, body, info = pcall(opensak.get_url,
---  "https://example.com/answers.csv", { caption = "Answers" })
---if not ok then
---  opensak.message("Download failed: " .. body)
---elseif info.status == 200 then
---  print(#body .. " characters from " .. info.url)
---end
---```
---@param url string An http: or https: URL.
---@param options? opensak.GetUrlOptions `timeout`: seconds without an answer (1–120, default 30). `caption`: show a progress window with this title. `binary`: return the bytes as they are, without decoding the text. `max_bytes`: the largest body (default 10 MB, at most 50 MB).
---@return string # The body, as text decoded with the charset the server names (UTF-8 if none; invalid bytes replaced) or, with `binary`, as bytes; and `{ status, content_type, url }`, `url` being the address after redirects.
---@return opensak.UrlInfo
function opensak.get_url(url, options) end

---Read a whole text file, of any file type. A relative path is resolved against the macro file's folder. The folder needs read permission; for an unapproved one the user is asked first. UTF-8 unless `encoding` says otherwise (`"utf-8"`, `"latin-1"`, `"cp1252"`); a UTF-8 byte order mark is removed and line endings become `"\n"`. At most 10 MB.
---
---Since API version 3.
---
---```lua
---local text = opensak.read_text("notes.txt")
---for line in text:gmatch("[^\n]+") do
---    print(line)
---end
---```
---@param path string The file to read.
---@param options? {encoding?: string} `encoding`: e.g. `"cp1252"` for a file from an older Windows program.
---@return string # The file's text.
function opensak.read_text(path, options) end

---Whether a file exists (false for a folder). A relative path is resolved against the macro file's folder. The folder needs read permission, like opensak.read_text().
---
---Since API version 3.
---
---```lua
---if not opensak.file_exists(opensak.temp_dir() .. "/report.html") then
---    print("no report yet")
---end
---```
---@param path string The file.
---@return boolean # true if the file exists.
function opensak.file_exists(path) end

---Whether a folder exists. A relative path is resolved against the macro file's folder. The folder containing it needs read permission.
---
---Since API version 3.
---
---```lua
---if opensak.folder_exists("C:/GPX") then print("found") end
---```
---@param path string The folder.
---@return boolean # true if the folder exists.
function opensak.folder_exists(path) end

---The files and folders in a folder (not in its subfolders), sorted by name. A relative path is resolved against the macro file's folder. The folder needs read permission; for an unapproved one the user is asked first. `pattern` keeps the names that match it, ignoring case: `*` stands for any text, `?` for one character. At most 10,000 entries. Each entry has `name`, `path` (full path), `dir` (true for a folder), `size` (bytes, 0 for a folder) and `modified` (seconds since the epoch, like `os.time()`).
---
---Since API version 3.
---
---```lua
---for _, f in ipairs(opensak.list_dir("C:/GPX", "*.gpx")) do
---    print(f.name, f.size, os.date("%Y-%m-%d", f.modified))
---end
---```
---@param folder string The folder.
---@param pattern? string A file name pattern, e.g. "*.gpx".
---@return {name: string, path: string, dir: boolean, size: integer, modified: integer}[] # One table per entry.
function opensak.list_dir(folder, pattern) end

---Size and modification time of one file or folder, as in opensak.list_dir(); nil if it does not exist. A relative path is resolved against the macro file's folder. The folder needs read permission.
---
---Since API version 3.
---
---```lua
---local f = opensak.file_info("C:/GPX/pq.zip")
---if f and os.time() - f.modified < 86400 then print("downloaded today") end
---```
---@param path string The file or folder.
---@return {name: string, path: string, dir: boolean, size: integer, modified: integer}? # nil if there is nothing at *path*.
function opensak.file_info(path) end

---Copy a file (GSAK's FileCopy) and return the file written. *target* is a file, or a folder (existing, or ending in `/`) to copy into under the same name. Relative paths are resolved against the macro file's folder. The source's folder needs read permission and the target's folder write permission; for an unapproved one the user is asked first. Only these file types can be written: .txt .csv .tsv .html .htm .svg .md .json .gpx .loc .kml .kmz .ggz .zip .xml .log .jpg .jpeg .png .gif .webp .pdf — no programs or scripts. An existing target is replaced, the same way opensak.write_text() replaces a file, unless `if_exists = "skip"`. The modification time is kept. At most 1 GB.
---
---Since API version 3.
---
---```lua
---local backup = opensak.copy_file("C:/GPX/pq.zip", opensak.temp_dir() .. "/")
---print("copied to " .. backup)
---```
---@param source string The file to copy.
---@param target string The new file, or the folder to copy into.
---@param options? {if_exists?: "overwrite"|"skip"} `if_exists`: `"overwrite"` (default) or `"skip"`.
---@return string? # The file written (full path); nil if skipped.
function opensak.copy_file(source, target, options) end

---Move or rename a file: like opensak.copy_file(), then the source is deleted, so its folder needs write permission too and it must be one of the file types listed there. Nothing is copied when the source may not be deleted.
---
---Since API version 3.
---
---```lua
---for _, f in ipairs(opensak.list_dir("C:/Downloads", "*.zip")) do
---    opensak.move_file(f.path, "C:/GPX/done/")
---end
---```
---@param source string The file to move.
---@param target string The new file, or the folder to move into.
---@param options? {if_exists?: "overwrite"|"skip"} `if_exists`: `"overwrite"` (default) or `"skip"`.
---@return string? # The new file (full path); nil if skipped (the source is then kept).
function opensak.move_file(source, target, options) end

---Delete a file (GSAK's FileErase). A relative path is resolved against the macro file's folder. Its folder needs write permission; for an unapproved one the user is asked first. Only the file types opensak.copy_file() writes can be deleted, never a folder, a link or OpenSAK's own files.
---
---Since API version 3.
---
---```lua
---for _, f in ipairs(opensak.list_dir(opensak.temp_dir(), "*.gpx")) do
---    opensak.delete_file(f.path)
---end
---```
---@param path string The file.
---@return boolean # false if the file did not exist.
function opensak.delete_file(path) end

---Create a folder, with any missing folders above it, and return it. A relative path is resolved against the macro file's folder. The folder it goes into needs write permission; for an unapproved one the user is asked first. A folder that exists already is fine.
---
---Since API version 3.
---
---```lua
---local out = opensak.create_folder(opensak.temp_dir() .. "/" .. os.date("%Y-%m-%d"))
---```
---@param path string The folder.
---@return string # The folder (full path).
function opensak.create_folder(path) end

---Delete an empty folder; a folder with anything in it is an error, so delete its files first. The folder it is in needs write permission. A folder listed in Settings → Folder permissions is never deleted.
---
---Since API version 3.
---
---```lua
---opensak.delete_folder(opensak.temp_dir() .. "/old")
---```
---@param path string The folder.
---@return boolean # false if the folder did not exist.
function opensak.delete_folder(path) end

---Export the caches of the active filter with a saved export setting (File → Export → GPX/LOC/GGZ: format, folder, file name, if the file exists, corrected coordinates, max. caches). The file name variables are filled in as in the dialog, {filter} with the name of the active filter and {center} with the active centre point. The file goes into *folder* if given, else into the setting's folder. That folder needs write permission (Settings → Folder permissions); for an unapproved one the user is asked first. Nothing is written when no cache with coordinates is shown, or when the file exists and the setting says skip (or ask, and the user answers No).
---
---Since API version 2.
---
---```lua
---local path, n = opensak.export_file("GPX Export")
---if path then print(n .. " caches → " .. path) end
---```
---@param setting string Name of the saved export setting.
---@param folder? string Folder to write to instead of the setting's folder, e.g. opensak.temp_dir().
---@return string? # The file written and the number of caches in it; nil if nothing was written.
---@return integer?
function opensak.export_file(setting, folder) end

---Export the caches of the active filter without a saved export setting. `path` is the file to write; its name may use the variables of the export dialog ({database}, {filter}, {center}, {date}, {count}, ...), and a relative path is resolved against the macro file's folder. Its folder needs write permission, like opensak.export_file(). `rename(c)` and `description(c)` are called with each cache table (see Cache fields) and return the name and the waypoint description to write (nil keeps the default). With `target = "device"`, the file goes into the GPX or GGZ folder of the connected Garmin device instead (`path` is then only the file name; MTP devices are not supported yet). Nothing is written when no cache with coordinates is shown, or when the file exists and `if_exists` says skip (or ask, and the user answers No).
---
---Since API version 2.
---
---```lua
---local path, n = opensak.export_gpx{
---  path = opensak.temp_dir() .. "/{database}_{filter}.gpx",
---  rename = function(c) return c.difficulty .. "/" .. c.terrain .. " " .. c.name end,
---  pois = { child_waypoints = false },
---}
---if path then print(n .. " caches → " .. path) end
---```
---@param spec opensak.ExportSpec What to export and where.
---@return string? # The file written and the number of caches in it; nil if nothing was written.
---@return integer?
function opensak.export_gpx(spec) end

---Export the caches of the active filter and/or their child waypoints as Garmin POI files (.gpi) with a saved POI export setting (File → Export Garmin POI: folder, file name, if the file exists, which points, name/description/extra templates, category, proximity alert, icon, max. points). The file name variables are filled in as in the dialog. With "one file per waypoint type" a file is written for the caches and one for every waypoint type. The files go into *folder* if given, else into the setting's folder, which needs write permission (Settings → Folder permissions); for an unapproved one the user is asked first. An existing file is skipped when the setting says skip (or ask, and the user answers No).
---
---Since API version 3.
---
---```lua
---local files, n = opensak.export_poi("Garmin POI")
---if files then print(n .. " POIs → " .. table.concat(files, ", ")) end
---```
---@param setting string Name of the saved POI export setting.
---@param folder? string Folder to write to instead of the setting's folder, e.g. the device's Garmin/POI folder.
---@return string[]? # The files written and the number of POIs in them; nil if nothing was written.
---@return integer?
function opensak.export_poi(setting, folder) end

---Ask the user a Yes/No question.
---
---Since API version 1.
---
---```lua
---if not opensak.confirm("Update 12 caches?") then return end
---```
---@param message string The question.
---@return boolean # true on Yes.
function opensak.confirm(message) end

---Let the user pick a file in a file dialog. The picked file may be used for the rest of this run without a folder permission: read with mode "open" (the default), written with mode "save". OpenSAK's own settings and database files cannot be picked. The dialog starts in the macro file's folder.
---
---Since API version 2.
---
---```lua
---local path = opensak.choose_file("Solved puzzles", "CSV files (*.csv)")
---if not path then return end      -- cancelled
---for _, row in ipairs(opensak.read_csv(path)) do
---    opensak.set_corrected(row.code, row.coords)
---end
---```
---@param title? string Dialog title.
---@param filter? string File types, e.g. "CSV files (*.csv);;All files (*)".
---@param mode? "open"|"save" "open" picks an existing file to read (default), "save" a file to write (the dialog asks before replacing an existing one).
---@return string? # Full path of the picked file, or nil if cancelled.
function opensak.choose_file(title, filter, mode) end

---Let the user pick a folder in a folder dialog. The macro may then read and write files in it for the rest of this run, without a folder permission. OpenSAK's own folders and whole drives cannot be picked.
---
---Since API version 3.
---
---```lua
---local folder = opensak.choose_folder("Export to")
---if not folder then return end      -- cancelled
---opensak.export_gpx{ path = folder .. "/{filter}.gpx" }
---```
---@param title? string Dialog title.
---@return string? # Full path of the folder, or nil if cancelled.
function opensak.choose_folder(title) end

---Show a message with an OK button and wait until the user closes it.
---
---Since API version 3.
---
---```lua
---opensak.message("Done: 12 caches updated")
---```
---@param text string The message.
---@param title? string Window title.
function opensak.message(text, title) end

---Ask for one line of text.
---
---Since API version 3.
---
---```lua
---local name = opensak.input("Name of the new database", "CH_Zurich")
---if not name then return end      -- cancelled
---```
---@param prompt string The question.
---@param default? string Text the field starts with.
---@param title? string Window title.
---@return string? # The text entered, or nil if cancelled.
function opensak.input(prompt, default, title) end

---Let the user pick one item of a list.
---
---Since API version 3.
---
---```lua
---local fmt = opensak.choose("Export format", {"GPX", "GGZ", "KML"}, "GPX")
---if not fmt then return end      -- cancelled
---```
---@param prompt string The question.
---@param options (string|number)[] The items.
---@param default? string|number The item selected at first.
---@param title? string Window title.
---@return string|number? # The item picked, or nil if cancelled.
function opensak.choose(prompt, options, default, title) end

---Show a dialog built from a table (see Forms) and wait until it is closed. Fields with a value come back in a table keyed by their `id`. Callbacks such as `on_change` run while the form stays open and can change it through the handle they get. Without `buttons` the form has OK and Cancel. Tools → Form designer in the macro window builds forms visually and imports GSAK forms.
---
---Since API version 3. Changed in API version 4: with `remember = true`, `on_change` is called for each field that gets a remembered value when the form opens (#1197).
---
---```lua
---local v, button = opensak.form{
---  title = "Export settings",
---  remember = true,
---  fields = {
---    { type = "folder",   id = "folder",  label = "Output folder", required = true },
---    { type = "number",   id = "maxdist", label = "Max distance", default = 25,
---      min = 0, suffix = " km" },
---    { type = "checkbox", id = "unfound", label = "Unfound only", default = true },
---    { type = "choice",   id = "fmt",     label = "Format", options = {"GPX", "GGZ"},
---      on_change = function(f, value)
---        f:set_enabled("unfound", value == "GPX")
---      end },
---  },
---}
---if not v then return end      -- cancelled
---print(v.folder, v.maxdist, v.unfound, v.fmt)
---```
---@param spec opensak.FormSpec The form.
---@return table<string, any>? # The values by field id and the id of the button (or `exit_on_change` field, or "timeout") that closed the form; nil if it was cancelled.
---@return string?
function opensak.form(spec) end

---Open a progress window with a Cancel button, for long loops. `p:update(i [, text])` moves the bar to *i* of *total* and returns false once the user has pressed Cancel; `p:close()` closes the window (also done when the macro ends). Without *total* the bar just shows that work is going on.
---
---Since API version 3.
---
---```lua
---local codes = opensak.codes()
---local p = opensak.progress("Checking caches", #codes)
---for i, code in ipairs(codes) do
---    if not p:update(i, code) then break end      -- Cancel pressed
---    -- … work on the cache …
---end
---p:close()
---```
---@param title string What is being done.
---@param total? integer Number of steps.
---@return opensak.Progress # The window.
function opensak.progress(title, total) end

---Select a cache in the cache list and show it in the detail panel and on the map, like a click on its row.
---
---Since API version 3.
---
---```lua
---if not opensak.select("GC12345") then print("not in the current list") end
---```
---@param code string GC code, e.g. "GC12345".
---@return boolean # false if the cache is not among the caches of the active filter.
function opensak.select(code) end

---Redraw the cache list, detail panel and map now. Changes a macro makes are otherwise shown when it ends.
---
---Since API version 3.
---
---```lua
---opensak.set_corrected("GC12345", 47.36872, 8.54093)
---opensak.refresh()
---```
function opensak.refresh() end

---OpenSAK's folder inside the system temp folder (read and write permission by default), without a trailing separator. "/" works as separator on every platform.
---
---Since API version 1.
---
---```lua
---local rows = opensak.read_csv(opensak.temp_dir() .. "/solved.csv")
---```
---@return string # Folder path.
function opensak.temp_dir() end

---OpenSAK's macros folder (read permission by default), without a trailing separator.
---
---Since API version 1.
---
---```lua
---local rows = opensak.read_csv(opensak.macros_dir() .. "/data/solved.csv")
---```
---@return string # Folder path.
function opensak.macros_dir() end

---All cache types OpenSAK knows, with GSAK's one-letter code where GSAK has one, e.g. for a form's choice list or `filter{ type = … }`.
---
---Since API version 3.
---
---```lua
---for _, t in ipairs(opensak.cache_types()) do print(t.code or "-", t.name) end
---```
---@return {name: string, code: string?}[] # One table per cache type.
function opensak.cache_types() end

---Convert a cache type between GSAK's one-letter code and its name, like GSAK's CacheType(): *value* is a letter ("T"), a full name ("Traditional Cache") or a short one ("Traditional"), in any case.
---
---Since API version 3.
---
---```lua
---local name, code = opensak.cache_type("U")  -- "Unknown Cache", "U"
---print(select(2, opensak.cache_type("multi-cache")))  -- M
---```
---@param value string A GSAK letter or a cache type name.
---@return string? # The cache type and its GSAK letter (nil if it has none); nil if the type is unknown.
---@return string?
function opensak.cache_type(value) end

---The child waypoint types, as in the waypoint dialog, each with its usual prefix — e.g. for opensak.insert_waypoint().
---
---Since API version 3.
---
---```lua
---for _, t in ipairs(opensak.waypoint_types()) do print(t.prefix, t.name) end
---```
---@return {name: string, prefix: string}[] # One table per waypoint type.
function opensak.waypoint_types() end

---The waypoint type for a type name (any case) or a waypoint prefix ("PK", "S1", "F"), with its usual prefix (GSAK's ChildType).
---
---Since API version 3.
---
---```lua
---local name, prefix = opensak.waypoint_type("PK")  -- "Parking Area", "PK"
---```
---@param value string A waypoint type or prefix.
---@return string? # The waypoint type and its usual prefix; nil if it is unknown.
---@return string?
function opensak.waypoint_type(value) end

---The OpenSAK version this macro runs in.
---
---Since API version 2.
---
---```lua
---print("Running in OpenSAK " .. opensak.version())
---```
---@return string # Version, e.g. "1.21.0-beta.3".
function opensak.version() end

---Run another macro and wait until it has finished, e.g. to chain several macros from one control macro. A relative path is looked up in the folder of the calling macro, and `.lua` is added when the name has no extension. The called macro gets its own global variables; values are passed as extra arguments (read them with `...`) and come back as its return values. An error in it stops the calling macro too, unless caught with `pcall`. Macros run this way share the run's limits, sleep time and folder approvals; they may call each other at most 10 levels deep.
---
---Since API version 3.
---
---```lua
---opensak.run_macro("refresh_corrected")
---local count = opensak.run_macro("lib/count_unfound.lua", "Zürich")
----- lib/count_unfound.lua:
-----   local area = ...
-----   return opensak.filter{ found = false, state = area }
---```
---@param file string The macro file.
---@param ... any Passed to the macro as `...`.
---@return any ... # What the macro returns.
function opensak.run_macro(file, ...) end

---Pause the macro. One pause lasts at most 10 s and all pauses of a run together at most 60 s; a cancelled macro stops at its next pause.
---
---Since API version 2.
---
---```lua
---opensak.sleep(500)
---```
---@param ms number Milliseconds.
function opensak.sleep(ms) end

---Write a message to OpenSAK's log file and the macro output. Run with `opensak --run-macro`, it goes to stderr while print() goes to stdout.
---
---Since API version 3.
---
---```lua
---opensak.log("checked " .. opensak.count() .. " caches")
---```
---@param ... any Values, joined with tabs like print().
function opensak.log(...) end

---End the macro right away. pcall cannot catch it, and it is not an error: the macro window shows the code, and `opensak --run-macro` exits with it.
---
---Since API version 3.
---
---```lua
---if opensak.count() == 0 then
---    opensak.log("nothing to do")
---    opensak.exit(1)
---end
---```
---@param code? integer Exit code 0-255 (default 0).
function opensak.exit(code) end

opensak.coords = {}

---Parse a coordinate string. Without `fmt` the format is detected: latitude/longitude (DMM, DMS, decimal degrees), UTM, MGRS, British grid letters ("TQ 30268 79642"), Plus Codes, Maidenhead locators, geohashes (5+ characters mixing letters and digits) and the Swiss, Dutch, Swedish and German grids as number pairs, which are only accepted inside their country. Pass `fmt` to read one format only — needed for numeric British grid positions, short geohashes and to rule out a wrong guess. Formats: `"latlon"`, `"utm"`, `"mgrs"`, `"ch1903"`, `"rd"`, `"osgb"`, `"sweref99"`, `"gk"`, `"olc"`, `"geohash"`, `"maidenhead"` (`"latlon"` covers DD, DMM and DMS; every name `opensak.coords.format` takes works too).
---
---Since API version 2. Changed in API version 3: added `fmt`, the third return value and the grid, code and locator formats.
---
---```lua
---local lat, lon = opensak.coords.parse("N47 22.123 E008 32.456")
---if not lat then error("not a coordinate") end
---local lat2, lon2, fmt = opensak.coords.parse("32T MT 65339 46242")
---print(fmt)  -- mgrs
---print(opensak.coords.parse("530268 179642", "osgb"))
---```
---@param text string The coordinates.
---@param fmt? string Read only this format; detect it if omitted.
---@return number? # Latitude, longitude and the format that was read, or nil if the text cannot be parsed.
---@return number?
---@return string?
function opensak.coords.parse(text, fmt) end

---Format coordinates. Formats: `"dmm"` (default), `"dms"`, `"dd"`, `"utm"`, `"mgrs"`, `"ch1903"` (Swiss LV03), `"ch1903+"` (Swiss LV95), `"rd"` (Dutch RD), `"osgb"` (British National Grid), `"sweref99"` (SWEREF 99 TM), `"gk"` (German Gauss-Krüger), `"olc"` (Plus Code), `"geohash"` and `"maidenhead"`. Aliases: `"lv03"`, `"lv95"`, `"bng"`, `"sweref"`, `"gauss-krueger"`, `"pluscode"`, `"qth"`. The national grids are only meaningful in and around their country; `"osgb"` and `"utm"` raise an error where they are not defined.
---
---Since API version 2. Changed in API version 3: added `"mgrs"`, `"rd"`, `"osgb"`, `"sweref99"`, `"gk"`, `"olc"`, `"geohash"`, `"maidenhead"` and the aliases.
---
---```lua
---print(opensak.coords.format(47.36872, 8.54093, "utm"))
---print(opensak.coords.format("N47 22.123 E008 32.456", "ch1903"))
---print(opensak.coords.format(51.50072, -0.12462, "osgb"))
---```
---@param lat number Latitude in decimal degrees.
---@param lon number Longitude in decimal degrees.
---@param fmt? string Output format, "dmm" if omitted.
---@return string # E.g. "N47 22.123  E008 32.456" or "32T E 465123 N 5247123".
---@overload fun(coords: string, fmt?: string): string
function opensak.coords.format(lat, lon, fmt) end

---Great-circle distance between two points.
---
---Since API version 2.
---
---```lua
---local km = opensak.coords.distance("N47 22.123 E008 32.456",
---                                   "N47 23.000 E008 33.000")
---```
---@param lat1 number Latitude of the first point.
---@param lon1 number Longitude of the first point.
---@param lat2 number Latitude of the second point.
---@param lon2 number Longitude of the second point.
---@return number # Distance in km.
---@overload fun(a: string, b: string): number
function opensak.coords.distance(lat1, lon1, lat2, lon2) end

---Initial bearing from the first point to the second.
---
---Since API version 2.
---
---```lua
---local deg = opensak.coords.bearing(47.36872, 8.54093, 47.38333, 8.55)
---```
---@param lat1 number Latitude of the first point.
---@param lon1 number Longitude of the first point.
---@param lat2 number Latitude of the second point.
---@param lon2 number Longitude of the second point.
---@return number # Degrees, 0 = North, clockwise.
---@overload fun(a: string, b: string): number
function opensak.coords.bearing(lat1, lon1, lat2, lon2) end

---Waypoint projection: the point a given distance away in a given direction.
---
---Since API version 2.
---
---```lua
---local lat, lon = opensak.coords.project("N47 22.123 E008 32.456", 45, 0.25)
---print(opensak.coords.format(lat, lon))
---```
---@param lat number Latitude in decimal degrees.
---@param lon number Longitude in decimal degrees.
---@param bearing number Direction in degrees, 0 = North, clockwise.
---@param km number Distance in km.
---@return number # Latitude and longitude of the projected point.
---@return number
---@overload fun(coords: string, bearing: number, km: number): number, number
function opensak.coords.project(lat, lon, bearing, km) end

---The point halfway between two points (along the great circle).
---
---Since API version 2.
---
---```lua
---local lat, lon = opensak.coords.midpoint(47.0, 8.0, 48.0, 9.0)
---```
---@param lat1 number Latitude of the first point.
---@param lon1 number Longitude of the first point.
---@param lat2 number Latitude of the second point.
---@param lon2 number Longitude of the second point.
---@return number # Latitude and longitude of the midpoint.
---@return number
---@overload fun(a: string, b: string): number, number
function opensak.coords.midpoint(lat1, lon1, lat2, lon2) end

---Whether a point lies inside a polygon. The polygon is either a file (GPX track/route/waypoints, KML, or a text file with one coordinate per line; read permission needed, relative paths are resolved against the macro file's folder) or a table of points, each a coordinate string, `{lat, lon}` or `{lat = ..., lon = ...}`. Edges are straight lines in latitude/longitude, as in the line/polygon filter.
---
---Since API version 2.
---
---```lua
---local area = { "N47 20 E008 30", "N47 25 E008 30", "N47 25 E008 40" }
---print(opensak.coords.inside(47.37, 8.54, area))
---```
---@param lat number Latitude in decimal degrees.
---@param lon number Longitude in decimal degrees.
---@param polygon string|table Polygon file path, or a table of points.
---@return boolean # true if the point is inside.
---@overload fun(coords: string, polygon: string|table): boolean
function opensak.coords.inside(lat, lon, polygon) end

---Offline reverse geocoding with the boundary data used by Update location. A field is nil where no region matches.
---
---Since API version 2.
---
---```lua
---local loc = opensak.coords.location(47.36872, 8.54093)
---if loc then print(loc.country, loc.state, loc.county) end
---```
---@param lat number Latitude in decimal degrees.
---@param lon number Longitude in decimal degrees.
---@return {country: string?, state: string?, county: string?}? # nil if the boundary data is not installed.
---@overload fun(coords: string): {country: string?, state: string?, county: string?}?
function opensak.coords.location(lat, lon) end

opensak.re = {}

---Search for the first match of a regular expression (Python syntax). Each call may run at most 2 s.
---
---Since API version 2.
---
---```lua
---local whole, n, e = opensak.re.find(desc, [[N\s*(\d+)\D+E\s*(\d+)]])
---```
---@param text string The text.
---@param pattern string Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.
---@return string? # The whole match followed by the captures (nil for a group that did not take part), or nil if there is no match.
---@return string? ...
function opensak.re.find(text, pattern) end

---Whether the text contains a match. Use `^` and `$` to match the whole text.
---
---Since API version 2.
---
---```lua
---if opensak.re.match(c.name, [[(?i)^bonus]]) then print("bonus cache") end
---```
---@param text string The text.
---@param pattern string Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.
---@return boolean # true if the pattern matches.
function opensak.re.match(text, pattern) end

---All non-overlapping matches. Without capture groups each item is the whole match, with one group it is the capture, with several it is an array of the captures.
---
---Since API version 2.
---
---```lua
---for _, number in ipairs(opensak.re.findall("A=3, B=12", [[\d+]])) do
---    print(number)
---end
---```
---@param text string The text.
---@param pattern string Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.
---@return string[]|string[][] # The matches.
function opensak.re.findall(text, pattern) end

---Replace matches. In a replacement string, `\1` or `\g<name>` insert a capture. A replacement function gets the whole match and the captures and returns the new text (nil or false keeps the match).
---
---Since API version 2.
---
---```lua
---local text = opensak.re.replace("A=3 B=12", [[\d+]], function(n)
---    return n * 2
---end)
---```
---@param text string The text.
---@param pattern string Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.
---@param repl string|fun(match: string, ...: string?): any Replacement text or function.
---@param count? integer Replace at most this many matches; all if omitted.
---@return string # The new text and the number of replacements.
---@return integer
function opensak.re.replace(text, pattern, repl, count) end

---Split the text at every non-empty match.
---
---Since API version 2.
---
---```lua
---local parts = opensak.re.split("a, b;c", [[[,;]\s*]])  -- {"a", "b", "c"}
---```
---@param text string The text.
---@param pattern string Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.
---@return string[] # The pieces between the matches.
function opensak.re.split(text, pattern) end

---Escape every character that has a meaning in a regular expression, so the text matches literally (GSAK's RegExEscape), e.g. to search for a cache name in a pattern.
---
---Since API version 3.
---
---```lua
---local pattern = "^" .. opensak.re.escape("Bonus (1/3)") .. "$"
---print(opensak.re.match("Bonus (1/3)", pattern))  -- true
---```
---@param text string The text.
---@return string # The escaped text.
function opensak.re.escape(text) end

opensak.text = {}

---Turn HTML (e.g. a cache description) into plain text: tags removed, entities decoded, block elements and `<br>` become line breaks.
---
---Since API version 2.
---
---```lua
---print(opensak.text.html_to_text("<p>Stage&nbsp;1:<br>N47 22.123</p>"))
---```
---@param html string The HTML.
---@return string # The text.
function opensak.text.html_to_text(html) end

---ROT13 as used for hints. Text in [square brackets] stays unchanged, like on geocaching.com.
---
---Since API version 2.
---
---```lua
---print(opensak.text.rot13("haqre gur fgbar [Ubhfr]"))
---```
---@param text string The text.
---@return string # The decoded (or encoded) text.
function opensak.text.rot13(text) end

---Cross sum: the sum of all digits; other characters are ignored.
---
---Since API version 2.
---
---```lua
---print(opensak.text.digit_sum(1987))  -- 25
---```
---@param n number|string The number or text.
---@return integer # Sum of the digits.
function opensak.text.digit_sum(n) end

---Letter value sum (A=1 … Z=26). Accents are dropped (Ä counts as A); other characters are ignored.
---
---Since API version 2.
---
---```lua
---print(opensak.text.word_value("Geocache"))  -- 47
---```
---@param text string The text.
---@return integer # Sum of the letter values.
function opensak.text.word_value(text) end

---Make a string safe as a database or file name on every platform: characters such as `\ / : * ? " < > |` become `_`, white space is collapsed, leading/trailing dots and spaces are removed and the length is limited to 100. Never empty.
---
---Since API version 2.
---
---```lua
---local name = opensak.text.normalize_name("CH: Zürich / Nord")  -- "CH_ Zürich _ Nord"
---```
---@param s string The name.
---@return string # The safe name.
function opensak.text.normalize_name(s) end

---Upper case for all letters, not only ASCII: `string.upper` and `s:upper()` leave ä, é, ø … as they are. ß becomes SS.
---
---Since API version 3.
---
---```lua
---print(opensak.text.upper("zürich"))  -- ZÜRICH; ("zürich"):upper() gives ZüRICH
---```
---@param text string The text.
---@return string # The text in upper case.
function opensak.text.upper(text) end

---Lower case for all letters, not only ASCII (see opensak.text.upper()).
---
---Since API version 3.
---
---```lua
---if opensak.text.lower(c.country) == "österreich" then print(c.code) end
---```
---@param text string The text.
---@return string # The text in lower case.
function opensak.text.lower(text) end

---Remove white space at the start and end, including line breaks and Unicode spaces such as the no-break space that `%s` in a Lua pattern does not match (GSAK's Alltrim).
---
---Since API version 3.
---
---```lua
---local name = opensak.text.trim("  Zürich\u{00A0}")  -- "Zürich"
---```
---@param text string The text.
---@return string # The trimmed text.
function opensak.text.trim(text) end

---Like `string.sub`, but counting characters instead of bytes, so it never cuts an ü or é in half (GSAK's Left, Right and SubStr). Positions start at 1; negative ones count from the end; both ends are included. `utf8.len(s)` gives the number of characters.
---
---Since API version 3.
---
---```lua
---local s = "Zürich Nord"
---print(opensak.text.sub(s, 1, 6))  -- Zürich (Left)
---print(opensak.text.sub(s, -4))    -- Nord (Right)
---```
---@param text string The text.
---@param i? integer First character (default 1).
---@param j? integer Last character (default -1, the end).
---@return string # The characters from i to j.
function opensak.text.sub(text, i, j) end

---Decode HTML entities such as `&amp;`, `&auml;` and `&#228;` and keep everything else, tags included (GSAK's EntityDecode). opensak.text.html_to_text() also removes the tags.
---
---Since API version 3.
---
---```lua
---print(opensak.text.html_decode("Z&uuml;rich &amp; Bern"))  -- Zürich & Bern
---```
---@param text string The text.
---@return string # The decoded text.
function opensak.text.html_decode(text) end

---Split one CSV line into its fields (GSAK's CsvGet): a field in double quotes may contain the separator, and `""` inside quotes is one quote. For a whole file use opensak.read_csv().
---
---Since API version 3.
---
---```lua
---local f = opensak.text.csv_split([[GC12345,"Bonus, final",N47 22.123]])
---print(f[2])  -- Bonus, final
---```
---@param line string One line of CSV.
---@param sep? string Field separator, one character (default ",").
---@return string[] # The fields.
function opensak.text.csv_split(line, sep) end

---Percent-encode text for a URL, e.g. a search term in a link: everything except letters, digits and `-._~` becomes `%XX` of its UTF-8 bytes (GSAK's HttpEncode).
---
---Since API version 3.
---
---```lua
---local url = "https://www.geocaching.com/play/search?q=" .. opensak.text.url_encode("Zürich & Bern")
---```
---@param text string The text.
---@return string # The encoded text.
function opensak.text.url_encode(text) end

---Decode `%XX` sequences, and `+` as a space as in a query string.
---
---Since API version 3.
---
---```lua
---print(opensak.text.url_decode("Z%C3%BCrich+Nord"))  -- Zürich Nord
---```
---@param text string The text.
---@return string # The decoded text.
function opensak.text.url_decode(text) end

---Base64-encode text (GSAK's MimeEncode).
---
---Since API version 3.
---
---```lua
---print(opensak.text.base64_encode("Zürich"))  -- WsO8cmljaA==
---```
---@param text string The text.
---@return string # The Base64 text.
function opensak.text.base64_encode(text) end

---Decode Base64 (GSAK's MimeDecode); white space and line breaks in it are ignored. Invalid Base64 is an error.
---
---Since API version 3.
---
---```lua
---print(opensak.text.base64_decode("WsO8cmljaA=="))  -- Zürich
---```
---@param text string The Base64 text.
---@return string # The decoded text.
function opensak.text.base64_decode(text) end

---The number behind a geocaching.com code (GSAK's GsCodeToNum): up to GCFFFF the code is hexadecimal, from GCG000 on it is base 31. Works for TB, GL and other two-letter prefixes too; any case.
---
---Since API version 3.
---
---```lua
---print(opensak.text.gc_to_id("GC12345"))  -- 574995
---print(opensak.text.gc_to_id("GCG000"))   -- 65536
---```
---@param code string A code such as "GC12345".
---@return integer? # The number; nil if *code* is not a geocaching.com code.
function opensak.text.gc_to_id(code) end

---The geocaching.com code for a number (GSAK's NumToGsCode), the reverse of opensak.text.gc_to_id().
---
---Since API version 3.
---
---```lua
---print(opensak.text.id_to_gc(574995))      -- GC12345
---print(opensak.text.id_to_gc(1, "TB"))     -- TB1
---```
---@param id integer The number, 0 or more.
---@param prefix? string Two letters instead of "GC", e.g. "TB".
---@return string # The code.
function opensak.text.id_to_gc(id, prefix) end

opensak.chart = {}

---A bar chart of *rows* as an SVG string, for an HTML page (opensak.html.page, opensak.write_text) or a .svg file. Each row is a table; `x` names its category field (default "key") and `y` its value field (default "count"), nil counts as 0. `horizontal = true` draws the bars from the left, for long category names. Text and grid lines take the colour of the surrounding page; the bars use the CSS variable --chart-color, else `color`. Each bar shows its value as tooltip; `labels` also writes it on the bar (default: up to 16 bars). With a list of fields as `y`, the bars are stacked, one segment per field, with a legend (`names` instead of the field names; at most 8); a matrix from opensak.stats.pivot gives one stacked bar per matrix row, e.g. finds per year and cache type. At most 5000 rows.
---
---Since API version 3.
---
---```lua
---local rows = opensak.sql([[SELECT strftime('%Y', found_date) AS year,
---  COUNT(*) AS finds FROM caches WHERE found GROUP BY year ORDER BY year]])
---local svg = opensak.chart.bar(rows, { x = "year", y = "finds", title = "Finds per year" })
---opensak.write_text(opensak.temp_dir() .. "/finds.svg", svg)
----- stacked: finds per year and type
---local by_type = opensak.stats.pivot(finds, "year", "type")
---local svg2 = opensak.chart.bar(by_type, { title = "Finds per year and type" })
---```
---@param rows table[]|opensak.Matrix The rows, e.g. the result of opensak.sql(), or a matrix.
---@param options? {x?: string, y?: string|string[], title?: string, width?: number, height?: number, color?: string, horizontal?: boolean, labels?: boolean, names?: string[]} Fields to draw, title, size in pixels (default 640 × 280), colour (name or "#rrggbb").
---@return string # The chart as SVG.
function opensak.chart.bar(rows, options) end

---A line chart of *rows* in their order as an SVG string, like opensak.chart.bar: `x` the label on the x axis (evenly spaced, e.g. months), `y` the value. `area = true` fills below the line. The value of the last point is written next to it unless `labels = false`. Each point shows its value as tooltip.
---
---Since API version 3.
---
---```lua
---local rows, total = {}, 0
---for _, r in ipairs(opensak.sql([[SELECT strftime('%Y-%m', found_date) AS month,
---    COUNT(*) AS n FROM caches WHERE found GROUP BY month ORDER BY month]])) do
---  total = total + r.n
---  rows[#rows + 1] = { key = r.month, count = total }
---end
---local svg = opensak.chart.line(rows, { title = "Total finds", area = true })
---```
---@param rows table[] The rows, in the order to draw.
---@param options? {x?: string, y?: string, title?: string, width?: number, height?: number, color?: string, area?: boolean, labels?: boolean} As for opensak.chart.bar, plus `area`.
---@return string # The chart as SVG.
function opensak.chart.line(rows, options) end

---A grid chart of a matrix (see opensak.stats.pivot) as an SVG string: one cell per row × column value, the darker the larger, e.g. finds per difficulty × terrain. `labels = false` leaves the numbers out of the cells; `row_title` and `col_title` name the two fields.
---
---Since API version 3.
---
---```lua
---local dt = opensak.stats.pivot(finds, "difficulty", "terrain",
---  { rows = {1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5}, cols = {1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5} })
---local svg = opensak.chart.heatmap(dt, { row_title = "D", col_title = "T" })
---```
---@param matrix opensak.Matrix The result of opensak.stats.pivot().
---@param options? {title?: string, width?: number, color?: string, labels?: boolean, row_title?: string, col_title?: string} As for opensak.chart.bar, plus the field titles.
---@return string # The chart as SVG.
function opensak.chart.heatmap(matrix, options) end

---A rose (polar area) chart of *rows* as an SVG string: one sector per row, in row order clockwise from the top, e.g. finds per compass direction (N, NE, E, …). The area of a sector grows with its value; `x` names the label field, `y` the value (as for opensak.chart.bar).
---
---Since API version 3.
---
---```lua
---local DIRS = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" }
---for _, c in ipairs(finds) do
---  c.dir = DIRS[math.floor((opensak.coords.bearing(HOME_LAT, HOME_LON, c.lat, c.lon) + 22.5) / 45) % 8 + 1]
---end
---local rows = {}
---for _, g in ipairs(opensak.stats.group(finds, "dir")) do rows[g.key] = g.count end
---local sectors = {}
---for i, d in ipairs(DIRS) do sectors[i] = { key = d, count = rows[d] or 0 } end
---local svg = opensak.chart.rose(sectors, { title = "Finds by direction" })
---```
---@param rows table[] One row per sector, clockwise from north.
---@param options? {x?: string, y?: string, title?: string, width?: number, color?: string, labels?: boolean} As for opensak.chart.bar; `width` is also the height.
---@return string # The chart as SVG.
function opensak.chart.rose(rows, options) end

---A map of countries, states or counties as an SVG string, each region coloured by a value — e.g. finds per country from opensak.stats.group. `key` names the field with the region name (default "key"), `value` the number (default "count"), `layer` the kind of region: "country" (default), "state" or "county". `within` (a country name or a list) limits a state or county map to those countries; without it the countries the regions are in are shown. `extent = "all"` shows every region of the layer instead of zooming to the ones with a value. Names are matched ignoring case, accents and punctuation; those that match no region are listed below the map. The outlines are those of Update location (offline, no internet needed; county outlines are downloaded per country on first use, like there). Without the boundary data the map is a short note instead.
---
---Since API version 3.
---
---```lua
---local countries = opensak.stats.group(finds, "country")
---local map = opensak.chart.map(countries, { title = "Finds per country" })
---local cantons = opensak.stats.group(finds, "state")
---local map2 = opensak.chart.map(cantons, { layer = "state", within = "Switzerland" })
---```
---@param rows table[] One row per region with its value.
---@param options? {key?: string, value?: string, layer?: string, within?: string|string[], title?: string, width?: number, height?: number, color?: string, extent?: string, legend?: boolean} See above. The height follows the shape of the area unless given.
---@return string # The map as SVG.
function opensak.chart.map(rows, options) end

---A dot map as an SVG string: one dot per row at its coordinates (fields `lat` and `lon`), over the outlines of `layer` ("country", "state" or "county"), zoomed to the dots or to the `within` countries. `label` names the field shown as tooltip. At most 50000 rows; dots on the same pixel are drawn once.
---
---Since API version 3.
---
---```lua
---local svg = opensak.chart.points(finds, { label = "name", title = "My finds" })
---```
---@param rows table[] The rows, e.g. caches from opensak.caches().
---@param options? {lat?: string, lon?: string, label?: string, layer?: string, within?: string|string[], title?: string, width?: number, height?: number, color?: string, radius?: number} See above; `radius` of the dots in pixels (default 2.5).
---@return string # The map as SVG.
function opensak.chart.points(rows, options) end

opensak.geo = {}

---The outlines of countries, states or counties (`layer`) as a GeoJSON FeatureCollection (a table), e.g. for a map drawn by the macro itself or for another program. Like opensak.chart.map, `rows` puts the value (field `value`, default "count") of each row into the properties of the region its field `key` (default "key") names — matched ignoring case, accents and punctuation — `within` limits the countries and `extent = "all"` shows all regions instead of zooming to the ones with a value. `around` (rows with `lat`/`lon`) zooms to those points instead, `box` (`{min_lon, min_lat, max_lon, max_lat}`, e.g. the `bbox` of another collection) to an area. The outlines are clipped to the area and simplified for a map about `detail` pixels wide (default 800). Every feature has `properties.name` (and `country` for states and counties). The second value lists the names that match no region. nil if the boundary data is not installed.
---
---Since API version 3.
---
---```lua
---local outlines, unmatched = opensak.geo.outlines("country",
---  { rows = opensak.stats.group(finds, "country") })
---if outlines then
---  for _, f in ipairs(outlines.features) do
---    print(f.properties.name, f.properties.count or 0)
---  end
---end
---```
---@param layer? string "country" (default), "state" or "county".
---@param options? {within?: string|string[], rows?: table[], key?: string, value?: string, extent?: string, around?: table[], lat?: string, lon?: string, box?: number[], detail?: number} See above.
---@return table? # The FeatureCollection and the names without a region.
---@return string[]?
function opensak.geo.outlines(layer, options) end

---The regions of a layer ("country", "state" or "county") in the boundary data used by Update location and opensak.chart.map, one row per name: `{name =}`, for states and counties also `country`. `within` limits them to one country or a list. With `rows` (e.g. finds per state from opensak.stats.group) the rows are matched to the regions like opensak.chart.map does — the field `key` (default "key") names the region, ignoring case, accents and punctuation — and a region named by rows gets the sum of their field `value` (default "count"); the second value then lists the names that match no region. With it a macro can say "18 of 26 cantons". nil if the boundary data is not installed.
---
---Since API version 3.
---
---```lua
---local cantons = opensak.geo.regions("state", { within = "Switzerland" })
---if cantons then print(#cantons .. " cantons") end
----- the cantons you have found a cache in
---local all, unmatched = opensak.geo.regions("state",
---  { within = "Switzerland", rows = opensak.stats.group(finds, "state") })
---local n = 0
---for _, r in ipairs(all or {}) do if r.count then n = n + 1 end end
---print(n .. " of " .. #(all or {}) .. " cantons")
---```
---@param layer? string "country" (default), "state" or "county".
---@param options? {within?: string|string[], rows?: table[], key?: string, value?: string} See above.
---@return {name: string, country?: string}[]? # The regions, sorted (with `rows`: and the names that match none).
---@return string[]?
function opensak.geo.regions(layer, options) end

opensak.stats = {}

---Count *rows* per value of the field *by* and return one row per group: `{key =, count =}`, sorted by key. Rows can come from opensak.caches(), opensak.sql() or opensak.read_csv(); rows whose *by* is nil are left out. Options: `period` groups a date field ("YYYY-MM-DD" or os.time() seconds) by "day", "week" ("2026-W41"), "month" ("2026-10"), "year" or "weekday" ("Mon"…"Sun", sorted Monday first). `count_distinct = field` adds `distinct`, the number of different values of that field per group; `sum = field` adds `sum`. `sort = "count"` (or "sum") puts the largest group first. `top = n` keeps n groups and puts the rest into one group named `other` (default "Other").
---
---Since API version 3.
---
---```lua
---local finds = {}
---for c in opensak.caches{ found = true, fields = {"found_date", "type"} } do
---  finds[#finds + 1] = c
---end
---for _, g in ipairs(opensak.stats.group(finds, "found_date",
---    { period = "year", count_distinct = "found_date" })) do
---  print(g.key, g.count .. " finds", g.distinct .. " cache days")
---end
---local types = opensak.stats.group(finds, "type", { sort = "count", top = 8 })
---```
---@param rows table[] The rows.
---@param by string The field to group by.
---@param options? {period?: string, count_distinct?: string, sum?: string, sort?: string, top?: integer, other?: string} See above.
---@return {key: any, count: integer, distinct?: integer, sum?: number}[] # One row per group.
function opensak.stats.group(rows, by, options) end

---*rows* sorted by the period in field `key` (default "key"), with a row added for each missing "day", "month" (default) or "year" between the first and the last row, or between `from` and `to`. Number fields of an added row are 0. Use it before a chart or table over time, so months without finds still take their place.
---
---Since API version 3.
---
---```lua
---local months = opensak.stats.fill(
---  opensak.stats.group(finds, "found_date", { period = "month" }))
---```
---@param rows table[] The rows, e.g. from opensak.stats.group.
---@param options? {key?: string, period?: string, from?: string, to?: string} Field with the period, its length, first and last period (e.g. "2020-01").
---@return table[] # New rows.
function opensak.stats.fill(rows, options) end

---Copies of *rows* with the running total of *field* (default "count") in *into* (default "total"), e.g. the total number of finds at the end of each month.
---
---Since API version 3.
---
---```lua
---local total = opensak.stats.running(months)   -- months[i].total
---print("finds at the end: " .. total[#total].total)
---```
---@param rows table[] The rows, in order.
---@param field? string The number field to add up.
---@param into? string The field for the total.
---@return table[] # New rows.
function opensak.stats.running(rows, field, into) end

---Count *rows* per pair of values of two fields, e.g. difficulty × terrain. Returns `{rows =, cols =, cells =, max =, total =, filled =}`: `rows` and `cols` are the values of the two fields (sorted), `cells[i][j]` the count for rows[i] and cols[j], `filled` the number of cells that are not 0. The options `rows` and `cols` give the values and their order instead (other values are left out); `sum = field` adds up that field instead of counting. For opensak.html.matrix.
---
---Since API version 3.
---
---```lua
---local dt = opensak.stats.pivot(finds, "difficulty", "terrain")
---print(dt.filled .. " of 81 D/T combinations")
---```
---@param rows table[] The rows.
---@param row_field string Field for the rows of the matrix.
---@param col_field string Field for the columns.
---@param options? {rows?: any[], cols?: any[], sum?: string} See above.
---@return opensak.Matrix # The matrix.
function opensak.stats.pivot(rows, row_field, col_field, options) end

---Every *step*-th row of *rows*, e.g. every 1000th find (sort the rows first, see opensak.stats.sort): copies with the row number in `n`. The first and the last row are included unless `first = false` / `last = false`. With `date = field`, `days` holds the days since the previous row picked.
---
---Since API version 3.
---
---```lua
---local milestones = opensak.stats.every(finds, 1000, { date = "found_date" })
---for _, m in ipairs(milestones) do print(m.n, m.found_date, m.code, m.days) end
---```
---@param rows table[] The rows, in order.
---@param step integer Pick every step-th row.
---@param options? {first?: boolean, last?: boolean, date?: string} See above.
---@return table[] # Copies of the picked rows.
function opensak.stats.every(rows, step, options) end

---Key figures of *field* over *rows* (nil values left out): `count`, `distinct`, `min` and `max` (also for dates and text), and for numbers `sum`, `mean` and `median`.
---
---Since API version 3.
---
---```lua
---local f = opensak.stats.summary(finds, "found_date")
---print(f.count .. " finds on " .. f.distinct .. " days, first " .. f.min)
---print("mean difficulty", opensak.stats.summary(finds, "difficulty").mean)
---```
---@param rows table[] The rows.
---@param field string The field.
---@return {count: integer, distinct: integer, min?: any, max?: any, sum?: number, mean?: number, median?: number} # The figures.
function opensak.stats.summary(rows, field) end

---A new list of *rows* sorted by the fields in turn; "-field" sorts largest first. nil values come last. Faster than table.sort with a compare function, and stable: rows with equal fields keep their order.
---
---Since API version 3.
---
---```lua
---finds = opensak.stats.sort(finds, "found_date", "code")
---local best = opensak.stats.sort(caches, "-favorite_points", "name")
---```
---@param rows table[] The rows.
---@param ... string The fields to sort by.
---@return table[] # The same rows in the new order.
function opensak.stats.sort(rows, ...) end

---Runs of days in a date field: `days` (the number of different days), `longest` (most days in a row with at least one row, e.g. your longest caching streak), `gap` (most days in a row without, between the first and the last day) and `last` (the run that ends on the last day). Each run is `{days =, first =, last =}`; `gap` is nil if there is none.
---
---Since API version 3.
---
---```lua
---local s = opensak.stats.streaks(finds, "found_date")
---print("longest streak: " .. s.longest.days .. " days, from " .. s.longest.first)
---```
---@param rows table[] The rows.
---@param field string The date field.
---@return {days: integer, longest?: opensak.Run, gap?: opensak.Run, last?: opensak.Run} # The runs.
function opensak.stats.streaks(rows, field) end

opensak.html = {}

---*value* as text for HTML: `& < > " '` are escaped, nil becomes "", 3.0 becomes "3".
---
---Since API version 3.
---
---```lua
---local cell = "<td>" .. opensak.html.escape(c.name) .. "</td>"
---```
---@param value any A string, number, boolean or nil.
---@return string # Escaped text.
function opensak.html.escape(value) end

---*rows* as an HTML table (values escaped), e.g. the rows of opensak.sql() (GSAK's SqlToHtml). `columns` lists the fields to show, each a field name or `{field, heading}`; without it all fields are shown, sorted by name — a Lua table has no field order, so pass `columns` to keep the order of the query. Number columns are right-aligned. `link = { code = "url" }` turns the code cells into links to the row's url field (http/https only). `bars = {"count"}` adds a bar next to each value of those number fields, scaled to the largest (in `color`, "#rrggbb"). The bars use inline styles only, so they also show without the stylesheet of opensak.html.page.
---
---Since API version 3.
---
---```lua
---local rows = opensak.sql(
---  "SELECT country, COUNT(*) AS n FROM caches GROUP BY country ORDER BY n DESC")
---local html = opensak.html.table(rows, {
---  columns = { {"country", "Country"}, {"n", "Caches"} }, bars = { "n" } })
---```
---@param rows table[] The rows.
---@param options? {columns?: (string|string[])[], link?: table<string, string>, caption?: string, bars?: string[], color?: string} See above.
---@return string # HTML.
function opensak.html.table(rows, options) end

---A matrix (from opensak.stats.pivot) as an HTML table: the values of the two fields as headings, each cell shaded by its value. `percent = true` shows each cell as a share of all, `totals = true` adds a total row and column, `shade = false` leaves the colours out; `row_title` and `col_title` name the fields, `color` ("#rrggbb") the shading. Inline styles only, so the shading also shows without the stylesheet of opensak.html.page.
---
---Since API version 3.
---
---```lua
---local dt = opensak.stats.pivot(finds, "difficulty", "terrain")
---local html = opensak.html.matrix(dt, { row_title = "D", col_title = "T", totals = true })
---```
---@param matrix opensak.Matrix The matrix.
---@param options? {shade?: boolean, percent?: boolean, totals?: boolean, row_title?: string, col_title?: string, color?: string} See above.
---@return string # HTML.
function opensak.html.matrix(matrix, options) end

---Key figures as a row of tiles: a list of `{label =, value =}`.
---
---Since API version 3.
---
---```lua
---local found = opensak.sql("SELECT COUNT(*) AS n FROM caches WHERE found = 1")[1].n
---local html = opensak.html.tiles{
---  { label = "caches", value = opensak.count() },
---  { label = "found", value = found },
---}
---```
---@param items {label: string, value: any}[] The tiles.
---@return string # HTML.
function opensak.html.tiles(items) end

---Put HTML parts (e.g. two tables) side by side; on a narrow window they go one below the other.
---
---Since API version 3.
---
---```lua
---local html = opensak.html.columns(table1, table2)
---```
---@param ... string The HTML parts (or one list of them).
---@return string # HTML.
function opensak.html.columns(...) end

---A complete HTML page for opensak.write_text: `title` (also the heading), `subtitle`, the body — the strings in the list part of the table, and/or `body` — and `footer`. A built-in stylesheet with a light and a dark theme styles headings (`<h2>`), tables, tiles and columns; `css` is added after it. The body is put in as it is: build it from the html.* functions, or escape your own text with html.escape.
---
---Since API version 3.
---
---```lua
---local rows = opensak.sql("SELECT code, name, difficulty FROM caches LIMIT 50")
---local page = opensak.html.page{
---  title = "Caches", subtitle = opensak.database(),
---  opensak.html.tiles{ { label = "caches", value = opensak.count() } },
---  "<h2>First 50</h2>",
---  opensak.html.table(rows, { columns = { "code", "name", "difficulty" } }),
---}
---opensak.write_text(opensak.temp_dir() .. "/caches.html", page)
---```
---@param spec {title?: string, subtitle?: string, css?: string, lang?: string, footer?: string, body?: string|string[], [integer]: string} See above.
---@return string # The page.
function opensak.html.page(spec) end

opensak.date = {}

---Parse a date into seconds since the epoch, the same kind of value as `os.time()`. Without a format, ISO 8601 (`"2026-10-06"`, `"2026-10-06T14:30:00Z"`) and `"06.10.2026 [14:30[:00]]"` are understood. Times without a zone are local time.
---
---Since API version 2.
---
---```lua
---local t = opensak.date.parse("2026-10-06")
---local t2 = opensak.date.parse("10/06/2026", "%m/%d/%Y")
---```
---@param text string The text.
---@param fmt? string strptime format, e.g. "%d/%m/%Y".
---@return integer? # Seconds since the epoch, or nil if the text does not parse.
function opensak.date.parse(text, fmt) end

---Format seconds since the epoch (e.g. from `os.time()` or `opensak.date.parse()`) in local time.
---
---Since API version 2.
---
---```lua
---print(opensak.date.format(os.time(), "%d.%m.%Y %H:%M"))
---```
---@param t number Seconds since the epoch.
---@param fmt? string strftime format, "%Y-%m-%d" if omitted.
---@return string # The formatted date.
function opensak.date.format(t, fmt) end

---Whole days from date *a* to date *b* (negative if *b* is earlier). Dates are "YYYY-MM-DD" strings (a time after it is ignored) or seconds since the epoch.
---
---Since API version 3.
---
---```lua
---print(opensak.date.diff("2026-01-01", "2026-10-06"))   -- 278
---```
---@param a string|number The first date.
---@param b string|number The second date.
---@return integer # Days.
function opensak.date.diff(a, b) end

---The period a date falls in, named as opensak.stats.group names it: "day" ("2026-10-06"), "week" ("2026-W41"), "month" ("2026-10"), "year" ("2026") or "weekday" ("Tue"). For a field of your own to group or pivot by, e.g. the year × month table of your finds.
---
---Since API version 3.
---
---```lua
---for _, c in ipairs(finds) do
---  c.year = opensak.date.period(c.found_date, "year")
---  c.month = c.found_date:sub(6, 7)
---end
---local grid = opensak.stats.pivot(finds, "year", "month")
---```
---@param date string|number "YYYY-MM-DD" (a time after it is ignored) or seconds since the epoch.
---@param period string "day", "week", "month", "year" or "weekday".
---@return string # The period.
function opensak.date.period(date, period) end

opensak.settings = {}

---The settings this macro saved with opensak.settings.save(), or an empty table on the first run. Each macro has its own settings, kept per macro file in the `settings` folder of the macros folder; no other macro can read them. A renamed macro starts with empty settings, and an unsaved macro has none.
---
---Since API version 3.
---
---```lua
---local s = opensak.settings.load()
---s.maxdist = s.maxdist or 25
---```
---@return table # The saved settings; empty if there are none.
function opensak.settings.load() end

---Save settings for the next run of this macro, replacing the saved ones. Strings, numbers, booleans and nested tables can be saved; a table needs string keys, or 1, 2, 3, … for a list. Functions and cyclic tables are refused, and the settings may take up at most 1 MB.
---
---Since API version 3.
---
---```lua
---local s = opensak.settings.load()
---s.maxdist = 30
---s.types = {"Traditional", "Multi"}
---opensak.settings.save(s)
---```
---@param settings table The settings to save.
function opensak.settings.save(settings) end

---Remove this macro's saved settings, so the next opensak.settings.load() returns an empty table.
---
---Since API version 3.
---
---```lua
---opensak.settings.clear()
---```
function opensak.settings.clear() end
