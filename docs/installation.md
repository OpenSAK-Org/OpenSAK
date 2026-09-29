# Installation

OpenSAK runs on Linux, Windows and macOS. Choose the method that fits your platform.

| Platform | Recommended | Alternatives |
|---|---|---|
| **Windows** | [Microsoft Store](https://apps.microsoft.com/detail/9p4nbmm84h2d) | Direct download (ZIP), from source |
| **macOS** | `.dmg` from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases) | From source |
| **Linux** | AppImage from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases) | Automatic installer script, from source |

---

## System requirements

| Platform | Requirement |
|---|---|
| **Linux** | Ubuntu 20.04+ / Linux Mint 20+ / Debian 11+ |
| **Windows** | Windows 10 or newer |
| **macOS** | macOS 11 (Big Sur) or newer |
| **Python** | 3.11 or newer (source installs only) |
| **Disk space** | ~500 MB (including PySide6) |

---

## Linux — AppImage (recommended)

Download the latest `OpenSAK-…-Linux-x86_64.AppImage` from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases), make it executable and run it:

```bash
chmod +x OpenSAK-*.AppImage
./OpenSAK-*.AppImage
```

On first launch OpenSAK offers to add itself to your application menu. Choosing **Yes** copies the AppImage to `~/.local/bin/OpenSAK.AppImage` and adds a menu entry and icon, so future updates can replace it in place. Changed your mind? The same option is under **Settings → Settings… → Advanced → AppImage**.

### Linux — Automatic installer script

Installs OpenSAK from source. The script installs all dependencies, downloads OpenSAK, and creates a shortcut in your application menu automatically.

```bash
curl -fsSL https://raw.githubusercontent.com/OpenSAK-Org/OpenSAK/main/scripts/install-opensak.sh | bash
```

The installer will:
- Check and install required system packages (`python3`, `git`, `libxcb-cursor0`, etc.)
- Clone the repository to `~/opensak`
- Set up a Python virtual environment
- Create an entry in your application menu
- Optionally create a desktop shortcut
- Offer to start OpenSAK immediately when done

### Linux — Manual install

Use this if the automatic installer does not work on your distribution.

```bash
sudo apt update
sudo apt install git python3 python3-venv python3-pip libxcb-cursor0

cd ~
git clone https://github.com/OpenSAK-Org/opensak.git
cd opensak

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

opensak  # or python run.py
```

---

## Windows — Microsoft Store (recommended)

Install OpenSAK from the [Microsoft Store](https://apps.microsoft.com/detail/9p4nbmm84h2d). It is free, signed by Microsoft, never blocked by Windows' Smart App Control, and the Store keeps it up to date automatically.

### Windows — Direct download

Download the latest `OpenSAK-…-Windows.zip` from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases), unzip it to a folder of your choice, and double-click `OpenSAK.exe` — no Python or Git required.

> This download is not code-signed. Windows SmartScreen may warn you on first launch (click **More info → Run anyway**), and on PCs where **Smart App Control** is switched on, Windows may block OpenSAK from starting at all. OpenSAK then explains what happened; install it from the Microsoft Store instead.

### Windows — Manual install

Install **Python 3.11+** from [python.org](https://www.python.org/downloads/) — check **"Add Python to PATH"** during setup.

Install **Git** from [git-scm.com](https://git-scm.com/download/win), then:

```powershell
cd $env:USERPROFILE
git clone https://github.com/OpenSAK-Org/opensak.git
cd opensak
python -m venv .venv
.venv\Scripts\activate
pip install -e .
opensak  # or python run.py
```

---

## macOS — App bundle (recommended)

Download the correct `.dmg` for your Mac from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases):

| Mac type | File to download |
|----------|-----------------|
| Apple Silicon (M1/M2/M3/M4) | `OpenSAK-…-macOS-arm64.dmg` |
| Intel | `OpenSAK-…-macOS-x86_64.dmg` |

> **Not sure which Mac you have?** Click the Apple menu → **About This Mac**. If it says "Apple M1/M2/M3/M4" choose **arm64**. If it says "Intel" choose **x86_64**.

Open the `.dmg` and drag OpenSAK to your Applications folder. The app is signed and notarized by Apple, so it opens normally on first launch.

### macOS — Manual install

```bash
xcode-select --install   # if not already installed
brew install python git

cd ~
git clone https://github.com/OpenSAK-Org/opensak.git
cd opensak
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
opensak  # or python run.py
```

---

## Diagnostics (opensak-doctor)

If the application fails to start or behaves unexpectedly, run the built-in diagnostic tool:

```bash
opensak-doctor
```

It checks:
- Python version — ensures your system meets the minimum requirement
- Virtual environment — confirms you are running inside a venv
- Dependencies — verifies all required packages are installed
- Configuration directory — ensures OpenSAK can write to its data folder

---

## Updating to the latest version

OpenSAK checks for new versions at startup (turn this off under **Settings → Settings… → Advanced**), and you can check yourself with **Help → Check for updates…**. Tick *Notify me about beta releases too* to be offered betas as well.

| Installed from | How updating works |
|---|---|
| **Microsoft Store** | The Store updates OpenSAK automatically in the background. OpenSAK doesn't show its own update popup. |
| **Windows direct download** | **Download & Install** downloads the new ZIP, verifies its checksum and shows it in File Explorer. Unzip it over your existing folder (or to a new one). |
| **macOS `.dmg`** | **Download & Install** downloads and verifies the new version, replaces the app where it is installed, and closes OpenSAK — open it again to start the new version. If the app's folder isn't writable, the `.dmg` is saved in your Downloads folder instead. |
| **Linux AppImage** | **Upgrade now** downloads, verifies and replaces the AppImage in place. |
| **From source** | `cd ~/opensak && git pull origin main && source .venv/bin/activate && pip install -e .` (use `.venv\Scripts\activate` on Windows) |

Updating never touches your databases or settings.

---

## Uninstalling

Uninstalling removes the program only. Your databases and settings are **never** deleted unless you explicitly choose to remove them.

| Installed from | How to uninstall |
|---|---|
| **Microsoft Store** | Windows **Settings → Apps → Installed apps**, find OpenSAK, choose **Uninstall** |
| **Windows direct download** | Delete the folder you unzipped OpenSAK into |
| **macOS** | **Settings → Settings… → Advanced → Uninstall → Uninstall OpenSAK** — choose *Remove program only* or *Remove program and all data*. OpenSAK moves itself to the Trash. |
| **Linux AppImage** | **Settings → Settings… → Advanced → AppImage → Uninstall OpenSAK**, with the same two choices |
| **From source** | Delete the `~/opensak` folder (and the menu entry the installer script created, if any) |

To remove your data on Windows as well, or to back it up first, use **Help → OpenSAK File Locations…** before uninstalling — it lists every place OpenSAK keeps files, with an **Open folder** button for each. See [Uninstalling OpenSAK](Uninstalling-OpenSAK.md) for details and manual clean-up steps.
