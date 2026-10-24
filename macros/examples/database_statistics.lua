-- OpenSAK Macro
-- Name: Database statistics
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 3
-- Description: Write an HTML page with statistics and maps of all caches in the database
-- Permissions: open
--
-- Known limitations:
-- - This is a template macro: copy it and adapt the statistics, texts and
--   layout to your needs. It is not a finished report.
-- - The page cannot be used on geocaching.com (e.g. pasted into your
--   profile): the profile editor only keeps very limited HTML and removes
--   the inline SVG charts, maps and the stylesheet.
-- - Maps use OpenSAK's boundary data (the outlines of Update location),
--   which is not yet stable: outlines, names and the country of a state may
--   still change. Without the boundary data the maps are a short note, and
--   county outlines are downloaded per country on first use.
-- - Region names come from the cache data (GPX / GSAK import or Update
--   location) and are matched to the outlines ignoring case, accents and
--   punctuation; names spelled differently (e.g. in another language) are
--   listed below the map as "Not on the map". Caches without a country or
--   state are left out of those statistics.
-- - The page is in English only; dates are shown as stored (YYYY-MM-DD).
-- - The statistics describe what is in the database, not geocaching.com:
--   archived and disabled caches are only as current as your last import,
--   and favourite points as of the import of each cache.
-- - All caches of the database are read, whatever the active filter shows;
--   a large database (100 000+ caches) takes a while. The dot map shows at
--   most MAX_DOTS caches (every n-th one).
-- - "Home" for the direction is HOME below, else the active centre point
--   (the centre drop-down in the toolbar).
-- - The states shown are those of one country: COUNTRY below, or else the
--   country with the most caches in the database. In a database mixing
--   countries (e.g. a holiday area larger than your home), set COUNTRY.
-- - Countries and states are counted as the regions of the boundary data
--   that the names in the cache data match, so "states in …" can never be
--   more than all states. Names that match no region are listed at the end.
--
-- Reads every cache of the active database (or of DATABASE) and writes
-- database_statistics.html into OpenSAK's temp folder: key figures, caches
-- per type, size, status and year placed, difficulty × terrain (all caches
-- and the ones you still can find), maps of the countries and states,
-- where the caches still to find are, the direction from home, owners and
-- the caches with the most favourite points, then opens it in your browser.
--
-- Built from the generic opensak.stats.*, opensak.chart.*, opensak.geo.* and
-- opensak.html.* functions, like find_statistics.lua.

local DATABASE = nil    -- e.g. "Switzerland"; nil = the active database
local HOME = nil        -- e.g. { lat = 47.3769, lon = 8.5417 }; nil = the active centre point
local COUNTRY = nil     -- e.g. "United States": whose states are shown; nil = the country with most caches
local TOP = 15          -- rows in the region, owner and favourite lists
local MAX_DOTS = 20000  -- the dot map shows at most this many caches
local FILE = opensak.temp_dir() .. "/database_statistics.html"
local OPEN = true        -- open the page in the browser when done

local stats, chart, html, date = opensak.stats, opensak.chart, opensak.html, opensak.date

