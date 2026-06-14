"""tests/unit-tests/test_garmin.py — Garmin GPX/LOC/GGZ generation/export (no device needed)."""

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensak.gps.garmin import (
    GARMIN_GPX_SUBPATH,
    DeleteResult,
    ExportResult,
    _cache_symbol,
    _effective_coords,
    _is_garmin,
    _macos_volumes,
    debug_scan,
    delete_gpx_files,
    export_to_device,
    export_to_file,
    find_garmin_devices,
    generate_ggz,
    generate_gpx,
    generate_loc,
    get_garmin_gpx_path,
)


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
    encoded_hints=None,
    hidden_date=None,
    logs=None,
    user_note=None,
    cache_id=1,
    container="Regular",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=cache_id,
        gc_code=gc_code,
        name=name,
        cache_type=cache_type,
        latitude=latitude,
        longitude=longitude,
        difficulty=difficulty,
        terrain=terrain,
        placed_by=placed_by,
        available=available,
        archived=archived,
        country=country,
        encoded_hints=encoded_hints,
        hidden_date=hidden_date,
        logs=logs or [],
        user_note=user_note,
        container=container,
    )


def _log(log_id="1", log_type="Found it", finder="Tester", text="TFTC", log_date=None):
    return SimpleNamespace(
        log_id=log_id,
        log_type=log_type,
        finder=finder,
        text=text,
        log_date=log_date or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _note(is_corrected=True, corrected_lat=55.0, corrected_lon=12.0):
    return SimpleNamespace(
        is_corrected=is_corrected,
        corrected_lat=corrected_lat,
        corrected_lon=corrected_lon,
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

    def test_unknown_type_falls_back(self):
        assert _cache_symbol("Nonexistent Type") == "Geocache"

    def test_empty_string_falls_back(self):
        assert _cache_symbol("") == "Geocache"


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

    def test_custom_filename_in_metadata(self):
        result = generate_gpx([_cache()], filename="my_export")
        assert "my_export" in result

    def test_country_present_when_set(self):
        result = generate_gpx([_cache(country="Denmark")])
        assert "Denmark" in result

    def test_hints_present_when_set(self):
        result = generate_gpx([_cache(encoded_hints="Under a rock")])
        assert "Under a rock" in result

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
        assert "Ingen" in str(r)

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


# ── device scanning ───────────────────────────────────────────────────────────

class TestDeviceScan:
    def test_find_garmin_devices_filters_to_garmin(self, tmp_path, monkeypatch):
        garmin = tmp_path / "GARMIN_DEV"
        (garmin / "Garmin").mkdir(parents=True)
        (garmin / "Garmin" / "GarminDevice.xml").write_text("<device/>")
        plain = tmp_path / "USB"
        plain.mkdir()
        monkeypatch.setattr("opensak.gps.garmin._get_mount_points", lambda: [garmin, plain])
        assert find_garmin_devices() == [garmin]

    def test_debug_scan_reports_devices(self, tmp_path, monkeypatch):
        garmin = tmp_path / "GARMIN_DEV"
        (garmin / "Garmin").mkdir(parents=True)
        (garmin / "Garmin" / "GarminDevice.xml").write_text("<device/>")
        monkeypatch.setattr("opensak.gps.garmin._get_mount_points", lambda: [garmin])
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
        assert "fejl" in result.error.lower()

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
