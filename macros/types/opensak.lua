---@meta
-- Generated from src/opensak/macro/runtime.py by scripts/generate_macro_api_docs.py — do not edit by hand.
-- OpenSAK Lua macro API, version 3. Reference: docs/macros/api.md

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
---@field dnf? boolean That flag set, or not set.
---@field ftf? boolean That flag set, or not set.
---@field premium? boolean That flag set, or not set.
---@field has_trackables? boolean With or without trackables in the cache.
---@field owned? boolean Owned by you (owner = your geocaching.com username in Settings), or not.
---@field near? table Within `km` of a coordinate or of a saved centre point.
---@field distance? number|number[] Km from the active centre point: at most a number, or {min, max} where nil leaves a side open.
---@field bearing? number[] Bearing from the active centre point, clockwise from the first to the second value.
---@field favorites? number|number[] Favourite points / elevation in metres: exact value, or {min, max} where nil leaves a side open.
---@field elevation? number|number[] Favourite points / elevation in metres: exact value, or {min, max} where nil leaves a side open.
---@field hidden? string[] Date range {from, to}, both inclusive, "YYYY-MM-DD"; nil leaves a side open. `last_gpx_update` also takes a time, "2026-09-01T18:00".
---@field found_date? string[] Date range {from, to}, both inclusive, "YYYY-MM-DD"; nil leaves a side open. `last_gpx_update` also takes a time, "2026-09-01T18:00".
---@field last_gpx_update? string[] Date range {from, to}, both inclusive, "YYYY-MM-DD"; nil leaves a side open. `last_gpx_update` also takes a time, "2026-09-01T18:00".
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

---Filter keys plus `fields` and `database`, understood by `opensak.caches{}`.
---@class opensak.CachesSpec: opensak.FilterSpec
---@field fields? string[] Cache fields to load (`code` is always included).
---@field database? string Read this database instead of the active one.

---Options of the read functions (`opensak.cache()`, `opensak.sql()`, ...).
---@class opensak.ReadOptions
---@field database? string Read this database instead of the active one.

---Options of `opensak.move_caches()` and `opensak.copy_caches()`.
---@class opensak.TransferOptions
---@field codes? string[] GC codes to transfer (default: the caches of the active filter).
---@field if_exists? "newer"|"replace"|"skip" When the cache exists in the target (default "newer").

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
---@field remember? boolean Start with the values the user entered last time (stored per macro file, next to opensak.settings; the macro must be saved). Fields with `remember = false` and passwords are not kept.
---@field id? string Name the remembered values are kept under (default: the title), for macros with several forms.
---@field timeout? number Close the form after this many seconds; it then returns the values and "timeout".
---@field on_open? fun(f: opensak.Form) Called once when the form opens.
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
---@field on_change? fun(f: opensak.Form, value: any, id: string) Called when the user changes the value — a text or number field when it loses focus or Enter is pressed. The form stays open.
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
---@field note string? Your local note.
---@field gc_note string? Personal note from geocaching.com.
---@field hint string? Hint text as imported.
---@field url string? Listing URL.
---@field corrected {lat: number, lon: number}? Corrected coordinates; nil if the cache is not solved.
---@field waypoint_count integer Number of additional waypoints.
---@field log_count integer Number of logs stored.
---@field trackable_count integer Number of trackables in the cache.
---@field last_log_date string? Date of the latest log, "YYYY-MM-DD".
---@field last_gpx_update string? When an import last touched the cache, "YYYY-MM-DDTHH:MM:SS"; finds caches the latest Pocket Query did not refresh.

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
---@field note? string|false Your local note.
---@field gc_note? string|false Personal note from geocaching.com.
---@field hint? string|false Hint text as imported.
---@field url? string|false Listing URL.
---@field corrected? {lat: number, lon: number}|string|false Corrected coordinates; nil if the cache is not solved.

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
---Since API version 1.
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

---Sort the cache list, like a click on a column header. opensak.caches() and opensak.codes() then return the caches in this order. Fields: `name`, `code`, `type`, `container`, `difficulty`, `terrain`, `hidden`, `placed_by`, `country`, `state`, `county`, `found`, `found_date`, `dnf`, `dnf_date`, `ftf`, `archived`, `premium`, `distance`, `bearing`, `favorite_points`, `trackable_count`, `user_flag`, `locked`, `user_sort`, `user_data1`, `user_data2`, `user_data3`, `user_data4`.
---
---Since API version 2.
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
---Since API version 2.
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

---Iterate over caches, one table per cache (see Cache fields), in a generic `for`. Without arguments: the caches of the active filter, in grid order. With filter keys (see Filter keys): the caches matching them, sorted by name; the view and the active filter stay unchanged. `database` reads another database, without switching: its caches matching the filter keys, or all of them, sorted by name. `fields` limits the fields loaded (`code` is always included), which makes loops over many caches faster. Caches are loaded in chunks, so large databases do not hit the memory limit.
---
---Since API version 2.
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
---@param spec? opensak.CachesSpec Filter keys, `database` and/or `fields`; nothing = the active filter.
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

---Run a read-only SQL query (SQLite) against the active database (or the one named by the `database` option) and return all rows. Only reading statements are allowed, except on TEMP tables: a macro may create, fill, change and drop its own TEMP tables, views and indexes (`CREATE TEMP TABLE ...`) and use them in any query, opensak.sql_write() included. They are never stored in the database and are gone when the macro ends or calls opensak.switch_database(); changes to them inside opensak.transaction() are undone with it. A TEMP table may not take the name of a table of the database. Column names follow the database schema, which may change between versions (see opensak.tables() and opensak.columns()). Use `AS` to name computed columns. NULL values are nil. At most 100,000 rows; use opensak.sql_each() for more. A query is aborted after 60 s.
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

---Like opensak.sql(), but returns an iterator for a generic `for` that fetches the rows in chunks — for results of any size.
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
---Since API version 2. Changed in API version 3: lists the macro's TEMP tables and views, and the tables of opensak.macro_db() as `macro.<name>`.
---
---```lua
---print(table.concat(opensak.tables(), ", "))
---```
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return string[] # Table and view names, sorted.
function opensak.tables(options) end

---The columns of a table or view of the active database (or the one named by the `database` option).
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

---Create a new, empty database in the default database folder and add it to the list. The active database does not change. Fails if the name is taken or its file already exists.
---
---Since API version 2.
---
---```lua
---local name = opensak.create_database("CH_Zurich")
---```
---@param name string Name of the new database.
---@return string # The name actually used (surrounding spaces removed).
function opensak.create_database(name) end

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

---Move caches from the active database to another one, with all logs, waypoints, attributes, trackables and notes (like Database → Move caches). By default the caches of the active filter. When a cache already exists in the target, `if_exists` decides: `"newer"` (default) replaces it only if the active database's copy was imported later, `"replace"` always, `"skip"` never. A cache not written to the target stays in the active database.
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

---Change fields of a cache in the active database. The keys are cache field names (see Changing caches); only the given fields change. `false` clears a field that is not a boolean. Fields OpenSAK maintains itself, such as `code`, `distance` or `log_count`, cannot be written. Locked caches can be changed too: the lock only protects from imports. Before a macro's first change, OpenSAK asks the user to allow that macro (see Changing caches).
---
---Since API version 3.
---
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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

---Add a new cache to the active database. `code`, `name`, `type`, `lat` and `lon` are required; any other writable field may be given too (see Changing caches). Fails if the code is already in the database. Needs the user's permission like opensak.update().
---
---Since API version 3.
---
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).
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
---Since API version 3.
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
