-- OpenSAK Macro
-- Name: Refile by country
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 2
-- Description: Move caches to a database per country or state
-- Permissions: write
--
-- Sorts the caches of the active database into one database per region
-- (issue #808): for each country / state found in the active database,
--
--   * if a database "<country>_<state>" exists, its caches go there;
--   * otherwise they go to a database "<country>", which is created
--     if it does not exist yet.
--
-- So you choose the split yourself: create e.g. "Germany_Bavaria" once,
-- and from then on Bavarian caches land there while the rest of Germany
-- goes to "Germany". Set CREATE_ALL to true to split every state instead:
-- the missing "<country>_<state>" databases are then created too, and
-- only caches without a state go to "<country>". Names are made legal by turning spaces and
-- punctuation into "_" ("Baden-Württemberg" → "Baden_Württemberg").
--
-- Caches without a country stay where they are, and so do caches whose
-- target is the active database itself. The plan is shown before anything
-- is moved; set DRY_RUN to true to only print it. When a cache already
-- exists in the target, the newer copy wins (if_exists = "newer").

local DRY_RUN = false
local CREATE_ALL = false       -- true: create a database for every state
local SEPARATOR = "_"          -- between country and state in a name

-- "Baden-Württemberg" → "Baden_Württemberg". Only ASCII spaces and
-- punctuation are replaced, so letters such as ü stay as they are.
local function db_name(text)
    local name = text:gsub("[%s%p]+", "_"):gsub("^_+", ""):gsub("_+$", "")
    return name
end

local active = opensak.database()

-- Every country / state pair with its number of caches, largest first.
local groups = opensak.sql([[
    SELECT TRIM(country) AS country, TRIM(COALESCE(state, '')) AS state,
           COUNT(*) AS n
    FROM caches
    WHERE TRIM(COALESCE(country, '')) <> ''
    GROUP BY 1, 2
    ORDER BY n DESC]])

-- Decide the target of each group; several groups may share one
-- (every state of a country without its own database).
local plan, order = {}, {}     -- target name → {codes, new, groups}
local unfiled = 0

for _, g in ipairs(groups) do
    local target = nil
    if g.state ~= "" then
        local name = db_name(g.country .. SEPARATOR .. g.state)
        if CREATE_ALL or opensak.database_exists(name) then target = name end
    end
    target = target or db_name(g.country)

    if target == "" or target == active then
        unfiled = unfiled + g.n
    else
        if not plan[target] then
            plan[target] = { codes = {}, new = not opensak.database_exists(target), groups = 0 }
            table.insert(order, target)
        end
        local p = plan[target]
        p.groups = p.groups + 1
        local rows = opensak.sql(
            "SELECT gc_code FROM caches WHERE TRIM(country) = ? AND TRIM(COALESCE(state, '')) = ?",
            { g.country, g.state })
        for _, r in ipairs(rows) do table.insert(p.codes, r.gc_code) end
    end
end

if #order == 0 then
    print(("Nothing to move in %s (%d caches stay)."):format(active, unfiled))
    return
end

-- Show the plan.
local lines, total = {}, 0
for _, target in ipairs(order) do
    local p = plan[target]
    total = total + #p.codes
    table.insert(lines, ("%6d → %s%s"):format(#p.codes, target, p.new and "  (new)" or ""))
end
print(("Plan for %s:"):format(active))
for _, line in ipairs(lines) do print(line) end
if unfiled > 0 then print(("%6d stay (no country, or already here)"):format(unfiled)) end

if DRY_RUN then
    print("DRY_RUN is on — nothing moved.")
    return
end
if not opensak.confirm(("Move %d caches from %s into %d databases?"):format(
        total, active, #order)) then
    print("Cancelled — nothing moved.")
    return
end

-- Create the missing databases, then move.
local moved = 0
for _, target in ipairs(order) do
    local p = plan[target]
    if p.new then opensak.create_database(target) end
    local n = opensak.move_caches(target, { codes = p.codes, if_exists = "newer" })
    moved = moved + n
    if n < #p.codes then
        print(("%s: %d moved, %d kept (an equal or newer copy is already there)"):format(
            target, n, #p.codes - n))
    else
        print(("%s: %d moved"):format(target, n))
    end
end

print(("Done: %d of %d caches moved."):format(moved, total))
