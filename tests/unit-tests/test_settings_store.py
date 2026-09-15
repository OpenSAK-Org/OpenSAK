# tests/unit-tests/test_settings_store.py — SettingsStore persistence tests.

import json
import os
from pathlib import Path

import pytest

from opensak import settings_store as ss

# Forces os.name="posix" + Path(str), which can't instantiate PosixPath on
# Windows (see test_config.py for the same, pre-existing pattern). Needed
# because pathlib's WindowsPath/PosixPath.__new__ overrides are fixed at
# interpreter-startup based on the REAL OS, not the (later, patched) os.name
# value the Path() factory itself dispatches on — so any bare Path(string)
# construction reached while os.name is patched away from the real OS raises
# NotImplementedError, even though the platform-branch logic being tested is
# otherwise correct.
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX-only path branch")


@pytest.fixture
def store(tmp_path):
    """Fresh SettingsStore backed by a temp file — no disk pollution."""
    s = ss.SettingsStore()
    s._data = {}
    s._path = tmp_path / "opensak.json"
    return s


# ── Basic get/set ──────────────────────────────────────────────────────────

class TestBasicGetSet:
    def test_get_missing_returns_default(self, store):
        assert store.get("missing.key", "fallback") == "fallback"

    def test_set_then_get_roundtrip(self, store):
        store.set("a.b", 42)
        assert store.get("a.b") == 42

    def test_set_many(self, store):
        store.set_many({"x": 1, "y": 2})
        assert store.get("x") == 1
        assert store.get("y") == 2

    def test_delete_removes_key(self, store):
        store.set("k", "v")
        store.delete("k")
        assert store.get("k") is None

    def test_get_section(self, store):
        store.set_many({"sort.dbA.field": "name", "sort.dbB.field": "date", "other": 1})
        section = store.get_section("sort")
        assert section == {"dbA.field": "name", "dbB.field": "date"}


# ── Boolean serialization regression (the AA== bug) ───────────────────────

class TestBooleanSerialization:
    """
    Regression tests for a bug where True/False were silently corrupted
    to base64 strings on disk.

    Root cause: `bytes(obj)` succeeds for any int-like object, and bool is
    an int subclass in Python — bytes(True) == b'\\x00' (1 zero byte),
    bytes(False) == b'' (0 bytes). The old _flush() fallback tried
    `bytes(obj)` on every non-dict/list/bytes value to catch QByteArray,
    and ints/bools slipped through that net and got base64-encoded.
    """

    def test_true_round_trips_as_bool_through_flush(self, store):
        store.set("flag", True)
        # Re-load from disk to ensure the *written* value, not just the
        # in-memory dict, is a real bool.
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["flag"] is True

    def test_false_round_trips_as_bool_through_flush(self, store):
        store.set("flag", False)
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["flag"] is False

    def test_true_is_not_base64_encoded(self, store):
        store.set("flag", True)
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["flag"] != "AA=="
        assert raw["flag"] != "AQ=="

    def test_int_round_trips_correctly(self, store):
        store.set("count", 5)
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["count"] == 5
        assert isinstance(raw["count"], int)

    def test_zero_int_round_trips_correctly(self, store):
        # bytes(0) == b'' — another edge case the old code mishandled.
        store.set("count", 0)
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["count"] == 0

    def test_float_round_trips_correctly(self, store):
        store.set("ratio", 0.49)
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["ratio"] == pytest.approx(0.49)

    def test_string_round_trips_correctly(self, store):
        store.set("name", "Default")
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["name"] == "Default"

    def test_none_round_trips_correctly(self, store):
        store.set("empty", None)
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["empty"] is None

    def test_bool_survives_full_reload_cycle(self, store):
        # Simulates the real bug scenario: set -> flush -> fresh load.
        store.set("updates.check_enabled", True)
        reloaded = ss.SettingsStore()
        reloaded._path = store._path
        assert reloaded.get("updates.check_enabled") is True

    def test_nested_bool_in_dict_serializes_correctly(self, store):
        store.set("nested", {"enabled": True, "count": 0})
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["nested"]["enabled"] is True
        assert raw["nested"]["count"] == 0

    def test_bool_in_list_serializes_correctly(self, store):
        store.set("flags", [True, False, True])
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["flags"] == [True, False, True]


# ── QByteArray-like serialization (still must work) ───────────────────────

