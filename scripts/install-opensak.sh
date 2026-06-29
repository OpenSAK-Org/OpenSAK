#!/bin/bash
# ==============================================================
# OpenSAK — Linux installationsscript
# Testet på: Ubuntu 20.04+, Linux Mint 20+, Debian 11+, openSUSE Tumbleweed
#
# Brug:
#   chmod +x install-opensak.sh
#   ./install-opensak.sh
# ==============================================================

set -e  # Afbryd ved fejl

# ---- Farver til terminal output ----
GROEN='\033[0;32m'
GUL='\033[1;33m'
ROED='\033[0;31m'
BLA='\033[0;34m'
NC='\033[0m'  # Ingen farve

# ---- Linux Distributions ---
DEBIAN="command -v apt"
SUSE="command -v zypper"

ok()   { echo -e "${GROEN}✓${NC} $1"; }
info() { echo -e "${BLA}→${NC} $1"; }
advar(){ echo -e "${GUL}⚠${NC} $1"; }
fejl() { echo -e "${ROED}✗ FEJL:${NC} $1"; exit 1; }

echo ""
echo -e "${BLA}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLA}║        OpenSAK — Installationsscript     ║${NC}"
echo -e "${BLA}║   Open Source geocaching styringsværktøj ║${NC}"
echo -e "${BLA}╚══════════════════════════════════════════╝${NC}"
echo ""

# ==============================================================
# TRIN 1: Tjek styresystem
# ==============================================================
info "Tjekker system..."

if [ ! -f /etc/os-release ]; then
    fejl "Kan ikke identificere Linux-distribution"
fi

. /etc/os-release
ok "Kører på: $PRETTY_NAME"

# ==============================================================
# TRIN 2: Tjek og installer system-afhængigheder
# ==============================================================
info "Tjekker system-afhængigheder..."

MANGLER=()

if ! command -v python3 &>/dev/null; then MANGLER+=("python3"); fi
if ! command -v git &>/dev/null; then MANGLER+=("git"); fi
if ! python3 -c "import venv" &>/dev/null 2>&1; then MANGLER+=("python3-venv"); fi
if $DEBIAN &>/dev/null; then
	if ! dpkg -l libxcb-cursor0 &>/dev/null 2>&1; then MANGLER+=("libxcb-cursor0"); fi
	if ! dpkg -l python3-pip &>/dev/null 2>&1; then MANGLER+=("python3-pip"); fi
elif $SUSE &>/dev/null; then
	if ! rpm -q libxcb-cursor0 &>/dev/null 2>&1; then MANGLER+=("libxcb-cursor0"); fi
	if ! rpm -q python3-pip &>/dev/null 2>&1; then MANGLER+=("python3-pip"); fi
fi

