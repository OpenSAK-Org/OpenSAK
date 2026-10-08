# tests/unit-tests/test_poi_export.py — caches and child waypoints as Garmin
# POI files: settings, templates, point selection and the written files.

from datetime import datetime
from types import SimpleNamespace

import pytest

from opensak.export.poi_export import (
    build_poi_groups, expand_poi_template, smart_name, write_poi_export,
)
from opensak.export.poi_export_settings import PoiExportProfile, PoiExportSettings


def _wp(prefix, wp_type, lat=47.01, lon=8.01, name="", flag=False, **kw):
    return SimpleNamespace(
        prefix=prefix, wp_type=wp_type, name=name, description=kw.get("description"),
        comment=kw.get("comment"), latitude=lat, longitude=lon, wp_flag=flag,
        wp_code=kw.get("wp_code"),
    )


def _cache(code="GC12345", name="Hidden in the Old Forest", waypoints=(), **kw):
    return SimpleNamespace(
        gc_code=code, name=name, cache_type=kw.get("cache_type", "Traditional Cache"),
        latitude=kw.get("lat", 47.0), longitude=kw.get("lon", 8.0),
        difficulty=1.5, terrain=2.0, placed_by="Placer", owner_name="Owner",
        container="Small", encoded_hints="under the stone", favorite_points=7,
        hidden_date=datetime(2020, 5, 17), country="Switzerland", state="Zürich",
        county=None, user_data_1="d1", user_data_2=None, user_data_3=None,
        user_data_4=None, user_note=kw.get("user_note"), waypoints=list(waypoints),
    )


def _names(groups):
    return [[p.name for p in g.points] for g in groups]


# ── Templates ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, length, expected", [
    ("Short", 8, "Short"),
    ("Hidden in the Old Forest", 30, "Hidden in the Old Forest"),
    ("Hidden in the Old Forest", 12, "HiddnOldFrst"),
    ("Hidden in the Old Forest", 8, "HddnOldF"),
    ("Der kleine Wald!", 10, "KleineWald"),
    ("  many   spaces  ", 20, "many spaces"),
])
def test_smart_name(text, length, expected):
    assert smart_name(text, length) == expected


def test_template_fills_known_and_keeps_unknown_variables():
    values = {"name": "N", "dif": "1.5"}
    assert expand_poi_template("{NAME} ({dif}) {nope}", values) == "N (1.5) {nope}"


# ── Settings ──────────────────────────────────────────────────────────────────

class TestPoiExportSettings:
    def test_roundtrip(self):
        s = PoiExportSettings(folder="/x", file_name="{database}", if_exists="skip",
                              waypoints_only=True, waypoint_flag="flagged",
                              split_by_type=True, max_points=5, name="{code}",
                              smart_length=8, extra_field="address", category="C",
                              proximity=0.5, proximity_unit="km", icon="/i.bmp")
        assert PoiExportSettings.from_dict(s.to_dict()) == s

    def test_invalid_values_fall_back_to_defaults(self):
        s = PoiExportSettings.from_dict({
            "if_exists": "never", "waypoint_flag": "x", "extra_field": 3,
            "proximity_unit": "parsec", "max_points": -1, "smart_length": 0,
            "proximity": True, "include_waypoints": "yes", "name": None,
        })
        assert s == PoiExportSettings()

    def test_int_proximity_is_accepted(self):
        assert PoiExportSettings.from_dict({"proximity": 50}).proximity == 50.0

    @pytest.mark.parametrize("value, unit, meters", [
        (0, "m", 0), (50, "m", 50), (0.5, "km", 500), (100, "ft", 30), (1, "mi", 1609),
    ])
    def test_proximity_meters(self, value, unit, meters):
        settings = PoiExportSettings(proximity=value, proximity_unit=unit)
        assert settings.proximity_meters() == meters

    def test_profiles_are_stored_apart_from_file_export(self, tmp_path):
        PoiExportProfile("Garmin", PoiExportSettings(category="Mine")).save(tmp_path)
        (path,) = PoiExportProfile.list_profiles(tmp_path)
        loaded = PoiExportProfile.load(path)
        assert loaded.name == "Garmin" and loaded.settings.category == "Mine"
        assert PoiExportProfile.default_dir().name == "poi_export_settings"
        assert PoiExportProfile.load_last_used(tmp_path / "none") == PoiExportSettings()


# ── Points ────────────────────────────────────────────────────────────────────

def test_default_texts_for_caches_and_waypoints():
    cache = _cache(waypoints=[_wp("PK", "Parking Area", name="Parking", comment="free")])
    (group,) = build_poi_groups([cache], PoiExportSettings(extra="{hint} {comment}"))
    tradi, parking = group.points
    assert group.category == "OpenSAK"
    assert (tradi.name, tradi.description, tradi.phone) == (
        "HiddenOldForest", "Hidden in the Old Forest by Placer (1.5/2)",
        "under the stone")
    assert (parking.name, parking.description, parking.phone) == (
        "Parking", "Parking by Placer (1.5/2)", "under the stone free")
    assert (parking.lat, parking.lon) == (47.01, 8.01)


