"""tests/unit-tests/test_macro_import_gsak_937.py — the unattended GSAK
import (#937): gsak_import_service.run_gsak_import() and its Lua wrapper
opensak.import_gsak(). Sources: a backup .zip, a GSAK folder, a single
.db3 file and gsak.db3; existing targets skipped / merged / overwritten;
the user locations and saved filters of gsak.db3; no question asked."""

import importlib.util
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest

from opensak.db.manager import get_db_manager
from opensak.gui.settings import get_settings
from opensak.importer import gsak_import_service as service
from opensak.importer.gsak_import_service import GsakImportError, GsakImportOptions
from opensak.importer.gsak_importer import list_gsak_folder
from opensak.macro import FolderPermission, MacroError, MacroRuntime, WriteApproval

_spec = importlib.util.spec_from_file_location(
    "_gsak_importer_tests", Path(__file__).with_name("test_gsak_importer.py")
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)


@pytest.fixture(autouse=True)
def _temp_in_tmp_path(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.fixture
def manager():
    mgr = get_db_manager()
    mgr.ensure_active_initialised()
    return mgr


def _gsak_db(path: Path, *codes: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    codes = codes or ("GC1TEST",)
    return _helpers._make_gsak_db(
        path, caches=[{"Code": c} for c in codes],
        memos=[{"Code": c, "Url": f"https://coord.info/{c}"} for c in codes],
    )


def _settings_db(path: Path, locations: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Settings (Type TEXT, Description TEXT, Data TEXT)")
    if locations:
        conn.execute("INSERT INTO Settings VALUES ('LO', 'Locations', ?)", (locations,))
    conn.commit()
    conn.close()
    return path


LOCATIONS = "Work,N47 22.000 E008 32.000\nCabin,N46 30.000 E009 50.000\nbroken line"


def _backup(tmp_path: Path, dbs: dict[str, tuple[str, ...]], settings: bool = True) -> Path:
    archive = tmp_path / "GSAKBackup.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, codes in dbs.items():
            zf.write(_gsak_db(tmp_path / "src" / name / "sqlite.db3", *codes),
                     f"{name}/sqlite.db3")
        if settings:
            zf.write(_settings_db(tmp_path / "src" / "gsak.db3", LOCATIONS), "gsak.db3")
    return archive


def _gsak_install(tmp_path: Path) -> Path:
    """%AppData%\\gsak: gsak.db3 + data/<name>/sqlite.db3."""
    root = tmp_path / "gsak"
    _gsak_db(root / "data" / "AllCH" / "sqlite.db3", "GC1A", "GC1B")
    _gsak_db(root / "data" / "Holidays" / "sqlite.db3", "GC2A")
    _settings_db(root / "gsak.db3", LOCATIONS)
    return root


def _codes(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT gc_code FROM caches"))
    finally:
        conn.close()


def _db(name: str):
    return next(d for d in get_db_manager().databases if d.name == name)


def _location_names() -> set[str]:
    return {p.name for p in get_settings().home_points}


# ── Sources ──────────────────────────────────────────────────────────────────

class TestSources:
    def test_install_folder(self, tmp_path):
        root = _gsak_install(tmp_path)
        contents = list_gsak_folder(root)
        assert [d.name for d in contents.databases] == ["AllCH", "Holidays"]
        assert contents.settings_path == root / "gsak.db3"
        assert not contents.is_zip

    def test_data_folder_finds_the_install_settings(self, tmp_path):
        root = _gsak_install(tmp_path)
        contents = list_gsak_folder(root / "data")
        assert [d.name for d in contents.databases] == ["AllCH", "Holidays"]
        assert contents.settings_path == root / "gsak.db3"

    def test_folder_of_one_database(self, tmp_path):
        root = _gsak_install(tmp_path)
        contents = list_gsak_folder(root / "data" / "AllCH")
        assert [d.name for d in contents.databases] == ["AllCH"]
        assert contents.settings_path == root / "gsak.db3"

    def test_kinds(self, tmp_path):
        root = _gsak_install(tmp_path)
        assert service.list_gsak_source(root)[0] == service.SOURCE_FOLDER
        kind, contents = service.list_gsak_source(root / "data" / "AllCH" / "sqlite.db3")
        # A single file is just that database, not the install's settings.
        assert kind == service.SOURCE_FILE and contents.settings_path is None
        assert service.list_gsak_source(root / "gsak.db3")[0] == service.SOURCE_SETTINGS
        assert service.list_gsak_source(_backup(tmp_path, {"A": ("GC1",)}))[0] == service.SOURCE_BACKUP

    def test_missing_or_empty_source(self, tmp_path):
        with pytest.raises(GsakImportError, match="does not exist"):
            service.list_gsak_source(tmp_path / "nope.zip")
        (tmp_path / "empty").mkdir()
        with pytest.raises(GsakImportError, match="no GSAK database"):
            service.list_gsak_source(tmp_path / "empty")


# ── run_gsak_import ──────────────────────────────────────────────────────────

class TestRun:
    def test_backup_creates_one_database_each(self, manager, tmp_path):
        report = service.run_gsak_import(
            _backup(tmp_path, {"A": ("GC1A",), "B": ("GC2B", "GC2C")}))
        assert report.ok and report.kind == service.SOURCE_BACKUP
        assert [(d.gsak_name, d.target, d.action) for d in report.databases] == [
            ("A", "A", "created"), ("B", "B", "created")]
        assert report.new_databases == ["A", "B"]
        assert _codes(_db("B").path) == ["GC2B", "GC2C"]
        assert report.total("created") == 3
        # Filters and locations are opt-in (#1090).
        assert report.locations is None and report.filters is None
        # Unpacked copies are gone.
        assert not list(tmp_path.glob("gsak_import_*"))

    def test_selected_and_excluded_databases(self, manager, tmp_path):
        archive = _backup(tmp_path, {"A": ("GC1A",), "B": ("GC2B",), "C": ("GC3C",)})
        report = service.run_gsak_import(
            archive, GsakImportOptions(databases=["a", "C"], exclude=["c"]))
        assert [d.gsak_name for d in report.databases] == ["A"]

    def test_unknown_database_changes_nothing(self, manager, tmp_path):
        before = [d.name for d in manager.databases]
        with pytest.raises(GsakImportError, match="'nope'.*found: A"):
            service.run_gsak_import(_backup(tmp_path, {"A": ("GC1A",)}),
                                    GsakImportOptions(databases=["Nope"]))
        assert [d.name for d in manager.databases] == before

    def test_existing_target_is_skipped_by_default(self, manager, tmp_path):
        manager.new_database("A")
        report = service.run_gsak_import(_backup(tmp_path, {"A": ("GC1A",)}))
        assert report.databases[0].action == "skipped"
        assert _codes(_db("A").path) == []

    @pytest.mark.parametrize("mode, expected", [
        ("merge", ["GC1A", "GC9OLD"]), ("overwrite", ["GC1A"]),
    ])
    def test_existing_target_merged_or_overwritten(self, manager, tmp_path, mode, expected):
        service.run_gsak_import(_gsak_db(tmp_path / "old" / "A" / "sqlite.db3", "GC9OLD"))
        report = service.run_gsak_import(_backup(tmp_path, {"A": ("GC1A",)}),
                                         GsakImportOptions(if_exists=mode))
        assert report.databases[0].action == {"merge": "merged", "overwrite": "overwritten"}[mode]
        assert _codes(_db("A").path) == expected
        assert report.databases[0].cleared == (1 if mode == "overwrite" else 0)

    def test_overwrite_empties_a_shared_target_only_once(self, manager, tmp_path):
        manager.new_database("All")
        report = service.run_gsak_import(
            _backup(tmp_path, {"A": ("GC1A",), "B": ("GC2B",)}),
            GsakImportOptions(target="All", if_exists="overwrite"))
        assert [d.action for d in report.databases] == ["overwritten", "merged"]
        assert _codes(_db("All").path) == ["GC1A", "GC2B"]

    def test_target_mapping_and_create_false(self, manager, tmp_path):
        manager.new_database("Switzerland")
        report = service.run_gsak_import(
            _backup(tmp_path, {"A": ("GC1A",), "B": ("GC2B",)}),
            GsakImportOptions(targets={"a": "Switzerland"}, if_exists="merge", create=False))
        a, b = report.databases
        assert (a.target, a.action) == ("Switzerland", "merged")
        assert b.action == "failed" and "no OpenSAK database" in b.reason
        assert not report.ok
        assert "B" not in [d.name for d in manager.databases]

    def test_settings_from_a_backup(self, manager, tmp_path):
        get_settings().home_points  # initialise
        report = service.run_gsak_import(
            _backup(tmp_path, {"A": ("GC1A",)}),
            GsakImportOptions(locations="skip", filters="skip"))
        assert sorted(report.locations.added) == ["Cabin", "Work"]
        assert report.locations_invalid == 1
        assert {"Cabin", "Work"} <= _location_names()
        assert report.filters is not None and report.filters.written == 0

    def test_existing_location_skipped_or_replaced(self, manager, tmp_path):
        archive = _backup(tmp_path, {"A": ("GC1A",)})
        service.run_gsak_import(archive, GsakImportOptions(locations="skip"))
        again = service.run_gsak_import(archive, GsakImportOptions(locations="skip"))
        assert sorted(again.locations.skipped) == ["Cabin", "Work"]
        replaced = service.run_gsak_import(archive, GsakImportOptions(locations="overwrite"))
        assert sorted(replaced.locations.updated) == ["Cabin", "Work"]

    def test_install_folder_with_locations(self, manager, tmp_path):
        report = service.run_gsak_import(_gsak_install(tmp_path),
                                         GsakImportOptions(locations="skip"))
        assert [d.action for d in report.databases] == ["created", "created"]
        assert _codes(_db("AllCH").path) == ["GC1A", "GC1B"]
        assert sorted(report.locations.added) == ["Cabin", "Work"]

    def test_single_file_ignores_settings(self, manager, tmp_path):
        root = _gsak_install(tmp_path)
        report = service.run_gsak_import(root / "data" / "Holidays" / "sqlite.db3",
                                         GsakImportOptions(locations="skip"))
        assert [d.target for d in report.databases] == ["Holidays"]
        assert report.locations is None and "only imported from" in report.notes[0]

    def test_gsak_db3_alone_imports_its_settings(self, manager, tmp_path):
        report = service.run_gsak_import(_settings_db(tmp_path / "gsak.db3", LOCATIONS))
        assert report.databases == []
        assert sorted(report.locations.added) == ["Cabin", "Work"]

    def test_refused_file_fails_only_that_database(self, manager, tmp_path):
        root = _gsak_install(tmp_path)

        def check(path):
            if path.parent.name == "Holidays":
                raise PermissionError("not allowed")

        report = service.run_gsak_import(root, check_file=check)
        assert [(d.gsak_name, d.action) for d in report.databases] == [
            ("AllCH", "created"), ("Holidays", "failed")]
        # Nothing created for the refused one.
        assert "Holidays" not in [d.name for d in manager.databases]

    def test_cancel_stops_the_rest(self, manager, tmp_path):
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) > 3

        report = service.run_gsak_import(
            _backup(tmp_path, {"A": ("GC1A",), "B": ("GC2B",)}), cancel_cb=cancel)
        assert report.cancelled and not report.ok
        assert report.databases[-1].action in ("cancelled", "skipped")

    def test_cancel_before_the_settings(self, manager, tmp_path):
        archive = _settings_db(tmp_path / "gsak.db3", LOCATIONS)
        report = service.run_gsak_import(archive, cancel_cb=lambda: True)
        assert report.cancelled and report.locations is None

    def test_summary_is_logged(self, manager, tmp_path, caplog):
        caplog.set_level("INFO", logger="opensak.importer.gsak_import_service")
        service.run_gsak_import(_backup(tmp_path, {"A": ("GC1A",)}))
        assert "A → A: created" in caplog.text
        assert "1 new, 0 updated" in caplog.text


# ── opensak.import_gsak() ────────────────────────────────────────────────────

class Host:
    def __init__(self, answer=WriteApproval.SESSION, confirm=True):
        self.answer = answer
        self.confirm_answer = confirm
        self.questions: list[str] = []
        self.events: list[str] = []
        self.folders_asked: list[Path] = []

    def database_name(self):
        return "Default"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def approve_macro_write(self, macro, change=None, editing=False):
        self.events.append("write?")
        return self.answer

    def approve_folder(self, target, folder, write, macro=None):
        from opensak.macro.runtime import FolderApproval
        self.folders_asked.append(folder)
        return FolderApproval.ONCE

    def confirm(self, message):
        self.questions.append(message)
        return self.confirm_answer

    def database_list_changed(self):
        self.events.append("list")

    def caches_changed(self, codes, added):
        self.events.append(f"changed:{added}")

    def caches_removed(self):
        self.events.append("removed")

    def locations_changed(self):
        self.events.append("locations")

    def end_macro(self):
        pass


def _run(source, folder, host=None, permitted=True):
    out: list[str] = []
    host = host or Host()
    permissions = [FolderPermission(str(folder), read=True, write=False)] if permitted else []
    MacroRuntime(host, output=out.append, folder_permissions=permissions).run(
        source, chunk_name="m.lua", base_dir=folder)
    return host, out


def test_lua_backup_report(manager, tmp_path):
    _backup(tmp_path, {"A": ("GC1A",), "B": ("GC2B", "GC2C")})
    host, out = _run("""
        local r = opensak.import_gsak("GSAKBackup.zip", {
            databases = {"B"}, locations = true, quiet = true })
        print(r.ok, r.kind, r.created, r.updated, #r.databases, r.new_databases[1])
        local db = r.databases[1]
        print(db.gsak, db.target, db.action, db.created, db.logs, #db.errors)
        print(r.locations.added, r.locations.invalid, r.filters)
    """, tmp_path)
    assert out == [
        "true\tbackup\t2\t0\t1\tB",
        "B\tB\tcreated\t2\t0\t0",
        "2\t1\tnil",
    ]
    assert host.events == ["write?", "list", "locations"]
    assert _codes(_db("B").path) == ["GC2B", "GC2C"]


def test_lua_prints_the_summary(manager, tmp_path):
    _backup(tmp_path, {"A": ("GC1A",)}, settings=False)
    _, out = _run('opensak.import_gsak("GSAKBackup.zip")', tmp_path)
    assert out[0].startswith("GSAK import from") and "A → A: created" in out[1]


def test_lua_import_into_the_active_database_refreshes_it(manager, tmp_path):
    active = manager.active
    root = tmp_path / "g"
    _gsak_db(root / "x" / "sqlite.db3", "GC7A")
    host, out = _run(f"""
        local r = opensak.import_gsak("g/x/sqlite.db3",
            {{ target = "{active.name}", if_exists = "merge", quiet = true }})
        print(r.databases[1].action, r.databases[1].target)
    """, tmp_path)
    assert out == [f"merged\t{active.name}"]
    assert "changed:True" in host.events
    assert "GC7A" in _codes(active.path)


def test_lua_folder_is_approved_once(manager, tmp_path):
    root = _gsak_install(tmp_path)
    host, out = _run('local r = opensak.import_gsak("gsak", { quiet = true }) print(#r.databases)',
                     tmp_path, permitted=False)
    assert out == ["2"]
    assert host.folders_asked == [root]


@pytest.mark.parametrize("options, message", [
    ('{ if_exists = "replace" }', "if_exists must be one of skip, merge, overwrite"),
    ('{ bogus = 1 }', "unknown option"),
    ('{ databases = 3 }', "databases must be a name or a list of names"),
    ('{ target = 3 }', "target must be a database name"),
    ('{ filters = "yes" }', "filters must be true, false"),
    ('{ quiet = 1 }', "quiet must be true or false"),
    ('{ databases = {"Nope"} }', "no GSAK database named 'nope'"),
])
def test_lua_bad_options(manager, tmp_path, options, message):
    _backup(tmp_path, {"A": ("GC1A",)})
    with pytest.raises(MacroError, match=message):
        _run(f'opensak.import_gsak("GSAKBackup.zip", {options})', tmp_path)


def test_lua_missing_source(manager, tmp_path):
    with pytest.raises(MacroError, match="does not exist"):
        _run('opensak.import_gsak("nope.zip")', tmp_path)


def test_lua_write_denied(manager, tmp_path):
    _backup(tmp_path, {"A": ("GC1A",)})
    with pytest.raises(MacroError, match="did not allow"):
        _run('opensak.import_gsak("GSAKBackup.zip")', tmp_path, host=Host(WriteApproval.DENY))
    assert "A" not in [d.name for d in manager.databases]


def test_lua_not_available_without_write_flag(manager, tmp_path, monkeypatch):
    from opensak.utils import flags
    monkeypatch.setattr(flags, "lua_macro_write", False)
    _, out = _run("print(opensak.import_gsak)", tmp_path)
    assert out == ["nil"]


def test_lua_refused_inside_a_transaction(manager, tmp_path):
    _backup(tmp_path, {"A": ("GC1A",)})
    with pytest.raises(MacroError, match="transaction"):
        _run('opensak.transaction(function() opensak.import_gsak("GSAKBackup.zip") end)',
             tmp_path)


# ── The shipped example ──────────────────────────────────────────────────────

EXAMPLE = Path(__file__).parents[2] / "macros" / "examples" / "import_gsak_appdata.lua"


def _example(**settings) -> str:
    """The example's source, with some of its settings changed."""
    source = EXAMPLE.read_text(encoding="utf-8")
    for name, value in settings.items():
        source, n = re.subn(rf"^local {name} = [^\n-]*", f"local {name} = {value} ",
                            source, count=1, flags=re.M)
        assert n == 1, name
    return source


def test_example_imports_gsak_from_appdata(manager, tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    _gsak_install(appdata)            # appdata/gsak/{gsak.db3, data/...}
    monkeypatch.setenv("APPDATA", str(appdata))
    source = _example(IF_EXISTS='"merge"')
    host, out = _run(source, tmp_path, permitted=False)
    assert len(host.questions) == 1 and "are updated" in host.questions[0]
    assert host.folders_asked == [appdata / "gsak"]
    assert out[0] == f"GSAK import from {appdata / 'gsak'}"
    assert "AllCH" in out[1] and "created (2 new, 0 updated)" in out[1]
    assert "  Locations: 2 added, 0 updated, 0 kept, 1 invalid" in out
    assert out[-1] == "Total: 3 new and 0 updated caches, 0 errors"
    assert _codes(_db("Holidays").path) == ["GC2A"]

    # A second run merges into the databases it created.
    _, out = _run(source, tmp_path, permitted=False)
    assert "merged (0 new, 2 updated)" in out[1]
    assert "  Locations: 0 added, 0 updated, 2 kept, 1 invalid" in out


def test_example_skip_leaves_existing_databases(manager, tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    _gsak_install(appdata)
    monkeypatch.setenv("APPDATA", str(appdata))
    source = _example(IF_EXISTS='"skip"')
    _run(source, tmp_path, permitted=False)
    host, out = _run(source, tmp_path, permitted=False)
    assert "left untouched" in host.questions[0]
    assert "skipped" in out[1] and "already" in out[1]


def test_example_without_gsak_exits_1(manager, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "nothing"))
    out: list[str] = []
    code = MacroRuntime(Host(), output=out.append, folder_permissions=[]).run(
        EXAMPLE.read_text(encoding="utf-8"), chunk_name="m.lua", base_dir=tmp_path)
    assert code == 1
    assert out[0].startswith("Nothing imported:") and "does not exist" in out[0]


def test_example_asks_first_and_no_imports_nothing(manager, tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    _gsak_install(appdata)
    monkeypatch.setenv("APPDATA", str(appdata))
    host, out = _run(EXAMPLE.read_text(encoding="utf-8"), tmp_path,
                     host=Host(confirm=False), permitted=False)
    assert out == ["Cancelled — nothing imported."]
    # Asked before anything else: no write or folder approval, nothing created.
    assert host.events == [] and host.folders_asked == []
    assert "AllCH" not in [d.name for d in manager.databases]


def test_example_without_question(manager, tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    _gsak_install(appdata)
    monkeypatch.setenv("APPDATA", str(appdata))
    source = _example(ASK="false")
    host, out = _run(source, tmp_path, permitted=False)
    assert host.questions == []
    assert out[-1] == "Total: 3 new and 0 updated caches, 0 errors"


def test_example_filters_and_locations_separately(manager, tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    _gsak_install(appdata)
    monkeypatch.setenv("APPDATA", str(appdata))
    host, out = _run(_example(FILTERS="false", LOCATIONS='"overwrite"'),
                     tmp_path, permitted=False)
    assert "Saved filters: not imported." in host.questions[0]
    assert "User locations: imported, existing ones with the same name are replaced." \
        in host.questions[0]
    assert not any(line.startswith("  Filters") for line in out)
    assert "  Locations: 2 added, 0 updated, 0 kept, 1 invalid" in out
