-- corrected_coords_from_csv.lua — set corrected coordinates from a CSV file
--
-- Reads corrected_coords.csv (next to this macro) and stores the solved
-- coordinates of each listed cache as its corrected coordinates.
--
-- CSV layout (header line required, separator , ; or tab is detected):
--
--     code;coords;note
--     GC1;N47 22.123 E008 32.456;Mystery solved via checksum
--
-- "coords" accepts every format OpenSAK understands (DMM, DMS, decimal
-- degrees). Instead of one "coords" column the file may also have separate
-- "lat" and "lon" columns in decimal degrees.
--
-- Empty never deletes anything — a blank cell is far more often a typo or a
-- cache not solved yet than a wish to throw away a solution:
--
--   * no coordinates in the row   → skipped (listed in the output)
--   * only lat or only lon        → error for that row; the rest carries on
--   * coords cell reads "clear"   → corrected coordinates are removed
--
-- Open this file via Macros → Run macro… → Open… so the relative CSV path
-- is resolved against this folder.

local CSV_FILE = "corrected_coords.csv"

local function has(v) return v ~= nil and v ~= "" end

-- Returns (found, action) or raises an error for bad or half-filled
-- coordinates. found is nil for a skipped row.
local function apply(row)
    if has(row.coords) then
        if row.coords:lower() == "clear" then
            return opensak.clear_corrected(row.code), "cleared"
        end
        return opensak.set_corrected(row.code, row.coords), "set"
    elseif has(row.lat) and has(row.lon) then
        return opensak.set_corrected(row.code, row.lat, row.lon), "set"
    elseif has(row.lat) then
        error("lon missing", 0)
    elseif has(row.lon) then
        error("lat missing", 0)
    end
    return nil, "skipped"
end

local rows = opensak.read_csv(CSV_FILE)
print(("Read %d row(s) from %s"):format(#rows, CSV_FILE))

local changed = {}          -- GC codes that were updated, for the filter below
local set, cleared, skipped, missing, failed = 0, 0, 0, 0, 0

for i, row in ipairs(rows) do
    if not has(row.code) then
        print(("Row %d: no GC code — skipped"):format(i))
        failed = failed + 1
    else
        -- pcall so one bad line does not stop the whole run
        local ok, found, action = pcall(apply, row)
        if not ok then
            print(("%s: %s"):format(row.code, found))   -- found = error message
            failed = failed + 1
        elseif action == "skipped" then
            print(("%s: no coordinates — skipped"):format(row.code))
            skipped = skipped + 1
        elseif not found then
            print(("%s: not in the database — skipped"):format(row.code))
            missing = missing + 1
        elseif action == "cleared" then
            print(("%s: corrected coordinates removed"):format(row.code))
            cleared = cleared + 1
        else
            local coords = has(row.coords) and row.coords or (row.lat .. ", " .. row.lon)
            print(("%s: corrected → %s  %s"):format(row.code, coords, row.note or ""))
            set = set + 1
            changed[#changed + 1] = "'" .. row.code:upper():gsub("'", "''") .. "'"
        end
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
