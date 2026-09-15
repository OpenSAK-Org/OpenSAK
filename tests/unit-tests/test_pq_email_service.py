# tests/unit-tests/test_pq_email_service.py — mailbox-scan orchestration
# (issue #443, session 2). Everything external (IMAP, the importer, the
# database manager) is mocked; no real network or database is touched.

from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from opensak.db.manager import DatabaseInfo
from opensak.email.connection import ImapConfig
from opensak.email.service import _match_database, scan_and_import

_CONFIG = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")


class FakeImportResult:
    def __init__(self, created=0, updated=0, waypoints=0, errors=None):
        self.created = created
        self.updated = updated
        self.waypoints = waypoints
        self.errors = errors or []

    @property
    def total(self):
        return self.created + self.updated


def _zip_email_bytes(filename: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = "pq-club@example.com"
    msg["Subject"] = "Your PQ"
    msg.set_content("body")
    msg.add_attachment(b"PK-fake-zip", maintype="application", subtype="zip", filename=filename)
    return msg.as_bytes()


def _no_attachment_email_bytes() -> bytes:
    msg = EmailMessage()
    msg["From"] = "someone@example.com"
    msg.set_content("just text")
    return msg.as_bytes()


@contextmanager
def _fake_get_session():
    yield MagicMock()


class TestMatchDatabase:
    def test_matches_by_exact_name(self, monkeypatch):
        north = DatabaseInfo("North", Path("/dbs/north.sqlite"))
        south = DatabaseInfo("South", Path("/dbs/south.sqlite"))
        manager = MagicMock()
        manager.databases = [north, south]
        manager.active = north
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)

        assert _match_database("South") is south

    def test_matches_case_insensitively(self, monkeypatch):
        north = DatabaseInfo("North", Path("/dbs/north.sqlite"))
        manager = MagicMock()
        manager.databases = [north]
        manager.active = north
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)

        assert _match_database("north") is north

    def test_falls_back_to_active_when_no_name_match(self, monkeypatch):
        north = DatabaseInfo("North", Path("/dbs/north.sqlite"))
        manager = MagicMock()
        manager.databases = [north]
        manager.active = north
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)

        assert _match_database("Unrelated Name") is north


class TestScanAndImport:
    def _setup_common_mocks(self, monkeypatch, import_result=None, dbs=None):
        north = DatabaseInfo("North", Path("/dbs/north.sqlite"))
        manager = MagicMock()
        manager.databases = dbs if dbs is not None else [north]
        manager.active = (dbs[0] if dbs else north)
        manager.active_path = manager.active.path
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        monkeypatch.setattr("opensak.db.database.get_session", _fake_get_session)
        monkeypatch.setattr("opensak.db.database.init_db", lambda db_path=None: None)
        monkeypatch.setattr(
            "opensak.importer.import_zip",
            lambda *a, **kw: import_result if import_result is not None else FakeImportResult(created=1),
        )
        return manager

    def test_empty_inbox_returns_no_outcomes(self, monkeypatch):
        self._setup_common_mocks(monkeypatch)
        conn = MagicMock()
        conn.search.return_value = ("OK", [b""])
        monkeypatch.setattr("opensak.email.service.connect", lambda *a, **kw: conn)

        result = scan_and_import(_CONFIG, "pw")

        assert result.outcomes == []
        conn.logout.assert_called_once()

    def test_email_without_zip_is_ignored(self, monkeypatch):
        self._setup_common_mocks(monkeypatch)
        conn = MagicMock()
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = ("OK", [(b"1 (RFC822 {n}", _no_attachment_email_bytes())])
        monkeypatch.setattr("opensak.email.service.connect", lambda *a, **kw: conn)

        result = scan_and_import(_CONFIG, "pw")

        assert result.outcomes == []
        conn.store.assert_not_called()

    def test_successful_import_recorded_and_not_deleted_by_default(self, monkeypatch):
        self._setup_common_mocks(monkeypatch, import_result=FakeImportResult(created=3))
        conn = MagicMock()
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = (
            "OK", [(b"1 (RFC822 {n}", _zip_email_bytes("North.zip"))]
        )
        monkeypatch.setattr("opensak.email.service.connect", lambda *a, **kw: conn)

        result = scan_and_import(_CONFIG, "pw", delete_after_import=False)

        assert len(result.outcomes) == 1
        outcome = result.outcomes[0]
        assert outcome.imported is True
        assert outcome.created == 3
        assert outcome.matched_db_name == "North"
        assert outcome.deleted_email is False
        assert outcome.marked_seen is True
        conn.store.assert_called_once_with("1", "+FLAGS", "\\Seen")
        conn.expunge.assert_not_called()

    def test_successful_import_deleted_when_opted_in(self, monkeypatch):
        self._setup_common_mocks(monkeypatch, import_result=FakeImportResult(created=1))
        conn = MagicMock()
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = (
            "OK", [(b"1 (RFC822 {n}", _zip_email_bytes("North.zip"))]
        )
        monkeypatch.setattr("opensak.email.service.connect", lambda *a, **kw: conn)

        result = scan_and_import(_CONFIG, "pw", delete_after_import=True)

        assert result.outcomes[0].deleted_email is True
        conn.store.assert_called_once_with("1", "+FLAGS", "\\Deleted")
        conn.expunge.assert_called_once()

    def test_failed_import_not_deleted_even_when_opted_in(self, monkeypatch):
        self._setup_common_mocks(
            monkeypatch,
            import_result=FakeImportResult(errors=["No .gpx file found in zip"]),
        )
        conn = MagicMock()
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = (
            "OK", [(b"1 (RFC822 {n}", _zip_email_bytes("Bad.zip"))]
        )
        monkeypatch.setattr("opensak.email.service.connect", lambda *a, **kw: conn)

        result = scan_and_import(_CONFIG, "pw", delete_after_import=True)

        outcome = result.outcomes[0]
        assert outcome.imported is False
        assert outcome.deleted_email is False
        assert outcome.marked_seen is False
        conn.store.assert_not_called()

    def test_exception_during_import_recorded_as_failed_outcome(self, monkeypatch):
        self._setup_common_mocks(monkeypatch)
        monkeypatch.setattr(
            "opensak.importer.import_zip",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disk full")),
        )
        conn = MagicMock()
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = (
            "OK", [(b"1 (RFC822 {n}", _zip_email_bytes("North.zip"))]
        )
        monkeypatch.setattr("opensak.email.service.connect", lambda *a, **kw: conn)

        result = scan_and_import(_CONFIG, "pw")

        outcome = result.outcomes[0]
        assert outcome.imported is False
        assert "disk full" in outcome.errors[0]

    def test_multiple_zip_attachments_in_one_email_each_get_an_outcome(self, monkeypatch):
        self._setup_common_mocks(monkeypatch, import_result=FakeImportResult(created=1))
        msg = EmailMessage()
        msg.set_content("body")
        msg.add_attachment(b"PK-1", maintype="application", subtype="zip", filename="North.zip")
        msg.add_attachment(b"PK-2", maintype="application", subtype="zip", filename="South.zip")
        conn = MagicMock()
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = ("OK", [(b"1 (RFC822 {n}", msg.as_bytes())])
        monkeypatch.setattr("opensak.email.service.connect", lambda *a, **kw: conn)

        result = scan_and_import(_CONFIG, "pw")

        assert {o.zip_filename for o in result.outcomes} == {"North.zip", "South.zip"}

    def test_auth_error_propagates_from_connect(self, monkeypatch):
        from opensak.email.connection import ImapAuthError

        def _raise(*a, **kw):
            raise ImapAuthError("bad login")

        monkeypatch.setattr("opensak.email.service.connect", _raise)

        with pytest.raises(ImapAuthError):
            scan_and_import(_CONFIG, "wrong")

    def test_logout_is_always_called_even_on_search_failure(self, monkeypatch):
        self._setup_common_mocks(monkeypatch)
        conn = MagicMock()
        conn.search.side_effect = RuntimeError("boom")
        monkeypatch.setattr("opensak.email.service.connect", lambda *a, **kw: conn)

        with pytest.raises(RuntimeError):
            scan_and_import(_CONFIG, "pw")

        conn.logout.assert_called_once()


