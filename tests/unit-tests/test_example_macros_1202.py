"""
tests/unit-tests/test_example_macros_1202.py — every example macro runs
(#1202).

The macros in macros/examples/ are the first thing people copy, so each one
runs here with the runtime the app uses, against the test database
tests/fixtures/macros/examples.gpx, and must finish without a Lua or API
error, without calling anything deprecated and without being refused a
folder. Each runs twice: with the feature flags as they will be in
the stable release (decision D1 in #1196: `lua-macro-net` off) and with all
beta flags on.

A new example is picked up by itself. The user is scripted: confirmations
are answered Yes, writes are allowed until OpenSAK closes, a form is closed
with its default button (Enter) and cancelled when it comes back. SCENARIOS
adds, per example, what the user does in its forms, what has to be in place
before it runs, and the change it has to make — an example that writes is
checked for the change, not only for running without an error.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, cast

import pytest

from opensak.db import companion
from opensak.db.corrected_coords import set_corrected_coords
from opensak.db.database import get_session
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache, UserNote, Waypoint
from opensak.export.file_export import active_database_name
from opensak.filters.engine import FilterSet, apply_filters_auto
from opensak.importer import import_gpx
from opensak.macro import FolderApproval, MacroRuntime, WriteApproval, db_access
from opensak.macro.runtime import MacroHost
from opensak.macro.permissions import temp_dir
from opensak.utils import flags
from tests.data import EXAMPLES_GPX, seed_example_macro_caches

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "macros" / "examples"
NAMES = sorted(p.name for p in EXAMPLES.glob("*.lua"))


def _load(name: str):
    """A module of this folder (the hyphenated folder is no package)."""
    spec = importlib.util.spec_from_file_location(f"_examples_{name[:-3]}",
                                                  Path(__file__).with_name(name))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


Scripted = _load("test_macro_forms.py").Scripted

# Feature flags per run: as decided for v1.22.0 stable (D1) and all on.
FLAGS = {
    "stable": {"lua_macros": True, "lua_macro_write": True, "companion_db": True,
               "lua_macro_net": False},
    "beta": {"lua_macros": True, "lua_macro_write": True, "companion_db": True,
             "lua_macro_net": True},
}


# ── The user and the window ──────────────────────────────────────────────────


def _buttons(c) -> list:
    return [f for f in c.form.walk() if f.type == "button" and f.id]


def press_enter(c) -> None:
    """Close the form with its default button, as Enter does."""
    buttons = _buttons(c)
    default = next((b for b in buttons if b.get("default")), None) or next(
        b for b in buttons if not b.get("cancel") and b.get("on_click") is None)
    c.click(default.id)


def press_escape(c) -> None:
    cancel = next((b for b in _buttons(c) if b.get("cancel")), None)
    if cancel is not None:
        c.click(cancel.id)
    else:
        c.close_form(None)


class Window:
    """A progress window nobody cancels."""

    def __init__(self):
        self.steps = 0
        self.closed = False

    def update(self, value, text):
        self.steps += 1
        return True

    def close(self):
        self.closed = True


class ExampleHost:
    """The main window: the real DatabaseManager and active database, a
    user who says Yes, allows writes and closes forms with Enter."""

    def __init__(self, ctx: "Context"):
        self.ctx = ctx
        self.filterset, self.label = FilterSet(), ""
        self.applied: list[tuple[str, int]] = []
        self.current: Optional[str] = "GCEX003"
        self.selected: list[str] = []
        self.opened: list[Path] = []
        self.messages: list[str] = []
        self.forms: list[Any] = []
        self.asked_folders: list[tuple[Path, bool]] = []
        self.denied_folders: list[tuple[Path, bool]] = []
        self.user: Optional[Callable[[Any], None]] = None

    # filter and list
    def apply_filter(self, filterset, label):
        with get_session() as s:
            n = len(apply_filters_auto(s, filterset))
        if n:
            self.filterset, self.label = filterset, label
        self.applied.append((label, n))
        return n

    def clear_filter(self):
        self.filterset, self.label = FilterSet(), ""

    def filtered_caches(self):
        with get_session() as s:
            return apply_filters_auto(s, self.filterset)

    def cache_count(self):
        return len(self.filtered_caches())

    def filter_name(self):
        return self.label

    def active_filterset(self):
        return self.filterset

    def set_sort(self, sort):
        pass

    def current_code(self):
        return self.current

    def selected_codes(self):
        return [self.current] if self.current else []

    def select_cache(self, code):
        self.selected.append(code)
        return True

    # databases
    def database_name(self):
        return active_database_name()

    def center_name(self):
        return ""

    def switch_database(self, name):
        manager = get_db_manager()
        manager.switch_to(next(d for d in manager.databases if d.name == name))
        self.clear_filter()

    def database_list_changed(self):
        pass

    def caches_removed(self):
        pass

    def caches_changed(self, codes, added):
        pass

    def set_corrected_coords(self, gc_code, lat, lon):
        return set_corrected_coords(gc_code, lat, lon)

    def end_macro(self):
        pass

    def refresh(self):
        pass

    def locations_changed(self):
        pass

    def filter_profiles_changed(self):
        pass

    # questions
    def confirm(self, message):
        return True

    def approve_macro_write(self, macro, change=None, editing=False):
        return WriteApproval.SESSION

    def approve_folder(self, target, folder, write, macro=None):
        self.asked_folders.append((Path(folder), write))
        if any(Path(folder).resolve().is_relative_to(f) for f in self.ctx.allow_folders):
            return FolderApproval.ONCE
        self.denied_folders.append((Path(folder), write))
        return FolderApproval.DENY

    def approve_host(self, host, macro=None, local=False, secure=True, declared=False):
        return FolderApproval.DENY       # no network in the tests

    # dialogs
    def message(self, text, title):
        self.messages.append(text)

    def input(self, prompt, default, title):
        return default

    def choose(self, prompt, options, default, title):
        return max(default, 0)

    def choose_file(self, title, file_filter, save, start_dir):
        return None

    def choose_folder(self, title, start_dir):
        return self.ctx.chosen

    def show_form(self, form, events):
        assert len(self.forms) < 20, "the macro keeps showing forms"
        key = (form.title, tuple(f.id for f in form.walk()))
        again = key in {k for k, _ in self.forms}
        controller = Scripted(form, events)
        self.forms.append((key, controller))
        events(controller, "open", None)
        if controller.closer == "open":
            if again:
                press_escape(controller)
            else:
                (self.user or press_enter)(controller)
        assert controller.closer != "open", (
            f"form {form.title!r} stayed open: {controller.messages}")
        if controller.closer is None:
            return None
        return {f.id: v for f, v in controller.values.items()}, controller.closer

    def progress(self, title, total):
        return Window()

    def open_file(self, path):
        self.opened.append(Path(path))
        return True


# ── One run ──────────────────────────────────────────────────────────────────


@dataclass
class Context:
    """What one example's run has: its folder (the examples installed in a
    macros folder), a folder the user picks, the host, and the folders the
    user allows when asked."""

    root: Path
    folder: Path
    chosen: Path
    host: ExampleHost = field(init=False)
    allow_folders: list[Path] = field(default_factory=list)

    def __post_init__(self):
        self.host = ExampleHost(self)

    def run(self, name: str) -> tuple[int, list[str]]:
        path = self.folder / name
        out: list[str] = []
        # cast: the host leaves out what the runtime treats as optional
        code = MacroRuntime(cast(MacroHost, self.host), output=out.append).run(
            path.read_text(encoding="utf-8"), chunk_name=name, base_dir=self.folder,
            macro_path=path)
        return code, out


@pytest.fixture(params=sorted(FLAGS))
def ctx(request, tmp_path, monkeypatch) -> Iterator[Context]:
    for name, value in FLAGS[request.param].items():
        monkeypatch.setattr(flags, name, value)
    # OpenSAK's temp folder and macros folder inside tmp_path
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))
    (tmp_path / "temp").mkdir()
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: tmp_path / "macros")
    shutil.copytree(EXAMPLES, tmp_path / "macros" / "examples")
    (tmp_path / "chosen").mkdir()
    companion.reset()
    db_access.reset_session()

    manager = get_db_manager()
    db = manager.new_database("Examples")
    manager.switch_to(db)
    seed_example_macro_caches()
    yield Context(tmp_path, tmp_path / "macros" / "examples", tmp_path / "chosen")
    companion.reset()
    db_access.reset_session()


# ── Per example: the user, the preparation, the change ───────────────────────


def _codes(db_name: Optional[str] = None) -> list[str]:
    manager = get_db_manager()
    info = manager.active if db_name is None else next(
        (d for d in manager.databases if d.name == db_name), None)
    assert info is not None, f"no database {db_name!r}"
    with sqlite3.connect(str(info.path)) as conn:
        return sorted(r[0] for r in conn.execute("SELECT gc_code FROM caches"))


def _corrected() -> dict[str, tuple[Optional[float], Optional[float]]]:
    with get_session() as s:
        return {c.gc_code: (n.corrected_lat, n.corrected_lon)
                for c, n in s.query(Cache, UserNote).join(UserNote)
                if n.is_corrected}


def _companion_rows(query: str) -> list[tuple]:
    active = get_db_manager().active
    assert active is not None
    path = companion.companion_path(active.path)
    with sqlite3.connect(str(path)) as conn:
        return conn.execute(query).fetchall()


def _report(ctx: Context, name: str) -> str:
    """The HTML page a report example wrote to OpenSAK's temp folder and opened."""
    page = temp_dir() / name
    assert ctx.host.opened == [page]
    return page.read_text(encoding="utf-8")


