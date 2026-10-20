-- OpenSAK Macro
-- Name: Find statistics
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 3
-- Description: Write an HTML page with statistics, charts and maps of your finds
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
-- - Only finds in the database count: caches you found that are not in it
--   (e.g. archived ones missing from your pocket queries) are left out. For
--   complete statistics, import your "My Finds" pocket query into its own
--   database and set DATABASE.
-- - Only caches with a found date are counted. Finds on the same day are
--   ordered by the import order of their "Found it" log, so a milestone
--   (e.g. your 1000th find) can be off by a few caches on busy days.
-- - "Home" for direction and distance is the middle (median) of your finds
--   unless HOME is set below.
-- - The states shown are those of one country: COUNTRY below, or else the
--   country with the most finds. If you found more caches abroad than at
--   home, set COUNTRY.
-- - Countries and states are counted as the regions of the boundary data
--   that the names in the cache data match, so "states in …" can never be
--   more than all states. Names that match no region are listed at the end.
--
-- Reads the caches marked as found in the active database (or in DATABASE)
-- and writes find_statistics.html into OpenSAK's temp folder: key figures
-- and streaks, finds per year (by cache type) and per month, the running
-- total, weekday, direction and distance from home, maps of your countries
-- and states, difficulty × terrain and the milestones, then opens it in
-- your browser.
--
-- Everything is built from the generic opensak.stats.*, opensak.chart.*,
-- opensak.geo.* and opensak.html.* functions — copy the macro and add your
-- own statistics.

local DATABASE = nil          -- e.g. "My Finds"; nil = the active database
local HOME = nil              -- e.g. { lat = 47.3769, lon = 8.5417 }; nil = middle of your finds
local COUNTRY = nil           -- e.g. "United States": whose states are shown; nil = the country with most finds
local MILESTONE_STEP = nil    -- e.g. 500; nil = chosen from the number of finds
local MAIN_TYPES = 4          -- cache types shown on their own in "finds per year"
local FILE = opensak.temp_dir() .. "/find_statistics.html"
local OPEN = true             -- open the page in the browser when done

local stats, chart, html, date = opensak.stats, opensak.chart, opensak.html, opensak.date

