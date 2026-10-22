-- OpenSAK Macro
-- Name: Owned cache statistics
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 3
-- Description: Write an HTML page with statistics of the caches you own and the finds they received
--
-- Known limitations:
-- - This is a template macro: copy it and adapt the statistics, texts and
--   layout to your needs. It is not a finished report.
-- - The page cannot be used on geocaching.com (e.g. pasted into your
--   profile): the profile editor only keeps very limited HTML and removes
--   the inline SVG charts, maps and the stylesheet.
-- - Maps use OpenSAK's boundary data (the outlines of Update location),
--   which is not yet stable: outlines, names and the country of a state may
--   still change. Without the boundary data the map is a short note.
-- - The page is in English only; dates are shown as stored (YYYY-MM-DD).
-- - Your caches are the ones OpenSAK marks as yours: their owner is the
--   geocaching.com account in Settings (username or member id). For caches
--   of another account (e.g. a team account), set OWNER.
-- - Only your caches in the database count: archived ones are left out
--   unless they are in it. For complete statistics, import all your caches
--   (e.g. a GSAK database of your hides) into their own database and set
--   DATABASE.
-- - Finds received are counted from the logs in the database. A Pocket
--   Query carries only the latest logs of each cache, so the finds per
--   year and month, the finders and the log records are only complete if
--   all logs were imported (e.g. from a GSAK database with all logs). The
--   number of finds of a cache is its find count from the import where
--   known (GSAK's FoundCount), else the number of its find logs.
-- - Finder countries (FindStatGen's "finders by country") are not known
--   to OpenSAK and are not shown.
-- - Favourite points are as of the import of each cache. Their share is
--   per find; geocaching.com counts it per premium member log, which
--   OpenSAK does not know.
-- - OpenSAK does not store when a cache was archived: the lifetime of an
--   archived cache ends at its latest log. Events are left out of the
--   lifetimes and of "found most often".
-- - Finders are told apart by their geocaching.com id where the logs have
--   it, else by name, so a finder who renamed may be counted twice.
--
-- Reads your caches in the active database (or in DATABASE) and their logs
-- and writes owned_statistics.html into OpenSAK's temp folder: key figures,
-- finds received per year and month and in total, records (northernmost,
-- most finds, most favourite points, longest lifetime, …), the finds and
-- favourites of each cache, the top finders, your caches per type, size,
-- difficulty and terrain, difficulty × terrain, the hide dates as a
-- calendar and a map, then opens it in your browser.
--
-- Built from the generic opensak.stats.*, opensak.chart.* and
-- opensak.html.* functions, like find_statistics.lua.

local DATABASE = nil     -- e.g. "My hides"; nil = the active database
local OWNER = nil        -- e.g. "MyTeamName": the owner name to report on; nil = your account in Settings
local TOP_FINDERS = 20   -- finders listed in "Top finders"
local MIN_FINDS = 10     -- finds a cache needs for "most favourites per find"
local FILE = opensak.temp_dir() .. "/owned_statistics.html"
local OPEN = true         -- open the page in the browser when done

local stats, chart, html, date, text = opensak.stats, opensak.chart, opensak.html, opensak.date, opensak.text
local name = DATABASE or opensak.database()
local today = os.date("%Y-%m-%d")

-- ── Your caches ──────────────────────────────────────────────────────────
-- OWNER: matched to the owner name in Lua, which compares non-ASCII names
-- correctly (SQLite's lower() does not). Else the caches OpenSAK marks as
-- yours from the account in Settings.
local codes = {}
if OWNER then
    local want = text.lower(text.trim(OWNER))
    for r in opensak.sql_each("SELECT gc_code AS code, owner_name AS owner FROM caches "
            .. "WHERE owner_name IS NOT NULL", nil, { database = DATABASE }) do
        if text.lower(text.trim(r.owner)) == want then codes[#codes + 1] = r.code end
    end
else
    for _, r in ipairs(opensak.sql("SELECT gc_code AS code FROM caches WHERE is_owner",
            nil, { database = DATABASE })) do
        codes[#codes + 1] = r.code
    end
end
if #codes == 0 then
    print("No caches owned by " .. (OWNER or "you") .. " in " .. name .. "."
        .. (OWNER and "" or " Set your geocaching.com username in Settings, or OWNER in the macro."))
    return
end

local function is_event(t)
    return t:find("Event") or t:find("Celebration") or t:find("Block Party")
end

local caches, by_code = {}, {}
for c in opensak.caches{
    codes = codes, database = DATABASE,
    fields = { "name", "type", "container", "difficulty", "terrain", "hidden", "available",
               "archived", "lat", "lon", "elevation", "favorite_points", "find_count",
               "url", "last_log_date" },
} do
    c.event = is_event(c.type) and true or false
    c.status = c.archived and "Archived" or (c.available and "Active" or "Disabled")
    c.fp = c.favorite_points or 0
    c.logged = 0                 -- find logs in the database
    caches[#caches + 1] = c
    by_code[c.code] = c
end
caches = stats.sort(caches, "hidden", "code")

-- ── The logs your caches received ────────────────────────────────────────
-- One query for all of them: the codes go in as one JSON list.
local FIND_TYPES = { ["Found it"] = true, ["Attended"] = true, ["Webcam Photo Taken"] = true }
local json = {}
for i, code in ipairs(codes) do json[i] = '"' .. code:gsub('[\\"]', "\\%0") .. '"' end
local finds, log_types, per_day = {}, {}, {}
local words, longest_log = { sum = 0, n = 0 }, nil
for r in opensak.sql_each([[
    SELECT c.gc_code AS code, l.log_type AS type, date(l.log_date) AS day,
           l.finder AS finder, l.finder_id AS finder_id, l.text AS text
    FROM logs l JOIN caches c ON c.id = l.cache_id
    WHERE c.gc_code IN (SELECT value FROM json_each(:codes)) AND NOT l.logged_by_owner
    ORDER BY l.log_date, l.id]], { codes = "[" .. table.concat(json, ",") .. "]" },
    { database = DATABASE }) do
    log_types[r.type] = (log_types[r.type] or 0) + 1
    local c = by_code[r.code]
    if FIND_TYPES[r.type] and c then
        c.logged = c.logged + 1
        local who = r.finder_id and text.trim(r.finder_id) ~= "" and r.finder_id
            or r.finder and text.lower(text.trim(r.finder))
        finds[#finds + 1] = { code = r.code, day = r.day, who = who, finder = r.finder }
        if r.day then
            c.last_found = r.day                      -- logs come oldest first
            local key = r.code .. " " .. r.day
            per_day[key] = (per_day[key] or 0) + 1
        end
        local n = 0
        for _ in (r.text or ""):gmatch("%S+") do n = n + 1 end
        if n > 0 then
            words.sum, words.n = words.sum + n, words.n + 1
            if not longest_log or n > longest_log.words then
                longest_log = { words = n, code = r.code }
            end
        end
    end
end

-- ── Fields of our own, per cache ─────────────────────────────────────────
local MONTHS = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" }
local total_finds, fp_total = 0, 0
for _, c in ipairs(caches) do
    c.finds = c.find_count or c.logged
    total_finds = total_finds + c.finds
    fp_total = fp_total + c.fp
    c.fp_share = c.finds > 0 and string.format("%.0f %%", 100 * c.fp / c.finds) or ""
    if c.hidden then
        c.year = date.period(c.hidden, "year")
        c.hmonth = MONTHS[tonumber(c.hidden:sub(6, 7))]
        c.hday = tonumber(c.hidden:sub(9, 10))
        -- Lifetime: until today, or for an archived cache until its last log.
        local stop = c.archived and c.last_log_date or (not c.archived and today)
        if stop and not c.event then c.lifetime = date.diff(c.hidden, stop) end
        if c.lifetime and c.finds > 0 then
            c.days_per_find = string.format("%.1f", c.lifetime / c.finds)
            c.every = c.lifetime / c.finds
        end
    end
end
for _, f in ipairs(finds) do
    if f.day then
        f.year = date.period(f.day, "year")
        f.month = f.day:sub(6, 7)
    end
end
local dated = {}
for _, f in ipairs(finds) do if f.day then dated[#dated + 1] = f end end

-- ── Statistics ───────────────────────────────────────────────────────────
local function pct(n, of) return string.format("%.1f %%", of > 0 and 100 * n / of or 0) end
local function with_share(rows)
    for _, r in ipairs(rows) do r.pct = pct(r.count, #caches) end
    return rows
end

local by_status = {}
for _, g in ipairs(stats.group(caches, "status")) do by_status[g.key] = g.count end
local events = {}
for _, c in ipairs(caches) do if c.event then events[#events + 1] = c end end

-- Finders: the different geocachers with a find log, by the number of
-- your caches they found. Shown under the name of their latest log.
local finders = {}
for _, f in ipairs(finds) do
    if f.who then
        local r = finders[f.who]
        if not r then
            r = { caches = {}, n = 0 }
            finders[f.who] = r
        end
        if not r.caches[f.code] then r.caches[f.code], r.n = true, r.n + 1 end
        r.finder = f.finder or f.who
    end
end
local top = {}
for _, r in pairs(finders) do top[#top + 1] = { finder = r.finder, count = r.n } end
local n_finders = #top
top = stats.sort(top, "-count", "finder")
for i = #top, TOP_FINDERS + 1, -1 do top[i] = nil end
for i, r in ipairs(top) do
    r.rank = i
    r.pct = pct(r.count, #caches)
end

-- Attendees of your events: the different finders of their "Attended" logs.
local attendees = {}
for _, f in ipairs(finds) do
    if f.who and by_code[f.code].event then attendees[f.who] = true end
end
local n_attendees = 0
for _ in pairs(attendees) do n_attendees = n_attendees + 1 end

-- FP h-index: h of your caches have at least h favourite points.
local h_index = 0
for i, c in ipairs(stats.sort(caches, "-fp")) do
    if c.fp >= i then h_index = i end
end

local years = stats.group(dated, "day", { period = "year", count_distinct = "day" })
for _, y in ipairs(years) do y.per_day = string.format("%.2f", y.count / y.distinct) end
local MM = { "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12" }
local per_month = stats.pivot(dated, "year", "month", { cols = MM })
per_month.cols = MONTHS
local total = #dated > 0 and stats.running(stats.fill(stats.group(dated, "day", { period = "month" }))) or {}
local weekdays = stats.group(dated, "day", { period = "weekday" })
local first_hidden = caches[1].hidden
local span = first_hidden and date.diff(first_hidden, today) + 1 or 0

local placed = stats.pivot(caches, "year", "status", { cols = { "Active", "Disabled", "Archived" } })
local types = with_share(stats.group(caches, "type", { sort = "count" }))
local sizes = with_share(stats.group(caches, "container", { sort = "count" }))
local STEPS = { 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 }
local function per_step(field)
    local n = {}
    for _, g in ipairs(stats.group(caches, field)) do n[g.key] = g.count end
    local rows = {}
    for i, s in ipairs(STEPS) do rows[i] = { key = string.format("%.1f", s), count = n[s] or 0 } end
    return with_share(rows)
end
local dt = stats.pivot(caches, "difficulty", "terrain", { rows = STEPS, cols = STEPS })
local hard = 0
for _, c in ipairs(caches) do
    if (c.difficulty or 0) >= 3 or (c.terrain or 0) >= 3 then hard = hard + 1 end
end
local DAYS = {}
for d = 1, 31 do DAYS[d] = d end
local calendar = stats.pivot(caches, "hmonth", "hday", { rows = MONTHS, cols = DAYS })

local received = {}
for t, n in pairs(log_types) do received[#received + 1] = { key = t, count = n } end
received = stats.sort(received, "-count", "key")

-- Most favourite points per cache type and per size.
local function most_favourited(field)
    local best = {}
    for _, c in ipairs(caches) do
        local k = c[field] or "?"
        if not best[k] or c.fp > best[k].fp then best[k] = c end
    end
    local rows = {}
    for k, c in pairs(best) do
        rows[#rows + 1] = { key = k, code = c.code, name = c.name, url = c.url,
                            fp = c.fp, fp_share = c.fp_share }
    end
    return stats.sort(rows, "-fp", "key")
end

-- ── Records ──────────────────────────────────────────────────────────────
-- Each record: the cache with the largest (or smallest) value of a field.
local records = {}
local function record(label, value, c)
    records[#records + 1] = { record = label, value = value,
                              code = c and c.code, name = c and c.name, url = c and c.url }
end
local function extreme(field, largest, keep)
    local best
    for _, c in ipairs(caches) do
        local v = c[field]
        if v ~= nil and (not keep or keep(c))
            and (not best or (largest and v > best[field]) or (not largest and v < best[field])) then
            best = c
        end
    end
    return best
end
local function coords(c) return opensak.coords.format(c.lat, c.lon) end
local c
c = extreme("lat", true);  record("Northernmost", coords(c), c)
c = extreme("lat", false); record("Southernmost", coords(c), c)
c = extreme("lon", true);  record("Easternmost", coords(c), c)
c = extreme("lon", false); record("Westernmost", coords(c), c)
c = extreme("elevation", true)
if c then
    record("Highest", string.format("%.0f m", c.elevation), c)
    c = extreme("elevation", false)
    record("Lowest", string.format("%.0f m", c.elevation), c)
end
c = extreme("hidden", false); if c then record("Oldest", c.hidden, c) end
c = extreme("hidden", true);  if c then record("Newest", c.hidden, c) end
c = extreme("finds", true);   record("Most finds", c.finds, c)
local day_best
for key, n in pairs(per_day) do
    if not day_best or n > day_best.n or (n == day_best.n and key < day_best.key) then
        day_best = { key = key, n = n }
    end
end
if day_best then
    local code, day = day_best.key:match("^(%S+) (.+)$")
    record("Most finds on one day", day_best.n .. " on " .. day, by_code[code])
end
local busiest = stats.sort(stats.group(dated, "day"), "-count", "key")[1]
if busiest then record("Day with the most finds", busiest.count .. " on " .. busiest.key) end
c = extreme("fp", true); record("Most favourite points", c.fp, c)
c = extreme("fp", true, function(x) return x.finds >= MIN_FINDS end)
if c then record("Most favourites per find (" .. MIN_FINDS .. "+ finds)", c.fp_share, c) end
c = extreme("every", false)
if c then record("Found most often", "every " .. c.days_per_find .. " days", c) end
c = extreme("last_found", false, function(x) return x.status == "Active" and not x.event end)
if c then
    record("Active cache found the longest ago", c.last_found .. " ("
        .. date.diff(c.last_found, today) .. " days)", c)
end
c = extreme("finds", true, function(x) return x.event end)
if c then record("Event with the most attendees", c.finds, c) end
local function lifetimes(keep)
    local sum, n = 0, 0
    for _, x in ipairs(caches) do
        if x.lifetime and keep(x) then sum, n = sum + x.lifetime, n + 1 end
    end
    return n > 0 and string.format("%.0f days on average", sum / n)
end
local avg = lifetimes(function() return true end)
if avg then
    c = extreme("lifetime", true)
    record("Lifetime", avg .. "; longest " .. c.lifetime .. " days", c)
end
avg = lifetimes(function(x) return x.archived end)
if avg then
    c = extreme("lifetime", true, function(x) return x.archived end)
    record("Lifetime of archived caches", avg .. "; longest " .. c.lifetime .. " days", c)
end
if words.n > 0 then
    record("Find log length", string.format("%.0f words on average", words.sum / words.n))
    record("Longest find log", longest_log.words .. " words", by_code[longest_log.code])
end

-- ── Page ─────────────────────────────────────────────────────────────────
local cache_columns = {
    { "hidden", "Hidden" }, { "last_found", "Last found" }, { "finds", "Finds" },
    { "days_per_find", "Days / find" }, { "fp", "FP" }, { "fp_share", "FP / finds" },
    { "code", "Code" }, { "name", "Name" }, { "type", "Type" }, { "status", "Status" },
}
local function share_table(rows, heading)
    return html.table(rows, { columns = { { "key", heading }, { "count", "Caches" }, { "pct", "Share" } },
                              bars = { "count" } })
end
local favourite_columns = function(heading)
    return { { "key", heading }, { "code", "Code" }, { "name", "Name" }, { "fp", "FP" },
             { "fp_share", "FP / finds" } }
end

local page = html.page{
    title = "Owned cache statistics",
    subtitle = (OWNER or "Your caches") .. " · " .. name
        .. (first_hidden and (" · hidden since " .. first_hidden) or ""),
    footer = "Written by OpenSAK " .. opensak.version() .. " on " .. os.date("%Y-%m-%d %H:%M"),

    html.tiles{
        { label = "caches", value = #caches },
        { label = "active", value = by_status.Active or 0 },
        { label = "disabled", value = by_status.Disabled or 0 },
        { label = "archived", value = by_status.Archived or 0 },
        { label = "events hosted", value = #events },
        { label = "attendees of your events", value = n_attendees },
        { label = "finds received", value = total_finds },
        { label = "finds per day", value = string.format("%.2f", span > 0 and total_finds / span or 0) },
        { label = "finds per cache", value = string.format("%.1f", total_finds / #caches) },
        { label = "different finders", value = n_finders },
        { label = "favourite points", value = fp_total },
        { label = "FP h-index", value = h_index },
        { label = "D/T combinations", value = dt.filled .. " / 81" },
        { label = "D or T 3 or more", value = hard .. " (" .. pct(hard, #caches) .. ")" },
    },

    "<h2>Finds received per year</h2>",
    html.table(years, { columns = { { "key", "Year" }, { "count", "Finds" }, { "distinct", "Cache days" },
                                    { "per_day", "Finds per cache day" } },
                        bars = { "count" } }),

    "<h2>Finds received per month</h2>",
    html.matrix(per_month, { totals = true }),

    "<h2>Total finds received</h2>",
    chart.line(total, { y = "total", area = true, width = 900 }),

    html.columns(
        "<h2>Caches placed per year</h2>" .. chart.bar(placed, { width = 440 }),
        "<h2>Finds received per weekday</h2>" .. chart.bar(weekdays, { width = 440 })
    ),

    "<h2>Records</h2>",
    html.table(records, { columns = { { "record", "Record" }, { "value", "Value" },
                                      { "code", "Code" }, { "name", "Name" } },
                          link = { code = "url" } }),

    "<h2>Finds and favourites per cache</h2>",
    html.table(caches, { columns = cache_columns, link = { code = "url" }, bars = { "finds" } }),

    html.columns(
        "<h2>Most favourite points per type</h2>"
            .. html.table(most_favourited("type"), { columns = favourite_columns("Type"),
                                                     link = { code = "url" } }),
        "<h2>Most favourite points per size</h2>"
            .. html.table(most_favourited("container"), { columns = favourite_columns("Size"),
                                                          link = { code = "url" } })
    ),

    "<h2>Top finders</h2>",
    "<p>The geocachers who found the most of your " .. #caches .. " caches.</p>",
    html.table(top, { columns = { { "rank", "#" }, { "finder", "Finder" }, { "count", "Caches" },
                                  { "pct", "Share" } },
                      bars = { "count" } }),
    n_finders > #top and ("<p>" .. (n_finders - #top) .. " more geocachers found your caches.</p>") or "",

    html.columns(
        "<h2>Caches per type</h2>" .. share_table(types, "Type"),
        "<h2>Caches per size</h2>" .. share_table(sizes, "Size")
    ),
    html.columns(
        "<h2>Caches per difficulty</h2>" .. share_table(per_step("difficulty"), "Difficulty"),
        "<h2>Caches per terrain</h2>" .. share_table(per_step("terrain"), "Terrain")
    ),

    "<h2>Difficulty / terrain</h2>",
    html.matrix(dt, { row_title = "D", col_title = "T", totals = true }),

    "<h2>Hide dates</h2>",
    "<p>" .. calendar.filled .. " of 366 days of the year have a cache hidden on them.</p>",
    html.matrix(calendar, { totals = true }),

    "<h2>Logs received per type</h2>",
    html.table(received, { columns = { { "key", "Log type" }, { "count", "Logs" } }, bars = { "count" } }),

    "<h2>Where your caches are</h2>",
    -- States, not countries: caches in one small area would otherwise show
    -- no border at all.
    chart.points(caches, { label = "name", layer = "state", width = 900 }),
}

local path = opensak.write_text(FILE, page)
print(#caches .. " caches, " .. total_finds .. " finds → " .. path)
if OPEN then opensak.open(path) end
