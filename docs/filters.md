# Filter Reference

OpenSAK's filter engine lets you narrow your cache list to exactly what you want. Filters combine with AND or OR logic and can be saved as named profiles for reuse.

---

## Opening the Filter Dialog

Click **View → Set filter…** or press `Ctrl+F`.

---

## AND vs OR logic

By default all active filters are combined with **AND** — a cache must pass every filter to appear in the list.

Switch the mode to **OR** to show caches that pass *at least one* filter. This is useful for broad searches, e.g. "Traditional OR Multi-cache".

Filters can also be **nested**: an outer AND group can contain an inner OR group, letting you express complex conditions like "not found AND (difficulty ≤ 2 OR terrain ≤ 2)".

---

## Invert filter

Tick **Invert filter** (next to the Reset buttons) to flip the whole filter:
the list then shows exactly the caches the filter would otherwise hide. For
example, "Traditional Cache, difficulty ≤ 2" inverted shows every cache that
is *not* an easy Traditional.

The checkbox is highlighted while it is on, is saved with a filter profile, and
is cleared by **Reset all** (but not by **Reset tab**, since it belongs to no
tab). With no filter conditions set, inverting has no effect — all caches are
shown.

---

## Filter tabs

The filter dialog is split across ten tabs:

| Tab | What's on it |
|---|---|
| **General** | Cache type, container, D/T, found status, availability, premium, trackables, corrected coordinates |
| **Dates** | Hidden date, found by me date, DNF date, last log date |
| **Other** | Country / State / County, user data 1–4, GC.com note, personal note, user flag, DNF, FTF, favourite points, elevation, locked — plus one block with a shared **centre point** for distance, direction, and distance corrected ↔ posted |
| **Logs** | Caches by their logs — log date, log type, who logged, how many |
| **Line/Polygon** | Caches along a route, inside an area, or near a list of points |
| **Child Waypoints** | Caches by their child waypoints — code, type, date, name, comment, created by user, count |
| **Trackables** | Caches by their trackables — name, tracking code, count |
| **Attributes** | ~70 standard Groundspeak attributes |
| **Text Search** | Full-text search across description, logs, notes, and (optionally) hint |
| **Where** | Raw SQL WHERE clause for advanced filtering |

---

## Seeing what a filter actually does

Every filter element that differs from its default is highlighted in yellow,
and so is any tab that holds one. Open a saved filter and you can tell at a
glance what it sets — a narrowed difficulty range highlights the **Difficulty**
label, an enabled distance limit highlights the **Distance from centre point**
group, an attribute set to Yes or No highlights that attribute's row, and the
**General**, **Attributes** or any other affected tab is highlighted in the tab
bar. Anything left at its default stays unhighlighted, so what you see marked
is exactly what the filter restricts.

**Reset all** and **Reset tab** clear the highlighting along with the values.

---

## Filter types

### Cache type

Show only specific cache types. Select one or more from the list.

| Value |
|---|
| Traditional Cache |
| Multi-cache |
| Mystery/Unknown Cache |
| EarthCache |
| Letterbox Hybrid |
| Event Cache |
| CITO Event |
| Mega-Event Cache |
| Wherigo Cache |
| Virtual Cache |
| Webcam Cache |

Use **Enable all / Disable all** to quickly select or deselect every type at once.

---

### Container size

Filter by physical container size.

| Value |
|---|
| Nano |
| Micro |
| Small |
| Regular |
| Large |
| Very Large |
| Other |
| Not chosen |
| Virtual |

---

### Difficulty

Show caches within a difficulty range. Values run from **1.0** (easiest) to **5.0** (hardest) in 0.5 steps.

Example: Difficulty 1.0–2.0 shows only easy caches.

Caches with no difficulty set always pass this filter.

---

### Terrain

Show caches within a terrain range. Same 1.0–5.0 scale as difficulty.

---

### Found / Not found

| Filter | Shows |
|---|---|
| Found | Only caches you have marked as found |
| Not found | Only caches you have not found |

---

### Availability

Control which active/inactive states appear:

| Option | What it includes |
|---|---|
| Available | Caches that are active and available |
| Unavailable | Caches temporarily disabled by the owner |
| Archived | Caches permanently archived |

All three can be toggled independently. Default: available only.

---

### Distance