-- ── The finds: one table per cache ───────────────────────────────────────
local finds = {}
for c in opensak.caches{
    found = true, database = DATABASE,
    fields = { "name", "type", "difficulty", "terrain", "found_date", "url",
               "lat", "lon", "country", "state" },
} do
    if c.found_date then finds[#finds + 1] = c end
end
if #finds == 0 then
    print("No found caches with a found date in " .. (DATABASE or opensak.database()) .. ".")
    return
end

-- Finds on the same day: in the import order of their "Found it" log, then
-- by code. That decides which cache is e.g. your 1000th find.
local log_row = {}
for _, r in ipairs(opensak.sql([[
    SELECT c.gc_code AS code, MIN(l.id) AS log_row
    FROM caches c JOIN logs l ON l.cache_id = c.id
    WHERE c.found AND l.log_type IN ('Found it', 'Attended', 'Webcam Photo Taken')
      AND date(l.log_date) = date(c.found_date)
    GROUP BY c.id]], nil, { database = DATABASE })) do
    log_row[r.code] = r.log_row
end
for _, c in ipairs(finds) do c.log_row = log_row[c.code] end
finds = stats.sort(finds, "found_date", "log_row", "code")

-- ── Fields of our own, to group and pivot by ─────────────────────────────
local home = HOME or { lat = stats.summary(finds, "lat").median, lon = stats.summary(finds, "lon").median }
local DIRS = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" }
local BANDS = { 10, 25, 50, 100, 250, 500, 1000, 2500 }     -- km

-- The most found cache types on their own, the rest as "Other".
local main, kinds = {}, {}
for i, t in ipairs(stats.group(finds, "type", { sort = "count" })) do
    if i <= MAIN_TYPES then
        main[t.key] = true
        kinds[#kinds + 1] = t.key
    elseif i == MAIN_TYPES + 1 then
        kinds[#kinds + 1] = "Other"
    end
end
local function band_of(km)
    local low = 0
    for _, b in ipairs(BANDS) do
        if km < b then return string.format("%d–%d km", low, b) end
        low = b
    end
    return "≥ " .. low .. " km"
end
for _, c in ipairs(finds) do
    c.year = date.period(c.found_date, "year")
    c.month = c.found_date:sub(6, 7)
    c.kind = main[c.type] and c.type or "Other"
    local deg = opensak.coords.bearing(home.lat, home.lon, c.lat, c.lon)
    c.dir = DIRS[math.floor((deg + 22.5) / 45) % 8 + 1]
    c.band = band_of(opensak.coords.distance(home.lat, home.lon, c.lat, c.lon))
end

-- ── Statistics ───────────────────────────────────────────────────────────
local days = stats.summary(finds, "found_date")      -- count, distinct days, min, max
local span = date.diff(days.min, days.max) + 1
local streak = stats.streaks(finds, "found_date")
local year_ago = date.format(os.time() - 365 * 86400, "%Y-%m-%d")
local last_year = 0
for _, c in ipairs(finds) do
    if c.found_date >= year_ago then last_year = last_year + 1 end
end

local years = stats.group(finds, "found_date", { period = "year", count_distinct = "found_date" })
local by_type = stats.pivot(finds, "year", "kind", { cols = kinds })
local MONTHS = { "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12" }
local per_month = stats.pivot(finds, "year", "month", { cols = MONTHS })
per_month.cols = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" }
local total = stats.running(stats.fill(stats.group(finds, "found_date", { period = "month" })))
local weekdays = stats.group(finds, "found_date", { period = "weekday" })
local types = stats.group(finds, "type", { sort = "count" })

local by_dir = {}
for _, g in ipairs(stats.group(finds, "dir")) do by_dir[g.key] = g.count end
local directions = {}
for i, d in ipairs(DIRS) do directions[i] = { key = d, count = by_dir[d] or 0 } end
local by_band = {}
for _, g in ipairs(stats.group(finds, "band")) do by_band[g.key] = g.count end
local distances = {}
for _, c in ipairs({ 0, table.unpack(BANDS) }) do
    local key = band_of(c)
    distances[#distances + 1] = { key = key, count = by_band[key] or 0 }
end

local countries = stats.group(finds, "country", { sort = "count" })
local home_country = COUNTRY or (countries[1] and countries[1].key)
local home_key = home_country and opensak.text.lower(home_country)
local states = stats.group(finds, "state", { sort = "count" })
local home_states = {}
for _, c in ipairs(finds) do
    if c.country and opensak.text.lower(c.country) == home_key then
        home_states[#home_states + 1] = c
    end
end
home_states = stats.group(home_states, "state", { sort = "count" })

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
local states_shown = home_country and coverage(home_states, "state", home_country) or "0"

local STEPS = { 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 }
local dt = stats.pivot(finds, "difficulty", "terrain", { rows = STEPS, cols = STEPS })

local step = MILESTONE_STEP or (#finds < 1000 and 100 or #finds < 5000 and 500 or 1000)
local milestones = stats.every(finds, step, { date = "found_date" })

-- ── Page ─────────────────────────────────────────────────────────────────
local name = DATABASE or opensak.database()
local page = html.page{
    title = "Find statistics",
    subtitle = name .. " · " .. days.min .. " to " .. days.max,
    footer = "Written by OpenSAK " .. opensak.version() .. " on " .. os.date("%Y-%m-%d %H:%M"),

    html.tiles{
        { label = "finds", value = days.count },
        { label = "cache days", value = days.distinct },
        { label = "finds per cache day", value = string.format("%.2f", days.count / days.distinct) },
        { label = "finds per day", value = string.format("%.2f", days.count / span) },
        { label = "in the last 365 days", value = last_year },
        { label = "longest streak (days)", value = streak.longest.days },
        { label = "longest break (days)", value = streak.gap and streak.gap.days or 0 },
        { label = "D/T combinations", value = dt.filled .. " / 81" },
        { label = "countries", value = countries_shown },
        { label = "states in " .. (home_country or "?"), value = states_shown },
    },

    "<h2>Finds per year</h2>",
    chart.bar(by_type, { width = 900, height = 320 }),
    html.table(years, { columns = { { "key", "Year" }, { "count", "Finds" }, { "distinct", "Cache days" } },
                        bars = { "count" } }),

    "<h2>Finds per month</h2>",
    html.matrix(per_month, { totals = true }),

    "<h2>Total finds</h2>",
    chart.line(total, { y = "total", area = true, width = 900 }),

    html.columns(
        "<h2>Finds per weekday</h2>" .. chart.bar(weekdays, { width = 440 }),
        "<h2>Direction from home</h2>" .. chart.rose(directions, { width = 360 })
    ),
    html.columns(
        "<h2>Distance from home</h2>" .. chart.bar(distances, { horizontal = true, width = 440 }),
        "<h2>Finds per cache type</h2>" .. chart.bar(types, { horizontal = true, width = 440 })
    ),

    "<h2>Countries</h2>",
    chart.map(countries, { width = 900 }),
    html.table(countries, { columns = { { "key", "Country" }, { "count", "Finds" } }, bars = { "count" } }),

    "<h2>States in " .. html.escape(home_country or "?") .. "</h2>",
    chart.map(home_states, { layer = "state", within = home_country, extent = "all", width = 900 }),

    "<h2>Where you found them</h2>",
    chart.points(finds, { label = "name", width = 900 }),

    "<h2>All states</h2>",
    html.table(states, { columns = { { "key", "State" }, { "count", "Finds" } }, bars = { "count" } }),

    "<h2>Difficulty / terrain</h2>",
    html.matrix(dt, { row_title = "D", col_title = "T", totals = true }),

    "<h2>Milestones</h2>",
    html.table(milestones, {
        columns = { { "n", "Find" }, { "found_date", "Date" }, { "days", "Days" },
                    { "code", "Code" }, { "name", "Name" }, { "type", "Type" } },
        link = { code = "url" },
    }),

    #unmatched_rows > 0 and ("<h2>Names that match no region</h2>"
        .. "<p>Countries and states as written in the cache data that are not in the "
        .. "boundary data (e.g. another spelling or language); they are not counted "
        .. "in the tiles and maps above.</p>"
        .. html.table(unmatched_rows, { columns = { { "layer", "Kind" }, { "key", "Name" },
                                                    { "count", "Finds" } } })) or "",
}

local path = opensak.write_text(FILE, page)
print(#finds .. " finds → " .. path)
if OPEN then opensak.open(path) end
