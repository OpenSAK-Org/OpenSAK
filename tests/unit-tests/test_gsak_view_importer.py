# tests/unit-tests/test_gsak_view_importer.py — saved GSAK views (gsak.db3,
# Settings Type 'GV') become OpenSAK Column Views; unknown columns are left out.

import json
import sqlite3
from pathlib import Path

import pytest

from opensak.gui.dialogs.column_dialog import ColumnView, _ALL_COLUMNS_DEF
from opensak.importer.gsak_filter_importer import GsakFilterSourceError
from opensak.importer.gsak_view_importer import (
    GSAK_COLUMN_MAP,
    convert_view,
    gsak_custom_fields,
    import_gsak_views,
    load_gsak_views,
    parse_view_blob,
)

# Shaped like a view saved by a current GSAK (German UI).
MY_VIEW = "\r\n".join([
    "Code=82~Code",
    "Name=323~Wegpunktname",
    "PlacedBy=174~Platziert von",
    "Distance=67~Km",
    "FavPerc=51~FavPerc",
    "ToSolve=67~ToSolve",
    "TbLocked=67~TbLocked",
    "wFormat",
    "rbtMinutes.Checked=True",
    "Other",
    "Grid3=yes",
    "Filter=None",
])

# Shaped like the "Default" view of an old GSAK: upper-case names, widths in
# characters, and a wDisplay section of bare T/F lines.
OLD_VIEW = "\r\n".join([
    "CODE=8~Code~F",
    "DESC=30~Waypoint Name~F",
    "TYPEICON=3~ ~F",
    "USERFOUND=10~Found by me~F",
    "wDisplay",
    "T",
    "F",
])


