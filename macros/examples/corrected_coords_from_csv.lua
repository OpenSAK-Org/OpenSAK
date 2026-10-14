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
-- Every row is checked first (GC code in the database, coordinates
-- readable), and nothing is written until you confirm the summary
-- ("12 will be set, 2 cleared — continue?").
--
-- Open it via Macros → Open example: that copies this macro and the sample
-- CSV into your macros folder, where the relative CSV path is resolved.

local CSV_FILE = "corrected_coords.csv"

local function has(v) return v ~= nil and v ~= "" end

-- What a row asks for: "clear", "skip" or "set" plus lat, lon. Raises an
-- error for a row that cannot be applied.
local function plan(row)
    if not has(row.code) then
        error("no GC code", 0)
    elseif not opensak.cache(row.code) then
        error("not in the database", 0)
    end
    local coords
    if has(row.coords) then
        if row.coords:lower() == "clear" then return "clear" end
        coords = row.coords
    elseif has(row.lat) and has(row.lon) then
        coords = row.lat .. ", " .. row.lon
    elseif has(row.lat) then
        error("lon missing", 0)
    elseif has(row.lon) then
        error("lat missing", 0)
    else
        return "skip"
    end
    local lat, lon = opensak.coords.parse(coords)
    if not lat then error(("cannot read coordinates %q"):format(coords), 0) end
    return "set", lat, lon
end

local rows = opensak.read_csv(CSV_FILE)
print(("Read %d row(s) from %s"):format(#rows, CSV_FILE))

-- Pass 1: check every row, write nothing
local todo = {}             -- {code, action, lat, lon} for the rows to apply
local to_set, to_clear, skipped, failed = 0, 0, 0, 0

for i, row in ipairs(rows) do
    local ok, action, lat, lon = pcall(plan, row)
    if not ok then
        print(("%s: %s"):format(has(row.code) and row.code or ("Row " .. i), action))
        failed = failed + 1
    elseif action == "skip" then
        print(("%s: no coordinates — skipped"):format(row.code))
        skipped = skipped + 1
    else
        todo[#todo + 1] = { code = row.code, action = action, lat = lat, lon = lon,
                            note = row.note }
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

-- Pass 2: write — every row was checked above, so nothing can fail here
local changed = {}          -- GC codes that got corrected coordinates

for _, t in ipairs(todo) do
    if t.action == "clear" then
        opensak.clear_corrected(t.code)
        print(("%s: corrected coordinates removed"):format(t.code))
    else
        opensak.set_corrected(t.code, t.lat, t.lon)
        print(("%s: corrected → %s  %s"):format(
            t.code, opensak.coords.format(t.lat, t.lon), t.note or ""))
        changed[#changed + 1] = t.code
    end
end

print(("Done: %d set, %d cleared, %d skipped, %d failed"):format(
    to_set, to_clear, skipped, failed))

-- Show the caches that just got corrected coordinates
if #changed > 0 then
    opensak.filter{ codes = changed, label = "Corrected via CSV" }
end