class TestBytesLikeSerialization:
    def test_real_bytes_value_is_base64_encoded(self, store):
        store.set("blob", b"\x01\x02\x03")
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        import base64
        assert raw["blob"] == base64.b64encode(b"\x01\x02\x03").decode()

    def test_bytearray_value_is_base64_encoded(self, store):
        store.set("blob", bytearray(b"\xff\x00"))
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        import base64
        assert raw["blob"] == base64.b64encode(bytes(bytearray(b"\xff\x00"))).decode()

    def test_qbytearray_like_object_is_base64_encoded(self, store):
        # Minimal stand-in for PySide6.QtCore.QByteArray: supports __bytes__
        # but is not a Python bytes/bytearray instance.
        class _FakeQByteArray:
            def __bytes__(self):
                return b"\x10\x20"

        store.set("qba", _FakeQByteArray())
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        import base64
        assert raw["qba"] == base64.b64encode(b"\x10\x20").decode()


# ── repair_corrupted_bool_keys ──────────────────────────────────────────────

class TestRepairCorruptedBoolKeys:
    """
    Regression coverage for the auto-repair that fixes settings.json files
    written by a previous (buggy) version of _flush(), where True/False
    were corrupted to base64 'AQ=='/'AA==' strings.
    """

    def test_repairs_known_corrupted_key(self, store):
        store._data = {"updates.check_enabled": "AA=="}
        store._flush()
        ss.repair_corrupted_bool_keys(store)
        assert store.get("updates.check_enabled") is False

    def test_repairs_true_corrupted_key(self, store):
        store._data = {"updates.check_enabled": "AQ=="}
        store._flush()
        ss.repair_corrupted_bool_keys(store)
        assert store.get("updates.check_enabled") is True

    def test_leaves_clean_bool_untouched(self, store):
        store.set("updates.check_enabled", True)
        ss.repair_corrupted_bool_keys(store)
        assert store.get("updates.check_enabled") is True

    def test_leaves_missing_key_untouched(self, store):
        ss.repair_corrupted_bool_keys(store)
        assert store.get("updates.check_enabled") is None

    def test_repairs_multiple_known_keys(self, store):
        store._data = {
            "updates.check_enabled": "AA==",
            "display.use_miles": "AQ==",
        }
        store._flush()
        ss.repair_corrupted_bool_keys(store)
        assert store.get("updates.check_enabled") is False
        assert store.get("display.use_miles") is True

    def test_does_not_touch_unrelated_string_values(self, store):
        store.set("user.gc_username", "AA==")  # coincidentally same string
        # gc_username is not in the known bool-key list, so it must survive.
        ss.repair_corrupted_bool_keys(store)
        assert store.get("user.gc_username") == "AA=="

    def test_idempotent_second_call_is_a_no_op(self, store):
        store._data = {"updates.check_enabled": "AA=="}
        store._flush()
        ss.repair_corrupted_bool_keys(store)
        ss.repair_corrupted_bool_keys(store)  # second call should not error
        assert store.get("updates.check_enabled") is False

    def test_new_writes_after_repair_stay_correct(self, store):
        store._data = {"updates.check_enabled": "AA=="}
        store._flush()
        ss.repair_corrupted_bool_keys(store)
        store.set("updates.check_enabled", True)
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["updates.check_enabled"] is True


# ── sync and invalidate_path_cache ─────────────────────────────────────────

class TestSyncAndInvalidate:
    def test_sync_flushes_loaded_data(self, store):
        store.set("x", 99)
        store.sync()
        raw = json.loads(store._path.read_text(encoding="utf-8"))
        assert raw["x"] == 99

    def test_sync_no_op_when_data_not_loaded(self, store):
        store._data = None
        store.sync()  # must not raise

    def test_invalidate_path_cache_resets_both(self, store):
        store.set("y", 1)
        store.invalidate_path_cache()
        assert store._path is None
        assert store._data is None


# ── module-level singleton ─────────────────────────────────────────────────

class TestModuleSingleton:
    def test_get_store_returns_instance(self, monkeypatch):
        monkeypatch.setattr(ss, "_store", None)
        result = ss.get_store()
        assert isinstance(result, ss.SettingsStore)

    def test_get_store_returns_same_instance(self, monkeypatch):
        monkeypatch.setattr(ss, "_store", None)
        assert ss.get_store() is ss.get_store()

    def test_reset_store_creates_new_instance(self, monkeypatch):
        monkeypatch.setattr(ss, "_store", None)
        first = ss.get_store()
        ss.reset_store()
        second = ss.get_store()
        assert first is not second


# ── _load edge cases ───────────────────────────────────────────────────────

