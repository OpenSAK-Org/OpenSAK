"""
src/opensak/macro/gsak_compare.py — GSAK macro commands vs. the Lua API.

The last section of docs/macros/api.md, for people porting GSAK macros.
A comparison and work in progress, not part of the API contract; kept as
Markdown here so api_docs.py can append it unchanged.
"""

GSAK_COMPARE = """\
## GSAK macros compared

> **Work in progress.** This section is a comparison, not part of the API: it maps the commands and functions of GSAK's macro language to their OpenSAK counterparts, to help when porting a GSAK macro. It was checked on 2026-10-12 and will change as functions are added.

The sandbox keeps the Lua 5.4 `string`, `table`, `math`, `utf8` and `coroutine` libraries, plus `os.time`, `os.date` and `os.clock`. It removes `io`, `debug`, `package`, `require`, `load*`, `dofile` and the rest of `os`. So most of GSAK's language, string and math functions need no API.

**Legend:** ✅ available · 🚧 in development, not in this version yet · ➖ covered by Lua itself, no API needed · ❌ not available yet · 🚫 not applicable or deliberately left out

### Main gaps

- Deleting caches, waypoints and logs (`Delete`, `MacroDelete`, `PurgeLogs`): `opensak.sql_write()` refuses DELETE. Planned.
- Loading GPX / ZIP files (`Load`). Planned.
- Writing files and reading text files (`PutFile`, `AppendFile`, `FileOpen`, `TextOut`, `GetFile`, `FileRead`): only `opensak.read_csv()` exists. Planned.
- File system helpers (`FileExists`, `FolderExists`, `Dir`, `FileCopy`, `FileErase`).
- Unicode `Upper` / `Lower`: `string.upper` only changes ASCII letters, so `("zürich"):upper()` gives `"ZüRICH"`.
- Centre point (`Centre`), saving and restoring the filter (`SaveFilter`, `RestoreFilter`), custom fields (`CustomGet`, `CustomPut`), shared code (`Include`), sorting by several columns (`SQLSort`).

### Database / Waypoints

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| AddNew | ✅ | `insert{}`, `insert_waypoint/log/attribute/trackable` |
| CacheMate | 🚫 | Palm-era format |
| CancelFilter | ✅ | `clear_filter()` |
| CodeMatch | ✅ | `filter{codes=}`, `sql("… WHERE code IN …")` |
| CustomConfig / CustomGet / CustomPut | ❌ | needs custom fields; `user_data[1..4]` exist today |
| Database | ✅ partial | `switch_database`, `create_database`, `databases`; no delete |
| DatabaseExists | ✅ | `database_exists()` |
| DataSave / DataRestore | ✅ workaround | `macro_db()` + `sql_write`; no direct counterpart |
| Delete | ❌ | planned |
| Elevation | ✅ partial | `elevation` field (metres); no conversion to the user's unit |
| Export | ✅ partial | `export_file` / `export_gpx` (GPX/LOC/GGZ), POI export 🚧; no CSV, HTML or other formats |
| Filter | ✅ | `filter_profile(name)`, `profiles()` |
| GarminApi | 🚫 | Opencaching.com is gone |
| GlobalReplace | ✅ | `sql_write("UPDATE …")`, `update()` |
| GetCountry / GetState / GetCounty | ✅ | `coords.location()`, `country/state/county` fields |
| GetNear | ✅ | `filter{near=}`, `caches{near=}` |
| GetPolygon | ✅ | `coords.inside()`, `filter{polygon=}` |
| GoTo / Seek | ✅ / 🚧 | read with `cache(code)`; selecting a cache in the grid 🚧 |
| Grab | ❌ | planned |
| IsOwner | ✅ partial | `filter{owned=true}`; no `owned` cache field, no log-owner check |
| Load | ❌ | planned |
| MacroDelete | ❌ | `sql_write` refuses DELETE; planned |
| MacroFlag | ❌ | workaround: `filter{codes=}` or a table in `macro_db()` |
| MFilter | ✅ | `filter{…}`, `filter{where=}` |
| MoveCopy | ✅ | `move_caches`, `copy_caches` |
| PurgeLogs | ❌ | |
| PurgeNote | ✅ partial | `update(code, {note=false})` |
| SaveFilter / RestoreFilter | ❌ | only `filter_name()` |
| ReSync | ✅ | `resync()` |
| ReSyncLogs | 🚫 | needs the geocaching.com API |
| SetColor | ✅ partial | `update{color=}` for the cache colour; no per-column colouring |
| SQLGet / SQLNext / SQLGetClose | ✅ | `sql_each()` |
| SQLQuote | ✅ | `?` / `:name` parameters make it unnecessary |
| SQLite | ✅ | `sql`, `sql_write`, `tables`, `columns`, `macro_db` |
| SQLSort | ✅ partial | `sort(field, dir)`, one field only |
| SqlToHtml | ❌ | planned |
| Table | ➖ | separate functions per table |
| TotChild | ✅ | `waypoint_count` field |
| Transaction / InTransaction | ✅ | `transaction(fn)`, `in_transaction()` |
| UpdateElevation | ❌ | |
| UserFlag | ✅ | `update{user_flag=}`, `sql_write` |
| UserSort | ✅ | read `user_sort`; write `update{user_sort=}` |
| View | ❌ | |

### Date / Time

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| DateDiff | ➖ | `(t2 - t1) // 86400` with `date.parse` / `os.time` |
| DateFormat / DateToString | ✅ | `date.format(t, fmt)`, `os.date` |
| DateToSQL / SQLToDate | ✅ | `date.format(t, "%Y-%m-%d")` / `date.parse()` |
| StringToDate | ✅ | `date.parse(text, fmt)` |
| Time | ➖ | `os.date("%H:%M:%S")` |
| ValidDate | ✅ | `pcall(opensak.date.parse, s)` |

### File system

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| AppendFile / PutFile / FileOpen | ❌ | planned |
| TextOut | ❌ | planned |
| GetFile / FileRead / EndRead / StopRead | ❌ | `read_csv` for CSV only; text files planned |
| FileExists / FolderExists | ❌ | |
| Dir | ❌ | |
| FileCopy / FileErase | ❌ | |
| DriveExists | 🚫 | Windows-specific |
| ZipFile | ❌ | |

### Geocaching.com access

| GSAK | Status | Note |
|---|---|---|
| GcApi, GcApi2, GcBalance, GcFavPoints, GcGetCaches, GcGetLogs, GcGetNewToken, GcGetPQ, GcPublishFetch, GcRefresh, GcStatusCheck, GcSwapToken, GcUpdateUserInfo, GeocacherCounts | 🚫 | OpenSAK has no geocaching.com API access |

### Internet

| GSAK | Status | Note |
|---|---|---|
| GetMail / Pop3 | 🚫 | deprecated in GSAK too |
| GetUrl / PostUrl | 🚫 | the sandbox has no network access on purpose |
| Web | ❌ | planned |

### Macro language

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| If / Else / While / Repeat / Break / BreakRepeat / BeginCase / Case / Not | ➖ | Lua control flow |
| BeginSub / EndSub / GoSub / ExitSub | ➖ | `local function` / `return` |
| Declare / Option / RemoveVar / VarExists / Vsub | ➖ | Lua variables (`x = nil`, `x ~= nil`) |
| Return | ➖ | `return` at chunk level |
| Cancel | ✅ / 🚧 | `error()`; `exit()` 🚧 |
| Exit (quit GSAK) | ❌ | not needed |
| Delay | ✅ | `sleep(ms)` (max 10 s per call, 60 s per run) |
| Include | ❌ | `require` is removed |
| Macro | 🚧 | running another macro |
| MacHeader | ❌ | planned |
| NoError / Deprecated / VerCheck | ✅ | `api_version()`, `version()`, `if opensak.x then` |
| UpdateCheck | 🚫 | online version check |
| SpeedMode | ➖ | the UI refreshes when the macro ends |
| Debug | ❌ | only `print` |
| InTransaction | ✅ | `in_transaction()` |

### Mathematical / Numeric

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| Abs, ArcCos, ArcSin, ArcTan, ArcTan2, Cos, Sin, Tan, Exp, Ln, Log, Sqrt, Int, Frac, Random, DegToRad, RadToDeg | ➖ | `math.*` (`math.atan(y, x)`, `math.log(x, base)`, `math.fmod`, `math.rad/deg`) |
| ArcCosh, ArcSinh, ArcTanh, Cosh, Sinh, Tanh, Cotan | ➖ | one-line formulas; not in Lua 5.4 `math` |
| Sqr | ➖ | `x^2` |
| Round | ➖ | `string.format("%.2f", x)` / `math.floor(x * 10^n + 0.5) / 10^n` |
| FormatNumber / Str / NumToStr / Val | ➖ | `string.format`, `tostring`, `tonumber` (no thousands separator) |
| GCalc | ✅ | `coords.parse/format/distance/bearing/project/midpoint` |

### String manipulation

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| AddStr, Alltrim, Trim, Left, Right, SubStr, Len, Asc, Chr, At, AtStart, Replace, Quote, SlashAdd, Equal, IsEmpty, BoolToStr, StrToBool, Keep, Remove, Extract | ➖ | `string.*`, `table.concat`, `gsub`, `find(…, 1, true)`, `gmatch` |
| Upper / Lower | ➖ ⚠️ | ASCII letters only |
| Array / List | ➖ | Lua tables |
| `<Data>` | ➖ | long strings `[[ … ]]` |
| UTF8 / UTF16 | ➖ | strings are UTF-8; `utf8.*` |
| RegEx, RegExData, RegExSub, RegExPos, RegExReplace, RegExCount | ✅ | `re.match/find/findall/replace/split`; count = `#re.findall()` |
| RegExEscape | ❌ | |
| ROT13 | ✅ | `text.rot13` |
| EntityDecode | ✅ partial | `text.html_to_text` decodes entities |
| CsvGet | ❌ | `read_csv` for files only |
| HttpEncode | ❌ | |
| MimeEncode / MimeDecode | ❌ | |
| SmartName | ❌ | |
| Eval | 🚫 | GSAK special tags |

### User input / forms

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| YesNo | ✅ | `confirm()` |
| MsgOK / Pause | 🚧 | message box |
| Input | 🚧 | input box |
| Choose | 🚧 | choice list |
| Form / EditForm | 🚧 | forms with callbacks, visual form designer |
| ShowStatus / ShowStop | ✅ / 🚧 | `print`; progress bar with Cancel 🚧 |
| (file/folder pickers) | ✅ / 🚧 | `choose_file`; folder picker 🚧 |

### Miscellaneous

| GSAK | Status | OpenSAK Lua |
|---|---|---|
| Backup / Restore | ❌ | backup planned |
| BBcode | ❌ | |
| CacheType / ChildType | ❌ | |
| Centre | ❌ | planned |
| Clip / GetClipText | ❌ | |
| CustomFormat / RefreshCustomHtml | 🚫 | OpenSAK has no custom HTML formats |
| Dualscreen / SplitScreen | 🚫 | GSAK UI |
| GetEnvV | 🚫 | deliberately not exposed; `temp_dir`/`macros_dir` cover the common use |
| GPSInfo / GPSReceive | 🚫 | |
| GrabImages | ❌ | |
| GsCodeToNum / NumToGsCode | ❌ | |
| ImageInfo / PlayWav | 🚫 | |
| MacroSet | ➖ | options are passed as tables (`export_gpx{}`, `filter{}`) or saved settings (`export_file`) |
| MacSettings | 🚧 | saved macro settings |
| MakeGGZ | ✅ | `export_file` with a GGZ setting |
| Mtp | ✅ partial | `export_gpx{target="device"}` |
| MakeTranslation / Translate / GetSil | ❌ | |
| RGBColor | ➖ | `string.format("#%02X%02X%02X", r, g, b)` |
| RunPgm | 🚫 | deliberately not in the sandbox |
| SysInfo / SysVarPath | ✅ partial | `version`, `api_version`, `temp_dir`, `macros_dir` |
| Timer | ➖ | `os.clock()` |
| XmlToSqlite | ❌ | |

### Deprecated in GSAK

DBToSQL, DBToSQLite, SQLiteCaches → `sql()` reads the database directly · GeoCalc → `coords.*` · GetSpecial, HTML, PngToJpg, PreProcess → 🚫 · Set → Lua assignment · ShowForm → forms 🚧 · Sort → `sort()` · SQL → `sql()`.

GSAK's `$d_*` database variables map to the [cache fields](#cache-fields); custom fields and an `owned` field are missing. System variables (`$_CurrentDatabase` → `database()`, `$_Count` → `count()`) were not checked one by one.
"""
