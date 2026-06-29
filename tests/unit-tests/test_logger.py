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
        # mode="w" truncates — old line must be gone.
        assert "first session line" not in second_content

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
