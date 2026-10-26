"""tests/unit-tests/test_macro_settings.py — per-macro saved settings (#983)."""

import json
from pathlib import Path

import pytest

from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.macro.permissions import protected_reason, resolve_path
from opensak.macro.settings import MAX_SETTINGS_BYTES, settings_file



class FakeHost:
    """The settings functions need nothing from the host."""

    def end_macro(self):
        pass

    def approve_folder(self, target, folder, write, macro=None):
        return FolderApproval.DENY

    def __getattr__(self, name):
        # Bound into the opensak table at every run, never called here.
        return lambda *args: None


@pytest.fixture(autouse=True)
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "install" / "macros"
    d.mkdir(parents=True)
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d


def _run(source, macro_path, **kwargs):
    out: list[str] = []
    MacroRuntime(FakeHost(), output=out.append, **kwargs).run(
        source, macro_path=macro_path
    )
    return out


def test_first_load_is_empty_and_save_round_trips(macros_dir):
    macro = macros_dir / "nearby.lua"
    out = _run("""
        local s = opensak.settings.load()
        print(next(s) == nil)
        s.maxdist = 25
        s.ratio = 2.5
        s.name = "Zürich"
        s.found = false
        s.types = {"Traditional", "Multi"}
        s.nested = {a = {b = 1}}
        opensak.settings.save(s)
    """, macro)
    assert out == ["true"]
    file = macros_dir / "settings" / "nearby.json"
    assert json.loads(file.read_text(encoding="utf-8")) == {
        "maxdist": 25, "ratio": 2.5, "name": "Zürich", "found": False,
        "types": ["Traditional", "Multi"], "nested": {"a": {"b": 1}},
    }
    out = _run("""
        local s = opensak.settings.load()
        print(s.maxdist, math.type(s.maxdist), s.ratio, s.name, s.found,
              #s.types, s.types[2], s.nested.a.b)
    """, macro)
    assert out == ["25\tinteger\t2.5\tZürich\tfalse\t2\tMulti\t1"]


def test_clear_resets_to_empty(macros_dir):
    macro = macros_dir / "m.lua"
    _run("opensak.settings.save({x = 1})", macro)
    out = _run("""
        opensak.settings.clear()
        opensak.settings.clear()  -- nothing saved: no error
        print(next(opensak.settings.load()) == nil)
    """, macro)
    assert out == ["true"]
    assert not (macros_dir / "settings" / "m.json").exists()


def test_each_macro_has_its_own_settings(macros_dir):
    _run("opensak.settings.save({who = 'a'})", macros_dir / "a.lua")
    _run("opensak.settings.save({who = 'sub'})", macros_dir / "examples" / "a.lua")
    assert _run("print(opensak.settings.load().who)", macros_dir / "b.lua") == ["nil"]
    assert _run("print(opensak.settings.load().who)", macros_dir / "a.lua") == ["a"]
    assert _run("print(opensak.settings.load().who)",
                macros_dir / "examples" / "a.lua") == ["sub"]
    assert (macros_dir / "settings" / "examples" / "a.json").is_file()


def test_macro_outside_macros_folder_is_keyed_by_file_name(macros_dir, tmp_path):
    _run("opensak.settings.save({x = 1})", tmp_path / "elsewhere" / "far.lua")
    assert (macros_dir / "settings" / "far.json").is_file()


def test_settings_dir_override(tmp_path, macros_dir):
    folder = tmp_path / "custom"
    _run("opensak.settings.save({x = 1})", macros_dir / "m.lua", settings_dir=folder)
    assert (folder / "m.json").is_file()


def test_unsaved_macro_has_no_settings():
    with pytest.raises(MacroError, match="save the macro first"):
        _run("opensak.settings.load()", None)


def test_other_macros_settings_cannot_be_read_as_files(macros_dir):
    _run("opensak.settings.save({secret = 1})", macros_dir / "a.lua")
    target = resolve_path(macros_dir / "settings" / "a.json")
    assert protected_reason(target) is not None
    # The rest of the macros folder stays readable.
    assert protected_reason(resolve_path(macros_dir / "data.csv")) is None
    with pytest.raises(MacroError, match="saved settings of macros"):
        _run('opensak.read_csv("settings/a.json")', macros_dir / "b.lua")


