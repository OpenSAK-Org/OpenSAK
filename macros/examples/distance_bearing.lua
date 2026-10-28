-- OpenSAK Macro
-- Name: Distance and bearing
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 5
-- Description: List the caches around any point with distance and compass direction (GSAK's g_Distance, g_Bearing1/2 and g_Project in SQL)
-- Permissions: none
--
-- Enter a point (any format opensak.coords.parse() reads, e.g.
-- "N47 22.200 E008 32.400"), a radius and how to measure, and the macro
-- lists the caches within the radius: distance, bearing in degrees and as
-- a compass direction, and how much the great circle and the Vincenty
-- ellipsoid differ for each — the difference that decides edge cases of
-- distance challenges. The centre point of OpenSAK is not changed.
--
-- Everything is measured in SQL with the functions GSAK users know from
-- their where clauses, so the query works unchanged in the Where tab of
-- the filter dialog (with numbers in place of :lat, :lon, …):
--   g_Distance(lat1, lon1, lat2, lon2, 'K')   km; 'M' miles; 'KH' / 'MH' Vincenty
--   g_Bearing2(lat1, lon1, lat2, lon2, 'G')   degrees; 'H' Vincenty
--   g_Bearing1(lat1, lon1, lat2, lon2, 'G')   N, NE, E, … as a compass direction
--   g_Project(lat, lon, degrees, distance, 'K', 'G')   'lat;lon'; 'V' Vincenty
-- The edge of the circle in the eight compass directions is projected with
-- g_Project() and measured back with opensak.coords.distance(), which
-- takes the same method ("haversine" or "vincenty").
--
-- "Show in the cache list" filters the list to the caches found.

local TITLE = "Distance and bearing"
local METHODS = { "Great circle", "Vincenty (WGS-84 ellipsoid)" }
local DIRS = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" }

-- :units is 'K' or 'KH', :method 'G' or 'H'. The inner query measures, the
-- outer one keeps what lies within the radius; diff_m is Vincenty minus
-- great circle in metres.
local QUERY = [[
SELECT gc_code, name, cache_type, km, deg, dir, (km_h - km_g) * 1000 AS diff_m
FROM (
  SELECT gc_code, name, cache_type,
         g_Distance(:lat, :lon, latitude, longitude, :units) AS km,
         g_Distance(:lat, :lon, latitude, longitude, 'K')    AS km_g,
         g_Distance(:lat, :lon, latitude, longitude, 'KH')   AS km_h,
         g_Bearing2(:lat, :lon, latitude, longitude, :method) AS deg,
         g_Bearing1(:lat, :lon, latitude, longitude, :method) AS dir
  FROM caches
)
WHERE km <= :radius
ORDER BY km
LIMIT :max]]

local html = opensak.html

local function fmt(value, decimals)
  if value == nil then return "" end            -- the bearing of the point itself
  return ("%." .. decimals .. "f"):format(value)
end

-- The caches within the radius, as rows for opensak.html.table().
local function caches_near(lat, lon, v, vincenty)
  local rows = opensak.sql(QUERY, {
    lat = lat, lon = lon, radius = v.radius, max = v.max,
    units = vincenty and "KH" or "K", method = vincenty and "H" or "G",
  })
  for _, r in ipairs(rows) do
    r.km, r.deg, r.diff_m = fmt(r.km, 3), fmt(r.deg, 1), fmt(r.diff_m, 1)
    r.dir = r.dir or ""
  end
  return rows
end

-- How many of *rows* lie in each compass direction.
local function per_direction(rows)
  local counts = {}
  for _, g in ipairs(opensak.stats.group(rows, "dir")) do counts[g.key] = g.count end
  local out = {}
  for _, d in ipairs(DIRS) do out[#out + 1] = { dir = d, caches = counts[d] or 0 } end
  return out
end

-- The edge of the circle in the eight compass directions: projected in SQL
-- with g_Project(), then measured back with opensak.coords.distance().
local function circle_edge(lat, lon, v, vincenty)
  local method = vincenty and "vincenty" or "haversine"
  local out = {}
  for i, d in ipairs(DIRS) do
    local deg = (i - 1) * 45
    local p = opensak.sql("SELECT g_Project(:lat, :lon, :deg, :km, 'K', :calc) AS p", {
      lat = lat, lon = lon, deg = deg, km = v.radius, calc = vincenty and "V" or "G",
    })[1].p
    local plat = assert(tonumber(p:match("^(.-);")))
    local plon = assert(tonumber(p:match(";(.*)$")))
    out[#out + 1] = {
      dir = d,
      deg = deg,
      point = opensak.coords.format(plat, plon),
      -- to the metre: g_Project() rounds to 6 decimals (about 0.1 m), as GSAK
      back = fmt(opensak.coords.distance(lat, lon, plat, plon, method), 3),
    }
  end
  return out
end

local function report(lat, lon, v)
  local vincenty = v.method == METHODS[2]
  local rows = caches_near(lat, lon, v, vincenty)
  local parts = {
    ("<p>Around <b>%s</b>, within %s km, measured along the %s.</p>"):format(
      html.escape(opensak.coords.format(lat, lon)), fmt(v.radius, 3),
      vincenty and "WGS-84 ellipsoid (Vincenty)" or "great circle"),
  }
  if #rows == 0 then
    parts[#parts + 1] = "<p>No caches within the radius.</p>"
  else
    parts[#parts + 1] = ("<h3>%d cache%s</h3>"):format(#rows, #rows == 1 and "" or "s")
    parts[#parts + 1] = html.table(rows, { columns = {
      { "gc_code", "Code" }, { "name", "Name" }, { "cache_type", "Type" },
      { "km", "km" }, { "deg", "Bearing °" }, { "dir", "Direction" },
      { "diff_m", "Vincenty − great circle (m)" },
    } })
    parts[#parts + 1] = "<h3>Per direction</h3>"
    parts[#parts + 1] = html.table(per_direction(rows), {
      columns = { { "dir", "Direction" }, { "caches", "Caches" } }, bars = { "caches" } })
  end
  parts[#parts + 1] = "<h3>Edge of the circle</h3>"
  parts[#parts + 1] = html.table(circle_edge(lat, lon, v, vincenty), { columns = {
    { "dir", "Direction" }, { "deg", "Bearing °" }, { "point", "Point (g_Project)" },
    { "back", "Measured back (km)" },
  } })
  return table.concat(parts), rows
end

local center = opensak.center()

while true do
  local v = opensak.form{
    id = "around",
    title = TITLE,
    remember = true,
    width = 460,
    buttons = {
      { id = "run", label = "Show", default = true },
      { id = "close", label = "Close", cancel = true },
    },
    validate = function(f)
      if not opensak.coords.parse(f:get("point")) then
        return "Please enter coordinates, e.g. N47 22.200 E008 32.400."
      end
    end,
    fields = {
      { type = "text", id = "point", label = "Point",
        default = opensak.coords.format(center.lat, center.lon),
        placeholder = "e.g. N47 22.200 E008 32.400" },
      { type = "number", id = "radius", label = "Radius", default = 10,
        min = 0.01, max = 20000, decimals = 2, suffix = " km" },
      { type = "radio", id = "method", label = "Measure along the", options = METHODS,
        default = METHODS[1] },
      { type = "number", id = "max", label = "At most", default = 200, min = 1,
        max = 5000, suffix = " caches" },
      { type = "checkbox", id = "show", label = "Show in the cache list" },
    },
  }
  if not v then break end                                   -- Close

  local lat, lon = opensak.coords.parse(v.point)
  local body, rows = report(lat, lon, v)
  if v.show and #rows > 0 then
    local codes = {}
    for _, r in ipairs(rows) do codes[#codes + 1] = r.gc_code end
    opensak.filter{ codes = codes }
  end

  local _, button = opensak.form{
    title = TITLE,
    width = 760,
    buttons = {
      { id = "again", label = "Another point", default = true },
      { id = "close", label = "Close", cancel = true },
    },
    fields = { { type = "html", html = body, h = 480 } },
  }
  if button ~= "again" then break end
end
