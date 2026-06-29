# tests/unit-tests/test_coords.py — format_coords / parse_coords across all formats.

import pytest
from opensak.coords import (
    FORMAT_DD,
    FORMAT_DMM,
    FORMAT_DMS,
    format_coords,
    format_lat,
    format_lon,
    parse_coords,
)


# ── format_coords ─────────────────────────────────────────────────────────────

class TestFormatCoordsDD:
    def test_positive(self):
        assert format_coords(55.78750, 12.41667, FORMAT_DD) == "55.78750, 12.41667"

    def test_negative_lat(self):
        assert format_coords(-33.86785, 151.20732, FORMAT_DD) == "-33.86785, 151.20732"

    def test_negative_lon(self):
        assert format_coords(51.50735, -0.12776, FORMAT_DD) == "51.50735, -0.12776"

    def test_both_negative(self):
        assert format_coords(-34.60376, -58.38162, FORMAT_DD) == "-34.60376, -58.38162"

    def test_zero_lat(self):
        assert format_coords(0.0, 32.0, FORMAT_DD) == "0.00000, 32.00000"

    def test_zero_lon(self):
        assert format_coords(45.0, 0.0, FORMAT_DD) == "45.00000, 0.00000"

    def test_both_zero(self):
        assert format_coords(0.0, 0.0, FORMAT_DD) == "0.00000, 0.00000"

    def test_five_decimal_places(self):
        result = format_coords(1.123456789, 2.987654321, FORMAT_DD)
        assert result == "1.12346, 2.98765"


class TestFormatCoordsDMM:
    def test_north_east(self):
        result = format_coords(55.7875, 12.41667, FORMAT_DMM)
        assert result.startswith("N")
        assert "E" in result

    def test_south_west(self):
        result = format_coords(-34.60376, -58.38162, FORMAT_DMM)
        assert result.startswith("S")
        assert "W" in result

    def test_north_west(self):
        result = format_coords(51.50735, -0.12776, FORMAT_DMM)
        assert result.startswith("N")
        assert "W" in result

    def test_south_east(self):
        result = format_coords(-33.86785, 151.20732, FORMAT_DMM)
        assert result.startswith("S")
        assert "E" in result

    def test_zero_lat(self):
        result = format_coords(0.0, 10.0, FORMAT_DMM)
        assert result.startswith("N")

    def test_minutes_precision(self):
        # lat=55.7875 → 55° + 0.7875*60 = 47.250 min
        result = format_coords(55.7875, 12.41667, FORMAT_DMM)
        assert "47.250" in result

    def test_lon_zero_padded_to_three_digits(self):
        # lon=9.x should be formatted as E009
        result = format_coords(55.0, 9.5, FORMAT_DMM)
        assert "E009" in result

    def test_default_format_is_dmm(self):
        # format_coords falls back to DMM for unknown format strings
        result = format_coords(55.7875, 12.41667, "unknown")
        assert result == format_coords(55.7875, 12.41667, FORMAT_DMM)


class TestFormatCoordsDMS:
    def test_north_east(self):
        result = format_coords(55.7875, 12.41667, FORMAT_DMS)
        assert result.startswith("N")
        assert "E" in result
        assert "°" in result
        assert "'" in result
        assert '"' in result

    def test_south_west(self):
        result = format_coords(-34.60376, -58.38162, FORMAT_DMS)
        assert result.startswith("S")
        assert "W" in result

    def test_seconds_present(self):
        # lat=55.7875 → 55° 47' 15.00"
        result = format_coords(55.7875, 12.41667, FORMAT_DMS)
        assert "47'" in result or "47' " in result
        assert "15.00" in result

    def test_zero_seconds(self):
        result = format_coords(55.0, 12.0, FORMAT_DMS)
        assert "00.00" in result


# ── parse_coords ──────────────────────────────────────────────────────────────

