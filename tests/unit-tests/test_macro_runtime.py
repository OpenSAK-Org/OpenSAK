"""tests/unit-tests/test_macro_runtime.py — Lua macro runtime (proof of concept).

The runtime is Qt-free: a fake MacroHost stands in for the main window. The
DB-backed host applies the FilterSet the macro built against a real test
database, so "a Lua script selects caches" is covered end to end.
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensak.db.corrected_coords import set_corrected_coords
from opensak.db.database import get_session
from opensak.db.models import Cache, UserNote
from opensak.export.file_export_settings import FileExportProfile, FileExportSettings
from opensak.filters.engine import (
    CacheTypeFilter, DifficultyFilter, FilterProfile, FilterSet, GcCodeFilter,
    NotFoundFilter, apply_filters_auto,
)
from opensak.macro import FolderApproval, MacroError, MacroRuntime, build_filterset
from opensak.macro import cache_data
from opensak.macro.permissions import FolderPermission


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

    caches: list = []

    def filtered_caches(self):
        return self.caches

    def filter_name(self):
        return self.applied[-1][1] if self.applied else ""

    def database_name(self):
        return "TestDB"

    def center_name(self):
        return "Home"

    def set_corrected_coords(self, gc_code, lat, lon):
        self.corrected = getattr(self, "corrected", [])
        self.corrected.append((gc_code, lat, lon))
        return gc_code != "GCNONE"

    current: str | None = None

    def current_code(self):
        return self.current

    def selected_codes(self):
        return [self.current] if self.current else []

    answer = True

    def confirm(self, message):
        self.asked = getattr(self, "asked", [])
        self.asked.append(message)
        return self.answer

    def end_macro(self):
        self.ended = getattr(self, "ended", 0) + 1

    def approve_folder(self, target, folder, write):
        return FolderApproval.DENY


class DbHost(FakeHost):
    """Applies the filter against the test DB and remembers the selection."""

    def __init__(self):
        super().__init__()
        self.selected: set[str] = set()
        self.filterset, self.label = FilterSet(), ""

    def apply_filter(self, filterset, label):
        with get_session() as s:
            codes = {c.gc_code for c in apply_filters_auto(s, filterset)}
        if codes:
            self.selected = codes
            self.filterset, self.label = filterset, label
        return len(codes)

    def filtered_caches(self):
        with get_session() as s:
            return apply_filters_auto(s, self.filterset)

    def filter_name(self):
        return self.label

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
            ("GCMAC5", "Unknown Cache", 3.0, False),
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
    MacroRuntime(host, output=out.append, folder_permissions=_rw(tmp_path)).run(
        (FIXTURES / "csv_import.lua").read_text(encoding="utf-8"), base_dir=tmp_path)
    return host, out


def _rw(folder):
    """Read/write permission for *folder* only (tmp_path is no longer inside
    the default temp folder)."""
    return [FolderPermission(str(folder), read=True, write=True)]


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
    MacroRuntime(FakeHost(), output=out.append, folder_permissions=_rw(tmp_path)).run("""
        local rows = opensak.read_csv("a.csv")
        print(#rows, rows[1].code, rows[1].lat, rows[2].lon)
        print(opensak.read_csv("b.csv", "|")[1].x)
    """, base_dir=tmp_path)
    assert out == ["2\tGC1\t47.1\t7", "y"]


def test_read_csv_missing_file(tmp_path):
    with pytest.raises(MacroError, match="file not found"):
        MacroRuntime(FakeHost(), output=lambda _: None, folder_permissions=_rw(tmp_path)).run(
            'opensak.read_csv("nope.csv")', base_dir=tmp_path)


def test_read_csv_without_base_dir_resolves_in_macros_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: tmp_path)
    (tmp_path / "solved.csv").write_text("code\nGC9\n", encoding="utf-8")
    _, out = _run('print(opensak.read_csv("solved.csv")[1].code)')
    assert out == ["GC9"]


def test_install_example_copies_macro_and_csv_into_macros_folder(tmp_path, monkeypatch):
    from opensak.macro.examples import install_example, list_examples

    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: tmp_path)
    assert "corrected_coords_from_csv.lua" in list_examples()
    path = install_example("corrected_coords_from_csv.lua")
    assert path == tmp_path / "examples" / "corrected_coords_from_csv.lua"
    assert (tmp_path / "examples" / "corrected_coords.csv").is_file()
    assert not (tmp_path / "examples" / "export_filters_to_gpx.lua").exists()

    # A second open keeps the user's edits.
    path.write_text("-- edited", encoding="utf-8")
    install_example("corrected_coords_from_csv.lua")
    assert path.read_text(encoding="utf-8") == "-- edited"

    with pytest.raises(FileNotFoundError):
        install_example("nope.lua")


def test_install_example_restore_keeps_changed_copy_as_bak(tmp_path, monkeypatch):
    from opensak.macro.examples import (
        bundled_examples_dir, example_differs, install_example,
    )

    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: tmp_path)
    name = "corrected_coords_from_csv.lua"
    original = (bundled_examples_dir() / name).read_text(encoding="utf-8")
    assert not example_differs(name)             # not installed yet
    path = install_example(name)
    assert not example_differs(name)             # installed, unchanged

    # Restoring an unchanged copy writes no backup.
    install_example(name, restore=True)
    assert not list(path.parent.glob("*.bak"))

    # An edited (or outdated) copy differs; restore brings the original
    # back and keeps the old copy, data files stay as they are.
    path.write_text("-- edited", encoding="utf-8")
    csv = path.parent / "corrected_coords.csv"
    csv.write_text("code\nGC1\n", encoding="utf-8")
    assert example_differs(name)
    assert install_example(name, restore=True) == path
    assert path.read_text(encoding="utf-8") == original
    assert not example_differs(name)
    [bak] = path.parent.glob(f"{name}.*.bak")
    assert bak.read_text(encoding="utf-8") == "-- edited"
    assert csv.read_text(encoding="utf-8") == "code\nGC1\n"


def test_temp_and_macros_dir():
    from opensak.macro.permissions import macros_dir, temp_dir

    _, out = _run("print(opensak.temp_dir()); print(opensak.macros_dir())")
    assert out == [str(temp_dir()), str(macros_dir())]


def test_read_csv_from_temp_dir():
    import tempfile

    from opensak.macro.permissions import temp_dir

    with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8", dir=temp_dir()
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
        _macro_caches_removed=False,
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


def test_mainwindow_reloads_once_after_macro_moved_caches():
    from types import SimpleNamespace
    from opensak.gui import mainwindow as mw

    calls = {"row": [], "moved": 0}
    win = SimpleNamespace(
        _macro_changed_codes={"GC1"},
        _macro_caches_removed=True,
        _on_corrected_coords_changed=calls["row"].append,
        _on_caches_moved=lambda: calls.__setitem__("moved", calls["moved"] + 1),
    )
    mw.MainWindow.end_macro(win)

    assert calls == {"row": [], "moved": 1}
    assert win._macro_changed_codes == set() and win._macro_caches_removed is False


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


# ── Raw SQL, read-only ───────────────────────────────────────────────────────

def test_sql_returns_rows_keyed_by_column():
    _, out = _run("""
        local rows = opensak.sql(
            "SELECT gc_code, difficulty FROM caches WHERE gc_code LIKE 'GCMAC%' "
            .. "AND found = ? ORDER BY gc_code", { false })
        for _, r in ipairs(rows) do print(r.gc_code, r.difficulty) end
        local n = opensak.sql("SELECT COUNT(*) AS n FROM caches WHERE cache_type = :t "
            .. "AND gc_code LIKE :p", { t = "Traditional Cache", p = "GCMAC%" })[1].n
        local r = opensak.sql("SELECT NULL AS gone, 'x' AS kept")[1]
        print(n, r.gone, r.kept)
    """)
    assert out == ["GCMAC1\t1.5", "GCMAC2\t4", "GCMAC3\t1", "GCMAC5\t3", "3\tnil\tx"]


def test_sql_each_streams_rows(monkeypatch):
    from opensak.macro import sql
    monkeypatch.setattr(sql, "FETCH_SIZE", 2)
    _, out = _run("""
        local codes = {}
        for r in opensak.sql_each("SELECT gc_code FROM caches WHERE gc_code LIKE ? "
                                  .. "ORDER BY gc_code", { "GCMAC%" }) do
            codes[#codes + 1] = r.gc_code
        end
        print(table.concat(codes, ","))
    """)
    assert out == ["GCMAC1,GCMAC2,GCMAC3,GCMAC4,GCMAC5"]


def test_sql_sees_writes_made_earlier_in_the_macro():
    _add_caches(["GCSQL1"])
    _, out = _run("""
        opensak.set_corrected("GCSQL1", 46.5, 7.5)
        print(opensak.sql("SELECT n.corrected_lat AS lat FROM user_notes n "
            .. "JOIN caches c ON c.id = n.cache_id WHERE c.gc_code = 'GCSQL1'")[1].lat)
    """, host=DbHost())
    assert out == ["46.5"]


@pytest.mark.parametrize("query", [
    "DELETE FROM caches",
    "UPDATE caches SET name = 'x'",
    "INSERT INTO caches (gc_code) VALUES ('GCX')",
    "WITH x AS (SELECT 1) DELETE FROM caches",
    "CREATE TABLE t (x)",
    "DROP TABLE caches",
    "PRAGMA query_only = OFF",
    "PRAGMA journal_mode = DELETE",
    "ATTACH DATABASE 'other.db' AS other",
    "BEGIN",
])
def test_sql_is_read_only(query):
    with pytest.raises(MacroError, match="read-only"):
        _run(f'opensak.sql("{query}")')
    _, out = _run("print(opensak.sql(\"SELECT COUNT(*) AS n FROM caches WHERE gc_code LIKE 'GCMAC%'\")[1].n)")
    assert out == ["5"]


def test_sql_connection_is_read_only_even_without_authorizer(monkeypatch):
    """Defence in depth: mode=ro refuses writes at the file level."""
    import sqlite3

    from opensak.db.database import get_engine
    from opensak.macro import sql
    monkeypatch.setattr(sql, "_authorizer", lambda *a: sqlite3.SQLITE_OK)
    db = sql.ReadOnlyDatabase(get_engine().url.database)
    try:
        db.query("PRAGMA query_only = OFF")
        with pytest.raises(sql.SqlError, match="readonly"):
            db.query("DELETE FROM caches")
    finally:
        db.close()


def test_sql_rejects_several_statements():
    with pytest.raises(MacroError, match="one statement"):
        _run('opensak.sql("SELECT 1; DELETE FROM caches")')


def test_sql_aborts_slow_query():
    from opensak.db.database import get_engine
    from opensak.macro import sql
    db = sql.ReadOnlyDatabase(get_engine().url.database, timeout_s=0.05)
    try:
        with pytest.raises(sql.SqlError, match="took longer than 0.05 s"):
            db.query("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
                     "SELECT COUNT(*) FROM n")
        assert db.query("SELECT 1 AS one") == [{"one": 1}]
    finally:
        db.close()


def test_sql_row_limit():
    from opensak.db.database import get_engine
    from opensak.macro import sql
    db = sql.ReadOnlyDatabase(get_engine().url.database)
    try:
        assert len(db.query("SELECT gc_code FROM caches LIMIT 2", max_rows=2)) == 2
        with pytest.raises(sql.SqlError, match="more than 2 rows"):
            db.query("SELECT gc_code FROM caches LIMIT 3", max_rows=2)
    finally:
        db.close()


@pytest.mark.parametrize("call,msg", [
    ("opensak.sql()", "opensak.sql expects an SQL query"),
    ('opensak.sql("SELECT ?", 1)', "parameters must be a table"),
    ('opensak.sql("SELECT ?", { {} })', "SQL parameters must be numbers"),
    ('opensak.sql("SELECT ?, :a", { 1, a = 2 })', "not both"),
    ('opensak.sql("SELECT * FROM nope")', "SQL error: no such table"),
    ('for r in opensak.sql_each("SELECT * FROM nope") do end', "SQL error: no such table"),
    ('opensak.columns("nope")', "no table named 'nope'"),
])
def test_sql_rejects_bad_input(call, msg):
    with pytest.raises(MacroError, match=msg):
        _run(call)


# ── Cache access ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def full_cache(seed):
    """One cache with every kind of field filled in."""
    with get_session() as s:
        cache = Cache(
            gc_code="GCACC1", name="All fields", cache_type="Unknown Cache",
            container="Small", latitude=47.1, longitude=8.2, difficulty=2.5,
            terrain=3.0, owner_name="Owner", placed_by="Placer",
            hidden_date=datetime(2020, 5, 17, 13, 45), found=True,
            found_date=datetime(2024, 1, 2), first_to_find=True, premium_only=True,
            country="Switzerland", state="Zürich", distance=12.5, bearing=90.0,
            user_flag=True, user_sort=7, user_data_2="Solved", color="#FF5733",
            encoded_hints="under the stone", url="https://coord.info/GCACC1",
            short_description="short", long_description="<p>long</p>",
            long_desc_html=True, waypoint_count=2, log_count=5,
            last_log_date=datetime(2025, 3, 4, 10, 0),
            last_gpx_update=datetime(2026, 9, 30, 18, 5, 9),
        )
        s.add(cache)
        s.flush()
        s.add(UserNote(cache_id=cache.id, note="my note"))
    set_corrected_coords("GCACC1", 47.2, 8.3)


def test_cache_returns_snapshot_with_api_field_names(full_cache):
    _, out = _run("""
        local c = opensak.cache("gcacc1")
        print(c.code, c.name, c.type, c.container, c.lat, c.lon, c.difficulty, c.terrain)
        print(c.owner, c.placed_by, c.hidden, c.found, c.found_date, c.ftf, c.premium)
        print(c.dnf, c.dnf_date, c.archived, c.country, c.state, c.county, c.favorite_points)
        print(c.user_flag, c.user_sort, #c.user_data, c.user_data[1] == "", c.user_data[2])
        print(c.color, c.note, c.hint, c.url, c.corrected.lat, c.corrected.lon)
        print(c.distance, c.bearing, c.waypoint_count, c.log_count, c.last_log_date)
        print(c.last_gpx_update, opensak.date.parse(c.last_gpx_update) ~= nil)
        c.name = "changed"
        print(opensak.cache("GCACC1").name, opensak.cache("GCNONE"))
    """)
    assert out == [
        "GCACC1	All fields	Unknown Cache	Small	47.1	8.2	2.5	3",
        "Owner	Placer	2020-05-17	true	2024-01-02	true	true",
        "false	nil	false	Switzerland	Zürich	nil	nil",
        "true	7	4	true	Solved",
        "#FF5733	my note	under the stone	https://coord.info/GCACC1	47.2	8.3",
        "12.5	90	2	5	2025-03-04",
        "2026-09-30T18:05:09	true",
        "All fields	nil",
    ]


def test_every_cache_field_is_loaded(full_cache):
    with get_session() as s:
        (record,) = cache_data.load_records(s, ["GCACC1"])
    assert tuple(record) == cache_data.FIELD_NAMES
    assert record["corrected"] == {"lat": 47.2, "lon": 8.3}


def test_cache_without_corrected_coords_has_nil_corrected():
    _, out = _run('print(opensak.cache("GCMAC1").corrected)')
    assert out == ["nil"]


def test_caches_iterates_active_filter_in_order():
    host = DbHost()
    _, out = _run("""
        opensak.filter{ type = "Traditional", code = "GCMAC" }
        for c in opensak.caches() do print(c.code, c.difficulty) end
        print(table.concat(opensak.codes(), ","))
    """, host=host)
    assert out == ["GCMAC1	1.5", "GCMAC2	4", "GCMAC4	1", "GCMAC1,GCMAC2,GCMAC4"]


def test_caches_with_filter_keys_leaves_view_unchanged():
    host = DbHost()
    _, out = _run("""
        for c in opensak.caches{ code = "GCMAC", found = false, fields = {"difficulty"} } do
            local keys = {}
            for k in pairs(c) do keys[#keys + 1] = k end
            table.sort(keys)
            print(c.code, table.concat(keys, ","))
        end
    """, host=host)
    assert out == [f"GCMAC{i}	code,difficulty" for i in (1, 2, 3, 5)]
    assert host.selected == set() and host.label == ""


def test_caches_loads_in_chunks(monkeypatch):
    monkeypatch.setattr(cache_data, "CHUNK_SIZE", 2)
    _, out = _run("""
        local codes = {}
        for c in opensak.caches{ code = "GCMAC", fields = {"code"} } do codes[#codes + 1] = c.code end
        print(table.concat(codes, ","))
    """)
    assert out == ["GCMAC1,GCMAC2,GCMAC3,GCMAC4,GCMAC5"]


@pytest.mark.parametrize("call,msg", [
    ('opensak.caches{ fields = {"code", "bogus"} }', r"unknown cache field\(s\) \['bogus'\]"),
    ('opensak.caches{ bogus = 1 }', "unknown filter key"),
    ('opensak.caches("GC1")', "expects nothing or a table"),
    ("opensak.cache()", "opensak.cache expects a GC code"),
    ("opensak.description(1)", "opensak.description expects a GC code"),
])
def test_cache_access_rejects_bad_input(call, msg):
    with pytest.raises(MacroError, match=msg):
        _run(call)


def test_tables_and_columns():
    _, out = _run("""
        local t = {}
        for _, name in ipairs(opensak.tables()) do t[name] = true end
        print(t.caches, t.logs, t.user_notes)
        local c = opensak.columns("caches")[2]
        print(c.name, c.type)
    """)
    assert out == ["true\ttrue\ttrue", "gc_code\tVARCHAR(16)"]


def test_sql_connection_is_closed_after_run():
    host = FakeHost()
    runtime = MacroRuntime(host, output=lambda _: None)
    runtime.run('opensak.sql("SELECT 1")')
    assert runtime._sql_db is None


def test_current_and_selected_follow_host():
    host = FakeHost()
    _, out = _run("print(opensak.current(), #opensak.selected())", host=host)
    host.current = "GCMAC3"
    _, out2 = _run("""
        print(opensak.current().name, opensak.selected()[1])
    """, host=host)
    assert out == ["nil	0"]
    assert out2 == ["GCMAC3	GCMAC3"]


def test_description(full_cache):
    _, out = _run("""
        local d = opensak.description("GCACC1")
        print(d.short, d.long, d.html, opensak.description("GCNONE"))
    """)
    assert out == ["short	<p>long</p>	true	nil"]


# ── GPX export ───────────────────────────────────────────────────────────────

def _export_runtime(tmp_path, host=None, **settings):
    """Runtime with an export setting "GPX Export" (writing to tmp_path/out
    unless *settings* say otherwise) and write permission for tmp_path."""
    settings.setdefault("folder", str(tmp_path / "out"))
    FileExportProfile("GPX Export", FileExportSettings(**settings)).save(
        tmp_path / "export_settings")
    out: list[str] = []
    runtime = MacroRuntime(
        host or DbHost(), output=out.append,
        profiles_dir=tmp_path / "filters",
        export_settings_dir=tmp_path / "export_settings",
        folder_permissions=[FolderPermission(str(tmp_path), read=True, write=True)],
    )
    return runtime, out


def _exported(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {code for code in ("GCMAC1", "GCMAC2", "GCMAC3", "GCMAC4", "GCMAC5")
            if code in text}


def test_export_file_writes_caches_of_active_filter(tmp_path):
    runtime, out = _export_runtime(tmp_path, file_name="{filter}_{count}_{database}_{center}")
    runtime.run("""
        opensak.filter{ type = "Multi-cache", label = "Multis" }
        print(opensak.export_file("GPX Export"))
    """)
    target = (tmp_path / "out" / "Multis_1_TestDB_Home.gpx").resolve()
    assert out == [f"{target}\t1"]
    assert _exported(target) == {"GCMAC3"}


def test_export_file_follows_format_and_record_limit(tmp_path):
    runtime, out = _export_runtime(tmp_path, fmt="loc", file_name="{count}", max_records=2)
    runtime.run("""
        opensak.filter{ type = "Traditional", code = "GCMAC" }
        local path, n = opensak.export_file("GPX Export")
        print(n)
    """)
    assert out == ["2"]
    assert len(_exported(tmp_path / "out" / "2.loc")) == 2


@pytest.mark.parametrize("if_exists, answer, written", [
    ("overwrite", None, True),
    ("skip", None, False),
    ("ask", True, True),
    ("ask", False, False),
])
def test_export_file_if_exists(tmp_path, if_exists, answer, written):
    target = tmp_path / "out" / "x.gpx"
    target.parent.mkdir()
    target.write_text("old", encoding="utf-8")
    host = DbHost()
    host.answer = answer
    runtime, out = _export_runtime(tmp_path, host, file_name="x", if_exists=if_exists)
    runtime.run("""
        opensak.filter{ type = "Multi-cache" }
        print(opensak.export_file("GPX Export") ~= nil)
    """)
    assert out == [str(written).lower()]
    assert (target.read_text(encoding="utf-8") != "old") is written
    assert len(getattr(host, "asked", [])) == (1 if if_exists == "ask" else 0)


def test_export_file_writes_nothing_without_caches(tmp_path):
    host = FakeHost()
    host.caches = [SimpleNamespace(latitude=None)]
    runtime, out = _export_runtime(tmp_path, host)
    runtime.run('print(opensak.export_file("GPX Export"))')
    assert out == ["nil"]
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("call, folder, msg", [
    ("opensak.export_file()", None, "expects the name"),
    ('opensak.export_file("Missing")', None, "no saved export setting"),
    ('opensak.export_file("GPX Export")', "", "has no folder"),
    ('opensak.export_file("GPX Export")', "<outside>", "may not write"),
])
def test_export_file_rejects_bad_setup(tmp_path, tmp_path_factory, call, folder, msg):
    settings = {}
    if folder is not None:
        settings["folder"] = (str(tmp_path_factory.mktemp("no_permission"))
                              if folder == "<outside>" else folder)
    runtime, _ = _export_runtime(tmp_path, **settings)
    with pytest.raises(MacroError, match=msg):
        runtime.run(f'opensak.filter{{ type = "Multi-cache" }} {call}')
    if folder:
        assert not list(Path(settings["folder"]).iterdir())


def test_export_file_folder_argument_overrides_setting(tmp_path):
    runtime, out = _export_runtime(tmp_path, folder="", file_name="{filter}")
    runtime.run(f"""
        opensak.filter{{ type = "Multi-cache", label = "Multis" }}
        print(opensak.export_file("GPX Export", {str(tmp_path / "other")!r}))
    """)
    target = (tmp_path / "other" / "Multis.gpx").resolve()
    assert out == [f"{target}	1"]
    assert _exported(target) == {"GCMAC3"}


@pytest.mark.parametrize("answer, written", [
    (FolderApproval.ONCE, True),
    (FolderApproval.DENY, False),
])
def test_export_file_asks_before_writing_to_unapproved_folder(
        tmp_path, tmp_path_factory, answer, written):
    folder = tmp_path_factory.mktemp("unapproved")
    asked = []

    class Host(DbHost):
        def approve_folder(self, target, folder, write):
            asked.append((folder, write))
            return answer

    runtime, out = _export_runtime(tmp_path, Host(), folder=str(folder), file_name="x")
    script = 'opensak.filter{ type = "Multi-cache" } print(opensak.export_file("GPX Export"))'
    if written:
        runtime.run(script)
        assert out == [f"{(folder / 'x.gpx').resolve()}	1"]
    else:
        with pytest.raises(MacroError, match="denied by the user"):
            runtime.run(script)
    assert asked == [(folder.resolve(), True)]
    assert (folder / "x.gpx").exists() is written


# ── Example: export_filters_to_gpx.lua ───────────────────────────────────────

EXPORT_EXAMPLE = EXAMPLES / "export_filters_to_gpx.lua"

# The filters named in the example's FILTERS list, narrowed to the seeded caches.
_EXAMPLE_FILTERS = {
    "Traditionals": "Traditional Cache",
    "Multi-caches": "Multi-cache",
    "Mysteries": "Unknown Cache",
}


def _save_example_filters(tmp_path, names=tuple(_EXAMPLE_FILTERS)):
    for name in names:
        fs = FilterSet(mode="AND")
        fs.add(CacheTypeFilter([_EXAMPLE_FILTERS[name]]))
        fs.add(GcCodeFilter("GCMAC"))
        FilterProfile(name, fs).save(tmp_path / "filters")


@pytest.fixture
def example_temp_dir(tmp_path, monkeypatch):
    """opensak.temp_dir() as the example sees it: tmp_path/out."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("opensak.macro.runtime.temp_dir", lambda: out_dir)
    return out_dir


def _run_export_example(runtime):
    runtime.run(EXPORT_EXAMPLE.read_text(encoding="utf-8"), base_dir=EXAMPLES)


def test_export_example_writes_one_file_per_filter(tmp_path, example_temp_dir):
    _save_example_filters(tmp_path)
    runtime, out = _export_runtime(tmp_path, folder="", file_name="{filter}",
                                   if_exists="overwrite")

    _run_export_example(runtime)

    out_dir = tmp_path / "out"
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "Multi-caches.gpx", "Mysteries.gpx", "Traditionals.gpx"]
    assert _exported(out_dir / "Traditionals.gpx") == {"GCMAC1", "GCMAC2", "GCMAC4"}
    assert _exported(out_dir / "Multi-caches.gpx") == {"GCMAC3"}
    assert _exported(out_dir / "Mysteries.gpx") == {"GCMAC5"}
    assert out[0] == f"Traditionals: 3 caches → {(out_dir / 'Traditionals.gpx').resolve()}"
    assert out[-1] == f"Done: 3 exported, 0 skipped (folder: {out_dir})"


def test_export_example_skips_unsaved_and_empty_filters(tmp_path, example_temp_dir):
    _save_example_filters(tmp_path, ("Traditionals",))
    FilterProfile("Mysteries", FilterSet().add(GcCodeFilter("GCNOMATCH"))).save(
        tmp_path / "filters")
    runtime, out = _export_runtime(tmp_path, file_name="{filter}")

    _run_export_example(runtime)

    assert "Multi-caches: no saved filter with this name — skipped" in out
    assert "Mysteries: no caches match — skipped" in out
    assert out[-1] == f"Done: 1 exported, 2 skipped (folder: {example_temp_dir})"
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["Traditionals.gpx"]


def test_export_example_reports_existing_file(tmp_path, example_temp_dir):
    _save_example_filters(tmp_path, ("Traditionals",))
    (tmp_path / "out" / "Traditionals.gpx").write_text("old", encoding="utf-8")
    runtime, out = _export_runtime(tmp_path, file_name="{filter}", if_exists="skip")

    _run_export_example(runtime)

    assert "Traditionals: file exists — not overwritten" in out
    assert (tmp_path / "out" / "Traditionals.gpx").read_text(encoding="utf-8") == "old"


def test_export_example_stops_when_file_name_lacks_filter(tmp_path, example_temp_dir):
    _save_example_filters(tmp_path)
    runtime, _ = _export_runtime(tmp_path, file_name="caches", if_exists="overwrite")

    with pytest.raises(MacroError, match="Traditionals and Multi-caches both exported to"):
        _run_export_example(runtime)
