---@meta
-- Generated from src/opensak/macro/runtime.py by scripts/generate_macro_api_docs.py — do not edit by hand.
-- OpenSAK Lua macro API, version 1. Reference: docs/macros/api.md

---Keys understood by `opensak.filter{}`, all combined with AND.
---@class opensak.FilterSpec
---@field type? string|string[] Cache type(s); the " Cache" suffix may be left out.
---@field container? string|string[] Container size(s).
---@field difficulty? number|number[] Exact value or {min, max}.
---@field terrain? number|number[] Exact value or {min, max}.
---@field found? boolean Only found or only unfound caches.
---@field available? boolean Only available caches (not disabled or archived).
---@field name? string "Contains" match on that field.
---@field code? string "Contains" match on that field.
---@field owner? string "Contains" match on that field.
---@field country? string "Contains" match on that field.
---@field state? string "Contains" match on that field.
---@field county? string "Contains" match on that field.
---@field where? string Raw clause against the caches table.
---@field label? string Shown in the toolbar (optional, default "Macro").

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

---Build a filter from the given keys (see Filter keys; all combined with AND) and apply it. Usually called with table syntax: `opensak.filter{ ... }`. When nothing matches, the view is left unchanged.
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

---Read a CSV file (UTF-8) into an array of rows keyed by the header line. A relative path is resolved against the macro file's folder. The file must lie in a folder with read permission (Settings → Folder permissions) and may be at most 10 MB.
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

---The system temp folder (read and write permission by default), without a trailing separator. "/" works as separator on every platform.
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