class TestParseCoordsDD:
    def test_comma_separated(self):
        result = parse_coords("55.78750, 12.41667")
        assert result == pytest.approx((55.78750, 12.41667), rel=1e-5)

    def test_space_separated(self):
        result = parse_coords("55.78750 12.41667")
        assert result == pytest.approx((55.78750, 12.41667), rel=1e-5)

    def test_negative_values(self):
        result = parse_coords("-34.60376, -58.38162")
        assert result == pytest.approx((-34.60376, -58.38162), rel=1e-5)

    def test_mixed_signs(self):
        result = parse_coords("51.50735, -0.12776")
        assert result == pytest.approx((51.50735, -0.12776), rel=1e-5)

    def test_zero_coords(self):
        result = parse_coords("0.0, 0.0")
        assert result == pytest.approx((0.0, 0.0))


class TestParseCoordsDMM:
    def test_north_east(self):
        result = parse_coords("N55 47.250 E012 25.000")
        assert result == pytest.approx((55.7875, 12.41667), rel=1e-4)

    def test_south_west(self):
        result = parse_coords("S34 36.226 W058 22.897")
        assert result is not None
        lat, lon = result
        assert lat < 0
        assert lon < 0

    def test_lowercase_hemisphere(self):
        result = parse_coords("n55 47.250 e012 25.000")
        assert result == pytest.approx((55.7875, 12.41667), rel=1e-4)

    def test_north_west(self):
        result = parse_coords("N51 30.441 W000 07.666")
        assert result is not None
        lat, lon = result
        assert lat > 0
        assert lon < 0

    def test_zero_lat(self):
        result = parse_coords("N00 00.000 E032 00.000")
        assert result == pytest.approx((0.0, 32.0), rel=1e-5)


class TestParseCoordsDMMDegree:
    # Geocaching.com copy-paste format: N 34° 58.088' E 034° 03.281'

    def test_basic(self):
        result = parse_coords("N 34° 58.088' E 034° 03.281'")
        assert result is not None
        lat, lon = result
        assert lat == pytest.approx(34 + 58.088 / 60, rel=1e-5)
        assert lon == pytest.approx(34 + 3.281 / 60, rel=1e-5)

    def test_south_west(self):
        result = parse_coords("S 34° 58.088' W 034° 03.281'")
        assert result is not None
        lat, lon = result
        assert lat < 0
        assert lon < 0

    def test_no_spaces(self):
        result = parse_coords("N34°58.088'E034°03.281'")
        assert result is not None

    def test_zero_minutes(self):
        result = parse_coords("N 45° 00.000' E 090° 00.000'")
        assert result == pytest.approx((45.0, 90.0), rel=1e-5)


class TestParseCoordsDMS:
    def test_basic(self):
        result = parse_coords("N55° 47' 15.00\" E012° 25' 00.00\"")
        assert result is not None
        lat, lon = result
        assert lat == pytest.approx(55.7875, rel=1e-4)

    def test_south_west(self):
        result = parse_coords("S34° 36' 13.00\" W058° 22' 53.00\"")
        assert result is not None
        lat, lon = result
        assert lat < 0
        assert lon < 0

    def test_zero_seconds(self):
        result = parse_coords("N45° 00' 00.00\" E090° 00' 00.00\"")
        assert result == pytest.approx((45.0, 90.0), rel=1e-5)


class TestParseCoordsInvalid:
    def test_empty_string(self):
        assert parse_coords("") is None

    def test_plain_text(self):
        assert parse_coords("not a coordinate") is None

    def test_partial_dd(self):
        assert parse_coords("55.78750") is None

    def test_only_hemisphere(self):
        assert parse_coords("N E") is None

    def test_integer_only(self):
        assert parse_coords("55, 12") is None

    def test_whitespace_only(self):
        assert parse_coords("   ") is None


