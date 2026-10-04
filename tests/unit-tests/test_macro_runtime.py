"""tests/unit-tests/test_macro_runtime.py — Lua macro runtime (proof of concept).

The runtime is Qt-free: a fake MacroHost stands in for the main window. The
DB-backed host applies the FilterSet the macro built against a real test
database, so "a Lua script selects caches" is covered end to end.
"""

from pathlib import Path

import pytest

from opensak.db.corrected_coords import set_corrected_coords
from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.filters.engine import (
    CacheTypeFilter, DifficultyFilter, FilterProfile, FilterSet, NotFoundFilter,
    apply_filters_auto,
)
from opensak.macro import MacroError, MacroRuntime, build_filterset


class FakeHost:
    def __init__(self, count: int = 3):
        self.applied: list[tuple[FilterSet, str]] = []
        self.cleared = 0
        self._count = count

    def apply_filter(self, filterset, label):
        self.applied.append((filterset, label))
        return self._count

    def clear_filter(self):
        self.cleared += 1

    def cache_count(self):
        return 99

    def set_corrected_coords(self, gc_code, lat, lon):
        self.corrected = getattr(self, "corrected", [])
        self.corrected.append((gc_code, lat, lon))
        return gc_code != "GCNONE"

    answer = True

    def confirm(self, message):
        self.asked = getattr(self, "asked", [])
        self.asked.append(message)
        return self.answer

    def end_macro(self):
        self.ended = getattr(self, "ended", 0) + 1


class DbHost(FakeHost):
    """Applies the filter against the test DB and remembers the selection."""

    def __init__(self):
        super().__init__()
        self.selected: set[str] = set()

    def apply_filter(self, filterset, label):
        with get_session() as s:
            codes = {c.gc_code for c in apply_filters_auto(s, filterset)}
        if codes:
            self.selected = codes
        return len(codes)

    def set_corrected_coords(self, gc_code, lat, lon):
        return set_corrected_coords(gc_code, lat, lon)


def _run(source, host=None, **kwargs):
    out: list[str] = []
    host = host or FakeHost()
    MacroRuntime(host, output=out.append, **kwargs).run(source)
    return host, out


# ── Lua table → FilterSet ────────────────────────────────────────────────────

def test_build_filterset_maps_keys():
    fs, label = build_filterset({
        "type": "Traditional", "difficulty": 2, "found": False, "label": "X",
    })
    assert label == "X"
    kinds = [type(f) for f in fs._filters]
    assert kinds == [CacheTypeFilter, DifficultyFilter, NotFoundFilter]
    assert fs._filters[0].types == ["Traditional Cache"]
    assert (fs._filters[1].min_difficulty, fs._filters[1].max_difficulty) == (2.0, 2.0)


@pytest.mark.parametrize("spec,msg", [
    ({"foo": 1}, "unknown filter key"),
    ({"type": "Bogus"}, "unknown cache type"),
    ({"difficulty": "hard"}, "difficulty must be"),
    ({"label": "only a label"}, "at least one criterion"),
])
def test_build_filterset_rejects_bad_input(spec, msg):
    with pytest.raises(MacroError, match=msg):
        build_filterset(spec)


# ── API ──────────────────────────────────────────────────────────────────────

def test_filter_call_reaches_host_and_returns_count():
    host, out = _run("""
        local n = opensak.filter{ type = {"Traditional", "Multi-cache"}, terrain = {1, 3} }
        print("n", n, opensak.count())
        opensak.clear_filter()
    """)
    assert out == ["n\t3\t99"]
    assert host.cleared == 1
    fs, label = host.applied[0]
    assert label == "Macro"
    assert fs._filters[0].types == ["Traditional Cache", "Multi-cache"]


def test_filter_profile(tmp_path):
    FilterProfile("Easy", FilterSet().add(DifficultyFilter(1, 2))).save(tmp_path)
    host, out = _run(
        'print(#opensak.profiles()) opensak.filter_profile("Easy")',
        profiles_dir=tmp_path,
    )
    assert out == ["1"]
    assert host.applied[0][1] == "Easy"
    with pytest.raises(MacroError, match="no saved filter profile"):
        _run('opensak.filter_profile("Missing")', profiles_dir=tmp_path)


# ── Sandbox ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", [
    'io.open("x")',
    'os.execute("echo hi")',
    'require("os")',
    'load("return 1")()',
    'python.eval("1")',
    'local f = opensak.filter; print(f.__globals__)',
])
def test_sandbox_blocks_escape_hatches(source):
    with pytest.raises(MacroError):
        _run(source)


