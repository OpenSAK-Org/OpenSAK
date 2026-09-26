# tests/conftest.py — shared fixtures for OpenSAK tests.

import os

# QtWebEngine's Chromium stack crashes the headless process (SIGTRAP); force native
# widgets and keep stray WebEngine views off the GPU, before any widget is built.
os.environ.setdefault("OPENSAK_DISABLE_WEBENGINE", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-software-rasterizer --disable-gpu-compositing",
)

import pytest
from opensak.db.database import init_db, make_session
from opensak.db.models import Cache


@pytest.fixture(autouse=True)
def _isolated_app_paths(tmp_path, monkeypatch):
    """
    Isolate the process-global singletons in settings_store, logger og
    db.manager fra de RIGTIGE bruger-stier (%APPDATA%/opensak,
    ~/.config/opensak, ~/Library/Application Support/opensak osv.).

    Issue #829: uden denne fixture er isolation kun sat op pr. testfil
    (se test_logger.py, test_welcome_wizard.py, test_db_manager.py). En
    test der rammer disse singletons UDEN lokal isolation (direkte eller
    indirekte via config.py/app.py) læser/skriver derfor stille den
    faktiske installation på udviklerens/CI-maskinen — det var root
    cause bag den korrupte fil, der udløste #828 i praksis.

    Function-scoped (ikke session-scoped) med sin egen tmp_path pr. test,
    så to tests der begge skriver bootstrap-/settings-data ikke kan
    kollidere med hinanden via en delt tmp-mappe.

    Denne fixture er et sikkerhedsnet UNDER de eksisterende per-fil
    fixtures (som stadig kører oveni og typisk går et niveau dybere,
    fx ved at mocke en fuld SettingsStore) — den fanger de tests, der
    IKKE selv har lokal isolation sat op.

    VIGTIGT: patcher de underliggende inputs (Path.home(), APPDATA/XDG-
    miljøvariabler) — IKKE selve _bootstrap_path()/_default_install_dir()
    funktionerne. TestPlatformSpecificPaths og TestMigrateMacosDefaultPaths
    i test_settings_store.py tester netop disse to funktioners interne
    platform-forgrening (macOS vs. Linux vs. Windows/MSIX), så en fixture
    der erstatter funktionerne selv ville gøre de tests meningsløse (og gør
    det reelt — se historik for denne fixture). Tests, der selv patcher
    Path.home/os.name/sys.platform for at simulere en anden platform, lægger
    sig oveni denne fixtures patch uden konflikt (samme monkeypatch-instans,
    seneste kald vinder for resten af testen).
    """
    import opensak.db.manager as dbmanager
    import opensak.logger as logmod
    import opensak.settings_store as ss

    # Ikke pre-oprettet: nogle tests (fx test_gives_up_after_all_retries_...)
    # tjekker at tmp_path er helt tom efter en operation. Rigtig kode
    # opretter selv mapper via mkdir(parents=True, exist_ok=True) når den
    # rent faktisk skal skrive noget.
    fake_home = tmp_path / "home"
    monkeypatch.setattr(ss.Path, "home", lambda: fake_home)
    monkeypatch.setenv("APPDATA", str(fake_home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / ".local" / "share"))
    ss.reset_store()

    # Issue #906: Qt cacher sin config-mappe pr. proces og respekterer derfor
    # IKKE den monkeypatchede XDG_CONFIG_HOME ovenfor — uden denne patch
    # ville en purge-test rydde udviklerens RIGTIGE QSettings-fil
    # (~/.config/OpenSAK Project/OpenSAK.conf / registreringsdatabasen).
    import opensak.paths as paths_mod
    fake_qsettings = fake_home / ".config" / "OpenSAK Project" / "OpenSAK.conf"
    monkeypatch.setattr(paths_mod, "qsettings_location", lambda: str(fake_qsettings))

    logmod.reset_logging()

    monkeypatch.setattr(dbmanager, "_manager", None)

    yield

    # Sikkerhedsnet: nulstil igen efter testen, så en evt. singleton
    # oprettet sent i teardown (fx via en finalizer) ikke lækker videre
    # til NÆSTE tests opsætning.
    ss.reset_store()
    logmod.reset_logging()
    monkeypatch.setattr(dbmanager, "_manager", None)


@pytest.fixture(autouse=True)
def _no_network_update_check(monkeypatch):
    """Stub the GitHub update check offline so no test ever opens a socket.

    The real urlopen() in a QThread can't be interrupted by quit(), so on CI it
    blocks in getaddrinfo and aborts the process at teardown (SIGABRT / exit 134).
    """
    monkeypatch.setattr("opensak.updater.fetch_latest_release", lambda: None)


@pytest.fixture(scope="module")
def tmp_db(tmp_path_factory):
    # Create a fresh SQLite DB in a temp directory for a test module.
    db_path = tmp_path_factory.mktemp("data") / "test.db"
    init_db(db_path=db_path)
    return db_path


@pytest.fixture
def db_session(tmp_path):
    # Fresh isolated DB + bare Session for each test. Caller must commit.
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path)
    session = make_session()
    yield session
    session.close()


@pytest.fixture
def make_cache():
    # Return a factory that builds Cache instances with sensible defaults.
    def _factory(gc_code: str = "GC12345", **kwargs) -> Cache:
        defaults = dict(
            name="Test Cache",
            cache_type="Traditional Cache",
            latitude=55.0,
            longitude=12.0,
        )
        defaults.update(kwargs)
        return Cache(gc_code=gc_code, **defaults)
    return _factory
