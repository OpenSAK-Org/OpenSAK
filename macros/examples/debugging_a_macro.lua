-- OpenSAK Macro
-- Name: Debugging a macro
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 5
-- Description: See what a macro works with: opensak.log() with tables, opensak.inspect() and opensak.log_vars()
-- Permissions: none
--
-- Three ways to look inside a macro while it runs. The macro only reads the
-- database; everything it finds goes to the macro output and the log.
--
--   1. opensak.log() writes a table out with its contents, where print()
--      shows only the table's address.
--   2. opensak.inspect() returns that text: to print it, to put it in a
--      message, to cut it short with depth, to put it on one line with
--      indent = 0, or to compare two tables.
--   3. opensak.log_vars() writes every variable in scope at its line, one
--      "name = value" line each, without a log() call per variable. With
--      { globals = true } it also lists the globals the macro created,
--      which shows a variable that was meant to be local.
--
-- Calls like these are for while a macro is being written: take them out
-- once it works.

-- ── 1. A table in the log ───────────────────────────────────────────────────

local cache = opensak.current() or opensak.caches{ all = true }()
if not cache then
    print("The database has no caches.")
    return
end

print("print() shows only an address:", cache)
opensak.log("opensak.log() shows what the table holds:", cache)

-- ── 2. opensak.inspect(): the same text, to use yourself ────────────────────

local summary = { code = cache.code, name = cache.name,
                  dt = { cache.difficulty, cache.terrain } }
print("On one line: " .. opensak.inspect(summary, { indent = 0 }))

-- Only the first level: the nested table dt shows as {...}.
print("Top level only: " .. opensak.inspect(summary, { depth = 1 }))

-- The keys are always sorted, so the same contents give the same text.
local a = { terrain = 2, difficulty = 1.5 }
local b = { difficulty = 1.5, terrain = 2 }
print("a and b hold the same: " .. tostring(opensak.inspect(a) == opensak.inspect(b)))

-- An error can be a table, not only a message.
local ok, err = pcall(error, { code = cache.code, reason = "demo error" })
if not ok then
    opensak.log("pcall caught: " .. opensak.inspect(err, { indent = 0 }))
end

-- ── 3. opensak.log_vars(): everything in scope at one line ──────────────────

local by_difficulty = {}
local counted = 0

local function count(c)
    local d = ("D%.1f"):format(c.difficulty or 0)
    by_difficulty[d] = (by_difficulty[d] or 0) + 1
    counted = counted + 1
    -- A mistake: without "local" this creates a global, which every other
    -- function of the macro can then change too. (The editor warns about
    -- it as well; this line keeps the warning out of the example.)
    ---@diagnostic disable-next-line: lowercase-global
    last_code = c.code
    if counted == 1 then
        -- Its parameter and local (c, d), the upvalues it uses
        -- (by_difficulty, counted) and, with globals = true, the global
        -- last_code that should have been a local.
        opensak.log_vars({ globals = true })
    end
end

for c in opensak.caches{ all = true, fields = { "difficulty" } } do
    count(c)
end

-- Every local of the macro so far; depth = 1 keeps the cache table short.
opensak.log_vars({ depth = 1 })

local difficulties = 0
for _ in pairs(by_difficulty) do difficulties = difficulties + 1 end
print(("Counted %d caches in %d difficulties."):format(counted, difficulties))
