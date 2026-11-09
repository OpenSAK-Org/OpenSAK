-- OpenSAK Macro
-- Name: Region coverage
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 3
-- Description: Write an HTML page of the states (or countries) you have found a cache in, and where to go next
-- Permissions: open
--
-- Known limitations:
-- - This is a template macro: copy it and adapt the statistics, texts and
--   layout to your needs. It is not a finished report.
-- - The page cannot be used on geocaching.com (e.g. pasted into your
--   profile): the profile editor only keeps very limited HTML and removes
--   the inline SVG charts, maps and the stylesheet.
-- - Maps and the list of all regions use OpenSAK's boundary data (the
--   outlines of Update location), which is not yet stable: outlines, names
--   and the country of a state may still change. Without the boundary data
--   the macro stops with a note.
-- - Region names come from the cache data (GPX / GSAK import or Update
--   location) and are matched to the boundary data ignoring case, accents
--   and punctuation ("Graubunden" = "Graubünden"), like the maps do. A name
--   in another language or spelled differently matches no region: it is
--   not counted and is listed under "Names that match no region".
-- - The states are those of one country: COUNTRY below, or else the
--   country with the most finds. If you found more caches abroad than at
--   home, set COUNTRY.
-- - Only finds in the database count; caches without a country or state
--   are left out.
-- - "Nearest cache still to find" is the nearest active, unfound cache in
--   the database, in a straight line from home — not a route, and only
--   among the caches you have imported.
-- - Home is HOME below, else the active centre point (the centre drop-down
--   in the toolbar).
-- - The page is in English only; dates are shown as stored (YYYY-MM-DD).
--
-- For a "find a cache in every state / canton / country" challenge: reads
-- all caches of the active database (or of DATABASE), compares the regions
-- you have found a cache in with every region of the boundary data and
-- writes region_coverage.html into OpenSAK's temp folder: how many regions
-- you have, a map, when you got each one, and for every missing region the
-- nearest cache still to find. Then opens the page in your browser.
--
-- Built from opensak.stats.*, opensak.chart.*, opensak.geo.* and
-- opensak.html.*, like find_statistics.lua.

local DATABASE = nil    -- e.g. "Switzerland"; nil = the active database
local LAYER = "state"   -- "state" (in COUNTRY) or "country"
local COUNTRY = nil     -- for LAYER "state", e.g. "Switzerland"; nil = the country with most finds
local HOME = nil        -- e.g. { lat = 47.3769, lon = 8.5417 }; nil = the active centre point
local FILE = opensak.temp_dir() .. "/region_coverage.html"
local OPEN = true       -- open the page in the browser when done

local stats, chart, html = opensak.stats, opensak.chart, opensak.html

-- The region of the boundary data a name from the cache data stands for,
-- matched like the maps match it (case, accents, punctuation); nil if
-- none. Each name is looked up once.
local known = {}
local function region_of(layer, name, within)
    if not name or name == "" then return nil end
    local id = layer .. "|" .. (within or "") .. "|" .. name
    if known[id] == nil then
        known[id] = false
        local all = opensak.geo.regions(layer, { within = within, rows = { { key = name } } })
        for _, r in ipairs(all or {}) do
            if r.count then known[id] = r.name break end
        end
    end
    return known[id] or nil
end

