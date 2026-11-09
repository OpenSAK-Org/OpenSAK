# tests/unit-tests/test_app.py — application bootstrap (app.py).

import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QSplashScreen

import opensak.app as appmod


# ── _migrate_legacy_db ────────────────────────────────────────────────────────

class TestMigrateLegacyDb:
    @pytest.fixture
    def app_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("opensak.config.get_app_data_dir", lambda: tmp_path)
        return tmp_path

    def test_legacy_only_renamed(self, app_dir):
        (app_dir / "opensak.db").write_text("data")
        appmod._migrate_legacy_db()
        assert not (app_dir / "opensak.db").exists()
        assert (app_dir / "Default.db").read_text() == "data"

    def test_both_legacy_bigger_replaces_default(self, app_dir):
        (app_dir / "opensak.db").write_text("lots of data here")
        (app_dir / "Default.db").write_text("x")
        (app_dir / "Default.db-wal").write_text("w")
        (app_dir / "Default.db-shm").write_text("s")
        appmod._migrate_legacy_db()
        assert not (app_dir / "opensak.db").exists()
        assert (app_dir / "Default.db").read_text() == "lots of data here"
        assert not (app_dir / "Default.db-wal").exists()

    def test_both_default_bigger_deletes_legacy(self, app_dir):
        (app_dir / "opensak.db").write_text("x")
        (app_dir / "opensak.db-wal").write_text("w")
        (app_dir / "Default.db").write_text("lots of data here")
        appmod._migrate_legacy_db()
        assert not (app_dir / "opensak.db").exists()
        assert not (app_dir / "opensak.db-wal").exists()
        assert (app_dir / "Default.db").read_text() == "lots of data here"

    def test_default_only_noop(self, app_dir):
        (app_dir / "Default.db").write_text("data")
        appmod._migrate_legacy_db()
        assert (app_dir / "Default.db").read_text() == "data"

    def test_neither_noop(self, app_dir):
        appmod._migrate_legacy_db()  # nothing to do, no crash


# ── _make_splash ──────────────────────────────────────────────────────────────

def test_make_splash(qapp):
    splash = appmod._make_splash(qapp)
    try:
        assert isinstance(splash, QSplashScreen)
        assert not splash.pixmap().isNull()
    finally:
        splash.close()


# ── "Using OpenSAK since …" on the splash (#1130) ─────────────────────────────

def _same_image(a, b) -> bool:
    return a.toImage() == b.toImage()


def test_splash_pixmap_without_since_matches_plain_splash(qapp):
    # No date → the splash looks exactly as before the feature.
    assert _same_image(appmod._splash_pixmap(), appmod._splash_pixmap(None))
    assert _same_image(appmod._splash_pixmap(), appmod._splash_pixmap(""))


def test_splash_pixmap_with_since_draws_the_extra_line(qapp):
    plain = appmod._splash_pixmap()
    with_since = appmod._splash_pixmap("Using OpenSAK since 3 May 2026 — day 175")
    assert not with_since.isNull()
    assert (with_since.width(), with_since.height()) == (plain.width(), plain.height())
    assert not _same_image(plain, with_since)


def test_show_using_since_uses_about_text_and_date_format(qapp, monkeypatch):
    from datetime import date, timedelta
    import opensak.lang as lang

    seen = {}

    def fake_tr(key, **kw):
        seen["key"], seen["kw"] = key, kw
        return f"since {kw['date']} day {kw['day']}"

    monkeypatch.setattr(lang, "tr", fake_tr)
    drawn = []
    monkeypatch.setattr(appmod, "_splash_pixmap",
                        lambda since=None: drawn.append(since) or "PIX")
    splash = SimpleNamespace(pixmaps=[])
    splash.setPixmap = splash.pixmaps.append

    first = date.today() - timedelta(days=9)
    appmod._show_using_since_on_splash(splash, first)

    assert seen["key"] == "about_using_since"
    assert seen["kw"]["day"] == 10           # first day is day 1
    assert drawn == [f"since {seen['kw']['date']} day 10"]
    assert splash.pixmaps == ["PIX"]


def test_show_using_since_failure_leaves_splash_unchanged(qapp, monkeypatch):
    from datetime import date
    import opensak.lang as lang

    def broken_tr(key, **kw):
        raise KeyError(key)

    monkeypatch.setattr(lang, "tr", broken_tr)
    splash = SimpleNamespace(pixmaps=[])
    splash.setPixmap = splash.pixmaps.append

    appmod._show_using_since_on_splash(splash, date(2026, 5, 3))  # must not raise

    assert splash.pixmaps == []


def test_show_using_since_on_real_splash(qapp):
    from datetime import date
    splash = appmod._make_splash(qapp)
    try:
        before = splash.pixmap().toImage()
        appmod._show_using_since_on_splash(splash, date(2026, 5, 3))
        assert splash.pixmap().toImage() != before
    finally:
        splash.close()


