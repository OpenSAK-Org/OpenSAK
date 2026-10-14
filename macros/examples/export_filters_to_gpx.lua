-- export_filters_to_gpx.lua — one GPX file per filter
--
-- Applies each filter in FILTERS in turn and exports the caches it shows
-- to <label>.gpx in OpenSAK's temp folder (opensak.temp_dir(), write
-- permission by default). An existing file is overwritten.
--
-- Each entry of FILTERS is passed as it is to opensak.filter{}, so it may
-- use any filter key (type, difficulty, found, near, corrected, ...; see
-- "Filter keys" in the macro API docs). Its label names the output file
-- and is shown in the toolbar. Adapt the entries to your needs.
--
-- A filter that matches no cache is skipped: with no match the view is left
-- unchanged, so exporting would write the caches of the previous filter
-- again. The last exported filter stays active afterwards.

local FILTERS = {
    { label = "Easy traditionals", type = "Traditional", difficulty = {1, 2} },
    { label = "Multi-caches",      type = "Multi-cache" },
    { label = "Solved mysteries",  type = "Unknown", corrected = true },
}
local FOLDER = opensak.temp_dir()

local exported, skipped = 0, 0

for _, spec in ipairs(FILTERS) do
    local name = spec.label
    if opensak.filter(spec) == 0 then
        print(("%s: no caches match — skipped"):format(name))
        skipped = skipped + 1
    else
        -- {filter} is replaced with the label of the active filter
        local path, count = opensak.export_gpx{ path = FOLDER .. "/{filter}.gpx" }
        if path then
            print(("%s: %d caches → %s"):format(name, count, path))
            exported = exported + 1
        else
            print(("%s: no caches with coordinates — skipped"):format(name))
            skipped = skipped + 1
        end
    end
end

print(("Done: %d exported, %d skipped (folder: %s)"):format(exported, skipped, FOLDER))
