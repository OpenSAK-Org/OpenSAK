-- export_filters_to_gpx.lua — one GPX file per saved filter
--
-- Applies each filter in FILTERS in turn and exports the caches it shows
-- with the saved export setting EXPORT_SETTING, so every filter ends up in
-- a file of its own. The files go into OpenSAK's temp folder
-- (opensak.temp_dir(), write permission by default), whatever folder the
-- setting names.
--
-- Setup, once:
--
--   1. File → Export → GPX/LOC/GGZ: choose GPX and a file name containing
--      {filter}, e.g. "{filter}_{date}" or "{filter}_{center}", then save
--      the options as "GPX Export". Without {filter} every filter would
--      write the same file — the macro stops when that happens.
--   2. Put the names of three of your saved filters into FILTERS below.
--
-- A filter that is not saved, or that matches no cache, is skipped: with no
-- match the view is left unchanged, so exporting would write the caches of
-- the previous filter again. What happens to an existing file follows the
-- setting's "if the file exists" option. The last exported filter stays
-- active afterwards.

local EXPORT_SETTING = "GPX Export"
local FILTERS = { "Traditionals", "Multi-caches", "Mysteries" }
local FOLDER = opensak.temp_dir()

local saved = {}
for _, name in ipairs(opensak.profiles()) do saved[name] = true end

local written = {}          -- file path → filter that wrote it
local exported, skipped = 0, 0

for _, name in ipairs(FILTERS) do
    if not saved[name] then
        print(("%s: no saved filter with this name — skipped"):format(name))
        skipped = skipped + 1
    elseif opensak.filter_profile(name) == 0 then
        print(("%s: no caches match — skipped"):format(name))
        skipped = skipped + 1
    else
        local path, count = opensak.export_file(EXPORT_SETTING, FOLDER)
        if not path then
            print(("%s: file exists — not overwritten"):format(name))
            skipped = skipped + 1
        elseif written[path] then
            error(("%s and %s both exported to %s — add {filter} to the file name of the export setting %q")
                :format(written[path], name, path, EXPORT_SETTING), 0)
        else
            written[path] = name
            print(("%s: %d caches → %s"):format(name, count, path))
            exported = exported + 1
        end
    end
end

print(("Done: %d exported, %d skipped (folder: %s)"):format(exported, skipped, FOLDER))
