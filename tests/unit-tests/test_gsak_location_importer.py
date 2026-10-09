# tests/unit-tests/test_gsak_location_importer.py — GSAK user locations → OpenSAK (#1001).

import sqlite3
from pathlib import Path

import pytest

from opensak.importer import gsak_location_importer as gli
from opensak.importer.gsak_location_importer import (
    GsakLocationSourceError, apply_locations, existing_location_names,
    load_gsak_locations, parse_gsak_locations,
)

# The samples from the issue: GSAK's own header plus three coordinate styles.
SAMPLE = """# examples (valid if # is removed)
#
#Home, S32 25.921  E 115 47.050
#Some where else,  32.7123  -101.2045
Zurich,47.371722, 8.537466
Paris,N48° 51.767 E2° 19.886

  London, 51.49872,-0.137326
"""


def _gsak_db3(path: Path, rows: list[tuple[str, str, str]], table: str = "Settings") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"CREATE TABLE {table} (Type TEXT, Description TEXT, Data TEXT)")
        conn.executemany(f"INSERT INTO {table} VALUES (?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def settings(monkeypatch):
    """AppSettings on the isolated store (conftest), without a database."""
    monkeypatch.setattr(
        "opensak.db.manager.get_db_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("no manager")),
    )
    from opensak.gui.settings import get_settings
    return get_settings()


# ── parse_gsak_locations ─────────────────────────────────────────────────────

class TestParse:
    def test_issue_sample(self):
        locs = parse_gsak_locations(SAMPLE)
        assert [(l.line_no, l.name) for l in locs] == [
            (5, "Zurich"), (6, "Paris"), (8, "London"),
        ]
        assert all(l.valid for l in locs)
        zurich, paris, london = locs
        assert (zurich.lat, zurich.lon) == pytest.approx((47.371722, 8.537466))
        assert (paris.lat, paris.lon) == pytest.approx((48 + 51.767 / 60, 2 + 19.886 / 60))
        assert (london.lat, london.lon) == pytest.approx((51.49872, -0.137326))
        assert london.text == "London, 51.49872,-0.137326"

    def test_name_ends_at_first_comma(self):
        (loc,) = parse_gsak_locations("Somewhere else,  32.7123  -101.2045")
        assert loc.name == "Somewhere else"
        assert (loc.lat, loc.lon) == pytest.approx((32.7123, -101.2045))

    def test_dmm_with_hemisphere_letters(self):
        (loc,) = parse_gsak_locations("Perth, S32 25.921  E 115 47.050")
        assert loc.valid
        assert loc.lat == pytest.approx(-(32 + 25.921 / 60))

    @pytest.mark.parametrize("line, expected", [
        ("Biel, N47,1395 E7,243", (47.1395, 7.243)),
        ("Luzern, 47,03555 8,25546", (47.03555, 8.25546)),
        ("Luzern, 47,03555, 8,25546", (47.03555, 8.25546)),
        ("Süd, S33,5 W70,6", (-33.5, -70.6)),
        ("DMM, N47 08,370 E007 14,580", (47 + 8.37 / 60, 7 + 14.58 / 60)),
    ])
    def test_decimal_comma(self, line, expected):
        (loc,) = parse_gsak_locations(line)
        assert loc.valid, loc.error
        assert (loc.lat, loc.lon) == pytest.approx(expected)

    def test_decimal_point_with_comma_separator_is_not_reinterpreted(self):
        (loc,) = parse_gsak_locations("A,47.1,8.5")
        assert (loc.lat, loc.lon) == pytest.approx((47.1, 8.5))

    def test_decimal_comma_still_needs_a_valid_coordinate(self):
        (loc,) = parse_gsak_locations("A, 147,5 8,5")
        assert loc.error == gli.ERROR_BAD_COORD

    def test_blank_and_comment_lines_are_ignored(self):
        assert parse_gsak_locations("\n   \n# a comment\n  #indented comment\n") == []

    @pytest.mark.parametrize("line, error", [
        (", 47.1 8.5", gli.ERROR_NO_NAME),
        ("Nowhere", gli.ERROR_NO_COORD),
        ("Nowhere,   ", gli.ERROR_NO_COORD),
        ("Nowhere, 123 abc", gli.ERROR_BAD_COORD),
        ("Nowhere, 95.0, 8.5", gli.ERROR_BAD_COORD),
        ("★ Home, 47.1, 8.5", gli.ERROR_RESERVED),
        ("Home, 47.1, 8.5", gli.ERROR_RESERVED),
        ("home, 47.1, 8.5", gli.ERROR_RESERVED),
        ("HOME, 47.1, 8.5", gli.ERROR_RESERVED),
        ("  ★home , 47.1, 8.5", gli.ERROR_RESERVED),
    ])
    def test_invalid_lines_are_kept_with_reason(self, line, error):
        (loc,) = parse_gsak_locations(line)
        assert loc.error == error and not loc.valid
        assert loc.lat is None and loc.lon is None

    def test_repeated_name_keeps_first_line(self):
        locs = parse_gsak_locations("A, 47.1, 8.5\nA, 46.0, 7.0\nB, 1.0, 2.0")
        assert [l.error for l in locs] == [None, gli.ERROR_DUPLICATE, None]
        assert locs[0].lat == pytest.approx(47.1)

    def test_names_are_case_sensitive(self):
        locs = parse_gsak_locations("Work, 47.1, 8.5\nwork, 46.0, 7.0")
        assert all(l.valid for l in locs)

    def test_names_merely_containing_home_are_fine(self):
        locs = parse_gsak_locations("Home 2, 47.1, 8.5\nMy Home, 46.0, 7.0")
        assert all(l.valid for l in locs)


# ── load_gsak_locations ──────────────────────────────────────────────────────

class TestLoad:
    def test_reads_the_lo_row(self, tmp_path):
        db = _gsak_db3(tmp_path / "gsak.db3", [
            ("FI", "My filter", "edtX=1"),
            ("LO", "Location", "Zurich,47.371722, 8.537466"),
        ])
        assert load_gsak_locations(db) == "Zurich,47.371722, 8.537466"

    def test_no_lo_row_is_empty(self, tmp_path):
        db = _gsak_db3(tmp_path / "gsak.db3", [("FI", "My filter", "edtX=1")])
        assert load_gsak_locations(db) == ""

    def test_no_settings_table_is_empty(self, tmp_path):
        db = _gsak_db3(tmp_path / "gsak.db3", [], table="Other")
        assert load_gsak_locations(db) == ""

    def test_not_a_database_raises(self, tmp_path):
        bad = tmp_path / "gsak.db3"
        bad.write_bytes(b"settings" * 100)
        with pytest.raises(GsakLocationSourceError):
            load_gsak_locations(bad)

    def test_file_is_released_after_reading(self, tmp_path):
        db = _gsak_db3(tmp_path / "gsak.db3", [("LO", "Location", "A, 1.0, 2.0")])
        load_gsak_locations(db)
        db.unlink()   # fails on Windows while a handle is still open


# ── apply_locations ──────────────────────────────────────────────────────────

class TestApply:
    def test_adds_new_locations(self, settings):
        result = apply_locations(parse_gsak_locations(SAMPLE), overwrite=False)
        assert result.added == ["Zurich", "Paris", "London"]
        assert [p.name for p in settings.home_points] == ["Zurich", "Paris", "London"]
        assert existing_location_names() == {"Zurich", "Paris", "London"}

    def test_invalid_entries_are_ignored(self, settings):
        result = apply_locations(parse_gsak_locations("A, 1.0, 2.0\nB, nonsense"),
                                 overwrite=False)
        assert result.added == ["A"]
        assert [p.name for p in settings.home_points] == ["A"]

    def test_existing_name_is_skipped_without_overwrite(self, settings):
        from opensak.gui.settings import HomePoint
        settings.home_points = [HomePoint("Zurich", 1.0, 2.0)]
        result = apply_locations(parse_gsak_locations(SAMPLE), overwrite=False)
        assert result.skipped == ["Zurich"]
        assert result.added == ["Paris", "London"]
        zurich = settings.home_points[0]
        assert (zurich.name, zurich.lat, zurich.lon) == ("Zurich", 1.0, 2.0)

    def test_existing_name_is_replaced_in_place_with_overwrite(self, settings):
        from opensak.gui.settings import HomePoint
        settings.home_points = [HomePoint("Work", 5.0, 6.0), HomePoint("Zurich", 1.0, 2.0)]
        result = apply_locations(parse_gsak_locations(SAMPLE), overwrite=True)
        assert result.updated == ["Zurich"]
        names = [p.name for p in settings.home_points]
        assert names == ["Work", "Zurich", "Paris", "London"]
        assert settings.home_points[1].lat == pytest.approx(47.371722)

    def test_overwriting_the_active_location_moves_the_centre(self, settings):
        from opensak.gui.settings import HomePoint
        point = HomePoint("Zurich", 1.0, 2.0)
        settings.home_points = [point]
        settings.set_active_home(point)
        apply_locations(parse_gsak_locations(SAMPLE), overwrite=True)
        assert settings.active_home_name == "Zurich"
        assert (settings.home_lat, settings.home_lon) == pytest.approx((47.371722, 8.537466))

    def test_active_location_untouched_when_not_overwritten(self, settings):
        from opensak.gui.settings import HomePoint
        point = HomePoint("Work", 1.0, 2.0)
        settings.home_points = [point]
        settings.set_active_home(point)
        apply_locations(parse_gsak_locations(SAMPLE), overwrite=True)
        assert settings.active_home_name == "Work"
        assert (settings.home_lat, settings.home_lon) == pytest.approx((1.0, 2.0))

    def test_home_point_is_never_stored(self, settings):
        settings.gc_home_location = "N55 47.250 E012 25.000"
        apply_locations(parse_gsak_locations("★ Home, 1.0, 2.0\nHome, 3.0, 4.0\n"
                                             "A, 1.0, 2.0"),
                        overwrite=True)
        from opensak import settings_store
        stored = settings_store.get_store().get("homepoints.list")
        assert [d["name"] for d in stored] == ["A"]
        home = settings.home_points[0]
        assert home.name == "★ Home" and home.lat == pytest.approx(55 + 47.25 / 60)

    def test_nothing_to_write_leaves_store_alone(self, settings):
        from opensak import settings_store
        apply_locations(parse_gsak_locations("A, nonsense"), overwrite=False)
        assert settings_store.get_store().get("homepoints.list") is None