# ── _apply_version_override ───────────────────────────────────────────────────

class TestVersionOverride:
    def test_no_version_arg_returns(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["opensak"])
        assert appmod._apply_version_override() is None

    def test_plain_version_prints_and_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["opensak", "--version"])
        with pytest.raises(SystemExit) as exc:
            appmod._apply_version_override()
        assert exc.value.code == 0
        import opensak
        assert opensak.__version__ in capsys.readouterr().out

    def test_version_x_not_in_git_repo(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["opensak", "--version=1.2.3"])
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
        with pytest.raises(SystemExit) as exc:
            appmod._apply_version_override()
        assert exc.value.code == 1

    def test_version_x_tag_not_found(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["opensak", "--version=1.2.3"])

        def fake_run(cmd, *a, **k):
            if "rev-parse" in cmd:
                return SimpleNamespace(returncode=0, stdout="/repo\n", stderr="")
            if "tag" in cmd:
                return SimpleNamespace(returncode=0, stdout="", stderr="")  # no tag
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        monkeypatch.setattr("subprocess.run", fake_run)
        with pytest.raises(SystemExit) as exc:
            appmod._apply_version_override()
        assert exc.value.code == 1

    def test_version_x_tag_found_runs_worktree(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["opensak", "--version=1.2.3"])
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(cmd)
            if "rev-parse" in cmd:
                return SimpleNamespace(returncode=0, stdout="/repo\n", stderr="")
            if "tag" in cmd:
                return SimpleNamespace(returncode=0, stdout="v1.2.3\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        monkeypatch.setattr("subprocess.run", fake_run)
        with pytest.raises(SystemExit) as exc:
            appmod._apply_version_override()
        assert exc.value.code == 0
        # worktree add + the version run + worktree remove all dispatched
        assert any("worktree" in c for c in calls)


# ── main (bootstrap smoke, event loop mocked) ─────────────────────────────────

def test_main_smoke(qapp, monkeypatch):
    import PySide6.QtWidgets as W
    import PySide6.QtCore as C

    monkeypatch.setattr(sys, "argv", ["opensak"])
    monkeypatch.setattr(type(qapp), "exec", lambda self: 0)
    monkeypatch.setattr(appmod, "_make_splash",
                        lambda app: SimpleNamespace(
                            showMessage=lambda *a, **k: None,
                            finish=lambda w: None,
                            hide=lambda: None,
                            show=lambda: None))
    monkeypatch.setattr(appmod, "_migrate_legacy_db", lambda: None)
    monkeypatch.setattr("opensak.settings_store.is_first_run", lambda: False)
    monkeypatch.setattr("opensak.gui.theme.apply_theme", lambda app: None)
    monkeypatch.setattr("opensak.config.get_language", lambda: "en")
    monkeypatch.setattr("opensak.lang.load_language", lambda lang: None)
    lock_calls = []
    monkeypatch.setattr("opensak.db.manager.get_db_manager",
                        lambda: SimpleNamespace(
                            ensure_active_initialised=lambda: None,
                            # #937: the GUI holds the active database's lock
                            hold_active_lock=lambda: lock_calls.append("hold"),
                            release_active_lock=lambda: lock_calls.append("release")))

    notices = []

    class FakeWindow:
        def show(self):
            pass

        def show_premigration_backup_notices(self):  # issue #549
            notices.append("premigration")
            raise RuntimeError("broken notice")

        def show_database_fallback_notice(self):  # issue #1045
            notices.append("fallback")
    monkeypatch.setattr("opensak.gui.mainwindow.MainWindow", FakeWindow)
    from opensak import crash_report
    reported = []
    monkeypatch.setattr(crash_report, "_show_exception_dialog", reported.append)
    monkeypatch.setattr(crash_report, "show_previous_crash_notice",
                        lambda parent=None: notices.append("crash"))

    # Reuse the existing QApplication; restore the class *before* the test
    # returns so pytest-qt's post-call _process_events still sees the real one.
    real_qapp_cls = W.QApplication
    real_single = C.QTimer.singleShot
    W.QApplication = lambda *a, **k: qapp
    C.QTimer.singleShot = staticmethod(lambda ms, cb: cb())  # fire splash-close now
    try:
        with pytest.raises(SystemExit) as exc:
            appmod.main()
    finally:
        W.QApplication = real_qapp_cls
        C.QTimer.singleShot = real_single
        crash_report.shutdown()
    # The startup notices run one after another from a single timer; a
    # failing one is reported and doesn't hide the rest.
    assert notices == ["premigration", "fallback", "crash"]
    assert len(reported) == 1 and reported[0].endswith("RuntimeError: broken notice")
    assert lock_calls == ["hold", "release"]
