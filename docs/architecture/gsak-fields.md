# Full GSAK Field Compatibility — Design (Epic #1028)

*Draft, 9 October 2026; revised 15 October 2026 after nagisml's review on #1028, 21 October 2026 when the small columns landed (#1139) and OpenSAK started filling them itself (#1140) and using them in filters (#1141), 23 October 2026 when custom fields moved to the companion database (#1034, epic #1149) and their storage and GSAK import were implemented (step 3), and 24 October 2026 when their UI and macro API were implemented (steps 4–5, #1149 phase 6). Update this file in the same commit when a sub-issue changes the design.*

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

**Exists in OpenSAK; the GSAK value is kept on import (#1139, previously replaced by the import time):**

| GSAK | OpenSAK | Note |
| --- | --- | --- |
| Created | `imported_at` | "First seen" survives the migration. On a re-import the earlier of GSAK's date and the existing value is kept |
| LastGPXDate | `last_gpx_update` | "Not refreshed by a PQ since …" filters keep working. Falls back to the import time when GSAK has no date |
| UserNoteDate | `user_notes.updated_at` | |

**Timestamps are stored in UTC (#1097).** Every timestamp OpenSAK records itself (`imported_at`, `last_gpx_update`, `last_user_update`, `user_notes.updated_at`) is naive UTC, so a database means the same on any computer and in any time zone. GSAK writes these dates in the computer's local time; the importer converts them to UTC, and a date without a time is taken as local midnight. The cache list and the date filters convert back to local time (`opensak/utils/timestamps.py`). Listing-side dates (placed, found, the listing's "changed" date, log dates) are calendar dates and are not converted.

**Added in #1139 (migration 27), filled by the GSAK importer:**

| GSAK | Used | OpenSAK |
| --- | --- | --- |
| LastUserDate | 12,600 | `last_user_update` (datetime, UTC). GSAK's "last changed by the user" date. Settles #821 item 6\*: kept separate from `last_updated` (the listing-side change date) rather than merging the two |
| MacroFlag | 96 | `macro_flag` (boolean, default false). GSAK macros' own working flag, separate from the user flag. Needed by ported macros |
| MacroSort | 2,240 | `macro_sort` (text). Same, for sorting |
| IsOwner | 28 | `is_owner` (boolean, default false). Also set by the GPX import and recalculated when the account changes (#1140, below). The GSAK filter importer maps `IsOwner` to it (#1141) |
| Symbol | 12,600 | `symbol` (text). Mostly "Geocache" / "Geocache Found" but user-editable in GSAK. Also taken from GPX `<sym>` (#1140); a source without one (a `.loc` file) leaves it as it is |

All of these, like the other personal fields, are updated even for a locked cache.

**How OpenSAK keeps them current (#1140, migration 28):**

- **`last_user_update` is stamped by the database.** Triggers set it to the current UTC time when a user field really changes: `user_flag`, `user_sort`, `user_data_1`–`4`, `locked` on `caches`, and the note or correction on `user_notes` (a new note with text or a correction, or a change to either). Because it happens in SQLite, every path is covered — the cache list, detail panel, edit dialog, bulk "clear flags", corrected coordinates, macro `update()` and raw `sql_write`. Text fields treat NULL and '' as equal, so saving an untouched dialog doesn't count. A statement that sets `last_user_update` itself wins. Imports and copies that carry their own date (the GSAK import, move/copy between databases) write it back explicitly after their rows are flushed. Not covered: deleting a note row with raw SQL (OpenSAK clears notes by updating them; a trigger touching the parent cache while the cache itself is being deleted would be undefined in SQLite). A migration that rebuilds a table must keep the triggers; `_add_column_defaults` drops and recreates them around the rebuild.
- **`is_owner` follows the account in Settings.** A cache is the user's own when its owner ID equals `gc_finder_id` or its normalised owner name equals `gc_username` — the same either-signal rule as `count_own_found_logs()` (`db/ownership.py`). The GPX import sets it per cache, and so do the edit dialog (adding or editing a cache) and macro writes that change the owner; changing the account in Settings or the welcome wizard recalculates it in every registered database, replacing earlier values including GSAK's; migration 28 does the same once. Without any account configured nothing is recalculated, so GSAK's values stay.
- **`symbol`** is taken from GPX `<sym>` when present.
- **`corrected_source`** is `user` for corrections made in the GUI and `macro` for macros (`set_corrected_coords(source=…)`, macro `update()`); clearing a correction clears it. The edit dialog only marks a correction as the user's when its coordinates actually change. Raw `sql_write` leaves it as it is.

**Macro access (step 2c):** `opensak.cache()` / `caches()` return `owned` (`is_owner`), `macro_flag`, `macro_sort`, `symbol`, `corrected_source` and `last_user_update` (UTC, like `last_gpx_update`). `update()` and `insert{}` can write `macro_flag`, `macro_sort` and `symbol`; `owned`, `last_user_update` and `corrected_source` are maintained by OpenSAK and refused. `sql_write` refuses `is_owner` and `last_user_update` as well (the database's own triggers may still stamp `last_user_update`), and recalculates `is_owner` when a macro changes `owner_id` / `owner_name` or inserts a cache, as `update()` and `insert{}` do. `filter{ owned = }` uses `is_owner` (`IsOwnerFilter`), so it agrees with the `owned` field; before, it compared the owner name with the username only. `filter{}` has a `last_user_update` date key and a `macro_flag` key (`MacroFlagFilter`, which has no control in the Filter dialog, as GSAK's MacroFlag has none in GSAK's GUI). `opensak.macro_flag("set"|"clear"|"swap", caches)` is GSAK's MACROFLAG: the active filter by default, GC codes, or `{ all = true }`. `opensak.sort("macro_sort")` sorts by `macro_sort` as text; the cache list has no column for it.

**Not added:**

| GSAK | Used | Proposal |
| --- | --- | --- |
| Source | 12,600 | Low priority (only "GC" here). Leave out unless a user shows other values |
| SmartName, SmartOverride | 0 | GSAK's short names for GPS units. Out for now; revisit with GPS export |
| ChildLoad, LinkedTo, GetPolyFlag | 0 / 0 / 15 | GSAK-internal. Left out |

### Corrected (101 rows)

Coordinates are mapped. Not mapped:

- **kType** — how the correction was made: `gui` 61, `macro` 37, `api` 3. Imported into `user_notes.corrected_source` (#1139): `gui` → `user`, `macro` → `macro`, `api` → `import`, anything else → NULL. Corrections that existed before migration 27 stay NULL ("unknown"), so a macro never mistakes an old user correction for one of its own. Since #1140 OpenSAK sets it itself when the user or a macro corrects coordinates (see above).
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

Custom fields live in the database's **companion database** (`Found.db` → `Found.companion.db3`, `db/companion.py`, #1034), not in the main file, owned by OpenSAK. Code: `db/custom_fields.py`.

```
custom_fields                       -- definitions, one row per field (registry kind 'free')
  field_id    TEXT PRIMARY KEY      -- stable UUID; schema changes match on this, never on name
  name        TEXT UNIQUE           -- case-insensitive, validated identifier
  type        TEXT                  -- string | integer | real | date | boolean
  position    INTEGER               -- display order
  default_value TEXT                -- as text, as GSAK stores it
  expression  TEXT NULL             -- GSAK's fexpression, kept; not evaluated yet (see Computed fields)
  control     TEXT, height INTEGER  -- kept for round-trip, UI may ignore at first
  scope       TEXT                  -- 'local' | 'global'
  origin      TEXT                  -- 'gsak' | 'opensak' (where the definition came from)
  orphaned    BOOLEAN               -- column kept after its field was deleted, see below

custom_values                       -- at most one row per cache (registry kind 'cache')
  gc_code     TEXT PRIMARY KEY      -- not cache_id: a foreign key can't point into another file
  <field>     <type>                -- one real column per stored field, nullable, no default
```

- **Keyed by GC code**, the same in every database (GSAK's `Custom.cCode` too). OpenSAK does the cascade itself: the companion's housekeeping deletes the rows with their caches, copies them on move/copy and removes orphans when the database is opened (#1034).
- **No row is needed for an untouched cache, and the columns are nullable.** A trigger in the main database can't write into another file, so instead the `caches_all` view (below) shows `COALESCE(value, default)`. A GSAK clause like `Angel = 0` still matches caches nobody has touched, and changing a default is only a change of the definition.
- **Local fields** are stored only in the database's companion.
- **Global fields** have their master definition in the app settings (`custom_fields.global`, `settings_store`; deleted ids in `custom_fields.global_deleted`). Each database also keeps a copy of the definition (`scope = 'global'`), so a backup or a copied database stays self-contained and can be opened on another machine without the global master.
- **Lazy:** a database gets its companion only when something is stored. A global field alone doesn't create one; until the database stores a value, `caches_all` shows the field's default as a constant. The first value written stores the definition and adds the column.

#### The `caches_all` view

SQLite doesn't let a view in one file refer to another, so `caches_all` is a **TEMP view**, created on each connection of the database engines (the active database and `session_for()`) when it is checked out: `caches.*` plus one column per field (local and global), `COALESCE(cv.<field>, <default>)`, from `caches LEFT JOIN companion.custom_values`. It is rebuilt when the definitions change. Ported GSAK SQL using `CachesAll` can use it.

#### Model built at runtime

Custom fields are defined by the user, not at build time, so `custom_values` cannot be a normal declarative model.

- `custom_values` is mapped at runtime from the global and local definitions with `registry.map_imperatively`, using its **own `MetaData`** so `create_all` never touches it.
- One model per open database (`custom_fields.values_model()`), cached by database path and a definition generation, and **rebuilt after every definition change**. The table is in the `companion` schema, so it works in sessions of the engines that attach the companion, next to `Cache`.
- Bulk paths (GSAK import, move/copy) write on the companion's own connection (`write_values()`, `companion.copy_rows()`), after the caches are committed (main database first).
- Prototyped by nagisml on SQLAlchemy 2.1 (inside the `sqlalchemy>=2.0,<2.2` range in `pyproject.toml`). There is no foreign key: `gc_code` links the row to its cache.

#### Applying definitions to a database

Runs when a database is opened and after each definition edit. Fields are matched by `field_id`, not by name.

| Situation | Action |
| --- | --- |
| Field defined, no column yet (e.g. a global field created while this database was closed) | `ADD COLUMN`, automatically |
| Same id, new name | `RENAME COLUMN`, automatically and without data loss |
| Column without a definition (field deleted, also a global field deleted from the master) | Keep the column and its data, mark it orphaned, offer cleanup. **Never drop automatically** (`drop_field()` on request) |
| Default, position, expression changed in the master | Copied into the database's definition (no column change) |
| Type changed | Reported as pending; the column is converted only after the user confirms (`change_type()`) |

**Why not `create_all` or Alembic's comparison** (both tested by nagisml):

- `create_all` never alters an existing table — a new column was not added.
- Alembic's `compare_metadata` reports a rename as drop + add, which loses all values.
- A ~50-line comparison using `PRAGMA table_info` plus the field ids handles the safe cases and leaves destructive changes as explicit user actions.

This is separate from OpenSAK's numbered migrations in `db/database.py` and the companion's own (`PRAGMA user_version`): those change fixed schemas, this only ever touches `custom_values`. It only runs for a database whose companion exists (lazy, see Storage).

#### What it costs

- Opening a database can now change its companion's schema, but only by adding or renaming columns in `custom_values`. Anything destructive needs the user's confirmation.
- No atomic commit across the two files: values are written after the caches, so a crash leaves at most orphan rows (removed on open), never a cache that lost its values.
- A global field couples databases together. The per-database copy of the definition limits that: a database never depends on the global master to open or to be read.
- Dropping or renaming a column needs SQLite 3.35 / 3.25. Python 3.12's bundled SQLite is newer, but check what the PyInstaller and MSIX builds actually ship.
- Names must not collide with `caches` columns or SQL keywords; the importer renames on collision and records it in the import report. All DDL uses quoted, validated names.

#### Computed fields

**Settled 23 Oct 2026 from real data:** in GSAK a field with an `fexpression` still has its column and stored value; the expression is a *display* transform of that value (`nullif(GCFinds,-1)` refers to the field itself). So every field keeps its column, `caches_all` shows the stored value (as GSAK's `CachesAll` does, so filters behave the same), and the expression is imported verbatim into `expression`. The cache list and the detail panel show it (step 4, `custom_fields.display_expression()` and `load_values()`): a GSAK expression goes through the GSAK filter translator, which knows the database's field names; one that can't be translated (GSAK functions `g_…`) or fails to run stays inactive and the stored value is shown. Filters and sorting always use the stored value, as GSAK's do. The expression runs in the SELECT under an authorizer that allows only reading (`custom_fields.read_only()`). Expressions are only ever evaluated inside a read-only SELECT — the same rule as `opensak.sql` and the known limitation in the macro concept §9b.

Examples from the sample database:

```sql
nullif(GCFinds,-1)                                   -- hide GSAK's "not fetched" sentinel
CASE PMFav WHEN 0.0 THEN '' ELSE printf(...) END     -- formatted display value from another field
```

#### GSAK import

Implemented in `importer/gsak_custom_fields.py`, called by `import_gsak_db()` after the caches are committed (only with the `companion-db` feature flag). The import dialog and `run_gsak_import()` unpack `gsak.db3` before the databases, so the global definitions are there.

- Local definitions come from `CustomLocal` in the GSAK database. Global definitions come from `CustomGlobal` in `gsak.db3`: every field there is global, so `CustomDefs` isn't needed (it is also stale in real data).
- Values come from `Custom`, which holds the columns for both local and global fields, for the imported caches that are in the target database.
- Imported global fields become OpenSAK global fields (`scope = 'global'`, `origin = 'gsak'`). If `gsak.db3` isn't available (a single database imported without its settings file), the columns without a local definition are imported as local fields, defined from the column's type and default, and the import report says so.
- Names that collide with `caches` columns or SQL keywords get `_custom` appended (`Name` → `Name_custom`) and are reported.
- A field that exists already (same name) is reused; with another type the existing definition is kept and values are converted where possible. GSAK's type names are taken case-insensitively (`string`, `Boolean`).

### Where custom fields show up

| Place | What |
| --- | --- |
| GSAK import | Local definitions from `CustomLocal`, global definitions from `CustomGlobal` / `CustomDefs` in `gsak.db3`, values from `Custom` |
| Define | *Tools → Custom fields…* (`gui/dialogs/custom_fields_dialog.py`): add, rename, change the type (after confirmation; values that don't convert are lost), change the default and the display expression (checked on save with `check_expression()`; a GSAK field's is still read as GSAK SQL), delete (orphaned, values kept) and *Remove data…* (`drop_field()`), local or global |
| Cache list | One column per field in the column chooser (`custom:<name>`, per database like the other columns; a rename moves the column along). `RefreshWorker` loads the visible fields from `caches_all` off the GUI thread (`load_values()`); display value as above, sorting by the stored value |
| Cache detail | A read-only *Custom* tab, hidden while the database has no fields |
| Edit cache | A *Custom fields* tab, one input per field by type; only changed fields are written, after the cache is committed |
| Filter dialog | The Custom tab (#821 item 5): one row per field, operator by type (yes/no; the text operators from #850; the number operators; the date operators without *compare*). `CustomFieldFilter` (by name, with its type) runs on `caches_all`, so untouched caches have the default. A profile's filter on a field the database lacks is kept and matches nothing |
| Where tab | Runs on `caches_all AS caches` while the view exists, so fields are used by name and `caches.id` sub-selects keep working |
| GSAK filter importer | The `*custom*` lines (`name;type;operator label;value1;value2`, the label in GSAK's UI language — English and German mapped) become native `CustomFieldFilter`s; where clauses use the active database's fields by name (renamed ones as `<name>_custom`), and `Custom` / `CachesAll` become `caches_all` (`Custom.cCode` → `gc_code`) |
| Macro API | `cache.custom` (name → value; booleans as true/false, defaults for untouched caches), `opensak.update/insert(…, { custom = { … } })` with the write approval (inside `opensak.transaction()` written after the outermost commit), `opensak.custom_fields()`, `opensak.define_custom_field{}` (idempotent for the same name and type), `opensak.delete_custom_field()`. SQL can read `companion.custom_values` but not change it (owner `opensak`). `macro_flag` and `macro_sort` are exposed to macros from the start |
| Move / copy caches | Values travel with the cache; a field missing in the target database is created there first (logged) |
| Backup / restore | Values and a copy of every stored definition (local and global) are in the companion, which backup sets include (#1034). The global master in the app settings belongs with the app-settings part of backup (#942) |
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
2. **Small columns** — **done in #1139** (migration 27): `last_user_update`, `macro_flag`, `macro_sort`, `is_owner`, `symbol`, `user_notes.corrected_source`; the GSAK importer fills them and keeps GSAK's `imported_at`, `last_gpx_update` and `user_notes.updated_at`, all as UTC (#1097).
   - **2b-1.** **Done in #1140** (migration 28): OpenSAK fills the columns itself — `last_user_update` via triggers, `is_owner` from the account, `symbol` from GPX, `corrected_source` from the GUI and macros.
   - **2b-2.** **Done in #1141**: the Dates tab has *Last user update* (`last_user_update`, compared in local time; settles #821 item 6\*), the column chooser has *Last user update* and *GPX symbol*, and the GSAK filter importer maps LastUserDate, IsOwner, MacroFlag, MacroSort and Symbol to the new columns.
   - **2c.** Macro access to the new columns (@nagisml): read all of them, write `macro_flag`, `macro_sort` and `symbol` (see above).
3. **Custom fields: storage and GSAK import** — implemented in the companion database (#1149 phase 4, behind the `companion-db` flag): `custom_fields`, the runtime-built `custom_values` model, the apply-definitions step, global master in the app settings, the `caches_all` TEMP view, import of local and global definitions and values, import report.
4. **Custom fields: UI** — implemented (#1149 phase 6, behind the `companion-db` flag): define dialog, column chooser, detail panel, Edit cache, Custom tab in the Filter dialog (#821 item 5), Where tab and GSAK filter importer, display expressions.
5. **Custom fields: macro API** — implemented with step 4: read, write, define.
6. **Ignore list** — #473.
7. **Cache and log images** — import the image metadata (URL, name, description). Lowest priority.

Steps 2 and 3 stop the data loss; everything after builds on stored data and can land in any order.

## Questions

- Does GSAK export custom fields in its GPX extension at all? If so, the GPX importer should read them too.
- `kBeforeState/County` and `kAfterState/County`: is OpenSAK's `location_basis` enough, or do users rely on GSAK's before/after values?

## Out of scope for now

- GSAK-internal tables and columns listed as "left out" above.
- Writing custom fields into GPX exports.
- Syncing global definitions beyond what the apply-definitions step does (add, rename, orphan). There is no automatic propagation of type or default changes to closed databases — those are confirmed per database when it is opened.
