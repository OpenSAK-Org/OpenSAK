# tests/unit-tests/test_kmz_export_1361.py — #1361 (part 3): KML export as
# KMZ, a zip with the KML and its icons, so an offline viewer shows them.
# The icons are OpenSAK's own cache-type and waypoint icons rendered to PNG,
# not Google's. A plain KML still links Google's icons, now over https.

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from opensak.export.kml import KMZ_FILES_DIR, export_kml
from opensak.gui import icon_provider

KML_NS = "{http://www.opengis.net/kml/2.2}"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _wpt(wp_type="Parking Area", name="WP1"):
    return SimpleNamespace(name=name, wp_type=wp_type, latitude=55.0, longitude=12.0,
                           description="", comment="")


def _cache(gc_code="GC1", cache_type="Traditional Cache", found=False, waypoints=None):
    return SimpleNamespace(
        gc_code=gc_code, name="Test", cache_type=cache_type, latitude=55.6, longitude=12.5,
        difficulty=2.0, terrain=2.0, container="Small", placed_by="Owner",
        short_description="", encoded_hints=None, found=found, user_note=None,
        waypoints=waypoints or [],
    )


def _export(tmp_path, caches):
    out = tmp_path / "out.kmz"
    count = export_kml(caches, out, kmz=True)
    return out, count


def _doc(zf: zipfile.ZipFile) -> ET.Element:
    return ET.fromstring(zf.read("doc.kml"))


def _text(elem: ET.Element, path: str) -> str:
    found = elem.find(path)
    assert found is not None and found.text is not None, path
    return found.text


def _styles(doc: ET.Element) -> dict[str, str]:
    """style id → icon href"""
    return {
        st.get("id", ""): _text(st, f"{KML_NS}IconStyle/{KML_NS}Icon/{KML_NS}href")
        for st in doc.iter(f"{KML_NS}Style")
    }


def _style_of(doc: ET.Element, name_part: str) -> str:
    for pm in doc.iter(f"{KML_NS}Placemark"):
        if name_part in _text(pm, f"{KML_NS}name"):
            return _text(pm, f"{KML_NS}styleUrl").lstrip("#")
    raise AssertionError(name_part)


class TestKmz:
    def test_zip_with_kml_first(self, qapp, tmp_path):
        out, count = _export(tmp_path, [_cache()])
        assert count == 1
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            assert names[0] == "doc.kml"   # viewers open the first .kml
            assert all(n == "doc.kml" or n.startswith(f"{KMZ_FILES_DIR}/") for n in names)
            assert zf.testzip() is None

    def test_every_icon_is_inside_and_a_png(self, qapp, tmp_path):
        out, _ = _export(tmp_path, [_cache(waypoints=[_wpt()])])
        with zipfile.ZipFile(out) as zf:
            hrefs = set(_styles(_doc(zf)).values())
            assert hrefs
            for href in hrefs:
                assert zf.read(href).startswith(PNG_MAGIC), href

    def test_no_link_to_the_web(self, qapp, tmp_path):
        out, _ = _export(tmp_path, [_cache(found=True, waypoints=[_wpt()])])
        with zipfile.ZipFile(out) as zf:
            kml = zf.read("doc.kml").decode("utf-8")
        hrefs = re.findall(r"<href>([^<]+)</href>", kml)
        assert hrefs and not any("://" in h for h in hrefs)
        assert "maps.google.com" not in kml

    def test_one_style_per_opensak_type(self, qapp, tmp_path):
        out, _ = _export(tmp_path, [])
        with zipfile.ZipFile(out) as zf:
            styles = _styles(_doc(zf))
        for key in icon_provider.CACHE_TYPE_KEYS:
            assert styles[f"style_{key}"] == f"{KMZ_FILES_DIR}/{key}.png"
            assert styles[f"style_found_{key}"] == f"{KMZ_FILES_DIR}/{key}_found.png"
        for key in icon_provider.WAYPOINT_TYPE_KEYS:
            assert styles[f"style_wpt_{key}"] == f"{KMZ_FILES_DIR}/wpt_{key}.png"

    @pytest.mark.parametrize("cache_type, found, style", [
        ("Traditional Cache", False, "style_traditional"),
        ("Traditional Cache", True, "style_found_traditional"),   # type kept, smiley added
        ("Unknown Cache", False, "style_mystery"),
        ("Webcam Cache", True, "style_found_webcam"),             # not in the KML list
        ("Something New", False, "style_unknown"),
        (None, False, "style_unknown"),
    ])
    def test_cache_styles(self, qapp, tmp_path, cache_type, found, style):
        out, _ = _export(tmp_path, [_cache(cache_type=cache_type, found=found)])
        with zipfile.ZipFile(out) as zf:
            doc = _doc(zf)
        assert _style_of(doc, "GC1") == style
        assert style in _styles(doc)

    @pytest.mark.parametrize("wp_type, style", [
        ("Parking Area", "style_wpt_parking_area"),
        ("Parking", "style_wpt_parking_area"),
        ("Final Location", "style_wpt_final_location"),
        ("Stages of a Multicache", "style_wpt_stage"),
        ("Hotel/POI", "style_wpt_hotel_poi"),
        ("Listed Coordinates", "style_wpt_reference_point"),
        ("Question to Answer", "style_wpt_waypoint"),
        (None, "style_wpt_waypoint"),
    ])
    def test_waypoint_styles(self, qapp, tmp_path, wp_type, style):
        out, _ = _export(tmp_path, [_cache(waypoints=[_wpt(wp_type, name="WPX")])])
        with zipfile.ZipFile(out) as zf:
            doc = _doc(zf)
        assert _style_of(doc, "WPX") == style

    def test_found_icon_has_its_hot_spot_on_the_type_icon(self, qapp, tmp_path):
        out, _ = _export(tmp_path, [])
        with zipfile.ZipFile(out) as zf:
            doc = _doc(zf)
        spots = {st.get("id"): st.find(f"{KML_NS}IconStyle/{KML_NS}hotSpot")
                 for st in doc.iter(f"{KML_NS}Style")}
        assert spots["style_traditional"] is None
        spot = spots["style_found_traditional"]
        assert spot is not None and spot.get("xunits") == "fraction"
        assert float(spot.get("x")) == pytest.approx(0.4, abs=0.01)

    def test_user_icon_is_used(self, qapp, tmp_path, monkeypatch):
        # #519: the user's own icons apply, as everywhere else.
        rendered: list[str] = []
        real = icon_provider._get_svg_for_key
        monkeypatch.setattr(icon_provider, "_get_svg_for_key",
                            lambda key: rendered.append(key) or real(key))
        _export(tmp_path, [])
        assert "traditional" in rendered and "parking_area" in rendered

    def test_kml_streams_into_the_zip(self, qapp, tmp_path):
        caches = (_cache(gc_code=f"GC{i}") for i in range(50))
        out = tmp_path / "out.kmz"
        assert export_kml(caches, out, kmz=True, total=50, count=50) == 50
        with zipfile.ZipFile(out) as zf:
            assert len(list(_doc(zf).iter(f"{KML_NS}Placemark"))) == 50

    def test_failure_keeps_existing_file(self, qapp, tmp_path):
        out = tmp_path / "out.kmz"
        out.write_bytes(b"old")

        def broken():
            yield _cache()
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            export_kml(broken(), out, kmz=True, total=2, count=2)
        assert out.read_bytes() == b"old"


