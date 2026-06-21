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

The filter dialog is split across five tabs:

| Tab | What's on it |
|---|---|
| **General** | Cache type, container, D/T, found status, availability, distance, premium, trackables, corrected coordinates |
| **Dates** | Hidden date, found by me date, DNF date, last log date |
| **Other** | Country / State / County, user flag, DNF, FTF, favourite points |
| **Attributes** | ~70 standard Groundspeak attributes |
| **WHERE** | Raw SQL WHERE clause for advanced filtering |

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

## WHERE clause filter

The **WHERE** tab lets you enter a raw SQL `WHERE` clause that is applied directly against the cache database. This is intended for advanced users who need conditions not covered by the other filters.

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
