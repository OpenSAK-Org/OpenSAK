# tests/unit-tests/test_startup_log_privacy_1046.py
"""
Issue #1046: startup debug logging must not dump the whole settings store.
opensak.log is attached to public GitHub issues when startup fails, so
personal settings (PQ e-mail account, home location, Geocaching.com
username and finder ID) must never end up in it — while the database
settings needed to diagnose loading problems (#878, #1045) still must.
"""

import json
import logging

import pytest

pytest.importorskip("pytestqt")

from opensak import settings_store as ss
from opensak.db.manager import DatabaseManager

SECRETS = {
    "pq_email.username": "secret.mailbox@example.org",
    "pq_email.host": "imap.secret-host.example",
    "user.gc_username": "SecretCacher",
    "user.gc_finder_id": "SECRETFINDER42",
    "user.gc_home_location": "N 55° 42.123 E 011° 43.456",
}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def capture():
    """Collect everything the two startup debug loggers emit at DEBUG."""
    handler = _Capture()
    loggers = [logging.getLogger("opensak.settings_migration"),
               logging.getLogger("opensak.db_manager")]
    saved = [(lg, lg.level) for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
    yield handler
    for lg, level in saved:
        lg.removeHandler(handler)
        lg.setLevel(level)


@pytest.fixture
def store(tmp_path, qapp, monkeypatch):
    """A settings store on disk with personal settings and one database."""
    db_path = tmp_path / "MyCaches.db"
    data = {
        **SECRETS,
        "_wizard_completed": True,
        "_migrated_from_qsettings": True,
        "databases.dir": str(tmp_path),
        "databases.list": [{"name": "MyCaches", "path": str(db_path)}],
        "databases.active": str(db_path),
    }
    settings_file = tmp_path / "opensak.json"
    settings_file.write_text(json.dumps(data), encoding="utf-8")

    fresh = ss.SettingsStore()  # _data is None: loads settings_file on first use
    fresh._path = settings_file
    monkeypatch.setattr(ss, "_store", fresh)
    monkeypatch.setattr(fresh, "_settings_path", lambda: settings_file)
    return fresh


def _run_startup_logging(store) -> None:
    ss.migrate_from_qsettings(store)  # already migrated → logs, then returns
    ss.is_first_run()
    DatabaseManager()


def test_personal_settings_are_not_logged(store, capture):
    _run_startup_logging(store)

    assert capture.messages, "expected startup debug output"
    text = "\n".join(capture.messages)
    for key, value in SECRETS.items():
        assert value not in text, f"{key} leaked into the startup log"
        assert key not in text, f"{key} leaked into the startup log"


def test_database_settings_are_still_logged(store, capture, tmp_path):
    _run_startup_logging(store)

    text = "\n".join(capture.messages)
    assert str(tmp_path / "MyCaches.db") in text
    assert "_wizard_completed" in text


def test_summary_contains_only_safe_keys(store):
    store.get("databases.active")  # load from disk
    summary = ss.settings_summary_for_log(store)

    assert set(summary) <= {
        "databases.active", "databases.dir", "databases.list",
        "_wizard_completed", "_migrated_from_qsettings", "key_count",
    }
    assert summary["key_count"] == len(store._data)


def test_summary_before_load_is_empty(qapp):
    fresh = ss.SettingsStore()
    assert fresh._data is None
    assert ss.settings_summary_for_log(fresh) == {"key_count": 0}