Filter on the distance from a centre point — your home point, a saved point, the selected cache or a custom coordinate. The centre point is chosen once, at the top of the centre-point block on the **Other** tab, and is shared with the Direction filter. Check **Enable**, pick an operator — *Equal*, *Less than*, *At most*, *More than*, *At least*, *Between (inclusive)* or *Not between* — and enter the distance. The unit (km or mi) follows your preference set in Settings. *Equal* matches within ±5 m. For *Between* / *Not between* the two values may be entered in either order; the smaller one is moved to the first box when the filter is applied.

Examples: *At most 10 km* is the classic radius; *Between 5 and 20 km* skips the caches right around you; *More than 100 km* finds caches far from home.

Filter profiles saved with the older *Min* / *Max* fields load as *At most Max*, or *Between Min and Max* when a minimum was set — the same caches as before.

---

### Name

Show caches whose name contains a given text string (case-insensitive, partial match).

Example: `bridge` matches "Old Bridge Cache" and "Bridgetown Mystery".

---

### GC code

Show caches whose GC code contains a given text string (case-insensitive).

Example: `GC1A` matches GC1A2B3 and GC1A999.

---

### Placed by

Show caches placed by owners whose name contains a given text (case-insensitive). This matches the `placed_by` field from the GPX file.

---

### Owner name

Show caches whose current owner name contains a given text (case-insensitive). This matches the `owner` field, which reflects adopted caches correctly — use this instead of *Placed by* when filtering by the person who currently owns the cache.

---

### Country / State / County

Text contains search (case-insensitive) applied to the country, state, or county fields. Available on the **Other** tab.

---

### Attribute

Show caches that have a specific Groundspeak attribute set. You can filter for attributes that are present (e.g. "Dogs allowed: yes") or explicitly absent ("Dogs allowed: no").

The filter dialog shows the ~70 standard Groundspeak attributes on the **Attributes** tab.

---

### Child waypoints

Filter caches by their child waypoints (parking, stages, final, …). Available on the **Child Waypoints** tab.

| Field | Matches |
|---|---|
| Code | The waypoint code (GSAK imports), or the two-letter prefix for GPX imports — same text operators as Name |
| Type | Waypoint type, e.g. `Parking Area`, `Physical Stage` |
| Date | Waypoint date — same operators as the **Dates** tab, except comparing with another date |
| Name / Comment | Waypoint name and comment |
| Created by user | Yes = only waypoints you added yourself, No = only imported ones |
| Count | Any, Equal, At least, At most, or Between |

All criteria must hold for the **same** waypoint. Count is the number of waypoints that meet them: with **Any**, a cache needs at least one; **Equal 0** finds caches with none — e.g. Type contains `Parking` and Count equal 0 shows caches without a parking waypoint. Count alone filters on the total number of waypoints.

---

### Logs

Filter caches by the logs on them, on the **Logs** tab. It mirrors GSAK's Logs tab and is read top to bottom in three steps.

**1. Which logs are searched**

| Setting | Effect |
|---|---|
| Logs to search | *All logs*, or only each cache's *N* most recent ones (Latest, Last 2 … Last 100) |
| Include / exclude | Whether the caches that match are kept or dropped |

The window counts **every** log the cache has, not just the ones the criteria below look for. So *Logs to search: Last 2* with **Not found** ticked means "a DNF among the cache's two most recent logs" — a cache whose only DNF sits under three newer finds does *not* match.

**2. What a log has to be**

| Field | Matches |
|---|---|
| Found / Not found / Other | The kind of log. *Found* covers Found it, Attended and Webcam Photo Taken; *Not found* covers Didn't find it; *Other* is everything else |
| Log date | The log's date — the same operators as the **Dates** tab, except comparing with another date |
| Log types | The ticked types. Untick **All** to choose individual ones; `"Other"` matches any type not in the list |
| Logged by | The log's finder — the same text operators as *Name*. Tick **Match the user ID** to compare the numeric user ID instead of the display name |

All of these must hold for the **same** log. Leaving **Logged by** empty matches a log by *anyone* — to find your own logs, type your geocaching name there.

**3. How many such logs**

**Required count** is the number of logs that met the criteria: *At least one log*, *At most*, *At least*, *Equal* or *Between*. Together with **Exclude** this is what makes negative conditions expressible:

