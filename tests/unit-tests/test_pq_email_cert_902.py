"""tests/unit-tests/test_pq_email_cert_902.py — issue #902.

imaplib.IMAP4_SSL without an ssl_context uses an UNVERIFIED context: the
connection was encrypted, but the server's certificate and hostname were
never checked, so the IMAP password could be intercepted by a
man-in-the-middle. The connection now verifies with opensak.net.SSL_CONTEXT
(system store + certifi) and a failed verification gets its own error and
message. Deliberately no exception for self-signed servers (option A).

Besides the mocked tests, TestRealSelfSignedServer runs an actual local TLS
server with a self-signed certificate — the only way to prove verification
is genuinely switched on, and that the password never reaches the server.
"""

from __future__ import annotations

import datetime
import socket
import ssl
import threading
from unittest.mock import MagicMock, patch

import pytest

from opensak.email.connection import (
    ImapCertificateError,
    ImapConfig,
    ImapNetworkError,
    connect,
)
from opensak.net import SSL_CONTEXT

_CFG = ImapConfig(host="imap.example.com", port=993, use_ssl=True, username="alice")


def _cert_error(message: str = "self-signed certificate") -> ssl.SSLCertVerificationError:
    exc = ssl.SSLCertVerificationError(1, f"[SSL: CERTIFICATE_VERIFY_FAILED] {message}")
    exc.verify_message = message
    return exc


# ── connection.py ─────────────────────────────────────────────────────────

class TestConnect:
    @patch("imaplib.IMAP4_SSL")
    def test_ssl_connection_uses_the_verifying_context(self, mock_ssl_cls):
        mock_ssl_cls.return_value = MagicMock()
        connect(_CFG, "pw")
        assert mock_ssl_cls.call_args.kwargs["ssl_context"] is SSL_CONTEXT
        assert SSL_CONTEXT.verify_mode == ssl.CERT_REQUIRED
        assert SSL_CONTEXT.check_hostname is True

    @patch("imaplib.IMAP4_SSL", side_effect=_cert_error())
    def test_certificate_failure_raises_certificate_error(self, _mock):
        with pytest.raises(ImapCertificateError, match="self-signed certificate"):
            connect(_CFG, "pw")

    @patch("imaplib.IMAP4_SSL", side_effect=_cert_error("Hostname mismatch"))
    def test_certificate_error_is_still_a_network_error(self, _mock):
        # Callers that only distinguish auth/network keep working.
        with pytest.raises(ImapNetworkError):
            connect(_CFG, "pw")

    @patch("imaplib.IMAP4_SSL", side_effect=ssl.SSLError("TLS handshake failed"))
    def test_other_tls_errors_stay_plain_network_errors(self, _mock):
        with pytest.raises(ImapNetworkError) as info:
            connect(_CFG, "pw")
        assert not isinstance(info.value, ImapCertificateError)

    @patch("imaplib.IMAP4_SSL")
    def test_detail_falls_back_to_the_full_message(self, mock_ssl_cls):
        exc = ssl.SSLCertVerificationError(1, "certificate verify failed")
        mock_ssl_cls.side_effect = exc
        with pytest.raises(ImapCertificateError) as info:
            connect(_CFG, "pw")
        assert "certificate verify failed" in str(info.value)


# ── Real TLS server with a self-signed certificate ────────────────────────

def _self_signed_cert(tmp_path):
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    return cert_file, key_file


class _SelfSignedImapServer:
    """Accepts one TLS connection, sends an IMAP greeting and records every
    byte the client sends after the handshake (it would contain LOGIN)."""

    def __init__(self, cert_file, key_file):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert_file, key_file)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(10)
        self.port = self.sock.getsockname()[1]
        self.received = b""
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            raw, _ = self.sock.accept()
        except OSError:
            return
        try:
            with self.ctx.wrap_socket(raw, server_side=True) as tls:
                tls.settimeout(5)
                tls.sendall(b"* OK IMAP ready\r\n")
                buf = b""
                while True:
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    self.received += chunk
                    buf += chunk
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        tag, _, rest = line.partition(b" ")
                        if rest.upper().startswith(b"CAPABILITY"):
                            tls.sendall(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + tag + b" OK done\r\n")
                        elif rest.upper().startswith(b"LOGIN"):
                            tls.sendall(tag + b" NO denied\r\n")
                            return
                        else:
                            tls.sendall(tag + b" BAD unsupported\r\n")
        except (OSError, ssl.SSLError):
            pass  # the client aborted the handshake — expected

    def close(self):
        self.sock.close()
        self.thread.join(timeout=5)