class TestLoadEdgeCases:
    def test_invalid_json_falls_back_to_empty_dict(self, tmp_path):
        path = tmp_path / "opensak.json"
        path.write_text("not json", encoding="utf-8")
        store = ss.SettingsStore()
        store._path = path
        assert store.get("anything") is None
        assert store._data == {}

    def test_non_dict_json_falls_back_to_empty_dict(self, tmp_path):
        path = tmp_path / "opensak.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        store = ss.SettingsStore()
        store._path = path
        assert store.get("anything") is None
        assert store._data == {}


# ── helper functions ───────────────────────────────────────────────────────

class TestHelperFunctions:
    def test_is_first_run_true_when_wizard_not_completed(self, monkeypatch, tmp_path):
        store = ss.SettingsStore()
        store._data = {}
        store._path = tmp_path / "opensak.json"
        monkeypatch.setattr(ss, "_store", store)
        assert ss.is_first_run() is True

    def test_mark_wizard_completed_clears_first_run(self, monkeypatch, tmp_path):
        store = ss.SettingsStore()
        store._data = {}
        store._path = tmp_path / "opensak.json"
        monkeypatch.setattr(ss, "_store", store)
        ss.mark_wizard_completed()
        assert ss.is_first_run() is False

    def test_get_db_dir_falls_back_to_install_dir(self, monkeypatch, tmp_path):
        store = ss.SettingsStore()
        store._data = {}
        store._path = tmp_path / "opensak.json"
        monkeypatch.setattr(ss, "_store", store)
        monkeypatch.setattr(ss, "get_install_dir", lambda: tmp_path)
        assert ss.get_db_dir() == tmp_path

    def test_get_db_dir_uses_custom_dir_when_set(self, monkeypatch, tmp_path):
        custom = tmp_path / "dbs"
        store = ss.SettingsStore()
        store._data = {"databases.dir": str(custom)}
        store._path = tmp_path / "opensak.json"
        monkeypatch.setattr(ss, "_store", store)
        result = ss.get_db_dir()
        assert result == custom
        assert result.exists()


# ── bootstrap / install dir ────────────────────────────────────────────────

class TestBootstrapAndInstallDir:
    def test_get_install_dir_reads_bootstrap_json(self, monkeypatch, tmp_path):
        custom = tmp_path / "myinstall"
        bootstrap = tmp_path / "bootstrap.json"
        bootstrap.write_text(json.dumps({"install_dir": str(custom)}), encoding="utf-8")
        monkeypatch.setattr(ss, "_bootstrap_path", lambda: bootstrap)
        result = ss.get_install_dir()
        assert result == custom
        assert result.exists()

    def test_get_install_dir_falls_back_on_missing_key(self, monkeypatch, tmp_path):
        bootstrap = tmp_path / "bootstrap.json"
        bootstrap.write_text("{}", encoding="utf-8")
        fallback = tmp_path / "fallback"
        monkeypatch.setattr(ss, "_bootstrap_path", lambda: bootstrap)
        monkeypatch.setattr(ss, "_default_install_dir", lambda: fallback)
        assert ss.get_install_dir() == fallback

    def test_get_install_dir_falls_back_on_invalid_json(self, monkeypatch, tmp_path):
        bootstrap = tmp_path / "bootstrap.json"
        bootstrap.write_text("INVALID", encoding="utf-8")
        fallback = tmp_path / "fallback"
        monkeypatch.setattr(ss, "_bootstrap_path", lambda: bootstrap)
        monkeypatch.setattr(ss, "_default_install_dir", lambda: fallback)
        assert ss.get_install_dir() == fallback

    def test_set_install_dir_writes_bootstrap_json(self, monkeypatch, tmp_path):
        bootstrap = tmp_path / "bootstrap.json"
        monkeypatch.setattr(ss, "_bootstrap_path", lambda: bootstrap)
        ss.set_install_dir(tmp_path / "install")
        data = json.loads(bootstrap.read_text(encoding="utf-8"))
        assert data["install_dir"] == str(tmp_path / "install")

    def test_set_install_dir_preserves_existing_keys(self, monkeypatch, tmp_path):
        bootstrap = tmp_path / "bootstrap.json"
        bootstrap.write_text(json.dumps({"other": "value"}), encoding="utf-8")
        monkeypatch.setattr(ss, "_bootstrap_path", lambda: bootstrap)
        ss.set_install_dir(tmp_path / "install")
        data = json.loads(bootstrap.read_text(encoding="utf-8"))
        assert data["other"] == "value"
        assert "install_dir" in data