class TestParseCoordsOutOfRange:
    # Regression for #323: syntactically valid but geographically impossible inputs
    # must return None instead of producing nonsense coordinates.

    def test_dd_lat_exceeds_90(self):
        assert parse_coords("91.0, 10.0") is None

    def test_dd_lon_exceeds_180(self):
        assert parse_coords("45.0, 181.0") is None

    def test_dd_lat_below_minus_90(self):
        assert parse_coords("-91.0, 10.0") is None

    def test_dd_lon_below_minus_180(self):
        assert parse_coords("45.0, -181.0") is None

    def test_dmm_lat_degrees_exceeds_90(self):
        # N418 33.000 E008 40.000 — lat degrees way out of range
        assert parse_coords("N418 33.000 E008 40.000") is None

    def test_dmm_lon_minutes_overflow(self):
        # minutes value far exceeds 59.999
        assert parse_coords("N41 08.330 W008 40000000000000.323") is None

    def test_dmm_lat_minutes_at_60(self):
        assert parse_coords("N41 60.000 E008 30.000") is None

    def test_dmm_lon_degrees_exceeds_180(self):
        assert parse_coords("N41 30.000 E181 00.000") is None

    def test_dms_lat_degrees_exceeds_90(self):
        assert parse_coords("N91° 00' 00.00\" E012° 00' 00.00\"") is None

    def test_dms_lat_minutes_at_60(self):
        assert parse_coords("N45° 60' 00.00\" E012° 00' 00.00\"") is None

    def test_dms_lon_seconds_at_60(self):
        assert parse_coords("N45° 00' 00.00\" E012° 00' 60.00\"") is None

    def test_boundary_lat_90_is_valid(self):
        assert parse_coords("N90 00.000 E000 00.000") is not None

    def test_boundary_lon_180_is_valid(self):
        assert parse_coords("N00 00.000 E180 00.000") is not None


class TestParseCoordsRoundtrip:
    # format_coords → parse_coords should recover the original values.

    @pytest.mark.parametrize("lat,lon", [
        (55.7875, 12.41667),
        (-33.86785, 151.20732),
        (51.50735, -0.12776),
        (-34.60376, -58.38162),
        (0.0, 0.0),
    ])
    def test_dmm_roundtrip(self, lat, lon):
        text = format_coords(lat, lon, FORMAT_DMM)
        result = parse_coords(text)
        assert result is not None
        assert result == pytest.approx((lat, lon), abs=1e-4)

    @pytest.mark.parametrize("lat,lon", [
        (55.7875, 12.41667),
        (-33.86785, 151.20732),
        (51.50735, -0.12776),
        (-34.60376, -58.38162),
    ])
    def test_dms_roundtrip(self, lat, lon):
        text = format_coords(lat, lon, FORMAT_DMS)
        result = parse_coords(text)
        assert result is not None
        # DMS has lower precision (seconds rounded to 2 dp)
        assert result == pytest.approx((lat, lon), abs=1e-3)


# ── format_lat / format_lon (single-axis, used by table columns) ──────────────

class TestFormatLat:
    def test_dd(self):
        assert format_lat(55.78750, FORMAT_DD) == "55.787500"

    def test_dd_negative(self):
        assert format_lat(-33.86785, FORMAT_DD) == "-33.867850"

    def test_dmm_north(self):
        assert format_lat(55.7875, FORMAT_DMM) == "N55 47.250"

    def test_dmm_south(self):
        assert format_lat(-33.5, FORMAT_DMM) == "S33 30.000"

    def test_dms_north(self):
        assert format_lat(55.7875, FORMAT_DMS) == "N55° 47' 15.00\""

    def test_dms_south(self):
        assert format_lat(-1.5, FORMAT_DMS).startswith("S01°")


class TestFormatLon:
    def test_dd(self):
        assert format_lon(12.41667, FORMAT_DD) == "12.416670"

    def test_dd_negative(self):
        assert format_lon(-0.12776, FORMAT_DD) == "-0.127760"

    def test_dmm_east_pads_three_digits(self):
        assert format_lon(12.41667, FORMAT_DMM) == "E012 25.000"

    def test_dmm_west(self):
        assert format_lon(-90.379567, FORMAT_DMM) == "W090 22.774"

    def test_dms_east(self):
        assert format_lon(12.41667, FORMAT_DMS) == "E012° 25' 00.01\""

    def test_dms_west(self):
        assert format_lon(-90.5, FORMAT_DMS).startswith("W090°")
