# Distance Computation — Architecture

---

## Problem with the legacy approach

Before this flag, distance was recomputed on **every `_refresh_cache_list()` call** — which fires on every filter change, sort change, search keystroke, import, and dialog close. For a database with tens of thousands of caches this meant running the same Haversine batch dozens of times per session, even when neither the cache coordinates nor the centre point had changed.

There was also a secondary inefficiency: when the sort column was `distance`, `apply_filters()` computed it a second time in a per-row scalar loop independently of the vectorised batch already computed by the table model.

---

## New design (flag ON)

### One write per centre-point change, zero writes per refresh

```
user changes centre point
  └─ _on_home_changed()
       ├─ recalculate_distances(lat, lon)   ← one batch DB write
       │    └─ distance_km_batch()          ← Haversine or Vincenty
       └─ _refresh_cache_list()
            └─ apply_filters()              ← ORDER BY caches.distance (SQL)
                 └─ load_caches()
                      └─ _update_distances()  ← reads cache.distance from object
```

`recalculate_distances()` in `db/database.py`:
1. Loads `(id, latitude, longitude)` for all caches in one SQL query.
2. Computes distances and bearings in a single numpy-vectorised batch.
3. Writes back via `executemany` — one `UPDATE caches SET distance=:d, bearing=:b WHERE id=:id` across all rows.
4. Returns the number of rows updated.

### DB column

`Cache.distance` (Float, nullable) and `Cache.bearing` (Float, nullable) already exist in the schema (added in migration 4, issue #33). No new migration is needed. Values are `NULL` until the first `recalculate_distances()` call; the table model falls back to `99999.0` for sort when `NULL`.

### SQL-level sort

When the flag is ON, `_sql_order_expr("distance")` returns `COALESCE(caches.distance, 99999.0)`, so `apply_filters()` can push `ORDER BY distance` into SQLite instead of sorting in Python after loading all rows.

### Table model

`CacheTableModel._update_distances()` dispatches on the flag:

`CacheTableModel._update_distances()` reads `cache.distance` / `cache.bearing` from the already-loaded ORM objects — no recomputation on refresh.

---

## Calculation methods

Setting: `AppSettings.distance_method` — `"haversine"` (default) or `"vincenty"`.  
Stored in `computation.distance_method` (JSON settings store).

### Haversine (default)

Treats the Earth as a sphere with radius R = 6371.0 km (IUGG mean radius, equivalent to ~3958.8 miles). Matches Geocaching.com's current approach.

```python
_haversine_km(lat1, lon1, lat2, lon2)   # scalar
haversine_km_batch(lat0, lon0, lats, lons)  # numpy-vectorised
```

### Vincenty WGS84

Uses the WGS84 oblate spheroid (a = 6378.137 km, f = 1/298.257223563). Iterates to convergence (typically 3–5 iterations for geocaching distances), falling back to Haversine on antipodal non-convergence. Accuracy improvement is up to ~0.3 % for long distances.

```python
_vincenty_km(lat1, lon1, lat2, lon2)    # scalar (iterative)
vincenty_km_batch(lat0, lon0, lats, lons)  # Python loop (no numpy)
```

Vincenty does not vectorise cleanly (it is iterative), so the batch form is a plain Python loop. This is acceptable because the batch only runs on centre-point change, not on every refresh.

### Dispatcher

```python
distance_km(lat1, lon1, lat2, lon2)         # scalar — reads flag + setting
distance_km_batch(lat0, lon0, lats, lons)   # batch  — reads flag + setting
```

Both read `AppSettings.distance_method` and dispatch accordingly.

---

## File map

| File | What changed |
|------|-------------|
| `src/opensak/filters/engine.py` | Added `_vincenty_km`, `vincenty_km_batch`, `distance_km`, `distance_km_batch`; updated `_sql_order_expr` and `apply_filters` to use DB column |
| `src/opensak/db/database.py` | Added `recalculate_distances(lat, lon) → int` |
| `src/opensak/gui/settings.py` | Added `AppSettings.distance_method` property |
| `src/opensak/gui/mainwindow.py` | `_on_home_changed()` and `_initial_load()` call `recalculate_distances()` |
| `src/opensak/gui/cache_table.py` | `_update_distances()` reads from ORM column |
| `src/opensak/gui/dialogs/settings_dialog.py` | Added "Distance Calculation" group in Advanced tab |
| `src/opensak/lang/en.py` + all others | Added `settings_group_distance`, `settings_distance_method_label`, `settings_distance_haversine`, `settings_distance_vincenty`, `settings_distance_hint` |
| `tests/unit-tests/test_distance.py` | Added Vincenty tests, dispatcher tests, `recalculate_distances()` tests |
| `tests/unit-tests/test_flags.py` | Added `TestDistanceComputation` class; updated default-flags assertions |

---

## Background: why not store distance during GPX import?

Distance is relative to the user's active centre point, which can change at any time. Storing it during import would produce a value that becomes stale the moment the user switches centre point. The GSAK approach (store + recalculate on centre point change) is the correct design: one write per user action rather than one recompute per table refresh.

---

## Testing

```bash
# Unit tests (all methods + DB population)
pytest tests/unit-tests/test_distance.py tests/unit-tests/test_flags.py -v
```