-- ── All caches, with a few fields derived for counting ───────────────────
local caches = {}
for c in opensak.caches{
    all = true, database = DATABASE,
    fields = { "name", "type", "container", "difficulty", "terrain", "hidden", "found",
               "archived", "available", "premium", "country", "state", "owner",
               "favorite_points", "url", "lat", "lon" },
} do
    c.status = c.archived and "archived" or (c.available and "active" or "disabled")
    c.found_n = c.found and 1 or 0
    c.to_find = (not c.found and c.status == "active") and 1 or 0
    c.region = (c.country or "?") .. (c.state and (" / " .. c.state) or "")
    c.year = c.hidden and date.period(c.hidden, "year")
    caches[#caches + 1] = c
end
local name = DATABASE or opensak.database()
if #caches == 0 then
    print("Database " .. name .. " has no caches.")
    return
end

local function percent(part, whole)
    return whole > 0 and string.format("%.0f %%", 100 * part / whole) or ""
end

-- Home: given, or the middle of the caches you found (else of all caches).
local found_caches, open_caches = {}, {}
for _, c in ipairs(caches) do
    if c.found then found_caches[#found_caches + 1] = c end
    if c.to_find == 1 then open_caches[#open_caches + 1] = c end
end
local around = #found_caches > 0 and found_caches or caches
-- Home: HOME, else the active centre point (the centre drop-down in the
-- toolbar, which OpenSAK's distances are measured from), else the middle
-- of the caches you found (else of all caches).
local center = opensak.center()
local home = HOME or (center.lat and center.lon and { lat = center.lat, lon = center.lon })
    or { lat = stats.summary(around, "lat").median, lon = stats.summary(around, "lon").median }
local DIRS = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" }
for _, c in ipairs(open_caches) do
    local deg = opensak.coords.bearing(home.lat, home.lon, c.lat, c.lon)
    c.dir = DIRS[math.floor((deg + 22.5) / 45) % 8 + 1]
end

-- ── Statistics ───────────────────────────────────────────────────────────
local found = #found_caches
local to_find = #open_caches
local hidden = stats.summary(caches, "hidden")
local favs = stats.summary(caches, "favorite_points")
local status = stats.group(caches, "status", { sort = "count" })
local placing = stats.streaks(caches, "hidden")

-- Per type, with the share you have found.
local types = stats.group(caches, "type", { sort = "count", sum = "found_n" })
for _, t in ipairs(types) do t.share = percent(t.sum, t.count) end

local sizes = stats.group(caches, "container", { sort = "count" })
local by_status = stats.pivot(caches, "year", "status", { cols = { "active", "disabled", "archived" } })
local placed = stats.running(stats.fill(stats.group(caches, "hidden", { period = "year" }), { period = "year" }))

local STEPS = { 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 }
local dt_all = stats.pivot(caches, "difficulty", "terrain", { rows = STEPS, cols = STEPS })
local dt_todo = stats.pivot(caches, "difficulty", "terrain",
    { rows = STEPS, cols = STEPS, sum = "to_find" })

local countries = stats.group(caches, "country", { sort = "count", sum = "found_n" })
for _, c in ipairs(countries) do c.share = percent(c.sum, c.count) end
local main_country = COUNTRY or (countries[1] and countries[1].key)
local main_key = main_country and opensak.text.lower(main_country)
local in_main, open_in_main = {}, {}
for _, c in ipairs(caches) do
    if c.country and opensak.text.lower(c.country) == main_key then
        in_main[#in_main + 1] = c
        if c.to_find == 1 then open_in_main[#open_in_main + 1] = c end
    end
end
local open_states = stats.group(open_in_main, "state")

-- Regions named by *rows* ({key =, count =}), matched to the boundary data
-- like the maps match them → "n / all" and the names that match no region.
local unmatched_rows = {}
local function coverage(rows, layer, within)
    local all, unmatched = opensak.geo.regions(layer, { within = within, rows = rows })
    if not all then return tostring(#rows) end      -- no boundary data
    local n = 0
    for _, r in ipairs(all) do if r.count then n = n + 1 end end
    local count_of = {}
    for _, r in ipairs(rows) do count_of[r.key] = r.count end
    for _, k in ipairs(unmatched) do
        unmatched_rows[#unmatched_rows + 1] = { layer = layer, key = k, count = count_of[k] }
    end
    return n .. " / " .. #all
end
local countries_shown = coverage(countries, "country")
local states_shown = main_country and coverage(stats.group(in_main, "state"), "state", main_country) or "0"

local by_dir = {}
for _, g in ipairs(stats.group(open_caches, "dir")) do by_dir[g.key] = g.count end
local directions = {}
for i, d in ipairs(DIRS) do directions[i] = { key = d, count = by_dir[d] or 0 } end

-- The dot map: every n-th cache still to find, so it stays fast.
local dots = open_caches
if #dots > MAX_DOTS then
    dots = {}
    local every = math.ceil(#open_caches / MAX_DOTS)
    for i = 1, #open_caches, every do dots[#dots + 1] = open_caches[i] end
end

local regions = stats.group(caches, "region", { sort = "count", top = TOP })
local owners = stats.group(caches, "owner", { sort = "count", sum = "favorite_points", top = TOP })

local best = {}
for i, c in ipairs(stats.sort(caches, "-favorite_points", "name")) do
    if i > TOP then break end
    best[i] = c
end

-- ── Page ─────────────────────────────────────────────────────────────────
local count_of = {}
for _, s in ipairs(status) do count_of[s.key] = s.count end
local premium_count = 0
for _, c in ipairs(caches) do if c.premium then premium_count = premium_count + 1 end end

local page = html.page{
    title = "Database statistics",
    subtitle = name .. " · placed " .. (hidden.min or "?") .. " to " .. (hidden.max or "?"),
    footer = "Written by OpenSAK " .. opensak.version() .. " on " .. os.date("%Y-%m-%d %H:%M"),

    html.tiles{
        { label = "caches", value = #caches },
        { label = "found by you (" .. percent(found, #caches) .. ")", value = found },
        { label = "still to find (active)", value = to_find },
        { label = "active", value = count_of.active or 0 },
        { label = "disabled", value = count_of.disabled or 0 },
        { label = "archived", value = count_of.archived or 0 },
        { label = "premium only", value = premium_count },
        { label = "owners", value = stats.summary(caches, "owner").distinct },
        { label = "countries", value = countries_shown },
        { label = "states in " .. (main_country or "?"), value = states_shown },
        { label = "favourite points", value = favs.sum or 0 },
        { label = "most days in a row with a new cache",
          value = placing.longest and placing.longest.days or 0 },
    },

    "<h2>Caches per type</h2>",
    chart.bar(types, { horizontal = true, width = 900 }),
    html.table(types, { columns = { { "key", "Type" }, { "count", "Caches" },
                                    { "sum", "Found" }, { "share", "Found %" } },
                        bars = { "count" } }),

    html.columns(
        "<h2>Size</h2>" .. chart.bar(sizes, { width = 440 }),
        "<h2>Status</h2>" .. chart.bar(status, { width = 440 })
    ),

    "<h2>Caches placed per year</h2>",
    chart.bar(by_status, { width = 900, height = 320 }),
    "<h2>Caches placed, total</h2>",
    chart.line(placed, { y = "total", area = true, width = 900 }),

    "<h2>Difficulty / terrain: all caches</h2>",
    chart.heatmap(dt_all, { row_title = "D", col_title = "T", width = 520 }),
    "<h2>Difficulty / terrain: still to find</h2>",
    html.matrix(dt_todo, { row_title = "D", col_title = "T", totals = true }),

    "<h2>Countries</h2>",
    chart.map(countries, { width = 900 }),
    html.table(countries, { columns = { { "key", "Country" }, { "count", "Caches" },
                                        { "sum", "Found" }, { "share", "Found %" } },
                            bars = { "count" } }),

    "<h2>Still to find in " .. html.escape(main_country or "?") .. "</h2>",
    chart.map(open_states, { layer = "state", within = main_country, extent = "all", width = 900 }),

    html.columns(
        "<h2>Still to find: direction from home</h2>" .. chart.rose(directions, { width = 360 }),
        "<h2>Regions</h2>" .. chart.bar(regions, { horizontal = true, width = 440 })
    ),

    "<h2>Where the caches still to find are</h2>",
    chart.points(dots, { label = "name", width = 900, radius = 2 }),

    "<h2>Owners with the most caches</h2>",
    html.table(owners, { columns = { { "key", "Owner" }, { "count", "Caches" },
                                     { "sum", "Favourite points" } },
                         bars = { "count" } }),

    "<h2>Most favourite points</h2>",
    html.table(best, {
        columns = { { "favorite_points", "Favourites" }, { "code", "Code" }, { "name", "Name" },
                    { "type", "Type" }, { "owner", "Owner" }, { "hidden", "Placed" } },
        link = { code = "url" },
    }),

    #unmatched_rows > 0 and ("<h2>Names that match no region</h2>"
        .. "<p>Countries and states as written in the cache data that are not in the "
        .. "boundary data (e.g. another spelling or language); they are not counted "
        .. "in the tiles and maps above.</p>"
        .. html.table(unmatched_rows, { columns = { { "layer", "Kind" }, { "key", "Name" },
                                                    { "count", "Caches" } } })) or "",
}

local path = opensak.write_text(FILE, page)
print(#caches .. " caches → " .. path)
if OPEN then opensak.open(path) end
