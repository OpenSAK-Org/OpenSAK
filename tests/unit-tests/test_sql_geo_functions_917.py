"""tests/unit-tests/test_sql_geo_functions_917.py — GSAK's g_Distance(),
g_Bearing1/2() and g_Project() in OpenSAK's SQL (#917), Vincenty in
opensak.geodesy, and the optional method of opensak.coords.distance/bearing/
project.

The GSAK values were produced by GSAK 10 itself (a macro running the same
calls through Sqlite("sql", …), 2026-10-28). OpenSAK matches them except
where it chose differently (see db/sql_functions.py): a 6371 km sphere for
g_Project 'G' (GSAK: 6366.7 km), Vincenty to 1e-12 (GSAK: 1e-6), and NULL /
errors where GSAK calculates with 0 or crashes.
"""

import math
import sqlite3

import pytest

from opensak import geodesy
from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.db.sql_functions import register_geo_functions
from opensak.filters.engine import FilterSet, WhereClauseFilter, apply_filters, validate_where_sql
from opensak.importer.gsak_filter_importer import translate_where
from opensak.macro import MacroError, MacroRuntime

ZRH = "47.37, 8.54"
BRN = "46.948, 7.4474"
# Geoscience Australia's Vincenty test line: Flinders Peak → Buninyong,
# 54 972.271 m, initial bearing 306°52′05.37″.
FLI = (-37.951033416667, 144.424867888889)
BUN = (-37.652821138889, 143.926495527778)
FLI_BUN_KM = 54.972271
FLI_BUN_DEG = 306 + 52 / 60 + 5.37 / 3600


@pytest.fixture()
def con():
    conn = sqlite3.connect(":memory:")
    register_geo_functions(conn)
    yield conn
    conn.close()


def _q(con, expr):
    return con.execute(f"SELECT {expr}").fetchone()[0]


def _latlon(text):
    lat, lon = text.split(";")
    return float(lat), float(lon)


# ── Against GSAK ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("expr, gsak", [
    ("g_Project(-37.951033416667, 144.424867888889, 306.868158, 54.972271, 'K', 'V')",
     "-37.652821;143.926496"),
    ("g_Project(-33.45, -70.6667, 180, 10, 'K', 'V')", "-33.540161;-70.666700"),
    ("g_Project(89.9999, 0, 0, 1, 'K', 'V')", "89.991147;-180.000000"),
    ("g_Project(47.37, 8.54, 60, 10000, 'K', 'V')", "19.903016;121.336949"),
    ("g_Project(47.37, 8.54, 123, 0, 'K', 'G')", "47.370000;8.540000"),
])
def test_project_vincenty_matches_gsak_exactly(con, expr, gsak):
    assert _q(con, expr) == gsak


@pytest.mark.parametrize("args, gsak", [
    ("'47.37', '8.54', 0, 1, 'K', 'G'", "47.378999;8.540000"),
    ("47.37, 8.54, 45, 1000, 'T', 'G'", "47.376363;8.549397"),
    ("47.37, 8.54, 90, 1, 'M', 'G'", "47.369998;8.561385"),
    ("47.37, 8.54, 135, 1, 'N', 'G'", "47.358214;8.557397"),
    ("47.37, 8.54, 225, 1000, 'Y', 'G'", "47.364181;8.531409"),
    ("47.37, 8.54, 315, 1000, 'F', 'G'", "47.371940;8.537136"),
    ("0, 179.9995, 90, 1, 'K', 'G'", "0.000000;-179.991501"),
    ("47.37, 8.54, 450, 1, 'K', 'G'", "47.369999;8.553288"),
    ("47.37, 8.54, -90, 1, 'K', 'G'", "47.369999;8.526712"),
    ("47.37, 8.54, 0, -1, 'K', 'G'", "47.361001;8.540000"),
    ("47.37, 8.54, 0, 0.001, 'K', 'G'", "47.370009;8.540000"),
])
def test_project_great_circle_close_to_gsak(con, args, gsak):
    # GSAK's sphere is 0.07 % smaller: up to 0.7 m per km, here ≤ 2 km.
    lat, lon = _latlon(_q(con, f"g_Project({args})"))
    glat, glon = _latlon(gsak)
    assert lat == pytest.approx(glat, abs=2e-5)
    assert lon == pytest.approx(glon, abs=2e-5)


