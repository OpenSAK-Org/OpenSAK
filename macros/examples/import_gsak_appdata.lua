-- OpenSAK Macro
-- Name: Import GSAK data
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 3
-- Description: Import every GSAK database, filter and location
-- Permissions: write, read-files
--
-- Reads GSAK's own folder (%APPDATA%\gsak on Windows: gsak.db3 plus
-- data\<name>\sqlite.db3) and imports, without asking anything. Set
-- GSAK_SOURCE to a GSAK backup .zip (File → Backup) to import that instead;
-- its databases are unpacked one at a time into a temp folder.
--   * every GSAK database into the OpenSAK database of the same name
--     (created when it doesn't exist yet),
--   * GSAK's saved filters as OpenSAK filter profiles (FILTERS),
--   * GSAK's user locations as OpenSAK locations (LOCATIONS).
--
-- Run it again whenever GSAK has new data: with IF_EXISTS = "merge" new
-- caches are added and existing ones updated (locked caches are kept).
-- Started with --run-macro (e.g. from Task Scheduler), it ends with exit
-- code 1 when anything failed.
--
-- It asks for confirmation first, since it changes OpenSAK databases with
-- the same names as your GSAK databases. For runs nobody watches set ASK to
-- false, and answer "Always" when OpenSAK asks whether this macro may change
-- your databases and read the GSAK folder.

local GSAK_SOURCE = "%APPDATA%/gsak"   -- GSAK folder (File → Data folder) or backup .zip, e.g. "C:/Backups/GSAKAuto1.zip"
local IF_EXISTS = "skip"              -- existing OpenSAK database: "skip" | "merge" | "overwrite"
local FILTERS = "skip"                 -- saved filters: "skip" (keep existing) | "overwrite" | false (don't import)
local LOCATIONS = "skip"               -- user locations: "skip" (keep existing) | "overwrite" | false (don't import)
local EXCLUDE = {}                     -- GSAK databases to leave out, e.g. {"Test"}
local ASK = true                       -- false: no confirmation (scheduled runs)

if ASK then
    local what = {
        skip = "OpenSAK databases that already exist are left untouched.",
        merge = "OpenSAK databases that already exist are updated: new caches "
            .. "are added and existing caches are overwritten with GSAK's data "
            .. "(locked caches are kept).",
        overwrite = "OpenSAK databases that already exist are EMPTIED first and "
            .. "replaced by GSAK's data. Anything only in OpenSAK is lost.",
    }
    local function settings_line(label, mode)
        if not mode then return label .. ": not imported." end
        return label .. ": imported, existing ones with the same name are "
            .. (mode == "overwrite" and "replaced." or "kept.")
    end
    local question = "Import every GSAK database from\n" .. GSAK_SOURCE
        .. "\ninto the OpenSAK databases of the same name?\n\n"
        .. what[IF_EXISTS] .. "\n\n"
        .. settings_line("Saved filters", FILTERS) .. "\n"
        .. settings_line("User locations", LOCATIONS)
        .. "\n\nContinue?"
    if not opensak.confirm(question) then
        print("Cancelled — nothing imported.")
        return
    end
end

local ok, r = pcall(opensak.import_gsak, GSAK_SOURCE, {
    exclude = EXCLUDE,
    if_exists = IF_EXISTS,
    filters = FILTERS,
    locations = LOCATIONS,
    quiet = true,                      -- the summary is printed below instead
})
if not ok then
    print("Nothing imported: " .. tostring(r))
    opensak.exit(1)
end

print(("GSAK import from %s"):format(r.source))
for _, db in ipairs(r.databases) do
    local line = ("  %-20s → %-20s %s"):format(db.gsak, db.target, db.action)
    if db.created then
        line = line .. (" (%d new, %d updated)"):format(db.created, db.updated)
    end
    if db.reason then line = line .. " — " .. db.reason end
    print(line)
end

if r.filters then
    if r.filters.error then
        print("  Filters failed: " .. r.filters.error)
    else
        print(("  Filters: %d imported, %d kept"):format(r.filters.imported, r.filters.skipped))
    end
end
if r.locations then
    if r.locations.error then
        print("  Locations failed: " .. r.locations.error)
    else
        print(("  Locations: %d added, %d updated, %d kept, %d invalid"):format(
            r.locations.added, r.locations.updated, r.locations.skipped, r.locations.invalid))
    end
end
for _, note in ipairs(r.notes) do print("  Note: " .. note) end

print(("Total: %d new and %d updated caches, %d errors"):format(r.created, r.updated, r.errors))
if not r.ok then opensak.exit(1) end
