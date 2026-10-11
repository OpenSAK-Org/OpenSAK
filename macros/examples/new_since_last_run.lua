-- new_since_last_run.lua — show the unfound caches updated since the last run
--
-- Remembers when it last ran (opensak.settings), and filters the caches
-- that a GPX or pocket query import has added or updated since then — the
-- new ones to look at after loading your weekly pocket queries.
--
-- The first run only notes the time: run it once, import your GPX files,
-- then run it again. The last RUNS_KEPT runs are listed each time.
--
-- The settings belong to this macro alone and are kept by OpenSAK across
-- restarts; set RESET to true for one run to start over.

local RESET = false
local RUNS_KEPT = 5

if RESET then
    opensak.settings.clear()
    print("Settings cleared — the next run starts over.")
    return
end

local s = opensak.settings.load()
s.runs = s.runs or {}       -- list of {time = "...", count = n}, newest first

-- UTC, like the import time OpenSAK stores for each cache.
local now = os.date("!%Y-%m-%dT%H:%M")

if #s.runs == 0 then
    print("First run: from now on, caches imported after " .. now .. " UTC are shown.")
    table.insert(s.runs, 1, { time = now, count = 0 })
else
    local since = s.runs[1].time
    local n = opensak.filter{
        last_gpx_update = { since, nil },
        found = false,
        label = "New since " .. since .. " UTC",
    }
    if n == 0 then
        print("No unfound caches updated since " .. since .. " UTC — view unchanged.")
    else
        print(("%d unfound caches updated since %s UTC"):format(n, since))
    end
    table.insert(s.runs, 1, { time = now, count = n })
end

while #s.runs > RUNS_KEPT do table.remove(s.runs) end
opensak.settings.save(s)

print("Last runs (UTC):")
for _, run in ipairs(s.runs) do
    print(("  %s  %d caches"):format(run.time, run.count))
end