@pytest.mark.parametrize("args, gsak, tol", [
    (f"{ZRH}, {BRN}, 'K'", 95.0057190988871, 1e-6),
    (f"{ZRH}, {BRN}, 'M'", 59.0338169458407, 1e-6),
    (f"'47.37', '8.54', '46.948', '7.4474', 'K'", 95.0057190988871, 1e-6),
    (f"{FLI[0]}, {FLI[1]}, {BUN[0]}, {BUN[1]}, 'K'", 54.925466019817, 1e-6),
    # GSAK's Vincenty stops at 1e-6 rad, ~0.2 m short on these lines
    (f"{ZRH}, {BRN}, 'KH'", 95.2114206334403, 3e-6),
    (f"{ZRH}, {BRN}, 'MH'", 59.1616339536111, 3e-6),
    (f"{ZRH}, {BRN}, 'kh'", 95.2114206334403, 3e-6),
    (f"{FLI[0]}, {FLI[1]}, {BUN[0]}, {BUN[1]}, 'KH'", 54.9721170238014, 3e-6),
    (f"{ZRH}, {ZRH}, 'K'", 0.0, 0),
])
def test_distance_close_to_gsak(con, args, gsak, tol):
    assert _q(con, f"g_Distance({args})") == pytest.approx(gsak, rel=tol, abs=1e-12)


@pytest.mark.parametrize("args, gsak", [
    (f"{ZRH}, {BRN}", 240.803921630598),
    (f"'47.37', '8.54', '46.948', '7.4474'", 240.803921630598),
    (f"{FLI[0]}, {FLI[1]}, {BUN[0]}, {BUN[1]}", 306.983874754103),
    ("0, 0, 0, 1", 90.0),
    ("47, 8, 48, 8", 0.0),
])
def test_bearing2_matches_gsak(con, args, gsak):
    assert _q(con, f"g_Bearing2({args})") == pytest.approx(gsak, abs=1e-6)


@pytest.mark.parametrize("deg, gsak", [
    (1, "N"), (22.4, "N"), (22.6, "NE"), (67.4, "NE"), (67.6, "E"), (112.4, "E"),
    (112.6, "SE"), (157.4, "SE"), (157.6, "S"), (202.4, "S"), (202.6, "SW"),
    (247.4, "SW"), (247.6, "W"), (292.4, "W"), (292.6, "NW"), (337.4, "NW"),
    (337.6, "N"), (359, "N"),
])
def test_bearing1_sectors_match_gsak(con, deg, gsak):
    lat, lon = geodesy.project(47, 8, deg, 1.0)
    assert _q(con, f"g_Bearing1(47, 8, {lat}, {lon})") == gsak


def test_bearing1_examples(con):
    assert _q(con, f"g_Bearing1({ZRH}, {BRN})") == "SW"
    assert _q(con, "g_Bearing1(0, 0, 0, 1)") == "E"


# ── Vincenty ──────────────────────────────────────────────────────────────────

def test_vincenty_inverse_reference_line():
    km, deg = geodesy.vincenty_inverse(*FLI, *BUN)
    assert km == pytest.approx(FLI_BUN_KM, abs=1e-6)
    assert deg == pytest.approx(FLI_BUN_DEG, abs=1e-5)


def test_vincenty_direct_reference_line():
    lat, lon = geodesy.vincenty_direct(*FLI, FLI_BUN_DEG, FLI_BUN_KM)
    assert lat == pytest.approx(BUN[0], abs=1e-8)
    assert lon == pytest.approx(BUN[1], abs=1e-8)


@pytest.mark.parametrize("lat, lon, deg, km", [
    (47.37, 8.54, 60, 10000), (-33.45, -70.67, 200, 500), (0, 0, 90, 1000),
    (60, 179.9, 80, 50), (89.5, 10, 0, 100), (47.37, 8.54, 0, -1),
])
def test_vincenty_round_trip(lat, lon, deg, km):
    lat2, lon2 = geodesy.vincenty_direct(lat, lon, deg, km)
    back_km, back_deg = geodesy.vincenty_inverse(lat, lon, lat2, lon2)
    assert back_km == pytest.approx(abs(km), abs=1e-6)
    assert back_deg == pytest.approx((deg if km > 0 else deg + 180) % 360, abs=1e-6)


def test_vincenty_degenerate_lines():
    assert geodesy.vincenty_inverse(47.37, 8.54, 47.37, 8.54) == (0.0, 0.0)
    # does not converge: the great circle instead
    assert geodesy.vincenty_inverse(0, 0, 0.5, 179.7)[0] == pytest.approx(
        geodesy.distance_km(0, 0, 0.5, 179.7))


# ── Where GSAK calculates with 0 or crashes ──────────────────────────────────

@pytest.mark.parametrize("expr", [
    f"g_Distance({ZRH}, {ZRH}, 'KH')",
    "g_Distance(0, 0, 0, 180, 'K')",
    "g_Distance(0, 0, 0.5, 179.7, 'KH')",
])
def test_degenerate_lines_give_a_distance(con, expr):
    assert math.isfinite(_q(con, expr))


