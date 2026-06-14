"""tests/unit-tests/test_app.py — application bootstrap (app.py)."""

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
                            finish=lambda w: None))
    monkeypatch.setattr(appmod, "_migrate_legacy_db", lambda: None)
    monkeypatch.setattr("opensak.gui.theme.apply_theme", lambda app: None)
    monkeypatch.setattr("opensak.config.get_language", lambda: "en")
    monkeypatch.setattr("opensak.lang.load_language", lambda lang: None)
    monkeypatch.setattr("opensak.db.manager.get_db_manager",
                        lambda: SimpleNamespace(ensure_active_initialised=lambda: None))

    class FakeWindow:
        def show(self):
            pass
    monkeypatch.setattr("opensak.gui.mainwindow.MainWindow", FakeWindow)

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
    assert exc.value.code == 0