def test_endless_loop_is_aborted():
    with pytest.raises(MacroError, match="instruction limit"):
        _run("while true do end", instruction_limit=100_000)


@pytest.mark.parametrize("source", [
    # pcall/xpcall must not be able to swallow the abort
    "while true do pcall(function() while true do end end) end",
    "while true do xpcall(function() while true do end end, function() while true do end end) end",
    "xpcall(function() error('x') end, function() while true do end end)",
    # hooks are per thread, so coroutines need their own
    "coroutine.wrap(function() while true do end end)()",
    "coroutine.resume(coroutine.create(function() while true do end end))",
    "while true do pcall(coroutine.wrap(function() while true do end end)) end",
    # the budget is shared, so many short coroutines don't multiply it
    "for i = 1, 1e9 do coroutine.wrap(function() for j = 1, 50000 do end end)() end",
])
def test_instruction_limit_cannot_be_bypassed(source):
    with pytest.raises(MacroError, match="instruction limit"):
        _run(source, instruction_limit=100_000)


def test_coroutines_still_work():
    _, out = _run("""
        local gen = coroutine.wrap(function(a) local b = coroutine.yield(a + 1) coroutine.yield(b * 2) end)
        local co = coroutine.create(function() coroutine.yield("y") return "r" end)
        print(gen(1), gen(5), select(2, coroutine.resume(co)), select(2, coroutine.resume(co)))
        print(pcall(error, "caught"))
        print(xpcall(error, function(e) return "handled " .. e end, "x", 0))
        print(xpcall(function(a, b) return a + b end, print, 1, 2))
    """)
    assert out == ["2\t10\ty\tr", "false\tcaught", "false\thandled x", "true\t3"]


def test_gc_metamethods_are_rejected():
    # finalizers run with hooks disabled, so a loop in one could not be stopped
    with pytest.raises(MacroError, match="__gc"):
        _run("setmetatable({}, { __gc = true })", instruction_limit=100_000)
    _, out = _run('print(getmetatable(setmetatable({}, { __index = {a = 1} })).__index.a)')
    assert out == ["1"]


def test_memory_limit():
    with pytest.raises(MacroError, match="memory limit"):
        _run('local s = string.rep("x", 1e9)', memory_limit=32 * 1024 * 1024)
    # caught inside Lua: the allocation simply fails, nothing is exhausted
    _, out = _run('print(pcall(string.rep, "x", 1e9))', memory_limit=32 * 1024 * 1024)
    assert out == ["false\tnot enough memory"]


def test_syntax_error_is_reported():
    with pytest.raises(MacroError, match="macro:1"):
        _run("this is not lua")


# ── End to end against a real database ───────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        for code, ctype, diff, found in [
            ("GCMAC1", "Traditional Cache", 1.5, False),
            ("GCMAC2", "Traditional Cache", 4.0, False),
            ("GCMAC3", "Multi-cache", 1.0, False),
            ("GCMAC4", "Traditional Cache", 1.0, True),
        ]:
            s.add(Cache(gc_code=code, name=code, cache_type=ctype, difficulty=diff,
                        terrain=1.0, found=found, latitude=47.0, longitude=8.0))


def test_macro_selects_caches_in_db():
    host, out = _run("""
        local n = opensak.filter{ type = "Traditional", difficulty = {1, 2}, found = false }
        print(n)
    """, host=DbHost())
    assert out == ["1"]
    assert host.selected == {"GCMAC1"}


def test_empty_result_keeps_previous_selection():
    host = DbHost()
    _run("""
        opensak.filter{ type = "Multi-cache" }
        assert(opensak.filter{ name = "does-not-exist" } == 0)
    """, host=host)
    assert host.selected == {"GCMAC3"}


# ── Corrected coordinates / CSV ──────────────────────────────────────────────

EXAMPLES = Path(__file__).resolve().parents[2] / "macros" / "examples"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "macros"


def _run_csv_import(csv_fixture, tmp_path, host=None):
    """Run the csv_import.lua fixture against a copy of *csv_fixture*."""
    (tmp_path / "corrected_coords.csv").write_bytes((FIXTURES / csv_fixture).read_bytes())
    host, out = host or DbHost(), []
    MacroRuntime(host, output=out.append).run(
        (FIXTURES / "csv_import.lua").read_text(encoding="utf-8"), base_dir=tmp_path)
    return host, out


