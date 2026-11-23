# Privacy Policy — OpenSAK

**Open Source Geocache Manager**
*Effective date: November 23, 2026*

---

## 1. Overview

OpenSAK is a free, open source desktop application for managing geocaching data locally on your own computer. It runs on Windows, Linux, and macOS.

This Privacy Policy explains what data OpenSAK collects, how it is stored, and what choices you have. The short version:

- OpenSAK does **not** operate any servers or cloud services
- All your geocaching data is stored exclusively on **your own device**
- OpenSAK does **not** sell, share, or transmit your personal data to any third party
- OpenSAK does **not** contain any analytics, advertising, or tracking

The full source code is publicly available and auditable at any time:
**https://github.com/OpenSAK-Org/opensak**

---

## 2. Data Stored Locally on Your Device

### 2.1 Geocache data

When you import GPX files or Pocket Query ZIP files, OpenSAK stores geocache information in a local SQLite database on your device. This includes:

- Cache names, GC codes, coordinates, descriptions, hints, and attributes
- Difficulty and terrain ratings, cache type, container size, and status
- Cache logs (finder names, dates, log text) as included in your import files
- Trackable listings associated with individual caches
- Your own corrected coordinates for mystery caches

This data originates from files you supply (exported from Geocaching.com or similar sources). OpenSAK does not fetch this data independently.

### 2.2 User preferences and settings

OpenSAK stores your application preferences locally, including:

- Home point coordinates (for distance calculations)
- Display preferences (map provider, coordinate format, language)
- Filter profiles you have saved
- Column layout preferences

These are stored in your platform's standard application data directory and never leave your device.

### 2.3 Geocaching.com authentication token (optional)

If you choose to connect OpenSAK to your Geocaching.com account (optional feature, requires API access approval), an OAuth 2.0 access token is stored locally on your device. This token:

- Is stored in your application data directory with restricted file permissions (owner-only access)
- Is **never** transmitted to any OpenSAK server — none exists
- Is used only to authenticate your requests directly to the Geocaching.com API
- Can be deleted at any time by logging out within the application, or by deleting the file manually

The token file is located at:

| Platform | Path |
|----------|------|
| Linux    | `~/.local/share/opensak/gc_token.json` |
| Windows  | `%APPDATA%\opensak\gc_token.json` |
| macOS    | `~/Library/Application Support/opensak/gc_token.json` |

### 2.4 Pocket Query e-mail settings (optional)

If you set up **Pocket Queries by e-mail**, OpenSAK stores your mail server name, port, security setting and username in its settings file. The **password** is stored in your operating system's own secure credential store (the macOS Keychain, Windows Credential Manager, or the Secret Service keyring on Linux) — never in plain text.

When you check for PQ e-mails, OpenSAK connects **only to the mail server you entered**, over an encrypted connection, and verifies the server's certificate before sending your password. It reads messages to find Pocket Query attachments and, if you chose so, deletes the e-mail after a successful import. Nothing from your mailbox is sent anywhere else.

### 2.5 Logs and crash reports

OpenSAK writes a log file (`opensak.log`, plus `opensak.log.previous` from the session before) to its application data directory. To notice crashes, it also keeps a `session.lock` file while it runs and, if it crashes hard, writes the technical details to `crash_native.log` in the same place. None of these files leave your device on their own.

When OpenSAK runs into an unexpected error, or finds at startup that the previous session crashed, it shows a crash report dialog with the **exact text** that would be shared. Your home folder path, your computer's user name, e-mail addresses and access tokens are removed from it first. Other details, such as cache or database names, may still appear, so you can check the text before sharing it.

A crash report is shared **only if you choose to**:

- **Report on GitHub** opens a prefilled issue in your browser, which you can still edit and must submit yourself. GitHub's privacy statement applies.
- **Send e-mail** opens a prefilled message to hello@opensak.com in your own mail program, which you send yourself.

Nothing is sent automatically, and you can simply close the dialog.

---

## 3. Data We Do Not Collect

OpenSAK does not collect, transmit, or process any of the following:

