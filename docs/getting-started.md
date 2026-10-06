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

- **Windows** — install OpenSAK from the [Microsoft Store](https://apps.microsoft.com/detail/9p4nbmm84h2d). It's free, signed by Microsoft and keeps itself up to date. A direct download (ZIP) is also available.
- **macOS** — download the `.dmg` for your Mac (Apple Silicon or Intel) from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases), open it and drag OpenSAK to Applications. The app is signed and notarized, so it opens normally.
- **Linux** — download the AppImage from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases), make it executable (`chmod +x OpenSAK-*.AppImage`) and run it. On first launch it offers to add itself to your application menu.

See [Installation](installation.md) for all options, how updating works, and how to uninstall.

---

## 2. First Launch

The first time you start OpenSAK, the **Welcome Wizard** walks you through a few steps:

1. **Installation folder** — where OpenSAK keeps its settings and log file. The default is fine for most people.
2. **Database folder** — where your geocache databases are stored. Choose a different folder here if you want your databases somewhere specific (e.g. a folder you back up).
3. **Backup folder** — where OpenSAK keeps its backups, by default *Documents/OpenSAK Backups*. A folder on another disk, an external drive or a synced folder is safest, because it also protects your data if your computer's disk fails.
4. **Your Geocaching profile** — optional; you can also set this later in Settings.

After the wizard you'll see OpenSAK's three-panel layout: the cache list, the cache details and the map — all empty until you import some caches.

Before importing, it's a good idea to set your **home point**. It is used as the centre for distance calculations and the map.

1. Go to **Settings → Settings…** (`Ctrl+,`)
2. In the **User locations** section, add your home coordinates — any common format works (e.g. `N55 47.250 E012 25.000` or `55.7875, 12.4167`)
3. Click **Save**

You can save several named home points (Home, Cottage, Hotel…) and switch between them from the toolbar.

---

## 3. Importing Your Caches

OpenSAK works with standard **GPX files** and **Pocket Query ZIP files** — the same files you already download from Geocaching.com.

