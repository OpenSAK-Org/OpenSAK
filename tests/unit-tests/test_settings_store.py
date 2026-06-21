# tests/unit-tests/test_settings_store.py — SettingsStore persistence tests.

import json

import pytest

from opensak import settings_store as ss


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
