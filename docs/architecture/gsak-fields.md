# Full GSAK Field Compatibility — Design (Roadmap #3)

*Draft, 9 October 2026. Not agreed yet — the custom-field section in particular is a proposal to settle with nagisml (macros) before any code is written. Update this file in the same commit when a sub-issue changes the design.*

## Summary

OpenSAK should be able to hold every piece of data a GSAK user has in their GSAK database, so that a migration loses nothing, saved GSAK filters and ported GSAK macros find the fields they expect, and the Filter dialog can get the Custom tab that is the largest remaining gap in the filter parity epic (#821, item 5).

Most of this is already done. The GSAK importer (#469, #472, #538) maps 41 of the 68 `Caches` columns plus waypoints, logs, attributes, corrected coordinates, notes and trackables. What is left falls into three groups:

1. **Custom fields** — GSAK's user-defined columns. The one big piece, and the one with a real architecture decision.
2. **About ten small columns**, plus three date fields that exist in OpenSAK but are stamped locally instead of taken from GSAK.
3. **Three side tables** — the Ignore list, cache images and log images.

The rest of GSAK's schema is either derived (counters, flags OpenSAK computes itself) or GSAK-internal, and is left out on purpose.

**Goals**

- Nothing a user typed or a macro computed in GSAK is lost on import.
- Data is stored now, even where the UI or macro access comes later — a field that isn't stored at import time can only be recovered by importing again.
- Ported GSAK filters and macros can refer to custom fields by name, the way they do in GSAK.

## Source of truth

The inventory below comes from a real GSAK database (`PRAGMA user_version = 27`, 12,600 caches, 1.12 million logs — the same database used for #469), dumped with a read-only script that records every table, view, trigger and index plus per-column usage. Usage numbers below are from that database; another user's database will differ, but the schema is GSAK's own and the same for everyone on that version.

## Inventory

### Caches (68 columns)

**Mapped by the importer today (41):** Code, Name, PlacedBy, Archived, Status, TempDisabled, CacheId, CacheType, Changed, Container, Country, State, County, Difficulty, Terrain, DNF, DNFDate, Found, FoundByMeDate, FTF, Latitude, Longitude, Lock, ShortHtm, LongHtm, OwnerId, OwnerName, PlacedDate, UserData, User2, User3, User4, UserFlag, UserSort, Watch, Color, Elevation, Resolution (only as the "elevation is set" signal, #794), GcNote, IsPremium, Guid, FavPoints.

**Derived — OpenSAK computes its own value, no column needed:**

| GSAK | OpenSAK | Note |
| --- | --- | --- |
| Distance, Degrees | `distance`, `bearing` | From the active centre point |
| Bearing | — | GSAK stores the compass point as text (N, NE … W). Derive it from `bearing` where needed — this is #821 item 7c |
| NumberOfLogs, LastLog, LastFoundDate | `log_count`, `last_log_date`, `last_found_date` | Recomputed from the imported logs |
| HasCorrected, HasUserNote, HasTravelBug, FoundCount | `user_notes`, `trackable_count`, `found_log_count` | FoundCount is a 0/1 "found by me" flag, not a community count |
| LatOriginal, LonOriginal | `latitude`, `longitude` | OpenSAK always keeps the posted position; the correction lives in `user_notes` |

**Exists in OpenSAK, but the GSAK value is thrown away:**

| GSAK | OpenSAK | Today | Should be |
| --- | --- | --- | --- |
| Created | `imported_at` | Time of the OpenSAK import | GSAK's date, so "first seen" survives the migration |
| LastGPXDate | `last_gpx_update` | Time of the OpenSAK import | GSAK's date, so "not refreshed by a PQ since …" filters keep working |
| UserNoteDate | `user_notes.updated_at` | Time of the OpenSAK import | GSAK's date |

**Missing:**

| GSAK | Used | Proposal |
| --- | --- | --- |
| LastUserDate | 12,600 | New column `last_user_update`. GSAK's "last changed by the user" date. Settles #821 item 6\*: keep it separate from `last_updated` (the listing-side change date) rather than merging the two |
| MacroFlag | 96 | New column `macro_flag` (boolean). GSAK macros' own working flag, separate from the user flag. Needed by ported macros |
| MacroSort | 2,240 | New column `macro_sort` (text). Same, for sorting |
| IsOwner | 28 | New column `is_owner` (boolean), set from GSAK on import and from the account name on GPX import. Removes the owner-name workaround in the filter importer |
| Symbol | 12,600 | New column `symbol` (text) from GPX `<sym>` and GSAK. Mostly "Geocache" / "Geocache Found" but user-editable in GSAK |
| Source | 12,600 | Low priority (only "GC" here). Leave out unless a user shows other values |
| SmartName, SmartOverride | 0 | GSAK's short names for GPS units. Out for now; revisit with GPS export |
| ChildLoad, LinkedTo, GetPolyFlag | 0 / 0 / 15 | GSAK-internal. Left out |

### Corrected (101 rows)

Coordinates are mapped. Not mapped:

- **kType** — how the correction was made: `gui` 61, `macro` 37, `api` 3. Proposal: `user_notes.corrected_source` (`user` / `macro` / `import`), set by OpenSAK itself as well, so a macro can find and undo its own corrections.
- **kBeforeState/County, kAfterState/County** — the region before and after correction. Probably covered by OpenSAK's own reverse geocoding (`location_basis`); verify before adding anything.

### Other tables

| Table | Rows | Status |
| --- | --- | --- |
| Waypoints, WayMemo | 3,592 | Mapped. `sB1` unused (0 rows) — left out |
| Logs, LogMemo | 1.12 M | Mapped. `lHasHtml` unused (0 rows) — left out; `lTime` is folded into `log_date` |
| Attributes | 69,865 | Mapped |
| **Ignore** | 74 | Not imported. Code + name of caches the user never wants re-imported. Tracked as #473 |
| **CacheImages** | 4,831 | Not imported. Image URL, GUID, name, description per cache |
| **LogImages** | 91,654 | Not imported. The same per log |
| **Custom**, **CustomLocal** | 12,600 / 23 | Not imported — see next section |
| Filter, Matching, Recalc, CustomCheck | 0–1 | GSAK-internal working tables. Left out |

GSAK also has four views (`CachesAll`, `LogsAll`, `WayAll`, `AttName`) and a set of triggers that keep the side tables in step with `Caches`. OpenSAK gets the same behaviour from foreign keys with cascading deletes; the views matter only because saved GSAK SQL refers to them (see the filter importer).

## Custom fields

### What GSAK does

- **`Custom`** is a real table with one row per cache (`cCode` primary key) and **one real column per user-defined field** — 32 in this database. A trigger inserts the row whenever a cache is added; others delete and rename it with the cache.
- **`CustomLocal`** holds the field definitions: name, type (`String`, `Integer`, `Real`, `Date`, `Boolean`), display order, default, control type, height, and **`fexpression`** — an optional SQL expression that makes the field a *computed* field (e.g. `nullif(GCFinds,-1)`, `CASE PMFav WHEN 0.0 THEN '' ELSE printf(...)`).
- There are 23 local definitions for 32 columns. The other 9 are GSAK's **global** custom fields, defined once for all databases outside the database file (settings). *Open — see Questions.*
- The `CachesAll` view joins `Custom` straight onto `Caches`, so GSAK users' saved filters and macros use custom fields exactly like built-in columns: `FindNum > 100`, `Parking <> ''`.
- They are in real use: Parking is filled on 12,513 caches, GCFinds/PremFinders/PMFav on all 12,600, Place on 3,133, FindNum on 2,437, Delorme on 1,808. Most are filled by popular macros, so a user can't easily recreate them.

### Proposal: one row per cache, one real column per field

Copy GSAK's shape rather than a generic name/value (EAV) table.

```
custom_fields                       -- definitions, one row per field
  name        TEXT PRIMARY KEY      -- case-insensitive, validated identifier
  type        TEXT                  -- string | integer | real | date | boolean
  position    INTEGER               -- display order
  default     TEXT
  expression  TEXT NULL             -- set → computed field, no stored column
  control     TEXT, height INTEGER  -- kept for round-trip, UI may ignore at first
  origin      TEXT                  -- 'gsak-local' | 'gsak-global' | 'opensak'

custom_values                       -- one row per cache
  cache_id    INTEGER PRIMARY KEY REFERENCES caches(id) ON DELETE CASCADE
  <field>     <type>                -- one column per stored field, added by ALTER TABLE
```

**Why not EAV:** with a name/value table every filter on a custom field becomes a sub-select per field, typed comparisons need casts, and computed fields can't refer to other custom fields as plain columns. With real columns, a ported GSAK clause like `FindNum > 100 AND Parking <> ''` runs almost unchanged against a join of `caches` and `custom_values`, and a computed field is just its expression in the SELECT list — the same as in GSAK.

**What it costs:**

- `custom_values` lives outside the SQLAlchemy models. It needs a small module of its own for DDL (add, rename, drop column), reads and writes, always with quoted, validated names.
- Dropping or renaming a column needs SQLite 3.35 / 3.25. Python 3.12's bundled SQLite is newer, but check what the PyInstaller and MSIX builds actually ship.
- Names must not collide with `caches` columns or SQL keywords; the importer renames on collision and records it in the import report.

**Computed fields.** The expression is SQL and may use GSAK functions (`g_…`). Import the definition, translate the expression with the existing GSAK filter translator, and mark it inactive (definition kept, not evaluated) when it can't be translated. Expressions are only ever evaluated inside a read-only SELECT — the same rule as `opensak.sql` and the known limitation in the macro concept §9b.

**Definitions per database.** Definitions live in the database file, like the per-database settings from #659, so a backup or a copied database carries its fields along. GSAK's global fields are imported as ordinary fields with `origin = 'gsak-global'`; an "apply to all databases" feature can come later if users ask for it.

### Where custom fields show up

| Place | What |
| --- | --- |
| GSAK import | Definitions from `CustomLocal` (+ globals once we know where they live), values from `Custom` |
| Cache list | Available as columns in the column chooser |
| Cache detail | A Custom section listing the fields with their values |
| Filter dialog | The Custom tab (#821 item 5): pick a field, operator by type (the text operators from #850 for strings, ranges for numbers and dates, yes/no for booleans) |
| Where tab / GSAK filter importer | Custom fields usable by name; `WHERE_UNMAPPABLE['custom']` goes away |
| Macro API | Read `cache.custom.<name>`; write and define fields — names and shape decided with nagisml |
| Move / copy caches | Values travel with the cache; a field missing in the target database is created, with a line in the result |
| Backup / restore | Covered automatically — the data is in the database file |
| GPX export | Out of scope for now |

## Edge cases and risks

- **Import is a one-time migration** (#472): re-importing a GSAK database overwrites custom values for the caches it contains, like the rest of the importer.
- **Two GSAK databases with a field of the same name but different types** imported into one OpenSAK database: keep the first definition, convert values where possible, report the rest.
- **Wide tables:** GSAK users with many custom fields will have a wide `custom_values` table. SQLite's default limit is 2,000 columns, far above anything seen.
- **Default values:** GSAK uses sentinels (`-1` for "not fetched", `500` for an unset degree value). Import them as they are; the expressions that hide them (`nullif(GCFinds,-1)`) come along as computed fields.

## Sub-issues and order of work

1. **Agree this design** — custom-field section with nagisml; the global-field question answered by Mike Wood.
2. **Small columns** — one migration: `last_user_update`, `macro_flag`, `macro_sort`, `is_owner`, `symbol`, `user_notes.corrected_source`; the GSAK importer fills them and stops stamping `imported_at`, `last_gpx_update` and `user_notes.updated_at` with the import time. OpenSAK sets `last_user_update` whenever the user changes a user-owned field.
3. **Custom fields: storage and GSAK import** — `custom_fields`, `custom_values`, the DDL module, import of definitions and values, import report.
4. **Custom fields: UI** — column chooser, detail panel, Custom tab in the Filter dialog (#821 item 5), Where tab and GSAK filter importer.
5. **Custom fields: macro API** — read, write, define.
6. **Ignore list** — #473.
7. **Cache and log images** — import the image metadata (URL, name, description). Lowest priority.

Steps 2 and 3 stop the data loss; everything after builds on stored data and can land in any order.

## Questions

- Where does GSAK keep the definitions of **global** custom fields, and in what format? (For Mike Wood.)
- Does GSAK export custom fields in its GPX extension at all? If so, the GPX importer should read them too.
- `kBeforeState/County` and `kAfterState/County`: is OpenSAK's `location_basis` enough, or do users rely on GSAK's before/after values?

## Out of scope for now

- GSAK-internal tables and columns listed as "left out" above.
- Writing custom fields into GPX exports.
- Sharing custom field definitions across databases.