if [ ${#MANGLER[@]} -gt 0 ]; then
    advar "Følgende pakker mangler og installeres nu: ${MANGLER[*]}"
    echo "Dette kræver din adgangskode (sudo):"
    if $DEBIAN &>/dev/null; then
	    sudo apt-get update -qq
	    sudo apt-get install -y "${MANGLER[@]}"
    elif $SUSE &>/dev/null; then 
	    sudo zypper refresh 
	    sudo zypper install -y "${MANGLER[@]}"
    fi
    ok "System-pakker installeret"
else
    ok "Alle system-afhængigheder er på plads"
fi

# Tjek Python version
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    fejl "OpenSAK kræver Python 3.10+. Du har Python $PY_VERSION"
fi
ok "Python $PY_VERSION — OK"

# ==============================================================
# TRIN 3: Klon eller opdater repository
# ==============================================================
INSTALL_DIR="$HOME/opensak"

if [ -d "$INSTALL_DIR/.git" ]; then
    info "OpenSAK er allerede installeret — opdaterer..."
    cd "$INSTALL_DIR"
    git pull origin main
    ok "Opdateret til nyeste version"
else
    info "Downloader OpenSAK fra GitHub..."
    git clone https://github.com/AgreeDK/opensak.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    ok "OpenSAK downloadet til $INSTALL_DIR"
fi

# ==============================================================
# TRIN 4: Opret virtual environment og installer Python-pakker
# ==============================================================
info "Opsætter Python miljø..."

if [ ! -d "$INSTALL_DIR/.venv" ]; then
    python3 -m venv "$INSTALL_DIR/.venv"
    ok "Virtual environment oprettet"
else
    ok "Virtual environment findes allerede"
fi

source "$INSTALL_DIR/.venv/bin/activate"

info "Installerer Python-pakker (kan tage et par minutter)..."
pip install --upgrade pip -q
pip install -e "$INSTALL_DIR" -q
ok "Python-pakker installeret"

# ==============================================================
# TRIN 5: Opret start-script
# ==============================================================
info "Opretter start-script..."

cat > "$INSTALL_DIR/start-opensak.sh" << 'STARTSCRIPT'
#!/bin/bash
# OpenSAK start-script — kør dette for at starte programmet
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
source "$SCRIPT_DIR/.venv/bin/activate"
cd "$SCRIPT_DIR"
python run.py "$@"
STARTSCRIPT

chmod +x "$INSTALL_DIR/start-opensak.sh"
ok "Start-script oprettet: $INSTALL_DIR/start-opensak.sh"

# ==============================================================
# TRIN 6: Opret .desktop fil (ikon i applikationsmenuen)
# ==============================================================
info "Opretter genvej i applikationsmenuen..."

DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"

cat > "$DESKTOP_DIR/opensak.desktop" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=OpenSAK
GenericName=Geocaching styringsværktøj
Comment=Open Source geocaching management tool — efterfølger til GSAK
Exec=$INSTALL_DIR/start-opensak.sh
Icon=applications-games
Terminal=false
Categories=Utility;GPS;Science;
Keywords=geocaching;gps;gsak;cache;
StartupNotify=true
DESKTOP

chmod +x "$DESKTOP_DIR/opensak.desktop"

# Opdater desktop database
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

ok "Genvej oprettet i applikationsmenuen"

# ==============================================================
# TRIN 7: Tilbyd at oprette genvej på skrivebordet
# ==============================================================
SKRIVEBORD=""
# Find skrivebord (virker på dansk og engelsk Linux)
for kandidat in "$HOME/Skrivebord" "$HOME/Desktop"; do
    if [ -d "$kandidat" ]; then
        SKRIVEBORD="$kandidat"
        break
    fi
done

if [ -n "$SKRIVEBORD" ]; then
    echo ""
    read -p "Vil du have et ikon på skrivebordet? [J/n]: " SVAR
    SVAR=${SVAR:-J}
    if [[ "$SVAR" =~ ^[JjYy]$ ]]; then
        cp "$DESKTOP_DIR/opensak.desktop" "$SKRIVEBORD/opensak.desktop"
        # Giv tilladelse (kræves på nogle distros)
        gio set "$SKRIVEBORD/opensak.desktop" metadata::trusted true 2>/dev/null || \
        chmod +x "$SKRIVEBORD/opensak.desktop" 2>/dev/null || true
        ok "Ikon oprettet på skrivebordet"
    fi
fi

# ==============================================================
# FÆRDIG
# ==============================================================
echo ""
echo -e "${GROEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GROEN}║      ✓ OpenSAK er installeret!           ║${NC}"
echo -e "${GROEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo "  Start fra terminal:     cd ~/opensak && ./start-opensak.sh"
echo "  Start fra menuen:       Find 'OpenSAK' i din applikationsmenu"
if [ -n "$SKRIVEBORD" ] && [[ "$SVAR" =~ ^[JjYy]$ ]]; then
echo "  Start fra skrivebordet: Dobbeltklik på OpenSAK ikonet"
fi
echo ""
echo "  Opdater til ny version: cd ~/opensak && git pull && pip install -e ."
echo ""
echo "  Fejl og forslag:        https://github.com/AgreeDK/opensak/issues"
echo ""

# Tilbyd at starte nu
read -p "Vil du starte OpenSAK nu? [J/n]: " START
START=${START:-J}
if [[ "$START" =~ ^[JjYy]$ ]]; then
    info "Starter OpenSAK..."
    "$INSTALL_DIR/start-opensak.sh" &
fi
