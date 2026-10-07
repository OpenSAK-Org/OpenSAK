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


# ── Issue #751: rounding-carry overflow (minutes/seconds hitting 60) ───────
# Reporter's exact repro: pasting "N 59.99999 E 12.99999" produced wildly
# wrong output (degrees=5 instead of 59) due to a regex-backtracking bug in
# parse_coords AND, separately, the DD->DMM/DMS conversion could display
# "60.000" minutes or "60.00" seconds instead of carrying into the next
# unit — reproduced and fixed independently below.

class TestRoundingCarryDMM:
    def test_minutes_rounds_to_60_carries_into_degrees(self):
        # 59.999999 -> raw minutes = 59.99994, rounds to 60.000 at 3dp.
        assert format_coords(59.999999, 12.999999, FORMAT_DMM) == \
            "N60 00.000  E013 00.000"

    def test_just_under_carry_threshold_stays_59_999(self):
        assert format_coords(59.99999, 12.99999, FORMAT_DMM) == \
            "N59 59.999  E012 59.999"

    def test_carry_at_zero_degrees(self):
        # 0.999999... -> minutes round to 60.000, degrees carry 0 -> 1.
        assert format_coords(0.999999, 0.999999, FORMAT_DMM) == \
            "N01 00.000  E001 00.000"

    def test_format_lat_carries(self):
        assert format_lat(59.999999, FORMAT_DMM) == "N60 00.000"

    def test_format_lon_carries(self):
        assert format_lon(12.999999, FORMAT_DMM) == "E013 00.000"

    def test_never_displays_60_minutes(self):
        # Sweep of values close to the rounding boundary must never show
        # ":60.000" — every one must carry into degrees instead.
        for frac in [0.9999991, 0.9999995, 0.9999999]:
            result = format_coords(10 + frac, 20 + frac, FORMAT_DMM)
            assert "60.000" not in result, f"got {result!r} for frac={frac}"


class TestRoundingCarryDMS:
    def test_seconds_rounds_to_60_carries_into_minutes_and_degrees(self):
        # 59.999999 -> minutes=59, seconds round to 60.00, carries minutes
        # to 60, which itself carries into degrees (60 -> 60, 0, 0).
        assert format_coords(59.999999, 12.999999, FORMAT_DMS) == \
            'N60° 00\' 00.00"  E013° 00\' 00.00"'

    def test_just_under_carry_threshold_stays_59_96(self):
        assert format_coords(59.99999, 12.99999, FORMAT_DMS) == \
            'N59° 59\' 59.96"  E012° 59\' 59.96"'

    def test_seconds_carry_without_degree_carry(self):
        # Seconds overflow into minutes, but minutes stay under 60 so
        # degrees must NOT also increment.
        # 10 + 30/60 + 59.999/3600 degrees ~ minutes=30, seconds~59.999->60.00
        lat = 10 + 30 / 60 + 59.999 / 3600
        result = format_coords(lat, lat, FORMAT_DMS)
        assert "10° 31' 00.00" in result
        assert "10° 30' 60" not in result

    def test_format_lat_carries(self):
        assert format_lat(59.999999, FORMAT_DMS) == 'N60° 00\' 00.00"'

    def test_format_lon_carries(self):
        assert format_lon(12.999999, FORMAT_DMS) == 'E013° 00\' 00.00"'

    def test_never_displays_60_seconds(self):
        for frac in [0.99999991, 0.99999995, 0.99999999]:
            result = format_coords(10 + frac, 20 + frac, FORMAT_DMS)
            assert "60.00\"" not in result, f"got {result!r} for frac={frac}"


class TestParseCoordsHemisphereDecimalDegrees:
    """Issue #751: 'N <decimal> E <decimal>' — a plain decimal-degree value
    written with a hemisphere letter instead of a +/- sign, no separate
    minutes component. Previously mis-parsed via the DMM° branch due to
    regex backtracking (degrees group giving back digits to let the
    minutes group match), producing wildly wrong coordinates with no
    error shown."""

    def test_reporter_exact_repro(self):
        # The exact string from the issue report.
        assert parse_coords("N 59.99999 E 12.99999") == pytest.approx((59.99999, 12.99999))

    def test_reporter_second_example(self):
        assert parse_coords("N 59.999999 E 12.999999") == pytest.approx((59.999999, 12.999999))

    def test_south_west(self):
        assert parse_coords("S 33.86785 W 151.20732") == pytest.approx((-33.86785, -151.20732))

    def test_lowercase_hemisphere(self):
        assert parse_coords("n 55.78750 e 12.41667") == pytest.approx((55.78750, 12.41667))

    def test_no_space_between_hemisphere_and_number(self):
        assert parse_coords("N59.99999 E12.99999") == pytest.approx((59.99999, 12.99999))

    def test_out_of_range_rejected(self):
        assert parse_coords("N 95.0 E 12.0") is None

    def test_plain_dmm_still_takes_precedence_and_is_unaffected(self):
        # Sanity check this new branch doesn't swallow genuine DMM input —
        # "N55 47.250" has a distinct minutes component, must still parse
        # as degrees=55, minutes=47.250, not as a bare decimal.
        assert parse_coords("N55 47.250 E012 25.000") == pytest.approx((55.7875, 12.416666666666666))

    def test_dmm_degree_sign_format_still_takes_precedence(self):
        assert parse_coords("N 34° 58.088' E 034° 03.281'") == pytest.approx((34.968133333333334, 34.05468333333334))


