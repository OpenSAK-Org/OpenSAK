-- OpenSAK Macro
-- Name: SQL report
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 3
-- Description: Run an SQL query and show its result as an HTML table (GSAK's SqlToHtml)
--
-- Type a query, press Run, and the result is shown as a table — the
-- three-line GSAK macro "SQLite() into SqlToHtml", with a window to edit
-- the query and run it again. The form remembers the last query.
--
-- One report per value: a loop query (e.g. SELECT DISTINCT country FROM
-- caches) makes the query run once for each value of the loop query's
-- first column; the query reads that value as :value. Each report is also
-- written to its own HTML file in the OpenSAK temp folder, under a name no
-- earlier report has used, so no pause is needed between them; the files
-- are listed in the macro output to open them in a browser.
--
-- The query may only read the database (see opensak.sql()); the tables and
-- their columns are listed by opensak.tables() and opensak.columns().

local TITLE = "SQL report"
local MAX_ROWS = 5000        -- rows shown per report
local TEMP = "sql_report"    -- TEMP table that keeps the columns in query order

local EXAMPLE = [[
SELECT country, COUNT(*) AS caches, SUM(found) AS found
FROM caches
GROUP BY country
ORDER BY caches DESC]]

-- Run *query* and return its rows and its column names in query order.
-- opensak.sql() gives each row as a Lua table, which has no field order, so
-- the result goes through a TEMP table: opensak.columns() lists its columns
-- in the order of the query.
local function run(query, params)
  query = query:gsub("[%s;]+$", "")
  opensak.sql("DROP TABLE IF EXISTS temp." .. TEMP)
  -- The query on lines of its own, so that a "--" comment ends before ")".
  opensak.sql("CREATE TEMP TABLE " .. TEMP .. " AS SELECT * FROM (\n" .. query ..
              "\n) LIMIT " .. MAX_ROWS + 1, params)
  local columns = {}
  for _, c in ipairs(opensak.columns(TEMP)) do columns[#columns + 1] = c.name end
  local rows = opensak.sql("SELECT * FROM temp." .. TEMP .. " ORDER BY rowid")
  local more = #rows > MAX_ROWS
  if more then rows[#rows] = nil end
  return rows, columns, more
end

-- The result as an HTML table, with the number of rows above it.
local function result_html(rows, columns, more)
  local count = more and ("first %d rows"):format(MAX_ROWS)
                      or (#rows == 1 and "1 row" or #rows .. " rows")
  if #rows == 0 then return "<p>No rows.</p>" end
  return "<p>" .. count .. "</p>" .. opensak.html.table(rows, { columns = columns })
end

-- Each report under its own name: the time of the run and a number.
local stamp = os.date("%Y%m%d-%H%M%S")
local written = 0

local function write_report(title, subtitle, body)
  written = written + 1
  local file = ("%s/sql_report_%s_%d.html"):format(opensak.temp_dir(), stamp, written)
  return opensak.write_text(file, opensak.html.page{
    title = title, subtitle = subtitle, body = body,
    footer = opensak.html.escape(opensak.database() .. " · " .. os.date("%Y-%m-%d %H:%M")),
  })
end

-- Run the query (once, or once per value of the loop query) and return the
-- HTML to show; the reports are written to files on the way.
local function report(v)
  if v.loop:match("^%s*$") then
    local rows, columns, more = run(v.query)
    local body = result_html(rows, columns, more)
    print("Report: " .. write_report(v.title, opensak.database(), body))
    return body
  end

  local values, value_columns = run(v.loop)
  local key = value_columns[1]
  local p = opensak.progress(TITLE, #values)
  local parts = {}
  for i, row in ipairs(values) do
    local value = row[key]
    local label = tostring(value)
    if not p:update(i, label) then break end              -- Cancel pressed
    if value == nil then
      -- NULL: a Lua table cannot hold nil, and "= :value" would match nothing
      print("(empty): skipped")
    else
      local rows, columns, more = run(v.query, { value = value })
      local body = result_html(rows, columns, more)
      print(label .. ": " .. write_report(v.title .. " — " .. label, opensak.database(), body))
      parts[#parts + 1] = "<h2>" .. opensak.html.escape(label) .. "</h2>" .. body
    end
  end
  p:close()
  if #parts == 0 then return "<p>The loop query returned no values.</p>" end
  return table.concat(parts)
end

while true do
  local v = opensak.form{
    id = "query",
    title = TITLE,
    remember = true,
    width = 640,
    buttons = {
      { id = "run", label = "Run", default = true },
      { id = "close", label = "Close", cancel = true },
    },
    validate = function(f)
      if f:get("query"):match("^%s*$") then return "Please enter a query." end
    end,
    fields = {
      { type = "memo", id = "query", label = "Query", default = EXAMPLE,
        monospace = true, h = 160 },
      { type = "text", id = "loop", label = "One report per",
        placeholder = "optional loop query, e.g. SELECT DISTINCT country FROM caches ORDER BY 1" },
      { type = "label", italic = true,
        label = "With a loop query, the query runs once for each value of its first column, as :value." },
      { type = "text", id = "title", label = "Title", default = TITLE },
    },
  }
  if not v then break end                                   -- Close

  local ok, html = pcall(report, v)
  if not ok then
    -- e.g. a typing error in the query: show it and edit the query again
    -- (without the "m.lua:40: " in front, which points into this macro)
    opensak.message((tostring(html):gsub("^[^\n]-:%d+: ", "")), TITLE)
  else
    local _, button = opensak.form{
      title = v.title,
      width = 760,
      buttons = {
        { id = "edit", label = "Edit query", default = true },
        { id = "close", label = "Close", cancel = true },
      },
      fields = { { type = "html", html = html, h = 480 } },
    }
    if button ~= "edit" then break end
  end
end
