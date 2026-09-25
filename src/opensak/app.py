"""
app.py — Application entry point for OpenSAK.
"""

import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING
from opensak.gui.icon import get_app_icon

if TYPE_CHECKING:
    from PySide6.QtWidgets import QSplashScreen, QApplication
    from PySide6.QtCore import QTranslator

logger = logging.getLogger(__name__)

# Holder en reference til den installerede Qt-oversætter, så den ikke
# bliver garbage collected efter main() (QApplication.installTranslator()
# tager kun en svag reference internt i Qt).
_qt_translator: "QTranslator | None" = None

# OpenSAK's sprogkoder følger ikke altid Qt's egne locale-koder.
# "se" bruges i AVAILABLE_LANGUAGES for svensk, men Qt's oversættelsesfiler
# hedder qtbase_sv.qm (ISO 639-1 for svensk er "sv", ikke "se").
_QT_LOCALE_OVERRIDES: dict[str, str] = {
    "se": "sv",
    # Schweizertysk har ingen egen qtbase_de_CH.qm — brug den tyske.
    "de_CH": "de",
}


def _install_qt_translator(app: "QApplication", lang_code: str) -> None:
    """
    Installer Qt's indbyggede oversættelse (qtbase_xx.qm) for standard
    dialog-knapper (Close/OK/Cancel osv. fra QDialogButtonBox.StandardButton),
    som IKKE går gennem OpenSAK's eget sprogsystem (opensak.lang).

    Uden dette vises fx "Close"-knappen i Koordinaten-Konverter altid på
    engelsk, selv når resten af UI'et er oversat (#issue: German Close btn).

    Fejler stille (logger blot en advarsel) hvis der ikke findes en
    matchende qtbase_xx.qm — appen fungerer stadig fint, knappen falder
    bare tilbage til engelsk, som den gjorde før dette fix.
    """
    global _qt_translator
    from PySide6.QtCore import QTranslator, QLibraryInfo

    qt_locale = _QT_LOCALE_OVERRIDES.get(lang_code, lang_code)
    qm_name = f"qtbase_{qt_locale}"

    # Kandidat-mapper i prioriteret rækkefølge. QLibraryInfo's egen sti er
    # korrekt ved kørsel fra source, men er ikke altid pålidelig i en
    # PyInstaller-frosset build (afhænger af hvordan PySide6-hooket
    # placerer Qt/translations i bundlet) — så vi tjekker også et par
    # kendte bundle-layouts som fallback. Se opensak.spec for hvor
    # qtbase_*.qm faktisk bliver lagt ved packaging.
    candidate_dirs = [QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)]
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        candidate_dirs += [
            str(meipass / "PySide6" / "Qt" / "translations"),
            str(meipass / "Qt" / "translations"),
        ]

    translator = QTranslator()
    loaded = False
    for tdir in candidate_dirs:
        if translator.load(qm_name, tdir):
            loaded = True
            break

    if loaded:
        app.installTranslator(translator)
        _qt_translator = translator  # bevar reference
        logger.info(
            "startup: installed Qt base translator for '%s' (qtbase_%s)",
            lang_code, qt_locale,
        )
    else:
        logger.warning(
            "startup: no Qt base translator found for '%s' (qtbase_%s) — "
            "standard dialog buttons will show in English",
            lang_code, qt_locale,
        )

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

    # Initialiser logging-systemet FØRST (issue #232) — så vi kan logge
    # alt der sker under resten af opstarten, inkl. migration og wizard.
    from opensak.logger import setup_logging
    setup_logging()
    logger.info("startup: main() begin, version=%s", _ver)
    _startup_t0 = time.monotonic()

    # Kør macOS-sti-migreringen (issue #825) FØR NOGET ANDET rører
    # settings-systemet — inklusive apply_theme() nedenfor.
    #
    # Issue #878: denne migrering lå tidligere efter apply_theme(app), som
    # så ud til at være uden afhængighed af den (ren UI-palette-opsætning).
    # Men apply_theme() slår internt "display.theme" op via
    # get_settings().theme → get_store() — og SettingsStore cacher BÅDE sin
    # opløste fil-sti (_path) OG sit indlæste indhold (_data) permanent ved
    # første tilgang (_load() kører aldrig igen, jf. dens egen
    # "if self._data is not None: return"-guard). Når apply_theme() kørte
    # FØR migreringen, ramte denne første tilgang en sti hvor den endnu
    # ikke-migrerede opensak.json ikke fandtes endnu — singletonen cachede
    # `{}` for resten af processens levetid, og fortsatte med at gøre det
    # selvom migreringen et øjeblik senere fysisk flyttede den rigtige fil
    # ind på præcis den sti. Den efterfølgende migrate_from_qsettings()
    # (og enhver anden settings-læsning under resten af opstarten) arvede
    # dermed en permanent tom store, uanset hvad der reelt lå på disken —
    # den synlige effekt var at brugerens database, brugernavn og
    # hjem-koordinater så ud til at være forsvundet efter opdatering.
    #
    # Ingen effekt på Windows/Linux (funktionen er selv et no-op der).
    from opensak.settings_store import (
        get_store, migrate_from_qsettings, migrate_macos_default_paths,
        is_first_run, mark_wizard_completed, repair_corrupted_bool_keys,
    )
    migrate_macos_default_paths()

    # Anvend Fusion stil + platform-tilpasset font + brugertema
    # (gøres FØR nogen vinduer oprettes så alt arver paletten korrekt).
    # Skal køre EFTER migrate_macos_default_paths() ovenfor — se
    # kommentaren der.
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

    # Indlæs sprog FØR noget UI oprettes
    splash_msg("Indlæser sprog...")
    # Kør én-gangs migration fra QSettings → opensak.json (issue #209)
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
    _install_qt_translator(app, get_language())

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
    logger.info("startup: checking legacy db (+%.2fs)", time.monotonic() - _startup_t0)
    _migrate_legacy_db()

    # Initialiser database manager — åbner samme DB som sidst
    splash_msg("Indlæser database...")
    logger.info("startup: loading database (+%.2fs)", time.monotonic() - _startup_t0)
    from opensak.db.manager import get_db_manager
    manager = get_db_manager()
    try:
        manager.ensure_active_initialised()
    except Exception as e:
        # Issue #738/#723: previously uncaught — a migration failure here
        # (whether the already-fixed #723 bug, or anything else specific
        # to a user's database) propagated all the way out of app startup.
        # In a PyInstaller-bundled build with no console window that
        # traceback went nowhere, so the app just looked like it hung on
        # the splash screen. switch_to()/init_db() already logs the full
        # traceback (now preserved across a restart via #737's rotation);
        # here we additionally stop and tell the user directly, rather
        # than pressing on into a MainWindow with no valid database
        # engine, which would likely fail again in some more confusing
        # way further down.
        logger.exception("startup: failed to initialise active database")
        splash.hide()
        if _report_app_control_block(e):
            sys.exit(1)
        from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
        from opensak.config import get_log_path
        from opensak.lang import tr
        QMessageBox.critical(
            None, tr("startup_db_error_title"),
            tr("startup_db_error_msg", error=str(e), path=str(get_log_path())),
        )
        sys.exit(1)
    logger.info("startup: database loaded (+%.2fs)", time.monotonic() - _startup_t0)

    # Opret hovedvindue
    splash_msg("Starter OpenSAK...")
    logger.info("startup: building main window (+%.2fs)", time.monotonic() - _startup_t0)
    try:
        from opensak.gui.mainwindow import MainWindow
        window = MainWindow()
    except ImportError as e:
        # Issue #904: en blokeret DLL kan også dukke op her (fx lxml eller
        # shapely). Kun den særlige App Control-situation håndteres; alt
        # andet propagerer som hidtil.
        logger.exception("startup: failed to build main window")
        if _report_app_control_block(e):
            splash.hide()
            sys.exit(1)
        raise
    logger.info("startup: main window built (+%.2fs total)", time.monotonic() - _startup_t0)

    # Vent til cache-tabellen er loadet før splash lukkes
    def _close_splash():
        splash.finish(window)

    from PySide6.QtCore import QTimer
    QTimer.singleShot(400, _close_splash)

    window.show()
    sys.exit(app.exec())


def _report_app_control_block(exc: BaseException) -> bool:
    """
    Issue #904: hvis *exc* skyldes Windows Smart App Control, vis en besked
    der forklarer det og peger mod Microsoft Store-versionen, og returnér
    True. Ellers False — kalderen viser så sin almindelige fejlbesked.

    Store-versionen (MSIX) er signeret af Microsoft og blokeres ikke; skulle
    den alligevel ramme noget lignende, giver det ingen mening at henvise til
    sig selv, så dér vises den almindelige besked.
    """
    from opensak.app_control import is_app_control_block
    from opensak.msix import is_msix_packaged
    if not is_app_control_block(exc) or is_msix_packaged():
        return False
    logger.error("startup: blocked by Windows Smart App Control (issue #904)")
    from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
    from opensak.config import get_log_path
    from opensak.lang import tr
    QMessageBox.critical(
        None, tr("startup_app_control_title"),
        tr("startup_app_control_msg", error=str(exc), path=str(get_log_path())),
    )
    return True


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    main()
