# tests/unit-tests/test_geo_deps.py — shapely import and boundary engine smoke test.

import shapely
from opensak.geo.boundaries import _HAS_SHAPELY, _point_in_geometry


def test_shapely_importable() -> None:
    assert shapely.__version__


def test_geo_module_uses_shapely() -> None:
    assert _HAS_SHAPELY, "shapely installed but not detected by geo.boundaries"


def test_shapely_point_inside_triangle() -> None:
    tri = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [0, 2], [0, 0]]]}
    assert _point_in_geometry(0.5, 0.5, tri) is True


def test_shapely_point_outside_triangle() -> None:
    tri = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [0, 2], [0, 0]]]}
    assert _point_in_geometry(1.5, 1.5, tri) is False


def test_shapely_multipolygon() -> None:
    mp = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            [[[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]],
        ],
    }
    assert _point_in_geometry(0.5, 0.5, mp) is True
    assert _point_in_geometry(5.5, 5.5, mp) is True
    assert _point_in_geometry(3.0, 3.0, mp) is False