def _add_caches(codes):
    with get_session() as s:
        for code in codes:
            s.add(Cache(gc_code=code, name=code, cache_type="Unknown Cache",
                        latitude=47.0, longitude=8.0))


def test_set_corrected_accepts_numbers_strings_and_coord_text():
    host, out = _run("""
        print(opensak.set_corrected("gc123", 47.5, 8.25))
        print(opensak.set_corrected("GC124", "47.5", "8.25"))
        print(opensak.set_corrected("GC125", "N47 30.000 E008 15.000"))
        print(opensak.clear_corrected("GC126"))
        print(opensak.set_corrected("GCNONE", 1, 2))
    """)
    assert out == ["true", "true", "true", "true", "false"]
    assert host.corrected[:3] == [("GC123", 47.5, 8.25)] + [("GC124", 47.5, 8.25)] + [
        ("GC125", pytest.approx(47.5), pytest.approx(8.25))]
    assert host.corrected[3] == ("GC126", None, None)


@pytest.mark.parametrize("call,msg", [
    ('opensak.set_corrected("GC1", 91, 0)', "out of range"),
    ('opensak.set_corrected("GC1", "somewhere")', "cannot parse coordinates"),
    ('opensak.set_corrected("GC1", "x", 8)', "must be numbers"),
    ('opensak.set_corrected(nil, 1, 2)', "expects a GC code"),
    ('opensak.set_corrected("GC1", 47)', "coordinate string"),
])
def test_set_corrected_rejects_bad_input(call, msg):
    host = FakeHost()
    with pytest.raises(MacroError, match=msg):
        _run(call, host=host)
    assert not getattr(host, "corrected", [])


def test_read_csv_sniffs_separator_and_resolves_relative_path(tmp_path):
    (tmp_path / "a.csv").write_text(
        "﻿code ; lat;lon\nGC1;47.1;8.2\n\nGC2;46;7\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("code|x\nGC3|y\n", encoding="utf-8")
    out: list[str] = []
    MacroRuntime(FakeHost(), output=out.append).run("""
        local rows = opensak.read_csv("a.csv")
        print(#rows, rows[1].code, rows[1].lat, rows[2].lon)
        print(opensak.read_csv("b.csv", "|")[1].x)
    """, base_dir=tmp_path)
    assert out == ["2\tGC1\t47.1\t7", "y"]


def test_read_csv_missing_file(tmp_path):
    with pytest.raises(MacroError, match="file not found"):
        MacroRuntime(FakeHost(), output=lambda _: None).run(
            'opensak.read_csv("nope.csv")', base_dir=tmp_path)


def test_temp_and_macros_dir():
    from opensak.macro.permissions import macros_dir, temp_dir

    _, out = _run("print(opensak.temp_dir()); print(opensak.macros_dir())")
    assert out == [str(temp_dir()), str(macros_dir())]


def test_read_csv_from_temp_dir():
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8"
    ) as f:
        f.write("code\nGC1\n")
    try:
        _, out = _run(
            f'print(opensak.read_csv(opensak.temp_dir() .. "/{Path(f.name).name}")[1].code)'
        )
        assert out == ["GC1"]
    finally:
        Path(f.name).unlink()


def test_confirm_returns_host_answer():
    host, out = _run('print(opensak.confirm("Go?"))')
    assert host.asked == ["Go?"] and out == ["true"]
    host = FakeHost()
    host.answer = False
    _, out = _run('print(opensak.confirm("Go?"))', host=host)
    assert out == ["false"]
    with pytest.raises(MacroError, match="expects a message"):
        _run("opensak.confirm()")


def test_end_macro_called_once_after_run_even_on_error():
    host, _ = _run('opensak.set_corrected("GC1", 47, 8) opensak.clear_corrected("GC2")')
    assert host.ended == 1
    host = FakeHost()
    with pytest.raises(MacroError):
        MacroRuntime(host, output=lambda _: None).run('opensak.set_corrected("GC1", 47, 8) error("boom")')
    assert host.ended == 1


@pytest.mark.parametrize("n, row_refreshes, full_reloads", [(3, 3, 0), (51, 0, 1)])
def test_mainwindow_batches_macro_refresh(n, row_refreshes, full_reloads):
    from types import SimpleNamespace
    from opensak.gui import mainwindow as mw

    calls = {"row": [], "full": 0, "detail": []}
    win = SimpleNamespace(
        _macro_changed_codes={f"GC{i}" for i in range(n)},
        _on_corrected_coords_changed=calls["row"].append,
        _refresh_cache_list=lambda: calls.__setitem__("full", calls["full"] + 1),
        _detail_panel=SimpleNamespace(_current_gc_code="GC1",
                                      show_cache=calls["detail"].append),
        _load_full_cache=lambda code: code,
    )
    mw.MainWindow.end_macro(win)

    assert len(calls["row"]) == row_refreshes
    assert calls["full"] == full_reloads
    assert calls["detail"] == (["GC1"] if full_reloads else [])
    assert win._macro_changed_codes == set()