@pytest.mark.parametrize("expr", [
    f"g_Bearing2({ZRH}, {ZRH})",
    f"g_Bearing2({ZRH}, {ZRH}, 'H')",
    f"g_Bearing1({ZRH}, {ZRH})",
])
def test_bearing_between_identical_points_is_null(con, expr):
    assert _q(con, expr) is None


@pytest.mark.parametrize("expr", [
    f"g_Distance(NULL, 8.54, 46.948, 7.4474, 'K')",
    f"g_Distance({ZRH}, 46.948, NULL, 'K')",
    f"g_Distance({ZRH}, {BRN}, NULL)",
    f"g_Bearing2(NULL, 8.54, 46.948, 7.4474)",
    f"g_Bearing1({ZRH}, {BRN}, NULL)",
    "g_Project(NULL, 8.54, 0, 1, 'K', 'G')",
    "g_Project(47.37, 8.54, NULL, 1)",
    "g_Project(47.37, 8.54, 0, 1, 'K', NULL)",
])
def test_null_gives_null(con, expr):
    assert _q(con, expr) is None


@pytest.mark.parametrize("expr", [
    f"g_Distance({ZRH}, {BRN}, 'X')",
    f"g_Distance({ZRH}, {BRN}, 'N')",        # GSAK: silently nautical miles
    f"g_Distance({ZRH}, {BRN}, 'T')",        # GSAK: silently nautical miles
    f"g_Distance('abc', 8.54, 46.948, 7.4474, 'K')",     # GSAK: latitude 0
    f"g_Distance('47,37', '8,54', '46,948', '7,4474', 'K')",  # GSAK: 0.0
    f"g_Bearing2({ZRH}, {BRN}, 'X')",
    "g_Project(47.37, 8.54, 0, 1, 'X', 'G')",
    "g_Project(47.37, 8.54, 0, 1, 'K', 'X')",
    "g_Project('N47 22.200', 'E008 32.400', 0, 1, 'K', 'G')",
    f"g_Distance({ZRH}, {BRN}, 'K', 1)",     # no such argument count
])
def test_invalid_arguments_are_errors(con, expr):
    with pytest.raises(sqlite3.OperationalError):
        _q(con, expr)


# ── Defaults, flags and units ────────────────────────────────────────────────

def test_defaults(con):
    assert _q(con, f"g_Distance({ZRH}, {BRN})") == _q(con, f"g_Distance({ZRH}, {BRN}, 'K')")
    assert _q(con, "g_Project(47.37, 8.54, 0, 1)") == _q(con, "g_Project(47.37, 8.54, 0, 1, 'K', 'G')")
    assert _q(con, "g_Project(47.37, 8.54, 0, 1, 'M')") == _q(con, "g_Project(47.37, 8.54, 0, 1, 'M', 'G')")
    assert _q(con, f"g_Distance({ZRH}, {BRN}, 'H')") == _q(con, f"g_Distance({ZRH}, {BRN}, 'KH')")


def test_bearing_method(con):
    gc = _q(con, f"g_Bearing2({FLI[0]}, {FLI[1]}, {BUN[0]}, {BUN[1]})")
    assert _q(con, f"g_Bearing2({FLI[0]}, {FLI[1]}, {BUN[0]}, {BUN[1]}, 'G')") == gc
    for flag in ("H", "V", "h"):
        deg = _q(con, f"g_Bearing2({FLI[0]}, {FLI[1]}, {BUN[0]}, {BUN[1]}, '{flag}')")
        assert deg == pytest.approx(FLI_BUN_DEG, abs=1e-5)


@pytest.mark.parametrize("unit, km", [
    ("K", 1.0), ("M", 1.609344), ("N", 1.852), ("T", 0.001), ("Y", 0.0009144), ("F", 0.0003048),
])
@pytest.mark.parametrize("calc", ["G", "V"])
def test_project_units(con, unit, km, calc):
    assert _q(con, f"g_Project(47.37, 8.54, 30, 1000, '{unit}', '{calc}')") == \
        _q(con, f"g_Project(47.37, 8.54, 30, {1000 * km}, 'K', '{calc}')")


def test_project_then_measure_back(con):
    # unlike GSAK, whose g_Project 'G' and g_Distance use different spheres
    lat, lon = _latlon(_q(con, "g_Project(47.37, 8.54, 77, 25, 'K', 'G')"))
    assert _q(con, f"g_Distance(47.37, 8.54, {lat}, {lon}, 'K')") == pytest.approx(25, abs=1e-4)
    lat, lon = _latlon(_q(con, "g_Project(47.37, 8.54, 77, 25, 'K', 'V')"))
    assert _q(con, f"g_Distance(47.37, 8.54, {lat}, {lon}, 'KH')") == pytest.approx(25, abs=1e-4)