- Usage statistics or analytics
- Crash reports or error telemetry (a crash report is only shared when you send it yourself, see 2.5)
- Your location or GPS data
- Your name, email address, or Geocaching.com username (except as stored in locally imported cache logs)
- Any data about how you use the application

There are no third-party SDKs, advertising networks, or tracking libraries included in OpenSAK.

---

## 4. Geocaching.com API Integration (Optional)

When the optional Geocaching.com API integration is enabled and you have logged in, OpenSAK may retrieve the following data directly from Geocaching.com on your behalf:

- Favorite point counts for caches
- Trackable (Travel Bug) listings currently present in a cache
- Your personal find history, to mark caches as found in your local database

This data is fetched directly from Geocaching.com to your device. It is stored only in your local database and is subject to Geocaching.com's own Privacy Policy:
https://www.geocaching.com/account/documents/privacypolicy

OpenSAK does not log, cache on any server, or share any data returned from the Geocaching.com API.

With Offline mode on (see 5.7), OpenSAK does not contact the Geocaching.com API at all.

---

## 5. Third-Party Services

### 5.1 Map tiles

The in-application map uses OpenStreetMap tiles to display the map background by default. When you view the map, your device fetches map tile images from OpenStreetMap servers. This is a standard web request and may include your IP address, as with any web browsing. OpenStreetMap's privacy policy is available at:
https://wiki.osmfoundation.org/wiki/Privacy_Policy

Under **Settings → Map → Base maps** you can switch on other base maps. Tiles of a map you switch on and view are fetched in the same way from that map's provider: OpenTopoMap, OpenStreetMap France (CyclOSM, OSM Humanitarian), national mapping agencies (swisstopo, basemap.at, IGN France, PDOK, basemap.de, USGS), or, only if you enter your own API key for them, Esri, MapTiler or Thunderforest. Requests to a provider that needs a key include your key. Each provider's own privacy policy applies. API keys you enter are stored only on your computer, in your operating system's secure credential store (the macOS Keychain, Windows Credential Manager, or the Secret Service keyring on Linux) — not in the OpenSAK settings file, so they are not part of a settings backup. Only where no such store is available is a key kept in a file in the OpenSAK data folder that only your user account can read.

Map tiles you have viewed are kept in a cache on your own computer (in the OpenSAK data folder, up to the size set in **Settings → Map → Map tile cache**), so areas you viewed recently still show without an internet connection. Only tiles you actually viewed are stored, and only for as long as each provider allows; tiles of providers that do not allow storing them (Esri) are never written to disk. OpenSAK never downloads map areas in advance. The cache stays on your computer and is not sent anywhere.

You can optionally configure OpenSAK to open cache locations in Google Maps or OpenStreetMap in your browser. This is triggered only by your explicit action and is subject to those services' own privacy policies.

The map component itself (Leaflet and its marker-clustering and drawing plugins) is bundled with OpenSAK and loaded from your own computer; no content delivery network is contacted.

### 5.2 Garmin GPS devices

When you export caches to a connected Garmin device, OpenSAK writes a GPX file directly to the device via USB or MTP. No data is transmitted over the internet during this operation.

### 5.3 Update check and in-app updates (GitHub)

Unless you turn it off (**Settings → Advanced → Check for updates automatically**) or Offline mode is on (see 5.7), OpenSAK asks GitHub's public API at startup which OpenSAK release is the newest. The request contains no personal data beyond what any web request includes, such as your IP address. The Microsoft Store version skips this check, because the Store updates OpenSAK itself. With Offline mode on, **Help → Check for updates** asks before it connects.

When you click **Download & Install** or **Upgrade now**, the new version and its checksum file are downloaded from OpenSAK's release page on GitHub. GitHub's privacy statement applies:
https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement

### 5.4 Boundary data (OpenSAK-Data)

