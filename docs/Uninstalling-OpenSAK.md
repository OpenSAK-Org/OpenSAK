# Uninstalling OpenSAK (Windows / Linux / macOS)

Uninstalling OpenSAK removes the program only. Your databases and settings are
**never** deleted unless you explicitly choose to remove them.

| Installed from | The easy way |
|---|---|
| **Microsoft Store** | Windows **Settings → Apps → Installed apps**, find OpenSAK, choose **Uninstall** |
| **Linux AppImage** | In OpenSAK: **Settings → Settings… → Advanced → AppImage → Uninstall OpenSAK** |
| **macOS** | In OpenSAK: **Settings → Settings… → Advanced → Uninstall → Uninstall OpenSAK** |
| **Windows direct download** | Delete the folder you unzipped OpenSAK into |

On Linux and macOS you choose between *Remove program only* and *Remove program
and all data*; the second asks you to confirm again, and databases you opened
from other places (e.g. a USB drive) are never deleted.

**Your backups are always kept.** Backups made with **File → Back up now…** or
when closing OpenSAK live in your backup folder (by default
*Documents/OpenSAK Backups*, see **Settings → Advanced → Folders**). No way of
uninstalling removes them — not even *Remove program and all data* — so you
can restore them after a reinstall with **File → Restore from backup…**.
Delete the folder yourself if you no longer need them.

The rest of this guide explains what OpenSAK actually creates on your computer
and how to clean it up by hand — for Windows data, for older versions, or if
you've already deleted the program.

> **Tip:** **Help → OpenSAK File Locations…** (1.20.0 and later) lists every
> place OpenSAK keeps files, with an **Open folder** button for each. Use it
> before uninstalling if you want to back up or remove your data.

---

## ⚠️ Before you delete anything

Your **database file(s)** (`.db`, plus matching `-shm` / `-wal` files) contain
all your caches, logs, waypoints, and notes. There is no undo once they're
deleted. If in doubt:

1. Find your database folder (see below).
2. Copy the `.db` files somewhere safe (a USB stick, another folder, cloud
   storage) before deleting anything.

If you're running **1.14.0-beta or later**, the easiest way to see your exact
paths is inside the app itself: **Settings → Advanced** shows your current
*Install folder* and *Database folder* directly — no guessing needed.

---

## What OpenSAK creates on your computer

There are two completely separate things:

1. **The program itself** — the `.exe`, the AppImage, or the `.app` you
   downloaded/dragged into place. Deleting this removes the program but
   leaves your data untouched.
2. **Your data** — settings, database(s), and log file, created the first
   time you run OpenSAK. This is what you'd want to back up or remove
   separately.

Where your data lives depends on which version you're running, because this
changed in version 1.14.0:

| | Versions **before** 1.14.0 | Versions **1.14.0-beta and later** |
|---|---|---|
| Settings | Windows Registry / Qt `.ini`/`.conf` file under an **"OpenSAK Project"** key or folder | A plain `opensak.json` file, located via a small `bootstrap.json` pointer file |
| Language only | `preferences.json` | merged into `opensak.json` |
| Database(s) | Same folder as settings | A folder you chose in the welcome wizard (can be the same folder, or a different one) |
| Logs | `opensak.log` (same folder) | `opensak.log` inside the install folder |

If you upgraded from an older version to 1.14.0+, OpenSAK automatically copied
your old settings into the new `opensak.json` the first time it ran. The old
registry/`.ini` entries are no longer used after that — they're just leftover
clutter and are safe to delete if you want a clean system.

---

## Windows

### 1. Remove the program

- **Microsoft Store:** Windows **Settings → Apps → Installed apps**, find
  OpenSAK, and choose **Uninstall**. Windows removes the program. To find
  your data before uninstalling, use **Help → OpenSAK File Locations…**.
