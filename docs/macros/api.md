<!-- Generated from src/opensak/macro/runtime.py by scripts/generate_macro_api_docs.py — do not edit by hand. -->

# OpenSAK Lua macro API

API version: **1** (`opensak.api_version()`).

Macros are Lua 5.4 scripts run in a sandbox. They talk to OpenSAK through the global `opensak` table. File access is limited to the folders listed in Settings → Folder permissions.

See [Example macros](#example-macros) for complete scripts.

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
| [`opensak.temp_dir`](#opensaktempdir) | 1 |
| [`opensak.macros_dir`](#opensakmacrosdir) | 1 |

### opensak.api_version

```lua
opensak.api_version()
```

The API version of this OpenSAK build. Each function below lists the version it was added in.

Since API version 1.

Example:

```lua
if opensak.api_version() < 1 then
    error("this macro needs a newer OpenSAK")
end
```

### opensak.filter

```lua
opensak.filter{ key = value, ... }
```

Build a filter from the given keys (see Filter keys; all combined with AND) and apply it. Returns the number of matching caches. When nothing matches, 0 is returned and the view is left unchanged.

Since API version 1.

Example:

```lua
local n = opensak.filter{ type = "Traditional", difficulty = {1, 2}, found = false }
print("Easy unfound traditionals: " .. n)
```

### opensak.filter_profile

```lua
opensak.filter_profile("Name")
```

Apply a saved filter profile. Returns the number of matching caches.

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

Since API version 1.

Example:

```lua
print(opensak.count() .. " caches shown")
```

### opensak.profiles

```lua
opensak.profiles()
```

An array with the names of all saved filter profiles.

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
opensak.set_corrected(code, "N47 22.123 E008 32.456")
```

Set corrected coordinates, either as decimal degrees or as one coordinate string in any format OpenSAK understands. Returns false if the cache is not in the database.

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

Remove the corrected coordinates of a cache. Returns false if the cache is not in the database.

Since API version 1.

Example:

```lua
opensak.clear_corrected("GC12345")
```

### opensak.read_csv

```lua
opensak.read_csv(path [, sep])
```

Read a CSV file (UTF-8) into an array of rows keyed by the header line. The separator (, ; or tab) is detected unless given. A relative path is resolved against the macro file's folder. The file must lie in a folder with read permission (Settings → Folder permissions) and may be at most 10 MB.

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

Ask the user a Yes/No question. Returns true on Yes.

Since API version 1.

Example:

```lua
if not opensak.confirm("Update 12 caches?") then return end
```

### opensak.temp_dir

```lua
opensak.temp_dir()
```

The system temp folder (read and write permission by default), without a trailing separator. "/" works as separator on every platform.

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

Since API version 1.

Example:

```lua
local rows = opensak.read_csv(opensak.macros_dir() .. "/data/solved.csv")
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

## Globals

### print

```lua
print(...)
```

Write the arguments, separated by tabs, to the macro output pane.