@dataclass
class Scenario:
    user: Optional[Callable[[Any], None]] = None        # in its forms
    setup: Optional[Callable[[Context], None]] = None   # before it runs
    check: Optional[Callable[[Context, list[str]], None]] = None


def _coords_form(c) -> None:
    c.change("coords", "N46 57.300 E007 26.700")
    c.click("ok")


def _check_coords_form(ctx, out):
    assert out == ["GCEX003: corrected coordinates set to N46 57.300 E007 26.700"]
    lat, lon = _corrected()["GCEX003"]
    assert (lat, lon) == (pytest.approx(46 + 57.3 / 60), pytest.approx(7 + 26.7 / 60))


def _csv_for_the_test_database(ctx):
    """The example's own CSV, for caches of the test database: GC1 → GCEX001."""
    csv = ctx.folder / "corrected_coords.csv"
    text = csv.read_text(encoding="utf-8")
    csv.write_text(re.sub(r"(?m)^GC(\d);", r"GCEX00\1;", text), encoding="utf-8")


def _check_csv(ctx, out):
    assert out[-1] == "Done: 6 set, 0 cleared, 0 skipped, 0 failed"
    corrected = _corrected()
    assert sorted(corrected) == [f"GCEX00{i}" for i in range(1, 7)]
    assert corrected["GCEX003"] == (pytest.approx(47.514093), pytest.approx(7.470118))
    assert ctx.host.label == "Corrected via CSV"


