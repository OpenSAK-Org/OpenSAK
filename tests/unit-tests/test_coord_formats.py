# tests/unit-tests/test_coord_formats.py — grid projections (geodesy) and the
# string formats of opensak.coord_formats: reading, writing, auto-detection.

import pytest

from opensak import geodesy
from opensak.coord_formats import SYSTEMS, format_as, in_area, normalize_key, parse_any

# Reference positions computed with PROJ 9 (pyproj), using the same datum
# shifts (EPSG:1777 for DHDN, the Ordnance Survey Helmert set for OSGB36).


# ── Projections ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("to, back, lat, lon, expected, tol", [
    (geodesy.to_rd, geodesy.from_rd, 52.3731, 4.8922, (121_290.51, 487_362.28), 0.5),
    (geodesy.to_osgb, geodesy.from_osgb, 51.50072, -0.12462, (530_268.46, 179_642.91), 0.05),
    (geodesy.to_osgb, geodesy.from_osgb, 55.9486, -3.1999, (325_163.84, 673_490.65), 0.05),
    (geodesy.to_sweref99, geodesy.from_sweref99, 59.3293, 18.0686, (6_580_743.01, 674_571.87), 0.01),
    (geodesy.to_gauss_krueger, geodesy.from_gauss_krueger, 48.1374, 11.5755,
     (4_468_513.49, 5_333_350.27), 0.05),
    (geodesy.to_gauss_krueger, geodesy.from_gauss_krueger, 50.9413, 6.9583,
     (2_567_396.74, 5_645_554.16), 0.05),
])
def test_projection_matches_reference_and_round_trips(to, back, lat, lon, expected, tol):
    result = to(lat, lon)
    assert result == pytest.approx(expected, abs=tol)
    assert back(*result) == pytest.approx((lat, lon), abs=1e-6)


def test_utm_round_trip_southern_hemisphere():
    zone, band, easting, northing = geodesy.to_utm(-33.8688, 151.2093)
    assert (easting, northing) == pytest.approx((334_368.63, 6_250_948.35), abs=0.01)
    assert geodesy.from_utm(zone, False, easting, northing) == pytest.approx(
        (-33.8688, 151.2093), abs=1e-9)


def test_ch1903_inverse_matches_swisstopo_example():
    # swisstopo's worked example: 700 000 / 100 000 ↔ 46° 2' 38.87", 8° 43' 49.79"
    lat = 46 + 2 / 60 + 38.87 / 3600
    lon = 8 + 43 / 60 + 49.79 / 3600
    assert geodesy.from_ch1903(700_000, 100_000) == pytest.approx((lat, lon), abs=1e-5)
    assert geodesy.from_ch1903(2_700_000, 1_100_000) == pytest.approx((lat, lon), abs=1e-5)


# ── Formats ───────────────────────────────────────────────────────────────────

ZURICH = (47.36872, 8.54093)


@pytest.mark.parametrize("key, expected", [
    ("utm", "32T E 465340 N 5246242"),
    ("mgrs", "32T MT 65339 46242"),
    ("olc", "8FVC9G9R+F9M"),
    ("geohash", "u0qj6r5qf"),
    ("maidenhead", "JN47gi48"),
])
def test_format_global(key, expected):
    assert format_as(*ZURICH, key) == expected


def test_format_national_grids():
    assert format_as(51.50072, -0.12462, "osgb") == "TQ 30268 79642"
    assert format_as(52.3731, 4.8922, "rd") == "121290 / 487362"
    assert format_as(59.3293, 18.0686, "sweref99") == "N 6580743 E 674572"
    assert format_as(48.1374, 11.5755, "gk") == "R 4468513 H 5333350"


def test_osgb_outside_grid_raises():
    with pytest.raises(ValueError):
        format_as(*ZURICH, "osgb")


def test_aliases_and_unknown_formats():
    assert normalize_key("LV95") == "ch1903+"
    assert normalize_key("QTH") == "maidenhead"
    assert normalize_key("dd") == "dd"
    assert normalize_key("nope") is None
    with pytest.raises(ValueError):
        format_as(0, 0, "nope")
    with pytest.raises(ValueError):
        parse_any("x", "nope")


def test_in_area():
    assert in_area("ch1903", *ZURICH)
    assert not in_area("rd", *ZURICH)
    assert in_area("olc", -60, 170)          # global formats have no area


@pytest.mark.parametrize("lat, lon", [
    ZURICH, (52.3731, 4.8922), (51.50072, -0.12462), (59.3293, 18.0686),
    (52.5163, 13.3777), (-33.8688, 151.2093), (40.7128, -74.006), (-0.5, -0.5),
])
def test_every_format_round_trips_with_detection(lat, lon):
    for key, system in SYSTEMS.items():
        if not in_area(key, lat, lon):
            continue
        text = system.format(lat, lon)
        tolerance = 0.03 if key == "maidenhead" else 3e-5
        for fmt in (None, key):
            parsed = parse_any(text, fmt)
            assert parsed is not None, (key, fmt, text)
            assert parsed[:2] == pytest.approx((lat, lon), abs=tolerance), (key, text)


@pytest.mark.parametrize("text, key", [
    ("N47 22.123 E008 32.456", "latlon"),
    ("47.36872, 8.54093", "latlon"),
    ("32T 465340 5246242", "utm"),
    ("32 T E 465340 N 5246242", "utm"),
    ("32N 465340 5246242", "utm"),           # N as hemisphere, not band N
    ("32TMT6533946242", "mgrs"),
    ("32T MT 653 462", "mgrs"),
    ("683259 / 247015", "ch1903"),
    ("Y 683259 X 247015", "ch1903"),
    ("X 247015 Y 683259", "ch1903"),
    ("E 2683259, N 1247015", "ch1903"),
    ("121290 / 487362", "rd"),               # not LV03 near Geneva
    ("TQ 30268 79642", "osgb"),
    ("tq3026879642", "osgb"),
    ("N 6580743 E 674572", "sweref99"),
    ("674572 6580743", "sweref99"),
    ("R 4468513 H 5333350", "gk"),
    ("8FVC9G8F+6X", "olc"),
    ("JN47", "maidenhead"),
    ("NN17", "maidenhead"),                  # a locator, not an OSGB square
    ("u0qj8ze2t", "geohash"),
])
def test_detection(text, key):
    parsed = parse_any(text)
    assert parsed is not None and parsed[2] == key, (text, parsed)


@pytest.mark.parametrize("text", [
    "", "nonsense", "berg",                  # "berg" is a geohash, but not detected
    "530268 179642 0",
    "9G8F+6X",                               # short Plus Code: needs a reference
    "248115 / 683144",                       # Swiss numbers in the wrong order
    "1 2",
])
def test_detection_rejects(text):
    assert parse_any(text) is None


def test_numeric_osgb_needs_explicit_format():
    assert parse_any("530268 179642")[2] != "osgb"
    lat, lon, key = parse_any("530268 179642", "osgb")
    assert key == "osgb"
    assert (lat, lon) == pytest.approx((51.50072, -0.12462), abs=1e-5)


def test_explicit_format_only_tries_that_format():
    assert parse_any("N47 22.123 E008 32.456", "utm") is None
    assert parse_any("berg", "geohash") is not None
    assert parse_any("2683259 / 1247015", "ch1903+")[2] == "ch1903"
