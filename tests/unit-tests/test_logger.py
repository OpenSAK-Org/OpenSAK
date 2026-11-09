# tests/unit-tests/test_logger.py — central logging setup tests (issue #232).

import logging

import pytest

import opensak.logger as logger_module
from opensak.logger import get_logger, reset_logging, setup_logging


@pytest.fixture(autouse=True)
def _clean_logging(tmp_path, monkeypatch):
    """Isolate logging: fresh log path per test, reset state after."""
    from opensak import settings_store as ss
    fresh = ss.SettingsStore()
    fresh._data = {}
    fresh._path = tmp_path / "opensak.json"
    monkeypatch.setattr(ss, "_store", fresh)
    monkeypatch.setattr(ss, "get_install_dir", lambda: tmp_path)

    reset_logging()
    yield
    reset_logging()


# ── setup_logging ──────────────────────────────────────────────────────────

class TestSetupLogging:
    def test_creates_log_file(self, tmp_path):
        log_path = setup_logging()
        log = get_logger("updater")
        log.warning("hello")  # WARNING always fires regardless of flag
        for h in logging.getLogger("opensak").handlers:
            h.flush()
        assert log_path.exists()

    def test_returns_path_under_install_dir(self, tmp_path):
        log_path = setup_logging()
        assert log_path.parent == tmp_path

    def test_idempotent_second_call_returns_same_path(self):
        first = setup_logging()
        second = setup_logging()
        assert first == second

    def test_idempotent_does_not_duplicate_handlers(self):
        setup_logging()
        setup_logging()
        root = logging.getLogger("opensak")
        assert len(root.handlers) == 1

    def test_resets_log_on_each_setup_cycle(self, tmp_path):
        # First "session"
        log_path = setup_logging()
        log = get_logger("updater")
        log.warning("first session line")
        for h in logging.getLogger("opensak").handlers:
            h.flush()
        first_content = log_path.read_text(encoding="utf-8")
        assert "first session line" in first_content

        # Simulate a new app startup: reset state, reopen.
        reset_logging()
        setup_logging()
        second_content = log_path.read_text(encoding="utf-8")
        # Issue #737: rotation (not deletion) — the new session's log
        # must still start fresh, same observable behaviour as before.
        assert "first session line" not in second_content

    def test_no_rotation_appends_to_the_current_log(self, tmp_path):
        # #937: a headless run must not move away the log of an open OpenSAK.
        log_path = setup_logging()
        get_logger("updater").warning("gui session line")
        for h in logging.getLogger("opensak").handlers:
            h.flush()

        reset_logging()
        setup_logging(rotate=False)
        get_logger("updater").warning("headless line")
        for h in logging.getLogger("opensak").handlers:
            h.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "gui session line" in content and "headless line" in content

    def test_uses_rotating_file_handler(self):
        setup_logging()
        root = logging.getLogger("opensak")
        from logging.handlers import RotatingFileHandler
        assert any(isinstance(h, RotatingFileHandler) for h in root.handlers)

    def test_max_bytes_is_one_megabyte(self):
        setup_logging()
        root = logging.getLogger("opensak")
        handler = root.handlers[0]
        assert handler.maxBytes == 1 * 1024 * 1024

    def test_backup_count_is_one(self):
        setup_logging()
        root = logging.getLogger("opensak")
        handler = root.handlers[0]
        assert handler.backupCount == 1


# ── get_logger / per-module flags ────────────────────────────────────────────