-- ── The caches ───────────────────────────────────────────────────────────
local caches, finds = {}, {}
for c in opensak.caches{
    all = true, database = DATABASE,
    fields = { "name", "type", "found", "found_date", "archived", "available",
               "country", "state", "lat", "lon", "url" },
} do
    caches[#caches + 1] = c
    if c.found then finds[#finds + 1] = c end
end
local name = DATABASE or opensak.database()
if #finds == 0 then
    print("No found caches in " .. name .. ".")
    return
end
if not opensak.geo.regions("country") then
    print("The boundary data is not installed (see Update location).")
    return
end

local country = COUNTRY
if LAYER == "state" and not country then
    local top = stats.group(finds, "country", { sort = "count" })[1]
    country = top and top.key
    if not country then
        print("Your finds have no country: run Update location first.")
        return
    end
end
if LAYER == "state" then
    -- The country as the boundary data names it.
    local matched = region_of("country", country)
    if not matched then
        print("The boundary data has no country " .. country .. ": set COUNTRY.")
        return
    end
    country = matched
end
local area = LAYER == "state" and country or "the world"

local regions = LAYER == "state" and opensak.geo.regions("state", { within = country })
    or opensak.geo.regions("country")
if #regions == 0 then
    print("The boundary data has no " .. LAYER .. "s for " .. area .. ".")
    return
end

-- Home: HOME, else the active centre point (the centre drop-down in the
-- toolbar, which OpenSAK's distances are measured from), else the middle
-- of your finds.
local center = opensak.center()
local home = HOME or (center.lat and center.lon and { lat = center.lat, lon = center.lon })
    or { lat = stats.summary(finds, "lat").median, lon = stats.summary(finds, "lon").median }

-- The region of a cache, or nil; for states only the caches in COUNTRY.
-- The second value is true if the cache names a region that matches none.
local function region_name(c)
    if LAYER == "country" then
        local r = region_of("country", c.country)
        return r, r == nil and c.country ~= nil
    end
    if region_of("country", c.country) ~= country then return nil, false end
    local r = region_of("state", c.state, country)
    return r, r == nil and c.state ~= nil
end

-- ── Per region: finds, first find, nearest cache still to find ────────────
local by_name = {}
for _, r in ipairs(regions) do
    by_name[r.name] = { name = r.name, finds = 0, open = 0 }
end

local unmatched = {}
for _, c in ipairs(caches) do
    local r, no_match = region_name(c)
    local region = r and by_name[r]
    if no_match and c.found then
        unmatched[c[LAYER]] = (unmatched[c[LAYER]] or 0) + 1
    elseif region and c.found then
        region.finds = region.finds + 1
        if c.found_date and (not region.first or c.found_date < region.first) then
            region.first, region.first_code, region.first_url = c.found_date, c.code, c.url
        end
    elseif region and not c.archived and c.available then
        region.open = region.open + 1
        local km = opensak.coords.distance(home.lat, home.lon, c.lat, c.lon)
        if not region.km or km < region.km then
            region.km, region.code, region.cache, region.url = km, c.code, c.name, c.url
        end
    end
end

local have, missing = {}, {}
for _, r in ipairs(regions) do
    local region = by_name[r.name]
    if region.finds > 0 then have[#have + 1] = region else missing[#missing + 1] = region end
end
for _, r in ipairs(missing) do
    if r.km then r.km = math.floor(r.km + 0.5) end
end
have = stats.sort(have, "first", "name")
missing = stats.sort(missing, "km", "name")      -- no cache to find: last

local unmatched_rows = {}
for n, count in pairs(unmatched) do unmatched_rows[#unmatched_rows + 1] = { key = n, count = count } end
unmatched_rows = stats.sort(unmatched_rows, "-count", "key")

-- The regions on the map: finds per region.
local map_rows = {}
for _, r in ipairs(have) do map_rows[#map_rows + 1] = { key = r.name, count = r.finds } end

-- New regions per year, and the running total.
local per_year = stats.group(have, "first", { period = "year" })
local growth = stats.running(stats.fill(per_year, { period = "year" }))

-- ── Page ─────────────────────────────────────────────────────────────────
local label = LAYER == "state" and "states" or "countries"
local reachable = 0
for _, r in ipairs(missing) do if r.code then reachable = reachable + 1 end end

local body = {
    html.tiles{
        { label = label .. " found", value = #have .. " / " .. #regions },
        { label = "coverage", value = string.format("%.0f %%", 100 * #have / #regions) },
        { label = label .. " missing", value = #missing },
        { label = "missing with a cache to find", value = reachable },
        { label = "finds in " .. area, value = (function()
            local n = 0
            for _, r in ipairs(have) do n = n + r.finds end
            return n
        end)() },
    },

    "<h2>Map</h2>",
    chart.map(map_rows, { layer = LAYER, within = LAYER == "state" and country or nil,
                          extent = "all", width = 900, title = "Finds per " .. LAYER }),

    "<h2>Missing " .. label .. ": nearest cache still to find</h2>",
    #missing > 0 and html.table(missing, {
        columns = { { "name", LAYER == "state" and "State" or "Country" }, { "km", "km" },
                    { "code", "Code" }, { "cache", "Cache" }, { "open", "Caches to find" } },
        link = { code = "url" },
    }) or "<p>None — you have them all.</p>",

    "<h2>New " .. label .. " per year</h2>",
    chart.bar(growth, { width = 900, title = "New " .. label .. " per year" }),
    chart.line(growth, { y = "total", area = true, width = 900, title = label .. " in total" }),

    "<h2>Found " .. label .. ", in the order you got them</h2>",
    html.table(have, {
        columns = { { "first", "First find" }, { "name", LAYER == "state" and "State" or "Country" },
                    { "first_code", "First cache" }, { "finds", "Finds" } },
        link = { first_code = "first_url" },
        bars = { "finds" },
    }),
}
if #unmatched_rows > 0 then
    body[#body + 1] = "<h2>Names that match no region</h2>"
    body[#body + 1] = "<p>Finds whose " .. LAYER .. " is spelled differently from the "
        .. "boundary data; they are not counted above.</p>"
    body[#body + 1] = html.table(unmatched_rows, {
        columns = { { "key", LAYER == "state" and "State" or "Country" }, { "count", "Finds" } },
    })
end

local page = html.page{
    title = "Region coverage: " .. label .. " in " .. area,
    subtitle = name,
    footer = "Written by OpenSAK " .. opensak.version() .. " on " .. os.date("%Y-%m-%d %H:%M"),
    body = body,
}

local path = opensak.write_text(FILE, page)
print(#have .. " of " .. #regions .. " " .. label .. " → " .. path)
if OPEN then opensak.open(path) end