| To find | Set |
|---|---|
| Caches with no find in the last year | Log types = Found it, Log date During 1 years, Exclude |
| Caches whose most recent log is a DNF | Logs to search = Latest, Not found only |
| Caches you have never logged yourself | Logged by = equals *your name*, Exclude |
| Caches with at least 5 favourite-worthy finds | Log types = Found it, Required count At least 5 |
| Caches with a recent maintenance request | Logs to search = Last 5, Log types = Needs Maintenance |
| Caches you have DNFed in their last 2 logs | Logs to search = Last 2, Not found only, Logged by equals *your name* |

---

### Has trackable

Show only caches that currently have at least one trackable logged as in the cache.

---

### Trackables

Filter caches by the trackables (travel bugs, geocoins) in them. Available on the **Trackables** tab.

| Field | Matches |
|---|---|
| Name | Trackable name — same text operators as Name |
| Tracking code | The trackable's tracking code — same text operators as Name |
| Count | Any, Equal, At least, At most, or Between |

Both text criteria must hold for the **same** trackable. Count is the number of trackables that meet them: with **Any**, a cache needs at least one; **Equal 0** finds caches with none — e.g. Name contains `coin` and Count equal 0 shows caches without a geocoin. Count alone filters on the total number of trackables, e.g. **At least 3**.

---

### Premium / Non-premium

| Filter | Shows |
|---|---|
| Premium | Caches that require a premium Geocaching.com membership |
| Non-premium | Caches that are free to access without a premium membership |

---

### Corrected coordinates

| Filter | Shows |
|---|---|
| Has corrected | Only caches where you have stored corrected (puzzle-solved) coordinates |
| No corrected | Only caches without corrected coordinates |

**Distance corrected ↔ posted** — check **Enable** to filter on how far a cache's corrected coordinates lie from its posted coordinates. Pick an operator — *Equal*, *Less than*, *At most*, *More than*, *At least*, *Between (inclusive)* or *Not between* — and enter the distance in metres (feet when miles are selected in Settings). *Equal* compares to the whole metre. As with the centre-point distance, a reversed *Between* range is put in order when applied. Caches without corrected coordinates never match. Available in the centre-point block on the **Other** tab.

Examples: *More than 3219 m* finds solved finals outside the 2-mile rule (often a typo); *Equal 0 m* finds caches whose corrected coordinates are just the posted ones.

---

### Direction

Show only caches lying in a given direction as seen from the centre point (see *Distance* above). Available in the centre-point block on the **Other** tab. There are two ways to set it:

- **Degrees** — check **Enable**, pick an operator — *Equal*, *Less than*, *At most*, *More than*, *At least*, *Between (inclusive)* or *Not between* — and enter the bearing: 0° = north, 90° = east, clockwise. *Between* runs clockwise, so 315° – 45° is the sector through north.
- **Compass** — click the directions (N, NE, E, SE, S, SW, W, NW) on the compass rose. Each covers a 45° sector centred on it (N = 337.5°–22.5°), and clicking fills in the matching degree range. Directions that aren't next to each other can't be written as one range; the degree values are then greyed out. Changing the degree values again replaces the clicked directions.

The ⓘ button next to the filter explains both modes. Caches without coordinates never match.

Direction filters saved in earlier versions keep matching exactly as before — measured from your active home point, the same bearing as the **Bearing** column — until you edit them.

---

### User Flag

Filter on whether the user flag is set or not. Available on the **Other** tab.

---

### DNF

Filter on Did Not Find status. Available on the **Other** tab.

---

### FTF (First to Find)

Filter by First to Find status. Available on the **Other** tab.

---

### Personal note

