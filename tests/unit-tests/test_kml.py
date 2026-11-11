# tests/unit-tests/test_kml.py — KML export for Google Maps (no GUI/DB needed).

import io
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from opensak.export.kml import (
    _cache_description,
    _esc,
    _style_id_for_cache,
    _style_id_for_waypoint,
    _waypoint_description,
    export_kml,
)

KML_NS = "{http://www.opengis.net/kml/2.2}"


def _note(is_corrected=True, corrected_lat=60.0, corrected_lon=20.0):
    return SimpleNamespace(
        is_corrected=is_corrected,
        corrected_lat=corrected_lat,
        corrected_lon=corrected_lon,
    )


def _wpt(name="WP1", wp_type="Parking Area", latitude=55.0, longitude=12.0,
         description="desc", comment="note"):
    return SimpleNamespace(
        name=name, wp_type=wp_type, latitude=latitude, longitude=longitude,
        description=description, comment=comment,
    )


def _cache(
    gc_code="GC12345", name="Test Cache", cache_type="Traditional Cache",
    latitude=55.6761, longitude=12.5683, difficulty=2.0, terrain=3.0,
    container="Regular", placed_by="Owner", short_description="A nice cache",
    encoded_hints=None, found=False, user_note=None, waypoints=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        gc_code=gc_code, name=name, cache_type=cache_type,
        latitude=latitude, longitude=longitude, difficulty=difficulty,
        terrain=terrain, container=container, placed_by=placed_by,
        short_description=short_description, encoded_hints=encoded_hints,
        found=found, user_note=user_note, waypoints=waypoints or [],
    )


def _parse(path):
    return ET.fromstring(path.read_bytes())


# ── _esc ────────────────────────────────────────────────────────────────────

class TestEsc:
    def test_none_returns_empty(self):
        assert _esc(None) == ""

    def test_value_stringified(self):
        assert _esc(123) == "123"
        assert _esc("abc") == "abc"


# ── _cache_description ──────────────────────────────────────────────────────

class TestCacheDescription:
    def test_includes_type_dt_size(self):
        text = _cache_description(_cache(difficulty=1.5, terrain=4.0, container="Micro"))
        assert "Traditional Cache" in text
        assert "D1.5 / T4.0" in text
        assert "Micro" in text

    def test_unknown_dt_and_size_use_placeholder(self):
        text = _cache_description(_cache(difficulty=None, terrain=None, container=None))
        assert "D? / T?" in text
        assert "?" in text

    def test_placed_by_omitted_when_empty(self):
        assert "By:" not in _cache_description(_cache(placed_by=""))

    def test_short_description_included(self):
        assert "A nice cache" in _cache_description(_cache(short_description="A nice cache"))

    def test_hint_rot13_decoded(self):
        # Langt nok ROT13-eksempel til at heuristikken (split_hint) kan
        # afgøre retningen sikkert — simulerer en ægte ROT13-kodet hint fra
        # en gammel GSAK-eksport.
        rot13_text = "Haqre n ynetr ebpx sbezngvba arne gur byq bnx gerr"
        text = _cache_description(_cache(encoded_hints=rot13_text))
        assert "Under a large rock formation near the old oak tree" in text

    def test_short_hint_defaults_to_plaintext(self):
        # Regression for #329: korte hints er under heuristikkens tærskel
        # og antages bevidst at være klartekst (den almindelige case for
        # moderne geocaching.com-data) — selv hvis de ligner ROT13.
        text = _cache_description(_cache(encoded_hints="Haqre n ebpx"))
        assert "Haqre n ebpx" in text

    def test_hint_already_plaintext_kept_as_is(self):
        # Regression for #329: geocaching.com leverer i dag hints i
        # klartekst. KML-eksporten skal IKKE rode en allerede læsbar hint
        # til gibberish ved at antage den er ROT13-kodet.
        text = _cache_description(_cache(encoded_hints="Under a rock"))
        assert "Under a rock" in text

    def test_hint_decode_failure_falls_back(self):
        # bytes can't be rot_13-decoded → except branch keeps the raw value
        text = _cache_description(_cache(encoded_hints=b"\xff\xfe"))
        assert "Hint:" in text

    def test_hint_br_markup_rendered_as_html_line_break(self):
        # Issue #595: [br] must become a real <br/> in the HTML popup, not
        # show up literally (and definitely not as its ROT13'd "[oe]").
        text = _cache_description(_cache(encoded_hints="Under a rock [br] second line"))
        assert "<br/>" in text.split("Hint:")[1]
        assert "[br]" not in text
        assert "[oe]" not in text

    def test_gc_link_present(self):
        text = _cache_description(_cache(gc_code="GCABCDE"))
        assert "geocaching.com/geocache/GCABCDE" in text


# ── _waypoint_description ───────────────────────────────────────────────────

