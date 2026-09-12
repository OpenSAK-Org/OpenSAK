# tests/unit-tests/test_garmin.py — Garmin GPX/LOC/GGZ generation/export (no device needed).

import io
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import platform
import pytest

from opensak.gps.garmin import (
    GARMIN_GGZ_SUBPATH,
    GARMIN_GPX_SUBPATH,
    DeleteResult,
    ExportResult,
    _cache_symbol,
    _custom_wp_symbol,
    _effective_coords,
    _find_mtp_garmin_root,
    _find_mtp_ggz_dir,
    _find_mtp_gpx_dir,
    _is_garmin,
    _macos_volumes,
    cancel_mtp_transfer,
    debug_scan,
    delete_gpx_files,
    export_ggz_to_device,
    export_to_device,
    export_to_file,
    find_garmin_devices,
    generate_ggz,
    generate_gpx,
    generate_loc,
    get_garmin_ggz_path,
    get_garmin_gpx_path,
    is_mtp_device,
)


# ── Sprog ─────────────────────────────────────────────────────────────────────
# ExportResult/DeleteResult.__str__ er oversat via tr() (issue #675). Testene
# nedenfor tjekker den byggede tekst, så vi låser eksplicit til engelsk her,
# uafhængigt af hvilket sprog andre testfiler måtte have indlæst globalt
# (fx test_app.py, test_lang_modules.py m.fl. kalder alle load_language()).
@pytest.fixture(autouse=True)
def _fixed_language():
    from opensak.lang import load_language
    load_language("en")
    yield


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cache(
    gc_code="GC12345",
    name="Test Cache",
    cache_type="Traditional Cache",
    latitude=55.6761,
    longitude=12.5683,
    difficulty=2.0,
    terrain=3.0,
    placed_by="Owner",
    available=True,
    archived=False,
    country="Denmark",
    state=None,
    encoded_hints=None,
    hidden_date=None,
    logs=None,
    user_note=None,
    cache_id=1,
    container="Regular",
    short_description=None,
    short_desc_html=False,
    long_description=None,
    long_desc_html=False,
    attributes=None,
    gc_cache_id=None,
    owner_name=None,
    owner_id=None,
    waypoints=None,
    found=False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=cache_id,
        gc_code=gc_code,
        gc_cache_id=gc_cache_id,
        name=name,
        cache_type=cache_type,
        latitude=latitude,
        longitude=longitude,
        difficulty=difficulty,
        terrain=terrain,
        placed_by=placed_by,
        owner_name=owner_name,
        owner_id=owner_id,
        available=available,
        archived=archived,
        country=country,
        state=state,
        encoded_hints=encoded_hints,
        hidden_date=hidden_date,
        logs=logs or [],
        user_note=user_note,
        container=container,
        short_description=short_description,
        short_desc_html=short_desc_html,
        long_description=long_description,
        long_desc_html=long_desc_html,
        attributes=attributes or [],
        waypoints=waypoints or [],
        found=found,
    )


def _child_wpt(
    prefix="01",
    wp_type="Reference Point",
    name="Juvenesvegen",
    description="Juvenesvegen",
    comment="Leave Fv98. Drive up here.",
    latitude=59.891617,
    longitude=9.355417,
    url=None,
    wp_date=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        prefix=prefix,
        wp_type=wp_type,
        name=name,
        description=description,
        comment=comment,
        latitude=latitude,
        longitude=longitude,
        url=url,
        wp_date=wp_date,
    )


def _attribute(attribute_id=32, name="Bicycles", is_on=True):
    return SimpleNamespace(
        attribute_id=attribute_id,
        name=name,
        is_on=is_on,
    )