class TestOnlyUnseen:
    """Session 2 follow-up (Jimbo-DK feedback): GSAK's 'Only check new
    messages' equivalent, using IMAP's native \\Seen flag instead of
    separate on-disk bookkeeping."""

    def _setup(self, monkeypatch, import_result=None):
        north = DatabaseInfo("North", Path("/dbs/north.sqlite"))
        manager = MagicMock()
        manager.databases = [north]
        manager.active = north
        manager.active_path = north.path
        monkeypatch.setattr("opensak.db.manager.get_db_manager", lambda: manager)
        monkeypatch.setattr("opensak.db.database.get_session", _fake_get_session)
        monkeypatch.setattr("opensak.db.database.init_db", lambda db_path=None: None)
        monkeypatch.setattr(
            "opensak.importer.import_zip",
            lambda *a, **kw: import_result or FakeImportResult(created=1),
        )
        conn = MagicMock()
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = (
            "OK", [(b"1 (BODY[] {n}", _zip_email_bytes("North.zip"))]
        )
        monkeypatch.setattr("opensak.email.service.connect", lambda *a, **kw: conn)
        return conn

    def test_default_searches_unseen_only(self, monkeypatch):
        conn = self._setup(monkeypatch)
        scan_and_import(_CONFIG, "pw")
        conn.search.assert_called_once_with(None, "UNSEEN")

    def test_only_unseen_false_searches_all(self, monkeypatch):
        conn = self._setup(monkeypatch)
        scan_and_import(_CONFIG, "pw", only_unseen=False)
        conn.search.assert_called_once_with(None, "ALL")

    def test_fetch_uses_body_peek_not_rfc822(self, monkeypatch):
        # Plain RFC822 fetch marks a message \Seen as a side effect —
        # BODY.PEEK[] does not, which is what lets us control exactly
        # when a message becomes \Seen (see docstring in service.py).
        conn = self._setup(monkeypatch)
        scan_and_import(_CONFIG, "pw")
        conn.fetch.assert_called_once_with("1", "(BODY.PEEK[])")

    def test_successful_import_marks_seen_without_deleting(self, monkeypatch):
        conn = self._setup(monkeypatch)
        result = scan_and_import(_CONFIG, "pw", delete_after_import=False)
        assert result.outcomes[0].marked_seen is True
        conn.store.assert_called_once_with("1", "+FLAGS", "\\Seen")

    def test_deleted_email_is_not_also_marked_seen(self, monkeypatch):
        # No point marking \Seen on a message we just told the server
        # to delete — one IMAP round-trip instead of two.
        conn = self._setup(monkeypatch)
        result = scan_and_import(_CONFIG, "pw", delete_after_import=True)
        assert result.outcomes[0].deleted_email is True
        seen_calls = [c for c in conn.store.call_args_list if c.args[2] == "\\Seen"]
        assert seen_calls == []

    def test_failed_import_is_not_marked_seen_so_it_is_retried(self, monkeypatch):
        conn = self._setup(
            monkeypatch, import_result=FakeImportResult(errors=["bad zip"])
        )
        result = scan_and_import(_CONFIG, "pw")
        assert result.outcomes[0].marked_seen is False
        conn.store.assert_not_called()