- **Direct download:** delete the folder you unzipped OpenSAK into (and any
  shortcut you pinned yourself — OpenSAK doesn't create these).

### 2. Find your data

**If you're on 1.14.0-beta or later**, open OpenSAK → **Settings → Advanced**
to see the exact Install folder and Database folder paths, then go there in
File Explorer.

**Default locations**, if you never changed them:

| What | Typical path |
|---|---|
| Bootstrap pointer (1.14.0+) | `%APPDATA%\opensak\bootstrap.json` |
| Install folder / `opensak.json` / log (1.14.0+, default) | `%APPDATA%\opensak\` |
| Database, settings, logs (pre-1.14.0, default) | `%APPDATA%\opensak\` |

`%APPDATA%` is usually `C:\Users\<your name>\AppData\Roaming`. To get there
quickly: press **Win + R**, type `%APPDATA%`, press Enter.

### 3. Remove old QSettings (pre-1.14.0 leftovers)

Older versions also stored some settings directly in the Windows Registry
under an organization name of **"OpenSAK Project"**. To check and remove this:

1. Press **Win + R**, type `regedit`, press Enter.
2. Press **Ctrl+F**, search for `OpenSAK Project`.
3. If found (usually under `HKEY_CURRENT_USER\Software\OpenSAK Project`),
   right-click that key → **Delete**.

This step is optional — it only contains old preference values and is never
read by 1.14.0+ versions.

### 4. Delete the data folder (optional, only if you want a clean slate)

Once backed up, delete the `%APPDATA%\opensak\` folder (and the registry key
above, if present) to remove all traces of OpenSAK.

---

## Linux

### The easy way: uninstall from inside OpenSAK

If you added the AppImage to your application menu (OpenSAK offers this on first
launch), open **Settings → Settings… → Advanced → AppImage** and click
**Uninstall OpenSAK**. Choose *Remove program only* or *Remove program and all
data* — the second also removes the settings, database folder, older settings
files and the saved PQ Email password from your keyring, after a second
confirmation.

### 1. Remove the program manually

Just delete the AppImage file you downloaded (wherever you saved it — often
`~/Applications`, `~/Downloads`, or `~/.local/bin`).

If you integrated it into your application menu using a tool like
**AppImageLauncher** or `appimaged`, also remove the generated launcher entry
and icon it created, typically:

```bash
ls ~/.local/share/applications/ | grep -i opensak
ls ~/.local/share/icons/ -R | grep -i opensak
```

Delete any matching files found.

### 2. Find your data

**If you're on 1.14.0-beta or later**, check **Settings → Advanced** inside
the app for the exact paths.

**Default locations**, if you never changed them:

| What | Typical path |
|---|---|
| Bootstrap pointer (1.14.0+) | `~/.config/opensak/bootstrap.json` |
| Install folder / `opensak.json` / log (1.14.0+, default) | `~/.local/share/opensak/` |
| Database, settings, logs (pre-1.14.0, default) | `~/.local/share/opensak/` |
| Old QSettings file (pre-1.14.0) | `~/.config/OpenSAK Project/` (filename `OpenSAK.conf` or `OpenSAK.ini`) |

These are hidden folders (starting with a dot). In most file managers, press
**Ctrl+H** to show hidden files, or use a terminal:

```bash
ls -la ~/.config | grep -i opensak
ls -la ~/.local/share | grep -i opensak
```

### 3. Delete the data (optional)

Once you've backed up your `.db` files, you can remove the folders found
above with:

```bash
rm -rf ~/.local/share/opensak
rm -rf ~/.config/opensak
rm -rf "~/.config/OpenSAK Project"
```

(Only run commands for folders that actually exist on your system — check
with `ls` first, as shown above.)

---

## macOS

### The easy way: uninstall from inside OpenSAK (1.20.0 and later)

Open **Settings → Settings… → Advanced** and click **Uninstall OpenSAK** in the
*Uninstall* section. You choose between:

- **Remove program only** — OpenSAK is moved to the Trash; your caches,
  databases and settings are kept, so a later reinstall picks them up again.
- **Remove program and all data** — also deletes every place OpenSAK keeps
  data (the ones listed under **Help → OpenSAK File Locations…**, including a
  database folder you chose yourself and the PQ Email password in your
  Keychain). You're asked to confirm a second time, because this can't be
  undone. Databases you opened from other places, such as an external drive,
  are never deleted.

OpenSAK then closes. The program goes to the **Trash** (so you can still put
it back until you empty it); deleted data does not.

If OpenSAK is running straight from the `.dmg`, or your user account isn't
allowed to change the folder it's in, OpenSAK tells you so and you use the
manual steps below instead — Finder will ask for an administrator password
if needed.

The manual steps below are also what to do if you have already dragged
OpenSAK to the Trash.

### 1. Remove the program

If you haven't already, drag **OpenSAK.app** out of `/Applications` and into
the **Trash**, then empty the Trash. You can also delete the `.dmg` installer
file you originally downloaded — once the app is copied to `/Applications`,
the `.dmg` itself isn't needed anymore (eject it first if it's still mounted
on your Desktop).

### 2. Find your data

**If you're on 1.14.0-beta or later**, check **Settings → Advanced** inside
the app for the exact paths.

**Default locations**, if you never changed them:

| What | Typical path |
|---|---|
| Bootstrap pointer (1.14.0+) | `~/Library/Application Support/opensak/bootstrap.json` |
| Install folder / `opensak.json` / log (1.14.0+, default) | `~/Library/Application Support/opensak/` |
| Database, settings, logs (pre-1.14.0, default) | `~/Library/Application Support/opensak/` |
| Old QSettings preferences (pre-1.14.0) | `~/Library/Preferences/` (a `.plist` file with "opensak" somewhere in its name) |

To get to `~/Library` quickly: in Finder, click **Go** in the menu bar, hold
**Option**, and **Library** will appear in the list (it's hidden by default).
Or use **Go → Go to Folder...** and type `~/Library/Application Support`.

If you're not sure of the exact preferences filename, search for it in
Terminal:

```bash
find ~/Library/Preferences -iname "*opensak*"
find ~/Library/Application\ Support -iname "*opensak*"
```

### 3. Delete the data (optional)

Once backed up, remove what was found above, e.g.:

```bash
rm -rf ~/Library/Application\ Support/opensak
rm -f ~/Library/Preferences/<the file found above>
```

---

## Quick checklist

1. ✅ Back up your `.db` file(s) somewhere safe.
2. ✅ Delete the old program file (`.exe` / AppImage / `.app`).
3. ✅ (Optional) Delete the settings/database folder for your OS, listed above.
4. ✅ (Optional, pre-1.14.0 leftovers only) Remove the old Registry key /
   `~/.config/OpenSAK Project` folder / macOS `.plist` file.
5. ✅ Run your new OpenSAK version — it will recreate what it needs, or pick
   up your existing database if you kept it.

If anything looks different on your system than described here — for
example, you changed the install or database folder during the welcome
wizard — check **Settings → Advanced** in the app first; it always shows
your *actual* current paths.
