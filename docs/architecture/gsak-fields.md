# Full GSAK Field Compatibility — Design (Epic #1028)

*Draft, 9 October 2026; revised 15 October 2026 after nagisml's review on #1028. The custom-field storage model (runtime-built model, local and global fields, field ids) is now agreed in principle; the macro API shape and computed fields are still open — see Questions. Update this file in the same commit when a sub-issue changes the design.*

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
- There are 23 local definitions for 32 columns. The other 9 are GSAK's **global** custom fields, defined once for all databases. Their definitions live in **`gsak.db3`**, the GSAK settings database at the root of the GSAK data folder (found by nagisml). The importer already locates this file for saved filters (`GSAK_SETTINGS_DB_NAME`, `GsakBackupContents.settings_member` / `settings_path` in `importer/gsak_importer.py`), and GSAK backup zips include it at the zip root:

  ```sql
  -- gsak.db3: same columns as the per-database CustomLocal table
  CREATE TABLE CustomGlobal (fname text primary key, ftype text, fsequence integer,
                             fdefault text, fcontrol text, fheight integer, fexpression text);
  -- gsak.db3: which field belongs to which database
  CREATE TABLE CustomDefs (fname, ftype, fscope, fdb);
  --   ('Angel',  'Boolean', 'Global', '')        ← global field
  --   ('MyTour', 'Boolean', 'Local',  'AllXY')   ← local field of database "AllXY"
  ```

  The *values* of global fields are stored in each database's own `Custom` table, next to the local ones.
- The `CachesAll` view joins `Custom` straight onto `Caches`, so GSAK users' saved filters and macros use custom fields exactly like built-in columns: `FindNum > 100`, `Parking <> ''`.
- They are in real use: Parking is filled on 12,513 caches, GCFinds/PremFinders/PMFav on all 12,600, Place on 3,133, FindNum on 2,437, Delorme on 1,808. Most are filled by popular macros, so a user can't easily recreate them.

### Proposal: one row per cache, one real column per field

Copy GSAK's shape rather than a generic name/value (EAV) table. Agreed with nagisml on #1028: real columns make every other feature (filters, the Where tab, macros, the cache list) simpler and need no special handling. `opensak.sql`, `opensak.columns` and the filter engine need changes, but no showstoppers were found.

**Why not EAV:** with a name/value table every filter on a custom field becomes a sub-select per field, typed comparisons need casts, and computed fields can't refer to other custom fields as plain columns. With real columns, a ported GSAK clause like `FindNum > 100 AND Parking <> ''` runs almost unchanged against a join of `caches` and `custom_values`, and a computed field is just its expression in the SELECT list — the same as in GSAK.

#### Storage

```
custom_fields                       -- definitions, one row per field (normal ORM model in models.py)
  field_id    TEXT PRIMARY KEY      -- stable UUID; schema changes match on this, never on name
  name        TEXT UNIQUE           -- case-insensitive, validated identifier
  type        TEXT                  -- string | integer | real | date | boolean
  position    INTEGER               -- display order
  default     TEXT
  expression  TEXT NULL             -- set → computed field, no stored column
  control     TEXT, height INTEGER  -- kept for round-trip, UI may ignore at first
  scope       TEXT                  -- 'local' | 'global'
  origin      TEXT                  -- 'gsak' | 'opensak' (where the definition came from)
  orphaned    BOOLEAN               -- column kept after its field was deleted, see below

custom_values                       -- one row per cache, built at runtime
  cache_id    INTEGER PRIMARY KEY REFERENCES caches(id) ON DELETE CASCADE
  <field>     <type> NOT NULL DEFAULT <default>   -- one real column per stored field
```

- A trigger creates the `custom_values` row for every new cache, and the columns are `NOT NULL DEFAULT …`. That way a GSAK clause like `Angel = 0` also matches caches nobody has touched — the same behaviour as GSAK's `Custom` trigger.
- **Local fields** are stored only in the database file.
- **Global fields** have their master definition in the app settings (next to the other app-wide settings handled by `settings_store`). Each database also keeps a copy of the definition (`scope = 'global'`), so a backup or a copied database stays self-contained and can be opened on another machine without the global master.