class TestWaypointDescription:
    def test_full(self):
        text = _waypoint_description(_wpt(), "GC12345")
        assert "Parking Area" in text
        assert "desc" in text
        assert "note" in text
        assert "(GC12345)" in text

    def test_minimal_only_gc_code(self):
        text = _waypoint_description(
            _wpt(wp_type=None, description=None, comment=None), "GC1"
        )
        assert text == "(GC1)"


# ── style ids ───────────────────────────────────────────────────────────────

class TestStyleIds:
    def test_cache_found(self):
        assert _style_id_for_cache(_cache(found=True)) == "style_found"

    def test_cache_type_normalised(self):
        assert _style_id_for_cache(_cache(cache_type="Multi-cache")) == "style_Multi_cache"

    def test_cache_empty_type_default(self):
        assert _style_id_for_cache(_cache(cache_type="")) == "style_default"

    def test_waypoint_type_normalised(self):
        assert _style_id_for_waypoint(_wpt(wp_type="Parking Area")) == "style_wpt_Parking_Area"

    def test_waypoint_empty_type_default(self):
        assert _style_id_for_waypoint(_wpt(wp_type="")) == "style_wpt_default"


# ── export_kml ──────────────────────────────────────────────────────────────

class TestExportKml:
    def test_returns_count_and_writes_file(self, tmp_path):
        out = tmp_path / "out.kml"
        count = export_kml([_cache(), _cache(gc_code="GC99999")], out)
        assert count == 2
        assert out.exists()
        assert _parse(out).tag == f"{KML_NS}kml"

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "nested" / "deep" / "out.kml"
        export_kml([_cache()], out)
        assert out.exists()

    def test_empty_list_valid_kml(self, tmp_path):
        out = tmp_path / "empty.kml"
        assert export_kml([], out) == 0
        assert _parse(out).tag == f"{KML_NS}kml"

    def test_gc_code_and_name_in_output(self, tmp_path):
        out = tmp_path / "out.kml"
        export_kml([_cache(gc_code="GCXYZ", name="My Cache")], out)
        body = out.read_text(encoding="utf-8")
        assert "GCXYZ" in body
        assert "My Cache" in body

    def test_found_cache_marked_and_styled(self, tmp_path):
        out = tmp_path / "out.kml"
        export_kml([_cache(found=True)], out)
        body = out.read_text(encoding="utf-8")
        assert "✓" in body
        assert "#style_found" in body

    def test_include_found_false_filters(self, tmp_path):
        out = tmp_path / "out.kml"
        count = export_kml(
            [_cache(gc_code="GCFOUND", found=True), _cache(gc_code="GCNEW")],
            out, include_found=False,
        )
        assert count == 1
        assert "GCFOUND" not in out.read_text(encoding="utf-8")

    def test_corrected_coords_used(self, tmp_path):
        out = tmp_path / "out.kml"
        c = _cache(latitude=55.0, longitude=12.0, user_note=_note(corrected_lat=60.0, corrected_lon=20.0))
        export_kml([c], out)
        assert "20.0,60.0,0" in out.read_text(encoding="utf-8")

    def test_uncorrected_note_uses_original(self, tmp_path):
        out = tmp_path / "out.kml"
        c = _cache(latitude=55.0, longitude=12.0, user_note=_note(is_corrected=False))
        export_kml([c], out)
        assert "12.0,55.0,0" in out.read_text(encoding="utf-8")

    def test_cache_without_coords_skipped(self, tmp_path):
        out = tmp_path / "out.kml"
        c = _cache(gc_code="GCNULL", latitude=None, longitude=None)
        export_kml([_cache(gc_code="GCOK"), c], out)
        body = out.read_text(encoding="utf-8")
        # still counted in the header total, but no placemark written
        placemarks = _parse(out).iter(f"{KML_NS}Placemark")
        names = [p.find(f"{KML_NS}name").text for p in placemarks]
        assert any("GCOK" in n for n in names)
        assert not any("GCNULL" in n for n in names)

    def test_waypoints_folder_written(self, tmp_path):
        out = tmp_path / "out.kml"
        c = _cache(waypoints=[_wpt(name="Parking", wp_type="Parking Area")])
        export_kml([c], out)
        body = out.read_text(encoding="utf-8")
        assert "Waypoints" in body
        assert "Parking" in body

    def test_include_waypoints_false_skips_folder(self, tmp_path):
        out = tmp_path / "out.kml"
        c = _cache(waypoints=[_wpt(name="Parking")])
        export_kml([c], out, include_waypoints=False)
        folder_names = [f.find(f"{KML_NS}name").text for f in _parse(out).iter(f"{KML_NS}Folder")]
        assert "Waypoints" not in folder_names

    def test_waypoint_without_coords_skipped(self, tmp_path):
        out = tmp_path / "out.kml"
        c = _cache(waypoints=[_wpt(name="NoCoords", latitude=None, longitude=None)])
        export_kml([c], out)
        # waypoint folder may exist (built from list) but no placemark for it
        body = out.read_text(encoding="utf-8")
        assert "NoCoords" not in body

    def test_special_chars_escaped_roundtrip(self, tmp_path):
        out = tmp_path / "out.kml"
        export_kml([_cache(name="A & B <test>")], out)
        names = [p.find(f"{KML_NS}name").text for p in _parse(out).iter(f"{KML_NS}Placemark")]
        assert any("A & B <test>" in n for n in names)

    def test_known_type_icon_present(self, tmp_path):
        out = tmp_path / "out.kml"
        export_kml([_cache(cache_type="EarthCache")], out)
        assert "grn-diamond.png" in out.read_text(encoding="utf-8")