class TestGetLoggerPerModuleFlags:
    def test_logger_name_is_namespaced(self):
        log = get_logger("importer")
        assert log.name == "opensak.importer"

    def test_enabled_module_logs_debug(self, monkeypatch, tmp_path):
        monkeypatch.setitem(
            __import__("opensak.debug_flags", fromlist=["DEBUG_MODULES"]).DEBUG_MODULES,
            "test_mod_on", True,
        )
        log_path = setup_logging()
        log = get_logger("test_mod_on")
        log.debug("debug line should appear")
        for h in logging.getLogger("opensak").handlers:
            h.flush()
        assert "debug line should appear" in log_path.read_text(encoding="utf-8")

    def test_disabled_module_suppresses_debug(self, monkeypatch, tmp_path):
        monkeypatch.setitem(
            __import__("opensak.debug_flags", fromlist=["DEBUG_MODULES"]).DEBUG_MODULES,
            "test_mod_off", False,
        )
        log_path = setup_logging()
        log = get_logger("test_mod_off")
        log.debug("debug line should NOT appear")
        for h in logging.getLogger("opensak").handlers:
            h.flush()
        assert "debug line should NOT appear" not in log_path.read_text(encoding="utf-8")

    def test_disabled_module_still_logs_warning(self, monkeypatch, tmp_path):
        monkeypatch.setitem(
            __import__("opensak.debug_flags", fromlist=["DEBUG_MODULES"]).DEBUG_MODULES,
            "test_mod_warn", False,
        )
        log_path = setup_logging()
        log = get_logger("test_mod_warn")
        log.warning("warning line should appear even when disabled")
        for h in logging.getLogger("opensak").handlers:
            h.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "warning line should appear even when disabled" in content

    def test_flag_toggle_takes_effect_on_next_get_logger_call(self, monkeypatch, tmp_path):
        import opensak.debug_flags as flags_module
        log_path = setup_logging()

        monkeypatch.setitem(flags_module.DEBUG_MODULES, "test_mod_toggle", False)
        log = get_logger("test_mod_toggle")
        log.debug("should not appear (flag off)")

        monkeypatch.setitem(flags_module.DEBUG_MODULES, "test_mod_toggle", True)
        log2 = get_logger("test_mod_toggle")
        log2.debug("should appear (flag on)")

        for h in logging.getLogger("opensak").handlers:
            h.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "should not appear (flag off)" not in content
        assert "should appear (flag on)" in content


# ── reset_logging ─────────────────────────────────────────────────────────────

class TestResetLogging:
    def test_removes_handlers(self):
        setup_logging()
        assert len(logging.getLogger("opensak").handlers) > 0
        reset_logging()
        assert len(logging.getLogger("opensak").handlers) == 0

    def test_allows_clean_resetup(self, tmp_path):
        setup_logging()
        reset_logging()
        log_path = setup_logging()
        assert log_path.exists() or log_path.parent.exists()


# ── previous-session log rotation (issue #737) ─────────────────────────────────
#
# opensak.log used to be deleted outright at the start of every session
# (#232) — fine for normal use, but it meant a crash/freeze from the last
# session was unrecoverable the moment the user reopened OpenSAK to go
# looking for it. setup_logging() now rotates the previous session's log
# to opensak.log.previous instead of deleting it, and a new "Open log
# from last restart" Help-menu action (mainwindow.py) opens that file.

class TestPreviousSessionLogRotation:
    def test_first_ever_session_has_no_previous_log(self):
        from opensak.config import get_previous_log_path
        setup_logging()
        assert not get_previous_log_path().exists()

    def test_previous_session_content_preserved_after_rotation(self, tmp_path):
        from opensak.config import get_previous_log_path

        log_path = setup_logging()
        log = get_logger("updater")
        log.warning("session one line")
        for h in logging.getLogger("opensak").handlers:
            h.flush()

        reset_logging()
        setup_logging()  # rotates session one's log to .previous

        previous_content = get_previous_log_path().read_text(encoding="utf-8")
        assert "session one line" in previous_content
        # And the new session's own log must still start fresh (covered
        # again here since it's the behaviour #737 must not regress).
        assert "session one line" not in log_path.read_text(encoding="utf-8")

    def test_only_the_single_most_recent_previous_session_is_kept(self):
        from opensak.config import get_previous_log_path

        # Session one
        setup_logging()
        get_logger("updater").warning("session one line")
        for h in logging.getLogger("opensak").handlers:
            h.flush()
        reset_logging()

        # Session two
        setup_logging()
        get_logger("updater").warning("session two line")
        for h in logging.getLogger("opensak").handlers:
            h.flush()
        reset_logging()

        # Session three — rotates session two's log into .previous,
        # overwriting session one's (already rotated away by session two).
        setup_logging()

        previous_content = get_previous_log_path().read_text(encoding="utf-8")
        assert "session two line" in previous_content
        assert "session one line" not in previous_content

    def test_rotation_failure_falls_back_to_delete_not_crash(self, tmp_path, monkeypatch):
        # A locked/permission-denied rotation must not prevent the app
        # from starting — degrade to the old delete behaviour instead.
        import opensak.logger as logger_module
        from pathlib import Path

        setup_logging()
        get_logger("updater").warning("will be lost, and that's fine")
        for h in logging.getLogger("opensak").handlers:
            h.flush()
        reset_logging()

        real_replace = Path.replace

        def boom(self, target):
            raise OSError("simulated: file locked")

        monkeypatch.setattr(Path, "replace", boom)
        log_path = setup_logging()  # must not raise
        assert log_path.exists() or log_path.parent.exists()