| Filter | Shows |
|---|---|
Filter on the text of your personal note (the note on the cache's **Notes** tab), with the same text operators as *Name*, all case-insensitive. Available on the **Other** tab.

Surrounding whitespace is ignored, so a note containing only whitespace counts as empty. Use *is empty* / *is not empty* for the old yes/no check — filter profiles saved with the former **Yes** / **No** checkboxes load as exactly that. GC.com's synced personal cache note is not considered; it has its own filter, below.

---

### User data 1–4 / GC.com note

Text filters on GSAK's four **User data** fields and on the **GC.com note** (the personal cache note synced from geocaching.com), with the same text operators as *Name*, all case-insensitive. Available on the **Other** tab.

Example: *User data 1* equals `solved` finds the mysteries you've marked as solved in GSAK.

---

### Elevation

Check **Enable**, pick an operator — *Equal*, *Less than*, *At most*, *More than*, *At least*, *Between (inclusive)* or *Not between* — and enter the elevation. The unit is metres, or feet when miles are selected in Settings. *Equal* compares to the whole metre. A reversed *Between* range is put in order when applied. Caches whose elevation is unknown never match. Available on the **Other** tab.

---

### Favourite points

Check **Enable**, pick an operator — *Equal*, *Less than*, *At most*, *More than*, *At least*, *Between (inclusive)* or *Not between* — and enter the favourite point count. Caches without favourite points count as 0. A reversed *Between* range is put in order when applied. Available on the **Other** tab.

Filter profiles saved with the older from/to range for elevation or favourite points are converted when loaded: a range open at the top (e.g. favourite points 10 – 9999) becomes *At least*, one open at the bottom becomes *At most*, a single value becomes *Equal*, and anything else *Between*. Saving the profile again stores the new form.

---

### Locked

| Filter | Shows |
|---|---|
| Yes | Only caches that are locked against import overwrites |
| No | Only caches that are not locked |

Both are checked by default, so the filter has no effect until you uncheck one. Available on the **Other** tab. Lock or unlock a cache via its right-click menu, or the checkbox in **Edit cache…**

---

## Date filters

All date filters are on the **Dates** tab. Each can have an optional from-date, an optional to-date, or both.

### Hidden date

Filter by the date the cache was placed (hidden).

### Found by me date

Filter by the date you personally found the cache.

### DNF date

Filter by the date a Did Not Find was recorded.

### Last log date

Filter by the date of the most recent log entry for the cache.

---

## Line / polygon filter

The **Line/Polygon** tab works like GSAK's filter of the same name. Enter one point per line in the text box:

```text
53.18346, 8.71113
N 53 23.613, E 008 00.941
W,GC12345
```

- Any coordinate format OpenSAK understands works (decimal degrees, DMM, DMS), with or without a comma between latitude and longitude.
- `W,<code>` takes the coordinates of a cache (its corrected coordinates when set) or a waypoint in the current database.
- Text after `#` is ignored, so you can annotate the list.
- **Add flagged (user flag)** appends a `W,<code>` line for every cache with the user flag set.
- **Read points from file** loads a GPX file (track points, else route points, else waypoints), a KML file, or a text file in the format above — replacing or appending to the list.

Choose the filter type:

| Type | Includes caches… | Needs |
|---|---|---|
| Line | within the distance of the line through the points (a route or track) | 2+ points and a distance |
| Polygon | inside the area the points outline (closed automatically); a distance above 0 also includes caches that close to the outline | 3+ points |
| Points | within the distance of any single point | 1+ point and a distance |

Check **Exclude** to invert the filter and keep only the caches that do *not* match. The filter uses a cache's corrected coordinates when set. Distances are measured along the Earth's surface; polygon edges are straight lines in latitude/longitude. Shapes that cross the ±180° meridian are not supported.

---

## Text search filter

The **Text Search** tab searches free-text fields for a word, phrase or pattern. It has the same operators as *Name* (contains, equals, starts/ends with, in list, empty, regex, and their negations), all case-insensitive.

A positive operator matches when **any** of the searched fields — or any single log — matches. A negated operator (*does not contain*, *not regex*, …) and *is empty* match when **none** of them does, so *does not contain* `spoiler` keeps caches that mention "spoiler" in none of the ticked fields.

| Field | Searched by default |
|---|---|
| Description | ✓ |
| Logs | ✓ |
| Personal notes | ✓ |
| Hint | ✗ (off — enable it explicitly if you want hint text included) |

The search uses SQL `LIKE` pushdown rather than loading every cache into Python, so it stays fast even on large databases. Regex searches can't be pushed to SQL and are checked cache by cache, so they are slower — especially with *Logs* ticked.

---

## Where clause filter

The **Where** tab lets you enter a raw SQL `WHERE` clause that is applied directly against the cache database. This is intended for advanced users who need conditions not covered by the other filters.

Example:
```sql
difficulty > 3 AND terrain > 3
```

The clause is combined with AND alongside any other active filters.

---

## Saving a filter profile

Once you have set up a useful combination, save it so you can reload it in one click:

1. Configure your filters in the filter dialog
2. Click **Save profile**
3. Give it a name (e.g. "Easy day trip" or "Local tradis")
4. Reload it any time from the filter dialog's profile list or the toolbar dropdown

---

## Importing GSAK's saved filters

**File → Import GSAK Filters…** turns the filters you saved in GSAK into OpenSAK filter profiles. It is the companion of *Import from GSAK Database*: that one imports caches out of a cache database (`sqlite.db3`), this one imports filters out of GSAK's settings database (`gsak.db3`, normally `%AppData%\GSAK\gsak.db3`). A GSAK backup `.zip` works too — the `gsak.db3` inside it is found automatically.

Pick the file, tick the filters you want (all of them are ticked to begin with; the search box narrows the list, and **Select all / Select none** apply to what the search currently shows), and press **Start import**. Existing profiles of the same name are kept and reported as skipped unless you tick **Overwrite filter profiles that already exist**.

### Where each GSAK condition ends up

Every condition in a GSAK filter lands in one of three places:

| | What it means | Counts as migrated |
|---|---|---|
| **A filter** | The condition exists in the tabs above — cache types, D/T, dates, logs, child waypoints, polygons, attributes, text fields with all their operators | yes |
| **SQL in the Where tab** | OpenSAK stores the data but has no filter row for it — the watch list, elevation, bearing, user data 1–4, compass quadrants, TB/coin names | yes |
| **A comment in the Where tab** | Nothing in OpenSAK can express it | no |

The third case is why the Where tab of an imported filter often opens with a block of `--` lines. They do nothing; they are there so you can see exactly what GSAK filtered on and rebuild it yourself. A typical one looks like this:

```sql
-- NOT MIGRATED from the GSAK filter "Ideas_CH" (2 condition(s)).
-- Rebuild these by hand, then delete the comment.
--   User-defined GSAK column "Niggae_Ignore": OpenSAK stores no custom columns
--     (only user_data_1-4) … The GSAK criterion was: Niggae_Ignore;bool;…
--   Cache types "Waymark": GSAK has these cache types but OpenSAK does not …
-- Watch list
(coalesce(watch, 0) = 1)
```

The comments always come first and the executable SQL last, so the clause stays valid whatever you delete. Where a whole GSAK `WHERE` clause could not be translated, the translation-so-far is included in the comment, ready to be fixed up and uncommented — it is deliberately never left live, because SQL that fails to run would silently make the filter match nothing.

The things that cannot be migrated are, in practice: GSAK's user-defined columns (OpenSAK has no custom columns, and the cache import does not carry them over), the Waymark and L&F Celebration cache types (the cache import files them under *Unknown Cache*, where nothing tells them apart), GSAK macro variables inside a saved `WHERE` clause, and columns OpenSAK does not store at all (FavPerc, LabId, the GPX symbol name).

### The statistics after an import

The results panel ends with a migration-coverage figure:

```
Migration coverage
  GSAK conditions                  445
  … migrated to filters            286
  … migrated to Where SQL           63
  … left as SQL comments            96

  Coverage                      78.4 %
    fully migrated (100 %)          54
    partly migrated                 61
    nothing migrated (0 %)           7
```

Coverage is counted over **conditions, not filters** — 100 % means every condition from every imported filter runs, 0 % means all of it sits in comments — so one filter with twenty conditions weighs more than one with two. Filters that came through with something left in comments are listed underneath, lowest coverage first, so you know which ones to open and finish.

---

## Clearing filters

Click **View → Clear filter** or use the clear button (shown in red when active) in the toolbar to remove all active filters and show the full cache list.

---

## Common filter recipes

| Goal | Filters to combine |
|---|---|
| Unfound traditional caches within 10 km | Not found + Type = Traditional + Distance ≤ 10 km |
| Easy caches for a family trip | Difficulty ≤ 2 + Terrain ≤ 2 + Available |
| Caches with parking nearby | Attribute: Parking available = yes |
| All unfound caches, including archived | Not found + Availability: available + unavailable + archived |
| Caches by a specific owner | Owner name = [owner name] |
| Mystery caches you have not solved yet | Type = Mystery + Not found |
| Only unsolved puzzles | Type = Mystery + No corrected coordinates |
| FTF caches | FTF = Yes |
| Caches mentioning a specific trail or POI | Text search: [name], searching Description + Logs |
| Caches you've protected from re-import overwrites | Locked = Yes |
