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
| [`opensak.filter_profile`](#opensakfilterprofile) | 1 |
| [`opensak.clear_filter`](#opensakclearfilter) | 1 |
| [`opensak.count`](#opensakcount) | 1 |
| [`opensak.profiles`](#opensakprofiles) | 1 |
| [`opensak.set_corrected`](#opensaksetcorrected) | 1 |
| [`opensak.clear_corrected`](#opensakclearcorrected) | 1 |
| [`opensak.read_csv`](#opensakreadcsv) | 1 |
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

Build a filter from the given keys (see Filter keys; all combined with AND) and apply it. Usually called with table syntax: `opensak.filter{ ... }`. When nothing matches, the view is left unchanged.

Parameters:

- `spec` (`opensak.FilterSpec`) — The filter keys.

Returns `integer` — Number of matching caches (0 = view unchanged).

Since API version 1.

Example:

```lua
local n = opensak.filter{ type = "Traditional", difficulty = {1, 2}, found = false }
print("Easy unfound traditionals: " .. n)
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

Keys understood by `opensak.filter{}`, all combined with AND.

| Key | Value | Meaning |
|---|---|---|
| `type` | `"Traditional" \| {"Traditional", "Multi-cache", ...}` | Cache type(s); the " Cache" suffix may be left out. |
| `container` | `"Small" \| {"Micro", "Small", ...}` | Container size(s). |
| `difficulty` | `2 \| {1, 2.5}` | Exact value or {min, max}. |
| `terrain` | `2 \| {1, 2.5}` | Exact value or {min, max}. |
| `found` | `true \| false` | Only found or only unfound caches. |
| `available` | `true` | Only available caches (not disabled or archived). |
| `name`, `code`, `owner`, `country`, `state`, `county` | `"text"` | "Contains" match on that field. |
| `where` | `"SQL WHERE clause"` | Raw clause against the caches table. |
| `label` | `"text"` | Shown in the toolbar (optional, default "Macro"). |

## Example macros

Ready-to-use scripts to copy and adapt are in [`macros/examples/`](../../macros/examples/). Each one starts with a comment explaining what it does and which files it expects.

- [`corrected_coords_from_csv.lua`](../../macros/examples/corrected_coords_from_csv.lua) — set corrected coordinates from a CSV file

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