### Downloading a Pocket Query (recommended)
1. Log in to [geocaching.com](https://www.geocaching.com)
2. Go to **Play → Pocket Queries**
3. Create or run an existing Pocket Query
4. Download the `.zip` file — do **not** unzip it

### Importing into OpenSAK
1. Click **File → Import GPX / PQ zip…** (or press `Ctrl+I`)
2. Select your `.zip` or `.gpx` file
3. Click **Open** — OpenSAK will import all caches and their logs

> **Tip:** You can import multiple files into the same database. Duplicate caches are updated automatically, so you can re-import an updated Pocket Query without creating duplicates.

> **Auto-geocoding:** After a successful import, OpenSAK automatically runs an offline lookup to fill in the county, state, and country for any waypoints that are missing that data. No extra step needed. For higher-accuracy results you can run an optional online refinement afterwards — see [Waypoints](#7-waypoints).

### Pocket Queries by e-mail
If your Pocket Queries arrive by e-mail, OpenSAK can fetch them straight from your mailbox. Set up the mailbox under **Settings → Settings… → PQ Email**, then use **File → Check for PQ Email…**. The password is kept in your operating system's keyring, never in plain text.

### Coming from GSAK
You can import a GSAK database directly with **File → Import from GSAK Database…** — including personal notes, corrected coordinates, child waypoints, attributes and the full log history. OpenSAK also uses the same GPX/PQ format as GSAK, so your Pocket Queries import as usual.

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
Shows all caches in the current list as colour-coded pins, clustered when zoomed out. Click any pin to highlight that cache in the list and show its details. Use **View → Maximize map** or **Pop out map** for a bigger view.

---

## 5. Filtering Your Cache List

Filters let you narrow down the cache list to exactly what you want to see. The filter dialog is modelled on GSAK's and has ten tabs — General, Dates, Other, Logs, Line/Polygon, Child Waypoints, Trackables, Attributes, Text Search and Where — all combinable with AND/OR logic, plus a global **Invert filter**. See the [Filter Reference](filters.md) for every filter.

### Opening the Filter Dialog
Click **View → Set filter…** (or press `Ctrl+F`).

### Common filter examples

| Goal | Filter to use |
|---|---|
| Only unfound caches | Found = No |
| Difficulty 1–2 only | Difficulty ≤ 2 |
| Within 5 km of home | Other tab: Distance from centre point, At most 5 km |
| Traditional caches only | Cache type = Traditional |
| Caches with parking nearby | Attributes includes Parking |
| Not yet attempted (no DNF) | DNF = No |

### Saving a Filter Profile
Once you have set up a useful combination of filters, save it as a profile:
1. Configure your filters
2. Click **Save** and give the profile a name (e.g. "Easy day trip")
3. Pick it any time from the filter profile dropdown in the toolbar

### Clearing Filters
Click the red **✕** in the toolbar, or **View → Clear filter**, to show all caches again.

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
2. Open **Waypoint → Edit cache…** (`Ctrl+E`) and go to the **Waypoints** tab
3. Click **Add waypoint…**, and enter the prefix, type, name and coordinates
4. Click **OK**

For solved mystery caches, use **corrected coordinates** instead (right-click the cache → corrected coordinates): the original coordinates are kept, and the corrected ones are used on the map and when exporting to GPS.

### Updating Location Data (county, state, country)

OpenSAK can fill in the county, state, and country fields for waypoints using reverse geocoding.

- **On import** — the offline lookup runs automatically for any waypoints missing location data.
- **Manually** — go to **Waypoint → Update waypoint locations…** to re-run or refine the lookup for some or all caches.

The offline lookup uses the bundled [GeoNames](https://geonames.org/) database and works with no internet connection. An optional **online refinement** pass (using OpenStreetMap polygon data) is available for higher accuracy — it is opt-in because it is rate-limited and can be slow on large databases.

For full details, see [Update Waypoint Locations](update-location.md).

---

## 8. Marking Caches as Found

### Marking a Single Cache
Right-click the cache in the list → **Mark as Found**.

### Importing Finds from Geocaching.com (recommended)
For the most accurate found status, use a **My Finds Pocket Query** — see the next section.

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

The caches are written as a GPX or GGZ file to your Garmin's `Garmin/GPX/` folder. Newer Garmin models that connect over MTP instead of as a USB drive are supported on Windows and Linux. Bluetooth transfer is not currently available.

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

### Backing Up
Go to **File → Back up now…** to back up your databases together with your settings, filter profiles, column views and custom icons. Backups go into the backup folder you chose in the Welcome Wizard (by default *Documents/OpenSAK Backups*); change it under **Settings → Advanced → Folders** or in the backup dialog itself. A folder on another disk or an external drive is safest. Existing backups stay where they are when you change the folder. Backups you make this way are never deleted by OpenSAK.

When you close OpenSAK and something has changed since your last backup, OpenSAK asks whether to back up first: **Back Up and Close**, **Not Now**, or **Cancel** to stay in OpenSAK. Tick **Don't ask again** to always back up or never ask; change it later, together with how many automatic backups to keep (5 by default), under **Settings → Advanced → Backups**. Only the oldest automatic backups are deleted.

### Restoring a Backup
Go to **File → Restore from backup…**, pick a backup and the databases you want, and click **Restore**. A restored database is always added as a new database — nothing you have now is overwritten — and it opens exactly as it was when the backup was taken.

---

## 12. Changing the Language

1. Go to **Settings → Settings…**
2. Select your language in the **Language** section
3. Restart OpenSAK — the new language takes effect on next startup

Currently supported (11): **Danish**, **English**, **French**, **Dutch**, **Portuguese**, **German**, **Swiss German**, **Czech**, **Swedish**, **Polish** and **Spanish**

Want to add a new language? See [CONTRIBUTING.md](https://github.com/OpenSAK-Org/opensak/blob/main/CONTRIBUTING.md) for the step-by-step guide — it only requires translating one file.

---

## 13. Getting Help

**Found a bug or have a feature request?**
→ [github.com/OpenSAK-Org/opensak/issues](https://github.com/OpenSAK-Org/opensak/issues)

**Questions and community discussion?**
→ [OpenSAK Facebook Group](https://www.facebook.com/groups/opensak)

**Full user guide?**
→ [opensak.com/user-guide.html](https://opensak.com/user-guide.html)

**Latest releases and downloads?**
→ [github.com/OpenSAK-Org/opensak/releases](https://github.com/OpenSAK-Org/opensak/releases)

**Full changelog?**
→ [CHANGELOG.md](https://github.com/OpenSAK-Org/opensak/blob/main/CHANGELOG.md)

---

*OpenSAK is free and open-source software, released under the MIT licence. Contributions are welcome — see [CONTRIBUTING.md](https://github.com/OpenSAK-Org/opensak/blob/main/CONTRIBUTING.md) for details.*

*Last updated for v1.20.0.*
