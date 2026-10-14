-- csv_import.lua — test fixture for tests/unit-tests/test_macro_runtime.py
--
-- A CSV-import macro in the style of macros/examples/corrected_coords_from_csv.lua,
-- kept separate so the example can change without breaking the tests (and
-- the tests pin the behaviour regardless of what the example looks like).
-- Each test copies one of the CSV fixtures next to it as corrected_coords.csv.
--
-- Rules: coords (any format) or lat+lon → set; coords = "clear" → clear;
-- no coordinates → skipped; only lat or only lon → error for that row.
-- Nothing is written until opensak.confirm() returns true.

local CSV_FILE = "corrected_coords.csv"

local function has(v) return v ~= nil and v ~= "" end

-- What a row asks for: "set", "clear" or "skip". Raises an error for a
-- row without GC code or with half-filled coordinates.
local function plan(row)
    if not has(row.code) then
        error("no GC code", 0)
    elseif has(row.coords) then
        return row.coords:lower() == "clear" and "clear" or "set"
    elseif has(row.lat) and has(row.lon) then
        return "set"
    elseif has(row.lat) then
        error("lon missing", 0)
    elseif has(row.lon) then
        error("lat missing", 0)
    end
    return "skip"
end

local function apply(row, action)
    if action == "clear" then
        return opensak.clear_corrected(row.code)
    elseif has(row.coords) then
        return opensak.set_corrected(row.code, row.coords)
    end
    return opensak.set_corrected(row.code, row.lat, row.lon)
end

local rows = opensak.read_csv(CSV_FILE)
print(("Read %d row(s) from %s"):format(#rows, CSV_FILE))

-- Pass 1: check every row, write nothing
local todo = {}             -- {row, action} for the rows to apply
local to_set, to_clear, skipped, failed = 0, 0, 0, 0

for i, row in ipairs(rows) do
    local ok, action = pcall(plan, row)
    if not ok then
        print(("%s: %s"):format(has(row.code) and row.code or ("Row " .. i), action))
        failed = failed + 1
    elseif action == "skip" then
        print(("%s: no coordinates — skipped"):format(row.code))
        skipped = skipped + 1
    else
        todo[#todo + 1] = { row = row, action = action }
        if action == "set" then to_set = to_set + 1 else to_clear = to_clear + 1 end
    end
end

if #todo == 0 then
    print(("Nothing to do: %d skipped, %d failed"):format(skipped, failed))
    return
end

local question = ("%d will be set, %d cleared (%d skipped, %d invalid).\nContinue?")
    :format(to_set, to_clear, skipped, failed)
if not opensak.confirm(question) then
    print("Cancelled — nothing changed")
    return
end

-- Pass 2: write
local changed = {}          -- GC codes that were updated, for the filter below
local set, cleared, missing = 0, 0, 0

for _, t in ipairs(todo) do
    local row = t.row
    -- pcall so one bad coordinate does not stop the whole run
    local ok, found = pcall(apply, row, t.action)
    if not ok then
        print(("%s: %s"):format(row.code, found))   -- found = error message
        failed = failed + 1
    elseif not found then
        print(("%s: not in the database — skipped"):format(row.code))
        missing = missing + 1
    elseif t.action == "clear" then
        print(("%s: corrected coordinates removed"):format(row.code))
        cleared = cleared + 1
    else
        local coords = has(row.coords) and row.coords or (row.lat .. ", " .. row.lon)
        print(("%s: corrected → %s  %s"):format(row.code, coords, row.note or ""))
        set = set + 1
        changed[#changed + 1] = "'" .. row.code:upper():gsub("'", "''") .. "'"
    end
end

print(("Done: %d set, %d cleared, %d skipped, %d not found, %d failed"):format(
    set, cleared, skipped, missing, failed))

-- Show the caches that just got corrected coordinates
if #changed > 0 then
    opensak.filter{
        where = "gc_code IN (" .. table.concat(changed, ", ") .. ")",
        label = "Corrected via CSV",
    }
end
