# tests/unit-tests/test_trackable_br_migration_1331.py — migration 29 (#1331):
# strip GSAK's leading "<BR>" from trackable names imported before the fix.

from pathlib import Path

from sqlalchemy import text

from opensak.db.database import SCHEMA_VERSION, _run_migrations, get_engine, init_db


def _make_v28_db(path: Path) -> None:
    """A database as a GSAK import before #1331 left it: trackable names
    starting with <BR>, user_version 28."""
    init_db(db_path=path)
    with get_engine().connect() as c:
        c.execute(text(
            "INSERT INTO caches (gc_code, name, cache_type, latitude, longitude) "
            "VALUES ('GC1TB', 'With bugs', 'Traditional Cache', 47.0, 8.0)"
        ))
        for name in (
            "<BR>Hedgie 1",
            "<br/><BR> Block Party Nano (null)",
            "Clean name",
            "Name with <BR> inside",
        ):
            c.execute(text(
                "INSERT INTO trackables (cache_id, name, ref) VALUES (1, :name, 'TB1')"
            ), {"name": name})
        c.execute(text("PRAGMA user_version = 28"))
        c.commit()


def _names() -> list[str]:
    with get_engine().connect() as c:
        return [r[0] for r in c.execute(text("SELECT name FROM trackables ORDER BY id"))]


def test_schema_version():
    assert SCHEMA_VERSION >= 29


def test_strips_leading_br_from_existing_names(tmp_path):
    _make_v28_db(tmp_path / "v28.db")
    _run_migrations(get_engine())
    assert _names() == [
        "Hedgie 1",
        "Block Party Nano (null)",
        "Clean name",
        "Name with <BR> inside",
    ]
    with get_engine().connect() as c:
        assert c.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION


def test_rerun_is_a_no_op(tmp_path):
    _make_v28_db(tmp_path / "v28.db")
    engine = get_engine()
    _run_migrations(engine)
    with engine.connect() as c:
        c.execute(text("PRAGMA user_version = 28"))
        c.commit()
    _run_migrations(engine)
    assert _names() == [
        "Hedgie 1",
        "Block Party Nano (null)",
        "Clean name",
        "Name with <BR> inside",
    ]
