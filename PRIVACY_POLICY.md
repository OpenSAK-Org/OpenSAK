# Privacy Policy — OpenSAK

**Open Source Geocache Manager**
*Effective date: September 29, 2026*

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

---

## 3. Data We Do Not Collect

OpenSAK does not collect, transmit, or process any of the following:

- Usage statistics or analytics
- Crash reports or error telemetry
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

---

## 5. Third-Party Services

### 5.1 Map tiles

The in-application map uses OpenStreetMap tiles to display the map background. When you view the map, your device fetches map tile images from OpenStreetMap servers. This is a standard web request and may include your IP address, as with any web browsing. OpenStreetMap's privacy policy is available at:
https://wiki.osmfoundation.org/wiki/Privacy_Policy

You can optionally configure OpenSAK to open cache locations in Google Maps or OpenStreetMap in your browser. This is triggered only by your explicit action and is subject to those services' own privacy policies.

The map component itself (Leaflet and its marker-clustering plugin) is loaded from the unpkg.com content delivery network when the map is shown — again a standard web request.

### 5.2 Garmin GPS devices

When you export caches to a connected Garmin device, OpenSAK writes a GPX file directly to the device via USB or MTP. No data is transmitted over the internet during this operation.

### 5.3 Update check and in-app updates (GitHub)

Unless you turn it off (**Settings → Advanced → Check for updates automatically**), OpenSAK asks GitHub's public API at startup which OpenSAK release is the newest. The request contains no personal data beyond what any web request includes, such as your IP address. The Microsoft Store version skips this check, because the Store updates OpenSAK itself.

When you click **Download & Install** or **Upgrade now**, the new version and its checksum file are downloaded from OpenSAK's release page on GitHub. GitHub's privacy statement applies:
https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement

### 5.4 Boundary data (OpenSAK-Data)

Country, state and county lookup works offline. Only when you choose to download boundary packs or check for boundary data updates does OpenSAK fetch files from the OpenSAK-Data release page on GitHub.

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
- **Facebook community:** [OpenSAK — Open Source Geocache Manager](https://www.facebook.com/groups/opensak)
- **Website:** https://opensak.com

---

*OpenSAK is free, open source software licensed under the MIT License.*
*https://github.com/OpenSAK-Org/opensak*