def _check_dnf_stats(ctx, out):
    assert out[-1] == "Stored in companion.dnf_stats of database Examples."
    rows = _companion_rows("SELECT gc_code, dnfs, dnfs_since_find FROM dnf_stats ORDER BY gc_code")
    assert rows == [("GCEX003", 3, 3), ("GCEX008", 1, 1)]


def _run_dnf_stats(ctx):
    ctx.run("dnf_stats.lua")


def _check_dnf_alert(ctx, out):
    assert out[-1] == "1 cache filtered."
    assert ctx.host.applied == [("Macro", 1)]
    with get_session() as s:
        assert [c.gc_code for c in apply_filters_auto(s, ctx.host.filterset)] == ["GCEX003"]


def _check_export_filters(ctx, out):
    assert out[-1] == f"Done: 2 exported, 1 skipped (folder: {temp_dir()})"
    easy = (temp_dir() / "Easy traditionals.gpx").read_text(encoding="utf-8")
    assert all(code in easy for code in ("GCEX001", "GCEX004", "GCEX008"))
    multi = (temp_dir() / "Multi-caches.gpx").read_text(encoding="utf-8")
    assert "GCEX002" in multi and "GCEX001" not in multi


def _export_form(c) -> None:
    c.events(c, "browse", c.field("folder"))       # the "…" button
    c.click("export")