@pytest.mark.parametrize("script", sorted(EXAMPLES.glob("*.lua")), ids=lambda p: p.name)
def test_example_macros_compile(script):
    """Compile only: catches syntax errors in the shipped examples. Calls to
    a renamed or removed opensak.* function are only found when run."""
    from lupa.lua54 import LuaRuntime
    LuaRuntime().compile(script.read_text(encoding="utf-8"))


def test_csv_import_sets_corrected_coords_in_every_format(tmp_path):
    codes = ["GCF1", "GCF2", "GCF3", "GCF4", "GCF5", "GCF6"]
    _add_caches(codes)
    set_corrected_coords("GCF4", 1.0, 1.0)   # overwritten by the CSV

    host, out = _run_csv_import("formats.csv", tmp_path)

    assert out[0] == "Read 6 row(s) from corrected_coords.csv"
    assert host.asked == ["6 will be set, 0 cleared (0 skipped, 0 invalid).\nContinue?"]
    assert out[-1] == "Done: 6 set, 0 cleared, 0 skipped, 0 not found, 0 failed"
    assert host.selected == set(codes)
    with get_session() as s:
        got = {c.gc_code: (c.user_note.corrected_lat, c.user_note.corrected_lon,
                           c.user_note.is_corrected)
               for c in s.query(Cache).filter(Cache.gc_code.in_(codes))}
    assert got["GCF1"] == (pytest.approx(47 + 21.689 / 60), pytest.approx(6 + 18.718 / 60), True)
    assert got["GCF2"][:2] == (pytest.approx(47 + 8.905 / 60), pytest.approx(9 + 42.534 / 60))
    assert got["GCF3"] == (pytest.approx(47.514093), pytest.approx(7.470118), True)
    assert got["GCF4"][:2] == (pytest.approx(46 + 40.099 / 60), pytest.approx(6 + 33.842 / 60))
    assert got["GCF5"][:2] == (pytest.approx(47 + 25 / 60 + 0.37 / 3600),
                               pytest.approx(8 + 5 / 60 + 31.97 / 3600))
    assert got["GCF6"][:2] == (pytest.approx(46.66695), pytest.approx(8.32197))


def test_csv_import_writes_nothing_when_summary_is_declined(tmp_path):
    _add_caches(["GCD1"])
    host = DbHost()
    host.answer = False

    host, out = _run_csv_import("declined.csv", tmp_path, host)

    assert host.asked == ["1 will be set, 1 cleared (1 skipped, 0 invalid).\nContinue?"]
    assert out[-1] == "Cancelled — nothing changed"
    with get_session() as s:
        cache = s.query(Cache).filter_by(gc_code="GCD1").one()
        assert cache.user_note is None or not cache.user_note.is_corrected


def test_csv_import_never_clears_on_empty_or_half_filled_rows(tmp_path):
    codes = ["GCE1", "GCE2", "GCE3", "GCE4", "GCE5"]
    _add_caches(codes)
    for code in codes:
        set_corrected_coords(code, 1.0, 2.0)   # already solved

    # partial_rows.csv: GCE1 nothing, GCE2 lat only, GCE3 lon only,
    # GCE4 "Clear", GCE5 lat + lon
    _, out = _run_csv_import("partial_rows.csv", tmp_path)

    assert "GCE1: no coordinates — skipped" in out
    assert "GCE2: lon missing" in out
    assert "GCE3: lat missing" in out
    assert "GCE4: corrected coordinates removed" in out
    assert out[-1] == "Done: 1 set, 1 cleared, 1 skipped, 0 not found, 2 failed"
    with get_session() as s:
        got = {c.gc_code: (c.user_note.corrected_lat, c.user_note.corrected_lon)
               for c in s.query(Cache).filter(Cache.gc_code.in_(codes))}
    assert got["GCE1"] == (1.0, 2.0)
    assert got["GCE2"] == (1.0, 2.0)
    assert got["GCE3"] == (1.0, 2.0)
    assert got["GCE4"] == (None, None)
    assert got["GCE5"] == (pytest.approx(47.5), pytest.approx(8.5))