@pytest.mark.parametrize("value, message", [
    ("{f = print}", "cannot save a function at f"),
    ("{x = 0/0}", "only finite numbers"),
    ("{x = math.huge}", "only finite numbers"),
    ("{[1] = 'a', [3] = 'b'}", "cannot save a list with gaps"),
    ("{x = {[2] = 'b'}}", "cannot save a list with gaps at x"),
    ("{f = opensak.count}", "cannot save a function at f"),
    ("{[1.5] = 'a'}", "cannot save the key 1.5"),
    ("{a = {[true] = 1}}", "cannot save the key true at a"),
    ("42", "expects a table"),
    ("nil", "expects a table"),
])
def test_unsupported_values_are_rejected(macros_dir, value, message):
    with pytest.raises(MacroError, match=message):
        _run(f"opensak.settings.save({value})", macros_dir / "m.lua")
    assert not (macros_dir / "settings" / "m.json").exists()


def test_cyclic_table_is_rejected(macros_dir):
    with pytest.raises(MacroError, match="cyclic table at a.b"):
        _run("""
            local t = {a = {}}
            t.a.b = t
            opensak.settings.save(t)
        """, macros_dir / "m.lua")


def test_shared_table_is_not_cyclic(macros_dir):
    out = _run("""
        local shared = {1, 2}
        opensak.settings.save({x = shared, y = shared})
        print(opensak.settings.load().y[2])
    """, macros_dir / "m.lua")
    assert out == ["2"]


def test_too_large_is_rejected_and_old_settings_kept(macros_dir):
    macro = macros_dir / "m.lua"
    _run("opensak.settings.save({x = 1})", macro)
    with pytest.raises(MacroError, match="too large"):
        _run(f'opensak.settings.save({{s = string.rep("x", {MAX_SETTINGS_BYTES})}})', macro)
    assert _run("print(opensak.settings.load().x)", macro) == ["1"]


def test_damaged_file_gives_clear_error(macros_dir):
    file = settings_file(macros_dir / "settings", macros_dir / "m.lua", macros_dir)
    file.parent.mkdir(parents=True)
    file.write_text("{not json", encoding="utf-8")
    with pytest.raises(MacroError, match="damaged"):
        _run("opensak.settings.load()", macros_dir / "m.lua")
    _run("opensak.settings.clear()", macros_dir / "m.lua")
    assert not file.exists()


def test_replacing_string_does_not_break_cycle_check(macros_dir):
    out = _run("""
        string = nil
        opensak.settings.save({a = {1}})
        print(opensak.settings.load().a[1])
    """, macros_dir / "m.lua")
    assert out == ["1"]


EXAMPLE = (Path(__file__).resolve().parents[2] / "macros" / "examples"
           / "new_since_last_run.lua")


class FilterHost(FakeHost):
    def __init__(self, count):
        self.count, self.applied = count, []

    def apply_filter(self, filterset, label):
        self.applied.append((filterset, label))
        return self.count


def test_new_since_last_run_example(macros_dir):
    macro = macros_dir / "examples" / EXAMPLE.name
    source = EXAMPLE.read_text(encoding="utf-8")

    def run(count):
        host, out = FilterHost(count), []
        MacroRuntime(host, output=out.append).run(source, macro_path=macro)
        return host, out

    host, out = run(7)
    assert host.applied == []
    assert out[0].startswith("First run")
    first = json.loads((macros_dir / "settings" / "examples"
                        / "new_since_last_run.json").read_text(encoding="utf-8"))
    since = first["runs"][0]["time"]

    host, out = run(7)
    ((filterset, label),) = host.applied
    assert label == f"New since {since} UTC"
    assert out[0] == f"7 unfound caches updated since {since} UTC"
    assert out[-1] == f"  {since}  0 caches"
    assert out[-2].endswith("  7 caches")

    for _ in range(6):
        run(0)
    saved = json.loads((macros_dir / "settings" / "examples"
                        / "new_since_last_run.json").read_text(encoding="utf-8"))
    assert len(saved["runs"]) == 5