class TestRealSelfSignedServer:
    def test_self_signed_server_is_rejected_before_the_password_is_sent(self, tmp_path):
        server = _SelfSignedImapServer(*_self_signed_cert(tmp_path))
        try:
            cfg = ImapConfig(host="localhost", port=server.port, use_ssl=True, username="alice")
            with pytest.raises(ImapCertificateError):
                connect(cfg, "s3cret-password", timeout=5)
        finally:
            server.close()
        assert b"s3cret-password" not in server.received
        assert server.received == b""

    def test_without_verification_the_password_would_have_been_sent(self, tmp_path):
        # Documents the hole #902 closes: the old unverified behaviour
        # (imaplib's default context) connects and sends the password.
        import imaplib
        server = _SelfSignedImapServer(*_self_signed_cert(tmp_path))
        try:
            conn = imaplib.IMAP4_SSL("localhost", server.port, timeout=5)
            with pytest.raises(imaplib.IMAP4.error):
                conn.login("alice", "s3cret-password")
        finally:
            server.close()
        assert b"s3cret-password" in server.received


# ── Workers and messages ──────────────────────────────────────────────────

def _raise_cert(*_a, **_k):
    raise ImapCertificateError("self-signed certificate")


def test_settings_test_worker_reports_certificate_kind(monkeypatch):
    from opensak.gui.dialogs.settings_dialog import _ImapTestWorker
    monkeypatch.setattr("opensak.email.connection.check_connection", _raise_cert)
    w = _ImapTestWorker(_CFG, "pw")
    errs: list = []
    w.error.connect(lambda kind, detail: errs.append((kind, detail)))
    w.run()
    assert errs == [("certificate", "self-signed certificate")]


def test_pq_check_worker_reports_certificate_kind(monkeypatch):
    from opensak.gui.dialogs.pq_email_check_dialog import PQEmailCheckWorker
    monkeypatch.setattr("opensak.email.service.scan_and_import", _raise_cert)
    w = PQEmailCheckWorker(_CFG, "pw", False, True)
    errs: list = []
    w.error.connect(lambda kind, detail: errs.append((kind, detail)))
    w.run()
    assert errs == [("certificate", "self-signed certificate")]


@pytest.mark.parametrize("key", ["pq_email_test_error_certificate", "pq_check_error_certificate"])
def test_certificate_messages_explain_and_show_detail(key):
    from opensak.lang import load_language, tr
    load_language("en")
    msg = tr(key, detail="self-signed certificate")
    assert "certificate could not be verified" in msg
    assert "self-signed certificate" in msg
    assert msg != tr(key.replace("certificate", "network"), detail="self-signed certificate")


def test_settings_dialog_shows_certificate_message(qtbot):
    from opensak.gui.dialogs.settings_dialog import SettingsDialog
    from opensak.lang import load_language
    load_language("en")
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    dlg._on_pq_email_test_error("certificate", "Hostname mismatch")
    text = dlg._pq_email_status.text()
    assert "password was not sent" in text and "Hostname mismatch" in text


def test_pq_check_dialog_shows_certificate_message(qtbot):
    from opensak.gui.dialogs.pq_email_check_dialog import PQEmailCheckDialog
    from opensak.lang import load_language
    load_language("en")
    dlg = PQEmailCheckDialog()
    qtbot.addWidget(dlg)
    dlg._on_error("certificate", "self-signed certificate")
    text = dlg._log.toPlainText()
    assert "mailbox was not checked" in text and "self-signed certificate" in text
