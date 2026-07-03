# Update Waypoint Locations

OpenSAK can automatically fill in the county, state, and country fields for your waypoints using reverse geocoding. It always starts with a fast offline lookup, and optionally follows up with an online refinement for higher accuracy.

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

**Use corrected coordinates when available** — when checked (the default), waypoints with a corrected final location are looked up using those coordinates instead of the original listing coordinates. This is the correct behaviour for mystery and multi-caches where the final location differs from the published coordinates.

**Also use online lookup for higher accuracy** — when checked, an online refinement pass runs after the offline lookup. The initial state of this checkbox is controlled by **Settings → Advanced → Location refinement**; you can always override it per-run.

---

## Offline lookup

The offline lookup always runs first. Location data comes from [GeoNames](https://www.geonames.org/), a curated global geographic database bundled with the app. It resolves any number of coordinates instantly — no network connection required, no rate limits.

**Known limitations:**

- The lookup is point-based (nearest known locality), not polygon-based. Waypoints on or very close to an administrative boundary may resolve to the wrong side.
- GeoNames data is periodically updated but may not reflect very recent political changes (e.g. county redefinitions).
- Virginia (USA) has independent cities that are legally separate from the surrounding county. The offline lookup may assign the surrounding county instead of the correct independent city.

---

## Online refinement (opt-in)

When **Also use online lookup for higher accuracy** is checked, an additional pass runs after the offline lookup using the [Nominatim](https://nominatim.org/) reverse geocoding API (OpenStreetMap polygon data). This provides higher-accuracy results — especially for county — because it uses actual administrative boundary polygons rather than nearest-point matching.

**Important notes:**

- Requires an internet connection.
- Rate-limited to **1 request per second** per Nominatim's usage policy. For large databases this can take a long time (e.g. ~3 hours for 10 000 waypoints).
- Offline results are always written first. The online pass only overwrites a field if it returns a non-empty value — offline data is never erased by a failed or empty response.
- Progress and estimated time remaining are shown in the dialog. You can cancel at any time and keep whatever has been refined so far.

### Setting the default

The online checkbox is **unchecked by default**. To have it checked by default for every run, go to **Settings → Advanced → Location refinement** and tick **Enable online lookup for higher accuracy**.

---

## Auto-geocode on import

When importing a GPX or PQ zip file, OpenSAK automatically runs the offline lookup for any waypoints that are missing location data immediately after a successful import — no extra step needed.

The online refinement is **never triggered automatically on import**. To run it, open the dialog manually via **Waypoint → Update Waypoint Locations…** after the import.
