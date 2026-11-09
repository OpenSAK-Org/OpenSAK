# tests/unit-tests/test_usage_1050.py — "Using OpenSAK since … — day N" in Help → About (#1050).

import sqlite3
from datetime import date

import pytest

from opensak import usage
from opensak.settings_store import get_store

TODAY = date(2026, 10, 19)


def _db_with_imports(path, *imported_at):
    """A minimal database with a caches table, rows inserted in the given order."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE caches (id INTEGER PRIMARY KEY, imported_at DATETIME)")
    con.executemany("INSERT INTO caches (imported_at) VALUES (?)",
                    [(v,) for v in imported_at])
    con.commit()
    con.close()
    return path


class TestParseDate:
    @pytest.mark.parametrize("value, expected", [
        ("2025-03-04", date(2025, 3, 4)),
        ("2025-03-04 12:34:56.789000", date(2025, 3, 4)),   # SQLite DateTime
        ("2025-03-04T12:34:56", date(2025, 3, 4)),
        ("garbage!!", None), ("2025-13-40", None), ("", None),
        (None, None), (20250304, None), ([], None),
    ])
    def test_parse(self, value, expected):
        assert usage._parse_date(value) == expected


class TestEnsureFirstStarted:
    def test_fresh_install_records_today(self):
        assert usage.ensure_first_started([], today=TODAY) == TODAY
        assert get_store().get(usage.FIRST_STARTED_KEY) == "2026-10-19"

    def test_backdates_to_oldest_import(self, tmp_path):
        a = _db_with_imports(tmp_path / "a.sqlite", "2025-06-01 10:00:00", "2026-01-01 10:00:00")
        b = _db_with_imports(tmp_path / "b.sqlite", "2024-11-12 08:00:00")
        assert usage.ensure_first_started([a, b], today=TODAY) == date(2024, 11, 12)

    def test_uses_first_imported_row_not_later_rows(self, tmp_path):
        # Lowest id = first import; a later row with an older-looking date
        # (clock skew, odd data) doesn't count. Keeps it a PK lookup.
        db = _db_with_imports(tmp_path / "x.sqlite", "2025-06-01", "2020-01-01")
        assert usage.ensure_first_started([db], today=TODAY) == date(2025, 6, 1)

    def test_only_recorded_once(self, tmp_path):
        usage.ensure_first_started([], today=TODAY)
        old = _db_with_imports(tmp_path / "old.sqlite", "2020-01-01")
        assert usage.ensure_first_started([old], today=date(2027, 1, 1)) == TODAY

    def test_future_import_dates_ignored(self, tmp_path):
        db = _db_with_imports(tmp_path / "f.sqlite", "2099-01-01")
        assert usage.ensure_first_started([db], today=TODAY) == TODAY

    def test_bad_databases_ignored(self, tmp_path):
        missing = tmp_path / "missing.sqlite"
        junk = tmp_path / "junk.sqlite"
        junk.write_bytes(b"this is not sqlite")
        empty = _db_with_imports(tmp_path / "empty.sqlite")
        no_table = tmp_path / "no_table.sqlite"
        sqlite3.connect(no_table).close()
        nulls = _db_with_imports(tmp_path / "nulls.sqlite", None)
        assert usage.ensure_first_started(
            [missing, junk, empty, no_table, nulls, tmp_path], today=TODAY
        ) == TODAY

    def test_corrupt_stored_value_is_replaced(self):
        get_store().set(usage.FIRST_STARTED_KEY, "not a date")
        assert usage.ensure_first_started([], today=TODAY) == TODAY

    def test_real_schema_has_the_columns_we_read(self):
        from opensak.db.models import Cache
        assert Cache.__tablename__ == "caches"
        assert {"id", "imported_at"} <= set(Cache.__table__.columns.keys())

    def test_real_database(self, tmp_path):
        from opensak.db.database import init_db, get_session
        from opensak.db.models import Cache
        from datetime import datetime
        db = tmp_path / "real.sqlite"
        init_db(db_path=db)
        with get_session() as s:
            s.add(Cache(gc_code="GC1050", name="Days used",
                        cache_type="Traditional Cache", latitude=55.0, longitude=12.0,
                        imported_at=datetime(2025, 2, 3, 4, 5, 6)))
        assert usage.ensure_first_started([db], today=TODAY) == date(2025, 2, 3)


class TestDayNumber:
    def test_first_day_is_day_one(self):
        assert usage.day_number(TODAY, today=TODAY) == 1

    def test_counts_days(self):
        assert usage.day_number(date(2026, 10, 9), today=TODAY) == 11

    def test_never_below_one(self):
        assert usage.day_number(date(2030, 1, 1), today=TODAY) == 1


class TestAboutDialog:
    @pytest.fixture
    def shown(self, monkeypatch):
        pytest.importorskip("pytestqt")
        from opensak.gui import mainwindow
        from opensak.lang import load_language
        load_language("en")
        texts = []
        monkeypatch.setattr(mainwindow.QMessageBox, "about",
                            lambda parent, title, text: texts.append(text))
        return texts

    def test_about_shows_since_line(self, qtbot, shown):
        from opensak.gui.mainwindow import MainWindow
        from opensak.gui.date_format import format_date
        from opensak.gui.settings import get_settings
        from opensak.lang import tr
        today = date.today()
        get_store().set(usage.FIRST_STARTED_KEY, today.isoformat())
        MainWindow._show_about(None)  # self is only used as the box's parent
        since = tr("about_using_since",
                   date=format_date(today, get_settings().date_format), day=1)
        assert shown[0].endswith(f"<p><i>{since}</i></p>")

    def test_about_without_date_has_no_since_line(self, qtbot, shown):
        from opensak.gui.mainwindow import MainWindow
        from opensak.lang import tr
        MainWindow._show_about(None)
        assert shown[0] == tr("about_text", version=__import__("opensak").__version__)

    def test_about_links_to_current_repo(self, qtbot, shown):
        from opensak.gui.mainwindow import MainWindow
        MainWindow._show_about(None)
        assert "github.com/OpenSAK-Org/OpenSAK" in shown[0]
        assert "AgreeDK/opensak" not in shown[0]