def _log(log_id="1", log_type="Found it", finder="Tester", finder_id=None, text="TFTC", log_date=None):
    return SimpleNamespace(
        log_id=log_id,
        log_type=log_type,
        finder=finder,
        finder_id=finder_id,
        text=text,
        log_date=log_date or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _note(is_corrected=True, corrected_lat=55.0, corrected_lon=12.0, note=None):
    return SimpleNamespace(
        is_corrected=is_corrected,
        corrected_lat=corrected_lat,
        corrected_lon=corrected_lon,
        note=note,
    )


# ── _cache_symbol ─────────────────────────────────────────────────────────────

class TestCacheSymbol:
    def test_traditional(self):
        assert _cache_symbol("Traditional Cache") == "Geocache"

    def test_multi(self):
        assert _cache_symbol("Multi-cache") == "Geocache"

    def test_mystery(self):
        assert _cache_symbol("Unknown Cache") == "Geocache"

    def test_earthcache(self):
        assert _cache_symbol("Earthcache") == "Geocache"

    def test_lab_cache_gets_distinct_symbol(self):
        # Issue #660: Lab Cache stages get a distinct icon from regular
        # geocaches, purely cosmetic — content export is unaffected.
        assert _cache_symbol("Lab Cache") == "Flag, Blue"
        assert _cache_symbol("Lab Cache") != _cache_symbol("Traditional Cache")

    def test_unknown_type_falls_back(self):
        assert _cache_symbol("Nonexistent Type") == "Geocache"

    def test_empty_string_falls_back(self):
        assert _cache_symbol("") == "Geocache"

    def test_not_found_defaults_to_plain_geocache(self):
        # found defaults to False — existing callers/tests are unaffected.
        assert _cache_symbol("Traditional Cache") == "Geocache"
        assert _cache_symbol("Traditional Cache", found=False) == "Geocache"

    def test_found_traditional_gets_found_symbol(self):
        # Issue #766: found status must be signalled via <sym>, matching the
        # Groundspeak/GC.com Pocket Query convention GSAK and Garmin devices
        # read found status from.
        assert _cache_symbol("Traditional Cache", found=True) == "Geocache Found"

    def test_found_other_cache_types_get_found_symbol(self):
        for cache_type in (
            "Multi-cache", "Unknown Cache", "Letterbox Hybrid",
            "Wherigo Cache", "Event Cache", "Earthcache", "Virtual Cache",
        ):
            assert _cache_symbol(cache_type, found=True) == "Geocache Found"

    def test_found_lab_cache_keeps_distinct_symbol(self):
        # Lab Caches have no found-symbol in the Garmin convention — found
        # status doesn't change their (already distinct) icon.
        assert _cache_symbol("Lab Cache", found=True) == "Flag, Blue"

    def test_found_unknown_type_falls_back_to_found_geocache(self):
        assert _cache_symbol("Nonexistent Type", found=True) == "Geocache Found"


class TestCustomWpSymbol:
    """Direct unit tests for _custom_wp_symbol() (issue #660)."""

    def test_parking_area(self):
        assert _custom_wp_symbol("Parking Area") == "Parking Area"

    def test_trailhead(self):
        assert _custom_wp_symbol("Trailhead") == "Trail Head"

    def test_hotel_poi(self):
        assert _custom_wp_symbol("Hotel/POI") == "Lodging"

    def test_reference_point(self):
        assert _custom_wp_symbol("Reference Point") == "Flag, Green"

    def test_stage(self):
        assert _custom_wp_symbol("Stage") == "Flag, Blue"

    def test_final_location(self):
        assert _custom_wp_symbol("Final Location") == "Flag, Red"

    def test_waypoint(self):
        assert _custom_wp_symbol("Waypoint") == "Waypoint"

    def test_custom(self):
        assert _custom_wp_symbol("Custom") == "Waypoint"

    def test_unknown_type_falls_back_to_waypoint(self):
        assert _custom_wp_symbol("Something Else") == "Waypoint"

    def test_empty_string_falls_back_to_waypoint(self):
        assert _custom_wp_symbol("") == "Waypoint"


# ── _effective_coords ─────────────────────────────────────────────────────────

class TestEffectiveCoords:
    def test_no_user_note_returns_original(self):
        c = _cache(latitude=55.0, longitude=12.0, user_note=None)
        assert _effective_coords(c) == (55.0, 12.0)

    def test_uncorrected_note_returns_original(self):
        c = _cache(latitude=55.0, longitude=12.0, user_note=_note(is_corrected=False))
        assert _effective_coords(c) == (55.0, 12.0)

    def test_corrected_note_returns_corrected(self):
        c = _cache(latitude=55.0, longitude=12.0, user_note=_note(is_corrected=True, corrected_lat=56.0, corrected_lon=13.0))
        assert _effective_coords(c) == (56.0, 13.0)

    def test_corrected_note_with_none_lat_falls_back(self):
        note = _note(is_corrected=True, corrected_lat=None, corrected_lon=12.0)
        c = _cache(latitude=55.0, longitude=12.0, user_note=note)
        assert _effective_coords(c) == (55.0, 12.0)

    def test_corrected_note_with_none_lon_falls_back(self):
        note = _note(is_corrected=True, corrected_lat=55.0, corrected_lon=None)
        c = _cache(latitude=55.0, longitude=12.0, user_note=note)
        assert _effective_coords(c) == (55.0, 12.0)


# ── generate_gpx ──────────────────────────────────────────────────────────────

class TestGenerateGpx:
    def test_returns_string(self):
        result = generate_gpx([_cache()])
        assert isinstance(result, str)

    def test_valid_xml(self):
        result = generate_gpx([_cache()])
        root = ET.fromstring(result.split("\n", 1)[1])  # skip XML declaration
        assert root.tag.endswith("gpx")

    def test_xml_declaration_present(self):
        result = generate_gpx([_cache()])
        assert result.startswith('<?xml version="1.0"')

    def test_gc_code_in_output(self):
        result = generate_gpx([_cache(gc_code="GC99999")])
        assert "GC99999" in result

    def test_cache_name_in_output(self):
        result = generate_gpx([_cache(name="My Favourite Cache")])
        assert "My Favourite Cache" in result

    def test_coordinates_in_wpt_attributes(self):
        result = generate_gpx([_cache(latitude=55.1234, longitude=12.5678)])
        assert '55.123400' in result
        assert '12.567800' in result

    def test_creator_is_opensak(self):
        result = generate_gpx([_cache()])
        assert 'creator="OpenSAK"' in result

    def test_uses_gpx_1_0_namespace(self):
        # Regression test for #656 root cause: switched from GPX 1.1 to
        # GPX 1.0 to match GSAK's export format, which Garmin device
        # firmware's geocache parser is built around.
        result = generate_gpx([_cache()])
        assert 'version="1.0"' in result
        assert "http://www.topografix.com/GPX/1/0" in result
        assert "GPX/1/1" not in result

    def test_no_extensions_wrapper(self):
        # Regression test for #656 root cause: a device tester confirmed
        # hint displayed correctly but description/logs did not, on-device,
        # with a content-complete but GPX-1.1-style export. GSAK's export
        # (proven to work on the same device) puts groundspeak:cache
        # directly under <wpt>, with no <extensions> wrapper at all.
        result = generate_gpx([_cache()])
        assert "<extensions>" not in result
        assert "groundspeak:cache" in result

    def test_groundspeak_cache_namespace_declared_locally(self):
        # Matches GSAK's exact convention: xmlns:groundspeak is declared on
        # the <groundspeak:cache> element itself, not at the <gpx> root.
        result = generate_gpx([_cache()])
        assert 'xmlns:groundspeak="http://www.groundspeak.com/cache/1/0/1"' in result
        # Root <gpx> tag itself should not carry the groundspeak namespace.
        gpx_tag_end = result.index(">")
        assert "groundspeak" not in result[:gpx_tag_end]

    def test_custom_filename_in_metadata(self):
        result = generate_gpx([_cache()], filename="my_export")
        assert "my_export" in result

    def test_not_found_cache_gets_plain_sym(self):
        # Issue #766: default (not found) behaviour is unchanged.
        result = generate_gpx([_cache(found=False)])
        assert "<sym>Geocache</sym>" in result
        assert "Geocache Found" not in result

    def test_found_cache_gets_found_sym(self):
        # Issue #766: found status must round-trip through <sym>, matching
        # the Groundspeak/GC.com Pocket Query convention — otherwise no
        # downstream tool (GSAK, Garmin devices, or OpenSAK itself on
        # re-import) can detect it, even though the log list is present.
        result = generate_gpx([_cache(found=True)])
        assert "<sym>Geocache Found</sym>" in result

    def test_found_missing_attribute_defaults_to_not_found(self):
        # cache.found may be absent entirely on older/partial objects —
        # generate_gpx must not raise, and must fall back to "Geocache".
        cache = _cache()
        del cache.found
        result = generate_gpx([cache])
        assert "<sym>Geocache</sym>" in result

    def test_country_present_when_set(self):
        result = generate_gpx([_cache(country="Denmark")])
        assert "Denmark" in result

    def test_state_present_when_set(self):
        # Regression test for #656 follow-up: state was not exported at all,
        # confirmed missing via a direct GGZ diff against GSAK's export of
        # the same cache.
        result = generate_gpx([_cache(state="Region Sjælland")])
        assert "groundspeak:state" in result
        assert "Region Sjælland" in result

    def test_state_absent_when_not_set(self):
        result = generate_gpx([_cache(state=None)])
        assert "groundspeak:state" not in result

    def test_log_text_not_truncated_at_500_chars(self):
        # Regression test for #656 follow-up: log text was silently cut off
        # at 500 chars, confirmed via a direct GGZ diff against GSAK's
        # export of the same cache (GSAK had a 3616-char log, OSAK's
        # matching export was truncated to exactly 500).
        long_text = "A" * 2000
        lg = _log(text=long_text)
        result = generate_gpx([_cache(logs=[lg])])
        assert long_text in result

    def test_groundspeak_cache_element_order_matches_schema(self):
        # Regression test for #656 follow-up: element order inside
        # groundspeak:cache must follow the official cache.xsd sequence
        # (name, placed_by, type, container, attributes, difficulty,
        # terrain, country, state, short_description, long_description,
        # encoded_hints, logs). A device tester confirmed hints displayed
        # correctly on a GPSMAP64S but description fell back to a generic
        # "GC-code + coordinates" placeholder and logs didn't show at all
        # — consistent with a device parser that walks the block
        # sequentially and gives up once the expected order breaks down
        # (encoded_hints was previously written before the descriptions,
        # and attributes after long_description, both out of sequence).
        import re
        attrs = [_attribute()]
        lg = _log()
        result = generate_gpx([_cache(
            attributes=attrs, logs=[lg], country="Denmark", state="Region Sjælland",
            short_description="short", long_description="long",
            encoded_hints="hint",
        )])
        tags = re.findall(r"<groundspeak:(\w+)", result)
        expected_order = [
            "cache", "name", "placed_by", "type", "container",
            "attributes", "attribute",
            "difficulty", "terrain", "country", "state",
            "short_description", "long_description", "encoded_hints",
            "logs", "log",
        ]
        # Only check the leading tags we control the order of; logs/date/
        # etc. inside the <log> element aren't schema-order-sensitive here.
        assert tags[:len(expected_order)] == expected_order

    def test_hints_present_when_set(self):
        result = generate_gpx([_cache(encoded_hints="Under a rock")])
        assert "Under a rock" in result

    def test_custom_waypoint_has_no_groundspeak_cache_block(self):
        # Regression test for #660: Custom Waypoints (Hotel/POI, Parking
        # Area, etc. — issue #141) were previously exported wrapped in a
        # full groundspeak:cache block, showing up on Garmin devices as a
        # "fake geocache" with empty D/T stars and Size: (Not Chosen).
        result = generate_gpx([_cache(cache_type="Parking Area")])
        assert "groundspeak:cache" not in result

    def test_custom_waypoint_still_has_name_and_desc(self):
        result = generate_gpx([_cache(
            cache_type="Hotel/POI", name="Hotel Example", gc_code="GC00HOTEL",
        )])
        assert "GC00HOTEL" in result
        assert "Hotel Example" in result

    def test_custom_waypoint_symbol_parking_area(self):
        result = generate_gpx([_cache(cache_type="Parking Area")])
        assert "<sym>Parking Area</sym>" in result

    def test_custom_waypoint_symbol_hotel_poi(self):
        result = generate_gpx([_cache(cache_type="Hotel/POI")])
        assert "<sym>Lodging</sym>" in result

    def test_custom_waypoint_symbol_trailhead(self):
        result = generate_gpx([_cache(cache_type="Trailhead")])
        assert "<sym>Trail Head</sym>" in result

    def test_custom_waypoint_unmapped_type_falls_back_to_waypoint_symbol(self):
        result = generate_gpx([_cache(cache_type="Custom")])
        assert "<sym>Waypoint</sym>" in result

    def test_custom_waypoint_type_element_is_plain_not_geocache_prefixed(self):
        # Regular caches get "Geocache|<type>" in <type>; custom waypoints
        # should not carry the misleading "Geocache|" prefix.
        result = generate_gpx([_cache(cache_type="Parking Area")])
        assert "<type>Parking Area</type>" in result
        assert "Geocache|Parking Area" not in result

    def test_regular_cache_still_gets_groundspeak_cache_block(self):
        # Sanity check: the #660 branch must not affect normal geocaches.
        result = generate_gpx([_cache(cache_type="Traditional Cache")])
        assert "groundspeak:cache" in result
        assert "<sym>Geocache</sym>" in result

    def test_lab_cache_gets_distinct_icon_but_keeps_full_content(self):
        # Regression test for #660: Adventure Lab stages already displayed
        # correctly as full geocaches on a GPSMAP 64s (description, D/T,
        # hint all shown) — this only changes the icon so they're visually
        # distinguishable from regular geocaches, without losing content.
        result = generate_gpx([_cache(
            cache_type="Lab Cache",
            long_description="Lab stage description",
            encoded_hints="Lab hint",
        )])
        assert "<sym>Flag, Blue</sym>" in result
        assert "groundspeak:cache" in result
        assert "Lab stage description" in result
        assert "Lab hint" in result

    def test_short_description_present_when_set(self):
        # Regression test for #656: short_description was not exported at all.
        result = generate_gpx([_cache(short_description="A quick teaser")])
        assert "A quick teaser" in result
        assert 'groundspeak:short_description html="False"' in result

    def test_short_description_html_flag_reflected(self):
        result = generate_gpx([_cache(
            short_description="<b>Teaser</b>", short_desc_html=True,
        )])
        assert 'groundspeak:short_description html="True"' in result

    def test_short_description_absent_when_not_set(self):
        result = generate_gpx([_cache(short_description=None)])
        assert "groundspeak:short_description" not in result

    def test_long_description_present_when_set(self):
        # Regression test for #656: long_description was not exported at all,
        # which several users confirmed was missing from OpenSAK's GPX/GGZ
        # output compared to GC.com/GSAK exports of the same cache.
        result = generate_gpx([_cache(long_description="The full cache story goes here.")])
        assert "The full cache story goes here." in result
        assert 'groundspeak:long_description html="False"' in result

    def test_long_description_html_flag_reflected(self):
        result = generate_gpx([_cache(
            long_description="<p>Full story</p>", long_desc_html=True,
        )])
        assert 'groundspeak:long_description html="True"' in result

    def test_long_description_absent_when_not_set(self):
        result = generate_gpx([_cache(long_description=None)])
        assert "groundspeak:long_description" not in result

    def test_attributes_present_when_set(self):
        # Regression test for #656: attributes were not exported at all.
        attrs = [_attribute(attribute_id=32, name="Bicycles", is_on=True)]
        result = generate_gpx([_cache(attributes=attrs)])
        assert "groundspeak:attributes" in result
        assert 'groundspeak:attribute id="32" inc="1"' in result
        assert "Bicycles" in result

    def test_attribute_inc_zero_when_off(self):
        attrs = [_attribute(attribute_id=7, name="No dogs", is_on=False)]
        result = generate_gpx([_cache(attributes=attrs)])
        assert 'groundspeak:attribute id="7" inc="0"' in result

    def test_multiple_attributes_all_present(self):
        attrs = [
            _attribute(attribute_id=32, name="Bicycles", is_on=True),
            _attribute(attribute_id=7, name="No dogs", is_on=False),
        ]
        result = generate_gpx([_cache(attributes=attrs)])
        assert "Bicycles" in result
        assert "No dogs" in result

    def test_attributes_absent_when_empty(self):
        result = generate_gpx([_cache(attributes=[])])
        assert "groundspeak:attributes" not in result

    def test_groundspeak_element_order_matches_cache_xsd(self):
        # Regression test for #656 follow-up: elements inside groundspeak:cache
        # must appear in the exact order the official cache.xsd sequence
        # requires (name, placed_by, type, container, attributes, difficulty,
        # terrain, country, state, short_description, long_description,
        # encoded_hints, logs). Confirmed on real hardware (GPSMAP64S) that
        # violating this order — encoded_hints was previously written before
        # the descriptions, and attributes after them — caused hint to
        # display correctly while description and logs silently did not,
        # consistent with a strict sequential parser giving up partway
        # through the block once elements appeared out of sequence.
        import re
        attrs = [_attribute(attribute_id=32, name="Bicycles", is_on=True)]
        lg = _log(text="Great cache!")
        result = generate_gpx([_cache(
            country="Denmark", state="Region Sjælland",
            encoded_hints="Under a rock",
            short_description="Short", long_description="Long",
            attributes=attrs, logs=[lg],
        )])
        tags = re.findall(r"<groundspeak:(\w+)", result)
        expected_order = [
            "cache", "name", "placed_by", "type", "container",
            "attributes", "attribute",
            "difficulty", "terrain", "country", "state",
            "short_description", "long_description", "encoded_hints",
            "logs", "log",
        ]
        assert tags[:len(expected_order)] == expected_order

    def test_cache_with_none_coords_skipped(self):
        c = _cache()
        c.latitude = None
        result = generate_gpx([c])
        assert "GC12345" not in result

    def test_log_included_in_output(self):
        lg = _log(finder="TestFinder", text="Great cache!")
        result = generate_gpx([_cache(logs=[lg])])
        assert "TestFinder" in result
        assert "Great cache!" in result

    def test_all_logs_exported_not_just_five(self):
        # Regression test for #656: logs were previously hardcoded to the
        # last 5 only, unlike GC.com/GSAK which include full log history.
        # Allyn56's controlled comparison on issue #656 confirmed OpenSAK's
        # export was truncating logs where GC.com/GSAK exports were not.
        logs = [
            _log(log_id=str(i), finder=f"Finder{i}", text=f"Log number {i}",
                 log_date=datetime(2026, 1, i, tzinfo=timezone.utc))
            for i in range(1, 9)  # 8 logs — more than the old hardcoded cap of 5
        ]
        result = generate_gpx([_cache(logs=logs)])
        for i in range(1, 9):
            assert f"Finder{i}" in result, f"Log {i} missing — logs still being truncated"

    def test_logs_with_mixed_none_and_set_dates_does_not_crash(self):
        # Regression test for #348: sorting logs where some have log_date=None
        # and others have a real datetime used to raise
        # "'<' not supported between instances of 'datetime.datetime' and 'int'"
        # because None fell back to int 0 instead of a comparable datetime.
        dated = SimpleNamespace(
            log_id="1", log_type="Found it", finder="Alice", finder_id=None, text="TFTC",
            log_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        undated = SimpleNamespace(
            log_id="2", log_type="Write note", finder="Bob", finder_id=None, text="No date",
            log_date=None,
        )
        result = generate_gpx([_cache(logs=[undated, dated])])
        # Most recent (dated) log should be sorted first
        assert result.index("Alice") < result.index("Bob")

    def test_corrected_coords_used_in_wpt(self):
        note = _note(is_corrected=True, corrected_lat=60.0, corrected_lon=20.0)
        c = _cache(latitude=55.0, longitude=12.0, user_note=note)
        result = generate_gpx([c])
        assert '60.000000' in result
        assert '20.000000' in result

    def test_corrected_coords_store_original_in_comment(self):
        note = _note(is_corrected=True, corrected_lat=60.0, corrected_lon=20.0)
        c = _cache(latitude=55.0, longitude=12.0, user_note=note)
        result = generate_gpx([c])
        assert "Original" in result
        assert "55.000000" in result

    def test_user_note_emitted_as_gsak_element(self):
        n = _note(is_corrected=False, note="My personal note")
        result = generate_gpx([_cache(user_note=n)])
        assert "gsak:UserNote" in result
        assert "My personal note" in result

    def test_gsak_namespace_declared_when_note_present(self):
        n = _note(is_corrected=False, note="A note")
        result = generate_gpx([_cache(user_note=n)])
        assert "http://www.gsak.net/xmlv1/6" in result

    def test_no_gsak_extension_when_note_absent(self):
        result = generate_gpx([_cache(user_note=None)])
        assert "gsak:wptExtension" not in result

    def test_no_gsak_extension_when_note_is_empty_string(self):
        n = _note(is_corrected=False, note="")
        result = generate_gpx([_cache(user_note=n)])
        assert "gsak:wptExtension" not in result

    def test_empty_cache_list_produces_valid_gpx(self):
        result = generate_gpx([])
        root = ET.fromstring(result.split("\n", 1)[1])
        assert root.tag.endswith("gpx")

    def test_multiple_caches(self):
        caches = [
            _cache(gc_code="GC00001", cache_id=1),
            _cache(gc_code="GC00002", cache_id=2),
        ]
        result = generate_gpx(caches)
        assert "GC00001" in result
        assert "GC00002" in result

    def test_url_contains_gc_code(self):
        result = generate_gpx([_cache(gc_code="GC12345")])
        assert "coord.info/GC12345" in result

    def test_hidden_date_included(self):
        dt = datetime(2024, 6, 1, tzinfo=timezone.utc)
        result = generate_gpx([_cache(hidden_date=dt)])
        assert "2024-06-01" in result


# ── Child waypoint export (issue #753) ──────────────────────────────────────
# generate_gpx() used to only ever emit the ONE <wpt> for the cache's own
# listing — cache.waypoints (parking areas, trailheads, stages, final
# locations, etc.) were silently dropped from GPX/GGZ export and Send-to-GPS
# entirely. Verified end-to-end against the reporter's real GC1YB0C.gpx /
# opensak_GC1YB0C.gpx pair (2 <wpt> in the original, 1 in OpenSAK's export).

class TestChildWaypointExport:
    def test_no_waypoints_unaffected(self):
        # Backward compatibility: existing callers/tests build _cache()
        # without a waypoints= arg at all (defaults to []) — must still
        # produce exactly the one cache <wpt>, same as before this fix.
        result = generate_gpx([_cache()])
        assert result.count("<wpt ") == 1

    def test_single_child_waypoint_adds_second_wpt(self):
        result = generate_gpx([_cache(waypoints=[_child_wpt()])])
        assert result.count("<wpt ") == 2

    def test_multiple_child_waypoints_all_exported(self):
        wps = [
            _child_wpt(prefix="PK", wp_type="Parking Area", latitude=55.0, longitude=12.0),
            _child_wpt(prefix="TH", wp_type="Trailhead", latitude=55.1, longitude=12.1),
            _child_wpt(prefix="FN", wp_type="Final Location", latitude=55.2, longitude=12.2),
        ]
        result = generate_gpx([_cache(waypoints=wps)])
        assert result.count("<wpt ") == 4  # 1 cache + 3 children

    def test_child_waypoint_coordinates(self):
        result = generate_gpx([_cache(waypoints=[
            _child_wpt(latitude=59.891617, longitude=9.355417),
        ])])
        assert 'lat="59.891617"' in result
        assert 'lon="9.355417"' in result

    def test_child_waypoint_name_reconstructed_from_prefix_and_gc_code(self):
        # Regression for the exact reporter scenario: gc_code "GC1YB0C",
        # child prefix "01" -> reconstructed name "011YB0C", byte-identical
        # to the original geocaching.com GPX in this case.
        result = generate_gpx([_cache(
            gc_code="GC1YB0C",
            waypoints=[_child_wpt(prefix="01")],
        )])
        assert "<name>011YB0C</name>" in result

    def test_child_waypoint_missing_prefix_falls_back_to_wp(self):
        result = generate_gpx([_cache(
            gc_code="GC1YB0C",
            waypoints=[_child_wpt(prefix=None)],
        )])
        assert "<name>WP1YB0C</name>" in result

    def test_child_waypoint_comment_and_description(self):
        result = generate_gpx([_cache(waypoints=[_child_wpt(
            comment="Leave Fv98. Drive up here.",
            description="Juvenesvegen",
        )])])
        assert "<cmt>Leave Fv98. Drive up here.</cmt>" in result
        assert "<desc>Juvenesvegen</desc>" in result

    def test_child_waypoint_sym_and_type_from_wp_type(self):
        result = generate_gpx([_cache(waypoints=[
            _child_wpt(wp_type="Reference Point"),
        ])])
        assert "<sym>Reference Point</sym>" in result
        assert "<type>Waypoint|Reference Point</type>" in result

    def test_child_waypoint_missing_wp_type_falls_back_to_waypoint(self):
        result = generate_gpx([_cache(waypoints=[_child_wpt(wp_type=None)])])
        assert "<sym>Waypoint</sym>" in result
        assert "<type>Waypoint|Waypoint</type>" in result

    def test_child_waypoint_url_included_when_present(self):
        result = generate_gpx([_cache(waypoints=[
            _child_wpt(url="https://www.geocaching.com/seek/wpt.aspx?WID=abc123"),
        ])])
        assert "<url>https://www.geocaching.com/seek/wpt.aspx?WID=abc123</url>" in result

    def test_child_waypoint_url_omitted_when_absent(self):
        # GPX-sourced waypoints don't carry a URL (only GSAK-imported ones
        # might) — must not emit an empty <url/> element.
        result = generate_gpx([_cache(waypoints=[_child_wpt(url=None)])])
        assert "<url></url>" not in result
        assert "<url />" not in result

    def test_child_waypoint_urlname_falls_back_to_reconstructed_name(self):
        result = generate_gpx([_cache(
            gc_code="GC1YB0C",
            waypoints=[_child_wpt(prefix="01", name=None, description=None)],
        )])
        assert "<urlname>011YB0C</urlname>" in result

    def test_child_waypoint_time_included_when_wp_date_present(self):
        dt = datetime(2009, 10, 12, 11, 56, 59, tzinfo=timezone.utc)
        result = generate_gpx([_cache(waypoints=[_child_wpt(wp_date=dt)])])
        assert "<time>2009-10-12" in result

    def test_child_waypoint_without_coordinates_skipped(self):
        result = generate_gpx([_cache(waypoints=[
            _child_wpt(latitude=None, longitude=None),
        ])])
        assert result.count("<wpt ") == 1  # only the parent cache's own

    def test_child_waypoint_no_groundspeak_cache_block(self):
        # Child waypoints are plain GPX waypoints, not fake geocaches —
        # same reasoning as custom-waypoint-type caches (#660).
        result = generate_gpx([_cache(waypoints=[_child_wpt()])])
        assert result.count("<groundspeak:cache") == 1  # only the parent

    def test_child_waypoints_independent_per_cache(self):
        c1 = _cache(gc_code="GC00001", waypoints=[_child_wpt(prefix="PK")])
        c2 = _cache(gc_code="GC00002", waypoints=[])
        result = generate_gpx([c1, c2])
        assert result.count("<wpt ") == 3  # 2 caches + 1 child on c1 only

    def test_real_world_gc1yb0c_reference_point_round_trip(self):
        # Exact values from the reporter's original GC1YB0C.gpx <wpt> for
        # the Reference Point child waypoint.
        result = generate_gpx([_cache(
            gc_code="GC1YB0C",
            waypoints=[_child_wpt(
                prefix="01", wp_type="Reference Point", name="Juvenesvegen",
                description="Juvenesvegen", comment="Leave Fv98. Drive up here.",
                latitude=59.891617, longitude=9.355417,
            )],
        )])
        assert result.count("<wpt ") == 2
        assert 'lat="59.891617"' in result
        assert 'lon="9.355417"' in result
        assert "<name>011YB0C</name>" in result
        assert "<cmt>Leave Fv98. Drive up here.</cmt>" in result
        assert "<desc>Juvenesvegen</desc>" in result
        assert "<urlname>Juvenesvegen</urlname>" in result
        assert "<sym>Reference Point</sym>" in result
        assert "<type>Waypoint|Reference Point</type>" in result


# ── export_to_file ────────────────────────────────────────────────────────────

class TestExportToFile:
    def test_creates_file(self, tmp_path):
        output = tmp_path / "export.gpx"
        result = export_to_file([_cache()], output)
        assert output.exists()
        assert result.success

    def test_file_contains_gc_code(self, tmp_path):
        output = tmp_path / "out.gpx"
        export_to_file([_cache(gc_code="GCTEST1")], output)
        assert "GCTEST1" in output.read_text()

    def test_cache_count_reported(self, tmp_path):
        output = tmp_path / "out.gpx"
        result = export_to_file([_cache(), _cache(gc_code="GC99999", cache_id=2)], output)
        assert result.cache_count == 2

    def test_file_path_returned(self, tmp_path):
        output = tmp_path / "myfile.gpx"
        result = export_to_file([_cache()], output)
        assert result.file_path == output

    def test_creates_parent_dirs(self, tmp_path):
        output = tmp_path / "nested" / "dir" / "export.gpx"
        result = export_to_file([_cache()], output)
        assert output.exists()
        assert result.success

    def test_no_error_on_success(self, tmp_path):
        output = tmp_path / "out.gpx"
        result = export_to_file([_cache()], output)
        assert result.error is None

    def test_cache_with_none_lat_not_counted(self, tmp_path):
        output = tmp_path / "out.gpx"
        c_valid = _cache(gc_code="GC00001", cache_id=1)
        c_null = _cache(gc_code="GC00002", cache_id=2)
        c_null.latitude = None
        result = export_to_file([c_valid, c_null], output)
        assert result.cache_count == 1


# ── export_to_device ──────────────────────────────────────────────────────────

class TestExportToDevice:
    def test_creates_gpx_in_garmin_subdir(self, tmp_path):
        device_root = tmp_path / "garmin_device"
        result = export_to_device([_cache()], device_root, filename="test")
        expected = device_root / GARMIN_GPX_SUBPATH / "test.gpx"
        assert expected.exists()
        assert result.success

    def test_device_path_recorded(self, tmp_path):
        device_root = tmp_path / "device"
        result = export_to_device([_cache()], device_root)
        assert result.device == device_root

    def test_file_path_recorded(self, tmp_path):
        device_root = tmp_path / "device"
        result = export_to_device([_cache()], device_root, filename="mycaches")
        expected = device_root / GARMIN_GPX_SUBPATH / "mycaches.gpx"
        assert result.file_path == expected


class TestExportGgzToDevice:
    # Regression tests for #348: GGZ files must land in Garmin/GGZ, not
    # Garmin/GPX (the folder GSAK's GarminExport macro and Garmin devices
    # themselves expect .ggz files to live in).
    def test_creates_ggz_in_garmin_ggz_subdir(self, tmp_path):
        device_root = tmp_path / "garmin_device"
        result = export_ggz_to_device([_cache()], device_root, filename="test")
        expected = device_root / GARMIN_GGZ_SUBPATH / "test.ggz"
        assert expected.exists()
        assert result.success

    def test_does_not_write_to_gpx_subdir(self, tmp_path):
        device_root = tmp_path / "garmin_device"
        export_ggz_to_device([_cache()], device_root, filename="test")
        wrong_path = device_root / GARMIN_GPX_SUBPATH / "test.ggz"
        assert not wrong_path.exists()

    def test_file_path_recorded(self, tmp_path):
        device_root = tmp_path / "device"
        result = export_ggz_to_device([_cache()], device_root, filename="mycaches")
        expected = get_garmin_ggz_path(device_root) / "mycaches.ggz"
        assert result.file_path == expected

    def test_device_path_recorded(self, tmp_path):
        device_root = tmp_path / "device"
        result = export_ggz_to_device([_cache()], device_root)
        assert result.device == device_root


# ── delete_gpx_files ──────────────────────────────────────────────────────────

class TestDeleteGpxFiles:
    def _setup_device(self, root: Path, filenames: list[str]) -> Path:
        gpx_dir = root / GARMIN_GPX_SUBPATH
        gpx_dir.mkdir(parents=True)
        for fn in filenames:
            (gpx_dir / fn).write_text("dummy")
        return gpx_dir

    def test_deletes_gpx_files(self, tmp_path):
        gpx_dir = self._setup_device(tmp_path, ["a.gpx", "b.gpx"])
        result = delete_gpx_files(tmp_path)
        assert result.deleted_count == 2
        assert not (gpx_dir / "a.gpx").exists()

    def test_no_files_returns_zero_deleted(self, tmp_path):
        self._setup_device(tmp_path, [])
        result = delete_gpx_files(tmp_path)
        assert result.deleted_count == 0
        assert result.success

    def test_missing_gpx_dir_returns_success(self, tmp_path):
        result = delete_gpx_files(tmp_path)
        assert result.success
        assert result.deleted_count == 0

    def test_device_path_recorded(self, tmp_path):
        result = delete_gpx_files(tmp_path)
        assert result.device == tmp_path

    def test_folder_override_deletes_from_ggz_dir(self, tmp_path):
        # Regression test for #656 follow-up (CheminerWill): the delete-old-
        # files feature previously only worked for GPX; passing an explicit
        # folder lets it also clear the GGZ directory instead.
        ggz_dir = tmp_path / GARMIN_GGZ_SUBPATH
        ggz_dir.mkdir(parents=True)
        (ggz_dir / "opensak.ggz").write_text("dummy")
        gpx_dir = self._setup_device(tmp_path, ["untouched.gpx"])

        result = delete_gpx_files(tmp_path, pattern="*.ggz", folder=ggz_dir)

        assert result.deleted_count == 1
        assert not (ggz_dir / "opensak.ggz").exists()
        assert (gpx_dir / "untouched.gpx").exists()  # GPX dir left alone


# ── _is_garmin ────────────────────────────────────────────────────────────────

class TestIsGarmin:
    def test_detects_garmindevice_xml(self, tmp_path):
        garmin_dir = tmp_path / "Garmin"
        garmin_dir.mkdir()
        (garmin_dir / "GarminDevice.xml").write_text("<device/>")
        assert _is_garmin(tmp_path) is True

    def test_detects_gpx_subdir(self, tmp_path):
        gpx_dir = tmp_path / "Garmin" / "GPX"
        gpx_dir.mkdir(parents=True)
        assert _is_garmin(tmp_path) is True

    def test_detects_is_garmin_marker(self, tmp_path):
        (tmp_path / ".is_garmin").write_text("")
        assert _is_garmin(tmp_path) is True

    def test_non_garmin_path(self, tmp_path):
        assert _is_garmin(tmp_path) is False

    def test_unreadable_marker_does_not_raise(self, tmp_path, monkeypatch):
        # A candidate like /mnt/lost+found (root-only) makes .exists() raise
        # PermissionError on Python <=3.12; detection must skip it, not crash.
        real_exists = Path.exists

        def maybe_boom(self, *args, **kwargs):
            if str(self).startswith(str(tmp_path)):
                raise PermissionError(13, "Permission denied")
            return real_exists(self, *args, **kwargs)

        monkeypatch.setattr(Path, "exists", maybe_boom)
        assert _is_garmin(tmp_path) is False


# ── get_garmin_gpx_path ───────────────────────────────────────────────────────

class TestGetGarminGpxPath:
    def test_returns_correct_subpath(self, tmp_path):
        result = get_garmin_gpx_path(tmp_path)
        assert result == tmp_path / GARMIN_GPX_SUBPATH


# ── Result dataclasses ────────────────────────────────────────────────────────

class TestDeleteResult:
    def test_success_when_no_error(self):
        r = DeleteResult()
        assert r.success is True

    def test_not_success_when_error_set(self):
        r = DeleteResult()
        r.error = "something went wrong"
        assert r.success is False

    def test_deleted_count(self):
        r = DeleteResult()
        r.deleted_files = [Path("a.gpx"), Path("b.gpx")]
        assert r.deleted_count == 2

    def test_failed_count(self):
        r = DeleteResult()
        r.failed_files = [Path("c.gpx")]
        assert r.failed_count == 1

    def test_str_no_files(self):
        r = DeleteResult()
        assert "ℹ️" in str(r)

    def test_str_with_deleted_files(self):
        r = DeleteResult()
        r.deleted_files = [Path("a.gpx")]
        assert "1" in str(r)

    def test_str_error(self):
        r = DeleteResult()
        r.error = "Access denied"
        assert "Access denied" in str(r)


class TestExportResult:
    def test_success_when_no_error(self):
        r = ExportResult()
        assert r.success is True

    def test_not_success_when_error_set(self):
        r = ExportResult()
        r.error = "write failed"
        assert r.success is False

    def test_str_success(self):
        r = ExportResult()
        r.cache_count = 5
        r.device = Path("/mnt/garmin")
        r.file_path = Path("/mnt/garmin/Garmin/GPX/opensak.gpx")
        assert "5" in str(r)
        assert "opensak.gpx" in str(r)

    def test_str_error(self):
        r = ExportResult()
        r.error = "Permission denied"
        assert "Permission denied" in str(r)


# ── generate_loc ──────────────────────────────────────────────────────────────

class TestGenerateLoc:
    def test_xml_declaration_and_root(self):
        out = generate_loc([_cache()])
        assert out.startswith('<?xml version="1.0"')
        root = ET.fromstring(out.split("\n", 1)[1])
        assert root.tag == "loc"
        assert root.get("src") == "OpenSAK"

    def test_waypoint_fields(self):
        out = generate_loc([_cache(gc_code="GCLOC1", name="LocCache",
                                   placed_by="Owner", difficulty=2.0, terrain=3.0,
                                   container="Small")])
        assert 'id="GCLOC1"' in out
        assert "coord.info/GCLOC1" in out
        assert "<container>Small</container>" in out

    def test_cdata_label_restored(self):
        out = generate_loc([_cache(name="My Cache", placed_by="Bob",
                                   difficulty=1.5, terrain=2.0)])
        assert "<![CDATA[My Cache by Bob (1.5/2)]]>" in out

    def test_container_defaults_to_unknown(self):
        out = generate_loc([_cache(container=None)])
        assert "<container>Unknown</container>" in out

    def test_corrected_coords_used(self):
        c = _cache(latitude=55.0, longitude=12.0,
                   user_note=_note(is_corrected=True, corrected_lat=60.0, corrected_lon=20.0))
        out = generate_loc([c])
        assert 'lat="60.000000"' in out
        assert 'lon="20.000000"' in out

    def test_skips_cache_without_coords(self):
        c = _cache(gc_code="GCNULL")
        c.latitude = None
        out = generate_loc([c])
        assert "GCNULL" not in out


# ── generate_ggz ──────────────────────────────────────────────────────────────

def _ggz_entries(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


class TestGenerateGgz:
    def test_returns_valid_zip(self):
        blob = generate_ggz([_cache()])
        assert isinstance(blob, bytes)
        assert zipfile.is_zipfile(io.BytesIO(blob))

    def test_contains_gpx_and_index(self):
        entries = _ggz_entries(generate_ggz([_cache()], filename="myexport"))
        assert "data/myexport.gpx" in entries
        assert "index/com/garmin/geocaches/v0/index.xml" in entries

    def test_index_lists_cache(self):
        entries = _ggz_entries(generate_ggz([_cache(gc_code="GCGGZ1", name="GgzCache")]))
        index = entries["index/com/garmin/geocaches/v0/index.xml"].decode("utf-8")
        assert "GCGGZ1" in index
        assert "GgzCache" in index
        assert "<crc>" in index

    def test_container_size_mapping(self):
        index = _ggz_entries(generate_ggz([_cache(container="Regular")]))[
            "index/com/garmin/geocaches/v0/index.xml"].decode("utf-8")
        assert "<size>4.0</size>" in index

    def test_unknown_container_omits_size(self):
        index = _ggz_entries(generate_ggz([_cache(container="Huge")]))[
            "index/com/garmin/geocaches/v0/index.xml"].decode("utf-8")
        assert "<size>" not in index

    def test_no_container_omits_size(self):
        index = _ggz_entries(generate_ggz([_cache(container=None)]))[
            "index/com/garmin/geocaches/v0/index.xml"].decode("utf-8")
        assert "<size>" not in index

    def test_found_flag_in_index(self):
        c = _cache(gc_code="GCFOUND")
        c.found = True
        index = _ggz_entries(generate_ggz([c]))[
            "index/com/garmin/geocaches/v0/index.xml"].decode("utf-8")
        assert "<found>true</found>" in index

    def test_skips_cache_without_coords(self):
        c = _cache(gc_code="GCNULL")
        c.longitude = None
        index = _ggz_entries(generate_ggz([_cache(gc_code="GCOK"), c]))[
            "index/com/garmin/geocaches/v0/index.xml"].decode("utf-8")
        assert "GCOK" in index
        assert "GCNULL" not in index

    def test_directory_entries_are_not_epoch_date(self):
        # Regression test: zipfile.ZipFile.mkdir("name") with a plain string
        # used to default date_time to (1980, 1, 1, 0, 0, 0), showing up as
        # 1979-12-31/1980-01-01 in file managers depending on timezone.
        zf = zipfile.ZipFile(io.BytesIO(generate_ggz([_cache()])))
        this_year = datetime.now().year
        for info in zf.infolist():
            assert info.date_time[0] == this_year, (
                f"{info.filename} has suspicious date {info.date_time}"
            )

    def test_files_remain_compressed(self):
        # Regression test: passing an explicit ZipInfo to writestr() without
        # setting compress_type defaults to ZIP_STORED (uncompressed).
        zf = zipfile.ZipFile(io.BytesIO(generate_ggz([_cache()], filename="c")))
        gpx_info = zf.getinfo("data/c.gpx")
        index_info = zf.getinfo("index/com/garmin/geocaches/v0/index.xml")
        assert gpx_info.compress_type == zipfile.ZIP_DEFLATED
        assert index_info.compress_type == zipfile.ZIP_DEFLATED

    def test_file_pos_and_file_len_point_at_correct_wpt(self):
        # Regression test for #466: byte-offset computation used to run a
        # fresh regex search over the whole GPX text per cache (O(n²)). This
        # confirms the single-pass replacement still points file_pos/file_len
        # at the correct <wpt>...</wpt> block for each cache, in order.
        caches = [_cache(gc_code=f"GC{i:03d}", cache_id=i) for i in range(5)]
        entries = _ggz_entries(generate_ggz(caches))
        gpx_bytes = entries["data/opensak_export.gpx"]
        index_xml = entries["index/com/garmin/geocaches/v0/index.xml"].decode("utf-8")

        root = ET.fromstring(index_xml)
        for gch in root.iter("gch"):
            code = gch.find("code").text
            file_pos = int(gch.find("file_pos").text)
            file_len = int(gch.find("file_len").text)
            wpt_bytes = gpx_bytes[file_pos:file_pos + file_len]
            assert wpt_bytes.startswith(b"<wpt")
            assert wpt_bytes.endswith(b"</wpt>")
            assert f"<name>{code}</name>".encode("utf-8") in wpt_bytes

    def test_large_export_scales_linearly_not_quadratically(self):
        # Regression test for #466: with the old O(n²) per-cache regex scan,
        # 1500 caches took several seconds; the O(n) single-pass version
        # should comfortably finish in well under a second. A generous
        # threshold is used to avoid CI flakiness while still catching an
        # accidental reintroduction of the quadratic behaviour.
        caches = [_cache(gc_code=f"GC{i:05d}", cache_id=i) for i in range(1500)]
        start = time.perf_counter()
        generate_ggz(caches)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, (
            f"generate_ggz took {elapsed:.2f}s for 1500 caches — "
            "possible reintroduction of O(n²) byte-offset lookup"
        )


# ── device scanning ───────────────────────────────────────────────────────────

class TestDeviceScan:
    def test_find_garmin_devices_filters_to_garmin(self, tmp_path, monkeypatch):
        garmin = tmp_path / "GARMIN_DEV"
        (garmin / "Garmin").mkdir(parents=True)
        (garmin / "Garmin" / "GarminDevice.xml").write_text("<device/>")
        plain = tmp_path / "USB"
        plain.mkdir()
        monkeypatch.setattr("opensak.gps.garmin._get_mount_points", lambda: [garmin, plain])
        # MTP scanning is independent of the mocked mass-storage candidates;
        # disable both MTP discovery paths so this test remains focused on
        # mass-storage mount-point filtering.
        monkeypatch.setattr("opensak.gps.mtp.find_mtp_devices", lambda: [])
        monkeypatch.setattr("opensak.gps.garmin._linux_mtp_mounts", lambda: [])
        assert find_garmin_devices() == [garmin]

    def test_find_garmin_devices_requires_writable_mount(self, tmp_path, monkeypatch):
        garmin = tmp_path / "GARMIN_DEV"
        (garmin / "Garmin").mkdir(parents=True)
        (garmin / "Garmin" / "GarminDevice.xml").write_text("<device/>")
        monkeypatch.setattr("opensak.gps.garmin._get_mount_points", lambda: [garmin])
        monkeypatch.setattr("opensak.gps.garmin._linux_mtp_mounts", lambda: [])
        monkeypatch.setattr("opensak.gps.mtp.find_mtp_devices", lambda: [])
        monkeypatch.setattr("opensak.gps.garmin._is_writable_directory", lambda p: False)
        assert find_garmin_devices() == []

    @pytest.mark.skipif(platform.system() == "Windows", reason="MTP paths contain colons, illegal on Windows")
    def test_find_garmin_devices_includes_mtp_mounts(self, tmp_path, monkeypatch):
        mtp_root = tmp_path / "gvfs" / "mtp:host=091e_506a_0000"
        mtp_root.mkdir(parents=True)
        monkeypatch.setattr("opensak.gps.garmin._get_mount_points", lambda: [])
        monkeypatch.setattr("opensak.gps.garmin._linux_mtp_mounts", lambda: [mtp_root])
        monkeypatch.setattr("opensak.gps.mtp.find_mtp_devices", lambda: [])
        monkeypatch.setattr(
            "opensak.gps.garmin._is_garmin_mtp_mount",
            lambda p: p == mtp_root,
        )
        assert find_garmin_devices() == [mtp_root]

    def test_debug_scan_reports_devices(self, tmp_path, monkeypatch):
        garmin = tmp_path / "GARMIN_DEV"
        (garmin / "Garmin").mkdir(parents=True)
        (garmin / "Garmin" / "GarminDevice.xml").write_text("<device/>")
        monkeypatch.setattr("opensak.gps.garmin._get_mount_points", lambda: [garmin])
        monkeypatch.setattr("opensak.gps.garmin._linux_mtp_mounts", lambda: [])
        monkeypatch.setattr("opensak.gps.mtp.find_mtp_devices", lambda: [])
        report = debug_scan()
        assert "Garmin scan debug" in report
        assert "GARMIN" in report

    def test_macos_volumes_returns_list(self):
        # On the test host /Volumes exists; just assert the shape.
        assert isinstance(_macos_volumes(), list)


# ── error paths ───────────────────────────────────────────────────────────────

class TestErrorPaths:
    def _device_with_gpx(self, root, names):
        gpx_dir = root / GARMIN_GPX_SUBPATH
        gpx_dir.mkdir(parents=True)
        for n in names:
            (gpx_dir / n).write_text("x")
        return gpx_dir

    def test_delete_skips_non_file_match(self, tmp_path):
        gpx_dir = self._device_with_gpx(tmp_path, ["a.gpx"])
        (gpx_dir / "dir.gpx").mkdir()  # matches *.gpx but is not a file
        result = delete_gpx_files(tmp_path)
        assert result.deleted_count == 1

    def test_delete_records_failed_unlink(self, tmp_path, monkeypatch):
        self._device_with_gpx(tmp_path, ["a.gpx"])
        def boom(self, *a, **k):
            raise OSError("locked")
        monkeypatch.setattr(Path, "unlink", boom)
        result = delete_gpx_files(tmp_path)
        assert result.failed_count == 1
        assert result.deleted_count == 0

    def test_delete_outer_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "opensak.gps.garmin.get_garmin_gpx_path",
            lambda root: (_ for _ in ()).throw(OSError("boom")),
        )
        result = delete_gpx_files(tmp_path)
        assert result.success is False
        assert "boom" in result.error.lower()

    def test_export_to_device_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "opensak.gps.garmin.generate_gpx",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")),
        )
        result = export_to_device([_cache()], tmp_path / "dev")
        assert result.success is False

    def test_export_to_file_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "opensak.gps.garmin.generate_gpx",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")),
        )
        result = export_to_file([_cache()], tmp_path / "out.gpx")
        assert result.success is False
        assert "nope" in result.error


