# OpenSAK — Open Source Swiss Army Knife for Geocaching

A modern, cross-platform geocaching management tool for **Linux**, **Windows** and **macOS** — a free, open source successor to GSAK, built in Python.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![PySide6](https://img.shields.io/badge/GUI-PySide6-green)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Status](https://img.shields.io/badge/Status-Beta-orange)

---

> **⚠️ Hobby Project Notice**
>
> This project is developed in my spare time as a personal hobby project.
> Bug reports and suggestions are welcome via GitHub Issues, but responses
> and updates are not guaranteed. Development happens when time and interest allow.
>
> Pull requests are welcome, though they may not always be reviewed or merged.
>
> The software is provided as-is, without warranty or guaranteed support.

---

## Features

### Import & Database
- 📥 **Import** GPX files and Pocket Query ZIP files from Geocaching.com
- 🗄️ **Multiple databases** — keep regions separate (e.g. Zealand, Bornholm, Cyprus)
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
- 🔍 **Advanced filter dialog** — 5 tabs: General, Dates, Other, Attributes (~70 Groundspeak attributes) and a raw SQL WHERE tab
- 📊 **Configurable columns** — 17+ columns, toggle on/off
- 🎨 **Status icons** in list — ✅ found, ❌ DNF, 🔒 archived, ⚠️ unavailable
- 🔗 **Click GC code** → opens cache page on geocaching.com
- 🗺️ **Click coordinates** → opens in Google Maps or OpenStreetMap

### Cache Details
- 📋 **Cache details** — description, hints and logs
- 🔓 **ROT13 hint decoding** — one click to decode / re-hide the hint
- 🔍 **Search in logs** — real-time search with match highlighting
- 📍 **Corrected coordinates** — store solved puzzle coordinates per cache; used in GPS export and shown on map
- ✏️ **Add / edit / delete** caches manually

### Right-click Menu
- 🌐 Open on geocaching.com
- 🗺️ Open in map app (Google Maps / OpenStreetMap)
- 📋 Copy GC code / coordinates (in your chosen format)
- ☑ Mark as found / not found
- 📍 Add / edit / clear corrected coordinates
- ⇄ Open coordinate converter directly from the cache list

### GPS Export
- 📤 **Send to Garmin GPS** — auto-detects USB-mounted Garmin devices
- 🗑️ **Optional: delete existing GPX files** on device before upload
- 💾 **Save as GPX file** — export to any location

### Geocaching Tools
- **⇄ Coordinate Converter** — convert between DD, DMM and DMS formats with one click
- **📐 Coordinate Projection** — calculate a new coordinate from bearing and distance
- **🔢 Digit Checksum** — sum all digits in a coordinate (N/S and E/W separately)
- **⊕ Midpoint** — find the great-circle midpoint between two coordinates
- **📏 Distance & Bearing** — distance and azimuth between two coordinates
- All tools open pre-filled with the currently selected cache's coordinates

### Language Support
- 🌍 **Danish, English, French, Dutch, Portuguese, German, Czech and Swedish** built in
- 🔧 **Easy to add new languages** — copy one file, translate, done

---

## Known Limitations (Beta)

- Favourite points cannot be imported from GPX/PQ files (requires Geocaching.com API)
- No Geocaching.com Live API integration
- GPS auto-detection on Linux may not find all Garmin devices automatically
- macOS builds are not signed with an Apple Developer certificate (right-click → Open on first launch)

---

## Documentation

| Guide | Description |
|---|---|
| [Installation](docs/installation.md) | All platforms, automatic and manual methods, updating, uninstalling |
| [Getting Started](docs/getting-started.md) | First launch, importing, filtering, GPS export, multiple databases |
| [Filter Reference](docs/filters.md) | All filter types across 5 tabs, AND/OR logic, filter profiles |
| [Keyboard Shortcuts](docs/keyboard-shortcuts.md) | Full shortcut reference |
| [Feature Flags](docs/feature-flags.md) | Developer feature flag system |
| [CLI --version flag](docs/cli-version-flag.md) | Print version or run a specific release |
| [CHANGELOG](CHANGELOG.md) | Version history |
| [CONTRIBUTING](CONTRIBUTING.md) | Development setup, code style, translations, PR workflow |

---

## Quick Start

```bash
# Linux / macOS (from source)
git clone https://github.com/AgreeDK/opensak.git
cd opensak
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python run.py
```

For Windows, macOS bundles, and AppImage downloads see [docs/installation.md](docs/installation.md).

---

## Reporting Bugs

Please use [GitHub Issues](https://github.com/AgreeDK/opensak/issues) and include:
- Your platform (Linux / Windows / macOS + version)
- Python version: `python3 --version`
- The error message from the terminal (if any)

---

## Roadmap

- [ ] HTML/PDF reports and statistics
- [ ] GPS export — improve auto-detection on all Linux distros
- [ ] Favourite points (requires Geocaching.com API)
- [ ] More languages (Dutch, Finnish, …)
- [x] **Trip Planner** — radius and multi-point route corridor with map preview
- [x] **Home points list** — named locations with toolbar quick-switch
- [x] **Corrected coordinates** — store and use solved puzzle coordinates
- [x] Geocaching Tools menu — coordinate converter, projection, checksum, midpoint, distance & bearing
- [x] Coordinate format preference (DMM / DMS / DD)
- [x] French language — contributed by @theyoungstone
- [x] German, Czech, Swedish and Dutch languages added
- [x] Windows installer (.exe) — built automatically via GitHub Actions
- [x] Linux AppImage — built automatically via GitHub Actions
- [x] macOS installer (.dmg) — arm64 and x86_64, built automatically via GitHub Actions
- [x] GitHub Actions CI/CD pipeline

---

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

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