# ── streaming (#1078) ───────────────────────────────────────────────────────

class TestExportKmlStreaming:
    """export_kml() writes one placemark at a time from any iterable,
    instead of building the whole document in memory (#1078)."""

    def test_generator_is_not_read_ahead_of_writing(self, tmp_path):
        # Regression for #1078: every cache was read (and kept) before any
        # KML was written. Now each cache's placemark is in the output
        # before the next cache is taken from the generator.
        buf = io.StringIO()

        def save(path, write, binary):
            assert binary is False
            return write(buf)

        def caches():
            for i in range(3):
                if i:
                    assert f"GC{i - 1} " in buf.getvalue()
                yield _cache(gc_code=f"GC{i}")

        count = export_kml(caches(), tmp_path / "out.kml", save=save, total=3, count=3)
        assert count == 3
        root = ET.fromstring(buf.getvalue())
        names = [p.find(f"{KML_NS}name").text for p in root.iter(f"{KML_NS}Placemark")]
        assert [n.split()[0] for n in names] == ["GC0", "GC1", "GC2"]

    def test_count_shown_in_description_and_progress_uses_total(self, tmp_path, monkeypatch):
        monkeypatch.setattr("opensak.export.kml.tr", lambda key, **kw: f"{key}={kw['count']}")
        out = tmp_path / "out.kml"
        seen = []
        caches = (_cache(gc_code=f"GC{i}", found=i == 0) for i in range(3))
        count = export_kml(caches, out, include_found=False, total=3, count=2,
                           progress_cb=lambda d, t: seen.append((d, t)))
        assert count == 2
        assert seen == [(1, 3), (2, 3), (3, 3)]
        desc = _parse(out).find(f"{KML_NS}Document/{KML_NS}description").text
        assert desc == "kml_export_description=2"

    def test_waypoints_folder_after_caches(self, tmp_path):
        out = tmp_path / "out.kml"
        caches = (
            _cache(gc_code=f"GC{i}", waypoints=[_wpt(name=f"W{i}")]) for i in range(2)
        )
        export_kml(caches, out, total=2, count=2)
        folders = list(_parse(out).iter(f"{KML_NS}Folder"))
        assert [f.find(f"{KML_NS}name").text for f in folders] == ["Geocaches", "Waypoints"]
        wpt_names = [p.find(f"{KML_NS}name").text
                     for p in folders[1].iter(f"{KML_NS}Placemark")]
        assert wpt_names == ["W0 (GC0)", "W1 (GC1)"]
        assert "W0" not in "".join(
            p.find(f"{KML_NS}name").text for p in folders[0].iter(f"{KML_NS}Placemark"))

    def test_failure_keeps_existing_file(self, tmp_path):
        def caches():
            yield _cache(gc_code="GC1", waypoints=[_wpt()])
            raise RuntimeError("halfway")
        out = tmp_path / "out.kml"
        out.write_bytes(b"previous export")
        with pytest.raises(RuntimeError, match="halfway"):
            export_kml(caches(), out, total=2, count=2)
        assert out.read_bytes() == b"previous export"
        assert list(tmp_path.iterdir()) == [out]

    def test_xml_declaration_and_no_minidom_blank_lines(self, tmp_path):
        out = tmp_path / "out.kml"
        export_kml([_cache()], out)
        body = out.read_text(encoding="utf-8")
        assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<kml ')
        assert "\n\n" not in body.replace("\r\n", "\n")

    def test_write_export_file_streams_kml(self, tmp_path, monkeypatch):
        # write_export_file() hands export_kml() the chunked reload
        # generator, not a fully reloaded list.
        from opensak.export.file_export import write_export_file
        import opensak.export.kml as kml_mod
        got = {}

        def fake_export_kml(caches, output_path, **kw):
            got["caches"], got["kw"] = caches, kw
            return 0
        monkeypatch.setattr(kml_mod, "export_kml", fake_export_kml)
        caches = [_cache(gc_code="GC1"), _cache(gc_code="GC2", latitude=None)]
        assert write_export_file(caches, tmp_path / "x.kml", "kml") == 1
        assert not isinstance(got["caches"], list)
        assert got["kw"]["total"] == 2 and got["kw"]["count"] == 2
