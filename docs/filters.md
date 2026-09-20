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

## Filter tabs

The filter dialog is split across nine tabs:

| Tab | What's on it |
|---|---|
| **General** | Cache type, container, D/T, found status, availability, distance, premium, trackables, corrected coordinates |
| **Dates** | Hidden date, found by me date, DNF date, last log date |
| **Other** | Country / State / County, user flag, DNF, FTF, favourite points, locked |
| **Logs** | Caches by their logs — log date, log type, who logged, how many |
| **Line/Polygon** | Caches along a route, inside an area, or near a list of points |
| **Child Waypoints** | Caches by their child waypoints — code, type, date, name, comment, created by user, count |
| **Attributes** | ~70 standard Groundspeak attributes |
| **Text Search** | Full-text search across description, logs, notes, and (optionally) hint |
| **Where** | Raw SQL WHERE clause for advanced filtering |

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

Show only caches within a certain radius of your active home point. The unit (km or mi) follows your preference set in Settings.

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

### Favourite points

Filter by a minimum and/or maximum favourite point count. Available on the **Other** tab.

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

The **Text Search** tab searches free-text fields for a word or phrase, rather than an exact match like the *Name* or *GC code* filters.

| Field | Searched by default |
|---|---|
| Description | ✓ |
| Logs | ✓ |
| Personal notes | ✓ |
| Hint | ✗ (off — enable it explicitly if you want hint text included) |

The search uses SQL `LIKE` pushdown rather than loading every cache into Python, so it stays fast even on large databases.

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
