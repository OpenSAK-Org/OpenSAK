-- OpenSAK Macro
-- Name: DNF statistics
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 4
-- Description: Count the DNF logs of every cache into the companion database, for other macros
--
-- Stores, per cache with at least one "Didn't find it" log, how many DNFs
-- it has, how many since the last find, and the dates of the last DNF and
-- the last find, in the table dnf_stats of the active database's companion
-- database (opensak.companion()).
--
-- The companion database belongs to the database, not to this macro, so
-- other macros can read the table: see dnf_alert.lua, which filters the
-- caches with a run of DNFs. Only this macro can change it. The table has a
-- gc_code column, so OpenSAK deletes a cache's row when the cache is
-- deleted, and copies or moves it with the cache to another database.
--
-- Run it again after importing new logs to bring the numbers up to date.

-- Log types that count as a find
local FOUND_TYPES = "'Found it', 'Attended', 'Webcam Photo Taken'"

if not opensak.companion then
    print("This macro needs the companion database (the companion-db feature flag).")
    return
end

opensak.companion()     -- creates it next to the database if it has none yet

opensak.sql([[
    CREATE TABLE IF NOT EXISTS companion.dnf_stats (
        gc_code         TEXT PRIMARY KEY,
        dnfs            INTEGER NOT NULL,
        dnfs_since_find INTEGER NOT NULL,
        last_dnf        TEXT,
        last_found      TEXT,
        updated         TEXT NOT NULL
    )]])
opensak.companion_table{
    name = "dnf_stats",
    description = "DNF counts per cache, from dnf_stats.lua",
}

-- Recalculated from scratch: caches whose last DNF was deleted lose their row.
opensak.sql("DELETE FROM companion.dnf_stats")
opensak.sql([[
    INSERT INTO companion.dnf_stats
        (gc_code, dnfs, dnfs_since_find, last_dnf, last_found, updated)
    SELECT c.gc_code,
           SUM(l.log_type = 'Didn''t find it'),
           SUM(l.log_type = 'Didn''t find it' AND l.log_date > COALESCE(f.last_found, '')),
           MAX(CASE WHEN l.log_type = 'Didn''t find it' THEN l.log_date END),
           f.last_found,
           datetime('now')
    FROM caches c
    JOIN logs l ON l.cache_id = c.id
    LEFT JOIN (SELECT cache_id, MAX(log_date) AS last_found FROM logs
               WHERE log_type IN (]] .. FOUND_TYPES .. [[) GROUP BY cache_id) f
           ON f.cache_id = c.id
    GROUP BY c.id
    HAVING SUM(l.log_type = 'Didn''t find it') > 0]])

local totals = opensak.sql([[
    SELECT COUNT(*) AS caches, COALESCE(SUM(dnfs), 0) AS dnfs,
           COALESCE(SUM(dnfs_since_find > 0), 0) AS open_runs
    FROM companion.dnf_stats]])[1]
print(string.format("%d caches with %d DNF logs; %d with DNFs since their last find.",
    totals.caches, totals.dnfs, totals.open_runs))
print("Stored in companion.dnf_stats of database " .. opensak.database() .. ".")
