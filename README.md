# OpenSAK — Open Source Swiss Army Knife for Geocaching

A modern, cross-platform geocaching management tool for **Linux**, **Windows** and **macOS** — a free, open source successor to GSAK, built in Python.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![PySide6](https://img.shields.io/badge/GUI-PySide6-green)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Status](https://img.shields.io/badge/Status-Stable-brightgreen)
[![Open Collective backers](https://opencollective.com/opensak/backers/badge.svg)](https://opencollective.com/opensak)

---

> **ℹ️ Built by volunteers, for geocachers**
>
> OpenSAK is developed by a small international team of volunteers — geocachers
> building the tool they want to use themselves. There's no company behind it and
> no paid support, so while we read every issue and review every pull request,
> we can't promise when (or whether) a particular request will be addressed.
>
> The best way to move something forward is to get involved: report bugs with
> clear steps to reproduce, test the betas, help with translations, or open a
> pull request.
>
> OpenSAK is provided as-is, under the MIT License, without warranty or
> guaranteed support.

---

## Features

### Import & Database
- 📥 **Import** GPX files and Pocket Query ZIP files from Geocaching.com
- 📧 **Pocket Queries by e-mail** — fetch PQ ZIP attachments straight from your own IMAP mailbox; the password is kept in your OS keyring, never in plain text
- 🔁 **Import GSAK databases** — read a GSAK database directly, including personal notes, corrected coordinates, child waypoints, attributes and the full log history
- 🗄️ **Multiple databases** — keep regions separate (e.g. Zealand, Bornholm, Cyprus), and move caches between them
- 📍 **Home points** — save multiple named home points (Home, Cottage, Hotel…) and switch instantly from the toolbar
- ✅ **Update finds** from a reference database (e.g. your "My Finds" PQ)

### Trip Planning
- 🗺️ **Trip Planner** (`Ctrl+T`) — plan a geocaching trip in two modes:
  - **Radius** — find caches within a set distance from your active home point; sort by distance, difficulty, terrain, date or name
  - **Route A→B→…** — find caches along a multi-point route (up to 10 waypoints); caches sorted in driving order along the route
- Route points can be typed in any coordinate format or picked from your saved home points
- **Preview on map** — open selected trip caches on an interactive map with one click
- Export trip caches directly to GPS or as a GPX file

### View & Navigation
- 🗺️ **Interactive map** with OpenStreetMap and colour-coded cache pins with clustering
- 🔍 **Advanced filter dialog** modelled on GSAK's — 10 tabs: General, Dates, Other, Logs, Line/Polygon, Child Waypoints, Trackables, Attributes (~70 Groundspeak attributes), Text Search, and a raw SQL WHERE tab
  - 12 text operators on every text field (contains, equals, starts/ends with, in list, empty, regex — and their negations)
  - Date conditions, distance from any centre point, compass direction, caches along a route or inside a polygon
  - AND/OR logic, a global **Invert filter**, and saved filter profiles in the toolbar
- 📊 **Configurable columns** — 17+ columns, toggle on/off
- 🎨 **Color-coded status** — found (yellow) and your own caches (green) in the GC Code column and info bar, archived/disabled caches in red; clickable info-bar counts filter the list instantly
- 🔗 **Click GC code** → opens cache page on geocaching.com
- 🗺️ **Click coordinates** → opens in Google Maps or OpenStreetMap
- 🖥️ **Full-screen / popout map** — enlarge the map to a bigger, dedicated view
- 🌍 **Offline Country/State/County lookup** — automatic reverse-geocoding on import, no internet connection required; optional downloadable boundary packs for more detail

### Cache Details
- 📋 **Cache details** — description, hints, logs, attributes, personal notes, and child waypoints, each in their own tab
- 🔓 **ROT13 hint decoding** — one click to decode / re-hide the hint
- 🔍 **Search in logs** — real-time search with match highlighting; links in log text are clickable
- 📝 **Personal notes** — your own free-text notes per cache, round-trippable with GSAK (`gsak:UserNote`)
- 🧩 **Child waypoints** — parking spots, trail heads, and stages imported from GPX, shown on the map and in a dedicated tab; caches with waypoints show in **bold** in the list
- 🔒 **Lock caches** — freeze a cache's core fields (name, type, coordinates, D/T, owner, status, descriptions, hint…) against being overwritten by a later re-import
- 📍 **Corrected coordinates** — store solved puzzle coordinates per cache; used in GPS export and shown on map
- ✏️ **Add / edit / delete** caches manually

### Right-click Menu
- 🌐 Open on geocaching.com
- 🗺️ Open in map app (Google Maps / OpenStreetMap)
- 📋 Copy GC code / coordinates (in your chosen format)
- ☑ Mark as found / not found
- 🔒 Lock / unlock cache — protect against import overwrites
- 📍 Add / edit / clear corrected coordinates
- ⇄ Open coordinate converter directly from the cache list

### GPS Export
- 📤 **Send to Garmin GPS** — auto-detects USB-mounted Garmin devices, and newer MTP-only devices on both Windows and Linux
- 📦 **GGZ export** — Garmin's compressed format, lifting the 10,000-cache device limit
- 🗑️ **Optional: delete existing GPX files** on device before upload
- 💾 **Save as GPX, LOC, GGZ or KML** — export to any location

### Geocaching Tools
- **⇄ Coordinate Converter** — convert between DD, DMM and DMS formats with one click
- **📐 Coordinate Projection** — calculate a new coordinate from bearing and distance
- **🔢 Digit Checksum** — sum all digits in a coordinate (N/S and E/W separately)
- **⊕ Midpoint** — find the great-circle midpoint between two coordinates
- **📏 Distance & Bearing** — distance and azimuth between two coordinates
- All tools open pre-filled with the currently selected cache's coordinates

### Installation & Updates
- 🪟 **Windows** — available from the [Microsoft Store](https://apps.microsoft.com/detail/9p4nbmm84h2d) (free, signed, updates itself automatically), or as a direct download from GitHub
- 🍎 **macOS** — signed and notarized `.dmg` for Apple Silicon and Intel
- 🐧 **Linux** — AppImage that adds itself to your application menu, can update itself in place, and can uninstall itself from within the app
- 🔔 **Update check** — OpenSAK tells you when a new version is available, optionally including betas
- 🛡️ **Your data is never removed** by an update or uninstall unless you explicitly choose to

### Language Support
- 🌍 **11 languages** built in: Danish, English, French, Dutch, Portuguese, German, Swiss German, Czech, Swedish, Polish and Spanish
- 🔧 **Easy to add new languages** — copy one file, translate, done

---

## Known Limitations

- Favourite points cannot be imported from GPX/PQ files (requires Geocaching.com API)
- No Geocaching.com Live API integration
- GPS auto-detection may not find every Garmin model automatically
- The direct Windows download from GitHub is not code-signed, so SmartScreen or Smart App Control may warn or block it — the Microsoft Store version is signed and avoids this
- Pocket Queries by e-mail don't yet support Gmail or Outlook.com (both require OAuth2)

---

## Documentation

| Guide | Description |
|---|---|
| [User Guide](https://opensak.com/user-guide.html) | The complete, illustrated guide to using OpenSAK |
| [Installation](docs/installation.md) | All platforms, automatic and manual methods, updating, uninstalling |
| [Uninstalling](docs/Uninstalling-OpenSAK.md) | What OpenSAK creates on disk, and how to remove it |
| [Getting Started](docs/getting-started.md) | First launch, importing, filtering, GPS export, multiple databases |
| [Filter Reference](docs/filters.md) | All filter types across 10 tabs, text operators, AND/OR logic, invert, filter profiles |
| [Update Locations](docs/update-location.md) | Filling in country/state/county via offline and online reverse geocoding |
| [Keyboard Shortcuts](docs/keyboard-shortcuts.md) | Full shortcut reference |
| [Feature Flags](docs/feature-flags.md) | Developer feature flag system |
| [CLI --version flag](docs/cli-version-flag.md) | Print version or run a specific release |
| [Roadmap](docs/ROADMAP.md) | Planned features and priorities |
| [CHANGELOG](CHANGELOG.md) | Version history |
| [CONTRIBUTING](CONTRIBUTING.md) | Development setup, code style, translations, PR workflow |
| [Contributing with an AI Assistant](docs/CONTRIBUTING-with-AI.md) | Step-by-step guide to contributing code using an AI coding assistant |
| [Reverse-geocoding data](docs/reverse-geocoding-data.md) | Developer notes on the offline boundary data (OpenSAK-Data) |

---

## Quick Start

```bash
# Linux / macOS (from source)
git clone https://github.com/OpenSAK-Org/OpenSAK.git
cd OpenSAK
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python run.py
```

This runs the latest stable version. To try the newest features, clone the `beta` branch instead (`git clone --branch beta …`).

For the Microsoft Store, macOS `.dmg` and Linux AppImage downloads see [docs/installation.md](docs/installation.md) or the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases).

---

## Reporting Bugs

Please use [GitHub Issues](https://github.com/OpenSAK-Org/OpenSAK/issues) and include:
- Your OpenSAK version (**Help → About**) and how you installed it (Microsoft Store, direct download, AppImage, `.dmg`, or from source)
- Your platform (Linux / Windows / macOS + version)
- The error message, or the log file (**Help → Open log file**)

Questions and ideas are also welcome in [GitHub Discussions](https://github.com/OpenSAK-Org/OpenSAK/discussions) and the OpenSAK Facebook group.

---

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for planned features and their current priority, and [GitHub Issues](https://github.com/OpenSAK-Org/OpenSAK/issues) for what's being worked on right now. The [CHANGELOG](CHANGELOG.md) lists everything that has landed.

---

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

Using an AI coding assistant to help write your contribution? See
[Contributing with an AI Assistant](docs/CONTRIBUTING-with-AI.md) for a guide
tailored to that workflow.

---

## Support OpenSAK

OpenSAK is free and open source, developed in spare time. If it's useful to you,
consider supporting ongoing development — contributions help cover costs like
Windows code signing and macOS notarization so releases can be trusted and
installed without security warnings.

👉 [Support OpenSAK on Open Collective](https://opencollective.com/opensak)

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Acknowledgements

- [OpenStreetMap](https://www.openstreetmap.org) for map tiles
- [Leaflet.js](https://leafletjs.com) for the map library
- [PySide6 / Qt](https://www.qt.io) for the GUI framework
- [SQLAlchemy](https://www.sqlalchemy.org) for the database layer
- [OpenSAK Contributors](CONTRIBUTORS.md)
- Everyone who has tested the app and provided feedback!