def _check_export_form(ctx, out):
    files = list(ctx.chosen.iterdir())
    assert len(files) == 1 and files[0].suffix == ".gpx"
    assert ctx.host.messages == [f"8 caches written to\n{files[0].resolve()}"]
    assert all(f"GCEX00{i}" in files[0].read_text(encoding="utf-8") for i in range(1, 9))


def _check_find_with_form(ctx, out):
    # Traditional, difficulty <= 2.5, not found: GCEX004 and GCEX008
    assert ctx.host.applied == [("Find caches", 2)]
    assert ctx.host.selected == ["GCEX004"]


def _distance_form(c) -> None:
    """Around GCEX001 (Bern), 100 km along the ellipsoid, shown in the list;
    the result form is closed with "Another point"."""
    if not any(f.id == "point" for f in c.form.walk()):
        press_enter(c)
        return
    c.change("point", "N46 56.880 E007 26.844")
    c.change("radius", 100)
    c.change("method", "Vincenty (WGS-84 ellipsoid)")
    c.change("show", True)
    c.click("run")


def _check_distance_bearing(ctx, out):
    # Around, Another point on the result, then Close the point form
    assert [c.closer for _, c in ctx.host.forms] == ["run", "again", None]
    [result] = [c for _, c in ctx.host.forms if c.closer == "again"]
    page = next(f for f in result.form.walk() if f.type == "html").get("html")
    assert "5 caches" in page
    found = ["GCEX001", "GCEX006", "GCEX003", "GCEX008", "GCEX002"]   # nearest first
    assert [page.index(code) for code in found] == sorted(page.index(code) for code in found)
    assert "GCEX004" not in page and "GCEX007" not in page
    assert page.count("<td>100.000</td>") == 8      # the edge, measured back
    assert ctx.host.applied[-1][1] == 5


def _gsak_folder(ctx):
    """A minimal GSAK data folder in %APPDATA%\\gsak, which the user allows
    the macro to read when it asks."""
    import os
    gsak = Path(os.environ["APPDATA"]) / "gsak"
    helpers = _load("test_gsak_importer.py")
    for name, codes in (("AllCH", ("GC1A", "GC1B")), ("Holidays", ("GC2A",))):
        db = gsak / "data" / name / "sqlite.db3"
        db.parent.mkdir(parents=True)
        helpers._make_gsak_db(db, caches=[{"Code": c} for c in codes],
                              memos=[{"Code": c, "Url": f"https://coord.info/{c}"} for c in codes])
    with sqlite3.connect(str(gsak / "gsak.db3")) as conn:
        conn.execute("CREATE TABLE Settings (Type TEXT, Description TEXT, Data TEXT)")
        conn.execute("INSERT INTO Settings VALUES ('LO', 'Locations', ?)",
                     ("Work,N47 22.000 E008 32.000",))
    ctx.allow_folders.append(gsak.resolve())


def _check_gsak(ctx, out):
    assert out[-1] == "Total: 3 new and 0 updated caches, 0 errors"
    assert _codes("AllCH") == ["GC1A", "GC1B"]
    assert _codes("Holidays") == ["GC2A"]
    assert ctx.host.asked_folders                # it asked for the GSAK folder


def _first_run_then_import(ctx):
    _, out = ctx.run("new_since_last_run.lua")
    assert out[0].startswith("First run")
    with get_session() as s:
        import_gpx(EXAMPLES_GPX, s)        # the weekly pocket query


def _check_new_since(ctx, out):
    # The unfound caches: GCEX003, 004, 005, 007, 008
    assert re.fullmatch(r"5 unfound caches updated since \S+ UTC", out[0])
    assert ctx.host.applied == [(ctx.host.label, 5)]
    assert ctx.host.label.startswith("New since ")


def _puzzle(c) -> None:
    for button in ("digit", "word", "rot13", "numbers", "hint", "listing", "show"):
        c.click(button)
    c.click("save")
    c.click("close")