Country, state and county lookup works offline once the boundary data it needs is on your computer. OpenSAK fetches files from the OpenSAK-Data release page on GitHub when you download boundary packs or check for boundary data updates, and during an import or **Update waypoint locations**, for the county packs those caches need that are not on your computer yet. These are plain downloads: no cache data or coordinates are sent. With Offline mode on (see 5.7), nothing is downloaded during an import or **Update waypoint locations**; caches whose county pack is missing get their location once you run **Update waypoint locations** online again.

### 5.5 Lua macros contacting web sites

A Lua macro can download web pages or files (`opensak.get_url()`). A macro only contacts a web site after you allowed that site for that macro, when OpenSAK asks. Such a request goes from your device directly to that site and includes what any web request does, such as your IP address, plus whatever the macro puts in the address — which could include data from your databases. OpenSAK sends no cookies and no login data of its own. Only allow sites you trust; the site's own privacy policy applies. Sites you allowed always are listed in **Settings → Folder permissions**, where you can remove them. Macros can never contact geocaching.com or Groundspeak's other sites (coord.info, waymarking.com, wherigo.com). With Offline mode on (see 5.7), every such request fails at once, without asking you.

### 5.6 Macros from OpenSAK-Macros

OpenSAK checks the shared macro repository, OpenSAK-Macros on GitHub, for new versions of the macros you installed from it. It downloads the repository's macro list, `index.json` (from `raw.githubusercontent.com/OpenSAK-Org/OpenSAK-Macros`), at most once a day at startup, and whenever you click **Check for updates** or open **Browse** in the Macro manager. This is a plain download: nothing about your local macros is sent; OpenSAK compares the list with your macros on your own device. The request includes what any web request does, such as your IP address. Nothing is installed without asking: only when you click **Install** or **Update** does OpenSAK download that macro's files from the same repository, and checks each against the size and checksum in the list before saving it. To turn the startup check off, clear **Settings → Advanced → Check for macro updates at startup (once a day)**; the Macro manager then only checks when you click **Check for updates**. With Offline mode on (see 5.7), the startup check is skipped too. GitHub's privacy statement applies:
https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement

### 5.7 Offline mode

Under **Settings → General → Offline mode** you can tell OpenSAK not to go online by itself. While it is on, OpenSAK makes no automatic internet requests: no update check or macro update check at startup, no boundary data downloads during an import, and no Geocaching.com profile lookup when you open Settings. The Geocaching.com API, the Pocket Query e-mail check and Lua macros' web requests are refused with a message, and **Help → Check for updates** asks first. Downloads you start yourself in the Boundary packs dialog or the Macro manager still go out.

---

## 6. Your Rights and Data Control

Because all data is stored locally on your own device, you have full control at all times:

- You can delete your cache database at any time from the application (Database menu)
- You can log out of Geocaching.com at any time from the Settings dialog, which deletes the stored token
- **Help → OpenSAK File Locations…** shows every place OpenSAK keeps files on your device, including the saved PQ e-mail password
- You can uninstall OpenSAK and delete all associated files — on Linux (AppImage) and macOS, **Settings → Advanced → Uninstall OpenSAK → Remove program and all data** does this for you

There is no account to delete, no server-side data to request, and no data retention period — your data exists only on your device for as long as you keep it there.

---

## 7. Children's Privacy

OpenSAK does not knowingly collect any information from children. The application does not include any features directed at children, and all data handling is fully local and user-controlled.

---

## 8. Changes to This Policy

If this Privacy Policy is updated, the new version will be published on the OpenSAK GitHub repository and announced in the OpenSAK community. The effective date at the top of this document will be updated accordingly.

Significant changes will be announced in the OpenSAK Facebook community group and on [opensak.com](https://opensak.com).

---

## 9. Contact

OpenSAK is developed and maintained by Allan, Denmark.

If you have questions about this Privacy Policy or how OpenSAK handles data, please reach out via:

- **GitHub Issues:** https://github.com/OpenSAK-Org/opensak/issues
- **Facebook community:** [OpenSAK — Open Source Geocache Manager](https://www.facebook.com/groups/2062154441232996)
- **Website:** https://opensak.com

---

*OpenSAK is free, open source software licensed under the MIT License.*
*https://github.com/OpenSAK-Org/opensak*