class TestPlainKml:
    def test_google_icons_over_https(self, tmp_path):
        out = tmp_path / "out.kml"
        export_kml([_cache(found=True, waypoints=[_wpt()])], out)
        hrefs = re.findall(r"<href>([^<]+)</href>", out.read_text(encoding="utf-8"))
        assert hrefs and all(h.startswith("https://maps.google.com/") for h in hrefs)

    def test_not_a_zip(self, tmp_path):
        out = tmp_path / "out.kml"
        export_kml([_cache()], out)
        assert not zipfile.is_zipfile(out)


class TestRenderIconPng:
    def test_plain_and_found(self, qapp):
        plain = icon_provider.render_icon_png("traditional", 64)
        found = icon_provider.render_icon_png("traditional", 64, found=True)
        assert plain.startswith(PNG_MAGIC) and found.startswith(PNG_MAGIC)
        from PySide6.QtGui import QImage
        assert QImage.fromData(plain).size().width() == 64
        assert QImage.fromData(found).size().width() == 80   # room for the smiley

    def test_on_a_worker_thread(self, qapp):
        # The KML dialog exports on a QThread: no QPixmap may be involved.
        import threading
        result: list[bytes] = []
        t = threading.Thread(target=lambda: result.append(
            icon_provider.render_icon_png("project_ape", 64, found=True)))
        t.start()
        t.join()
        assert result[0].startswith(PNG_MAGIC)


class TestDialog:
    def test_checkbox_switches_the_extension(self, qtbot):
        from opensak.gui.dialogs.kml_export_dialog import KmlExportDialog
        dlg = KmlExportDialog([])
        qtbot.addWidget(dlg)
        assert not dlg._cb_kmz.isChecked()
        dlg._path_edit.setText("/tmp/caches.kml")
        dlg._cb_kmz.setChecked(True)
        assert dlg._path_edit.text() == "/tmp/caches.kmz"
        dlg._cb_kmz.setChecked(False)
        assert dlg._path_edit.text() == "/tmp/caches.kml"

    def test_browse_appends_kmz(self, qtbot, monkeypatch):
        from opensak.gui.dialogs import kml_export_dialog as ked
        dlg = ked.KmlExportDialog([])
        qtbot.addWidget(dlg)
        dlg._cb_kmz.setChecked(True)
        filters: list[str] = []
        monkeypatch.setattr(ked.QFileDialog, "getSaveFileName",
                            lambda *a, **k: (filters.append(a[3]) or ("/tmp/foo", "")))
        dlg._browse()
        assert dlg._path_edit.text() == "/tmp/foo.kmz"
        assert "*.kmz" in filters[0] or filters[0] == "kml_dialog_file_filter_kmz"

    def test_worker_gets_the_choice(self, qtbot, monkeypatch):
        from opensak.gui.dialogs import kml_export_dialog as ked
        dlg = ked.KmlExportDialog([])
        qtbot.addWidget(dlg)
        seen: dict = {}

        class FakeWorker:
            def __init__(self, **kw):
                seen.update(kw)
                self.finished = self.error = self.cancelled = self.progress = \
                    SimpleNamespace(connect=lambda *_: None)

            def start(self):
                pass

        monkeypatch.setattr(ked, "_ExportWorker", FakeWorker)
        dlg._cb_kmz.setChecked(True)
        dlg._start_export()
        assert seen["kmz"] is True and seen["output_path"].endswith(".kmz")

    @pytest.mark.parametrize("path, kmz, result", [
        ("a.kml", True, "a.kmz"),
        ("a.KMZ", False, "a.kml"),
        ("a", True, "a.kmz"),
        ("a.txt", True, "a.txt"),
        ("", True, ""),
    ])
    def test_with_export_suffix(self, path, kmz, result):
        from opensak.gui.dialogs.kml_export_dialog import with_export_suffix
        assert with_export_suffix(path, kmz) == result