def _check_puzzle(ctx, out):
    [line] = out
    assert re.fullmatch(r"GCEX003: waypoint FN \(Final Location\) saved at N46 57\.123 +E007 26\.456",
                        line)
    with get_session() as s:
        wp = s.query(Waypoint).join(Cache).filter(Cache.gc_code == "GCEX003").one()
        assert (wp.prefix, wp.wp_type, wp.created_by_user) == ("FN", "Final Location", True)
        assert (wp.latitude, wp.longitude) == (pytest.approx(46 + 57.123 / 60),
                                               pytest.approx(7 + 26.456 / 60))


def _check_refile(ctx, out):
    assert out[-1] == "Done: 8 of 8 caches moved."
    assert _codes() == []
    assert _codes("Switzerland") == ["GCEX001", "GCEX002", "GCEX003", "GCEX006", "GCEX008"]
    assert _codes("Germany") == ["GCEX004", "GCEX005", "GCEX007"]


def _check_sql_report(ctx, out):
    [line] = out
    page = Path(line.removeprefix("Report: "))
    assert page.parent == temp_dir()
    text = page.read_text(encoding="utf-8")
    assert "Switzerland" in text and "Germany" in text
    # Run, then Edit query on the result, then Close the query form
    assert [c.closer for _, c in ctx.host.forms] == ["run", "edit", None]


def _check_find_statistics(ctx, out):
    page = _report(ctx, "find_statistics.html")
    # GCEX001, 002 and 006; the first and the last are milestones
    assert "Bear Pit" in page and "Autumn Meeting" in page


def _check_database_statistics(ctx, out):
    assert "Switzerland" in _report(ctx, "database_statistics.html")


def _check_region_coverage(ctx, out):
    # The tests have no boundary data; with it: test_macro_charts.py
    assert out == ["The boundary data is not installed (see Update location)."]
    assert ctx.host.opened == []


def _check_owned_statistics(ctx, out):
    page = _report(ctx, "owned_statistics.html")
    assert "Beer Garden" in page and "Car Museum" in page


SCENARIOS: dict[str, Scenario] = {
    "corrected_coords_form.lua": Scenario(user=_coords_form, check=_check_coords_form),
    "corrected_coords_from_csv.lua": Scenario(setup=_csv_for_the_test_database,
                                              check=_check_csv),
    "database_statistics.lua": Scenario(check=_check_database_statistics),
    "distance_bearing.lua": Scenario(user=_distance_form, check=_check_distance_bearing),
    "dnf_alert.lua": Scenario(setup=_run_dnf_stats, check=_check_dnf_alert),
    "dnf_stats.lua": Scenario(check=_check_dnf_stats),
    "export_filters_to_gpx.lua": Scenario(check=_check_export_filters),
    "export_with_form.lua": Scenario(user=_export_form, check=_check_export_form),
    "find_statistics.lua": Scenario(check=_check_find_statistics),
    "find_with_form.lua": Scenario(check=_check_find_with_form),
    "import_gsak_appdata.lua": Scenario(setup=_gsak_folder, check=_check_gsak),
    "new_since_last_run.lua": Scenario(setup=_first_run_then_import, check=_check_new_since),
    "owned_statistics.lua": Scenario(check=_check_owned_statistics),
    "puzzle_helper.lua": Scenario(user=_puzzle, check=_check_puzzle),
    "refile_by_country.lua": Scenario(check=_check_refile),
    "region_coverage.lua": Scenario(check=_check_region_coverage),
    "sql_report.lua": Scenario(check=_check_sql_report),
}


# ── The tests ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", NAMES)
def test_example_macro_runs(name, ctx):
    scenario = SCENARIOS.get(name, Scenario())
    if scenario.setup:
        scenario.setup(ctx)
    ctx.host.user = scenario.user
    code, out = ctx.run(name)
    assert code == 0, out
    assert not [line for line in out if "deprecated" in line.lower()]
    # No folder it needed was refused: what it uses is OpenSAK's temp and
    # macros folder, a folder the user picks, or one the scenario allows
    assert ctx.host.denied_folders == []
    if scenario.check:
        scenario.check(ctx, out)


def test_scenarios_belong_to_existing_examples():
    assert set(SCENARIOS) <= set(NAMES)


def test_examples_are_found():
    assert len(NAMES) >= 16
