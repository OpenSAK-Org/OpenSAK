# tests/unit-tests/test_pq_email_connection.py — imaplib-mocked tests
# for the plain-IMAP connection helpers (issue #443, v1 scope). No
# real network connection is ever made.

import imaplib
import socket
import ssl
from unittest.mock import MagicMock, patch

import pytest

from opensak.email.connection import (
    DEFAULT_IMAP_PORT,
    DEFAULT_IMAP_SSL_PORT,
    ImapAuthError,
    ImapConfig,
    ImapNetworkError,
    connect,
    check_connection,
)

_CFG_SSL = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")
_CFG_PLAIN = ImapConfig(host="imap.example.com", port=143, use_ssl=False, username="alice")


class TestConnect:
    @patch("imaplib.IMAP4_SSL")
    def test_ssl_login_success_returns_connection(self, mock_ssl_cls):
        mock_conn = MagicMock()
        mock_ssl_cls.return_value = mock_conn

        result = connect(_CFG_SSL, "pw")

        mock_ssl_cls.assert_called_once_with("imap.example.com", 993, timeout=10.0)
        mock_conn.login.assert_called_once_with("alice", "pw")
        assert result is mock_conn

    @patch("imaplib.IMAP4")
    def test_plain_login_uses_imap4_not_ssl(self, mock_imap4_cls):
        mock_conn = MagicMock()
        mock_imap4_cls.return_value = mock_conn

        result = connect(_CFG_PLAIN, "pw")

        mock_imap4_cls.assert_called_once_with("imap.example.com", 143, timeout=10.0)
        assert result is mock_conn

    @patch("imaplib.IMAP4_SSL", side_effect=socket.gaierror("Name or service not known"))
    def test_dns_failure_raises_network_error(self, mock_ssl_cls):
        with pytest.raises(ImapNetworkError):
            connect(_CFG_SSL, "pw")

    @patch("imaplib.IMAP4_SSL", side_effect=ssl.SSLError("TLS handshake failed"))
    def test_tls_failure_raises_network_error(self, mock_ssl_cls):
        with pytest.raises(ImapNetworkError):
            connect(_CFG_SSL, "pw")

    @patch("imaplib.IMAP4_SSL", side_effect=OSError("Connection refused"))
    def test_connection_refused_raises_network_error(self, mock_ssl_cls):
        with pytest.raises(ImapNetworkError):
            connect(_CFG_SSL, "pw")

    @patch("imaplib.IMAP4_SSL")
    def test_bad_credentials_raises_auth_error_and_logs_out(self, mock_ssl_cls):
        mock_conn = MagicMock()
        mock_conn.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        mock_ssl_cls.return_value = mock_conn

        with pytest.raises(ImapAuthError):
            connect(_CFG_SSL, "wrong-password")

        # Session must be cleaned up even though login failed.
        mock_conn.logout.assert_called_once()

    @patch("imaplib.IMAP4_SSL")
    def test_logout_failure_after_bad_login_is_swallowed(self, mock_ssl_cls):
        # logout() itself raising during cleanup must not mask the
        # original ImapAuthError.
        mock_conn = MagicMock()
        mock_conn.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        mock_conn.logout.side_effect = OSError("already closed")
        mock_ssl_cls.return_value = mock_conn

        with pytest.raises(ImapAuthError):
            connect(_CFG_SSL, "wrong-password")


class TestCheckConnection:
    @patch("imaplib.IMAP4_SSL")
    def test_success_logs_out_again(self, mock_ssl_cls):
        mock_conn = MagicMock()
        mock_ssl_cls.return_value = mock_conn

        check_connection(_CFG_SSL, "pw")

        mock_conn.login.assert_called_once_with("alice", "pw")
        mock_conn.logout.assert_called_once()

    @patch("imaplib.IMAP4_SSL")
    def test_propagates_auth_error(self, mock_ssl_cls):
        mock_conn = MagicMock()
        mock_conn.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        mock_ssl_cls.return_value = mock_conn

        with pytest.raises(ImapAuthError):
            check_connection(_CFG_SSL, "wrong-password")


def test_default_ports():
    # Sanity check the constants used by the Settings dialog's
    # SSL-toggle-flips-default-port logic.
    assert DEFAULT_IMAP_SSL_PORT == 993
    assert DEFAULT_IMAP_PORT == 143