# ── _atomic_write retry on transient Windows file-lock (#574) ──────────────

class TestAtomicWriteRetry:
    def test_succeeds_normally_without_any_retry(self, tmp_path):
        path = tmp_path / "opensak.json"
        ss._atomic_write(path, {"a": 1})
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}

    def test_retries_and_succeeds_after_transient_lock(self, tmp_path, monkeypatch):
        # Issue #574: WinError 5 (Access is denied) on the rename, e.g. from
        # antivirus/indexer/roaming-profile-sync briefly holding the file
        # open right after boot or an update. Simulate it failing twice,
        # then succeeding on the third attempt.
        path = tmp_path / "opensak.json"
        real_replace = Path.replace
        calls = {"n": 0}

        def flaky_replace(self, target):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError("[WinError 5] Access is denied")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", flaky_replace)
        monkeypatch.setattr(ss.time, "sleep", lambda *_a: None)  # don't slow the test down

        ss._atomic_write(path, {"a": 1})

        assert calls["n"] == 3
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}

    def test_gives_up_after_all_retries_exhausted_and_cleans_up_temp_file(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "opensak.json"

        def always_fails(self, target):
            raise PermissionError("[WinError 5] Access is denied")

        monkeypatch.setattr(Path, "replace", always_fails)
        monkeypatch.setattr(ss.time, "sleep", lambda *_a: None)

        with pytest.raises(PermissionError):
            ss._atomic_write(path, {"a": 1})

        assert not path.exists()
        # The temp file must not be left behind after giving up.
        assert list(tmp_path.iterdir()) == []


# ── platform-specific path resolution (issue #825) ─────────────────────────
#
# Regression coverage for the os.name/darwin bug: os.name is "posix" on both
# Linux and macOS, so these paths must be exercised directly against a mocked
# sys.platform + os.name, rather than via the monkeypatched-function shortcuts
# used elsewhere in this file (those bypass the branching logic entirely).

class TestPlatformSpecificPaths:
    # Note: there's deliberately no test here for the os.name == "nt" branch.
    # Python 3.12's pathlib decides WindowsPath vs. PosixPath once, at module
    # import time, not dynamically per call — monkeypatching os.name/sys.platform
    # afterwards on a posix test runner can't make Path() build a WindowsPath
    # (raises "cannot instantiate 'WindowsPath' on your system"). This is a
    # pre-existing stdlib limitation unrelated to issue #825, and the Windows
    # branch itself isn't touched by this fix — only the darwin/posix split is.
    # Windows behaviour is exercised via the existing test suite on Windows CI.

    def test_bootstrap_path_macos(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ss.os, "name", "posix")
        monkeypatch.setattr(ss.sys, "platform", "darwin")
        monkeypatch.setattr(ss.Path, "home", lambda: tmp_path)
        result = ss._bootstrap_path()
        assert result == (
            tmp_path / "Library" / "Application Support" / "opensak" / "bootstrap.json"
        )

    def test_bootstrap_path_linux(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ss.os, "name", "posix")
        monkeypatch.setattr(ss.sys, "platform", "linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr(ss.Path, "home", lambda: tmp_path)
        result = ss._bootstrap_path()
        assert result == tmp_path / ".config" / "opensak" / "bootstrap.json"

    @posix_only
    def test_bootstrap_path_linux_respects_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ss.os, "name", "posix")
        monkeypatch.setattr(ss.sys, "platform", "linux")
        xdg = tmp_path / "custom-xdg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        result = ss._bootstrap_path()
        assert result == xdg / "opensak" / "bootstrap.json"

    def test_default_install_dir_macos(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ss.os, "name", "posix")
        monkeypatch.setattr(ss.sys, "platform", "darwin")
        monkeypatch.setattr(ss.Path, "home", lambda: tmp_path)
        result = ss._default_install_dir()
        assert result == tmp_path / "Library" / "Application Support" / "opensak"

    def test_default_install_dir_linux(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ss.os, "name", "posix")
        monkeypatch.setattr(ss.sys, "platform", "linux")
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(ss.Path, "home", lambda: tmp_path)
        result = ss._default_install_dir()
        assert result == tmp_path / ".local" / "share" / "opensak"

    def test_default_install_dir_windows_msix_packaged_uses_documents(self, monkeypatch, tmp_path):
        # Issue #820 part B: MSIX-packaged Windows installs default to
        # Documents instead of the virtualized %AppData% location.
        monkeypatch.setattr(ss.os, "name", "nt")
        monkeypatch.setattr(ss.Path, "home", lambda: tmp_path)
        import opensak.msix as msix_module
        monkeypatch.setattr(msix_module, "is_msix_packaged", lambda: True)
        result = ss._default_install_dir()
        assert result == tmp_path / "Documents" / "opensak"


# ── macOS default-path migration (issue #825) ──────────────────────────────

class TestMigrateMacosDefaultPaths:
    def _patch_platform(self, monkeypatch, home: Path):
        """Common setup: pretend to be macOS, rooted at a temp $HOME."""
        monkeypatch.setattr(ss.sys, "platform", "darwin")
        monkeypatch.setattr(ss.os, "name", "posix")
        monkeypatch.setattr(ss.Path, "home", lambda: home)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    def test_noop_on_non_macos(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ss.sys, "platform", "linux")
        assert ss.migrate_macos_default_paths() is False

    def test_noop_on_fresh_install_nothing_to_migrate(self, monkeypatch, tmp_path):
        self._patch_platform(monkeypatch, tmp_path)
        assert ss.migrate_macos_default_paths() is False
        # Must not have created anything on either the old or the new side.
        assert not (tmp_path / ".config").exists()
        assert not (tmp_path / "Library").exists()

    def test_noop_if_new_bootstrap_already_exists(self, monkeypatch, tmp_path):
        self._patch_platform(monkeypatch, tmp_path)
        new_bootstrap = ss._bootstrap_path()
        new_bootstrap.parent.mkdir(parents=True)
        new_bootstrap.write_text(json.dumps({"install_dir": "whatever"}), encoding="utf-8")
        # Old (legacy) data present too — must be left untouched.
        old_dir = ss._legacy_macos_default_install_dir()
        old_dir.mkdir(parents=True)
        (old_dir / "opensak.json").write_text("{}", encoding="utf-8")

        assert ss.migrate_macos_default_paths() is False
        assert (old_dir / "opensak.json").exists()  # untouched, not clobbered

    @posix_only
    def test_migrates_default_path_contents(self, monkeypatch, tmp_path):
        self._patch_platform(monkeypatch, tmp_path)
        old_dir = ss._legacy_macos_default_install_dir()
        old_dir.mkdir(parents=True)
        (old_dir / "opensak.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
        (old_dir / "MyCaches.sqlite").write_text("dummy-db", encoding="utf-8")

        assert ss.migrate_macos_default_paths() is True

        new_dir = ss._default_install_dir()
        assert (new_dir / "opensak.json").exists()
        assert (new_dir / "MyCaches.sqlite").read_text(encoding="utf-8") == "dummy-db"
        # Old dir should be cleaned up once empty.
        assert not old_dir.exists()
        # New bootstrap.json must point at the new default install dir.
        new_bootstrap_data = json.loads(ss._bootstrap_path().read_text(encoding="utf-8"))
        assert Path(new_bootstrap_data["install_dir"]) == new_dir

    @posix_only
    def test_preserves_custom_install_dir_moves_only_bootstrap(self, monkeypatch, tmp_path):
        # User picked a custom install dir via the welcome wizard (#210) —
        # that data isn't affected by the bug and must not be moved, only
        # the bootstrap.json pointer's own location needs correcting.
        self._patch_platform(monkeypatch, tmp_path)
        custom_dir = tmp_path / "MyOwnOpenSAKFolder"
        custom_dir.mkdir(parents=True)
        (custom_dir / "opensak.json").write_text(json.dumps({"a": 1}), encoding="utf-8")

        old_bootstrap = ss._legacy_macos_bootstrap_path()
        old_bootstrap.parent.mkdir(parents=True)
        old_bootstrap.write_text(json.dumps({"install_dir": str(custom_dir)}), encoding="utf-8")

        assert ss.migrate_macos_default_paths() is True

        # Custom dir contents must be untouched, in-place.
        assert (custom_dir / "opensak.json").exists()
        assert json.loads((custom_dir / "opensak.json").read_text(encoding="utf-8")) == {"a": 1}

        new_bootstrap_data = json.loads(ss._bootstrap_path().read_text(encoding="utf-8"))
        assert Path(new_bootstrap_data["install_dir"]) == custom_dir
        assert not old_bootstrap.exists()

    @posix_only
    def test_skips_colliding_entries_without_clobbering(self, monkeypatch, tmp_path):
        self._patch_platform(monkeypatch, tmp_path)
        old_dir = ss._legacy_macos_default_install_dir()
        old_dir.mkdir(parents=True)
        (old_dir / "opensak.json").write_text(json.dumps({"old": True}), encoding="utf-8")

        new_dir = ss._default_install_dir()
        new_dir.mkdir(parents=True)
        (new_dir / "opensak.json").write_text(json.dumps({"new": True}), encoding="utf-8")

        assert ss.migrate_macos_default_paths() is True

        # The colliding file must be left as-is on the new side, not
        # overwritten by the old side's version.
        assert json.loads((new_dir / "opensak.json").read_text(encoding="utf-8")) == {"new": True}
        # And the old side must still have its own copy, since it wasn't moved.
        assert json.loads((old_dir / "opensak.json").read_text(encoding="utf-8")) == {"old": True}

    @posix_only
    def test_idempotent_second_call_is_noop(self, monkeypatch, tmp_path):
        self._patch_platform(monkeypatch, tmp_path)
        old_dir = ss._legacy_macos_default_install_dir()
        old_dir.mkdir(parents=True)
        (old_dir / "opensak.json").write_text("{}", encoding="utf-8")

        assert ss.migrate_macos_default_paths() is True
        assert ss.migrate_macos_default_paths() is False  # nothing left to do

    @posix_only
    def test_rewrites_stale_database_paths_after_move(self, monkeypatch, tmp_path):
        """
        Regression test for the bug found while investigating a real macOS
        user's report (Mike, Sep 2026): the file move alone left
        databases.list/.active pointing at the now-gone old directory, so
        DatabaseManager couldn't find the (fully intact, just moved)
        database and silently created a fresh empty one instead.
        """
        self._patch_platform(monkeypatch, tmp_path)
        old_dir = ss._legacy_macos_default_install_dir()
        old_dir.mkdir(parents=True)
        old_db_path = str(old_dir / "Default.db")
        (old_dir / "opensak.json").write_text(json.dumps({
            "user.gc_username": "MikeWood",
            "databases.list": [{"name": "Default", "path": old_db_path}],
            "databases.active": old_db_path,
        }), encoding="utf-8")
        (old_dir / "Default.db").write_text("real-cache-data", encoding="utf-8")

        assert ss.migrate_macos_default_paths() is True

        new_dir = ss._default_install_dir()
        migrated_data = json.loads((new_dir / "opensak.json").read_text(encoding="utf-8"))

        # Plain settings values must survive untouched.
        assert migrated_data["user.gc_username"] == "MikeWood"

        # Path-shaped values must now point at the NEW directory, not the
        # old one that no longer exists.
        expected_db_path = str(new_dir / "Default.db")
        assert migrated_data["databases.list"][0]["path"] == expected_db_path
        assert migrated_data["databases.active"] == expected_db_path
        assert Path(expected_db_path).read_text(encoding="utf-8") == "real-cache-data"

        # And DatabaseManager must actually find it — this is the part
        # that silently failed before the fix.
        from opensak.db.manager import DatabaseManager
        mgr = DatabaseManager()
        assert mgr.active is not None
        assert mgr.active.path == Path(expected_db_path)
        assert mgr.active.path.exists()

    @posix_only
    def test_rewrite_helper_noop_when_no_stale_paths(self, monkeypatch, tmp_path):
        """A user with no databases.* keys at all must not error or change anything."""
        self._patch_platform(monkeypatch, tmp_path)
        old_dir = ss._legacy_macos_default_install_dir()
        old_dir.mkdir(parents=True)
        (old_dir / "opensak.json").write_text(
            json.dumps({"display.theme": "dark"}), encoding="utf-8"
        )

        assert ss.migrate_macos_default_paths() is True

        new_dir = ss._default_install_dir()
        data = json.loads((new_dir / "opensak.json").read_text(encoding="utf-8"))
        assert data == {"display.theme": "dark"}

    def test_rewrite_helper_missing_file_is_safe(self, tmp_path):
        """Must not raise if called against a path that doesn't exist."""
        ss._rewrite_stale_install_dir_paths(
            tmp_path / "does-not-exist.json", tmp_path / "old", tmp_path / "new"
        )  # no exception == pass

    def test_rewrite_helper_invalid_json_is_safe(self, tmp_path):
        """Must not raise on a corrupted/non-JSON opensak.json."""
        bad = tmp_path / "opensak.json"
        bad.write_text("{not valid json", encoding="utf-8")
        ss._rewrite_stale_install_dir_paths(bad, tmp_path / "old", tmp_path / "new")
        assert bad.read_text(encoding="utf-8") == "{not valid json"  # left untouched
