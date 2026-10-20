<!-- Generated from src/opensak/macro/runtime.py by scripts/generate_macro_api_docs.py — do not edit by hand. -->

# OpenSAK Lua macro API

API version: **3** (`opensak.api_version()`).

Macros are Lua 5.4 scripts run in a sandbox. They talk to OpenSAK through the global `opensak` table. File access is limited to the folders listed in Settings → Folder permissions. When a macro needs a file in another folder, OpenSAK asks the user whether to allow that folder for this run only or always, or to deny it; reading and writing are asked separately. OpenSAK's own settings and database files are never accessible.

See [Example macros](#example-macros) for complete scripts and [Editor support](#editor-support) for autocompletion in VS Code, Notepad++ and UltraEdit.

## Functions

| Function | Since |
|---|---|
| [`opensak.api_version`](#opensakapiversion) | 1 |
| [`opensak.filter`](#opensakfilter) | 1 |
| [`opensak.filter_name`](#opensakfiltername) | 2 |
| [`opensak.sort`](#opensaksort) | 2 |
| [`opensak.filter_profile`](#opensakfilterprofile) | 1 |
| [`opensak.clear_filter`](#opensakclearfilter) | 1 |
| [`opensak.save_filter`](#opensaksavefilter) | 3 |
| [`opensak.restore_filter`](#opensakrestorefilter) | 3 |
| [`opensak.count`](#opensakcount) | 1 |
| [`opensak.profiles`](#opensakprofiles) | 1 |
| [`opensak.cache`](#opensakcache) | 2 |
| [`opensak.caches`](#opensakcaches) | 2 |
| [`opensak.current`](#opensakcurrent) | 2 |
| [`opensak.selected`](#opensakselected) | 2 |
| [`opensak.codes`](#opensakcodes) | 2 |
| [`opensak.description`](#opensakdescription) | 2 |
| [`opensak.sql`](#opensaksql) | 2, changed in 3 |
| [`opensak.sql_each`](#opensaksqleach) | 2 |
| [`opensak.tables`](#opensaktables) | 2, changed in 3 |
| [`opensak.columns`](#opensakcolumns) | 2, changed in 3 |
| [`opensak.macro_db`](#opensakmacrodb) | 3 |
| [`opensak.attach`](#opensakattach) | 3 |
| [`opensak.detach`](#opensakdetach) | 3 |
| [`opensak.attached`](#opensakattached) | 3 |
| [`opensak.databases`](#opensakdatabases) | 2 |
| [`opensak.database`](#opensakdatabase) | 2 |
| [`opensak.database_exists`](#opensakdatabaseexists) | 2 |
| [`opensak.create_database`](#opensakcreatedatabase) | 2 |
| [`opensak.switch_database`](#opensakswitchdatabase) | 2 |
| [`opensak.move_caches`](#opensakmovecaches) | 2 |
| [`opensak.copy_caches`](#opensakcopycaches) | 2 |
| [`opensak.import_gsak`](#opensakimportgsak) | 3 |
| [`opensak.set_corrected`](#opensaksetcorrected) | 1 |
| [`opensak.clear_corrected`](#opensakclearcorrected) | 1 |
| [`opensak.update`](#opensakupdate) | 3 |
| [`opensak.insert`](#opensakinsert) | 3 |
| [`opensak.delete`](#opensakdelete) | 3 |
| [`opensak.logs`](#opensaklogs) | 3 |
| [`opensak.insert_log`](#opensakinsertlog) | 3 |
| [`opensak.update_log`](#opensakupdatelog) | 3 |
| [`opensak.delete_log`](#opensakdeletelog) | 3 |
| [`opensak.waypoints`](#opensakwaypoints) | 3 |
| [`opensak.insert_waypoint`](#opensakinsertwaypoint) | 3 |
| [`opensak.update_waypoint`](#opensakupdatewaypoint) | 3 |
| [`opensak.delete_waypoint`](#opensakdeletewaypoint) | 3 |
| [`opensak.attributes`](#opensakattributes) | 3 |
| [`opensak.insert_attribute`](#opensakinsertattribute) | 3 |
| [`opensak.update_attribute`](#opensakupdateattribute) | 3 |
| [`opensak.delete_attribute`](#opensakdeleteattribute) | 3 |
| [`opensak.trackables`](#opensaktrackables) | 3 |
| [`opensak.insert_trackable`](#opensakinserttrackable) | 3 |
| [`opensak.update_trackable`](#opensakupdatetrackable) | 3 |
| [`opensak.delete_trackable`](#opensakdeletetrackable) | 3 |
| [`opensak.sql_write`](#opensaksqlwrite) | 3 |
| [`opensak.transaction`](#opensaktransaction) | 3 |
| [`opensak.in_transaction`](#opensakintransaction) | 3 |
| [`opensak.resync`](#opensakresync) | 3 |
| [`opensak.read_csv`](#opensakreadcsv) | 1 |
| [`opensak.write_text`](#opensakwritetext) | 3 |
| [`opensak.read_text`](#opensakreadtext) | 3 |
| [`opensak.file_exists`](#opensakfileexists) | 3 |
| [`opensak.folder_exists`](#opensakfolderexists) | 3 |
| [`opensak.list_dir`](#opensaklistdir) | 3 |
| [`opensak.export_file`](#opensakexportfile) | 2 |
| [`opensak.export_gpx`](#opensakexportgpx) | 2 |
| [`opensak.export_poi`](#opensakexportpoi) | 3 |
| [`opensak.confirm`](#opensakconfirm) | 1 |
| [`opensak.choose_file`](#opensakchoosefile) | 2 |
| [`opensak.choose_folder`](#opensakchoosefolder) | 3 |
| [`opensak.message`](#opensakmessage) | 3 |
| [`opensak.input`](#opensakinput) | 3 |
| [`opensak.choose`](#opensakchoose) | 3 |
| [`opensak.form`](#opensakform) | 3 |
| [`opensak.progress`](#opensakprogress) | 3 |
| [`opensak.select`](#opensakselect) | 3 |
| [`opensak.refresh`](#opensakrefresh) | 3 |
| [`opensak.temp_dir`](#opensaktempdir) | 1 |
| [`opensak.macros_dir`](#opensakmacrosdir) | 1 |
| [`opensak.version`](#opensakversion) | 2 |
| [`opensak.run_macro`](#opensakrunmacro) | 3 |
| [`opensak.sleep`](#opensaksleep) | 2 |
| [`opensak.log`](#opensaklog) | 3 |
| [`opensak.exit`](#opensakexit) | 3 |
| [`opensak.coords.parse`](#opensakcoordsparse) | 2, changed in 3 |
| [`opensak.coords.format`](#opensakcoordsformat) | 2, changed in 3 |
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
| [`opensak.html.escape`](#opensakhtmlescape) | 3 |
| [`opensak.html.table`](#opensakhtmltable) | 3 |
| [`opensak.html.tiles`](#opensakhtmltiles) | 3 |
| [`opensak.html.columns`](#opensakhtmlcolumns) | 3 |
| [`opensak.html.page`](#opensakhtmlpage) | 3 |
| [`opensak.date.parse`](#opensakdateparse) | 2 |
| [`opensak.date.format`](#opensakdateformat) | 2 |
| [`opensak.settings.load`](#opensaksettingsload) | 3 |
| [`opensak.settings.save`](#opensaksettingssave) | 3 |
| [`opensak.settings.clear`](#opensaksettingsclear) | 3 |

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

### opensak.save_filter

```lua
opensak.save_filter()
```

The active filter (with its name, and any quick filter or search box) as a string, to put back later with opensak.restore_filter() — e.g. to leave the user's filter as it was after the macro filtered for its own work. Like GSAK's SaveFilter(). The sort order is not included. The string can be stored with opensak.settings.save(); don't build or change it.

Returns `string` — The saved filter; when no filter is active, restoring it clears the filter.

Since API version 3.

Example:

```lua
local saved = opensak.save_filter()
opensak.filter{ found = false }
-- ... work on the unfound caches ...
opensak.restore_filter(saved)
```

### opensak.restore_filter

```lua
opensak.restore_filter(saved)
```

Apply a filter saved with opensak.save_filter(), like GSAK's RestoreFilter(). As with opensak.filter{}, the view is left unchanged when nothing matches. A filter saved while none was active clears the filter.

Parameters:

- `saved` (`string`) — A string from opensak.save_filter().

Returns `integer` — Number of matching caches (0 = view unchanged, unless the saved filter was empty).

Since API version 3.

Example:

```lua
local saved = opensak.save_filter()
opensak.filter{ found = false }
opensak.restore_filter(saved)
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

Run a read-only SQL query (SQLite) against the active database (or the one named by the `database` option) and return all rows. Only reading statements are allowed, except on TEMP tables: a macro may create, fill, change and drop its own TEMP tables, views and indexes (`CREATE TEMP TABLE ...`) and use them in any query, opensak.sql_write() included. They are never stored in the database and are gone when the macro ends or calls opensak.switch_database(); changes to them inside opensak.transaction() are undone with it. A TEMP table may not take the name of a table of the database. Column names follow the database schema, which may change between versions (see opensak.tables() and opensak.columns()). Use `AS` to name computed columns. NULL values are nil. At most 100,000 rows; use opensak.sql_each() for more. A query is aborted after 60 s.

Parameters:

- `query` (`string`) — One SQL statement.
- `params` (`table`, optional) — Values for `?` placeholders ({ v1, v2 }) or for `:name` placeholders ({ name = v }); `nil` or `{}` for none.
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `table<string, any>[]` — One table per row, keyed by column name.

Since API version 2. Changed in API version 3: TEMP tables, views and indexes.

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

The tables and views of the active database (or the one named by the `database` option), for use with opensak.sql(). The macro's TEMP tables and views are included.

Parameters:

- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `string[]` — Table and view names, sorted.

Since API version 2. Changed in API version 3: lists the macro's TEMP tables and views, the tables of opensak.macro_db() as `macro.<name>` and those of opensak.attach() as `<schema>.<name>`.

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

Returns `{name: string, type: string, required: boolean}[]` — Column names and SQL types, in table order; `required`: NOT NULL without a default, so an INSERT must give it.

Since API version 2. Changed in API version 3: added `required`.

Example:

```lua
for _, c in ipairs(opensak.columns("caches")) do print(c.name, c.type) end
```

### opensak.macro_db

```lua
opensak.macro_db()
```

Attach the macro's own SQLite database — a file named after the macro file with the extension .db3, in the macro's folder, created if missing — to the SQL functions as schema `macro`. In it the macro has full access through opensak.sql(), sql_each() and sql_write(): CREATE, DROP, ALTER, INSERT, UPDATE and DELETE on `macro.<name>` tables, indexes, views and triggers, and `PRAGMA macro.user_version`. Its data stays between runs. One statement can read and write the active database and the macro database together, e.g. `INSERT INTO macro.seen SELECT gc_code FROM caches`; the rules for the active database stay the same. Unqualified names mean the active database first, so create with `macro.` in front. opensak.tables() lists its tables as `macro.<name>`. The macro's folder must be writable for macros, like any file a macro writes (Settings → Folder permissions; the macros folder is by default); otherwise OpenSAK asks the user to allow it, and a denial makes the call fail. The first call creates the file, even if nothing is written to it, and prints its path in the macro output. OpenSAK remembers the file, and its backups include it in `macro_databases/`, wherever it is. If the folder cannot be written the call fails; a read-only file can be read but not changed. Unlike opensak.update() it needs no approval to change databases (see Changing caches). Changes inside opensak.transaction() are kept or undone together with those of the active database. The SQL statement ATTACH itself stays refused. Cannot be called inside opensak.transaction(); stays attached after opensak.switch_database(). The macro must be saved, as the file is named after it. In a macro started with opensak.run_macro() it attaches the called macro's own file instead; the caller's file is attached again when the called macro returns.

Returns `string` — The path of the macro database file.

Since API version 3.

Example:

```lua
opensak.macro_db()
opensak.sql("CREATE TABLE IF NOT EXISTS macro.seen (code TEXT PRIMARY KEY, at TEXT)")
opensak.sql("INSERT OR IGNORE INTO macro.seen SELECT gc_code, date('now') FROM caches WHERE found = 1")
for _, r in ipairs(opensak.sql("SELECT c.gc_code, s.at FROM caches c "
        .. "JOIN macro.seen s ON s.code = c.gc_code LIMIT 5")) do
    print(r.gc_code, r.at)
end
```

### opensak.attach

```lua
opensak.attach(name [, schema])
```

Attach another OpenSAK database read-only to the SQL functions, under a schema name, so one opensak.sql() or sql_write() statement can read it together with the active database and opensak.macro_db(): `SELECT … FROM caches c JOIN found.caches f ON f.gc_code = c.gc_code`. Like GSAK's SQL `ATTACH`, which stays refused. Nothing can write to an attached database: the file is opened read-only, and SQL that would change it is refused. Without `schema`, the name is made from the database name: lower case, `_` for anything but letters and digits ("Found 2020" → `found_2020`). The schema name must be letters, digits and `_`; `main`, `temp`, `macro`, `companion` and names starting with `opensak_` are reserved. The active database cannot be attached, and neither can a database twice. Attaching the same database under the same name again does nothing. SQLite allows 10 attached databases at once, opensak.macro_db() included. The database is not updated to the current schema, so one last opened by an older OpenSAK version may lack columns; `opensak.columns("<schema>.caches")` shows what is there. Each attach prints a line in the macro output. Cannot be called inside opensak.transaction(). Stays attached after opensak.switch_database(), unless the database becomes the active one. Everything is detached when the macro ends. A macro started with opensak.run_macro() sees its caller's attached databases; what it attaches or detaches is undone when it returns. See Several databases in one query.

Parameters:

- `name` (`string`) — Database name, as in opensak.databases().
- `schema` (`string`, optional) — Schema name to use in SQL; default: made from the database name.

Returns `string` — The schema name.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.attach then`.

Example:

```lua
local found = opensak.attach("Found 2020", "found")
for _, r in ipairs(opensak.sql("SELECT c.gc_code, c.name FROM caches c "
        .. "JOIN found.caches f ON f.gc_code = c.gc_code")) do
    print(r.gc_code, r.name)
end
```

### opensak.detach

```lua
opensak.detach(schema)
```

Detach a database attached with opensak.attach(). Optional: everything is detached when the macro ends. Cannot be called inside opensak.transaction().

Parameters:

- `schema` (`string`) — The schema name opensak.attach() returned.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.attach then`.

Example:

```lua
opensak.detach("found")
```

### opensak.attached

```lua
opensak.attached()
```

The databases attached with opensak.attach(), in the order they were attached. `opensak` is always true: only OpenSAK databases can be attached so far.

Returns `{schema: string, name: string, path: string, opensak: boolean}[]` — One table per attached database.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.attach then`.

Example:

```lua
for _, a in ipairs(opensak.attached()) do print(a.schema, a.name) end
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

### opensak.import_gsak

```lua
opensak.import_gsak(source [, options])
```

Import GSAK databases without any question (like File → Import GSAK database, with the answers given up front) and return what was done. *source* is a GSAK backup .zip, a GSAK folder (the install / data folder with `gsak.db3` and `data/<name>/sqlite.db3`, its `data` folder, or one database's folder), a single `.db3` cache database (just that database), or `gsak.db3` (only user locations and saved filters). A relative path is resolved against the macro file's folder, and environment variables such as `%APPDATA%` are expanded; the folder needs read permission. Each GSAK database goes into the OpenSAK database of the same name, created if missing, unless `target` says otherwise. For an OpenSAK database that exists, `if_exists` decides: `"skip"` (default), `"merge"` (new caches added, existing ones updated, locked caches kept) or `"overwrite"` (emptied first). `filters` and `locations` also import the saved filters and user locations of `gsak.db3`; `true` skips the ones whose name exists when `if_exists` is `"skip"` and replaces them otherwise. Embedded note images are replaced by placeholders without asking. A database that fails is reported and the others are still imported. The summary is printed (unless `quiet`) and written to the log file.

Parameters:

- `source` (`string`) — GSAK backup .zip, GSAK folder or .db3 file.
- `options` (`opensak.GsakImportOptions`, optional) — databases, exclude, target, if_exists, create, filters, locations, quiet.

Returns `opensak.GsakImportReport` — What was imported, per database.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
local r = opensak.import_gsak("C:/Backups/GSAKBackup.zip", {
  databases = {"AllCH", "Holidays"}, if_exists = "merge",
  locations = true })
for _, db in ipairs(r.databases) do
  print(db.gsak, db.target, db.action, db.created or 0, db.updated or 0)
end
if not r.ok then opensak.exit(1) end
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

### opensak.update

```lua
opensak.update(code, fields)
```

Change fields of a cache in the active database. The keys are cache field names (see Changing caches); only the given fields change. `false` clears a field that is not a boolean. Fields OpenSAK maintains itself, such as `code`, `distance` or `log_count`, cannot be written. Locked caches can be changed too: the lock only protects from imports. Before a macro's first change, OpenSAK asks the user to allow that macro (see Changing caches).

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `fields` (`opensak.CacheUpdate`) — The fields to change.

Returns `boolean` — false if the cache is not in the database.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
opensak.update("GC12345", { user_flag = true, user_data = { [2] = "solved" } })
for c in opensak.caches{ found = true, fields = {"color"} } do
    if not c.color then opensak.update(c.code, { color = "#00AA00" }) end
end
```

### opensak.insert

```lua
opensak.insert(fields)
```

Add a new cache to the active database. `code`, `name`, `type`, `lat` and `lon` are required; any other writable field may be given too (see Changing caches). Fails if the code is already in the database. Needs the user's permission like opensak.update().

Parameters:

- `fields` (`opensak.CacheInsert`) — The new cache's fields.

Returns `string` — The cache code as stored (upper case).

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
opensak.insert{ code = "GC12345", name = "My bonus", type = "Unknown",
                lat = 47.36872, lon = 8.54093, user_flag = true }
```

### opensak.delete

```lua
opensak.delete(code)
opensak.delete(codes)
```

Delete caches from the active database, each with its logs, waypoints, attributes, trackables and user note, like Waypoint → Delete cache. Takes one GC code, or a list of them, e.g. opensak.codes() for the caches of the active filter. Deleted caches cannot be restored from inside OpenSAK (only from a backup); at the end of the run the macro output says how many caches were deleted. The cache list and map drop them after the macro. Locked caches are deleted too: the lock only protects from imports. Needs the user's permission like opensak.update().

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `codes` (`string[]`) — GC codes.

Returns `boolean|integer` — For one code: false if the cache is not in the database. For a list: the number of caches deleted.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
for c in opensak.caches{ archived = true, fields = {"code"} } do
    opensak.delete(c.code)
end
-- all caches of the active filter at once:
print(opensak.delete(opensak.codes()) .. " caches deleted")
```

### opensak.logs

```lua
opensak.logs(code [, options])
```

The logs of a cache, each with its `id` for opensak.update_log() and the fields listed under Logs, waypoints, attributes and trackables. Missing data is nil.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `opensak.Log[]?` — nil if the cache is not in the database.

Since API version 3.

Example:

```lua
for _, l in ipairs(opensak.logs("GC12345") or {}) do
    print(l.date, l.type, l.finder)
end
```

### opensak.insert_log

```lua
opensak.insert_log(code, fields)
```

Add a log to a cache in the active database; `type` required. OpenSAK updates the cache's log counts and dates. Needs the user's permission like opensak.update().

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `fields` (`opensak.LogInsert`) — The new log's fields.

Returns `integer?` — The new log's id; nil if the cache is not in the database.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
local id = opensak.insert_log("GC12345", { type = "Write note", date = "2026-10-11",
                                          finder = "me", text = "Final solved" })
```

### opensak.update_log

```lua
opensak.update_log(id, fields)
```

Change fields of a log in the active database; only the given fields change, and `false` clears a field that is not required or a boolean. Needs the user's permission like opensak.update().

Parameters:

- `id` (`integer`) — The log's id from opensak.logs().
- `fields` (`opensak.LogUpdate`) — The fields to change.

Returns `boolean` — false if there is no log with this id.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
for _, l in ipairs(opensak.logs("GC12345")) do
    if l.type == "Write note" then opensak.update_log(l.id, { text = "checked" }) end
end
```

### opensak.delete_log

```lua
opensak.delete_log(id)
```

Delete a log from the active database. OpenSAK updates the cache's log counts and dates. Needs the user's permission like opensak.update().

Parameters:

- `id` (`integer`) — The log's id from opensak.logs().

Returns `boolean` — false if there is no log with this id.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
for _, l in ipairs(opensak.logs("GC12345")) do
    if l.type == "Write note" and l.finder == "me" then opensak.delete_log(l.id) end
end
```

### opensak.waypoints

```lua
opensak.waypoints(code [, options])
```

The waypoints of a cache, each with its `id` for opensak.update_waypoint() and the fields listed under Logs, waypoints, attributes and trackables. Missing data is nil.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `opensak.Waypoint[]?` — nil if the cache is not in the database.

Since API version 3.

Example:

```lua
for _, w in ipairs(opensak.waypoints("GC12345") or {}) do
    print(w.prefix, w.type, w.lat, w.lon)
end
```

### opensak.insert_waypoint

```lua
opensak.insert_waypoint(code, fields)
```

Add a waypoint to a cache in the active database; `prefix`, `type` required. OpenSAK updates the cache's waypoint count. Needs the user's permission like opensak.update().

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `fields` (`opensak.WaypointInsert`) — The new waypoint's fields.

Returns `integer?` — The new waypoint's id; nil if the cache is not in the database.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
opensak.insert_waypoint("GC12345", { prefix = "FN", type = "Final Location",
                                     name = "Final", lat = 47.36872, lon = 8.54093 })
```

### opensak.update_waypoint

```lua
opensak.update_waypoint(id, fields)
```

Change fields of a waypoint in the active database; only the given fields change, and `false` clears a field that is not required or a boolean. Needs the user's permission like opensak.update().

Parameters:

- `id` (`integer`) — The waypoint's id from opensak.waypoints().
- `fields` (`opensak.WaypointUpdate`) — The fields to change.

Returns `boolean` — false if there is no waypoint with this id.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
for _, w in ipairs(opensak.waypoints("GC12345")) do
    if w.prefix == "PK" then opensak.update_waypoint(w.id, { comment = "free parking" }) end
end
```

### opensak.delete_waypoint

```lua
opensak.delete_waypoint(id)
```

Delete a waypoint from the active database. OpenSAK updates the cache's waypoint count. Needs the user's permission like opensak.update().

Parameters:

- `id` (`integer`) — The waypoint's id from opensak.waypoints().

Returns `boolean` — false if there is no waypoint with this id.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
for _, w in ipairs(opensak.waypoints("GC12345")) do
    if w.prefix == "FN" then opensak.delete_waypoint(w.id) end
end
```

### opensak.attributes

```lua
opensak.attributes(code [, options])
```

The attributes of a cache, each with its `id` for opensak.update_attribute() and the fields listed under Logs, waypoints, attributes and trackables. Missing data is nil.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `opensak.Attribute[]?` — nil if the cache is not in the database.

Since API version 3.

Example:

```lua
for _, a in ipairs(opensak.attributes("GC12345") or {}) do
    print(a.attribute_id, a.name, a.on)
end
```

### opensak.insert_attribute

```lua
opensak.insert_attribute(code, fields)
```

Add a attribute to a cache in the active database; `attribute_id` required. Needs the user's permission like opensak.update().

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `fields` (`opensak.AttributeInsert`) — The new attribute's fields.

Returns `integer?` — The new attribute's id; nil if the cache is not in the database.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
opensak.insert_attribute("GC12345", { attribute_id = 1, name = "Dogs", on = false })
```

### opensak.update_attribute

```lua
opensak.update_attribute(id, fields)
```

Change fields of a attribute in the active database; only the given fields change, and `false` clears a field that is not required or a boolean. Needs the user's permission like opensak.update().

Parameters:

- `id` (`integer`) — The attribute's id from opensak.attributes().
- `fields` (`opensak.AttributeUpdate`) — The fields to change.

Returns `boolean` — false if there is no attribute with this id.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
for _, a in ipairs(opensak.attributes("GC12345")) do
    if a.attribute_id == 1 then opensak.update_attribute(a.id, { on = true }) end
end
```

### opensak.delete_attribute

```lua
opensak.delete_attribute(id)
```

Delete a attribute from the active database. Needs the user's permission like opensak.update().

Parameters:

- `id` (`integer`) — The attribute's id from opensak.attributes().

Returns `boolean` — false if there is no attribute with this id.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
for _, a in ipairs(opensak.attributes("GC12345")) do
    if a.attribute_id == 1 then opensak.delete_attribute(a.id) end
end
```

### opensak.trackables

```lua
opensak.trackables(code [, options])
```

The trackables of a cache, each with its `id` for opensak.update_trackable() and the fields listed under Logs, waypoints, attributes and trackables. Missing data is nil.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `options` (`opensak.ReadOptions`, optional) — `database`: read another database instead of the active one.

Returns `opensak.Trackable[]?` — nil if the cache is not in the database.

Since API version 3.

Example:

```lua
for _, t in ipairs(opensak.trackables("GC12345") or {}) do print(t.ref, t.name) end
```

### opensak.insert_trackable

```lua
opensak.insert_trackable(code, fields)
```

Add a trackable to a cache in the active database. OpenSAK updates the cache's trackable count. Needs the user's permission like opensak.update().

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `fields` (`opensak.TrackableInsert`) — The new trackable's fields.

Returns `integer?` — The new trackable's id; nil if the cache is not in the database.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
opensak.insert_trackable("GC12345", { ref = "TB1234", name = "My travel bug" })
```

### opensak.update_trackable

```lua
opensak.update_trackable(id, fields)
```

Change fields of a trackable in the active database; only the given fields change, and `false` clears a field that is not required or a boolean. Needs the user's permission like opensak.update().

Parameters:

- `id` (`integer`) — The trackable's id from opensak.trackables().
- `fields` (`opensak.TrackableUpdate`) — The fields to change.

Returns `boolean` — false if there is no trackable with this id.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
for _, t in ipairs(opensak.trackables("GC12345")) do
    if t.ref == "TB1234" then opensak.update_trackable(t.id, { name = "Renamed" }) end
end
```

### opensak.delete_trackable

```lua
opensak.delete_trackable(id)
```

Delete a trackable from the active database. OpenSAK updates the cache's trackable count. Needs the user's permission like opensak.update().

Parameters:

- `id` (`integer`) — The trackable's id from opensak.trackables().

Returns `boolean` — false if there is no trackable with this id.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
for _, t in ipairs(opensak.trackables("GC12345")) do
    if t.ref == "TB1234" then opensak.delete_trackable(t.id) end
end
```

### opensak.sql_write

```lua
opensak.sql_write(query [, params])
```

Run one INSERT, UPDATE or DELETE statement (SQLite) against the active database — the power tool for bulk work, changing many rows with one statement. For a single cache or row, prefer `opensak.update()`, `insert{}`, `delete()`, `insert_<x>()`, `update_<x>()` and `delete_<x>()`, which check each field and need no knowledge of the schema (see Changing caches). Only the cache tables can be changed (`attributes`, `caches`, `logs`, `trackables`, `user_notes`, `waypoints`), and an UPDATE may not set the keys or the columns OpenSAK maintains itself (see Changing caches). Deleting a cache also deletes its logs, waypoints, attributes, trackables and user note, as opensak.delete() does. Locked caches are changed and deleted like any other (add `AND locked = 0` to leave them alone). REPLACE (`INSERT OR REPLACE`, `REPLACE INTO`, …) is refused, as it would delete those too; to update or insert, use `INSERT … ON CONFLICT (gc_code) DO UPDATE SET …`. OpenSAK recalculates distances, counts and log dates of the caches the statement touched — inside opensak.transaction() once at its end (see opensak.resync()). An INSERT only needs the real data: the columns opensak.columns() marks as `required`, e.g. `gc_code`, `name`, `cache_type`, `latitude` and `longitude` for a cache (see Changing caches); every other column gets its default. Each statement is committed on its own (or with the surrounding opensak.transaction()), or not at all on an error. BEGIN, COMMIT and the like are refused; use opensak.transaction(). Needs the user's permission like opensak.update().

Parameters:

- `query` (`string`) — One INSERT, UPDATE or DELETE statement.
- `params` (`table`, optional) — As for opensak.sql().

Returns `integer` — Number of rows inserted, updated or deleted (without the rows deleted along with a cache).

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
local n = opensak.sql_write(
  "UPDATE caches SET user_data_1 = ? WHERE country = ? AND found = 0",
  { "todo", "Switzerland" })
print(n .. " caches marked")
-- a found log: only cache_id and log_type are required
opensak.sql_write([[
  INSERT INTO logs (cache_id, log_type, log_date, finder, text)
  SELECT id, 'Found it', ?, ?, ? FROM caches WHERE gc_code = ?]],
  { "2026-10-10 14:30:00.000000", "myUsername", "TFTC", "GC12345" })
-- keep only the 5 newest logs of each cache
opensak.sql_write([[DELETE FROM logs WHERE id NOT IN (
  SELECT l2.id FROM logs l2 WHERE l2.cache_id = logs.cache_id
  ORDER BY l2.log_date DESC LIMIT 5)]])
```

### opensak.transaction

```lua
opensak.transaction(fn)
```

Run *fn* as one transaction: every change it makes to the active database (opensak.update, insert, delete, sql_write, set_corrected, clear_corrected and the functions for logs, waypoints, ...) is kept when *fn* returns, and all of it is undone if *fn* raises an error, which opensak.transaction() then raises again. Many changes in one transaction are also faster than the same changes committed one by one. Inside *fn*, opensak.cache, caches, sql and the like see the changes already made, except the columns OpenSAK maintains (counts, log dates, distance), which are recalculated once before the commit or when opensak.resync() is called; everything else in OpenSAK, exports included, sees them only afterwards. A transaction inside another one is undone on its own on an error, and kept with the outer one otherwise. opensak.switch_database, move_caches and copy_caches cannot be used inside. Needs the user's permission like opensak.update(). A macro that ends inside a transaction (e.g. cancelled) keeps none of it.

Parameters:

- `fn` (`fun()`) — The function to run.

Returns `any...` — What *fn* returns.

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
local n = opensak.transaction(function()
    local n = 0
    for c in opensak.caches{ found = true, fields = {"code"} } do
        opensak.update(c.code, { user_flag = true })
        n = n + 1
    end
    return n
end)
print(n .. " caches flagged")
-- error() inside the function undoes everything it changed:
local ok, err = pcall(opensak.transaction, function()
    opensak.update("GC12345", { user_flag = true })
    error("changed my mind")
end)
```

### opensak.in_transaction

```lua
opensak.in_transaction()
```

Whether the macro is inside opensak.transaction().

Returns `boolean` — true inside opensak.transaction().

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
if not opensak.in_transaction() then print("each change is committed on its own") end
```

### opensak.resync

```lua
opensak.resync()
```

Recalculate now the columns OpenSAK maintains itself (distance and bearing, log, waypoint and trackable counts, log dates) for the caches changed so far in the open opensak.transaction(). Inside a transaction they are recalculated only once, just before the commit, which makes many writes much faster; until then opensak.cache, sql and the like still see the old values. Call resync() when a later read inside the transaction depends on them. Outside a transaction it does nothing, as every write is recalculated before its own commit.

Returns `integer` — Number of caches recalculated (0 outside a transaction).

Since API version 3.

Beta: only available while the `lua-macro-write` feature flag is on, as it is in beta builds; check with `if opensak.update then`. Changing caches needs the user's approval, given per macro (see Changing caches).

Example:

```lua
opensak.transaction(function()
    for i = 1, 3 do opensak.insert_log("GC12345", { type = "Write note" }) end
    opensak.resync()
    print(opensak.cache("GC12345").log_count)
end)
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

### opensak.write_text

```lua
opensak.write_text(path, text [, options])
```

Write *text* to a file as UTF-8 and return the file written. A relative path is resolved against the macro file's folder. The folder needs write permission (the OpenSAK temp folder, opensak.temp_dir(), has it by default); for an unapproved one the user is asked first. Only these file types can be written: .txt .csv .tsv .html .htm .svg .md .json. An existing file is replaced, or extended with `append = true`. At most 20 MB.

Parameters:

- `path` (`string`) — The file to write.
- `text` (`string`) — The content.
- `options` (`{append?: boolean}`, optional) — `append`: add *text* at the end of an existing file.

Returns `string` — The file written (full path).

Since API version 3.

Example:

```lua
local path = opensak.write_text(opensak.temp_dir() .. "/report.html",
  "<h1>" .. opensak.count() .. " caches</h1>")
print("written: " .. path)
```

### opensak.read_text

```lua
opensak.read_text(path [, options])
```

Read a whole text file, of any file type. A relative path is resolved against the macro file's folder. The folder needs read permission; for an unapproved one the user is asked first. UTF-8 unless `encoding` says otherwise (`"utf-8"`, `"latin-1"`, `"cp1252"`); a UTF-8 byte order mark is removed and line endings become `"\n"`. At most 10 MB.

Parameters:

- `path` (`string`) — The file to read.
- `options` (`{encoding?: string}`, optional) — `encoding`: e.g. `"cp1252"` for a file from an older Windows program.

Returns `string` — The file's text.

Since API version 3.

Example:

```lua
local text = opensak.read_text("notes.txt")
for line in text:gmatch("[^\n]+") do
    print(line)
end
```

### opensak.file_exists

```lua
opensak.file_exists(path)
```

Whether a file exists (false for a folder). A relative path is resolved against the macro file's folder. The folder needs read permission, like opensak.read_text().

Parameters:

- `path` (`string`) — The file.

Returns `boolean` — true if the file exists.

Since API version 3.

Example:

```lua
if not opensak.file_exists(opensak.temp_dir() .. "/report.html") then
    print("no report yet")
end
```

### opensak.folder_exists

```lua
opensak.folder_exists(path)
```

Whether a folder exists. A relative path is resolved against the macro file's folder. The folder containing it needs read permission.

Parameters:

- `path` (`string`) — The folder.

Returns `boolean` — true if the folder exists.

Since API version 3.

Example:

```lua
if opensak.folder_exists("C:/GPX") then print("found") end
```

### opensak.list_dir

```lua
opensak.list_dir(folder [, pattern])
```

The files and folders in a folder (not in its subfolders), sorted by name. A relative path is resolved against the macro file's folder. The folder needs read permission; for an unapproved one the user is asked first. `pattern` keeps the names that match it, ignoring case: `*` stands for any text, `?` for one character. At most 10,000 entries. Each entry has `name`, `path` (full path), `dir` (true for a folder), `size` (bytes, 0 for a folder) and `modified` (seconds since the epoch, like `os.time()`).

Parameters:

- `folder` (`string`) — The folder.
- `pattern` (`string`, optional) — A file name pattern, e.g. "*.gpx".

Returns `{name: string, path: string, dir: boolean, size: integer, modified: integer}[]` — One table per entry.

Since API version 3.

Example:

```lua
for _, f in ipairs(opensak.list_dir("C:/GPX", "*.gpx")) do
    print(f.name, f.size, os.date("%Y-%m-%d", f.modified))
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

### opensak.export_poi

```lua
opensak.export_poi(setting [, folder])
```

Export the caches of the active filter and/or their child waypoints as Garmin POI files (.gpi) with a saved POI export setting (File → Export Garmin POI: folder, file name, if the file exists, which points, name/description/extra templates, category, proximity alert, icon, max. points). The file name variables are filled in as in the dialog. With "one file per waypoint type" a file is written for the caches and one for every waypoint type. The files go into *folder* if given, else into the setting's folder, which needs write permission (Settings → Folder permissions); for an unapproved one the user is asked first. An existing file is skipped when the setting says skip (or ask, and the user answers No).

Parameters:

- `setting` (`string`) — Name of the saved POI export setting.
- `folder` (`string`, optional) — Folder to write to instead of the setting's folder, e.g. the device's Garmin/POI folder.

Returns `string[]?, integer?` — The files written and the number of POIs in them; nil if nothing was written.

Since API version 3.

Example:

```lua
local files, n = opensak.export_poi("Garmin POI")
if files then print(n .. " POIs → " .. table.concat(files, ", ")) end
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

### opensak.choose_folder

```lua
opensak.choose_folder([title])
```

Let the user pick a folder in a folder dialog. The macro may then read and write files in it for the rest of this run, without a folder permission. OpenSAK's own folders and whole drives cannot be picked.

Parameters:

- `title` (`string`, optional) — Dialog title.

Returns `string?` — Full path of the folder, or nil if cancelled.

Since API version 3.

Example:

```lua
local folder = opensak.choose_folder("Export to")
if not folder then return end      -- cancelled
opensak.export_gpx{ path = folder .. "/{filter}.gpx" }
```

### opensak.message

```lua
opensak.message(text [, title])
```

Show a message with an OK button and wait until the user closes it.

Parameters:

- `text` (`string`) — The message.
- `title` (`string`, optional) — Window title.

Since API version 3.

Example:

```lua
opensak.message("Done: 12 caches updated")
```

### opensak.input

```lua
opensak.input(prompt [, default] [, title])
```

Ask for one line of text.

Parameters:

- `prompt` (`string`) — The question.
- `default` (`string`, optional) — Text the field starts with.
- `title` (`string`, optional) — Window title.

Returns `string?` — The text entered, or nil if cancelled.

Since API version 3.

Example:

```lua
local name = opensak.input("Name of the new database", "CH_Zurich")
if not name then return end      -- cancelled
```

### opensak.choose

```lua
opensak.choose(prompt, options [, default] [, title])
```

Let the user pick one item of a list.

Parameters:

- `prompt` (`string`) — The question.
- `options` (`(string|number)[]`) — The items.
- `default` (`string|number`, optional) — The item selected at first.
- `title` (`string`, optional) — Window title.

Returns `string|number?` — The item picked, or nil if cancelled.

Since API version 3.

Example:

```lua
local fmt = opensak.choose("Export format", {"GPX", "GGZ", "KML"}, "GPX")
if not fmt then return end      -- cancelled
```

### opensak.form

```lua
opensak.form(spec)
```

Show a dialog built from a table (see Forms) and wait until it is closed. Fields with a value come back in a table keyed by their `id`. Callbacks such as `on_change` run while the form stays open and can change it through the handle they get. Without `buttons` the form has OK and Cancel. Tools → Form designer in the macro window builds forms visually and imports GSAK forms.

Parameters:

- `spec` (`opensak.FormSpec`) — The form.

Returns `table<string, any>?, string?` — The values by field id and the id of the button (or `exit_on_change` field, or "timeout") that closed the form; nil if it was cancelled.

Since API version 3.

Example:

```lua
local v, button = opensak.form{
  title = "Export settings",
  remember = true,
  fields = {
    { type = "folder",   id = "folder",  label = "Output folder", required = true },
    { type = "number",   id = "maxdist", label = "Max distance", default = 25,
      min = 0, suffix = " km" },
    { type = "checkbox", id = "unfound", label = "Unfound only", default = true },
    { type = "choice",   id = "fmt",     label = "Format", options = {"GPX", "GGZ"},
      on_change = function(f, value)
        f:set_enabled("unfound", value == "GPX")
      end },
  },
}
if not v then return end      -- cancelled
print(v.folder, v.maxdist, v.unfound, v.fmt)
```

### opensak.progress

```lua
opensak.progress(title [, total])
```

Open a progress window with a Cancel button, for long loops. `p:update(i [, text])` moves the bar to *i* of *total* and returns false once the user has pressed Cancel; `p:close()` closes the window (also done when the macro ends). Without *total* the bar just shows that work is going on.

Parameters:

- `title` (`string`) — What is being done.
- `total` (`integer`, optional) — Number of steps.

Returns `opensak.Progress` — The window.

Since API version 3.

Example:

```lua
local codes = opensak.codes()
local p = opensak.progress("Checking caches", #codes)
for i, code in ipairs(codes) do
    if not p:update(i, code) then break end      -- Cancel pressed
    -- … work on the cache …
end
p:close()
```

### opensak.select

```lua
opensak.select(code)
```

Select a cache in the cache list and show it in the detail panel and on the map, like a click on its row.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".

Returns `boolean` — false if the cache is not among the caches of the active filter.

Since API version 3.

Example:

```lua
if not opensak.select("GC12345") then print("not in the current list") end
```

### opensak.refresh

```lua
opensak.refresh()
```

Redraw the cache list, detail panel and map now. Changes a macro makes are otherwise shown when it ends.

Since API version 3.

Example:

```lua
opensak.set_corrected("GC12345", 47.36872, 8.54093)
opensak.refresh()
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

### opensak.run_macro

```lua
opensak.run_macro(file, ...)
```

Run another macro and wait until it has finished, e.g. to chain several macros from one control macro. A relative path is looked up in the folder of the calling macro, and `.lua` is added when the name has no extension. The called macro gets its own global variables; values are passed as extra arguments (read them with `...`) and come back as its return values. An error in it stops the calling macro too, unless caught with `pcall`. Macros run this way share the run's limits, sleep time and folder approvals; they may call each other at most 10 levels deep.

Parameters:

- `file` (`string`) — The macro file.
- `...` (`any`) — Passed to the macro as `...`.

Returns `any...` — What the macro returns.

Since API version 3.

Example:

```lua
opensak.run_macro("refresh_corrected")
local count = opensak.run_macro("lib/count_unfound.lua", "Zürich")
-- lib/count_unfound.lua:
--   local area = ...
--   return opensak.filter{ found = false, state = area }
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

### opensak.log

```lua
opensak.log(...)
```

Write a message to OpenSAK's log file and the macro output. Run with `opensak --run-macro`, it goes to stderr while print() goes to stdout.

Parameters:

- `...` (`any`) — Values, joined with tabs like print().

Since API version 3.

Example:

```lua
opensak.log("checked " .. opensak.count() .. " caches")
```

### opensak.exit

```lua
opensak.exit([code])
```

End the macro right away. pcall cannot catch it, and it is not an error: the macro window shows the code, and `opensak --run-macro` exits with it.

Parameters:

- `code` (`integer`, optional) — Exit code 0-255 (default 0).

Since API version 3.

Example:

```lua
if opensak.count() == 0 then
    opensak.log("nothing to do")
    opensak.exit(1)
end
```

### opensak.coords.parse

```lua
opensak.coords.parse(text [, fmt])
```

Parse a coordinate string. Without `fmt` the format is detected: latitude/longitude (DMM, DMS, decimal degrees), UTM, MGRS, British grid letters ("TQ 30268 79642"), Plus Codes, Maidenhead locators, geohashes (5+ characters mixing letters and digits) and the Swiss, Dutch, Swedish and German grids as number pairs, which are only accepted inside their country. Pass `fmt` to read one format only — needed for numeric British grid positions, short geohashes and to rule out a wrong guess. Formats: `"latlon"`, `"utm"`, `"mgrs"`, `"ch1903"`, `"rd"`, `"osgb"`, `"sweref99"`, `"gk"`, `"olc"`, `"geohash"`, `"maidenhead"` (`"latlon"` covers DD, DMM and DMS; every name `opensak.coords.format` takes works too).

Parameters:

- `text` (`string`) — The coordinates.
- `fmt` (`string`, optional) — Read only this format; detect it if omitted.

Returns `number?, number?, string?` — Latitude, longitude and the format that was read, or nil if the text cannot be parsed.

Since API version 2. Changed in API version 3: added `fmt`, the third return value and the grid, code and locator formats.

Example:

```lua
local lat, lon = opensak.coords.parse("N47 22.123 E008 32.456")
if not lat then error("not a coordinate") end
local lat2, lon2, fmt = opensak.coords.parse("32T MT 65339 46242")
print(fmt)  -- mgrs
print(opensak.coords.parse("530268 179642", "osgb"))
```

### opensak.coords.format

```lua
opensak.coords.format(lat, lon [, fmt])
opensak.coords.format(coords [, fmt])
```

Format coordinates. Formats: `"dmm"` (default), `"dms"`, `"dd"`, `"utm"`, `"mgrs"`, `"ch1903"` (Swiss LV03), `"ch1903+"` (Swiss LV95), `"rd"` (Dutch RD), `"osgb"` (British National Grid), `"sweref99"` (SWEREF 99 TM), `"gk"` (German Gauss-Krüger), `"olc"` (Plus Code), `"geohash"` and `"maidenhead"`. Aliases: `"lv03"`, `"lv95"`, `"bng"`, `"sweref"`, `"gauss-krueger"`, `"pluscode"`, `"qth"`. The national grids are only meaningful in and around their country; `"osgb"` and `"utm"` raise an error where they are not defined.

Parameters:

- `lat` (`number`) — Latitude in decimal degrees.
- `lon` (`number`) — Longitude in decimal degrees.
- `fmt` (`string`, optional) — Output format, "dmm" if omitted.
- `coords` (`string`) — Coordinates, e.g. "N47 22.123 E008 32.456".

Returns `string` — E.g. "N47 22.123  E008 32.456" or "32T E 465123 N 5247123".

Since API version 2. Changed in API version 3: added `"mgrs"`, `"rd"`, `"osgb"`, `"sweref99"`, `"gk"`, `"olc"`, `"geohash"`, `"maidenhead"` and the aliases.

Example:

```lua
print(opensak.coords.format(47.36872, 8.54093, "utm"))
print(opensak.coords.format("N47 22.123 E008 32.456", "ch1903"))
print(opensak.coords.format(51.50072, -0.12462, "osgb"))
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

### opensak.html.escape

```lua
opensak.html.escape(value)
```

*value* as text for HTML: `& < > " '` are escaped, nil becomes "", 3.0 becomes "3".

Parameters:

- `value` (`any`) — A string, number, boolean or nil.

Returns `string` — Escaped text.

Since API version 3.

Example:

```lua
local cell = "<td>" .. opensak.html.escape(c.name) .. "</td>"
```

### opensak.html.table

```lua
opensak.html.table(rows [, options])
```

*rows* as an HTML table (values escaped), e.g. the rows of opensak.sql() (GSAK's SqlToHtml). `columns` lists the fields to show, each a field name or `{field, heading}`; without it all fields are shown, sorted by name — a Lua table has no field order, so pass `columns` to keep the order of the query. Number columns are right-aligned. `link = { code = "url" }` turns the code cells into links to the row's url field (http/https only). `bars = {"count"}` adds a bar next to each value of those number fields, scaled to the largest (in `color`, "#rrggbb"). The bars use inline styles only, so they also show without the stylesheet of opensak.html.page.

Parameters:

- `rows` (`table[]`) — The rows.
- `options` (`{columns?: (string|string[])[], link?: table<string, string>, caption?: string, bars?: string[], color?: string}`, optional) — See above.

Returns `string` — HTML.

Since API version 3.

Example:

```lua
local rows = opensak.sql(
  "SELECT country, COUNT(*) AS n FROM caches GROUP BY country ORDER BY n DESC")
local html = opensak.html.table(rows, {
  columns = { {"country", "Country"}, {"n", "Caches"} }, bars = { "n" } })
```

### opensak.html.tiles

```lua
opensak.html.tiles(items)
```

Key figures as a row of tiles: a list of `{label =, value =}`.

Parameters:

- `items` (`{label: string, value: any}[]`) — The tiles.

Returns `string` — HTML.

Since API version 3.

Example:

```lua
local found = opensak.sql("SELECT COUNT(*) AS n FROM caches WHERE found = 1")[1].n
local html = opensak.html.tiles{
  { label = "caches", value = opensak.count() },
  { label = "found", value = found },
}
```

### opensak.html.columns

```lua
opensak.html.columns(...)
```

Put HTML parts (e.g. two tables) side by side; on a narrow window they go one below the other.

Parameters:

- `...` (`string`) — The HTML parts (or one list of them).

Returns `string` — HTML.

Since API version 3.

Example:

```lua
local html = opensak.html.columns(table1, table2)
```

### opensak.html.page

```lua
opensak.html.page(spec)
```

A complete HTML page for opensak.write_text: `title` (also the heading), `subtitle`, the body — the strings in the list part of the table, and/or `body` — and `footer`. A built-in stylesheet with a light and a dark theme styles headings (`<h2>`), tables, tiles and columns; `css` is added after it. The body is put in as it is: build it from the html.* functions, or escape your own text with html.escape.

Parameters:

- `spec` (`{title?: string, subtitle?: string, css?: string, lang?: string, footer?: string, body?: string|string[], [integer]: string}`) — See above.

Returns `string` — The page.

Since API version 3.

Example:

```lua
local rows = opensak.sql("SELECT code, name, difficulty FROM caches LIMIT 50")
local page = opensak.html.page{
  title = "Caches", subtitle = opensak.database(),
  opensak.html.tiles{ { label = "caches", value = opensak.count() } },
  "<h2>First 50</h2>",
  opensak.html.table(rows, { columns = { "code", "name", "difficulty" } }),
}
opensak.write_text(opensak.temp_dir() .. "/caches.html", page)
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

### opensak.settings.load

```lua
opensak.settings.load()
```

The settings this macro saved with opensak.settings.save(), or an empty table on the first run. Each macro has its own settings, kept per macro file in the `settings` folder of the macros folder; no other macro can read them. A renamed macro starts with empty settings, and an unsaved macro has none.

Returns `table` — The saved settings; empty if there are none.

Since API version 3.

Example:

```lua
local s = opensak.settings.load()
s.maxdist = s.maxdist or 25
```

### opensak.settings.save

```lua
opensak.settings.save(settings)
```

Save settings for the next run of this macro, replacing the saved ones. Strings, numbers, booleans and nested tables can be saved; a table needs string keys, or 1, 2, 3, … for a list. Functions and cyclic tables are refused, and the settings may take up at most 1 MB.

Parameters:

- `settings` (`table`) — The settings to save.

Since API version 3.

Example:

```lua
local s = opensak.settings.load()
s.maxdist = 30
s.types = {"Traditional", "Multi"}
opensak.settings.save(s)
```

### opensak.settings.clear

```lua
opensak.settings.clear()
```

Remove this macro's saved settings, so the next opensak.settings.load() returns an empty table.

Since API version 3.

Example:

```lua
opensak.settings.clear()
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

`opensak.cache()`, `opensak.caches{}`, `opensak.description()`, `opensak.sql()`, `opensak.sql_each()`, `opensak.tables()` and `opensak.columns()` take a `database` option with the name of a database from `opensak.databases()`. It reads that database without switching to it, so the active filter stays. The database is opened read-only. A database last opened by an older OpenSAK version must be opened once first, so its schema is updated. Naming the active database is the same as leaving `database` out. To join another database with the active one in one statement, see [Several databases in one query](#several-databases-in-one-query).

## Several databases in one query

Beta: `opensak.attach()`, `opensak.detach()` and `opensak.attached()` are only available while the `lua-macro-write` feature flag is on.

The `database` option reads one other database per call. To join several databases in one statement, as GSAK macros do with SQL `ATTACH`, attach them with `opensak.attach()`: each one becomes a schema of the active database's SQL connection, next to `opensak.macro_db()`'s `macro`. Attached databases are read-only; `opensak.sql_write()` still only changes the active database (and `macro`), but may read the attached ones, e.g. in a subquery. SQL `ATTACH` and `DETACH` stay refused, so a macro cannot open files past the folder permissions.

For example, with found caches in one database and placed caches in another, as FindStatGen keeps them:

```lua
opensak.attach("Found")                -- schema "found"
opensak.attach("Placed 2020", "placed")

-- found caches that are also in the active database
for _, r in ipairs(opensak.sql([[
    SELECT c.gc_code, c.name, f.found_date FROM caches c
    JOIN found.caches f ON f.gc_code = c.gc_code
    ORDER BY f.found_date]])) do
    print(r.gc_code, r.name, r.found_date)
end

-- flag the active database's caches placed by me
opensak.sql_write("UPDATE caches SET user_flag = 1 "
    .. "WHERE gc_code IN (SELECT gc_code FROM placed.caches)")
```

An attached database is not updated to the current schema: one last opened by an older OpenSAK version may lack columns. Open it once (Manage Databases) to update it, or check with `opensak.columns("found.caches")`. A database in use by another OpenSAK window can be attached; the statement sees what that window has committed.

| GSAK | OpenSAK |
|---|---|
| `ATTACH 'x\sqlite.db3' AS x` (an OpenSAK database) | `opensak.attach("x", "x")` |
| `ATTACH 'any.db3' AS y` (any other file) | not available yet |
| `DETACH x` | `opensak.detach("x")`, or nothing: all are detached when the macro ends |
| own writable database | `opensak.macro_db()` |

## Forms

`opensak.form{}` builds a dialog from a table. With the default `layout = "auto"` the fields are stacked top to bottom with their `label` on the left, and a `group` is a framed section with its own `fields`. With `layout = "absolute"` every field is placed at its `x`, `y` (and `w`, `h`) in pixels, like in GSAK's form designer.

Callbacks run while the form stays open: `on_change` of a field, `on_click` of a button, `on_open` and `validate` of the form. Each gets a handle `f` to read and change the form (see Form handle). An error in a callback stops the macro. `exit_on_change = true` closes the form instead, as GSAK's ExitOnChange did.

The form returns two values: a table with the value of every field that has an `id` and a value, and the id of the button that closed it (or of the `exit_on_change` field, or "timeout"). Cancel, Esc, the window's close button and a button with `cancel = true` return nil.

**Tools → Form designer** in the macro window builds a form with the mouse: it inserts the `opensak.form{…}` table at the cursor or edits the one the cursor is in, and imports forms of GSAK macros (`<Data> VarName=$form …`).

### Form keys

| Key | Type | Meaning |
|---|---|---|
| `title` | `string` | Window title. |
| `fields` | `opensak.FormField[]` | The fields, top to bottom (see Form fields). |
| `buttons` | `opensak.FormButton[]` | The buttons at the bottom; default OK and Cancel. `{}` for none, e.g. when the fields include buttons. |
| `layout` | `"auto"\|"absolute"` | "auto" (default) stacks the fields with their labels on the left; "absolute" places each field at its `x`, `y` like GSAK. |
| `width` | `integer` | Width of the field area in pixels (absolute layout: default fits the fields). |
| `height` | `integer` | Height of the field area in pixels. |
| `remember` | `boolean` | Start with the values the user entered last time (stored per macro file, next to opensak.settings; the macro must be saved). Fields with `remember = false` and passwords are not kept. |
| `id` | `string` | Name the remembered values are kept under (default: the title), for macros with several forms. |
| `timeout` | `number` | Close the form after this many seconds; it then returns the values and "timeout". |
| `on_open` | `fun(f: opensak.Form)` | Called once when the form opens. |
| `validate` | `fun(f: opensak.Form, button: string): string?` | Called before the form closes with a button other than a cancel button; return a message to show it and keep the form open. |

### Form fields

Every field takes `type`, `id`, `label`, `tooltip`, `enabled`, `visible`, `x`, `y`, `w`, `h`, `bold`, `italic`, `color` and `font_size`; fields with a value also take `default`, `on_change`, `exit_on_change` and `remember`.

| Type | GSAK | Value | Own keys | Meaning |
|---|---|---|---|---|
| `label` | Label | — | `align` | Text such as a heading or a hint; `label` is the text. Simple HTML (`<b>`, `<i>`, `<br>`, `<font color>`) is shown formatted. |
| `separator` | — | — | — | A horizontal line between fields. |
| `text` | Edit | `string` | `placeholder`, `password`, `readonly`, `required` | Single-line text. |
| `number` | Edit | `number` | `min`, `max`, `decimals`, `step`, `suffix` | Number with up/down arrows; whole numbers unless `decimals` is set. |
| `memo` | Memo | `string` | `placeholder`, `readonly`, `required`, `wrap`, `monospace` | Multi-line text. |
| `checkbox` | CheckBox | `boolean` | — | A yes/no box; `label` is shown next to the box. |
| `choice` | ComboBox | `string\|number` | `options`, `editable`, `required` | Drop-down list; starts with the first option unless `default` says otherwise. With `editable = true` the user may also type a value (GSAK DirectInput). |
| `radio` | RadioButton | `string\|number` | `options`, `columns`, `required` | One of the options, as radio buttons. |
| `checklist` | CheckListBox, CheckGroup | `string[]` | `options`, `columns` | Several options to tick; the value is an array of the ticked ones. |
| `date` | Date | `string` | `min`, `max` | A date with a pop-up calendar, as "YYYY-MM-DD" (default today). |
| `file` | File | `string` | `filter`, `mode`, `placeholder`, `required` | A file path with a Browse button. A file picked with Browse may be used for the rest of the run like one from opensak.choose_file() (read with `mode = "open"`, the default, or written with "save"). |
| `folder` | Folder | `string` | `placeholder`, `required` | A folder path with a Browse button. A folder picked with Browse may be read and written for the rest of the run, like one from opensak.choose_folder(). |
| `group` | GroupBox | — | `fields` | A frame titled `label` around its own `fields`. In the absolute layout their positions are relative to the group. |
| `button` | Button | — | `on_click`, `default`, `cancel` | A button. Without `on_click` it closes the form, which then returns the values and the button's id (nil with `cancel = true`). With `on_click` it calls the function and the form stays open. |
| `image` | Image | — | `path`, `data`, `scale` | A picture (PNG, JPG, GIF, BMP, ICO) from a file (`path`; read permission needed, relative to the macro's folder) or from base64 `data`. `scale = true` stretches it to `w` × `h`. |
| `html` | Browser | — | `html` | Read-only formatted text from simple HTML (headings, lists, tables). Nothing is loaded from the internet and links are not followed. |

### Field keys

| Key | Type | Meaning |
|---|---|---|
| `type` | `string` | The field type (see Form fields). |
| `id` | `string` | Key of the value in the result; needed for fields with a value, buttons, and fields a callback changes. |
| `label` | `string` | Caption: on the left in the auto layout, next to a checkbox, the title of a group, the text of a label or button. |
| `default` | `any` | Initial value (for a button: the button Enter presses). |
| `tooltip` | `string` | Shown when the mouse rests on the field. |
| `enabled` | `boolean` | false greys the field out (default true). |
| `visible` | `boolean` | false hides the field (default true). |
| `x` | `integer` | Absolute layout: pixels from the left of the form or group. |
| `y` | `integer` | Absolute layout: pixels from the top of the form or group. |
| `w` | `integer` | Width in pixels (auto layout: minimum width). |
| `h` | `integer` | Height in pixels (auto layout: minimum height). |
| `bold` | `boolean` | Bold caption. |
| `italic` | `boolean` | Italic caption. |
| `color` | `string` | Caption colour, "#rrggbb" or a name such as "red". |
| `font_size` | `integer` | Caption font size in points. |
| `on_change` | `fun(f: opensak.Form, value: any, id: string)` | Called when the user changes the value — a text or number field when it loses focus or Enter is pressed. The form stays open. |
| `exit_on_change` | `boolean` | Close the form when the value changes (after on_change); it returns the values and the field's id. Like GSAK's ExitOnChange. |
| `remember` | `boolean` | false: never remember this field's value. |
| `on_click` | `fun(f: opensak.Form, id: string)` | button: called on a click; the form stays open (f:close() closes it). |
| `cancel` | `boolean` | button: closes the form, which returns nil (also Esc). |
| `options` | `(string\|number)[]` | choice, radio, checklist: the options. |
| `editable` | `boolean` | choice: also accept typed text. |
| `columns` | `integer` | radio, checklist: number of columns (default 1). |
| `placeholder` | `string` | Grey hint shown while the field is empty. |
| `password` | `boolean` | text: hide the characters (never remembered). |
| `readonly` | `boolean` | text, memo: can be selected and copied, not changed. |
| `required` | `boolean` | The form cannot be confirmed while the field is empty. |
| `min` | `number\|string` | number: smallest value; date: earliest date. |
| `max` | `number\|string` | number: largest value; date: latest date. |
| `decimals` | `integer` | number: digits after the decimal point (default 0). |
| `step` | `number` | number: change per arrow click (default 1). |
| `suffix` | `string` | number: unit shown after the value, e.g. " km". |
| `wrap` | `boolean` | memo: wrap long lines (default true). |
| `monospace` | `boolean` | memo: fixed-width font. |
| `filter` | `string` | file: file types, e.g. "CSV files (*.csv)". |
| `mode` | `"open"\|"save"` | file: pick a file to read (default) or to write. |
| `align` | `"left"\|"center"\|"right"` | label: text alignment. |
| `fields` | `opensak.FormField[]` | group: the fields inside it. |
| `path` | `string` | image: the picture file. |
| `data` | `string` | image: the picture as base64 text instead of a file. |
| `scale` | `boolean` | image: stretch the picture to w × h. |
| `html` | `string` | html: the HTML to show. |

### Form handle

Callbacks get the open form as their first argument; call its methods with a colon.

| Method | Meaning |
|---|---|
| `f:get(id)` | The current value of a field. |
| `f:set(id, value)` | Change a field's value (on_change is not called). |
| `f:values()` | All values, as the form would return them. |
| `f:set_options(id, options)` | Replace the options of a choice, radio or checklist field. The value is kept if it is still an option. |
| `f:set_label(id, text)` | Change a caption: the label of a field, a label's text, a button's text, a group's title. |
| `f:set_enabled(id, on)` | Enable or grey out a field. |
| `f:set_visible(id, on)` | Show or hide a field. |
| `f:set_title(text)` | Change the window title. |
| `f:focus(id)` | Move the keyboard focus to a field. |
| `f:close([id])` | Close the form: opensak.form() returns the values and *id* (default "ok"). `validate` is not called. |
| `f:cancel()` | Close the form like Cancel: opensak.form() returns nil. |

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

## Changing caches

Beta: `opensak.update()`, `opensak.insert{}`, `opensak.delete()`, `opensak.insert_<x>()`, `opensak.update_<x>()`, `opensak.delete_<x>()`, `opensak.sql_write()`, `opensak.transaction()`, `opensak.in_transaction()` and `opensak.resync()` are only available while the `lua-macro-write` feature flag is on, as it is in beta builds. Without it they are nil, and `opensak.set_corrected()` and `opensak.clear_corrected()` change the database without asking.

`opensak.update()`, `opensak.insert{}`, `opensak.delete()`, `opensak.insert_<x>()`, `opensak.update_<x>()`, `opensak.delete_<x>()`, `opensak.sql_write()`, `opensak.set_corrected()` and `opensak.clear_corrected()` change the active database. Before a macro's first change, OpenSAK asks whether this macro may change your OpenSAK databases: **Deny** (the function fails, and OpenSAK does not ask again during this run), **Until OpenSAK closes**, or **Always**. The answer belongs to the macro file, not to a database: editing the macro keeps it, a moved or renamed file is asked about again. Macros allowed always are listed in Settings → Folder permissions, where they can be removed again. An unsaved macro can only be allowed until OpenSAK closes. A macro started with `opensak.run_macro()` is asked about on its own. A run from an external editor (`opensak --run-macro`) asks in the OpenSAK window, like any other run.

Locked caches (`locked`) are only protected from being overwritten by an import. Macros can change and delete them like any other cache, as the GUI can; to leave them alone, filter them out, e.g. `opensak.caches{ locked = false }`.

Which function to use:

- A cache: `opensak.update(code, fields)` changes it, `opensak.insert{}` adds a new one, `opensak.delete(code)` deletes it with all its rows (also a list of codes).
- Its logs, waypoints, attributes and trackables: `opensak.insert_<x>(code, fields)`, `opensak.update_<x>(id, fields)` and `opensak.delete_<x>(id)` (see Logs, waypoints, attributes and trackables).
- Corrected coordinates: `opensak.set_corrected()` and `opensak.clear_corrected()`.
- `opensak.sql_write()` is the power tool for bulk work: one INSERT, UPDATE or DELETE statement that changes many rows at once, e.g. marking every unfound cache of a country, or copying rows from the macro's own database. Use it when a loop over the functions above would be too slow or too long-winded.

Start with the dedicated functions: they take Lua values, check each field (type, range, date format) with a clear error message, and need no knowledge of the database schema. `opensak.sql_write()` only refuses protected columns and what SQLite itself rejects, and its column names follow the schema, which may change between versions.

Writable cache fields: `name`, `type`, `container`, `lat`, `lon`, `difficulty`, `terrain`, `owner`, `placed_by`, `hidden`, `found`, `found_date`, `dnf`, `dnf_date`, `ftf`, `available`, `archived`, `premium`, `country`, `state`, `county`, `elevation`, `favorite_points`, `find_count`, `user_flag`, `user_sort`, `user_data`, `color`, `locked`, `watch`, `note`, `gc_note`, `hint`, `url`, `corrected`. `false` clears a field that is not a boolean, and "" clears a text field. `user_data` takes the slots to change, e.g. `{ [2] = "solved" }`; `corrected` takes `{ lat = .., lon = .. }` or a coordinate string. Dates are `"YYYY-MM-DD"`. `opensak.insert{}` also needs `code`, and `name`, `type`, `lat`, `lon`.

These fields cannot be written:

- `code` — it identifies the cache
- `distance` — OpenSAK calculates it from the coordinates and the centre point
- `bearing` — OpenSAK calculates it from the coordinates and the centre point
- `waypoint_count` — OpenSAK counts the waypoints
- `log_count` — OpenSAK counts the logs
- `trackable_count` — OpenSAK counts the trackables
- `last_log_date` — OpenSAK takes it from the logs
- `last_gpx_update` — it records when an import last touched the cache

Each change is committed on its own. To keep several changes together, or undo them all on an error, run them inside `opensak.transaction(function() ... end)`, which is also faster for many changes. SQL `BEGIN`/`COMMIT` is refused.

`opensak.sql_write()` may INSERT into, UPDATE and DELETE from these tables. An INSERT only has to give the required columns; every other column gets its default (`opensak.columns()` marks them as `required`). An UPDATE may not set the protected columns. Whatever an INSERT puts into the columns OpenSAK maintains is recalculated right away. A DELETE from `caches` also deletes the logs, waypoints, attributes, trackables and user note of each cache, like `opensak.delete()`.

Deleted caches cannot be restored from inside OpenSAK, only from a backup. At the end of a run that deleted caches, the macro output says how many, per database; the cache list, map and detail panel drop them once the macro has ended. Deletes inside `opensak.transaction()` are undone with it.

OpenSAK recalculates the columns it maintains (distance, bearing, counts, log dates) after every write. Inside `opensak.transaction()` it does so only once, just before the commit, for all caches the transaction changed — much faster for many writes. Until then reads inside the transaction see the old values; `opensak.resync()` recalculates them on demand.

| Table | Required for INSERT | Protected columns |
|---|---|---|
| `attributes` | `cache_id`, `attribute_id` | `cache_id`, `id` |
| `caches` | `gc_code`, `name`, `cache_type`, `latitude`, `longitude` | `bearing`, `distance`, `found_log_count`, `gc_cache_id`, `gc_code`, `guid`, `id`, `imported_at`, `last_found_date`, `last_four_logs`, `last_gpx_update`, `last_log_date`, `location_basis`, `location_dataset`, `location_source`, `location_updated`, `log_count`, `source_file`, `trackable_count`, `waypoint_count` |
| `logs` | `cache_id`, `log_type` | `cache_id`, `id`, `log_id` |
| `trackables` | `cache_id` | `cache_id`, `id` |
| `user_notes` | `cache_id` | `cache_id`, `id` |
| `waypoints` | `cache_id` | `cache_id`, `id`, `parent_gc_code` |

## Logs, waypoints, attributes and trackables

The rows belonging to a cache are read with `opensak.logs()`, `opensak.waypoints()`, `opensak.attributes()`, `opensak.trackables()`, added with `opensak.insert_<x>(code, fields)`, changed with `opensak.update_<x>(id, fields)` and deleted with `opensak.delete_<x>(id)`, without SQL. These are the way to add, change or delete a single row; keep `opensak.sql_write()` for bulk changes across many caches. Each row has an `id`, which identifies it and cannot be changed, as cannot the cache it belongs to. The changes need the user's permission and can be kept together with `opensak.transaction()`, like those of caches; OpenSAK updates the cache's counts and log dates itself.

### Logs

| Field | Type | Meaning |
|---|---|---|
| `type` | `string` | Log type, e.g. "Found it", "Write note". Required for `opensak.insert_log`. |
| `date` | `string?` | Log date "YYYY-MM-DDTHH:MM:SS" (a date alone is midnight). |
| `finder` | `string?` | Name of the logger. |
| `finder_id` | `string?` | geocaching.com id of the logger. |
| `text` | `string?` | Log text. |
| `encoded` | `boolean` | The log text is encoded (ROT13). |
| `lat` | `number?` | Coordinates of the log (e.g. Earthcache logs). |
| `lon` | `number?` | Coordinates of the log. |
| `by_owner` | `boolean` | Logged by the cache owner. |

### Waypoints

| Field | Type | Meaning |
|---|---|---|
| `prefix` | `string` | Waypoint prefix, e.g. "PK", "S1". Required for `opensak.insert_waypoint`. |
| `type` | `string` | Waypoint type, e.g. "Parking Area", "Final Location". Required for `opensak.insert_waypoint`. |
| `name` | `string?` | Name. |
| `code` | `string?` | Waypoint code, unique within the cache. |
| `lat` | `number?` | Latitude; nil if not known yet. |
| `lon` | `number?` | Longitude; nil if not known yet. |
| `description` | `string?` | Description. |
| `comment` | `string?` | Comment. |
| `url` | `string?` | URL. |
| `date` | `string?` | Date "YYYY-MM-DDTHH:MM:SS". |
| `flag` | `boolean` | User flag of the waypoint. |

### Attributes

| Field | Type | Meaning |
|---|---|---|
| `attribute_id` | `integer` | Groundspeak attribute id, unique within the cache. Required for `opensak.insert_attribute`. |
| `name` | `string?` | Name, e.g. "Dogs allowed". |
| `on` | `boolean` | true: yes, false: no (e.g. no dogs). |

### Trackables

| Field | Type | Meaning |
|---|---|---|
| `ref` | `string?` | Reference, e.g. "TB1234". |
| `name` | `string?` | Name. |
| `tracking_code` | `string?` | Tracking code. |

## Example macros

Ready-to-use scripts to copy and adapt are in [`macros/examples/`](../../macros/examples/). Each one starts with a comment explaining what it does and which files it expects.

- [`corrected_coords_form.lua`](../../macros/examples/corrected_coords_form.lua) — Enter or remove the corrected coordinates of the selected cache
- [`corrected_coords_from_csv.lua`](../../macros/examples/corrected_coords_from_csv.lua) — Set corrected coordinates from a CSV file
- [`export_filters_to_gpx.lua`](../../macros/examples/export_filters_to_gpx.lua) — One GPX file per filter
- [`export_with_form.lua`](../../macros/examples/export_with_form.lua) — Choose export options in a GSAK-style form, then export the list
- [`find_with_form.lua`](../../macros/examples/find_with_form.lua) — Pick search options in a form, then step through the matches
- [`import_gsak_appdata.lua`](../../macros/examples/import_gsak_appdata.lua) — Import every GSAK database, filter and location
- [`new_since_last_run.lua`](../../macros/examples/new_since_last_run.lua) — Show the unfound caches updated since the last run

## Macro header

**Macros → Macro manager** lists the macros in your macros folder and its subfolders. It shows what a comment block at the top of each macro says about it:

```lua
-- OpenSAK Macro
-- Name: Find easy traditionals
-- Author: Jane Doe
-- Version: 1.0
-- Requires-API: 3
-- Description: Filters for unfound traditional caches with low difficulty.
```

Every line is optional, and the keys are not case-sensitive. Without a `Name` the file name is shown. `Requires-API` is the lowest `opensak.api_version()` the macro works with: the manager marks a macro that needs a newer one and asks before running it. Only the comment lines before the first line of code are read.

From the manager, **Shortcut…** gives a macro a key (with Ctrl or Alt, or a function key) that runs it from the main window. The output then goes to the manager's output pane.

## Editor support

OpenSAK keeps editor files for this API in its macros folder (**Open macros folder** in the macro window), in `types`, refreshed on every start so they match the installed version:

- [`macros/types/opensak.lua`](../../macros/types/opensak.lua) — for the [Lua Language Server](https://luals.github.io/): VS Code and other editors with LSP support (Neovim, Sublime Text, Zed, Kate, ...)
- [`macros/types/notepad++/lua.xml`](../../macros/types/notepad++/lua.xml) — for Notepad++
- [`macros/types/ultraedit/opensak-lua.uew`](../../macros/types/ultraedit/opensak-lua.uew) — for UltraEdit

### VS Code

The stub gives autocompletion, parameter hints and these docs while you type, through the extension "Lua" by sumneko. OpenSAK puts a `.luarc.json` next to your macros that points the language server at it:

```json
{
  "runtime.version": "Lua 5.4",
  "workspace.library": [
    "types"
  ],
  "diagnostics.globals": [
    "opensak"
  ]
}
```

To set it up in VS Code:

1. Install the extension "Lua" by sumneko.
2. **File → Open Folder…** and pick OpenSAK's macros folder.
3. Open or create a `.lua` file and type `opensak.` — completion, parameter hints and these docs on hover appear.

OpenSAK writes `.luarc.json` only when there is none, so your own settings in it are kept; delete it to get the default back. Macros in another folder can use the stub too: copy `.luarc.json` there and change `types` to the full path of the macros folder's `types` folder. Macros inside the OpenSAK repository pick up [`macros/types/opensak.lua`](../../macros/types/opensak.lua) automatically.

To run a macro in OpenSAK straight from VS Code or another editor, see [Running macros from an editor](run-from-editor.md).

### Notepad++

`types\notepad++\lua.xml` is an auto-completion file for Notepad++: typing `opensak.` lists the API functions, and after `(` a calltip shows the parameters, the return value and the description. When a function takes several parameter lists, the arrows in the calltip switch between them. Notepad++ reads it from its own program folder, so copy it there:

1. Close Notepad++.
2. In Notepad++'s program folder (usually `C:\Program Files\Notepad++`), open `autoCompletion` and rename `lua.xml` to `lua.xml.bak`.
3. Copy `types\notepad++\lua.xml` from OpenSAK's macros folder into `autoCompletion`. Windows asks for administrator rights.
4. Start Notepad++, open **Settings → Preferences… → Auto-Completion**, tick **Enable auto-completion on each input**, choose **Function and word completion** and tick **Function parameters hint on input**.
5. Open a macro and type `opensak.` — the list appears. **Ctrl+Space** opens it by hand, **Ctrl+Shift+Space** the calltip.

The file replaces Notepad++'s own one for Lua. It has calltips for the standard Lua functions too, but leaves out what macros cannot use (`io`, `debug`, `require`, `load`, most of `os`, ...) and functions of older Lua versions; rename `lua.xml.bak` back to undo. Copy the file again after an OpenSAK update to get new functions.

### UltraEdit

`types\ultraedit\opensak-lua.uew` is a wordfile for UltraEdit: Lua syntax highlighting with the `opensak.*` functions in their own colour, the macro's functions in the function list, and the API names in auto-complete. To set it up:

1. Look up the wordfiles folder in **Advanced → Settings → Editor display → Syntax highlighting** (**Full directory path for wordfiles**).
2. Copy `types\ultraedit\opensak-lua.uew` from OpenSAK's macros folder into it. UltraEdit's own `lua.uew` there claims `.lua` files too: move it out of the folder, so the OpenSAK one is used.
3. Restart UltraEdit and open a macro. **View → View as (highlighting file type)** shows **Lua (OpenSAK)**.
4. For completion, turn on auto-complete including the syntax highlighting keywords in **Advanced → Settings → Editor → Auto-complete**; **Ctrl+Space** opens the list by hand.

Copy the wordfile again after an OpenSAK update to get new functions.

## Globals

### print

```lua
print(...)
```

Write the arguments, separated by tabs, to the macro output pane (to stdout when run with `opensak --run-macro`, see [Running macros from an editor](run-from-editor.md)).

## GSAK macros compared

> **Generated by AI — needs careful review.** This comparison was compiled automatically by an AI assistant from the GSAK command list and the OpenSAK API. Statuses and mappings may be wrong or incomplete; check a row against the function's own documentation before relying on it.
>
> **Work in progress.** This section is a comparison, not part of the API: it maps the commands and functions of GSAK's macro language to their OpenSAK counterparts, to help when porting a GSAK macro. It was checked on 2026-10-19 against API version 3 and will change as functions are added.

The sandbox keeps the Lua 5.4 `string`, `table`, `math`, `utf8` and `coroutine` libraries, plus `os.time`, `os.date` and `os.clock`. It removes `io`, `debug`, `package`, `require`, `load*`, `dofile` and the rest of `os`. So most of GSAK's language, string and math functions need no API.

**Legend:** ✅ available · β beta: needs the `lua-macro-write` feature flag, which is on in beta builds (see [Changing caches](#changing-caches)) · 🚧 in development, not in this version yet · ➖ covered by Lua itself, no API needed · ❌ not available yet · 🚫 not applicable or deliberately left out

### Main gaps

- Loading GPX / ZIP files (`Load`). Planned.
- Copying and deleting files (`FileCopy`, `FileErase`). Text files can be read (`read_text`), written (`write_text`, text file types only) and listed (`list_dir`).
- Unicode `Upper` / `Lower`: `string.upper` only changes ASCII letters, so `("zürich"):upper()` gives `"ZüRICH"`.
- Setting the centre point (`Centre`), shared code (`Include`; `run_macro()` runs another macro but shares no functions), sorting by several columns (`SQLSort`).
- Custom fields (`CustomConfig`, `CustomGet`, `CustomPut`): work not started.

### Database / Waypoints

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| AddNew | ✅ β | `insert{}`, `insert_waypoint/log/attribute/trackable` |
| CacheMate | 🚫 | Palm-era format |
| CancelFilter | ✅ | `clear_filter()` |
| CodeMatch | ✅ | `filter{codes=}`, `sql("… WHERE code IN …")` |
| CustomConfig / CustomGet / CustomPut | ❌ | custom fields: work not started; `user_data[1..4]` exist today |
| Database | ✅ partial | `database()`, `switch_database`, `create_database`, `databases`; no delete |
| DatabaseExists | ✅ | `database_exists()` |
| DataSave / DataRestore | ✅ workaround | `settings.save/load` for small values, `macro_db()` + `sql_write` (β) for tables |
| Delete | ✅ β | `delete(code)` or `delete(codes)`, e.g. `delete(codes())` for the active filter |
| Elevation | ✅ partial | `elevation` field (metres); no conversion to the user's unit |
| Export | ✅ partial | `export_file` / `export_gpx` (GPX/LOC/GGZ/KML), `export_poi` (Garmin GPI); CSV or HTML built in Lua and written with `write_text()` |
| Filter | ✅ | `filter_profile(name)`, `profiles()` |
| GarminApi | 🚫 | Opencaching.com is gone |
| GlobalReplace | ✅ β | `sql_write("UPDATE …")`, `update()` |
| GetCountry / GetState / GetCounty | ✅ | `coords.location()`, `country/state/county` fields |
| GetNear | ✅ | `filter{near=}`, `caches{near=}` |
| GetPolygon | ✅ | `coords.inside()`, `filter{polygon=}` |
| GoTo / Seek | ✅ | `select(code)` selects it in the grid; `cache(code)` reads it |
| Grab | ❌ | planned |
| IsOwner | ✅ partial | `filter{owned=true}`; no `owned` cache field, no log-owner check |
| Load | ❌ | planned |
| MacroDelete | ✅ β | `delete()`, `delete_waypoint/log/attribute/trackable`, `sql_write("DELETE …")` |
| MacroFlag | ❌ | workaround: `filter{codes=}` or a table in `macro_db()` |
| MFilter | ✅ | `filter{…}`, `filter{where=}` |
| MoveCopy | ✅ | `move_caches`, `copy_caches` |
| PurgeLogs | ✅ β | `delete_log(id)`, `sql_write("DELETE FROM logs WHERE …")` |
| PurgeNote | ✅ β | `update(code, {note=false, gc_note=false})` |
| SaveFilter / RestoreFilter | ✅ | `save_filter()`, `restore_filter(saved)`; the sort order is not saved |
| ReSync | ✅ β | `resync()` |
| ReSyncLogs | 🚫 | needs the geocaching.com API |
| SetColor | ✅ partial β | `update(code, {color=})` for the cache colour; no per-column colouring |
| SQLGet / SQLNext / SQLGetClose | ✅ | `sql_each()` |
| SQLQuote | ✅ | `?` / `:name` parameters make it unnecessary |
| SQLite | ✅ | `sql`, `sql_write`, `tables`, `columns`, `macro_db` |
| SQLite `ATTACH` / `DETACH` | ✅ partial β | `attach(name, schema)`, `detach(schema)` for OpenSAK databases, read-only (see [Several databases in one query](#several-databases-in-one-query)); other files not yet |
| SQLSort | ✅ partial | `sort(field, dir)`, one field only |
| SqlToHtml | ✅ | `html.table(sql(…), {columns=…})`, wrapped in `html.page{}` and written with `write_text()` |
| Table | ➖ | separate functions per table |
| TotChild | ✅ | `waypoint_count` field, `waypoints(code)` |
| Transaction / InTransaction | ✅ β | `transaction(fn)`, `in_transaction()` |
| UpdateElevation | ❌ | |
| UserFlag | ✅ β | read `user_flag`; write `update(code, {user_flag=})`, `sql_write` |
| UserSort | ✅ β | read `user_sort`; write `update(code, {user_sort=})` |
| View | ❌ | |

### Date / Time

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| DateDiff | ➖ | `(t2 - t1) // 86400` with `date.parse` / `os.time` |
| DateFormat / DateToString | ✅ | `date.format(t, fmt)`, `os.date` |
| DateToSQL / SQLToDate | ✅ | `date.format(t, "%Y-%m-%d")` / `date.parse()` |
| StringToDate | ✅ | `date.parse(text, fmt)` |
| Time | ➖ | `os.date("%H:%M:%S")` |
| ValidDate | ✅ | `pcall(opensak.date.parse, s)` |

### File system

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| AppendFile / PutFile / FileOpen | ✅ | `write_text(path, text)`, `write_text(path, text, {append=true})`; only `.txt .csv .tsv .html .htm .svg .md .json`, at most 20 MB, never an autostart location |
| TextOut | ✅ partial | `write_text()` with the text built in Lua; no CSV helper yet |
| GetFile / FileRead / EndRead / StopRead | ✅ | `read_text(path)` reads the whole file (any file type, UTF-8 or `{encoding="cp1252"}`, at most 10 MB); loop over the lines with `text:gmatch("[^\n]+")`; `read_csv` for CSV |
| FileExists / FolderExists | ✅ | `file_exists(path)`, `folder_exists(path)` |
| Dir | ✅ | `list_dir(folder, "*.gpx")`: name, path, dir, size, modified; not into subfolders |
| FileCopy / FileErase | ❌ | |
| DriveExists | 🚫 | Windows-specific |
| ZipFile | ❌ | |

### Geocaching.com access

| GSAK | Status | Note |
|---|---|---|
| GcApi, GcApi2, GcBalance, GcFavPoints, GcGetCaches, GcGetLogs, GcGetNewToken, GcGetPQ, GcPublishFetch, GcRefresh, GcStatusCheck, GcSwapToken, GcUpdateUserInfo, GeocacherCounts | 🚫 | OpenSAK has no geocaching.com API access |

### Internet

| GSAK | Status | Note |
|---|---|---|
| GetMail / Pop3 | 🚫 | deprecated in GSAK too |
| GetUrl / PostUrl | 🚫 | the sandbox has no network access on purpose |
| Web | ❌ | planned |

### Macro language

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| If / Else / While / Repeat / Break / BreakRepeat / BeginCase / Case / Not | ➖ | Lua control flow |
| BeginSub / EndSub / GoSub / ExitSub | ➖ | `local function` / `return` |
| Declare / Option / RemoveVar / VarExists / Vsub | ➖ | Lua variables (`x = nil`, `x ~= nil`) |
| Return | ➖ | `return` at chunk level |
| Cancel | ✅ | `exit()`, or `error()` for a failure |
| Exit (quit GSAK) | ❌ | not needed |
| Delay | ✅ | `sleep(ms)` (max 10 s per call, 60 s per run) |
| Include | ❌ | `require` is removed |
| Macro | ✅ | `run_macro(path, ...)` |
| MacHeader | ✅ | comment header (`-- Name:`, `-- Version:`, `-- Requires-API:`, …), shown in the Macro manager; see "Macro header" |
| NoError / Deprecated / VerCheck | ✅ | `api_version()`, `version()`, `if opensak.x then` |
| UpdateCheck | 🚫 | online version check |
| SpeedMode | ➖ | the UI refreshes when the macro ends; `refresh()` redraws it earlier |
| Debug | ❌ | only `print` and `log()` |
| InTransaction | ✅ β | `in_transaction()` |

### Mathematical / Numeric

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| Abs, ArcCos, ArcSin, ArcTan, ArcTan2, Cos, Sin, Tan, Exp, Ln, Log, Sqrt, Int, Frac, Random, DegToRad, RadToDeg | ➖ | `math.*` (`math.atan(y, x)`, `math.log(x, base)`, `math.fmod`, `math.rad/deg`) |
| ArcCosh, ArcSinh, ArcTanh, Cosh, Sinh, Tanh, Cotan | ➖ | one-line formulas; not in Lua 5.4 `math` |
| Sqr | ➖ | `x^2` |
| Round | ➖ | `string.format("%.2f", x)` / `math.floor(x * 10^n + 0.5) / 10^n` |
| FormatNumber / Str / NumToStr / Val | ➖ | `string.format`, `tostring`, `tonumber` (no thousands separator) |
| GCalc | ✅ | `coords.parse/format/distance/bearing/project/midpoint` |

### String manipulation

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| AddStr, Alltrim, Trim, Left, Right, SubStr, Len, Asc, Chr, At, AtStart, Replace, Quote, SlashAdd, Equal, IsEmpty, BoolToStr, StrToBool, Keep, Remove, Extract | ➖ | `string.*`, `table.concat`, `gsub`, `find(…, 1, true)`, `gmatch` |
| Upper / Lower | ➖ ⚠️ | ASCII letters only |
| Array / List | ➖ | Lua tables |
| `<Data>` | ➖ | long strings `[[ … ]]` |
| UTF8 / UTF16 | ➖ | strings are UTF-8; `utf8.*` |
| RegEx, RegExData, RegExSub, RegExPos, RegExReplace, RegExCount | ✅ | `re.match/find/findall/replace/split`; count = `#re.findall()` |
| RegExEscape | ❌ | |
| ROT13 | ✅ | `text.rot13` |
| EntityDecode | ✅ partial | `text.html_to_text` decodes entities |
| CsvGet | ❌ | `read_csv` for files only |
| HttpEncode | ❌ | |
| MimeEncode / MimeDecode | ❌ | |
| SmartName | ❌ | |
| Eval | 🚫 | GSAK special tags |

### User input / forms

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| YesNo | ✅ | `confirm()` |
| MsgOK / Pause | ✅ | `message()` |
| Input | ✅ | `input()` |
| Choose | ✅ | `choose()` |
| Form / EditForm | ✅ | `form{}` with callbacks; Tools → Form designer builds forms and imports GSAK forms |
| ShowStatus / ShowStop | ✅ | `print`, `progress()` with a Cancel button |
| (file/folder pickers) | ✅ | `choose_file`, `choose_folder` |

### Miscellaneous

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| Backup / Restore | ✅ partial β | `import_gsak()` imports a GSAK backup .zip, GSAK folder or database; backup planned |
| BBcode | ❌ | |
| CacheType / ChildType | ❌ | |
| Centre | ❌ | planned |
| Clip / GetClipText | ❌ | |
| CustomFormat / RefreshCustomHtml | 🚫 | OpenSAK has no custom HTML formats |
| Dualscreen / SplitScreen | 🚫 | GSAK UI |
| GetEnvV | 🚫 | deliberately not exposed; `temp_dir`/`macros_dir` cover the common use |
| GPSInfo / GPSReceive | 🚫 | |
| GrabImages | ❌ | |
| GsCodeToNum / NumToGsCode | ❌ | |
| ImageInfo / PlayWav | 🚫 | |
| MacroSet | ➖ | options are passed as tables (`export_gpx{}`, `filter{}`) or saved settings (`export_file`) |
| MacSettings | ✅ | `settings.load/save/clear` |
| MakeGGZ | ✅ | `export_file` with a GGZ setting |
| Mtp | ✅ partial | `export_gpx{target="device"}` |
| MakeTranslation / Translate / GetSil | ❌ | |
| RGBColor | ➖ | `string.format("#%02X%02X%02X", r, g, b)` |
| RunPgm | 🚫 | deliberately not in the sandbox |
| SysInfo / SysVarPath | ✅ partial | `version`, `api_version`, `database`, `temp_dir`, `macros_dir` |
| Timer | ➖ | `os.clock()` |
| XmlToSqlite | ❌ | |

### Deprecated in GSAK

DBToSQL, DBToSQLite, SQLiteCaches → `sql()` reads the database directly · GeoCalc → `coords.*` · GetSpecial, HTML, PngToJpg, PreProcess → 🚫 · Set → Lua assignment · ShowForm → `form{}` · Sort → `sort()` · SQL → `sql()`.

GSAK's `$d_*` database variables map to the [cache fields](#cache-fields); custom fields (work not started) and an `owned` field are missing. System variables (`$_CurrentDatabase` → `database()`, `$_Count` → `count()`) were not checked one by one.