@pytest.fixture
def gsak_db(tmp_path) -> Path:
    path = tmp_path / "gsak.db3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Settings (Type collate nocase, Description, Data)")
    conn.execute("CREATE TABLE CustomGlobal (fname text primary key, ftype text)")
    conn.execute("CREATE TABLE CustomDefs (fname, ftype, fscope, fdb)")
    conn.executemany("INSERT INTO Settings VALUES (?, ?, ?)", [
        ("GV", "MyView", MY_VIEW),
        ("GV", "Default", OLD_VIEW),
        ("GV", "Coords", "Latitude=67~Lat\r\nLongitude=67~Lon\r\nwFormat"),
        ("FI", "A filter", "chkFound=True"),
    ])
    conn.execute("INSERT INTO CustomGlobal VALUES ('FavPerc', 'Integer')")
    conn.execute("INSERT INTO CustomDefs VALUES ('ToSolve', 'Boolean', 'Local', 'AllCH')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def no_active_custom_fields(monkeypatch):
    monkeypatch.setattr(
        "opensak.importer.gsak_filter_importer.active_custom_fields", lambda: None)


class TestParse:
    def test_columns_in_order_up_to_the_first_section(self):
        cols = parse_view_blob(MY_VIEW)
        assert [c.field for c in cols] == [
            "Code", "Name", "PlacedBy", "Distance", "FavPerc", "ToSolve", "TbLocked"]
        assert cols[1].width == 323 and cols[1].caption == "Wegpunktname"

    def test_old_view_widths_count_characters_and_are_dropped(self):
        cols = parse_view_blob(OLD_VIEW)
        assert [c.field for c in cols] == ["CODE", "DESC", "TYPEICON", "USERFOUND"]
        assert all(c.width is None for c in cols)

    def test_empty(self):
        assert parse_view_blob("") == []


class TestConvert:
    def test_builtin_custom_and_ignored(self):
        c = convert_view("MyView", MY_VIEW, {"favperc": "FavPerc", "tosolve": "ToSolve"})
        assert c.visible_columns == [
            "gc_code", "name", "placed_by", "distance", "custom:FavPerc", "custom:ToSolve"]
        assert c.ignored == ["TbLocked"]
        assert c.custom == ["FavPerc", "ToSolve"]
        assert c.widths == {"gc_code": 82, "name": 323, "placed_by": 174, "distance": 67,
                            "custom:FavPerc": 51, "custom:ToSolve": 67}

    def test_custom_field_unknown_to_gsak_is_ignored(self):
        c = convert_view("MyView", MY_VIEW, {})
        assert c.ignored == ["FavPerc", "ToSolve", "TbLocked"]

    def test_custom_field_gets_the_name_the_gsak_import_gives_it(self):
        # A name OpenSAK can't use as it is is renamed like on import.
        c = convert_view("V", "Code=80~C\r\nMy Field=60~X", {"my field": "My Field"})
        assert c.visible_columns == ["gc_code", "name", "custom:My_Field"]

    def test_old_view_maps_legacy_names(self):
        c = convert_view("Default", OLD_VIEW, {})
        assert c.visible_columns == ["gc_code", "name", "cache_type", "found_date"]
        assert c.widths == {}
        assert c.ignored == []

    def test_always_visible_columns_are_added_in_front(self):
        c = convert_view("V", "User2=67~U2", {})
        assert c.visible_columns == ["gc_code", "name", "user_data_2"]
        c = convert_view("V", "Latitude=60~La\r\nCode=82~C", {})
        assert c.visible_columns == ["latitude", "gc_code", "name"]

    def test_duplicates_are_dropped(self):
        # The first occurrence wins, width and all.
        c = convert_view("V", "Code=80~C\r\nDESC=30~N~F\r\nName=200~N", {})
        assert c.visible_columns == ["gc_code", "name"]
        assert c.widths == {"gc_code": 80}

    def test_map_targets_are_real_columns(self):
        known = {col[0] for col in _ALL_COLUMNS_DEF}
        assert set(GSAK_COLUMN_MAP.values()) <= known


class TestReadGsakDb:
    def test_views_only_sorted(self, gsak_db):
        assert [name for name, _ in load_gsak_views(gsak_db)] == [
            "Coords", "Default", "MyView"]

    def test_custom_fields_global_and_local(self, gsak_db):
        assert gsak_custom_fields(gsak_db) == {"favperc": "FavPerc", "tosolve": "ToSolve"}

    def test_not_a_settings_db(self, tmp_path):
        path = tmp_path / "sqlite.db3"
        sqlite3.connect(path).close()
        with pytest.raises(GsakFilterSourceError):
            load_gsak_views(path)


class TestImport:
    def test_writes_loadable_column_views(self, gsak_db, tmp_path):
        out = tmp_path / "views"
        result = import_gsak_views(gsak_db, out_dir=out)
        assert result.written == 3 and result.skipped == 0 and result.failed == 0

        views = {v.name: v for v in (ColumnView.load(p) for p in ColumnView.list_views(out))}
        assert set(views) == {"Coords", "Default", "MyView"}
        assert views["MyView"].visible_columns == [
            "gc_code", "name", "placed_by", "distance", "custom:FavPerc", "custom:ToSolve"]
        assert views["MyView"].widths["name"] == 323
        assert views["Coords"].visible_columns == ["gc_code", "name", "latitude", "longitude"]
        # The file is the one a save from the Choose Columns dialog would use.
        assert (out / "MyView.json").is_file()
        assert ColumnView.path_for("MyView", out) == out / "MyView.json"

    def test_active_database_custom_fields_count(self, gsak_db, tmp_path, monkeypatch):
        monkeypatch.setattr("opensak.importer.gsak_filter_importer.active_custom_fields",
                            lambda: {"TbLocked": "boolean"})
        result = import_gsak_views(gsak_db, out_dir=tmp_path, names=["MyView"])
        conversion = result.entries[0].conversion
        assert "custom:TbLocked" in conversion.visible_columns
        assert conversion.ignored == []

    def test_existing_view_is_kept_unless_overwrite(self, gsak_db, tmp_path):
        ColumnView("MyView", ["gc_code"]).save(tmp_path)
        result = import_gsak_views(gsak_db, out_dir=tmp_path, names=["MyView"])
        assert result.skipped == 1 and result.written == 0
        assert json.loads((tmp_path / "MyView.json").read_text())["visible_columns"] == ["gc_code"]

        result = import_gsak_views(gsak_db, out_dir=tmp_path, names=["MyView"], overwrite=True)
        assert result.written == 1
        assert ColumnView.load(tmp_path / "MyView.json").visible_columns[:2] == ["gc_code", "name"]

    def test_names_that_share_a_file_get_their_own(self, tmp_path):
        db = tmp_path / "gsak.db3"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE Settings (Type, Description, Data)")
        conn.executemany("INSERT INTO Settings VALUES ('GV', ?, 'Code=80~C')",
                         [("A/B",), ("A_B",)])
        conn.commit()
        conn.close()
        result = import_gsak_views(db, out_dir=tmp_path / "views")
        assert result.written == 2
        assert {p.name for p in (tmp_path / "views").iterdir()} == {"A_B.json", "A_B_2.json"}

    def test_progress(self, gsak_db, tmp_path):
        calls = []
        import_gsak_views(gsak_db, out_dir=tmp_path, progress_cb=lambda d, t: calls.append((d, t)))
        assert calls == [(1, 3), (2, 3), (3, 3)]
