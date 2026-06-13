"""Smoke-cover the export package's public API surface."""

import opensak.export as export
from opensak.export import export_kml


def test_export_package_reexports_export_kml():
    assert export.__all__ == ["export_kml"]
    assert export.export_kml is export_kml
    assert callable(export_kml)
