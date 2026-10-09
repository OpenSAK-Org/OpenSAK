<!-- Generated from src/opensak/macro/runtime.py by scripts/generate_macro_api_docs.py — do not edit by hand. -->

# OpenSAK Lua macro API

API version: **2** (`opensak.api_version()`).

Macros are Lua 5.4 scripts run in a sandbox. They talk to OpenSAK through the global `opensak` table. File access is limited to the folders listed in Settings → Folder permissions. When a macro needs a file in another folder, OpenSAK asks the user whether to allow that folder for this run only or always, or to deny it; reading and writing are asked separately. OpenSAK's own settings and database files are never accessible.

See [Example macros](#example-macros) for complete scripts and [Editor support](#editor-support-vs-code) for autocompletion in VS Code.

## Functions

| Function | Since |
|---|---|
| [`opensak.api_version`](#opensakapiversion) | 1 |
| [`opensak.filter`](#opensakfilter) | 1 |
| [`opensak.filter_name`](#opensakfiltername) | 2 |
| [`opensak.sort`](#opensaksort) | 2 |
| [`opensak.filter_profile`](#opensakfilterprofile) | 1 |
| [`opensak.clear_filter`](#opensakclearfilter) | 1 |
| [`opensak.count`](#opensakcount) | 1 |
| [`opensak.profiles`](#opensakprofiles) | 1 |
| [`opensak.cache`](#opensakcache) | 2 |
| [`opensak.caches`](#opensakcaches) | 2 |
| [`opensak.current`](#opensakcurrent) | 2 |
| [`opensak.selected`](#opensakselected) | 2 |
| [`opensak.codes`](#opensakcodes) | 2 |
| [`opensak.description`](#opensakdescription) | 2 |
| [`opensak.sql`](#opensaksql) | 2 |
| [`opensak.sql_each`](#opensaksqleach) | 2 |
| [`opensak.tables`](#opensaktables) | 2 |
| [`opensak.columns`](#opensakcolumns) | 2 |
| [`opensak.databases`](#opensakdatabases) | 2 |
| [`opensak.database`](#opensakdatabase) | 2 |
| [`opensak.database_exists`](#opensakdatabaseexists) | 2 |
| [`opensak.create_database`](#opensakcreatedatabase) | 2 |
| [`opensak.switch_database`](#opensakswitchdatabase) | 2 |
| [`opensak.move_caches`](#opensakmovecaches) | 2 |
| [`opensak.copy_caches`](#opensakcopycaches) | 2 |
| [`opensak.set_corrected`](#opensaksetcorrected) | 1 |
| [`opensak.clear_corrected`](#opensakclearcorrected) | 1 |
| [`opensak.read_csv`](#opensakreadcsv) | 1 |
| [`opensak.export_file`](#opensakexportfile) | 2 |
| [`opensak.export_gpx`](#opensakexportgpx) | 2 |
| [`opensak.confirm`](#opensakconfirm) | 1 |
| [`opensak.choose_file`](#opensakchoosefile) | 2 |
| [`opensak.temp_dir`](#opensaktempdir) | 1 |
| [`opensak.macros_dir`](#opensakmacrosdir) | 1 |
| [`opensak.version`](#opensakversion) | 2 |
| [`opensak.sleep`](#opensaksleep) | 2 |
| [`opensak.coords.parse`](#opensakcoordsparse) | 2 |
| [`opensak.coords.format`](#opensakcoordsformat) | 2 |
| [`opensak.coords.distance`](#opensakcoordsdistance) | 2 |
| [`opensak.coords.bearing`](#opensakcoordsbearing) | 2 |
| [`opensak.coords.project`](#opensakcoordsproject) | 2 |
| [`opensak.coords.midpoint`](#opensakcoordsmidpoint) | 2 |
| [`opensak.coords.inside`](#opensakcoordsinside) | 2 |
| [`opensak.coords.location`](#opensakcoordslocation) | 2 |
| [`opensak.re.find`](#opensakrefind) | 2 |
| [`opensak.re.match`](#opensakrematch) | 2 |
| [`opensak.re.findall`](#opensakrefindall) | 2 |
| [`opensak.re.replace`](#opensakrereplace) | 2 |
| [`opensak.re.split`](#opensakresplit) | 2 |
| [`opensak.text.html_to_text`](#opensaktexthtmltotext) | 2 |
| [`opensak.text.rot13`](#opensaktextrot13) | 2 |
| [`opensak.text.digit_sum`](#opensaktextdigitsum) | 2 |
| [`opensak.text.word_value`](#opensaktextwordvalue) | 2 |
| [`opensak.text.normalize_name`](#opensaktextnormalizename) | 2 |
| [`opensak.date.parse`](#opensakdateparse) | 2 |
| [`opensak.date.format`](#opensakdateformat) | 2 |

### opensak.api_version

```lua
opensak.api_version()
```

The API version of this OpenSAK build. Each function lists the version it was added in.

Returns `integer` — The API version.

Since API version 1.

Example:

```lua
if opensak.api_version() < 1 then
    error("this macro needs a newer OpenSAK")
end
```

### opensak.filter

```lua
opensak.filter(spec)
```

Build a filter from the given keys (see Filter keys; combined with AND unless `mode = "OR"`) and apply it. Usually called with table syntax: `opensak.filter{ ... }`. When nothing matches, the view is left unchanged.

Parameters:

- `spec` (`opensak.FilterSpec`) — The filter keys.

Returns `integer` — Number of matching caches (0 = view unchanged).

Since API version 1.

Example:

```lua
local n = opensak.filter{ type = "Traditional", difficulty = {1, 2}, found = false }
print("Easy unfound traditionals: " .. n)
```

### opensak.filter_name

```lua
opensak.filter_name()
```

The name of the active filter: the `label` of opensak.filter{}, a profile name, or "" when no filter is active. The same as the {filter} variable of an export file name.

Returns `string` — The filter name; "" for none.

Since API version 2.

Example:

```lua
print("Filter: " .. opensak.filter_name())
```

### opensak.sort

```lua
opensak.sort(field [, direction])
```

Sort the cache list, like a click on a column header. opensak.caches() and opensak.codes() then return the caches in this order. Fields: `name`, `code`, `type`, `container`, `difficulty`, `terrain`, `hidden`, `placed_by`, `country`, `state`, `county`, `found`, `found_date`, `dnf`, `dnf_date`, `ftf`, `archived`, `premium`, `distance`, `bearing`, `favorite_points`, `trackable_count`, `user_flag`, `locked`, `user_sort`, `user_data1`, `user_data2`, `user_data3`, `user_data4`.

Parameters:

- `field` (`string`) — A cache field, e.g. "difficulty".
- `direction` (`"asc"|"desc"`, optional) — "asc" if omitted.

Since API version 2.

Example:

```lua
opensak.sort("difficulty", "desc")
```

### opensak.filter_profile

```lua
opensak.filter_profile(name)
```

Apply a saved filter profile.

Parameters:

- `name` (`string`) — Name of the saved profile.

Returns `integer` — Number of matching caches.

Since API version 1.

Example:

```lua
local n = opensak.filter_profile("Unfound nearby")
```

### opensak.clear_filter

```lua
opensak.clear_filter()
```

Remove the active filter, so all caches are shown again.

Since API version 1.

Example:

```lua
opensak.clear_filter()
```

### opensak.count

```lua
opensak.count()
```

The number of caches matching the active filter.

Returns `integer` — Number of caches shown.

Since API version 1.

Example:

```lua
print(opensak.count() .. " caches shown")
```

### opensak.profiles

```lua
opensak.profiles()
```

The names of all saved filter profiles.

Returns `string[]` — Profile names.

Since API version 1.

Example:

```lua
for _, name in ipairs(opensak.profiles()) do
    print(name)
end
```

### opensak.cache

```lua
opensak.cache(code [, options])
```

One cache as a table (see Cache fields). It is a snapshot: changing it changes nothing in the database.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `opensak.Cache?` — The cache, or nil if it is not in the database.

Since API version 2.

Example:

```lua
local c = opensak.cache("GC12345")
if c and c.corrected then print(c.name, c.corrected.lat, c.corrected.lon) end
local found = opensak.cache("GC12345", { database = "Found" })
```

### opensak.caches

```lua
opensak.caches([spec])
```

Iterate over caches, one table per cache (see Cache fields), in a generic `for`. Without arguments: the caches of the active filter, in grid order. With filter keys (see Filter keys): the caches matching them, sorted by name; the view and the active filter stay unchanged. `database` reads another database, without switching: its caches matching the filter keys, or all of them, sorted by name. `fields` limits the fields loaded (`code` is always included), which makes loops over many caches faster. Caches are loaded in chunks, so large databases do not hit the memory limit.

Parameters:

- `spec` (`opensak.CachesSpec`, optional) — Filter keys, `database` and/or `fields`; nothing = the active filter.

Returns `fun(): opensak.Cache?` — Iterator for a generic `for`.

Since API version 2.

Example:

```lua
for c in opensak.caches() do print(c.code, c.name) end
for c in opensak.caches{ found = true, country = "Switzerland",
                         fields = {"difficulty", "terrain"} } do
    print(c.code, c.difficulty, c.terrain)
end
for c in opensak.caches{ database = "Found", fields = {"corrected"} } do
    print(c.code, c.corrected and c.corrected.lat)
end
```

### opensak.current

```lua
opensak.current()
```

The cache selected in the grid.

Returns `opensak.Cache?` — The cache, or nil if no row is selected.

Since API version 2.

Example:

```lua
local c = opensak.current()
if c then print(c.code .. " " .. c.name) end
```

### opensak.selected

```lua
opensak.selected()
```

The GC codes of the rows selected in the grid.

Returns `string[]` — GC codes; empty if nothing is selected.

Since API version 2.

Example:

```lua
for _, code in ipairs(opensak.selected()) do print(code) end
```

### opensak.codes

```lua
opensak.codes()
```

The GC codes of the caches of the active filter, in grid order. Cheaper than opensak.caches() when only the codes are needed.

Returns `string[]` — GC codes.

Since API version 2.

Example:

```lua
print(table.concat(opensak.codes(), ", "))
```

### opensak.description

```lua
opensak.description(code [, options])
```

The listing description of a cache. Not part of the cache table because it can be large.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `{short: string?, long: string?, html: boolean}?` — Short and long description and whether they are HTML; nil if the cache is not in the database.

Since API version 2.

Example:

```lua
local d = opensak.description("GC12345")
if d and d.long and d.long:find("bonus") then print("bonus cache") end
```

### opensak.sql

```lua
opensak.sql(query [, params] [, options])
```

Run a read-only SQL query (SQLite) against the active database (or the one named by the `database` option) and return all rows. Only reading statements are allowed; the connection itself is read-only. Column names follow the database schema, which may change between versions (see opensak.tables() and opensak.columns()). Use `AS` to name computed columns. NULL values are nil. At most 100,000 rows; use opensak.sql_each() for more. A query is aborted after 60 s.

Parameters:

- `query` (`string`) — One SQL statement.
- `params` (`table`, optional) — Values for `?` placeholders ({ v1, v2 }) or for `:name` placeholders ({ name = v }); `nil` or `{}` for none.
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `table<string, any>[]` — One table per row, keyed by column name.

Since API version 2.

Example:

```lua
local rows = opensak.sql(
  "SELECT country, COUNT(*) AS n FROM caches WHERE found = ? GROUP BY country", { 1 })
for _, r in ipairs(rows) do print(r.country, r.n) end
```

### opensak.sql_each

```lua
opensak.sql_each(query [, params] [, options])
```

Like opensak.sql(), but returns an iterator for a generic `for` that fetches the rows in chunks — for results of any size.

Parameters:

- `query` (`string`) — One SQL statement.
- `params` (`table`, optional) — As for opensak.sql().
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `fun(): table<string, any>?` — Iterator for a generic `for`.

Since API version 2.

Example:

```lua
for r in opensak.sql_each("SELECT gc_code, name FROM caches WHERE found = 0") do
    print(r.gc_code, r.name)
end
```

### opensak.tables

```lua
opensak.tables([options])
```

The tables and views of the active database (or the one named by the `database` option), for use with opensak.sql().

Parameters:

- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `string[]` — Table and view names, sorted.

Since API version 2.

Example:

```lua
print(table.concat(opensak.tables(), ", "))
```

### opensak.columns

```lua
opensak.columns(table [, options])
```

The columns of a table or view of the active database (or the one named by the `database` option).

Parameters:

- `table` (`string`) — Table or view name.
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `{name: string, type: string}[]` — Column names and SQL types, in table order.

Since API version 2.

Example:

```lua
for _, c in ipairs(opensak.columns("caches")) do print(c.name, c.type) end
```

### opensak.databases

```lua
opensak.databases()
```

All databases in OpenSAK's database list, sorted by name.

Returns `{name: string, path: string, active: boolean, size_mb: number}[]` — One table per database.

Since API version 2.

Example:

```lua
for _, db in ipairs(opensak.databases()) do
    print(db.name, db.active and "(active)" or "", db.size_mb .. " MB")
end
```

### opensak.database

```lua
opensak.database()
```

The name of the active database.

Returns `string` — Database name.

Since API version 2.

Example:

```lua
print("Working on " .. opensak.database())
```

### opensak.database_exists

```lua
opensak.database_exists(name)
```

Whether a database with this name is in the database list (exact, case-sensitive match).

Parameters:

- `name` (`string`) — Database name.

Returns `boolean` — true if it exists.

Since API version 2.

Example:

```lua
if not opensak.database_exists("CH_Zurich") then
    opensak.create_database("CH_Zurich")
end
```

### opensak.create_database

```lua
opensak.create_database(name)
```

Create a new, empty database in the default database folder and add it to the list. The active database does not change. Fails if the name is taken or its file already exists.

Parameters:

- `name` (`string`) — Name of the new database.

Returns `string` — The name actually used (surrounding spaces removed).

Since API version 2.

Example:

```lua
local name = opensak.create_database("CH_Zurich")
```

### opensak.switch_database

```lua
opensak.switch_database(name)
```

Make another database the active one, as the toolbar dropdown does. The active filter is cleared.

Parameters:

- `name` (`string`) — Database name.

Since API version 2.

Example:

```lua
opensak.switch_database("CH_Zurich")
print(opensak.count() .. " caches in " .. opensak.database())
```

### opensak.move_caches

```lua
opensak.move_caches(target [, options])
```

Move caches from the active database to another one, with all logs, waypoints, attributes, trackables and notes (like Database → Move caches). By default the caches of the active filter. When a cache already exists in the target, `if_exists` decides: `"newer"` (default) replaces it only if the active database's copy was imported later, `"replace"` always, `"skip"` never. A cache not written to the target stays in the active database.

Parameters:

- `target` (`string`) — Name of the target database (not the active one).
- `options` (`opensak.TransferOptions`, optional) — codes, if_exists.

Returns `integer` — Number of caches moved.

Since API version 2.

Example:

```lua
local n = opensak.move_caches("CH_Zurich")
print(n .. " caches moved")
```

### opensak.copy_caches

```lua
opensak.copy_caches(target [, options])
```

Like opensak.move_caches(), but the caches stay in the active database.

Parameters:

- `target` (`string`) — Name of the target database (not the active one).
- `options` (`opensak.TransferOptions`, optional) — codes, if_exists.

Returns `integer` — Number of caches copied.

Since API version 2.

Example:

```lua
local n = opensak.copy_caches("CH_Zurich", { codes = {"GC1", "GC2"}, if_exists = "replace" })
```

### opensak.set_corrected

```lua
opensak.set_corrected(code, lat, lon)
opensak.set_corrected(code, coords)
```

Set corrected coordinates, either as decimal degrees or as one coordinate string in any format OpenSAK understands (DMM, DMS, decimal degrees).

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `lat` (`number|string`) — Latitude in decimal degrees.
- `lon` (`number|string`) — Longitude in decimal degrees.
- `coords` (`string`) — Coordinates, e.g. "N47 22.123 E008 32.456".

Returns `boolean` — false if the cache is not in the database.

Since API version 1.

Example:

```lua
opensak.set_corrected("GC12345", 47.36872, 8.54093)
opensak.set_corrected("GC12345", "N47 22.123 E008 32.456")
```

### opensak.clear_corrected

```lua
opensak.clear_corrected(code)
```

Remove the corrected coordinates of a cache.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".

Returns `boolean` — false if the cache is not in the database.

Since API version 1.

Example:

```lua
opensak.clear_corrected("GC12345")
```

### opensak.read_csv

```lua
opensak.read_csv(path [, sep])
```

Read a CSV file (UTF-8) into an array of rows keyed by the header line. A relative path is resolved against the macro file's folder. If the file's folder has no read permission (Settings → Folder permissions), OpenSAK asks the user to allow it for this run or always. The file may be at most 10 MB.

Parameters:

- `path` (`string`) — The CSV file.
- `sep` (`string`, optional) — Separator character; detected among , ; and tab if omitted.

Returns `table<string, string>[]` — One table per data row, keyed by header.

Since API version 1.

Example:

```lua
for _, row in ipairs(opensak.read_csv("solved.csv")) do
    opensak.set_corrected(row.code, row.coords)
end
```

### opensak.export_file

```lua
opensak.export_file(setting [, folder])
```

Export the caches of the active filter with a saved export setting (File → Export → GPX/LOC/GGZ: format, folder, file name, if the file exists, corrected coordinates, max. caches). The file name variables are filled in as in the dialog, {filter} with the name of the active filter and {center} with the active centre point. The file goes into *folder* if given, else into the setting's folder. That folder needs write permission (Settings → Folder permissions); for an unapproved one the user is asked first. Nothing is written when no cache with coordinates is shown, or when the file exists and the setting says skip (or ask, and the user answers No).

Parameters:

- `setting` (`string`) — Name of the saved export setting.
- `folder` (`string`, optional) — Folder to write to instead of the setting's folder, e.g. opensak.temp_dir().

Returns `string?, integer?` — The file written and the number of caches in it; nil if nothing was written.

Since API version 2.

Example:

```lua
local path, n = opensak.export_file("GPX Export")
if path then print(n .. " caches → " .. path) end
```

### opensak.export_gpx

```lua
opensak.export_gpx(spec)
```

Export the caches of the active filter without a saved export setting. `path` is the file to write; its name may use the variables of the export dialog ({database}, {filter}, {center}, {date}, {count}, ...), and a relative path is resolved against the macro file's folder. Its folder needs write permission, like opensak.export_file(). `rename(c)` and `description(c)` are called with each cache table (see Cache fields) and return the name and the waypoint description to write (nil keeps the default). With `target = "device"`, the file goes into the GPX or GGZ folder of the connected Garmin device instead (`path` is then only the file name; MTP devices are not supported yet). Nothing is written when no cache with coordinates is shown, or when the file exists and `if_exists` says skip (or ask, and the user answers No).

Parameters:

- `spec` (`opensak.ExportSpec`) — What to export and where.

Returns `string?, integer?` — The file written and the number of caches in it; nil if nothing was written.

Since API version 2.

Example:

```lua
local path, n = opensak.export_gpx{
  path = opensak.temp_dir() .. "/{database}_{filter}.gpx",
  rename = function(c) return c.difficulty .. "/" .. c.terrain .. " " .. c.name end,
  pois = { child_waypoints = false },
}
if path then print(n .. " caches → " .. path) end
```

### opensak.confirm

```lua
opensak.confirm(message)
```

Ask the user a Yes/No question.

Parameters:

- `message` (`string`) — The question.

Returns `boolean` — true on Yes.

Since API version 1.

Example:

```lua
if not opensak.confirm("Update 12 caches?") then return end
```

### opensak.choose_file

```lua
opensak.choose_file([title] [, filter] [, mode])
```

Let the user pick a file in a file dialog. The picked file may be used for the rest of this run without a folder permission: read with mode "open" (the default), written with mode "save". OpenSAK's own settings and database files cannot be picked. The dialog starts in the macro file's folder.

Parameters:

- `title` (`string`, optional) — Dialog title.
- `filter` (`string`, optional) — File types, e.g. "CSV files (*.csv);;All files (*)".
- `mode` (`"open"|"save"`, optional) — "open" picks an existing file to read (default), "save" a file to write (the dialog asks before replacing an existing one).

Returns `string?` — Full path of the picked file, or nil if cancelled.

Since API version 2.

Example:

```lua
local path = opensak.choose_file("Solved puzzles", "CSV files (*.csv)")
if not path then return end      -- cancelled
for _, row in ipairs(opensak.read_csv(path)) do
    opensak.set_corrected(row.code, row.coords)
end
```

### opensak.temp_dir

```lua
opensak.temp_dir()
```

OpenSAK's folder inside the system temp folder (read and write permission by default), without a trailing separator. "/" works as separator on every platform.

Returns `string` — Folder path.

Since API version 1.

Example:

```lua
local rows = opensak.read_csv(opensak.temp_dir() .. "/solved.csv")
```

### opensak.macros_dir

```lua
opensak.macros_dir()
```

OpenSAK's macros folder (read permission by default), without a trailing separator.

Returns `string` — Folder path.

Since API version 1.

Example:

```lua
local rows = opensak.read_csv(opensak.macros_dir() .. "/data/solved.csv")
```

### opensak.version

```lua
opensak.version()
```

The OpenSAK version this macro runs in.

Returns `string` — Version, e.g. "1.21.0-beta.3".

Since API version 2.

Example:

```lua
print("Running in OpenSAK " .. opensak.version())
```

### opensak.sleep

```lua
opensak.sleep(ms)
```

Pause the macro. One pause lasts at most 10 s and all pauses of a run together at most 60 s; a cancelled macro stops at its next pause.

Parameters:

- `ms` (`number`) — Milliseconds.

Since API version 2.

Example:

```lua
opensak.sleep(500)
```

### opensak.coords.parse

```lua
opensak.coords.parse(text)
```

Parse a coordinate string in any format OpenSAK understands (DMM, DMS, decimal degrees).

Parameters:

- `text` (`string`) — The coordinates.

Returns `number?, number?` — Latitude and longitude, or nil if the text cannot be parsed.

Since API version 2.

Example:

```lua
local lat, lon = opensak.coords.parse("N47 22.123 E008 32.456")
if not lat then error("not a coordinate") end
```

### opensak.coords.format

```lua
opensak.coords.format(lat, lon [, fmt])
opensak.coords.format(coords [, fmt])
```

Format coordinates. Formats: `"dmm"` (default), `"dms"`, `"dd"`, `"utm"`, `"ch1903"` (Swiss LV03) and `"ch1903+"` (Swiss LV95). The Swiss formats are only meaningful in and around Switzerland.

Parameters:

- `lat` (`number`) — Latitude in decimal degrees.
- `lon` (`number`) — Longitude in decimal degrees.
- `fmt` (`string`, optional) — Output format, "dmm" if omitted.
- `coords` (`string`) — Coordinates, e.g. "N47 22.123 E008 32.456".

Returns `string` — E.g. "N47 22.123  E008 32.456" or "32T E 465123 N 5247123".

Since API version 2.

Example:

```lua
print(opensak.coords.format(47.36872, 8.54093, "utm"))
print(opensak.coords.format("N47 22.123 E008 32.456", "ch1903"))
```

### opensak.coords.distance

```lua
opensak.coords.distance(lat1, lon1, lat2, lon2)
opensak.coords.distance(a, b)
```

Great-circle distance between two points.

Parameters:

- `lat1` (`number`) — Latitude of the first point.
- `lon1` (`number`) — Longitude of the first point.
- `lat2` (`number`) — Latitude of the second point.
- `lon2` (`number`) — Longitude of the second point.
- `a` (`string`) — First point as a coordinate string.
- `b` (`string`) — Second point as a coordinate string.

Returns `number` — Distance in km.

Since API version 2.

Example:

```lua
local km = opensak.coords.distance("N47 22.123 E008 32.456",
                                   "N47 23.000 E008 33.000")
```

### opensak.coords.bearing

```lua
opensak.coords.bearing(lat1, lon1, lat2, lon2)
opensak.coords.bearing(a, b)
```

Initial bearing from the first point to the second.

Parameters:

- `lat1` (`number`) — Latitude of the first point.
- `lon1` (`number`) — Longitude of the first point.
- `lat2` (`number`) — Latitude of the second point.
- `lon2` (`number`) — Longitude of the second point.
- `a` (`string`) — First point as a coordinate string.
- `b` (`string`) — Second point as a coordinate string.

Returns `number` — Degrees, 0 = North, clockwise.

Since API version 2.

Example:

```lua
local deg = opensak.coords.bearing(47.36872, 8.54093, 47.38333, 8.55)
```

### opensak.coords.project

```lua
opensak.coords.project(lat, lon, bearing, km)
opensak.coords.project(coords, bearing, km)
```

Waypoint projection: the point a given distance away in a given direction.

Parameters:

- `lat` (`number`) — Latitude in decimal degrees.
- `lon` (`number`) — Longitude in decimal degrees.
- `bearing` (`number`) — Direction in degrees, 0 = North, clockwise.
- `km` (`number`) — Distance in km.
- `coords` (`string`) — Coordinates, e.g. "N47 22.123 E008 32.456".

Returns `number, number` — Latitude and longitude of the projected point.

Since API version 2.

Example:

```lua
local lat, lon = opensak.coords.project("N47 22.123 E008 32.456", 45, 0.25)
print(opensak.coords.format(lat, lon))
```

### opensak.coords.midpoint

```lua
opensak.coords.midpoint(lat1, lon1, lat2, lon2)
opensak.coords.midpoint(a, b)
```

The point halfway between two points (along the great circle).

Parameters:

- `lat1` (`number`) — Latitude of the first point.
- `lon1` (`number`) — Longitude of the first point.
- `lat2` (`number`) — Latitude of the second point.
- `lon2` (`number`) — Longitude of the second point.
- `a` (`string`) — First point as a coordinate string.
- `b` (`string`) — Second point as a coordinate string.

Returns `number, number` — Latitude and longitude of the midpoint.

Since API version 2.

Example:

```lua
local lat, lon = opensak.coords.midpoint(47.0, 8.0, 48.0, 9.0)
```

### opensak.coords.inside

```lua
opensak.coords.inside(lat, lon, polygon)
opensak.coords.inside(coords, polygon)
```

Whether a point lies inside a polygon. The polygon is either a file (GPX track/route/waypoints, KML, or a text file with one coordinate per line; read permission needed, relative paths are resolved against the macro file's folder) or a table of points, each a coordinate string, `{lat, lon}` or `{lat = ..., lon = ...}`. Edges are straight lines in latitude/longitude, as in the line/polygon filter.

Parameters:

- `lat` (`number`) — Latitude in decimal degrees.
- `lon` (`number`) — Longitude in decimal degrees.
- `polygon` (`string|table`) — Polygon file path, or a table of points.
- `coords` (`string`) — Coordinates, e.g. "N47 22.123 E008 32.456".

Returns `boolean` — true if the point is inside.

Since API version 2.

Example:

```lua
local area = { "N47 20 E008 30", "N47 25 E008 30", "N47 25 E008 40" }
print(opensak.coords.inside(47.37, 8.54, area))
```

### opensak.coords.location

```lua
opensak.coords.location(lat, lon)
opensak.coords.location(coords)
```

Offline reverse geocoding with the boundary data used by Update location. A field is nil where no region matches.

Parameters:

- `lat` (`number`) — Latitude in decimal degrees.
- `lon` (`number`) — Longitude in decimal degrees.
- `coords` (`string`) — Coordinates, e.g. "N47 22.123 E008 32.456".

Returns `{country: string?, state: string?, county: string?}?` — nil if the boundary data is not installed.

Since API version 2.

Example:

```lua
local loc = opensak.coords.location(47.36872, 8.54093)
if loc then print(loc.country, loc.state, loc.county) end
```

### opensak.re.find

```lua
opensak.re.find(text, pattern)
```

Search for the first match of a regular expression (Python syntax). Each call may run at most 2 s.

Parameters:

- `text` (`string`) — The text.
- `pattern` (`string`) — Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.

Returns `string?, string?...` — The whole match followed by the captures (nil for a group that did not take part), or nil if there is no match.

Since API version 2.

Example:

```lua
local whole, n, e = opensak.re.find(desc, [[N\s*(\d+)\D+E\s*(\d+)]])
```

### opensak.re.match

```lua
opensak.re.match(text, pattern)
```

Whether the text contains a match. Use `^` and `$` to match the whole text.

Parameters:

- `text` (`string`) — The text.
- `pattern` (`string`) — Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.

Returns `boolean` — true if the pattern matches.

Since API version 2.

Example:

```lua
if opensak.re.match(c.name, [[(?i)^bonus]]) then print("bonus cache") end
```

### opensak.re.findall

```lua
opensak.re.findall(text, pattern)
```

All non-overlapping matches. Without capture groups each item is the whole match, with one group it is the capture, with several it is an array of the captures.

Parameters:

- `text` (`string`) — The text.
- `pattern` (`string`) — Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.

Returns `string[]|string[][]` — The matches.

Since API version 2.

Example:

```lua
for _, number in ipairs(opensak.re.findall("A=3, B=12", [[\d+]])) do
    print(number)
end
```

### opensak.re.replace

```lua
opensak.re.replace(text, pattern, repl [, count])
```

Replace matches. In a replacement string, `\1` or `\g<name>` insert a capture. A replacement function gets the whole match and the captures and returns the new text (nil or false keeps the match).

Parameters:

- `text` (`string`) — The text.
- `pattern` (`string`) — Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.
- `repl` (`string|fun(match: string, ...: string?): any`) — Replacement text or function.
- `count` (`integer`, optional) — Replace at most this many matches; all if omitted.

Returns `string, integer` — The new text and the number of replacements.

Since API version 2.

Example:

```lua
local text = opensak.re.replace("A=3 B=12", [[\d+]], function(n)
    return n * 2
end)
```

### opensak.re.split

```lua
opensak.re.split(text, pattern)
```

Split the text at every non-empty match.

Parameters:

- `text` (`string`) — The text.
- `pattern` (`string`) — Regular expression (Python syntax); a long bracket string [[...]] avoids doubling backslashes.

Returns `string[]` — The pieces between the matches.

Since API version 2.

Example:

```lua
local parts = opensak.re.split("a, b;c", [[[,;]\s*]])  -- {"a", "b", "c"}
```

### opensak.text.html_to_text

```lua
opensak.text.html_to_text(html)
```

Turn HTML (e.g. a cache description) into plain text: tags removed, entities decoded, block elements and `<br>` become line breaks.

Parameters:

- `html` (`string`) — The HTML.

Returns `string` — The text.

Since API version 2.

Example:

```lua
print(opensak.text.html_to_text("<p>Stage&nbsp;1:<br>N47 22.123</p>"))
```

### opensak.text.rot13

```lua
opensak.text.rot13(text)
```

ROT13 as used for hints. Text in [square brackets] stays unchanged, like on geocaching.com.

Parameters:

- `text` (`string`) — The text.

Returns `string` — The decoded (or encoded) text.

Since API version 2.

Example:

```lua
print(opensak.text.rot13("haqre gur fgbar [Ubhfr]"))
```

### opensak.text.digit_sum

```lua
opensak.text.digit_sum(n)
```

Cross sum: the sum of all digits; other characters are ignored.

Parameters:

- `n` (`number|string`) — The number or text.

Returns `integer` — Sum of the digits.

Since API version 2.

Example:

```lua
print(opensak.text.digit_sum(1987))  -- 25
```

### opensak.text.word_value

```lua
opensak.text.word_value(text)
```

Letter value sum (A=1 … Z=26). Accents are dropped (Ä counts as A); other characters are ignored.

Parameters:

- `text` (`string`) — The text.

Returns `integer` — Sum of the letter values.

Since API version 2.

Example:

```lua
print(opensak.text.word_value("Geocache"))  -- 47
```

### opensak.text.normalize_name

```lua
opensak.text.normalize_name(s)
```

Make a string safe as a database or file name on every platform: characters such as `\ / : * ? " < > |` become `_`, white space is collapsed, leading/trailing dots and spaces are removed and the length is limited to 100. Never empty.

Parameters:

- `s` (`string`) — The name.

Returns `string` — The safe name.

Since API version 2.

Example:

```lua
local name = opensak.text.normalize_name("CH: Zürich / Nord")  -- "CH_ Zürich _ Nord"
```

### opensak.date.parse

```lua
opensak.date.parse(text [, fmt])
```

Parse a date into seconds since the epoch, the same kind of value as `os.time()`. Without a format, ISO 8601 (`"2026-10-06"`, `"2026-10-06T14:30:00Z"`) and `"06.10.2026 [14:30[:00]]"` are understood. Times without a zone are local time.

Parameters:

- `text` (`string`) — The text.
- `fmt` (`string`, optional) — strptime format, e.g. "%d/%m/%Y".

Returns `integer?` — Seconds since the epoch, or nil if the text does not parse.

Since API version 2.

Example:

```lua
local t = opensak.date.parse("2026-10-06")
local t2 = opensak.date.parse("10/06/2026", "%m/%d/%Y")
```

### opensak.date.format

```lua
opensak.date.format(t [, fmt])
```

Format seconds since the epoch (e.g. from `os.time()` or `opensak.date.parse()`) in local time.

Parameters:

- `t` (`number`) — Seconds since the epoch.
- `fmt` (`string`, optional) — strftime format, "%Y-%m-%d" if omitted.

Returns `string` — The formatted date.

Since API version 2.

Example:

```lua
print(opensak.date.format(os.time(), "%d.%m.%Y %H:%M"))
```

## Filter keys

Keys understood by `opensak.filter{}`, combined with AND (or OR with `mode`).

| Key | Value | Meaning |
|---|---|---|
| `type` | `"Traditional" \| {"Traditional", "Multi-cache", ...}` | Cache type(s); the " Cache" suffix may be left out. |
| `container` | `"Small" \| {"Micro", "Small", ...}` | Container size(s). |
| `difficulty` | `2 \| {1, 2.5}` | Exact value or {min, max}. |
| `terrain` | `2 \| {1, 2.5}` | Exact value or {min, max}. |
| `found` | `true \| false` | Only found or only unfound caches. |
| `available` | `true` | Only available caches (not disabled or archived). |
| `archived` | `true \| false` | Only archived caches, or only caches that are not archived. |
| `corrected` | `true \| false` | With or without corrected coordinates. |
| `user_flag`, `locked`, `dnf`, `ftf`, `premium` | `true \| false` | That flag set, or not set. |
| `has_trackables` | `true \| false` | With or without trackables in the cache. |
| `owned` | `true \| false` | Owned by you (owner = your geocaching.com username in Settings), or not. |
| `near` | `{lat = 47.37, lon = 8.54, km = 10} \| {point = "Home", km = 10}` | Within `km` of a coordinate or of a saved centre point. |
| `distance` | `25 \| {5, 25} \| {10, nil}` | Km from the active centre point: at most a number, or {min, max} where nil leaves a side open. |
| `bearing` | `{45, 135} \| {315, 45}` | Bearing from the active centre point, clockwise from the first to the second value. |
| `favorites`, `elevation` | `10 \| {10, nil} \| {nil, 500}` | Favourite points / elevation in metres: exact value, or {min, max} where nil leaves a side open. |
| `hidden`, `found_date`, `last_gpx_update` | `{"2020-01-01", "2020-12-31"} \| {nil, "2026-09-01"}` | Date range {from, to}, both inclusive, "YYYY-MM-DD"; nil leaves a side open. `last_gpx_update` also takes a time, "2026-09-01T18:00". |
| `attributes` | `"Dogs" \| {"Dogs", "-Night cache", 13}` | Attributes the cache must have (all of them): English name or Groundspeak id; a leading `-` means the attribute's "no" form (e.g. "-Dogs" = no dogs allowed). |
| `name`, `code`, `owner`, `placed_by`, `country`, `state`, `county`, `user_data1`, `user_data2`, `user_data3`, `user_data4`, `gc_note`, `note` | `"text"` | "Contains" match on that field (`note` = your local note). |
| `text` | `"Brücke"` | Full-text search in description, logs and notes. |
| `polygon` | `"area.kml" \| {{47.1, 8.1}, {47.2, 8.1}, {47.2, 8.3}}` | Inside a polygon: a file (as for opensak.coords.inside) or a table of points. |
| `codes` | `{"GC1", "GC2"}` | Exactly these GC codes, e.g. from an opensak.sql() result. |
| `where` | `"SQL WHERE clause"` | Raw clause against the caches table. |
| `mode` | `"OR"` | How the criteria are combined (default AND). |
| `label` | `"text"` | Shown in the toolbar (optional, default "Macro"). |

`opensak.caches{}` also takes `fields`, an array of the cache fields to load (`code` is always included), and `database`.

## Reading another database

`opensak.cache()`, `opensak.caches{}`, `opensak.description()`, `opensak.sql()`, `opensak.sql_each()`, `opensak.tables()` and `opensak.columns()` take a `database` option with the name of a database from `opensak.databases()`. It reads that database without switching to it, so the active filter stays. The database is opened read-only. A database last opened by an older OpenSAK version must be opened once first, so its schema is updated. Naming the active database is the same as leaving `database` out.

## Cache fields

Keys of the table returned by `opensak.cache()`, `opensak.current()` and `opensak.caches()`. The table is a snapshot; missing data is `nil`.

| Field | Type | Meaning |
|---|---|---|
| `code` | `string` | GC code, e.g. "GC12345". |
| `name` | `string` | Cache name. |
| `type` | `string` | Cache type, e.g. "Traditional Cache". |
| `container` | `string?` | Container size, e.g. "Small". |
| `lat` | `number` | Posted latitude (decimal degrees). |
| `lon` | `number` | Posted longitude (decimal degrees). |
| `difficulty` | `number?` | Difficulty 1–5. |
| `terrain` | `number?` | Terrain 1–5. |
| `owner` | `string?` | Owner name. |
| `placed_by` | `string?` | Placed-by name as shown on the listing. |
| `hidden` | `string?` | Hidden date, "YYYY-MM-DD". |
| `found` | `boolean` | Found by you. |
| `found_date` | `string?` | Your find date, "YYYY-MM-DD". |
| `dnf` | `boolean` | Your latest log is a Didn't find it. |
| `dnf_date` | `string?` | Date of your DNF, "YYYY-MM-DD". |
| `ftf` | `boolean` | You were first to find. |
| `available` | `boolean` | Not disabled. |
| `archived` | `boolean` | Archived. |
| `premium` | `boolean` | Premium-member only. |
| `country` | `string?` | Country. |
| `state` | `string?` | State / region. |
| `county` | `string?` | County. |
| `distance` | `number?` | Distance from the active centre point in km. |
| `bearing` | `number?` | Bearing from the active centre point in degrees. |
| `elevation` | `number?` | Elevation in metres. |
| `favorite_points` | `integer?` | Favourite points (nil until known). |
| `find_count` | `integer?` | Number of Found it logs by anyone. |
| `user_flag` | `boolean` | User flag. |
| `user_sort` | `integer?` | User sort value. |
| `user_data` | `string[]` | User data fields 1–4; "" when empty. |
| `color` | `string?` | Colour tag, e.g. "#FF5733". |
| `locked` | `boolean` | Locked against import changes. |
| `watch` | `boolean` | On the watchlist. |
| `note` | `string?` | Your local note. |
| `gc_note` | `string?` | Personal note from geocaching.com. |
| `hint` | `string?` | Hint text as imported. |
| `url` | `string?` | Listing URL. |
| `corrected` | `{lat: number, lon: number}?` | Corrected coordinates; nil if the cache is not solved. |
| `waypoint_count` | `integer` | Number of additional waypoints. |
| `log_count` | `integer` | Number of logs stored. |
| `trackable_count` | `integer` | Number of trackables in the cache. |
| `last_log_date` | `string?` | Date of the latest log, "YYYY-MM-DD". |
| `last_gpx_update` | `string?` | When an import last touched the cache, "YYYY-MM-DDTHH:MM:SS"; finds caches the latest Pocket Query did not refresh. |

## Example macros

Ready-to-use scripts to copy and adapt are in [`macros/examples/`](../../macros/examples/). Each one starts with a comment explaining what it does and which files it expects.

- [`corrected_coords_from_csv.lua`](../../macros/examples/corrected_coords_from_csv.lua) — set corrected coordinates from a CSV file
- [`export_filters_to_gpx.lua`](../../macros/examples/export_filters_to_gpx.lua) — one GPX file per saved filter

## Editor support (VS Code)

[`macros/types/opensak.lua`](../../macros/types/opensak.lua) describes this API for the [Lua Language Server](https://luals.github.io/) (VS Code extension "Lua" by sumneko): autocompletion, parameter hints and these docs while you type. Macros inside the OpenSAK repository pick it up automatically. For macros in another folder, put a `.luarc.json` next to them that points at the folder holding the stub:

```json
{
  "runtime.version": "Lua 5.4",
  "workspace.library": ["C:/path/to/OpenSAK/macros/types"]
}
```

## Globals

### print

```lua
print(...)
```

Write the arguments, separated by tabs, to the macro output pane.