# ── Issue #765: tighter separators, more minute/seconds marks ────────────────

DMS_EXPECTED = (55.7875, 12.0 + 25 / 60)


class TestParseCoords765:
    @pytest.mark.parametrize("text", [
        "55.78750, 12.41667",
        "55.78750,12.41667",
        "55.78750 , 12.41667",
        "55.78750 12.41667",
    ])
    def test_dd_one_comma_or_whitespace(self, text):
        assert parse_coords(text) == pytest.approx((55.7875, 12.41667))

    @pytest.mark.parametrize("text", [
        "56.789 ,, ,  ,,,  12.345",
        "56.789,,12.345",
        "56.789, ,12.345",
    ])
    def test_dd_more_than_one_comma_is_rejected(self, text):
        assert parse_coords(text) is None

    @pytest.mark.parametrize("minute, second", [
        ("'", '"'),          # apostrophe, quotation mark
        ("′", "″"),          # prime, double prime
        ("’", "”"),          # what Word turns ' and " into
        ("'", "''"),         # two apostrophes for seconds
    ])
    def test_dms_minute_and_seconds_marks(self, minute, second):
        text = f"N55° 47{minute} 15.00{second} E012° 25{minute} 00.00{second}"
        assert parse_coords(text) == pytest.approx(DMS_EXPECTED)

    def test_dms_without_marks(self):
        assert parse_coords("N55 47 15 E012 25 00") == pytest.approx(DMS_EXPECTED)

    def test_dms_no_space_after_seconds_mark(self):
        assert parse_coords("N55° 47' 15.00\"E012° 25' 00.00\"") == pytest.approx(
            DMS_EXPECTED)

    @pytest.mark.parametrize("text", [
        # several quotes and spaces after the seconds (the case in #765)
        "N 56° 12' 34.567 \" \"\" E 12° 34' 56.789 \"\" \"",
        "N55° 47' 15.00\"\" E012° 25' 00.00\"",
        # the seconds mark must follow the number
        "N55° 47' 15.00 \" E012° 25' 00.00\"",
        "N55° 47' 15.00\" E012° 25' 00.00 \"",
    ])
    def test_dms_stray_seconds_marks_are_rejected(self, text):
        assert parse_coords(text) is None

    def test_dmm_degree_format_takes_word_apostrophe(self):
        assert parse_coords("N 34° 58.088’ E 034° 03.281’") == pytest.approx(
            parse_coords("N 34° 58.088' E 034° 03.281'"))


# ── Issue #767: hemisphere letter before or after the value ──────────────────

PERTH = (-32.371267, 115.827467)


class TestParseCoords767:
    @pytest.mark.parametrize("text", [
        "32.371267S 115.827467E",
        "32.371267 S 115.827467 E",
        "32.371267s 115.827467e",
        "32° 22.276 S 115° 49.648 E",
        "32°22.276'S 115°49.648'E",
        "32 22 16.56S 115 49 38.88E",
        "32° 22' 16.56\" S 115° 49' 38.88\" E",
    ])
    def test_trailing_hemisphere(self, text):
        # The three examples from GSAK's help page, plus variants.
        assert parse_coords(text) == pytest.approx(PERTH, abs=1e-5)

    @pytest.mark.parametrize("text", [
        "S 35.123° W 86.543°",
        "35.123° S 86.543° W",
        "S 35.123° 86.543° W",
        "35.123° S W 86.543°",
        "35.123° S, 86.543° W",
    ])
    def test_letter_before_or_after_each_coordinate(self, text):
        assert parse_coords(text) == pytest.approx((-35.123, -86.543))

    def test_mixed_placement_in_dmm(self):
        assert parse_coords("N55 47.250 012 25.000E") == pytest.approx(
            (55.7875, 12.0 + 25 / 60))

    @pytest.mark.parametrize("text", [
        # a hemisphere letter both before and after
        "N 35° 46.560 S E 86° 24.808",
        "N 35° 46.560 S E W 86° 24.808 E",
        "N 35.123 S E 86.543",
        # a minus sign together with a hemisphere letter
        "S 35.123° W -86.543°",
        "S 35.123° -86.543° W",
        "S -35.123° W -86.543°",
        "N -55 47.250 E012 25.000",
        # latitude letter for longitude and vice versa
        "N 35° 46.560 E 86° 24.808 W",
        "E 35.123 N 86.543",
        # no hemisphere letter at all
        "55 47.250 12 25.000",
    ])
    def test_ambiguous_hemispheres_are_rejected(self, text):
        assert parse_coords(text) is None

    def test_numbers_must_be_separated(self):
        # Without a letter, comma or space between them, the two numbers
        # can't be told apart.
        assert parse_coords("N 32.5115.8 E") is None

    def test_trailing_hemisphere_dd_is_not_misread_as_dmm(self):
        # The #751 trap, with the letter after the value.
        assert parse_coords("59.99999N 12.99999E") == pytest.approx(
            (59.99999, 12.99999))

    @pytest.mark.parametrize("text", [
        "91.0S 10.0E",
        "45° 60.000 N 10° 00.000 E",
        "45 00 60.0N 10 00 00.0E",
    ])
    def test_trailing_hemisphere_range_checks(self, text):
        assert parse_coords(text) is None
