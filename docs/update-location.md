# Update Waypoint Locations

OpenSAK can automatically fill in the county, state, and country fields for your waypoints using reverse geocoding. The lookup runs offline against boundary polygon data from [OpenSAK-Data](https://github.com/OpenSAK-Org/OpenSAK-Data).

---

## Opening the dialog

There are two ways to open the dialog, and both open the same window. The difference is the default scope selection:

- **Waypoint menu → Update Waypoint Locations…** — opens with *Only waypoints with missing location data* selected by default.
- **Right-click a waypoint → Update location data…** — opens with *Only this waypoint* selected by default.

This option is only visible when the `reverse-geocoding` feature flag is enabled. See [Feature Flags](feature-flags.md) for details.

---

## Scope section

Controls which waypoints are processed:

| Option | Behaviour |
|---|---|
| **Only this waypoint** | Processes the single waypoint that was right-clicked. Available only when opened via the context menu. |
| **Only waypoints with missing location data** | Skips waypoints that already have all three fields filled in. Recommended for bulk runs. |
| **Update all waypoints** | Overwrites existing values for every waypoint in the database. |

---

## Lookup options section

Controls how the lookup behaves:

**Use corrected coordinates when available** — off by default, so waypoints are looked up at their posted (listing) coordinates. Tick it to use a corrected final location instead, where one is set — for example for a mystery or multi-cache whose final is in another county.

---

## Offline lookup

The lookup finds the country, state and county whose boundary polygon contains each waypoint. It needs no network connection and has no rate limits. Country and state boundaries are included with OpenSAK; county boundaries come in packs per country, and a pack that is missing is downloaded the first time a waypoint needs it.

- **Waypoint → Download boundary packs…** downloads every county pack at once, for full offline coverage.
- **Waypoint → Check for boundary data updates…** fetches a newer version of the boundary data when there is one.

A waypoint whose county isn't covered by the boundary data keeps an empty county field.

> Earlier versions had an optional online refinement through OpenStreetMap's Nominatim service. It was removed when the boundary data replaced the old nearest-place lookup, and the setting for it is gone too (#1208). A macro can do an online lookup for waypoints the boundary data doesn't cover.

---

## Auto-geocode on import

When importing a GPX or PQ zip file, OpenSAK automatically runs the offline lookup for any waypoints that are missing location data immediately after a successful import — no extra step needed.
