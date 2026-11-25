# tests/unit-tests/test_virtual_container_1372.py — issue #1372: the GSAK
# database import rewrote the container "Virtual" to "Not chosen", while the
# GPX import kept "Virtual", so the same cache got a different container
# depending on where it came from. "Virtual" is a real geocaching.com size.
# The import tests themselves are in test_gsak_importer.py.

from pathlib import Path

from sqlalchemy import text

from opensak.db.database import SCHEMA_VERSION, _run_migrations, get_engine, init_db
from opensak.filters.engine import ContainerFilter
from opensak.importer.gsak_filter_importer import (
    Options,
    convert,
    parse_filter_blob,
)
from opensak.utils.constants import CONTAINER_SIZES


def test_virtual_is_a_container_size():
    assert "Virtual" in CONTAINER_SIZES


def test_container_filter_selects_virtual(make_cache):
    virtual = make_cache("GCV", cache_type="Virtual Cache", container="Virtual")
    not_chosen = make_cache("GCN", cache_type="Traditional Cache", container="Not chosen")
    f = ContainerFilter(["Virtual"])
    assert f.matches(virtual)
    assert not f.matches(not_chosen)


def _convert_containers(selected: set[str]):
    keys = ("cbxMicro", "cbxSmall", "cbxRegular", "cbxLarge", "cbxOther",
            "cbxNotChosen", "cbxVirtual", "chkUnknown")
    blob = "\r\n".join(f"{k}={'True' if k in selected else 'False'}" for k in keys)
    return convert(parse_filter_blob("T", blob), Options())


def test_filter_importer_maps_cbx_virtual_to_virtual():
    c = _convert_containers({"cbxVirtual"})
    containers = [f for f in c.filters if isinstance(f, ContainerFilter)]
    assert len(containers) == 1
    assert containers[0].sizes == ["Virtual"]
    assert not any("cbxVirtual" in n for n in c.notes)


def test_filter_importer_still_collapses_unknown():
    c = _convert_containers({"chkUnknown"})
    containers = [f for f in c.filters if isinstance(f, ContainerFilter)]
    assert containers[0].sizes == ["Not chosen"]
    assert any("chkUnknown" in n for n in c.notes)


# ── Migration 30: existing GSAK-imported virtuals get "Virtual" back ─────────

_ROWS = [
    ("GCVIRT", "Virtual Cache", "Not chosen"),
    ("GCWEBC", "Webcam Cache", "Not chosen"),
    ("GCLAB", "Lab Cache", "Not chosen"),
    ("GCTRAD", "Traditional Cache", "Not chosen"),
    ("GCMYST", "Unknown Cache", "Not chosen"),
    ("GCVSML", "Virtual Cache", "Small"),
]


def _make_v29_db(path: Path) -> None:
    init_db(db_path=path)
    with get_engine().connect() as c:
        for gc_code, cache_type, container in _ROWS:
            c.execute(text(
                "INSERT INTO caches (gc_code, name, cache_type, container, latitude, longitude) "
                "VALUES (:gc, :gc, :type, :cont, 47.0, 8.0)"
            ), {"gc": gc_code, "type": cache_type, "cont": container})
        c.execute(text("PRAGMA user_version = 29"))
        c.commit()


def _containers() -> dict[str, str]:
    with get_engine().connect() as c:
        return dict(c.execute(text("SELECT gc_code, container FROM caches")).fetchall())


_EXPECTED = {
    "GCVIRT": "Virtual",
    "GCWEBC": "Virtual",
    "GCLAB": "Virtual",
    "GCTRAD": "Not chosen",
    "GCMYST": "Not chosen",
    "GCVSML": "Small",
}


def test_schema_version():
    assert SCHEMA_VERSION >= 30


def test_migration_sets_virtual_on_existing_rows(tmp_path):
    _make_v29_db(tmp_path / "v29.db")
    _run_migrations(get_engine())
    assert _containers() == _EXPECTED
    with get_engine().connect() as c:
        assert c.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION


def test_migration_rerun_is_a_no_op(tmp_path):
    _make_v29_db(tmp_path / "v29.db")
    engine = get_engine()
    for _ in range(2):
        with engine.connect() as c:
            c.execute(text("PRAGMA user_version = 29"))
            c.commit()
        _run_migrations(engine)
    assert _containers() == _EXPECTED
