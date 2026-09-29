"""tests/unit-tests/test_macro_runtime.py — Lua macro runtime (proof of concept).

The runtime is Qt-free: a fake MacroHost stands in for the main window. The
DB-backed host applies the FilterSet the macro built against a real test
database, so "a Lua script selects caches" is covered end to end.
"""

import pytest

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
