# tests/unit-tests/test_debug_flags.py — per-module debug flag tests (issue #232).

import opensak.debug_flags as flags_module
from opensak.debug_flags import DEBUG_MODULES, is_debug_enabled


class TestDebugModulesStructure:
    def test_is_a_dict(self):
        assert isinstance(DEBUG_MODULES, dict)

    def test_all_values_are_bool(self):
        assert all(isinstance(v, bool) for v in DEBUG_MODULES.values())

    def test_all_keys_are_str(self):
        assert all(isinstance(k, str) for k in DEBUG_MODULES.keys())

    def test_contains_updater_enabled(self):
        # updater was enabled for issue #204 — guards against accidental
        # regression where this gets toggled off in a later edit.
        assert DEBUG_MODULES.get("updater") is True

    def test_not_empty(self):
        assert len(DEBUG_MODULES) > 0


class TestIsDebugEnabled:
    def test_returns_true_for_enabled_module(self, monkeypatch):
        monkeypatch.setitem(flags_module.DEBUG_MODULES, "fake_module", True)
        assert is_debug_enabled("fake_module") is True

    def test_returns_false_for_disabled_module(self, monkeypatch):
        monkeypatch.setitem(flags_module.DEBUG_MODULES, "fake_module", False)
        assert is_debug_enabled("fake_module") is False

    def test_returns_false_for_unregistered_module(self):
        # Typo-safety: an unknown module name must never silently enable logging.
        assert is_debug_enabled("totally_unknown_module_xyz") is False

    def test_updater_is_enabled_by_default(self):
        assert is_debug_enabled("updater") is True

    def test_database_is_disabled_by_default(self):
        assert is_debug_enabled("database") is False
