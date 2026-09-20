# tests/unit-tests/test_gsak_filter_importer.py — GSAK saved-filter import:
# conversion, where-tab comments and migration coverage.

import json
import sqlite3
from pathlib import Path

import pytest

from opensak.filters.engine import (
    DATE_OPS,
    DATE_UNITS,
    LOG_CATEGORIES,
    LOG_SCOPE_CHOICES,
    TEXT_OPS,
    FilterSet,
    validate_where_sql,
)
from opensak.importer import gsak_filter_importer as gfi
from opensak.importer.gsak_filter_importer import (
    COMMENT,
    NATIVE,
    SQL,
    Conversion,
    GsakFilterSourceError,
    Options,
    convert,
    find_gsak_filter_db,
    import_gsak_filters,
    load_gsak_filters,
    parse_filter_blob,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _blob(pairs: dict, where: str = "", custom: list | None = None) -> str:
    """Serialise a GSAK filter blob the way TranslateFilters.Data stores one."""
    lines = [f"{k}={v}" for k, v in pairs.items()]
    if custom:
        lines.append("*custom*")
        lines += custom
    text = "\r\n".join(lines)
    if where:
        text += "\r\n*where*=" + where
    return text


def _convert(pairs: dict, where: str = "", custom: list | None = None,
             opts: Options | None = None) -> Conversion:
    return convert(parse_filter_blob("T", _blob(pairs, where, custom)),
                   opts or Options())


def _statuses(c: Conversion) -> dict:
    return {crit.label: crit.status for crit in c.criteria}


def _make_gsak_db(path: Path, filters: dict[str, str]) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE TranslateFilters (Type TEXT, Description TEXT, Data TEXT)")
    conn.executemany("INSERT INTO TranslateFilters VALUES ('FI', ?, ?)",
                     list(filters.items()))
    conn.commit()
    conn.close()
    return path


# ── The vocabularies have to stay in step with the filter engine ──────────────

class TestVocabularies:
    """GSAK's dialog lists are decoded straight into OpenSAK's own operator
    names. If the engine ever renames one, these catch it here rather than as
    a ValueError halfway through someone's import."""

    def test_every_text_operator_exists(self):
        assert set(gfi.TEXT_OP.values()) <= set(TEXT_OPS)

    def test_every_date_operator_exists(self):
        for table in (gfi.DATE_OP, gfi.DATE_OP_NO_COMPARE, gfi.DATE_OP_MY_FOUND):
            assert {op for op in table.values() if op} <= set(DATE_OPS)

    def test_rolling_window_units_are_the_engines(self):
        assert gfi.DURING_UNITS == DATE_UNITS

    def test_date_fields_exist_in_the_engine(self):
        from opensak.filters.engine import DATE_FILTER_FIELDS
        fields = {row[6] for row in gfi._DATE_FIELDS if row[6]}
        assert fields <= set(DATE_FILTER_FIELDS)

    def test_log_scope_and_categories_are_the_engines(self):
        assert LOG_SCOPE_CHOICES[0] == 0        # index 0 = "all logs"
        assert set(LOG_CATEGORIES) == {"found", "not_found", "other"}


# ── Blob parsing ──────────────────────────────────────────────────────────────

class TestParseBlob:
    def test_splits_keys_custom_and_where(self):
        gf = parse_filter_blob("F", _blob({"chkFound": "True", "edtDesc": "abc"},
                                          where="Found = 1",
                                          custom=["MyCol;bool;;0;1"]))
        assert gf.flag("chkFound") is True
        assert gf.text("edtDesc") == "abc"
        assert gf.custom == ["MyCol;bool;;0;1"]
        assert gf.where == "Found = 1"

    def test_missing_keys_are_none(self):
        gf = parse_filter_blob("F", _blob({}))
        assert gf.flag("nope") is None and gf.num("nope") is None
        assert gf.text("nope") == ""


# ── Native conversions ────────────────────────────────────────────────────────

class TestNativeConversions:
    def test_found_and_availability(self):
        c = _convert({"chkFound": "True", "chkNotFound": "False",
                      "chkAvailable": "True", "chkTempUnavailable": "False",
                      "chkArchivedOnly": "False"})
        assert _statuses(c) == {"Found by me": NATIVE, "Availability": NATIVE}
        assert [f.filter_type for f in c.filters] == ["found", "availability"]

    def test_text_field_keeps_gsak_operator(self):
        # GSAK index 3 is "Ungleich" — OpenSAK's text rows have that operator,
        # so nothing has to fall back to SQL.
        c = _convert({"cbxOwnerName": "3", "edtOwnerName": "Vyrembi"})
        assert _statuses(c) == {"Owner": NATIVE}
        assert (c.filters[0].text, c.filters[0].op) == ("Vyrembi", "not_equals")

    def test_regex_text_field_stays_native(self):
        c = _convert({"cbxDesc": "7", "edtDesc": "^AL[0-9]+"})
        assert c.filters[0].op == "regex"
        assert c.coverage == 1.0

    def test_cache_types_partially_unmappable(self):
        types = {label: "False" for label in gfi.GSAK_TYPE_LABEL_TO_OSAK}
        types["Traditional"] = "True"
        types["Waymark"] = "True"      # no OpenSAK equivalent
        c = _convert(types)
        statuses = _statuses(c)
        assert statuses["Cache types"] == NATIVE
        assert c.filters[0].types == ["Traditional Cache"]
        assert any(s == COMMENT for s in statuses.values())
        assert c.coverage == 0.5

    def test_all_types_ticked_is_no_criterion(self):
        c = _convert({label: "True" for label in gfi.GSAK_TYPE_LABEL_TO_OSAK})
        assert c.criteria == []

    def test_dates_become_native_date_filters(self):
        # 44927 = 2023-01-01 as a Delphi TDateTime serial.
        c = _convert({"cbxPlaced": "1", "edtDateP": "44927", "edtDateP2": "44927"})
        assert _statuses(c) == {"Hidden date": NATIVE}
        f = c.filters[0]
        assert (f.field, f.op, f.date1.isoformat()) == \
               ("hidden_date", "on_or_after", "2023-01-01")

    def test_rolling_window_date(self):
        c = _convert({"cbxChange": "4", "edtChangeDuring": "30", "cbxChangeDuring": "0"})
        f = c.filters[0]
        assert (f.op, f.amount, f.unit) == ("during", 30, "days")

    def test_rolling_window_without_size_is_commented(self):
        c = _convert({"cbxChange": "4", "edtChangeDuring": "", "cbxChangeDuring": "0"})
        assert _statuses(c) == {"Last changed date": COMMENT}

    def test_polygon_becomes_line_polygon_filter(self):
        c = _convert({"rbtPoly": "True",
                      "ArcFilter": "~47.0,8.0~48.0,8.0~48.0,9.0~47.0,8.0"})
        f = c.filters[0]
        assert f.filter_type == "line_polygon" and f.mode == "polygon"
        # GSAK repeats the first vertex to close the ring; OpenSAK closes it itself.
        assert f.points == [(47.0, 8.0), (48.0, 8.0), (48.0, 9.0)]
        assert c.coverage == 1.0

    def test_degenerate_polygon_is_commented_not_crashing(self):
        c = _convert({"rbtPoly": "True", "ArcFilter": "~47.0,8.0"})
        assert list(_statuses(c).values()) == [COMMENT]

    def test_log_tab_becomes_log_filter(self):
        # GSAK's own "last 2 logs are DNF" standard filter.
        c = _convert({
            "chkLogFound": "False", "chkLogNotFound": "True",
            "chkLogSearchFound": "True", "chkLogSearchNotFound": "True",
            "chkLogSearchNote": "False",
            "cbxLogsToSearch": "2", "cbxLogCount": "2", "edtLogFrom": "2",
            "cbxLogDate": "6", "rbtId": "True", "edtGeoName": "",
            "LtDidn't find it": "True", "LtNeeds Archived": "True",
        })
        assert list(_statuses(c).values()) == [NATIVE]
        f = c.filters[0]
        assert f.filter_type == "log"
        assert f.types == ["Didn't find it", "Needs Archived"]
        assert f.categories == ["found", "not_found"]
        assert (f.last_n, f.count_op, f.count1) == (2, "at_least", 2)

    def test_waypoint_tab_becomes_waypoint_filter(self):
        c = _convert({"cbxcCount": "0", "cbxCtype2": "1", "cbxctype": "0",
                      "edtctype": "", "cbxcDate": "6"})
        f = c.filters[0]
        assert f.filter_type == "waypoint"
        assert f.texts["wp_type"].text == "Parking Area"

    def test_child_waypoint_yes_no_folds_into_waypoint_filter(self):
        c = _convert({"chkChildYes": "False", "chkChildNo": "True"})
        f = c.filters[0]
        assert (f.filter_type, f.count_op, f.count1) == ("waypoint", "equal", 0)


# ── SQL fallbacks ─────────────────────────────────────────────────────────────

class TestSqlFallbacks:
    def test_watch_list_has_no_gui_filter(self):
        c = _convert({"chkWatchYes": "True", "chkWatchNo": "False"})
        assert _statuses(c) == {"Watch list": SQL}
        assert c.sql_parts == [("Watch list", "coalesce(watch, 0) = 1")]
        assert c.coverage == 1.0        # SQL still counts as migrated

    def test_user_data_column(self):
        c = _convert({"cbxUserData": "0", "edtUserData": "solved"})
        label, sql = c.sql_parts[0]
        assert label == "User data 1"
        assert "user_data_1" in sql and "'%solved%'" in sql

    def test_user_data_regex_cannot_run_anywhere(self):
        c = _convert({"cbxUserData": "7", "edtUserData": "^x"})
        assert _statuses(c) == {"User data 1": COMMENT}

    def test_elevation_range(self):
        c = _convert({"cbxElevation": "4", "edtElevation": "1000",
                      "edtElevation2": "2000"})
        assert c.sql_parts == [("Elevation", "elevation between 1000 and 2000")]


# ── Where clause ──────────────────────────────────────────────────────────────

class TestGsakWhereClause:
    def test_translatable_clause_runs(self):
        c = _convert({}, where="UserFlag = '1' and cachetype = 'T'")
        assert _statuses(c) == {"GSAK where clause": SQL}
        sql = c.sql_parts[0][1]
        assert "user_flag = 1" in sql and "cache_type = 'Traditional Cache'" in sql

    def test_untranslatable_clause_is_commented_never_run(self):
        c = _convert({}, where="FavPerc > 70")
        assert _statuses(c) == {"GSAK where clause": COMMENT}
        assert c.sql_parts == []
        text = c.where_text()
        assert "FavPerc" in text
        # Every line of it is inert except the trailing no-op condition.
        body = [ln for ln in text.splitlines() if not ln.startswith("--")]
        assert body == ["1 = 1"]

    def test_custom_column_is_reported_by_name(self):
        c = _convert({}, where="MyCol = 1", custom=["MyCol;bool;;0;1"])
        labels = [crit.label for crit in c.criteria]
        assert any("MyCol" in label for label in labels)


# ── The Where tab's text ──────────────────────────────────────────────────────

class TestWhereText:
    def test_comments_precede_sql_so_the_clause_stays_valid(self):
        c = Conversion("F")
        c.sql("Watch list", "coalesce(watch, 0) = 1")
        c.comment("Something", "cannot be expressed")
        lines = c.where_text().splitlines()
        assert lines[0].startswith("--")
        assert not lines[-1].startswith("--")     # SQL must have the last word

    def test_several_sql_parts_are_anded(self):
        c = Conversion("F")
        c.sql("A", "a = 1")
        c.sql("B", "b = 2")
        assert [ln for ln in c.where_text().splitlines() if not ln.startswith("--")] \
            == ["(a = 1)", "AND (b = 2)"]

    def test_no_criteria_means_no_where_clause(self):
        fs = Conversion("F").build_filterset()
        assert len(fs) == 0

    def test_generated_sql_is_valid_against_the_real_schema(self, db_session):
        c = Conversion("F")
        c.sql("Watch list", "coalesce(watch, 0) = 1")
        c.comment("Custom column \"x\"", "OpenSAK has no custom columns")
        assert validate_where_sql(db_session, c.where_text()) is None

    def test_comment_only_clause_is_valid_and_matches_everything(self, db_session):
        c = Conversion("F")
        c.comment("Custom column \"x\"", "OpenSAK has no custom columns")
        assert validate_where_sql(db_session, c.where_text()) is None


# ── Coverage ──────────────────────────────────────────────────────────────────

class TestCoverage:
    def test_all_native_is_full_coverage(self):
        c = Conversion("F")
        c.native("A", object())
        assert c.coverage == 1.0 and c.not_migrated == 0

    def test_all_commented_is_zero_coverage(self):
        c = Conversion("F")
        c.comment("A", "nope")
        c.comment("B", "nope")
        assert c.coverage == 0.0

    def test_mixed_coverage_counts_sql_as_migrated(self):
        c = Conversion("F")
        c.native("A", object())
        c.sql("B", "b = 1")
        c.comment("C", "nope")
        assert c.coverage == pytest.approx(2 / 3)

    def test_empty_filter_counts_as_fully_migrated(self):
        assert Conversion("F").coverage == 1.0


# ── End to end ────────────────────────────────────────────────────────────────

class TestImportGsakFilters:
    def test_writes_profiles_and_totals_coverage(self, tmp_path):
        db = _make_gsak_db(tmp_path / "gsak.db3", {
            "Found": _blob({"chkFound": "True", "chkNotFound": "False"}),
            "Custom": _blob({}, custom=["MyCol;bool;;0;1"]),
        })
        out = tmp_path / "filters"
        result = import_gsak_filters(db, out_dir=out, opts=Options())

        assert result.written == 2 and result.failed == 0
        assert {p.name for p in out.glob("*.json")} == {"Found.json", "Custom.json"}
        assert (result.native_criteria, result.commented_criteria) == (1, 1)
        assert result.coverage == 0.5
        assert (result.fully_migrated, result.not_migrated) == (1, 1)

    def test_written_profiles_load_back_unchanged(self, tmp_path):
        db = _make_gsak_db(tmp_path / "gsak.db3", {
            "Mixed": _blob({"chkFound": "True", "chkNotFound": "False",
                            "chkWatchYes": "True", "chkWatchNo": "False"}),
        })
        out = tmp_path / "filters"
        import_gsak_filters(db, out_dir=out, opts=Options())
        data = json.loads((out / "Mixed.json").read_text(encoding="utf-8"))
        fs = FilterSet.from_dict(data["filterset"])
        assert len(fs) == len(data["filterset"]["filters"])
        assert fs.to_dict() == data["filterset"]

    def test_selected_names_only(self, tmp_path):
        db = _make_gsak_db(tmp_path / "gsak.db3", {
            "A": _blob({"chkFound": "True", "chkNotFound": "False"}),
            "B": _blob({"chkFound": "True", "chkNotFound": "False"}),
        })
        out = tmp_path / "filters"
        result = import_gsak_filters(db, out_dir=out, names=["B"], opts=Options())
        assert result.written == 1
        assert [p.name for p in out.glob("*.json")] == ["B.json"]

    def test_existing_profile_is_skipped_unless_overwriting(self, tmp_path):
        db = _make_gsak_db(tmp_path / "gsak.db3", {
            "A": _blob({"chkFound": "True", "chkNotFound": "False"}),
        })
        out = tmp_path / "filters"
        out.mkdir()
        (out / "A.json").write_text("keep me", encoding="utf-8")

        result = import_gsak_filters(db, out_dir=out, opts=Options())
        assert (result.written, result.skipped) == (0, 1)
        assert (out / "A.json").read_text(encoding="utf-8") == "keep me"
        # Skipped filters are left out of the statistics entirely.
        assert result.total_criteria == 0

        result = import_gsak_filters(db, out_dir=out, overwrite=True, opts=Options())
        assert (result.written, result.skipped) == (1, 0)
        assert "filterset" in (out / "A.json").read_text(encoding="utf-8")

    def test_names_colliding_on_disk_get_separate_files(self, tmp_path):
        db = _make_gsak_db(tmp_path / "gsak.db3", {
            "A/B": _blob({"chkFound": "True", "chkNotFound": "False"}),
            "A*B": _blob({"chkFound": "True", "chkNotFound": "False"}),
        })
        out = tmp_path / "filters"
        import_gsak_filters(db, out_dir=out, opts=Options())
        assert {p.name for p in out.glob("*.json")} == {"A_B.json", "A_B_2.json"}

    def test_one_broken_filter_does_not_stop_the_others(self, tmp_path, monkeypatch):
        db = _make_gsak_db(tmp_path / "gsak.db3", {
            "Bad": _blob({"chkFound": "True", "chkNotFound": "False"}),
            "Good": _blob({"chkFound": "True", "chkNotFound": "False"}),
        })
        real_convert = gfi.convert

        def explode(gf, opts):
            if gf.name == "Bad":
                raise RuntimeError("boom")
            return real_convert(gf, opts)

        monkeypatch.setattr(gfi, "convert", explode)
        result = import_gsak_filters(db, out_dir=tmp_path / "filters", opts=Options())
        assert (result.written, result.failed) == (1, 1)
        assert "boom" in next(e.error for e in result.entries if e.name == "Bad")

    def test_progress_is_reported_per_filter(self, tmp_path):
        db = _make_gsak_db(tmp_path / "gsak.db3", {
            "A": _blob({}), "B": _blob({}),
        })
        seen = []
        import_gsak_filters(db, out_dir=tmp_path / "filters",
                            opts=Options(), progress_cb=lambda d, t: seen.append((d, t)))
        assert seen == [(1, 2), (2, 2)]


# ── Source selection ──────────────────────────────────────────────────────────

class TestSource:
    def test_a_cache_database_is_rejected_with_a_clear_error(self, tmp_path):
        path = tmp_path / "sqlite.db3"
        sqlite3.connect(path).close()
        with pytest.raises(GsakFilterSourceError):
            load_gsak_filters(path)

    def test_zip_backup_is_unpacked(self, tmp_path):
        import zipfile
        db = _make_gsak_db(tmp_path / "gsak.db3", {"A": _blob({})})
        archive = tmp_path / "backup.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(db, "GSAK/gsak.db3")
        assert find_gsak_filter_db(archive).name == "gsak.db3"

    def test_zip_without_gsak_db3_is_rejected(self, tmp_path):
        import zipfile
        archive = tmp_path / "backup.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("readme.txt", "nothing here")
        with pytest.raises(GsakFilterSourceError):
            find_gsak_filter_db(archive)

    def test_filters_are_listed_alphabetically(self, tmp_path):
        db = _make_gsak_db(tmp_path / "gsak.db3",
                           {"zulu": _blob({}), "Alpha": _blob({})})
        assert [name for name, _ in load_gsak_filters(db)] == ["Alpha", "zulu"]