# ── Where it is available ────────────────────────────────────────────────────

@pytest.fixture()
def caches(db_session):
    for code, lat, lon in (("GCZRH", 47.37, 8.54), ("GCBRN", 46.948, 7.4474)):
        db_session.add(Cache(gc_code=code, name=code, cache_type="Traditional Cache",
                             latitude=lat, longitude=lon))
    db_session.commit()


def test_where_clause_filter(caches):
    fs = FilterSet().add(WhereClauseFilter(
        "g_Distance(latitude, longitude, 47.38, 8.55, 'KH') < 5"))
    with get_session() as s:
        assert [c.gc_code for c in apply_filters(s, fs)] == ["GCZRH"]
    fs = FilterSet().add(WhereClauseFilter(
        "g_Bearing1(47.0, 8.0, latitude, longitude) = 'W'"))
    with get_session() as s:
        assert [c.gc_code for c in apply_filters(s, fs)] == ["GCBRN"]


def test_where_clause_validation(caches):
    with get_session() as s:
        assert validate_where_sql(s, "g_Distance(latitude, longitude, 47, 8) < 5") is None
        assert validate_where_sql(s, "g_Project(latitude, longitude, 0, 1) <> ''") is None
        assert validate_where_sql(s, "g_Distance(latitude, longitude, 47) < 5") is not None


class _Host:
    def database_name(self):
        return "TestDB"

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def end_macro(self):
        pass


def _run(source):
    out: list[str] = []
    MacroRuntime(_Host(), output=out.append).run(source)  # type: ignore[arg-type]
    return out


def test_macro_sql(caches):
    out = _run('local r = opensak.sql("SELECT g_Project(latitude, longitude, 0, 1) AS p '
               'FROM caches WHERE gc_code = \'GCZRH\'")\nprint(r[1].p)')
    assert out == ["47.378993;8.540000"]


def test_gsak_filter_import_keeps_the_functions():
    sql, warnings, _notes, verified = translate_where(
        "g_Distance(Latitude, Longitude, 47.37, 8.54, 'KH') < 5 and g_Bearing1(47.37, 8.54, "
        "Latitude, Longitude) = 'SW'")
    assert verified and not warnings
    assert "g_Distance(latitude, longitude, 47.37, 8.54, 'KH')" in sql
    # other g_ names still only exist while a GSAK macro runs
    _sql, warnings, _notes, verified = translate_where("g_FoundLog(Code) = 1")
    assert not verified and warnings


# ── Lua: opensak.coords …, method ─────────────────────────────────────────────

def test_lua_coords_method():
    out = _run(
        f"print(opensak.coords.distance({FLI[0]}, {FLI[1]}, {BUN[0]}, {BUN[1]}, 'vincenty'))\n"
        f"print(opensak.coords.bearing({FLI[0]}, {FLI[1]}, {BUN[0]}, {BUN[1]}, 'vincenty'))\n"
        f"print(opensak.coords.project({FLI[0]}, {FLI[1]}, {FLI_BUN_DEG}, {FLI_BUN_KM}, 'vincenty'))\n"
        f"print(opensak.coords.distance({ZRH}, {BRN}) == opensak.coords.distance({ZRH}, {BRN}, 'haversine'))"
    )
    assert float(out[0]) == pytest.approx(FLI_BUN_KM, abs=1e-6)
    assert float(out[1]) == pytest.approx(FLI_BUN_DEG, abs=1e-5)
    lat, lon = (float(v) for v in out[2].split("\t"))
    assert (lat, lon) == (pytest.approx(BUN[0], abs=1e-8), pytest.approx(BUN[1], abs=1e-8))
    assert out[3] == "true"


def test_lua_coords_method_with_strings():
    out = _run('print(opensak.coords.distance("N47 22.200 E008 32.400", '
               '"N46 56.880 E007 26.844", "vincenty"))')
    assert float(out[0]) == pytest.approx(
        geodesy.vincenty_inverse(47.37, 8.54, 46.948, 7.4474)[0], abs=1e-9)


@pytest.mark.parametrize("call", [
    "opensak.coords.distance(47, 8, 48, 9, 'exact')",
    "opensak.coords.bearing(47, 8, 48, 9, 1)",
    "opensak.coords.project(47, 8, 90, 1, 'V')",
])
def test_lua_coords_bad_method(call):
    with pytest.raises(MacroError, match="method"):
        _run(call)
