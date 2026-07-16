# tests/unit-tests/test_welcome_wizard.py — WelcomeWizard folder-migration tests.
#
# Issue #562: re-running the wizard (or first-run) with a new install/db
# folder only updated the settings *pointers* — it never moved the actual
# opensak.json or existing database files, so the app silently "lost" all
# settings and databases after the folders were changed.

import json

import pytest

pytest.importorskip("pytestqt")

from opensak import settings_store as ss
from opensak.gui.dialogs import welcome_wizard as ww
from opensak.lang import load_language


@pytest.fixture(autouse=True)
def _language():
    load_language("en")  # tr() needs a language loaded to interpolate text


@pytest.fixture(autouse=True)
def _isolated_bootstrap(tmp_path, monkeypatch):
    """Point bootstrap.json at a temp file so tests never touch the real one."""
    bootstrap = tmp_path / "bootstrap.json"
    monkeypatch.setattr(ss, "_bootstrap_path", lambda: bootstrap)
    monkeypatch.setattr(ss, "_store", None)
    return bootstrap


# ── _move_settings_file ─────────────────────────────────────────────────────

class TestMoveSettingsFile:
    def test_moves_file_when_source_exists_and_dest_does_not(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_settings = old_dir / "opensak.json"
        old_settings.write_text(json.dumps({"gc_username": "Allan"}), encoding="utf-8")

        ww.WelcomeWizard._move_settings_file(old_dir, new_dir)

        assert not old_settings.exists()
        new_settings = new_dir / "opensak.json"
        assert new_settings.exists()
        assert json.loads(new_settings.read_text(encoding="utf-8")) == {"gc_username": "Allan"}

    def test_noop_when_source_missing(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()

        ww.WelcomeWizard._move_settings_file(old_dir, new_dir)  # should not raise

        assert not (new_dir / "opensak.json").exists()

    def test_does_not_overwrite_existing_dest(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "opensak.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
        (new_dir / "opensak.json").write_text(json.dumps({"b": 2}), encoding="utf-8")

        ww.WelcomeWizard._move_settings_file(old_dir, new_dir)

        # Neither file is touched — we refuse to clobber an existing settings file.
        assert json.loads((old_dir / "opensak.json").read_text(encoding="utf-8")) == {"a": 1}
        assert json.loads((new_dir / "opensak.json").read_text(encoding="utf-8")) == {"b": 2}

    def test_move_failure_is_best_effort_and_does_not_raise(self, tmp_path, monkeypatch):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "opensak.json").write_text("{}", encoding="utf-8")

        def _boom(*_a, **_k):
            raise OSError("simulated failure")

        monkeypatch.setattr(ww.shutil, "move", _boom)

        ww.WelcomeWizard._move_settings_file(old_dir, new_dir)  # must not raise


# ── _offer_move_databases ───────────────────────────────────────────────────

class _FakeDbInfo:
    def __init__(self, name, exists=True):
        self.name = name
        self.exists = exists


class _FakeManager:
    def __init__(self, databases, move_result=None):
        self.databases = databases
        self.move_calls = []
        self._move_result = move_result if move_result is not None else []

    def move_databases_to(self, new_dir, delete_originals):
        self.move_calls.append((new_dir, delete_originals))
        return self._move_result


@pytest.fixture
def wizard(qtbot, tmp_path):
    w = ww.WelcomeWizard()
    qtbot.addWidget(w)
    return w


def _click_button_with_text(monkeypatch, tr_key):
    """
    Simulate the user clicking the move-databases box button labelled with
    the given translation key's text.

    Selecting by text rather than add-order index, since Qt reorders a
    QMessageBox's buttons by ButtonRole for display, not by the order
    addButton() was called in.
    """
    from opensak.lang import tr as _tr
    label = _tr(tr_key)

    def _clicked(self):
        for btn in self.buttons():
            if btn.text() == label:
                return btn
        raise AssertionError(f"no button with text {label!r} found")

    monkeypatch.setattr(ww.QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(ww.QMessageBox, "clickedButton", _clicked)


class TestOfferMoveDatabases:
    def test_no_databases_skips_dialog_entirely(self, wizard, monkeypatch, tmp_path):
        manager = _FakeManager(databases=[])
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)

        def _fail_if_called(self):
            raise AssertionError("dialog should not be shown when there are no databases")

        monkeypatch.setattr(ww.QMessageBox, "exec", _fail_if_called)

        wizard._offer_move_databases(tmp_path / "olddb", wizard._default_db)

        assert manager.move_calls == []

    def test_keep_button_moves_without_deleting_originals(self, wizard, monkeypatch, tmp_path):
        manager = _FakeManager(databases=[_FakeDbInfo("Default")])
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        _click_button_with_text(monkeypatch, "settings_move_keep_originals")

        old_dir = tmp_path / "olddb"
        new_dir = tmp_path / "newdb"
        wizard._offer_move_databases(old_dir, new_dir)

        assert manager.move_calls == [(new_dir, False)]

    def test_delete_button_moves_and_deletes_originals(self, wizard, monkeypatch, tmp_path):
        manager = _FakeManager(databases=[_FakeDbInfo("Default")])
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        _click_button_with_text(monkeypatch, "settings_move_delete_originals")

        old_dir = tmp_path / "olddb"
        new_dir = tmp_path / "newdb"
        wizard._offer_move_databases(old_dir, new_dir)

        assert manager.move_calls == [(new_dir, True)]

    def test_skip_button_does_not_move_anything(self, wizard, monkeypatch, tmp_path):
        manager = _FakeManager(databases=[_FakeDbInfo("Default")])
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        _click_button_with_text(monkeypatch, "settings_move_skip")

        wizard._offer_move_databases(tmp_path / "olddb", tmp_path / "newdb")

        assert manager.move_calls == []

    def test_move_errors_are_surfaced_via_warning(self, wizard, monkeypatch, tmp_path):
        manager = _FakeManager(
            databases=[_FakeDbInfo("Default")],
            move_result=["Default.db: target already exists"],
        )
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        _click_button_with_text(monkeypatch, "settings_move_keep_originals")

        warnings = []
        monkeypatch.setattr(
            ww.QMessageBox, "warning",
            staticmethod(lambda *a, **k: warnings.append((a, k))),
        )

        wizard._offer_move_databases(tmp_path / "olddb", tmp_path / "newdb")

        assert len(warnings) == 1


# ── _save_all integration ───────────────────────────────────────────────────

class TestSaveAllMovesFoldersOnChange:
    def test_changing_install_dir_moves_settings_file(self, tmp_path, monkeypatch, qtbot):
        old_install = tmp_path / "old_install"
        new_install = tmp_path / "new_install"
        old_install.mkdir()
        (old_install / "opensak.json").write_text(
            json.dumps({"gc_username": "Allan", "databases.dir": str(old_install)}),
            encoding="utf-8",
        )
        ss.set_install_dir(old_install)

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        w._install_row.set_path(new_install)
        w._db_row.set_path(new_install)  # keep db dir == install dir, out of scope here

        # The database-move dialog is exercised separately above — stub it
        # out here so this test focuses purely on the settings-file move.
        monkeypatch.setattr(w, "_offer_move_databases", lambda *_a, **_k: None)

        w._save_all(use_defaults=False)

        assert not (old_install / "opensak.json").exists()
        moved = json.loads((new_install / "opensak.json").read_text(encoding="utf-8"))
        assert moved.get("gc_username") == "Allan"
        # New store on top of the moved file should also see the old key.
        assert ss.get_store().get("gc_username") == "Allan"

    def test_changing_db_dir_offers_to_move_databases(self, tmp_path, monkeypatch, qtbot):
        install_dir = tmp_path / "install"
        old_db = tmp_path / "old_db"
        new_db = tmp_path / "new_db"
        install_dir.mkdir()
        old_db.mkdir()
        ss.set_install_dir(install_dir)
        ss.get_store().set("databases.dir", str(old_db))
        ss.reset_store()

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        w._install_row.set_path(install_dir)
        w._db_row.set_path(new_db)

        calls = []
        monkeypatch.setattr(
            w, "_offer_move_databases",
            lambda old_dir, new_dir: calls.append((old_dir, new_dir)),
        )

        w._save_all(use_defaults=False)

        assert calls == [(old_db, new_db)]
        assert ss.get_store().get("databases.dir") == str(new_db)

    def test_skip_wizard_never_offers_to_move_databases(self, tmp_path, monkeypatch, qtbot):
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        ss.set_install_dir(install_dir)

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)

        called = []
        monkeypatch.setattr(
            w, "_offer_move_databases",
            lambda *_a, **_k: called.append(True),
        )

        w._save_all(use_defaults=True)

        assert called == []

    def test_unchanged_folders_do_not_trigger_any_move(self, tmp_path, monkeypatch, qtbot):
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        ss.set_install_dir(install_dir)

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        # Rows already default to the current install/db dirs — leave as-is.

        move_calls = []
        monkeypatch.setattr(
            w, "_offer_move_databases",
            lambda *_a, **_k: move_calls.append(True),
        )
        settings_move_calls = []
        monkeypatch.setattr(
            ww.WelcomeWizard, "_move_settings_file",
            staticmethod(lambda *_a, **_k: settings_move_calls.append(True)),
        )

        w._save_all(use_defaults=False)

        assert move_calls == []
        assert settings_move_calls == []

    def test_warns_when_destination_already_has_a_settings_file(self, tmp_path, monkeypatch, qtbot):
        """
        #562 follow-up: if the newly chosen install folder already contains
        an opensak.json (e.g. reused from an earlier test run), the old
        settings can't be moved there. That used to be a silent no-op —
        the user just saw nothing happen and had to guess why. It must now
        surface a visible warning instead.
        """
        old_install = tmp_path / "old_install"
        new_install = tmp_path / "new_install"
        old_install.mkdir()
        new_install.mkdir()
        (old_install / "opensak.json").write_text(json.dumps({"gc_username": "Allan"}), encoding="utf-8")
        (new_install / "opensak.json").write_text(json.dumps({"gc_username": "SomeoneElse"}), encoding="utf-8")
        ss.set_install_dir(old_install)

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        w._install_row.set_path(new_install)
        w._db_row.set_path(new_install)
        monkeypatch.setattr(w, "_offer_move_databases", lambda *_a, **_k: None)

        warnings = []
        monkeypatch.setattr(
            ww.QMessageBox, "warning",
            staticmethod(lambda *a, **k: warnings.append((a, k))),
        )

        w._save_all(use_defaults=False)

        assert len(warnings) == 1
        # Neither file was touched by the (correctly refused) move.
        assert json.loads((old_install / "opensak.json").read_text(encoding="utf-8"))["gc_username"] == "Allan"
        assert json.loads((new_install / "opensak.json").read_text(encoding="utf-8"))["gc_username"] == "SomeoneElse"

    def test_no_warning_when_destination_has_no_settings_file(self, tmp_path, monkeypatch, qtbot):
        """The common case (fresh/empty destination folder) must stay silent."""
        old_install = tmp_path / "old_install"
        new_install = tmp_path / "new_install"
        old_install.mkdir()
        (old_install / "opensak.json").write_text("{}", encoding="utf-8")
        ss.set_install_dir(old_install)

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        w._install_row.set_path(new_install)
        w._db_row.set_path(new_install)
        monkeypatch.setattr(w, "_offer_move_databases", lambda *_a, **_k: None)

        warnings = []
        monkeypatch.setattr(
            ww.QMessageBox, "warning",
            staticmethod(lambda *a, **k: warnings.append((a, k))),
        )

        w._save_all(use_defaults=False)

        assert warnings == []
        assert (new_install / "opensak.json").exists()


# ── _cleanup_old_dir ─────────────────────────────────────────────────────────

class TestCleanupOldDir:
    def test_removes_folder_when_empty(self, tmp_path):
        folder = tmp_path / "old"
        folder.mkdir()

        ww.WelcomeWizard._cleanup_old_dir(folder)

        assert not folder.exists()

    def test_leaves_folder_when_it_still_has_unrelated_content(self, tmp_path):
        folder = tmp_path / "old"
        folder.mkdir()
        (folder / "something_else.txt").write_text("keep me", encoding="utf-8")

        ww.WelcomeWizard._cleanup_old_dir(folder)

        assert folder.exists()
        assert (folder / "something_else.txt").exists()

    def test_deletes_named_extra_files_then_removes_folder(self, tmp_path):
        folder = tmp_path / "old"
        folder.mkdir()
        (folder / "opensak.log").write_text("log", encoding="utf-8")
        (folder / "opensak.log.1").write_text("log backup", encoding="utf-8")

        ww.WelcomeWizard._cleanup_old_dir(folder, extra_files=("opensak.log", "opensak.log.1"))

        assert not folder.exists()

    def test_missing_folder_does_not_raise(self, tmp_path):
        ww.WelcomeWizard._cleanup_old_dir(tmp_path / "does_not_exist")  # must not raise

    def test_extra_file_locked_leaves_folder_but_does_not_raise(self, tmp_path, monkeypatch):
        folder = tmp_path / "old"
        folder.mkdir()
        (folder / "opensak.log").write_text("log", encoding="utf-8")

        def _boom(self):
            raise OSError("file in use")

        monkeypatch.setattr(ww.Path, "unlink", _boom)

        ww.WelcomeWizard._cleanup_old_dir(folder, extra_files=("opensak.log",))  # must not raise

        assert folder.exists()  # log file still there, so folder wasn't removed


# ── _move_remaining_install_dir_contents ────────────────────────────────────

class TestMoveRemainingInstallDirContents:
    def test_moves_a_plain_file(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "gc_token.json").write_text('{"token": "abc"}', encoding="utf-8")

        ww.WelcomeWizard._move_remaining_install_dir_contents(old_dir, new_dir)

        assert not (old_dir / "gc_token.json").exists()
        assert (new_dir / "gc_token.json").read_text(encoding="utf-8") == '{"token": "abc"}'

    def test_moves_a_subfolder_with_its_contents(self, tmp_path):
        """#519: the user-customisable icons/ folder must move as a whole."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        icons = old_dir / "icons"
        icons.mkdir(parents=True)
        (icons / "traditional_cache.svg").write_text("<svg/>", encoding="utf-8")
        new_dir.mkdir()

        ww.WelcomeWizard._move_remaining_install_dir_contents(old_dir, new_dir)

        assert not icons.exists()
        assert (new_dir / "icons" / "traditional_cache.svg").read_text(encoding="utf-8") == "<svg/>"

    def test_deletes_log_files_instead_of_moving_them(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "opensak.log").write_text("log", encoding="utf-8")
        (old_dir / "opensak.log.1").write_text("log backup", encoding="utf-8")

        ww.WelcomeWizard._move_remaining_install_dir_contents(old_dir, new_dir)

        assert not (old_dir / "opensak.log").exists()
        assert not (old_dir / "opensak.log.1").exists()
        assert not (new_dir / "opensak.log").exists()  # deleted, not moved
        assert not (new_dir / "opensak.log.1").exists()

    def test_does_not_overwrite_existing_dest_entry(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        (old_dir / "gc_token.json").write_text("old-token", encoding="utf-8")
        (new_dir / "gc_token.json").write_text("new-token", encoding="utf-8")

        ww.WelcomeWizard._move_remaining_install_dir_contents(old_dir, new_dir)

        assert (old_dir / "gc_token.json").read_text(encoding="utf-8") == "old-token"
        assert (new_dir / "gc_token.json").read_text(encoding="utf-8") == "new-token"

    def test_moves_everything_leaving_source_empty(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "icons").mkdir(parents=True)
        (old_dir / "icons" / "found.svg").write_text("<svg/>", encoding="utf-8")
        (old_dir / "gc_token.json").write_text("token", encoding="utf-8")
        (old_dir / "opensak.log").write_text("log", encoding="utf-8")
        new_dir.mkdir()

        ww.WelcomeWizard._move_remaining_install_dir_contents(old_dir, new_dir)

        assert list(old_dir.iterdir()) == []  # nothing left behind

    def test_missing_source_dir_does_not_raise(self, tmp_path):
        old_dir = tmp_path / "does_not_exist"
        new_dir = tmp_path / "new"
        new_dir.mkdir()

        ww.WelcomeWizard._move_remaining_install_dir_contents(old_dir, new_dir)  # must not raise


# ── _save_all: full folder cleanup (Allan's reported scenario) ─────────────

class TestSaveAllCleansUpOldFolders:
    def test_nested_db_dir_under_install_dir_both_removed_when_empty(
        self, tmp_path, monkeypatch, qtbot
    ):
        """
        Reproduces the reported scenario: install dir "…/myOpenSAK" with the
        database folder nested inside it as "…/myOpenSAK/Data". Choosing
        "move and delete" for the databases must leave neither the Data
        subfolder nor the now-empty old install dir behind.
        """
        old_install = tmp_path / "myOpenSAK"
        old_db = old_install / "Data"
        old_db.mkdir(parents=True)
        (old_install / "opensak.json").write_text(
            json.dumps({"databases.dir": str(old_db)}), encoding="utf-8",
        )
        (old_db / "Default.db").write_text("db content", encoding="utf-8")
        ss.set_install_dir(old_install)

        new_install = tmp_path / "newOpenSAK"
        new_db = new_install / "Data"

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        w._install_row.set_path(new_install)
        w._db_row.set_path(new_db)

        manager = _FakeManager(databases=[_FakeDbInfo("Default")])
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        # Simulate the real move_databases_to's file-level effect (the fake
        # manager above only records the call) so the old Data folder
        # actually becomes empty, the way it would in production.
        real_move = manager.move_databases_to

        def _move_and_delete_file(new_dir, delete_originals):
            (old_db / "Default.db").unlink()
            return real_move(new_dir, delete_originals)

        monkeypatch.setattr(manager, "move_databases_to", _move_and_delete_file)
        _click_button_with_text(monkeypatch, "settings_move_delete_originals")

        w._save_all(use_defaults=False)

        assert not old_db.exists()
        assert not old_install.exists()

    def test_keep_originals_does_not_delete_old_db_folder(self, tmp_path, monkeypatch, qtbot):
        install_dir = tmp_path / "install"
        old_db = tmp_path / "old_db"
        old_db.mkdir()
        install_dir.mkdir()
        ss.set_install_dir(install_dir)
        ss.get_store().set("databases.dir", str(old_db))
        ss.reset_store()

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        w._install_row.set_path(install_dir)
        w._db_row.set_path(tmp_path / "new_db")

        manager = _FakeManager(databases=[_FakeDbInfo("Default")])
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        _click_button_with_text(monkeypatch, "settings_move_keep_originals")

        w._save_all(use_defaults=False)

        assert old_db.exists()  # "keep" was chosen — must not be touched

    def test_move_errors_prevent_old_db_folder_cleanup(self, tmp_path, monkeypatch, qtbot):
        """If any file failed to move, the old folder isn't actually empty
        (or the state is uncertain) — don't remove it."""
        install_dir = tmp_path / "install"
        old_db = tmp_path / "old_db"
        old_db.mkdir()
        install_dir.mkdir()
        ss.set_install_dir(install_dir)
        ss.get_store().set("databases.dir", str(old_db))
        ss.reset_store()

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        w._install_row.set_path(install_dir)
        w._db_row.set_path(tmp_path / "new_db")

        manager = _FakeManager(
            databases=[_FakeDbInfo("Default")],
            move_result=["Default.db: target already exists"],
        )
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        monkeypatch.setattr(ww.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
        _click_button_with_text(monkeypatch, "settings_move_delete_originals")

        w._save_all(use_defaults=False)

        assert old_db.exists()  # errors occurred — must not be cleaned up

    def test_icons_folder_moved_and_old_main_folder_fully_removed(
        self, tmp_path, monkeypatch, qtbot
    ):
        """
        Reported scenario: the old install ("main") folder wasn't removed
        because it still contained the icons/ subfolder (#519, user-
        customisable icons) — that content is now moved along with
        opensak.json, so the old main folder ends up genuinely empty and
        gets removed too.
        """
        old_install = tmp_path / "main"
        icons = old_install / "icons"
        icons.mkdir(parents=True)
        (icons / "traditional_cache.svg").write_text("<svg/>", encoding="utf-8")
        (old_install / "opensak.json").write_text("{}", encoding="utf-8")
        ss.set_install_dir(old_install)

        new_install = tmp_path / "new_main"

        w = ww.WelcomeWizard()
        qtbot.addWidget(w)
        w._install_row.set_path(new_install)
        w._db_row.set_path(new_install)
        monkeypatch.setattr(w, "_offer_move_databases", lambda *_a, **_k: None)

        w._save_all(use_defaults=False)

        assert not old_install.exists()
        assert (new_install / "icons" / "traditional_cache.svg").exists()
