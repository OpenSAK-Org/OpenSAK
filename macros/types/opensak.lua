---@meta
-- Generated from src/opensak/macro/runtime.py by scripts/generate_macro_api_docs.py — do not edit by hand.
-- OpenSAK Lua macro API, version 2. Reference: docs/macros/api.md

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

---Run a read-only SQL query (SQLite) against the active database (or the one named by the `database` option) and return all rows. Only reading statements are allowed; the connection itself is read-only. Column names follow the database schema, which may change between versions (see opensak.tables() and opensak.columns()). Use `AS` to name computed columns. NULL values are nil. At most 100,000 rows; use opensak.sql_each() for more. A query is aborted after 60 s.
---
---Since API version 2.
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

---The tables and views of the active database (or the one named by the `database` option), for use with opensak.sql().
---
---Since API version 2.
---
---```lua
---print(table.concat(opensak.tables(), ", "))
---```
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return string[] # Table and view names, sorted.
function opensak.tables(options) end

---The columns of a table or view of the active database (or the one named by the `database` option).
---
---Since API version 2.
---
---```lua
---for _, c in ipairs(opensak.columns("caches")) do print(c.name, c.type) end
---```
---@param table string Table or view name.
---@param options? opensak.ReadOptions `database`: read another database instead of the active one.
---@return {name: string, type: string}[] # Column names and SQL types, in table order.
function opensak.columns(table, options) end

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

---Pause the macro. One pause lasts at most 10 s and all pauses of a run together at most 60 s; a cancelled macro stops at its next pause.
---
---Since API version 2.
---
---```lua
---opensak.sleep(500)
---```
---@param ms number Milliseconds.
function opensak.sleep(ms) end

opensak.coords = {}

---Parse a coordinate string in any format OpenSAK understands (DMM, DMS, decimal degrees).
---
---Since API version 2.
---
---```lua
---local lat, lon = opensak.coords.parse("N47 22.123 E008 32.456")
---if not lat then error("not a coordinate") end
---```
---@param text string The coordinates.
---@return number? # Latitude and longitude, or nil if the text cannot be parsed.
---@return number?
function opensak.coords.parse(text) end

---Format coordinates. Formats: `"dmm"` (default), `"dms"`, `"dd"`, `"utm"`, `"ch1903"` (Swiss LV03) and `"ch1903+"` (Swiss LV95). The Swiss formats are only meaningful in and around Switzerland.
---
---Since API version 2.
---
---```lua
---print(opensak.coords.format(47.36872, 8.54093, "utm"))
---print(opensak.coords.format("N47 22.123 E008 32.456", "ch1903"))
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