#### Model built at runtime

Custom fields are defined by the user, not at build time, so `custom_values` cannot be a normal declarative model.

- `custom_values` is mapped at runtime from the global and local definitions with `registry.map_imperatively`, using its **own `MetaData`** so `create_all` never touches it.
- One model per open database, cached by database path and schema version, and **rebuilt after every definition change**.
- It works in the same session and transaction as `Cache`, so the edit dialog, `transfer.py` and macros use normal ORM objects. Bulk paths (GSAK import, the cache list's column load) use SQLAlchemy Core.
- Prototyped by nagisml on SQLAlchemy 2.1 (inside the `sqlalchemy>=2.0,<2.2` range in `pyproject.toml`). The foreign key must point at `Cache.__table__.c.id` — a string reference doesn't resolve across `MetaData` objects.

#### Applying definitions to a database

Runs when a database is opened and after each definition edit. Fields are matched by `field_id`, not by name.

| Situation | Action |
| --- | --- |
| Field defined, no column yet (e.g. a global field created while this database was closed) | `ADD COLUMN`, automatically |
| Same id, new name | `RENAME COLUMN`, automatically and without data loss |
| Column without a definition (field deleted) | Keep the column and its data, mark it orphaned, offer cleanup. **Never drop automatically** |
| Type or default changed | Rebuild the table, only after the user confirms |

**Why not `create_all` or Alembic's comparison** (both tested by nagisml):

- `create_all` never alters an existing table — a new column was not added.
- Alembic's `compare_metadata` reports a rename as drop + add, which loses all values.
- A ~50-line comparison using `PRAGMA table_info` plus the field ids handles the safe cases and leaves destructive changes as explicit user actions.

This is separate from OpenSAK's numbered migrations in `db/database.py` (`PRAGMA user_version`): those change the fixed schema, this only ever touches `custom_values`.

#### What it costs

- Opening a database can now change its schema, but only by adding or renaming columns in `custom_values`. Anything destructive needs the user's confirmation.
- A global field couples databases together. The per-database copy of the definition limits that: a database never depends on the global master to open or to be read.
- Dropping or renaming a column needs SQLite 3.35 / 3.25. Python 3.12's bundled SQLite is newer, but check what the PyInstaller and MSIX builds actually ship.
- Names must not collide with `caches` columns or SQL keywords; the importer renames on collision and records it in the import report. All DDL uses quoted, validated names.

#### Computed fields

The expression is SQL and may use GSAK functions (`g_…`). Import the definition, translate the expression with the existing GSAK filter translator, and mark it inactive (definition kept, not evaluated) when it can't be translated. Expressions are only ever evaluated inside a read-only SELECT — the same rule as `opensak.sql` and the known limitation in the macro concept §9b. Computed fields have no column in `custom_values` and are read-only for macros.

Examples from the sample database:

```sql
nullif(GCFinds,-1)                                   -- hide GSAK's "not fetched" sentinel
CASE PMFav WHEN 0.0 THEN '' ELSE printf(...) END     -- formatted display value from another field
```

#### GSAK import

- Local definitions come from `CustomLocal` in the GSAK database. Global definitions come from `CustomGlobal` in `gsak.db3`; `CustomDefs` maps each field to `Global` or to a local database.
- Values come from `Custom`, which holds the columns for both local and global fields.
- Imported global fields become OpenSAK global fields (`scope = 'global'`, `origin = 'gsak'`). If `gsak.db3` isn't available (a single database imported without its settings file), they are imported as local fields and the import report says so.
- Names that collide with `caches` columns or SQL keywords are renamed and reported.

### Where custom fields show up

| Place | What |
| --- | --- |
| GSAK import | Local definitions from `CustomLocal`, global definitions from `CustomGlobal` / `CustomDefs` in `gsak.db3`, values from `Custom` |
| Cache list | Available as columns in the column chooser |
| Cache detail | A Custom section listing the fields with their values |
| Filter dialog | The Custom tab (#821 item 5): pick a field, operator by type (the text operators from #850 for strings, ranges for numbers and dates, yes/no for booleans) |
| Where tab / GSAK filter importer | Custom fields usable by name; `WHERE_UNMAPPABLE['custom']` goes away |
| Macro API | Read, write and define fields, following the general field-write support nagisml is adding to the macro API. Exact shape (`cache.custom.<name>`, `opensak.set_custom(...)` or other) still open — nagisml will prototype it. `macro_flag` and `macro_sort` are exposed to macros from the start |
| Move / copy caches | Values travel with the cache; a local field missing in the target database is created there, with a line in the result. Global fields already exist in every database |
| Backup / restore | Values and a copy of every definition (local and global) are in the database file. The global master in the app settings belongs with the app-settings part of backup (#942) |
| GPX export | Out of scope for now |

## Edge cases and risks

- **Import is a one-time migration** (#472): re-importing a GSAK database overwrites custom values for the caches it contains, like the rest of the importer.
- **Two GSAK databases with a field of the same name but different types** imported into one OpenSAK database: keep the first definition, convert values where possible, report the rest.
- **Wide tables:** GSAK users with many custom fields will have a wide `custom_values` table. SQLite's default limit is 2,000 columns, far above anything seen.
- **Schema change on open:** applying definitions can add or rename columns when a database is opened. Only non-destructive changes run automatically; consider an automatic backup before a confirmed table rebuild once backup support (#942) lands.
- **Global field renamed or deleted while a database is closed:** handled on next open by `field_id` — rename follows, delete leaves the column orphaned with its data.
- **Default values:** GSAK uses sentinels (`-1` for "not fetched", `500` for an unset degree value). Import them as they are; the expressions that hide them (`nullif(GCFinds,-1)`) come along as computed fields.

## Sub-issues and order of work

1. **Agree this design** — storage model agreed with nagisml on #1028 (15 Oct 2026); global-field location answered by nagisml (`gsak.db3`). Still open: macro API shape and computed fields (see Questions).
2. **Small columns** — one migration: `last_user_update`, `macro_flag`, `macro_sort`, `is_owner`, `symbol`, `user_notes.corrected_source`; the GSAK importer fills them and stops stamping `imported_at`, `last_gpx_update` and `user_notes.updated_at` with the import time. OpenSAK sets `last_user_update` whenever the user changes a user-owned field.
3. **Custom fields: storage and GSAK import** — `custom_fields`, the runtime-built `custom_values` model, the apply-definitions step, global master in the app settings, import of local and global definitions and values, import report.
4. **Custom fields: UI** — column chooser, detail panel, Custom tab in the Filter dialog (#821 item 5), Where tab and GSAK filter importer.
5. **Custom fields: macro API** — read, write, define.
6. **Ignore list** — #473.
7. **Cache and log images** — import the image metadata (URL, name, description). Lowest priority.

Steps 2 and 3 stop the data loss; everything after builds on stored data and can land in any order.

## Questions

- **Macro API shape** for custom fields — `cache.custom.<name>`, `opensak.set_custom(...)` or something else. nagisml will prototype it once general field writes are in.
- **Computed fields** — confirm with nagisml from real examples that importing the expression, translating it and keeping it inactive when it can't be translated is fine from the macro side.
- Does GSAK export custom fields in its GPX extension at all? If so, the GPX importer should read them too.
- `kBeforeState/County` and `kAfterState/County`: is OpenSAK's `location_basis` enough, or do users rely on GSAK's before/after values?

## Out of scope for now

- GSAK-internal tables and columns listed as "left out" above.
- Writing custom fields into GPX exports.
- Syncing global definitions beyond what the apply-definitions step does (add, rename, orphan). There is no automatic propagation of type or default changes to closed databases — those are confirmed per database when it is opened.
