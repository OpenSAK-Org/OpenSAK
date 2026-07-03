"""
app.py — Application entry point for OpenSAK.
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from opensak.gui.icon import get_app_icon

if TYPE_CHECKING:
    from PySide6.QtWidgets import QSplashScreen

def _migrate_legacy_db() -> None:
    """
    Migrer gammel opensak.db til Default.db.

    Scenarier:
    - opensak.db eksisterer, Default.db ikke → omdøb
    - Begge eksisterer → slet den tomme Default.db, behold opensak.db
    - Kun Default.db → ingenting at gøre
    """
    from opensak.config import get_app_data_dir
    app_dir = get_app_data_dir()
    legacy = app_dir / "opensak.db"
    default = app_dir / "Default.db"

    if legacy.exists() and not default.exists():
        # Simpel migration
        legacy.rename(default)
        print(f"Migrerede {legacy.name} → {default.name}")

    elif legacy.exists() and default.exists():
        # Begge eksisterer — tjek hvilken der er størst (har data)
        legacy_size = legacy.stat().st_size
        default_size = default.stat().st_size
        if legacy_size > default_size:
            # opensak.db har data, Default.db er tom — erstat
            default.unlink()
            # Slet også WAL/SHM filer for Default hvis de findes
            for ext in [".db-shm", ".db-wal"]:
                p = app_dir / f"Default{ext}"
                if p.exists():
                    p.unlink()
            legacy.rename(default)
            print(f"Migrerede {legacy.name} → {default.name} (erstattede tom Default.db)")
        else:
            # Default.db har data — slet den tomme opensak.db
            legacy.unlink()
            for ext in [".db-shm", ".db-wal"]:
                p = app_dir / f"opensak{ext}"
                if p.exists():
                    p.unlink()
            print(f"Slettede tom {legacy.name}")


def _make_splash(app) -> "QSplashScreen":
    """Opret og vis en splash screen med OpenSAK navn og loading tekst."""
    from PySide6.QtWidgets import QSplashScreen
    from PySide6.QtGui import QPixmap, QPainter, QColor, QFont
    from PySide6.QtCore import Qt
    from opensak import __version__

    # Tegn splash pixmap programmatisk — ingen billedfil nødvendig
    W, H = 420, 220
    pix = QPixmap(W, H)
    pix.fill(QColor("#1e2a3a"))

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Baggrundsgradient-linje i toppen
    painter.fillRect(0, 0, W, 5, QColor("#4a9eff"))

    # Titel
    font_title = QFont("Sans Serif", 28, QFont.Weight.Bold)
    painter.setFont(font_title)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(0, 0, W, 100, Qt.AlignmentFlag.AlignCenter, "OpenSAK")

    # Undertitel
    font_sub = QFont("Sans Serif", 10)
    painter.setFont(font_sub)
    painter.setPen(QColor("#7ab8f5"))
    painter.drawText(0, 85, W, 40, Qt.AlignmentFlag.AlignCenter,
                     "Open Source Swiss Army Knife")

    # Versionsnummer
    font_ver = QFont("Sans Serif", 9)
    painter.setFont(font_ver)
    painter.setPen(QColor("#4a9eff"))
    painter.drawText(0, 120, W, 30, Qt.AlignmentFlag.AlignCenter,
                     f"v{__version__}")

    # Loading tekst placeholder (opdateres via showMessage)
    painter.end()

    splash = QSplashScreen(pix, Qt.WindowType.WindowStaysOnTopHint)
    splash.setFont(QFont("Sans Serif", 9))
    splash.show()
    app.processEvents()
    return splash


def _apply_version_override() -> None:
    """Handle --version[=X] from sys.argv.

    --version          → print current version and exit
    --version=1.2.3    → run the code from git tag v1.2.3 via a worktree
                         subprocess; if the tag does not exist, falls back
                         to running the current checkout (main)
    """
    import subprocess
    import tempfile
    import opensak
    from pathlib import Path

    args = sys.argv[1:]
    for arg in args:
        if arg == "--version":
            print(opensak.__version__)
            sys.exit(0)

        if arg.startswith("--version="):
            version = arg[len("--version="):]
            tag = f"v{version}"

            # Locate the git repo from the current working directory
            root_result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True,
            )
            if root_result.returncode != 0:
                print("Error: not inside a git repository.", file=sys.stderr)
                sys.exit(1)
            repo_root = Path(root_result.stdout.strip())

            # Validate tag exists
            check = subprocess.run(
                ["git", "tag", "-l", tag],
                capture_output=True, text=True, cwd=repo_root,
            )
            if not check.stdout.strip():
                print(f"Error: version '{version}' not found. Use 'git tag -l' to see available releases.", file=sys.stderr)
                sys.exit(1)

            # Run that version in an isolated worktree
            with tempfile.TemporaryDirectory() as tmpdir:
                subprocess.run(
                    ["git", "worktree", "add", "--detach", tmpdir, tag],
                    cwd=repo_root, check=True, capture_output=True,
                )
                try:
                    other_args = [a for a in sys.argv[1:] if not a.startswith("--version")]
                    subprocess.run(
                        [sys.executable, str(Path(tmpdir) / "run.py")] + other_args,
                        cwd=tmpdir,
                    )
                finally:
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", tmpdir],
                        cwd=repo_root, capture_output=True,
                    )
            sys.exit(0)


def main() -> None:
    import os
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt

    _apply_version_override()

    # Disable GPU acceleration for QtWebEngine — prevents black rendering
    # on Windows systems where GPU/OpenGL drivers are incomplete or virtual.
    # This affects map and description panels rendered via QWebEngineView.
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-software-rasterizer"
    )

    app = QApplication(sys.argv)
    app.setWindowIcon(get_app_icon())
    app.setApplicationName("OpenSAK")
    from opensak import __version__ as _ver
    app.setApplicationVersion(_ver)
    app.setOrganizationName("OpenSAK Project")
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    # Anvend Fusion stil + platform-tilpasset font + brugertema
    # (gøres FØR nogen vinduer oprettes så alt arver paletten korrekt)
    from opensak.gui.theme import apply_theme
    apply_theme(app)

    # Vis splash screen øjeblikkeligt
    splash = _make_splash(app)

    def splash_msg(text: str) -> None:
        from PySide6.QtGui import QColor
        from PySide6.QtCore import Qt
        splash.showMessage(
            text,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            QColor("#a0c8ff"),
        )
        app.processEvents()

    # Initialiser logging-systemet FØRST (issue #232) — så vi kan logge
    # alt der sker under resten af opstarten, inkl. migration og wizard.
    from opensak.logger import setup_logging
    setup_logging()

    # Indlæs sprog FØR noget UI oprettes
    splash_msg("Indlæser sprog...")
    # Kør én-gangs migration fra QSettings → opensak.json (issue #209)
    from opensak.settings_store import (
        get_store, migrate_from_qsettings, is_first_run,
        mark_wizard_completed, repair_corrupted_bool_keys,
    )
    did_migrate = migrate_from_qsettings(get_store())
    # Reparér evt. boolean-værdier korrumperet af en tidligere bug i
    # _flush() — kører altid, uafhængigt af om migration var nødvendig,
    # så også brugere af tidligere 1.14.0-beta-builds får det rettet.
    repair_corrupted_bool_keys(get_store())
    # Eksisterende installation (migreret fra QSettings) → wizard er ikke nødvendig
    if did_migrate:
        mark_wizard_completed()
    from opensak.config import get_language
    from opensak.lang import load_language
    load_language(get_language())

    # Vis velkomst-wizard ved første opstart (issue #210)
    if is_first_run():
        splash.hide()
        from opensak.gui.dialogs.welcome_wizard import WelcomeWizard
        wizard = WelcomeWizard()
        wizard.exec()
        # Genindlæs sprog hvis det blev ændret i wizard
        load_language(get_language())
        splash.show()
        app.processEvents()

    # Migrer gammel database hvis nødvendigt
    splash_msg("Kontrollerer database...")
    _migrate_legacy_db()

    # Seed reverse-geocoding baseline (country/state) on first run — no-op
    # once boundaries.db exists in the app-data dir (issue #60)
    splash_msg("Forbereder geografiske data...")
    from opensak.geo.store import ensure_baseline_seeded
    ensure_baseline_seeded()

    # Initialiser database manager — åbner samme DB som sidst
    splash_msg("Indlæser database...")
    from opensak.db.manager import get_db_manager
    manager = get_db_manager()
    manager.ensure_active_initialised()

    # Opret hovedvindue
    splash_msg("Starter OpenSAK...")
    from opensak.gui.mainwindow import MainWindow
    window = MainWindow()

    # Vent til cache-tabellen er loadet før splash lukkes
    def _close_splash():
        splash.finish(window)

    from PySide6.QtCore import QTimer
    QTimer.singleShot(400, _close_splash)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    main()
