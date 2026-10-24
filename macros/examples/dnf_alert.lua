-- OpenSAK Macro
-- Name: DNF alert
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 4
-- Description: Filter the unfound caches with several DNFs since their last find, from dnf_stats.lua's data
--
-- Builds on another macro's data: dnf_stats.lua stores DNF counts per cache
-- in the companion database (companion.dnf_stats). This macro only reads
-- that table, joins it with the caches, lists the caches with at least
-- MIN_DNFS DNF logs since their last find (maybe missing, maybe just hard)
-- and filters them in the cache list.
--
-- It can read every table in the companion database, but not change the
-- ones other macros made: an UPDATE of companion.dnf_stats here would fail
-- with "companion.dnf_stats belongs to dnf_stats.lua".

local MIN_DNFS = 2

if not opensak.companion then
    print("This macro needs the companion database (the companion-db feature flag).")
    return
end

-- Is the table there, and who made it?
local stats
for _, t in ipairs(opensak.companion_tables()) do
    if t.name == "dnf_stats" then stats = t end
end
if not stats then
    print("No DNF statistics in database " .. opensak.database() .. " yet: run dnf_stats.lua first.")
    return
end
print(string.format("Using companion.dnf_stats (%d caches) by %s.", stats.rows or 0,
    stats.owner_name or "an unknown macro"))

local rows = opensak.sql([[
    SELECT c.gc_code, c.name, d.dnfs_since_find, d.last_dnf, d.last_found
    FROM companion.dnf_stats d
    JOIN caches c USING (gc_code)
    WHERE c.found = 0 AND d.dnfs_since_find >= ?
    ORDER BY d.dnfs_since_find DESC, d.last_dnf DESC]], { MIN_DNFS })

if #rows == 0 then
    print(string.format("No unfound cache has %d or more DNFs since its last find.", MIN_DNFS))
    return
end

local codes = {}
for _, r in ipairs(rows) do
    codes[#codes + 1] = r.gc_code
    print(string.format("%-9s %2d DNFs, last %s, last found %s  %s", r.gc_code,
        r.dnfs_since_find, (r.last_dnf or "?"):sub(1, 10),
        r.last_found and r.last_found:sub(1, 10) or "never", r.name))
end
opensak.filter{ codes = codes }
print(#codes == 1 and "1 cache filtered." or (#codes .. " caches filtered."))
