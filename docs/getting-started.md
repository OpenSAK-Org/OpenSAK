# Getting Started with OpenSAK

**OpenSAK** (Open Source Swiss Army Knife) is a free, open-source geocache management tool for Windows, Linux, and macOS. It lets you import, organise, filter, and navigate your geocache collection — all offline, without needing a Geocaching.com subscription beyond your normal account.

> **Coming from GSAK?** OpenSAK uses the same GPX/Pocket Query workflow you already know. Jump to [Importing Your Caches](#3-importing-your-caches) to get started quickly.

---

## Table of Contents

1. [Installation](#1-installation)
2. [First Launch](#2-first-launch)
3. [Importing Your Caches](#3-importing-your-caches)
4. [Understanding the Interface](#4-understanding-the-interface)
5. [Filtering Your Cache List](#5-filtering-your-cache-list)
6. [Cache Details and Hints](#6-cache-details-and-hints)
7. [Waypoints](#7-waypoints)
8. [Marking Caches as Found](#8-marking-caches-as-found)
9. [Updating Finds from "My Finds"](#9-updating-finds-from-my-finds)
10. [Exporting to GPS](#10-exporting-to-gps)
11. [Multiple Databases](#11-multiple-databases)
12. [Changing the Language](#12-changing-the-language)
13. [Getting Help](#13-getting-help)

---

## 1. Installation

### Windows
1. Download the latest `.exe` installer from the [Releases page](https://github.com/AgreeDK/opensak/releases)
2. Run the installer — Windows SmartScreen may warn you the first time; click **More info → Run anyway**
3. OpenSAK will appear in your Start menu

### Linux
1. Download the latest `.AppImage` from the [Releases page](https://github.com/AgreeDK/opensak/releases)
2. Make it executable:
   ```bash
   chmod +x OpenSAK-*.AppImage
   ```
3. Run it:
   ```bash
   ./OpenSAK-*.AppImage
   ```

### macOS
1. Download the latest `.dmg` from the [Releases page](https://github.com/AgreeDK/opensak/releases)
2. Open the `.dmg` and drag OpenSAK to your Applications folder
3. On first launch, right-click the app and choose **Open** to bypass Gatekeeper

> **Note:** If you see a warning about an unidentified developer, this is expected for a community project without a paid Apple certificate. The source code is fully open at [github.com/AgreeDK/opensak](https://github.com/AgreeDK/opensak).

---

## 2. First Launch

When you open OpenSAK for the first time, you will see an empty three-panel layout:

- **Left/Top panel** — your cache list (empty until you import)
- **Bottom-left panel** — cache details
- **Bottom-right panel** — map

Before importing, it is a good idea to set your **home coordinates**. This is used as the centre point for distance calculations.

1. Go to **Settings → Settings…**
2. Enter your home coordinates (decimal degrees, e.g. `55.6761, 12.5683`)
3. Click **Save**

---

## 3. Importing Your Caches

OpenSAK works with standard **GPX files** and **Pocket Query ZIP files** — the same files you already download from Geocaching.com.

### Downloading a Pocket Query (recommended)
1. Log in to [geocaching.com](https://www.geocaching.com)
2. Go to **Play → Pocket Queries**
3. Create or run an existing Pocket Query
4. Download the `.zip` file — do **not** unzip it

### Importing into OpenSAK
1. Click **File → Import** (or press `Ctrl+I`)
2. Select your `.zip` or `.gpx` file
3. Click **Open** — OpenSAK will import all caches and their logs

> **Tip:** You can import multiple files into the same database. Duplicate caches are updated automatically, so you can re-import an updated Pocket Query without creating duplicates.

> **Auto-geocoding:** After a successful import, OpenSAK automatically runs an offline lookup to fill in the county, state, and country for any waypoints that are missing that data. No extra step needed. For higher-accuracy results you can run an optional online refinement afterwards — see [Waypoints](#7-waypoints).

### Coming from GSAK
OpenSAK uses the same GPX/PQ format as GSAK. Simply export or download your Pocket Queries as usual and import them into OpenSAK. Your existing GSAK databases cannot be opened directly, but re-importing your Pocket Queries takes only a few minutes.

---

## 4. Understanding the Interface

OpenSAK uses a three-panel layout:

```
┌─────────────────────────────────────────────┐
│             Cache List (top)                │
│  GC Code │ Name │ Type │ D/T │ Distance ... │
├───────────────────────┬─────────────────────┤
│   Cache Details       │       Map           │
│   (bottom-left)       │   (bottom-right)    │
└───────────────────────┴─────────────────────┘
```

### Cache List
- Click any column header to sort
- Right-click a cache for quick actions: **Open on geocaching.com**, **Copy coordinates**, **Mark as found**
- Choose which columns to show via **View → Columns**

### Cache Details
Shows the full description, hint (click to decode ROT13), attributes, and logs for the selected cache.

- Click the **GC code** to open the cache page in your browser
- Click the **coordinates** to open them in your preferred map app (Google Maps or OpenStreetMap — set in Settings)

### Map
Shows all visible caches as colour-coded pins:
- 🟢 **Green** — Traditional cache
- 🔵 **Blue** — Multi-cache
- 🟡 **Yellow** — Mystery/Unknown
- ⚫ **Grey** — Found by you

Click any pin to highlight that cache in the list and show its details.

---

## 5. Filtering Your Cache List

Filters let you narrow down the cache list to exactly what you want to see. The filter dialog has five tabs (General, Dates, Other, Attributes, WHERE) covering cache type, D/T, distance, dates, location, attributes, and more, all combinable with AND/OR logic.

### Opening the Filter Dialog
Click **View → Set filter…** (or press `Ctrl+F`).

### Common filter examples

| Goal | Filter to use |
|---|---|
| Only unfound caches | Found = No |
| Difficulty 1–2 only | Difficulty ≤ 2 |
| Within 5 km of home | Distance ≤ 5 km |
| Traditional caches only | Cache type = Traditional |
| Caches with parking nearby | Attributes includes Parking |
| Not yet attempted (no DNF) | DNF = No |

### Saving a Filter Profile
Once you have set up a useful combination of filters, save it as a profile:
1. Configure your filters
2. Click **Save Profile**
3. Give it a name (e.g. "Easy day trip")
4. Load it any time from the **Filters** menu

### Clearing Filters
Click **View → Clear filter** to show all caches again.

---

## 6. Cache Details and Hints

Click any cache in the list to see its full details in the bottom-left panel.

- **Description** — full HTML cache description
- **Hint** — click the hint text to toggle ROT13 decoding
- **Attributes** — icons showing cache features (dog friendly, available 24/7, etc.)
- **Logs** — recent logs from other cachers; use the search box to find specific entries

---

## 7. Waypoints

Waypoints are additional coordinates associated with a cache — parking spots, stages for multi-caches, final coordinates, etc.

### Viewing Waypoints
Waypoints imported from GPX/PQ files appear automatically in the cache details panel and as extra pins on the map.

### Adding a Waypoint Manually
1. Select a cache in the list
2. Right-click → **Add Waypoint** (or go to **Cache → Add Waypoint**)
3. Enter a name, type, and coordinates
4. Click **Save**

Manually added waypoints (such as corrected coordinates for mystery caches) are saved in your local database and are not affected by re-importing.

### Updating Location Data (county, state, country)

OpenSAK can fill in the county, state, and country fields for waypoints using reverse geocoding.

- **On import** — the offline lookup runs automatically for any waypoints missing location data.
- **Manually** — go to **Waypoint → Update Waypoint Locations…** to re-run or refine the lookup for some or all waypoints. You can also right-click a waypoint and choose **Update location data…**.

The offline lookup uses the bundled [GeoNames](https://geonames.org/) database and works with no internet connection. An optional **online refinement** pass (using OpenStreetMap polygon data) is available for higher accuracy — it is opt-in because it is rate-limited and can be slow on large databases.

For full details, see [Update Waypoint Locations](update-location.md).

> **Note:** This feature requires the `update-location` feature flag to be enabled. It is on by default in developer builds and can be enabled with `--feature update-location=true`. See [Feature Flags](feature-flags.md).

---

## 8. Marking Caches as Found

### Marking a Single Cache
Right-click the cache in the list → **Mark as Found**.

### Importing Finds from Geocaching.com (recommended)
For the most accurate found status, use a **My Finds Pocket Query**:
1. On geocaching.com, go to **Play → Pocket Queries**
2. Find the **My Finds** query and download it
3. In OpenSAK, go to **Settings → Update finds from reference database…**
4. Select your My Finds `.zip` file
5. OpenSAK will mark all matching caches as found

This method works even if you have found caches that are not in your current database.

---

## 9. Updating Finds from "My Finds"

For the most accurate found status across all your databases, use a **My Finds Pocket Query** from Geocaching.com.

1. On geocaching.com, go to **Play → Pocket Queries**
2. Find the **My Finds** query and download it as a `.zip` file
3. In OpenSAK, create a new database called "My Finds" (**File → Manage databases…**)
4. Import the My Finds ZIP into that database
5. Switch back to the database you want to update
6. Go to **Settings → Update finds from reference database…** and select the "My Finds" database

OpenSAK will mark all matching caches as found, even caches that are not in the current database.

---

## 10. Exporting to GPS

OpenSAK can export your filtered cache list directly to a Garmin GPS device connected via USB.

1. Connect your Garmin device
2. Click **GPS → Send to GPS** (or press `Ctrl+G`)
3. In the dialog, click **Scan** to detect your device
4. Select the detected device from the list
5. Choose whether to export all caches or only the currently filtered list
6. Click **Send**

The caches will be written as a GPX file to your Garmin's `Garmin/GPX/` folder.

> **Note:** Only Garmin devices that mount as a USB drive are supported. Bluetooth transfer is not currently available.

---

## 11. Multiple Databases

OpenSAK supports multiple separate databases — useful if you geocache in different regions or want to keep work and leisure caches separate.

### Creating a New Database
1. Go to **File → Manage databases…**
2. Click **New Database**
3. Give it a name and set a centre point (home coordinates for that region)
4. Click **Create**

### Switching Between Databases
Go to **File → Manage databases…** and double-click any database to switch to it.

Each database has its own:
- Cache list and import history
- Centre point for distance calculations
- Filter profiles

---

## 12. Changing the Language

1. Go to **Settings → Settings…**
2. Select your language in the **Language** section
3. Restart OpenSAK — the new language takes effect on next startup

Currently supported: **Danish (da)**, **English (en)**, **French (fr)**, **Dutch (nl)**, **Portuguese (pt)**, **German (de)**, **Czech (cs)**, **Swedish (se)**

Want to add a new language? See [CONTRIBUTING.md](https://github.com/AgreeDK/opensak/blob/main/CONTRIBUTING.md) for the step-by-step guide — it only requires translating one file.

---

## 13. Getting Help

**Found a bug or have a feature request?**
→ [github.com/AgreeDK/opensak/issues](https://github.com/AgreeDK/opensak/issues)

**Questions and community discussion?**
→ [OpenSAK Facebook Group](https://www.facebook.com/groups/opensak)

**Latest releases and downloads?**
→ [github.com/AgreeDK/opensak/releases](https://github.com/AgreeDK/opensak/releases)

**Full changelog?**
→ [CHANGELOG.md](https://github.com/AgreeDK/opensak/blob/main/CHANGELOG.md)

---

*OpenSAK is free and open-source software, released under the MIT licence. Contributions are welcome — see [CONTRIBUTING.md](https://github.com/AgreeDK/opensak/blob/main/CONTRIBUTING.md) for details.*

*Last updated for v1.14.0-beta.*
