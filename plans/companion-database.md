# Companion database: proposal and plan

*Draft, 22 October 2026, branch `feature/companion-db` (off `upstream/beta` 5f06fb74). No code yet.*

Sources: discussion #1034, #1048 (database maintenance), [Mike Wood's proposal via AgreeDK](https://github.com/OpenSAK-Org/OpenSAK/discussions/1034#discussioncomment-18886684), nagisml's answer of 13 Oct 2026, #1074 (read-only attach, done), epic #1028 and `docs/architecture/gsak-fields.md`, backup epic #942.

## 1. What was agreed in #1034

| # | Point | Source |
|---|---|---|
| A1 | One **companion database per cache database**, in the same folder, shared by all macros | Mike / AgreeDK Q1, nagisml "yes" |
| A2 | `opensak.macro_db()` stays, private per macro, for data that isn't per cache (FindStatGen pattern) | AgreeDK Q1, nagisml |
| A3 | The main DB keeps INSERT/UPDATE-only macro access and **no custom tables** | Mike |
| A4 | A table is **cache-linked** (keyed by cache code, OpenSAK does the housekeeping) or **free** (no housekeeping) | nagisml |
| A5 | The companion DB **belongs to OpenSAK**, not to a macro. Every table has an **owner** (macro id or `opensak`) | nagisml |
| A6 | **Custom fields (#1028) live in the companion DB**, owned by `opensak`. Macros read them; they write them only with permission. A `caches_all` view joins them, like GSAK's `CachesAll` | nagisml, AgreeDK Q4 |
| A7 | Cache-linked rows **follow the cache** on copy and move; a missing custom-field definition is created in the target first. Free tables and `macro_db()` data stay put | AgreeDK Q3, nagisml |
| A8 | The companion follows its main DB on **copy, rename, move, delete and backup** | Mike, AgreeDK Q3 |
| A9 | **Isolation:** a broken or huge companion must never crash OpenSAK or touch the main DB. Attach only when needed; on a failed integrity check rename it to `*.corrupt`, start an empty one, tell the user | nagisml |
| A10 | Maintenance: VACUUM, integrity check, **orphan cleanup** (no atomic commit across attached files in WAL mode). Follows the Database Manager maintenance of #1048 (see 2.9) | nagisml, #1048 |
| A11 | Read-only attach of other databases: done in #1074 (`opensak.attach()`); SQL `ATTACH` stays refused. `companion` is already a reserved schema name (`macro/sql.py` `_RESERVED_SCHEMAS`) | #1074 |

Ideas that were raised but marked as "maybe": size per owner and quotas, a maintenance dialog with per-owner delete/drop, keep or delete on macro uninstall (needs an install system), schema versioning, read/write permission per owner.

## 2. Proposal

### 2.1 The file

- **`<database stem>.companion.db3`** next to the main file: `Found.db` → `Found.companion.db3`.
  - `.db3` instead of `.db`: `db/discover.py` finds databases by `.db`, so the companion is never offered as a database of its own; `macro/permissions.py` `_is_sqlite_file()` already treats `.db3` as a database file, so macros' file functions can't touch it.
  - The name says what it belongs to, so a user copying files by hand sees the pair.
- **Created lazily**, the first time something writes to it (a custom field is defined, a macro creates a table). Most databases never get one, which also matches "attach only when needed".
- Own schema version in `PRAGMA user_version`, own numbered migrations (separate from the main DB's).
- Its identity is tied to the main DB: the companion stores the main DB's `db_uuid` (`db_settings`). On open, a mismatch (a companion copied next to another DB by hand) is reported, not silently used.

### 2.2 Inside the file

```
opensak_tables                     -- the registry, one row per user table (OpenSAK-owned, macros read only)
  name        TEXT PRIMARY KEY     -- table name in the companion
  owner       TEXT NOT NULL        -- 'opensak' or the macro path, as db_access._key() (Q6)
  owner_name  TEXT                 -- the macro's header name at creation, for display (Q6)
  kind        TEXT NOT NULL        -- 'cache' (cache-linked) | 'free'
  code_column TEXT                 -- for kind='cache': the column holding the GC code (default 'gc_code')
  created_at  TEXT, created_by_version TEXT
  description TEXT                 -- optional, shown in the maintenance dialog

opensak_meta                       -- key/value: main db_uuid, last orphan cleanup, ...

custom_fields                      -- owner 'opensak', kind 'free' (definitions, see gsak-fields.md)
custom_values                      -- owner 'opensak', kind 'cache'
  gc_code     TEXT PRIMARY KEY     -- NOT cache_id: ids differ between databases and can't be a cross-file FK
  <field>     <type> ...           -- one real column per stored field (agreed design, #1028)
```

Change against `gsak-fields.md`: `custom_values` is keyed by **`gc_code`** instead of `cache_id REFERENCES caches(id) ON DELETE CASCADE`. A foreign key can't point into another file, and the code is the only key that is the same in every database (it's also what GSAK's `Custom.cCode` uses). OpenSAK does the cascade itself (2.4).

**Rows for untouched caches:** no row is required. `caches_all` uses `LEFT JOIN` + `COALESCE(<field>, <default>)`, so `Angel = 0` still matches caches nobody touched, without the per-cache trigger GSAK needs (a main-DB trigger can't write into another file anyway).

### 2.3 Attaching

| Connection | When | How |
|---|---|---|
| Active DB engine (GUI, filters, cache list) | On connect, if the file exists | `ATTACH ... AS companion` in the `connect` listener in `db/database.py`, plus `TEMP VIEW caches_all` (a non-TEMP view in main can't refer to another schema, SQLite's rule) |
| `session_for(path)` (transfer, import into another DB) | Same | Same listener |
| Macro connections (`WritableDatabase`, `ReadOnlyDatabase`) | At run start if the file exists; created on the first companion write | Attached by OpenSAK as `companion`, authorizer rules in 2.5 |
| Other DBs via `opensak.attach()` | Optional | Also attach its companion read-only as `<schema>_companion` (counts against SQLite's limit of 10) |

If attaching fails (corrupt, locked, unreadable), OpenSAK logs it, continues without the companion, and shows one notice. Custom-field columns then show as empty and their filters are disabled; nothing in the main DB depends on the companion's schema.

### 2.4 Housekeeping OpenSAK does for cache-linked tables

| Event | Code today | Companion action |
|---|---|---|
| Delete caches (GUI, macro `opensak.delete`, `sql_write` DELETE) | `db/cache_delete.py`, TEMP BEFORE DELETE trigger in `macro/sql.py` | Delete the codes' rows in every `kind='cache'` table |
| Move/copy caches | `db/transfer.py` | Copy the rows; create a missing table in the target from its `sqlite_master` DDL + registry row; create missing custom-field definitions first; on move, delete in the source |
| `if_exists=skip/newer` | `transfer.py` | Same decision as the cache: a skipped cache's rows aren't copied |
| GSAK import | `importer/gsak_importer.py` | Custom fields → `custom_fields` / `custom_values` (#1028 step 3) |
| Re-import of a cache (GPX) | importer | Nothing: rows are keyed by code and stay |
| Database open | `init_db()` | Quick check + orphan cleanup (rows whose code is not in `caches`), at most once per session, in the background for large files |

**Write order** across the two files: main first, companion second. A crash in between leaves orphan rows in the companion (cleaned up on next open) but never a cache without its data, except for a move, where the source rows are deleted only after the target's are written.

### 2.5 Macro access rules

| Action | Main DB | Companion | `macro_db()` |
|---|---|---|---|
| SELECT | yes | yes, every table | yes |
| INSERT/UPDATE/DELETE | write approval, cache tables only | **own tables:** yes; **`opensak` tables (custom values):** with write approval, through the API; **other macros' tables:** see Q4 | yes |
| CREATE/ALTER/DROP | no | yes, own tables only, recorded in `opensak_tables` by the authorizer (it gets the table name with `SQLITE_CREATE_TABLE`) | yes |
| Triggers/views | TEMP only | own, only on own tables (SQLite: may only refer to the same file) | yes |
| Tables starting with `opensak_` | — | read only | — |

API (names to be agreed, shape follows `macro_db()` / `attach()`):

```lua
opensak.companion()                                  -- make sure the file exists and is attached; returns its path
opensak.companion_table{ name = "ftf_info", linked = true, code_column = "gc_code",
                         description = "FTF data from ftf.lua" }   -- declare kind before/after CREATE
opensak.companion_tables()                           -- registry rows: name, owner, kind, rows
opensak.sql("CREATE TABLE IF NOT EXISTS companion.ftf_info (gc_code TEXT PRIMARY KEY, ftf_date TEXT)")
opensak.sql("SELECT c.gc_code, f.ftf_date FROM caches c JOIN companion.ftf_info f USING (gc_code)")
```

Ownership is recorded automatically (authorizer + the running macro's key), which answers "not sure we can ensure that": macros can't create a table without OpenSAK seeing it.

### 2.6 Lifecycle of the file

| Operation | Code | Change |
|---|---|---|
| Rename | `DatabaseManager.rename()` | Rename the companion with its `-wal/-shm` |
| Move / move all | `move_database()`, `_relocate()`, `move_databases_to()` | Move it too |
| Copy | `copy_database()` | `snapshot_database()` of the companion too; store the copy's new `db_uuid` in it |
| Delete | `delete_database()` | Delete it too; the "folder is empty" check must count it |
| Remove from list | `remove_from_list()` | Leave the file; dispose its handles |
| Dispose | `dispose_engine()` | Releases the attached file too (same engine) |
| Discover / Found databases | `db/discover.py` | Not listed (`.db3`); maybe show "has companion data" |
| Backup | `backup/backupset.py` | `companions/` folder per set, `DatabaseEntry.companion` in the manifest (a new key, format stays 1 as for `macro_databases`); exit-state fingerprint includes it |
| Restore | `backup/restore.py` | Restore it next to the restored DB; an older set without one restores without one |
| Pre-migration backup | `backup/premigration.py` | Before a companion migration, back it up the same way |
| Maintenance (Info, Check, Optimize, Checkpoint, Compact) | `db/maintenance.py`, #1048 | Always for both files together, see 2.9 |

### 2.7 Isolation and corruption

- `PRAGMA quick_check` on first attach per session; full `integrity_check` from the maintenance dialog.
- On failure: detach, rename to `<stem>.companion.corrupt-<timestamp>.db3`, don't create a new one until something writes, show a notice with the path. The main DB is never touched.
- Size: `dbstat` is **not** compiled into the SQLite bundled with Python (checked: 3.50.4, no `ENABLE_DBSTAT_VTAB`), so per-table size is estimated (row count × average row size from a sample) or shown as row counts only. File size per database is exact.
- Warn when the companion grows above a threshold (e.g. 2× the main DB or 1 GB). Quotas per macro: later, if ever.

### 2.8 `macro_db()` vs companion

| | `macro_db()` | Companion |
|---|---|---|
| Owner | the macro | OpenSAK (tables owned by macros) |
| Per | macro (`foo.lua` → `foo.db3`) | cache database |
| Shared | no | all macros read, owners write |
| Housekeeping | none | cache-linked rows follow caches |
| Follows database copy/move/backup | no (backed up separately) | yes |
| Good for | options, log-based data (FSG), caches across DBs | per-cache data, custom fields, data other macros use |

### 2.9 Maintenance (#1048)

#1048 specifies database maintenance in the Database Manager: **Info, Check, Optimize, Checkpoint, Compact** (VACUUM), building on `db/maintenance.py` (#1033: `page_stats()`, `is_worthwhile()`, `check_free_space()`, `vacuum()`, `vacuum_file()`). The companion follows the same rules. Run from the Database Manager, every action works on **the database and its companion together**: the user maintains one database, not two files.

| #1048 function | Main DB | Companion |
|---|---|---|
| **Info** | size, `-wal`, free pages, schema, SQLite version, row counts | A second block: file and `-wal` size, free space, companion schema version, number of tables, and per owner: tables, rows, estimated size (no `dbstat`, see 2.7) |
| **Check** | `quick_check` / `integrity_check` + `foreign_key_check` | The same on the companion file (it has no foreign keys into the main DB), plus the companion's own checks: `db_uuid` matches, `opensak_tables` matches the real tables (unregistered tables, registry rows without a table), **orphan rows** in cache-linked tables (codes not in `caches`). Results listed per file. A failed check offers the corrupt handling of 2.7 for the companion, never for the main DB |
| **Optimize** | `PRAGMA optimize` (`0x10002` on SQLite ≥ 3.46) | `PRAGMA companion.optimize` on the same connection, or on its own connection. Also when the database is closed or switched and after imports, deletes and moves, as #1048 proposes for the main DB |
| **Checkpoint** | `wal_checkpoint(TRUNCATE)` | `PRAGMA companion.wal_checkpoint(TRUNCATE)` |
| **Compact** | checkpoint → `VACUUM` | `maintenance.vacuum_file()` on the companion file, after the main DB, in the same exclusive window |

**Rules for Compact, applied to the pair**
- **Exclusive access:** disposing the engine (as #1048 does for the active database) also releases the companion, since it is attached to the same connections. `session_for()` jobs on the same database must be finished or blocked, as for the main DB. The companion is not attached again until both files are done.
- **Disk space:** check room for the larger of the two files (they are compacted one after the other), with the margins of `check_free_space()`. Not enough for the companion → compact only the main DB and say so.
- **Show the gain first:** `free_bytes` for each file and the sum. Each file is compacted only when `is_worthwhile()` says so (thresholds of #1033/#1048), so a small companion is usually left as it is.
- **Backup:** the backup offered before Compact is a normal backup set, which already includes the companion (step 1).
- **Progress:** one busy indicator for the whole action, with the current file named.
- **Afterwards:** `optimize` on both files; "before → after" per file and in total.
- A failed or skipped companion compaction never fails the main DB's: it is reported in the result.

**Repair (orphans, registry).** #1048 leaves "Repair/Defrag" (GSAK's referential and data checks) for a separate issue. The companion's repairs belong there: delete orphan rows, register or drop unregistered tables, remove registry rows without a table. Until then the orphan cleanup on open (2.4) handles orphans silently.

**Macro API.** #1048 offers no maintenance to macros for now; that applies to the companion too. Macros can't run VACUUM, PRAGMA optimize or checkpoint on it (the authorizer refuses them, as on the main DB). If #1048 later adds a macro call, it should cover both files.

**Maintenance dialog (phase 5).** The per-owner companion view (tables, rows, size, delete rows, drop table, reassign owner) becomes a **"Companion data" section of the #1048 Maintenance dialog**, not a dialog of its own.

## 3. Plan

Behind a new feature flag `companion-db` (default off in release, on in `features.json`), like `lua-macro-write`.

| Phase | Content | Size |
|---|---|---|
| **0. Design** | Answer the questions in section 4; update `docs/architecture/gsak-fields.md` (custom fields move to the companion, `gc_code` key); new `docs/architecture/companion-database.md` from this plan; draft an epic issue (sub-issues = phases 1–6), posted only after OK | S |
| **1. File and lifecycle** | `db/companion.py` (path, create, schema v1 with `opensak_tables`/`opensak_meta`, migrations, uuid check); attach in the `connect` listener; rename/move/copy/delete/dispose in `DatabaseManager`; discover; backup, restore, pre-migration backup, exit state. Tests for every lifecycle operation. No UI, no macro access | M |
| **2. Housekeeping** | Cascade in `cache_delete.py` and the macro DELETE trigger; transfer of cache-linked rows; orphan cleanup on open; quick check + corrupt handling; companion support in `db/maintenance.py` for the #1048 functions (2.9): info, check, optimize, checkpoint, compact for the pair, without UI | M |
| **3. Macro access** | `companion` schema in macro connections; authorizer rules (2.5) and automatic owner recording; `opensak.companion()`, `companion_table{}`, `companion_tables()`; `api_docs.py` (regenerated `api.md` not committed); examples; also via `opensak.attach()` | M–L |
| **4. Custom fields storage + GSAK import** | #1028 step 3, in the companion: `custom_fields`, runtime-built `custom_values` model (own `MetaData`, `gc_code` key), apply-definitions step, global master in app settings, GSAK `CustomLocal`/`CustomGlobal`/`Custom` import, `caches_all` TEMP view | L |
| **5. Maintenance dialog** | A "Companion data" section in the #1048 Maintenance dialog: the companion's Info/Check/Compact results (2.9); per owner and table: kind, rows, estimated size; delete a table's rows / drop a table; owner missing (macro file moved or deleted) with "Reassign to …". Depends on the #1048 dialog | M |
| **6. Custom fields UI + macro API** | #1028 steps 4–5: column chooser, detail panel, Filter Custom tab, Where tab, GSAK filter importer, macro read/write/define | L |

Phases 1 and 2 belong together in one PR if they stay reviewable; 3 and 4 can run in parallel after that. Each PR: pytest + whole-repo mypy, CHANGELOG entry, user guide where something becomes visible.

### Tests to write early (phase 1)

- Rename/move/copy/delete with and without a companion, including `-wal/-shm`, on Windows file locks.
- Backup → restore round trip; restore of an old set without companion; compressed sets.
- Main DB opens normally with a missing, corrupt, locked or foreign-uuid companion.
- `discover.py` doesn't list companions; macro file functions can't open them.

### Status

- **Step 1 implemented** (22 Oct 2026, not committed): `db/companion.py`; attach on checkout in `db/database.py`; rename, move, move all, copy, delete in `db/manager.py` (plus: a companion can't be opened as a database); the welcome wizard leaves companions to the database move; backup set (`companions/`), restore, pre-migration backup (companion migrations), exit-backup state; feature flag `companion-db`; tests in `tests/unit-tests/test_companion_db_1034.py`. Discovery needed no change (`.db3`).
- **Step 2 implemented** (22 Oct 2026, not committed):
  - Cache-linked rule in `companion.cache_linked_tables()`: a `gc_code` column, unless the registry says `free`; the registry can name another code column; `opensak_*` tables never.
  - Delete: `delete_caches()` deletes the rows in the same transaction when the companion is attached (GUI, `opensak.delete` outside a transaction). Macro writes don't attach it yet (phase 3), so the runtime deletes the rows of `sql_write` and in-transaction deletes on the companion's own connection after the (outermost) commit; an undone transaction keeps them.
  - Copy/move: `transfer_caches()` copies the rows after the target's commit (`companion.copy_rows()`), creating the target's companion, the table with its indexes and its registry row; columns the target table lacks are left out; skipped caches keep the target's rows. Move then deletes them with the source caches.
  - Opening: the first `prepare()` per session runs `quick_check` and removes orphans. A damaged companion is moved to `<stem>.companion.corrupt-<time>.db3` (a locked one is only skipped); damaged, foreign and newer companions are reported once per session in a warning shown with the pre-migration notices.
  - Maintenance (#1048, no UI): `companion.info()`, `check()` (integrity/quick check, foreign keys, db_uuid, registry vs tables, orphans; changes nothing), `optimize()`, `checkpoint()`, and `maintenance.vacuum_with_companion()` for Compact of the pair.
  - Tests: `tests/unit-tests/test_companion_housekeeping_1034.py`.
- **Step 3 implemented** (23 Oct 2026, not committed, branch `feature/companion-db-macros` off `feature/companion-db`, worktree `OpenSAK-companion-macros`):
  - Macro SQL connection attaches the active companion as `companion` whenever it exists (`runtime._attach_active_companion()`); `sql.CompanionAccess` in both authorizers: read everything; create tables/indexes/views; change/alter/drop own only; `opensak_*` and unregistered tables read-only; triggers refused (decision 23 Oct: housekeeping would run them unchecked); macro without a file reads only. Owner recorded after each DDL statement (`record()`: register, follow a RENAME, unregister on DROP); registry cached per connection, reloaded on `PRAGMA companion.data_version` change or rollback.
  - Approval (decision 23 Oct): like `macro_db()` — `opensak.sql()` writes own tables without approval, `sql_write()` keeps asking.
  - `sql_write` DELETE of caches now deletes their companion rows in the same transaction; `_purge_companion()` only when the companion wasn't attached.
  - `opensak.companion()`, `companion_table{}` (table must exist; `linked`, `code_column`, `description`), `companion_tables()`; behind the `companion-db` flag (`ApiFunction.companion`); API_VERSION 4.
  - `opensak.attach()` also attaches the database's companion read-only as `<schema>_companion` (decision 23 Oct: automatic), checked with `companion.peek()` (no writes); detached with it; `attached()` has `companion`.
  - Registry views count in `check()`. API docs section "The companion database"; examples `dnf_stats.lua` (writer) and `dnf_alert.lua` (reader). Tests: `tests/unit-tests/test_companion_macros_1034.py`.
- **Step 4 implemented** (23 Oct 2026, not committed, branch `feature/companion-custom-fields` off `feature/companion-db`, worktree `OpenSAK-companion-custom-fields`; merges cleanly with `feature/companion-db-macros` and `upstream/beta`):
  - `db/custom_fields.py`: `custom_fields` / `custom_values` (gc_code key, registered as owner `opensak`, free / cache-linked), field ids, name rules (cache columns, SQL keywords → `_custom` suffix), types incl. "Decimal" → real (#126).
  - Decisions 23 Oct: globals are **lazy** (no companion just for them; `caches_all` shows their default as a constant until a value is written); **nullable columns + COALESCE** in `caches_all` (default change = definition only; type change needs `change_type()` after confirmation); **fexpression stored verbatim, not evaluated** (in real GSAK data it's a display transform of a stored column, e.g. `nullif(GCFinds,-1)`), translation with the UI in phase 6.
  - Apply on open (`database._open_engine` → `custom_fields.on_open`): add columns, follow master renames, orphan master deletes (tombstones `custom_fields.global_deleted`), report type changes as pending. Never drops.
  - `caches_all` TEMP view rebuilt per connection on checkout when the definitions' generation changed; `values_model()` runtime-built SQLAlchemy model (own MetaData, schema `companion`).
  - Transfer: `companion.copy_rows()` calls `copy_definitions()` first. Delete cascade and orphan cleanup come from phase 2 (cache-linked by `gc_code`).
  - GSAK import: `importer/gsak_custom_fields.py` (CustomLocal, CustomGlobal from gsak.db3, Custom values); `import_gsak_db(settings_db=)`; dialog and `run_gsak_import()` unpack gsak.db3 before the databases when the flag is on. New tr key `gsak_import_custom_fields`.
  - Docs: `docs/architecture/gsak-fields.md` updated. Tests: `tests/unit-tests/test_custom_fields_1028.py`.
  - Not done here: Ignore list (#473, #1028 step 6), GSAK extra fields in GPX import (lab2gpx mapping, #473), UI and macro API (phase 6), maintenance dialog (phase 5).
- **Step 5 implemented together with #1048** (24 Oct 2026, not committed, branch `feature/companion-maintenance` off a local merge of `feature/companion-custom-fields` and `feature/companion-db-macros`, worktree `OpenSAK-companion-maintenance`):
  - The #1048 dialog didn't exist yet, so this step builds it: `gui/dialogs/database_maintenance_dialog.py`, opened by *Maintenance…* in the Database Manager. Info, Check (Thorough), Optimize, Checkpoint, Compact… for the database and its companion; every step in a `QThread` with an inline busy bar.
  - Main-database side in `db/maintenance.py`: `file_info()`, `check_file()`, `optimize()`/`optimize_file()`, `checkpoint_file()`, dialog thresholds `MANUAL_MIN_RECLAIM_*` (10 MB and 5 %).
  - Compact: only files where it pays off; disk space checked for the larger file; backup set first (ticked from 100 MB); on the active database `database.release_connections()` empties the engine's pool instead of disposing the engine, so nothing on the GUI thread finds the database closed; optimize afterwards.
  - Companion data (flag `companion-db`): tables and views per owner, kind, rows, estimated size (`companion.size_estimate()`, sample of 1000 rows); delete rows, drop table/view, *Reassign owner…* (missing owner or unregistered table; a macro from the macro folder or any .lua file), *Clean up* (`companion.cleanup()`: orphans and registry rows without a table — the repairs the plan had left to the Repair/Defrag issue). OpenSAK-owned tables (custom fields) are listed but refused (`CompanionError`). Size warning above 1 GB or twice the database. A damaged companion found by Check can be moved aside (`companion.move_aside()`).
  - Optimize on its own (#1048 comment with measurements): `database.optimize_database()` on open, before an engine is disposed (close, switch, move, rename), after migrations, in `MainWindow.closeEvent`, and after GPX/PQ/GSAK imports, deletes (GUI and `opensak.delete` outside a transaction) and moves/copies. Own connection with `PRAGMA optimize=0x10002` (a plain `optimize` on a fresh connection does nothing); older SQLite: `ANALYZE` once, then `optimize`.
  - Tests: `tests/unit-tests/test_db_maintenance_1048.py`. User guide: *Database Maintenance*.
- **Step 6 implemented** (24 Oct 2026, not committed, branch `feature/companion-custom-fields-ui` off `feature/companion-maintenance`, worktree `OpenSAK-companion-custom-fields-ui`); decisions 24 Oct (nagisml): macro API integrated into the cache records and `update()`, values edited in Edit cache (detail panel read-only), a define dialog in this step, GSAK display expressions used for display:
  - Core (`db/custom_fields.py`): `load_values()` / `active_values()` read many caches' fields from `caches_all` with their display value; `display_expression()` translates a GSAK expression with the filter translator (`translate_where(known_fields=)`), checked with `SELECT … LIMIT 0` and run under `read_only()` (authorizer: read, select, function only); `parse_value()` checks input from the GUI and macros.
  - GUI: *Tools → Custom fields…* (`gui/dialogs/custom_fields_dialog.py`); columns `custom:<name>` in the column chooser and the cache list (values loaded in `RefreshWorker`, sort by stored value; `gui/custom_field_format.py`); read-only *Custom* tab in the detail panel; *Custom fields* tab in Edit cache (`CustomFieldsForm`, written after the cache's commit).
  - Filters: `CustomFieldFilter` (by name and type, `prepare()` on `caches_all`, unknown field → nothing); the Filter dialog's *Custom* tab (`CustomFilterRow`; filters on fields the database lacks are kept); Where clauses run on `caches_all AS caches` (`where_source()`); the ⓘ help lists the fields.
  - GSAK filter import: `*custom*` lines → native filters (operator labels English/German), known fields by name in where clauses (before `WHERE_UNMAPPABLE`, so e.g. FavPerc and LabID work when they are custom fields), `Custom` / `CachesAll` → `caches_all`.
  - Macros (`macro/custom_data.py`): `custom` in the records, `update/insert{ custom = }` (approval; queued until the outermost transaction commits), `custom_fields()`, `define_custom_field{}`, `delete_custom_field()`; API docs section "Custom fields"; GSAK comparison updated.
  - Tests: `tests/unit-tests/test_custom_fields_ui_1028.py`, `test_custom_fields_gui_1028.py`, `test_custom_fields_macros_1028.py`. Docs: `docs/filters.md`, `docs/architecture/gsak-fields.md`, user guide.
- Still open: repairs of the main database (GSAK's Repair/Defrag checks) wait for their own issue; the redundant and missing indexes found while measuring (#1048 comment) are a separate change. For custom fields: GSAK extra fields in GPX import/export (#473), GSAK's control/height properties in the GUI, macro-defined columns (§5).

## 4. Questions and options

### Decisions (22 Oct 2026, nagisml)

| Q | Decision |
|---|---|
| Q1 | (a) `Found.companion.db3` next to `Found.db` |
| Q2 | Custom fields live in the companion |
| Q3 | (a) a `gc_code` column makes a table cache-linked; `companion_table{ linked = false }` opts out |
| Q4 | (a) only the owner writes its tables; everyone reads. (c) `shared_write` can follow later without breaking anything |
| Q5 | (a) own tables without approval; custom values with write approval |
| Q6 | (a) owner = the macro file's path, as for write approvals and `macro_db()`. A moved or renamed macro is a new owner; its old tables show as "owner missing" in the maintenance dialog (phase 5), which offers "Reassign to …" and delete/drop |
| Q7 | (a) `quick_check` on first attach per session |
| Q8 | (a) create the missing table in the target |
| Q9, Q10 | open |

**Q1. File name and place**
- (a) `Found.companion.db3` next to `Found.db` — **recommended** (not discovered as a DB, already protected from macro file access)
- (b) `Found.db.companion` — clearly a sidecar, but the permission check and backup need a new suffix rule
- (c) a folder `Found.opensak/companion.db3` — room for more sidecars later (images cache), but more to move and harder to see

**Q2. Custom fields in the companion: confirm**
This changes the agreed #1028 design (main DB, `cache_id` FK with cascade). Gains: one design for macros and custom fields, main DB schema fixed. Costs: no FK/cascade (OpenSAK does it, 2.4), no atomic commit across the two files, every filter on a custom field joins a second file. Alternative: custom fields in the main DB as in `gsak-fields.md`, companion only for macro tables. **Recommended: companion, as written on #1034**, since it's what you already proposed publicly.

**Q3. How a table becomes cache-linked**
- (a) by convention: every companion table with a `gc_code` column is cache-linked (rows follow the cache, several rows per cache allowed, so log-based tables also work); `companion_table{ linked = false }` opts out — **recommended**, ported GSAK macros work with plain SQL
- (b) only by declaring it with `companion_table{}`; undeclared tables are free
- (c) only the API may create tables (no raw `CREATE`)

**Q4. Writing other owners' tables**

Only about tables that macros create. Custom fields belong to `opensak` and follow Q5 (write approval). Reading is always allowed.

Example: `ftf.lua` creates `companion.ftf_info (gc_code, ftf_date, ftf_by)`. `stats.lua` only reads it. `fix_ftf.lua` wants to correct a wrong date in it:

```lua
-- fix_ftf.lua
opensak.sql_write("UPDATE companion.ftf_info SET ftf_date = '2024-05-01' WHERE gc_code = 'GC12345'")
```

- (a) **only the owner writes.** `fix_ftf.lua` gets *"companion.ftf_info belongs to ftf.lua; only that macro can change it"*. The fix has to go into `ftf.lua` (or `ftf.lua` offers a function the user runs). Protects against a buggy or careless macro emptying another macro's data (`DELETE FROM companion.ftf_info`). Simple to explain and to implement (one authorizer check).
- (b) **any macro with write approval.** The UPDATE works once the user has approved `fix_ftf.lua` (the existing per-macro dialog). Closest to GSAK, where any macro could change anything. The approval dialog says "change your databases", not "change ftf.lua's data", so the user doesn't really see this.
- (c) **the owner decides.** `ftf.lua` declares `opensak.companion_table{ name = "ftf_info", shared_write = true }`; then (b) applies to that table, otherwise (a). Fits macro suites (an importer macro and an editor macro that work on the same table) without opening everything.
- **Suites without (c):** two macros of one suite can also be one owner, if Q6 gives them the same owner key (e.g. an `-- Id:` header).

**Recommended: (a) now, (c) later** when a real suite needs it. (a) → (c) is a non-breaking extension; (b) → (a) would break macros that rely on it.

**Q6. Owner key — how a table remembers which macro owns it**

Whatever is stored in `opensak_tables.owner` decides who may write the table (Q4), what the maintenance dialog shows, and when an owner counts as "gone".

- (a) **path** of the macro file, as the write approvals (`macro/db_access.py` `_key()`, `macros.write_always`) and `macro_db()` (`foo.lua` → `foo.db3`) already do:
  `owner = c:\users\alice\opensak\macros\ftf.lua`
  - ✅ unique, no two macros can share it by accident; consistent with approvals.
  - ❌ the user moves the file to `macros\gsak-ports\ftf.lua` → a different owner; `ftf.lua` can no longer write its own table.
  - ❌ restoring a backup on a new PC (`D:\Geocaching\OpenSAK\macros\ftf.lua`) → every table has a missing owner.
  - ❌ `ftf.lua` v1 in `Downloads\` and v2 in `macros\` are two owners.
- (b) **name** from the header (`-- Name: FTF Tracker`), else the file name:
  `owner = FTF Tracker`
  - ✅ survives moves, renames of the folder, a new PC, new versions.
  - ❌ collisions: two unrelated macros called `Statistics`, or two header-less files called `test.lua`, own each other's tables.
  - ❌ editing the header (`-- Name: FTF Tracker 2`) loses ownership.
- (c) **an explicit id** in the header, new key `-- Id: nagisml.ftf-tracker` (reverse-domain style, like a package name); without one, the path (a):
  - ✅ stable across moves, PCs and versions; a suite can use one id for several macros (Q4).
  - ❌ a new header key authors must know about; another macro could copy the id (it's protection against accidents, not attacks — a macro with write approval can already change the main DB).
- (d) **path, plus taking over when the old path is gone:** key as (a); the name and file name are stored too. When `ftf.lua` runs from a new place and finds a table whose owner path no longer exists **and** has the same file name, OpenSAK takes the table over and prints one line (*"companion.ftf_info: owner moved from … to …"*). Everything else goes through "Reassign to …" in the maintenance dialog.

Example registry rows with (c)/(d) combined:

| name | owner | owner_name | owner_file |
|---|---|---|---|
| `custom_values` | `opensak` | OpenSAK | — |
| `ftf_info` | `id:nagisml.ftf-tracker` | FTF Tracker | `ftf.lua` |
| `solver_notes` | `path:c:\users\alice\opensak\macros\solver.lua` | Puzzle solver | `solver.lua` |

**Recommended: (c) with (d) as the fallback** — the id when the header has one (shareable macros), otherwise the path with automatic take-over. That handles moves and a new PC without collisions. If a new header key is too much for now: (d) alone, and `-- Id:` later.

**Q5. Approval for companion writes**
- (a) own tables: no approval, like `macro_db()`; custom values: write approval, like `opensak.update()` — **recommended**
- (b) everything in the companion needs write approval

**Q7. Integrity check cost**
- (a) `quick_check` on first attach per session — **recommended**
- (b) only at startup for the active DB
- (c) only on demand (maintenance dialog)

**Q8. Copying a cache whose table doesn't exist in the target**
- (a) create the table in the target with the same DDL — **recommended** (what you wrote for custom fields)
- (b) skip and report

**Q9. First PR scope**
- (a) phase 1+2 (no user-visible change, behind the flag) — **recommended**
- (b) phase 1–3, so macro authors can try it right away
- (c) start with phase 4 (custom fields), the biggest user gain

**Q10. Issue tracking**
New epic "Companion database" linking #1034/#1028/#942, or sub-issues under #1028? (Draft first, posted only after OK.)

## 5. What becomes possible (next steps)

**Directly enabled**
- Full GSAK custom-field parity (#1028, #473, #126) and the Filter dialog's Custom tab (#821 item 5); ported GSAK SQL using `CachesAll` works through `caches_all`.
- Macros that build on each other's data (Mike's example): one macro stores FTF, finder counts or puzzle solutions; others read them.
- Large per-cache data kept out of the main DB: translated descriptions, cached elevation/region lookups, solver notes, images metadata (#1028 step 7).

**Follow-ups**
- **Macro-defined columns in the cache list and filters:** a macro registers a column of a cache-linked table (`companion_column{ table=, column=, label=, type= }`), and the GUI shows and filters on it like a custom field. This is the step that turns macros into real extensions.
- **Undo for macro writes:** an `opensak_changelog` table in the companion records old values of every macro `update()`/`sql_write`, so "undo last macro run" becomes possible without touching the main DB.
- **Export/import companion data:** CSV/JSON per table, or as a GPX extension, to share data between users or move it to another installation.
- **Macro install system:** install/uninstall with "keep or delete this macro's data" (needs the companion's owner registry; also the trust moment AgreeDK described).
- **Read-only attach of other DBs' companions** with `opensak.attach()` (FSG pattern: found and placed caches in separate DBs).
- **Quotas and size warnings per owner**, once the maintenance dialog exists.
- **Cache code renames/merges** (e.g. a GC code reused, or GSAK's `LinkedTo`): rows can follow a rename in one place.
- **Sync with GSAK:** write custom values back into a GSAK `Custom` table for users running both programs.