# ── MTP support ───────────────────────────────────────────────────────────────

@pytest.mark.skipif(platform.system() == "Windows", reason="MTP paths contain colons, illegal on Windows")
class TestMtp:
    """Tests for MTP detection, folder lookup, gio copy/remove, and MTP export."""

    def _mtp_device(self, tmp_path):
        """Build a fake MTP-style mount structure."""
        root = tmp_path / "gvfs" / "mtp:host=091e_test"
        storage = root / "Internal Storage"
        garmin = storage / "GARMIN"
        gpx = garmin / "GPX"
        gpx.mkdir(parents=True)
        (garmin / "GarminDevice.xml").write_text("<device/>")
        return root

    # ── is_mtp_device ─────────────────────────────────────────────────────

    def test_is_mtp_device_true(self):
        assert is_mtp_device(Path("/run/user/1000/gvfs/mtp:host=091e")) is True

    def test_is_mtp_device_false(self):
        assert is_mtp_device(Path("/media/user/GARMIN")) is False

    # ── _find_mtp_garmin_root ─────────────────────────────────────────────

    def test_find_mtp_garmin_root_found(self, tmp_path):
        root = self._mtp_device(tmp_path)
        result = _find_mtp_garmin_root(root)
        assert result is not None
        assert result.name == "GARMIN"

    def test_find_mtp_garmin_root_not_found(self, tmp_path):
        root = tmp_path / "gvfs" / "mtp:host=fake"
        (root / "Internal Storage" / "SomeFolder").mkdir(parents=True)
        assert _find_mtp_garmin_root(root) is None

    def test_find_mtp_garmin_root_empty(self, tmp_path):
        root = tmp_path / "gvfs" / "mtp:host=empty"
        root.mkdir(parents=True)
        assert _find_mtp_garmin_root(root) is None

    # ── _find_mtp_gpx_dir ─────────────────────────────────────────────────

    def test_find_mtp_gpx_dir_found(self, tmp_path):
        root = self._mtp_device(tmp_path)
        result = _find_mtp_gpx_dir(root)
        assert result is not None
        assert result.name == "GPX"

    def test_find_mtp_gpx_dir_no_garmin(self, tmp_path):
        root = tmp_path / "gvfs" / "mtp:host=nogarmin"
        root.mkdir(parents=True)
        assert _find_mtp_gpx_dir(root) is None

    def test_find_mtp_gpx_dir_case_insensitive(self, tmp_path):
        root = tmp_path / "gvfs" / "mtp:host=case"
        garmin = root / "Internal Storage" / "Garmin"
        (garmin / "gpx").mkdir(parents=True)
        (garmin / "GarminDevice.xml").write_text("<device/>")
        result = _find_mtp_gpx_dir(root)
        assert result is not None

    # ── _find_mtp_ggz_dir ─────────────────────────────────────────────────

    def test_find_mtp_ggz_dir_found(self, tmp_path):
        root = self._mtp_device(tmp_path)
        ggz = root / "Internal Storage" / "GARMIN" / "GGZ"
        ggz.mkdir()
        result = _find_mtp_ggz_dir(root)
        assert result is not None
        assert result.name == "GGZ"

    def test_find_mtp_ggz_dir_missing(self, tmp_path):
        root = self._mtp_device(tmp_path)
        assert _find_mtp_ggz_dir(root) is None

    # ── cancel_mtp_transfer ───────────────────────────────────────────────

    def test_cancel_mtp_transfer_no_proc(self):
        import opensak.gps.garmin as mod
        mod._active_gio_proc = None
        cancel_mtp_transfer()  # should not raise
        assert mod._active_gio_proc is None

    def test_cancel_mtp_transfer_kills_proc(self):
        import opensak.gps.garmin as mod
        killed = []
        proc = type("FakeProc", (), {"kill": lambda self: killed.append(True)})()
        mod._active_gio_proc = proc
        cancel_mtp_transfer()
        assert killed == [True]
        assert mod._active_gio_proc is None

    # ── _gio_copy (mocked) ────────────────────────────────────────────────

    def test_gio_subprocess_env_strips_packaged_library_paths(self, monkeypatch):
        from opensak.gps.garmin import _gio_subprocess_env

        monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/app/usr/lib")
        monkeypatch.setenv("GI_TYPELIB_PATH", "/tmp/app/usr/lib/girepository-1.0")
        monkeypatch.setenv("GIO_EXTRA_MODULES", "/tmp/app/usr/lib/gio/modules")
        monkeypatch.setenv("GIO_MODULE_DIR", "/tmp/app/usr/lib/gio/modules")

        env = _gio_subprocess_env()

        assert "LD_LIBRARY_PATH" not in env
        assert "GI_TYPELIB_PATH" not in env
        assert "GIO_EXTRA_MODULES" not in env
        assert "GIO_MODULE_DIR" not in env

    def test_gio_subprocess_env_restores_original_ld_library_path(self, monkeypatch):
        from opensak.gps.garmin import _gio_subprocess_env

        monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/app/usr/lib")
        monkeypatch.setenv("APPIMAGE_ORIGINAL_LD_LIBRARY_PATH", "/opt/custom/lib")

        env = _gio_subprocess_env()

        assert env["LD_LIBRARY_PATH"] == "/opt/custom/lib"

    def test_gio_copy_success(self, tmp_path, monkeypatch):
        from opensak.gps.garmin import _gio_copy
        src = tmp_path / "src.gpx"
        src.write_text("<gpx/>")
        dst = tmp_path / "dst.gpx"
        captured = {}

        class FakeProc:
            returncode = 0
            def communicate(self, timeout=None):
                return b"", b""
            def kill(self): pass

        def fake_popen(*args, **kwargs):
            captured.update(kwargs)
            return FakeProc()

        monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/app/usr/lib")
        monkeypatch.setattr("opensak.gps.garmin.subprocess.Popen", fake_popen)
        _gio_copy(src, dst)  # should not raise
        assert "LD_LIBRARY_PATH" not in captured["env"]

    def test_gio_copy_failure(self, tmp_path, monkeypatch):
        from opensak.gps.garmin import _gio_copy
        src = tmp_path / "src.gpx"
        src.write_text("<gpx/>")
        dst = tmp_path / "dst.gpx"

        class FakeProc:
            returncode = 1
            def communicate(self, timeout=None):
                return b"", b"error msg"
            def kill(self): pass

        monkeypatch.setattr("opensak.gps.garmin.subprocess.Popen", lambda *a, **k: FakeProc())
        with pytest.raises(OSError, match="error msg"):
            _gio_copy(src, dst)

    def test_gio_copy_timeout(self, tmp_path, monkeypatch):
        from opensak.gps.garmin import _gio_copy
        import subprocess as sp
        src = tmp_path / "src.gpx"
        src.write_text("<gpx/>")
        dst = tmp_path / "dst.gpx"

        class FakeProc:
            returncode = -9
            def communicate(self, timeout=None):
                if timeout:
                    raise sp.TimeoutExpired("gio", timeout)
                return b"", b""
            def kill(self): pass

        monkeypatch.setattr("opensak.gps.garmin.subprocess.Popen", lambda *a, **k: FakeProc())
        with pytest.raises(OSError, match="timed out"):
            _gio_copy(src, dst)

    # ── _gio_remove (mocked) ──────────────────────────────────────────────

    def test_gio_remove_success(self, monkeypatch):
        from opensak.gps.garmin import _gio_remove
        captured = {}

        def fake_run(*a, **k):
            captured.update(k)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/app/usr/lib")
        monkeypatch.setattr("opensak.gps.garmin.subprocess.run", fake_run)
        assert _gio_remove(Path("/fake")) is True
        assert "LD_LIBRARY_PATH" not in captured["env"]

    def test_gio_remove_failure(self, monkeypatch):
        from opensak.gps.garmin import _gio_remove
        def fake_run(*a, **k):
            return type("R", (), {"returncode": 1})()
        monkeypatch.setattr("opensak.gps.garmin.subprocess.run", fake_run)
        assert _gio_remove(Path("/fake")) is False

    def test_gio_remove_exception(self, monkeypatch):
        from opensak.gps.garmin import _gio_remove
        def fake_run(*a, **k):
            raise FileNotFoundError("no gio")
        monkeypatch.setattr("opensak.gps.garmin.subprocess.run", fake_run)
        assert _gio_remove(Path("/fake")) is False

    def test_gio_remove_and_wait_verifies_file_disappears(self, monkeypatch):
        from opensak.gps.garmin import _gio_remove_and_wait

        exists = [True, True, False]
        monkeypatch.setattr("opensak.gps.garmin._gio_remove", lambda path: True)
        monkeypatch.setattr("opensak.gps.garmin._path_exists", lambda path: exists.pop(0))
        monkeypatch.setattr("opensak.gps.garmin.time.sleep", lambda seconds: None)

        assert _gio_remove_and_wait(Path("/fake")) is True

    def test_gio_remove_and_wait_fails_when_file_remains(self, monkeypatch):
        from opensak.gps.garmin import _gio_remove_and_wait

        monkeypatch.setattr("opensak.gps.garmin._gio_remove", lambda path: True)
        monkeypatch.setattr("opensak.gps.garmin._path_exists", lambda path: True)
        monkeypatch.setattr("opensak.gps.garmin.time.sleep", lambda seconds: None)

        assert _gio_remove_and_wait(Path("/fake"), timeout=0.01) is False

    def test_gio_copy_with_replace_removes_existing_target(self, monkeypatch, tmp_path):
        from opensak.gps.garmin import _gio_copy_with_replace

        src = tmp_path / "Washington.gpx"
        dst = tmp_path / "mtp" / "Washington.gpx"
        src.write_text("<gpx/>")
        removed = []
        copied = []

        states = {dst: True}
        monkeypatch.setattr("opensak.gps.garmin._path_exists", lambda path: states.get(path, False))
        monkeypatch.setattr("opensak.gps.garmin._gio_remove_and_wait", lambda path: removed.append(path) or True)
        def fake_copy(source, dest):
            copied.append((source, dest))
            states[dst] = True
        monkeypatch.setattr("opensak.gps.garmin._gio_copy", fake_copy)

        _gio_copy_with_replace(src, dst)

        assert removed == [dst]
        assert copied == [(src, dst.parent)]

    def test_gio_copy_with_replace_checks_space_after_removing_target(self, monkeypatch, tmp_path):
        from opensak.gps.garmin import _gio_copy_with_replace

        src = tmp_path / "Washington.gpx"
        dst = tmp_path / "mtp" / "Washington.gpx"
        src.write_text("<gpx/>")
        events = []
        states = {dst: True}

        def fake_remove(path):
            events.append("remove")
            states[dst] = False
            return True

        def fake_space(source, folder):
            events.append("space")

        def fake_copy(source, folder):
            events.append("copy")
            states[dst] = True

        monkeypatch.setattr("opensak.gps.garmin._path_exists", lambda path: states.get(path, False))
        monkeypatch.setattr("opensak.gps.garmin._gio_remove_and_wait", fake_remove)
        monkeypatch.setattr("opensak.gps.garmin._ensure_mtp_space", fake_space)
        monkeypatch.setattr("opensak.gps.garmin._gio_copy", fake_copy)

        _gio_copy_with_replace(src, dst)

        assert events == ["remove", "space", "copy"]

    def test_ensure_mtp_space_reports_full_device(self, monkeypatch, tmp_path):
        from opensak.gps.garmin import _ensure_mtp_space

        src = tmp_path / "Washington.gpx"
        dest_folder = tmp_path / "mtp"
        src.write_bytes(b"x" * 2048)
        dest_folder.mkdir()

        monkeypatch.setattr(
            "opensak.gps.garmin.shutil.disk_usage",
            lambda path: type("Usage", (), {"free": 0})(),
        )

        with pytest.raises(OSError, match="Not enough free space"):
            _ensure_mtp_space(src, dest_folder)

    def test_ensure_mtp_space_allows_unknown_usage(self, monkeypatch, tmp_path):
        from opensak.gps.garmin import _ensure_mtp_space

        src = tmp_path / "Washington.gpx"
        dest_folder = tmp_path / "mtp"
        src.write_text("<gpx/>")

        def fail_usage(path):
            raise OSError("unknown")

        monkeypatch.setattr("opensak.gps.garmin.shutil.disk_usage", fail_usage)

        _ensure_mtp_space(src, dest_folder)

    def test_gio_copy_with_replace_retries_send_object_info(self, monkeypatch, tmp_path):
        from opensak.gps.garmin import _gio_copy_with_replace

        src = tmp_path / "Washington.gpx"
        dst = tmp_path / "mtp" / "Washington.gpx"
        src.write_text("<gpx/>")
        calls = []

        def fake_copy(source, dest):
            calls.append((source, dest))
            if len(calls) == 1:
                raise OSError("gio: file:///tmp/Washington.gpx: libmtp error:  Could not send object info.")

        states = {dst: False}
        monkeypatch.setattr("opensak.gps.garmin._path_exists", lambda path: states.get(path, False))
        monkeypatch.setattr("opensak.gps.garmin.time.sleep", lambda seconds: None)
        def copy_and_create(source, dest):
            fake_copy(source, dest)
            if len(calls) == 2:
                states[dst] = True
        monkeypatch.setattr("opensak.gps.garmin._gio_copy", copy_and_create)

        _gio_copy_with_replace(src, dst)

        assert calls == [(src, dst.parent), (src, dst.parent)]

    def test_gio_copy_with_replace_fails_when_expected_file_missing(self, monkeypatch, tmp_path):
        from opensak.gps.garmin import _gio_copy_with_replace

        src = tmp_path / "Washington.gpx"
        dst = tmp_path / "mtp" / "Washington.gpx"
        src.write_text("<gpx/>")

        monkeypatch.setattr("opensak.gps.garmin._path_exists", lambda path: False)
        monkeypatch.setattr("opensak.gps.garmin._gio_copy", lambda source, dest: None)

        with pytest.raises(OSError, match="expected file"):
            _gio_copy_with_replace(src, dst)

    # ── export_to_device MTP branch ───────────────────────────────────────

    def test_export_to_device_mtp(self, tmp_path, monkeypatch):
        root = self._mtp_device(tmp_path)
        copied = []
        def fake_gio_copy(src, dst):
            dst.parent.mkdir(parents=True, exist_ok=True)
            import shutil; shutil.copy2(src, dst)
            copied.append((src, dst))
        monkeypatch.setattr("opensak.gps.garmin._gio_copy_with_replace", fake_gio_copy)

        result = export_to_device([_cache()], root, "test_mtp")
        assert result.success
        assert result.cache_count == 1
        assert len(copied) == 1
        assert copied[0][0].name == "test_mtp.gpx"
        assert copied[0][1].name == "test_mtp.gpx"

    def test_export_to_device_mtp_no_gpx_dir(self, tmp_path, monkeypatch):
        root = tmp_path / "gvfs" / "mtp:host=091e_nodir"
        root.mkdir(parents=True)
        monkeypatch.setattr("opensak.gps.garmin._gio_copy_with_replace", lambda s, d: None)
        result = export_to_device([_cache()], root, "test")
        assert result.success is False
        assert "not found" in result.error.lower()

    # ── export_ggz_to_device MTP branch ───────────────────────────────────

    def test_export_ggz_to_device_mtp(self, tmp_path, monkeypatch):
        root = self._mtp_device(tmp_path)
        ggz = root / "Internal Storage" / "GARMIN" / "GGZ"
        ggz.mkdir()
        copied = []
        def fake_gio_copy(src, dst):
            copied.append((src, dst))
        monkeypatch.setattr("opensak.gps.garmin._gio_copy_with_replace", fake_gio_copy)

        result = export_ggz_to_device([_cache()], root, "test_ggz")
        assert result.success
        assert len(copied) == 1
        assert copied[0][0].name == "test_ggz.ggz"
        assert copied[0][1].name == "test_ggz.ggz"

    def test_export_ggz_to_device_mtp_no_garmin(self, tmp_path, monkeypatch):
        root = tmp_path / "gvfs" / "mtp:host=091e_nogarmin"
        root.mkdir(parents=True)
        monkeypatch.setattr("opensak.gps.garmin._gio_copy_with_replace", lambda s, d: None)
        result = export_ggz_to_device([_cache()], root, "test")
        assert result.success is False

    # ── delete_gpx_files MTP branch ───────────────────────────────────────

    def test_delete_mtp_uses_gio_remove(self, tmp_path, monkeypatch):
        root = self._mtp_device(tmp_path)
        gpx_dir = root / "Internal Storage" / "GARMIN" / "GPX"
        (gpx_dir / "old.gpx").write_text("<gpx/>")

        removed = []
        def fake_gio_remove(p):
            removed.append(p)
            return True
        monkeypatch.setattr("opensak.gps.garmin._gio_remove_and_wait", fake_gio_remove)

        result = delete_gpx_files(root)
        assert result.deleted_count == 1
        assert len(removed) == 1

    def test_delete_mtp_gio_remove_failure(self, tmp_path, monkeypatch):
        root = self._mtp_device(tmp_path)
        gpx_dir = root / "Internal Storage" / "GARMIN" / "GPX"
        (gpx_dir / "old.gpx").write_text("<gpx/>")

        monkeypatch.setattr("opensak.gps.garmin._gio_remove_and_wait", lambda p: False)

        result = delete_gpx_files(root)
        assert result.failed_count == 1
        assert result.deleted_count == 0

    def test_delete_mtp_with_explicit_folder(self, tmp_path, monkeypatch):
        root = self._mtp_device(tmp_path)
        ggz_dir = root / "Internal Storage" / "GARMIN" / "GGZ"
        ggz_dir.mkdir()
        (ggz_dir / "old.ggz").write_text("data")

        removed = []
        monkeypatch.setattr("opensak.gps.garmin._gio_remove_and_wait", lambda p: (removed.append(p) or True))

        result = delete_gpx_files(root, pattern="*.ggz", folder=ggz_dir)
        assert result.deleted_count == 1

    def test_delete_mtp_ggz_with_dialog_resolved_folder(self, tmp_path, monkeypatch):
        root = self._mtp_device(tmp_path)
        ggz_dir = root / "Internal Storage" / "GARMIN" / "GGZ"
        ggz_dir.mkdir()
        (ggz_dir / "old.ggz").write_text("data")

        monkeypatch.setattr("opensak.gps.garmin._gio_remove_and_wait", lambda path: True)

        result = delete_gpx_files(
            root,
            pattern="*.ggz",
            folder=get_garmin_ggz_path(root),
        )

        assert result.deleted_count == 1
