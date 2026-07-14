# Installation

OpenSAK runs on Linux, Windows and macOS. Choose the method that fits your platform.

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

## Linux — Automatic installer (recommended)

The easiest way to install OpenSAK on Linux. The script installs all dependencies, downloads OpenSAK, and creates a shortcut in your application menu automatically.

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

### Linux — AppImage

Alternatively, download the latest `.AppImage` from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases):

```bash
chmod +x OpenSAK-*.AppImage
./OpenSAK-*.AppImage
```

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

## Windows — Standalone installer (recommended)

Download the latest **OpenSAK-Windows.zip** from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases), unzip it, and double-click `OpenSAK.exe` — no Python or Git required.

> Windows SmartScreen may warn you on first launch. Click **More info → Run anyway**.

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
| Apple Silicon (M1/M2/M3/M4) | `OpenSAK-macOS-arm64.dmg` |
| Intel | `OpenSAK-macOS-x86_64.dmg` |

> **Not sure which Mac you have?** Click the Apple menu → **About This Mac**. If it says "Apple M1/M2/M3/M4" choose **arm64**. If it says "Intel" choose **x86_64**.

Open the `.dmg` and drag OpenSAK to your Applications folder.

> On first launch, macOS may block the app because it is not signed with an Apple Developer certificate. Right-click → **Open** to bypass this warning.

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
- Configuration directory — ensures OpenSAK can write to `~/.opensak`

---

## Updating to the latest version

### If you used the automatic Linux installer or manual source install

```bash
cd ~/opensak
git pull origin main
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\activate       # Windows
pip install -e .
python run.py
```

### If you downloaded a release (.exe / .dmg / AppImage)

Download the latest version from the [Releases page](https://github.com/OpenSAK-Org/OpenSAK/releases) and replace your existing installation.

---

## Uninstalling

OpenSAK does not use a system installer. Remove it by deleting the following files manually.

> **Tip:** Your geocaching databases are stored in the data folder below. Back it up before deleting if you want to keep your data.

### Linux

```bash
rm -rf ~/.local/share/opensak/
rm -rf ~/opensak/
rm -f ~/.local/share/applications/opensak.desktop
rm -f ~/Desktop/opensak.desktop
```

### Windows

```cmd
rmdir /s /q "%APPDATA%\opensak"
```
Then delete the folder where you placed `OpenSAK.exe`.

### macOS

```bash
rm -rf ~/Library/Application\ Support/opensak/
```
Then drag the OpenSAK app from Applications to Trash.
