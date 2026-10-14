"""
tests/unit-tests/test_settings_restore_987.py — opt-in "Also restore
settings" in Restore from backup (issue #987, part of #942).

The restore is staged now (safety set + pending folder) and applied at the
next start, before anything reads the settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("pytestqt")

from opensak.backup import settings_restore as sr
from opensak.backup.backupset import (
    KIND_MANUAL,
    KIND_SAFETY,
    BackupError,
    list_backup_sets,
    read_backup_set,
    rotate,
    write_backup_set,
)
from opensak.backup.settings_restore import (
    APPLIED,
    FAILED,
    FAILED_DIRNAME,
    PENDING_DIRNAME,
    SettingsRestoreError,
    apply_pending_settings_restore,
    can_restore_settings,
    merge_settings,
    pending_settings_restore,
    stage_settings_restore,
)
from opensak.gui.dialogs import restore_dialog as rdlg
from opensak.gui.dialogs.restore_dialog import RestoreDialog, RestoreWorker
from opensak.lang import load_language, tr
from opensak.settings_store import get_install_dir, get_store


@pytest.fixture(autouse=True)
def english():
    load_language("en")


def _settings_file() -> Path:
    return get_store().settings_path()


def _json() -> dict:
    return json.loads(_settings_file().read_text(encoding="utf-8"))


def _write_profile(name: str, content: str, folder: str = "filters") -> None:
    d = get_install_dir() / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(content, encoding="utf-8")


@pytest.fixture
def backups(tmp_path) -> Path:
    folder = tmp_path / "backups"
    get_store().set("backup.dir", str(folder))
    return folder


@pytest.fixture
def old_settings(backups):
    """A settings-only manual backup made with the "old" settings."""
    store = get_store()
    store.set("display.theme", "dark")
    store.set("app.language", "da")
    store.set("window.geometry", "OLD-SCREEN")
    store.set("databases.list", [{"name": "Old", "path": "/old/Old.db"}])
    _write_profile("Unfound", '{"v": "old"}')
    _write_profile("Wide", '{"v": "old"}', folder="column_views")
    backup_set = write_backup_set([], KIND_MANUAL, folder=backups).backup_set

    # …and then the user changes things.
    store.set("display.theme", "light")
    store.set("app.language", "en")
    store.set("window.geometry", "THIS-SCREEN")
    store.set("databases.list", [{"name": "Now", "path": "/now/Now.db"}])
    store.set("display.new_option", True)
    _write_profile("Unfound", '{"v": "new"}')
    _write_profile("Nearby", '{"v": "new"}')
    _write_profile("Wide", '{"v": "new"}', folder="column_views")
    return backup_set


def _restart() -> None:
    """A new start reads opensak.json from disk again."""
    get_store().reload()


# ── merge ─────────────────────────────────────────────────────────────────────

class TestMerge:
    def test_backup_values_win_except_for_this_machine(self):
        current = {
            "display.theme": "light", "databases.list": ["now"],
            "backup.dir": "/usb", "window.geometry": "here", "paths.last_import_dir": "/x",
            "updates.skipped_version": "1.2.3", "_wizard_completed": True,
            "display.only_now": 1,
        }
        restored = {
            "display.theme": "dark", "databases.list": ["then"],
            "backup.dir": "/old-usb", "window.geometry": "there",
            "_wizard_completed": False, "display.only_then": 2,
        }
        assert merge_settings(current, restored) == {
            "display.theme": "dark",
            "display.only_then": 2,
            "databases.list": ["now"],
            "backup.dir": "/usb",
            "window.geometry": "here",
            "paths.last_import_dir": "/x",
            "updates.skipped_version": "1.2.3",
            "_wizard_completed": True,
        }

    def test_a_kept_key_absent_now_stays_absent(self):
        assert merge_settings({}, {"backup.on_exit": "always"}) == {}


# ── can_restore ───────────────────────────────────────────────────────────────

class TestCanRestore:
    def test_ok(self, old_settings):
        assert can_restore_settings(old_settings) is None

    def test_no_settings_in_the_set(self, old_settings):
        from dataclasses import replace
        assert can_restore_settings(replace(old_settings, settings=False))

    def test_from_a_newer_opensak(self, old_settings):
        from dataclasses import replace
        assert "newer" in can_restore_settings(
            replace(old_settings, opensak_version="99.0.0"))


# ── stage ─────────────────────────────────────────────────────────────────────

class TestStage:
    def test_saves_a_safety_set_and_changes_nothing_yet(self, old_settings, backups):
        before = _settings_file().read_text(encoding="utf-8")
        safety = stage_settings_restore(old_settings)

        assert safety.kind == KIND_SAFETY
        assert safety.databases == () and safety.settings
        assert safety.path.parent == backups
        assert json.loads(
            (safety.path / "settings" / "opensak.json").read_text(encoding="utf-8")
        )["display.theme"] == "light"

        assert _settings_file().read_text(encoding="utf-8") == before
        assert pending_settings_restore()
        pending = get_install_dir() / PENDING_DIRNAME
        assert (pending / "opensak.json").is_file()
        assert (pending / "filters" / "Unfound.json").read_text(encoding="utf-8") == '{"v": "old"}'

    def test_a_second_choice_replaces_the_first(self, old_settings, backups):
        stage_settings_restore(old_settings)
        get_store().set("display.theme", "blue")
        newer = write_backup_set([], KIND_MANUAL, folder=backups).backup_set
        stage_settings_restore(newer)
        _restart()
        assert apply_pending_settings_restore() == APPLIED
        assert get_store().get("display.theme") == "blue"

    def test_refuses_a_set_without_settings(self, old_settings):
        from dataclasses import replace
        with pytest.raises(SettingsRestoreError):
            stage_settings_restore(replace(old_settings, settings=False))
        assert not pending_settings_restore()

    def test_nothing_staged_if_the_safety_set_fails(self, old_settings, monkeypatch):
        def broken(*_a, **_k):
            raise BackupError("disk full")
        monkeypatch.setattr(sr, "write_backup_set", broken)
        with pytest.raises(SettingsRestoreError, match="disk full"):
            stage_settings_restore(old_settings)
        assert not pending_settings_restore()

    def test_from_a_compressed_set(self, old_settings, backups):
        packed = write_backup_set([], KIND_MANUAL, folder=backups / "z",
                                  compress=True).backup_set
        assert packed.compressed
        stage_settings_restore(packed)
        assert pending_settings_restore()
        assert (get_install_dir() / PENDING_DIRNAME / "opensak.json").is_file()


# ── apply at the next start ───────────────────────────────────────────────────

class TestApply:
    def test_nothing_waiting(self):
        assert apply_pending_settings_restore() is None

    def test_applies_and_keeps_what_belongs_to_this_machine(self, old_settings):
        stage_settings_restore(old_settings)
        _restart()
        assert apply_pending_settings_restore() == APPLIED

        store = get_store()
        assert store.get("display.theme") == "dark"
        assert store.get("app.language") == "da"
        assert store.get("display.new_option") is None
        assert store.get("window.geometry") == "THIS-SCREEN"
        assert store.get("databases.list") == [{"name": "Now", "path": "/now/Now.db"}]
        assert store.get("backup.dir") == str(old_settings.path.parent)

        filters = get_install_dir() / "filters"
        assert sorted(p.name for p in filters.iterdir()) == ["Unfound.json"]
        assert (filters / "Unfound.json").read_text(encoding="utf-8") == '{"v": "old"}'
        assert (get_install_dir() / "column_views" / "Wide.json").read_text(
            encoding="utf-8") == '{"v": "old"}'
        assert not pending_settings_restore()

    def test_a_folder_missing_from_the_backup_is_left_alone(self, old_settings):
        _write_profile("MyIcon", "{}", folder="icons")   # backup had no icons/
        stage_settings_restore(old_settings)
        _restart()
        apply_pending_settings_restore()
        assert (get_install_dir() / "icons" / "MyIcon.json").is_file()

    def test_survives_being_overwritten_by_the_old_run(self, old_settings):
        # The running app writes opensak.json again after the restore was
        # chosen — that must not undo it.
        stage_settings_restore(old_settings)
        get_store().set("display.theme", "light-again")
        _restart()
        apply_pending_settings_restore()
        assert get_store().get("display.theme") == "dark"

    def test_a_broken_pending_restore_is_set_aside(self, old_settings):
        stage_settings_restore(old_settings)
        pending = get_install_dir() / PENDING_DIRNAME
        (pending / "opensak.json").write_text("{ not json", encoding="utf-8")
        before = _settings_file().read_text(encoding="utf-8")
        _restart()

        assert apply_pending_settings_restore() == FAILED
        assert _settings_file().read_text(encoding="utf-8") == before
        assert not pending.exists()
        assert (get_install_dir() / FAILED_DIRNAME).is_dir()
        assert apply_pending_settings_restore() is None   # not retried forever


# ── safety sets ───────────────────────────────────────────────────────────────

class TestSafetySets:
    def test_listed_and_never_rotated(self, old_settings, backups):
        safety = stage_settings_restore(old_settings)
        assert safety in list_backup_sets(backups)
        rotate(backups, keep=1)
        assert safety.path.exists()
        assert read_backup_set(safety.path).kind == KIND_SAFETY


# ── Restore dialog ────────────────────────────────────────────────────────────

@pytest.fixture
def boxes():
    with patch.object(rdlg, "QMessageBox") as box:
        yield box


@pytest.fixture
def sync_worker(monkeypatch):
    monkeypatch.setattr(RestoreWorker, "start", lambda self: self.run())


def _open(qtbot, folder: Path) -> RestoreDialog:
    with patch.object(rdlg, "get_backup_dir", return_value=folder):
        d = RestoreDialog()
    qtbot.addWidget(d)
    return d


def _select(d: RestoreDialog, backup_set) -> None:
    d._set_list.setCurrentRow([s.path for s in d._sets].index(backup_set.path))


class TestDialog:
    def test_checkbox_is_off_and_enabled_for_a_set_with_settings(
        self, qtbot, old_settings, backups, boxes
    ):
        d = _open(qtbot, backups)
        _select(d, old_settings)
        assert d._settings_cb.isEnabled()
        assert not d._settings_cb.isChecked()

    def test_safety_sets_are_labelled(self, qtbot, old_settings, backups, boxes):
        safety = stage_settings_restore(old_settings)
        d = _open(qtbot, backups)
        row = [s.path for s in d._sets].index(safety.path)
        assert tr("restore_kind_safety") in d._set_list.item(row).text()

    def test_settings_only_staged_after_confirming(
        self, qtbot, old_settings, backups, boxes
    ):
        boxes.question.return_value = rdlg.QMessageBox.StandardButton.Yes
        d = _open(qtbot, backups)
        _select(d, old_settings)
        d._settings_cb.setChecked(True)
        closing = []
        d.close_app_requested.connect(lambda: closing.append(True))
        d._start()
        assert pending_settings_restore()
        texts = [c.args[2] for c in boxes.question.call_args_list]
        assert texts[0] == tr("restore_settings_confirm")
        assert tr("restore_settings_staged") in texts[1]
        assert closing == [True]
        assert d.result() == RestoreDialog.DialogCode.Accepted

    def test_later_does_not_close(self, qtbot, old_settings, backups, boxes):
        yes = rdlg.QMessageBox.StandardButton.Yes
        boxes.question.side_effect = [yes, rdlg.QMessageBox.StandardButton.No]
        d = _open(qtbot, backups)
        _select(d, old_settings)
        d._settings_cb.setChecked(True)
        closing = []
        d.close_app_requested.connect(lambda: closing.append(True))
        d._start()
        assert pending_settings_restore()
        assert closing == []

    def test_not_confirmed_changes_nothing(self, qtbot, old_settings, backups, boxes):
        boxes.question.return_value = rdlg.QMessageBox.StandardButton.No
        d = _open(qtbot, backups)
        _select(d, old_settings)
        d._settings_cb.setChecked(True)
        d._start()
        assert not pending_settings_restore()
        assert len(list_backup_sets(backups)) == 1   # no safety set either

    def test_with_databases_too(self, qtbot, backups, boxes, sync_worker, tmp_path):
        from opensak.db.database import dispose_engine
        from opensak.db.manager import get_db_manager
        m = get_db_manager()
        m.new_database("Trip", tmp_path / "dbs" / "Trip.db")
        dispose_engine(tmp_path / "dbs" / "Trip.db")
        trip = next(db for db in m.databases if db.name == "Trip")
        backup_set = write_backup_set([trip], KIND_MANUAL, folder=backups).backup_set

        boxes.question.return_value = rdlg.QMessageBox.StandardButton.Yes
        d = _open(qtbot, backups)
        _select(d, backup_set)
        d._settings_cb.setChecked(True)
        d._start()
        assert any(db.name.startswith("Trip (restored") for db in m.databases)
        assert pending_settings_restore()
        dispose_engine()

    def test_cancelled_database_restore_stages_no_settings(
        self, qtbot, backups, boxes, tmp_path, monkeypatch
    ):
        from opensak.db.database import dispose_engine
        from opensak.db.manager import get_db_manager
        m = get_db_manager()
        m.new_database("Trip", tmp_path / "dbs" / "Trip.db")
        dispose_engine(tmp_path / "dbs" / "Trip.db")
        trip = next(db for db in m.databases if db.name == "Trip")
        backup_set = write_backup_set([trip], KIND_MANUAL, folder=backups).backup_set

        boxes.question.return_value = rdlg.QMessageBox.StandardButton.Yes
        d = _open(qtbot, backups)
        _select(d, backup_set)
        d._settings_cb.setChecked(True)

        def run_cancelled(self):
            self.cancelled.emit()
        monkeypatch.setattr(RestoreWorker, "start", run_cancelled)
        d._start()
        assert not pending_settings_restore()
        dispose_engine()
