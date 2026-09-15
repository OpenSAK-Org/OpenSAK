# tests/unit-tests/test_pq_email_credentials.py — keyring-mocked tests
# for PQ e-mail credential storage (issue #443). No real OS keyring is
# ever touched.

from unittest.mock import patch

import keyring.errors
import pytest

from opensak.email import credentials


class TestGetPassword:
    def test_returns_none_for_empty_username(self):
        assert credentials.get_password("") is None

    @patch("keyring.get_password", return_value="s3cret")
    def test_returns_stored_password(self, mock_get):
        assert credentials.get_password("alice") == "s3cret"
        mock_get.assert_called_once_with("OpenSAK PQ Email", "alice")

    @patch("keyring.get_password", return_value=None)
    def test_returns_none_when_nothing_stored(self, mock_get):
        assert credentials.get_password("alice") is None

    @patch("keyring.get_password", side_effect=keyring.errors.NoKeyringError())
    def test_returns_none_on_backend_error(self, mock_get):
        # No keyring backend available (e.g. headless CI) — should not
        # raise, just behave as if nothing was stored.
        assert credentials.get_password("alice") is None


class TestSetPassword:
    def test_returns_false_for_empty_username(self):
        assert credentials.set_password("", "pw") is False

    @patch("keyring.set_password")
    def test_stores_password(self, mock_set):
        assert credentials.set_password("alice", "s3cret") is True
        mock_set.assert_called_once_with("OpenSAK PQ Email", "alice", "s3cret")

    @patch("keyring.set_password", side_effect=keyring.errors.PasswordSetError())
    def test_returns_false_on_backend_error(self, mock_set):
        assert credentials.set_password("alice", "s3cret") is False


class TestDeletePassword:
    def test_returns_false_for_empty_username(self):
        assert credentials.delete_password("") is False

    @patch("keyring.delete_password")
    def test_deletes_existing_password(self, mock_delete):
        assert credentials.delete_password("alice") is True
        mock_delete.assert_called_once_with("OpenSAK PQ Email", "alice")

    @patch("keyring.delete_password", side_effect=keyring.errors.PasswordDeleteError())
    def test_treats_already_absent_as_success(self, mock_delete):
        # Nothing was stored to begin with — not an error for the caller.
        assert credentials.delete_password("alice") is True

    @patch("keyring.delete_password", side_effect=keyring.errors.NoKeyringError())
    def test_returns_false_on_backend_error(self, mock_delete):
        assert credentials.delete_password("alice") is False