def test_all_variables():
    cache = _cache(user_note=SimpleNamespace(note="my note", is_corrected=False))
    template = ("{code}|{type}|{comment}|{lat}|{lon}|{coords}|{cache_code}|{cache_name}|"
                "{by}|{owner}|{dif}|{ter}|{size}|{fav}|{hidden}|{country}|{state}|"
                "{county}|{note}|{data1}|{data2}")
    (group,) = build_poi_groups([cache], PoiExportSettings(description=template))
    assert group.points[0].description == (
        "GC12345|Traditional Cache||47.00000|8.00000|N47 00.000 E008 00.000|GC12345|"
        "Hidden in the Old Forest|Placer|Owner|1.5|2|Small|7|2020-05-17|Switzerland|"
        "Zürich||my note|d1|")


def test_waypoint_code_and_texts_describe_the_waypoint():
    cache = _cache(waypoints=[
        _wp("PK", "Parking Area"),
        _wp("S1", "Physical Stage", wp_code="S112345", description="Stage one"),
    ])
    settings = PoiExportSettings(name="{code}", description="{type}: {name} @ {cache_code}")
    (group,) = build_poi_groups([cache], settings)
    assert [(p.name, p.description) for p in group.points] == [
        ("GC12345", "Traditional Cache: Hidden in the Old Forest @ GC12345"),
        ("PK12345", "Parking Area: Parking Area @ GC12345"),
        ("S112345", "Physical Stage: Stage one @ GC12345"),
    ]


def test_corrected_coordinates():
    note = SimpleNamespace(note=None, is_corrected=True, corrected_lat=46.5, corrected_lon=7.5)
    cache = _cache(user_note=note)
    (point,) = build_poi_groups([cache], PoiExportSettings())[0].points
    assert (point.lat, point.lon) == (46.5, 7.5)
    (point,) = build_poi_groups([cache], PoiExportSettings(use_corrected_coords=False))[0].points
    assert (point.lat, point.lon) == (47.0, 8.0)


@pytest.mark.parametrize("settings, expected", [
    (dict(include_waypoints=False), [["C1", "C2"]]),
    (dict(), [["C1", "A", "B", "C2", "C"]]),
    (dict(waypoints_only=True), [["A", "B", "C"]]),
    (dict(waypoints_only=True, include_waypoints=False), [["A", "B", "C"]]),
    (dict(waypoint_flag="flagged"), [["C1", "B", "C2"]]),
    (dict(waypoint_flag="unflagged"), [["C1", "A", "C2", "C"]]),
    (dict(max_points=3), [["C1", "A", "B"]]),
    (dict(split_by_type=True), [["C1", "C2"], ["A", "C"], ["B"]]),
    (dict(split_by_type=True, waypoints_only=True), [["A", "C"], ["B"]]),
    (dict(split_by_type=True, max_points=2), [["C1"], ["A"]]),
])
def test_point_selection(settings, expected):
    caches = [
        _cache("GC1", "C1", [_wp("PK", "Parking Area", name="A"),
                             _wp("TR", "Trailhead", name="B", flag=True),
                             _wp("XX", "Parking Area", name="X", lat=None)]),
        _cache("GC2", "C2", [_wp("PK", "Parking Area", name="C")]),
        _cache("GC3", "No coords", lat=None),
    ]
    groups = build_poi_groups(caches, PoiExportSettings(**settings))
    assert _names(groups) == expected
    for g in groups:
        assert g.category == (g.waypoint_type or "OpenSAK")


def test_proximity_and_extra_field():
    settings = PoiExportSettings(proximity=0.1, proximity_unit="km", extra_field="address",
                                 extra="line 1\nline 2")
    (point,) = build_poi_groups([_cache()], settings)[0].points
    assert point.proximity_m == 100
    assert (point.address, point.phone) == ("line 1\nline 2", "")
    settings.extra_field = "phone"
    (point,) = build_poi_groups([_cache()], settings)[0].points
    assert (point.address, point.phone) == ("", "line 1 line 2")


def test_empty_name_falls_back_to_code():
    (point,) = build_poi_groups([_cache()], PoiExportSettings(name="{data2}"))[0].points
    assert point.name == "GC12345"


# ── Files ─────────────────────────────────────────────────────────────────────

def test_write_poi_export_names_files_and_asks_for_existing(tmp_path):
    caches = [_cache(waypoints=[_wp("PK", "Parking Area"), _wp("TR", "Trail/head")])]
    settings = PoiExportSettings(file_name="{database}_{count}", split_by_type=True)
    (tmp_path / "DB_3 - Parking Area.gpi").write_bytes(b"old")
    asked = []

    def should_write(path):
        asked.append(path.name)
        return False

    written = write_poi_export(caches, settings, tmp_path, database="DB",
                               should_write=should_write)
    assert [(p.name, n) for p, n in written] == [("DB_3.gpi", 1), ("DB_3 - Trail_head.gpi", 1)]
    assert asked == ["DB_3 - Parking Area.gpi"]
    assert (tmp_path / "DB_3 - Parking Area.gpi").read_bytes() == b"old"
    assert (tmp_path / "DB_3.gpi").read_bytes()[8:16] == b"GRMREC00"


def test_write_poi_export_writes_nothing_without_points(tmp_path):
    settings = PoiExportSettings(waypoints_only=True)
    assert write_poi_export([_cache()], settings, tmp_path / "out") == []
    assert not (tmp_path / "out").exists()
