"""
src/opensak/email/connection.py — plain IMAP connection handling for
PQ e-mail retrieval (issue #443, v1 scope).

v1 supports only standard IMAP login (host, port, SSL, username,
password) via Python's built-in `imaplib` — no new network dependency.
Gmail and Outlook.com/Live.com no longer allow this kind of login and
need OAuth2 instead; that is tracked separately as #697 and #698.

Kept free of GUI/Qt imports so it can be unit-tested against a mocked
`imaplib` without a real mailbox or a running application.
"""

from __future__ import annotations

import imaplib
import socket
import ssl
from dataclasses import dataclass

from opensak.net import SSL_CONTEXT

# Standard IMAP ports — used both as sensible dialog defaults and by
# the Settings dialog's "flip default port when SSL is toggled" logic.
DEFAULT_IMAP_SSL_PORT = 993
DEFAULT_IMAP_PORT = 143


@dataclass
class ImapConfig:
    """Ikke-hemmelige forbindelsesoplysninger for PQ-mailkontoen.

    Kodeordet er bevidst IKKE et felt her — det hentes separat fra
    opensak.email.credentials (OS keyring) og sendes direkte til
    connect()/check_connection(), så det aldrig ender i en logget eller
    serialiseret ImapConfig ved en fejl."""
    host: str
    port: int
    use_ssl: bool
    username: str


class ImapConnectionError(Exception):
    """Fælles basisklasse for forbindelsesfejl herunder — så en kalder
    der ikke er interesseret i skelnen bare kan fange denne ene."""


class ImapAuthError(ImapConnectionError):
    """Forkert brugernavn/kodeord — eller en konto der kræver
    app-password/OAuth2, som denne v1 ikke understøtter (se #697/#698)."""


class ImapNetworkError(ImapConnectionError):
    """Værten kunne ikke nås: DNS-opslag fejlede, forbindelse afvist/
    timeout, eller en TLS-fejl under opsætning af forbindelsen."""


class ImapCertificateError(ImapNetworkError):
    """Serverens certifikat kunne ikke verificeres (issue #902):
    selvsigneret, udløbet, eller værtsnavnet passer ikke til certifikatet.

    Sker under TLS-håndtrykket, altså FØR login — kodeordet er ikke sendt.
    Arver fra ImapNetworkError, så kode der kun skelner auth/netværk stadig
    fanger den; GUI'en fanger den først for at vise en præcis besked."""


def connect(config: ImapConfig, password: str, timeout: float = 10.0) -> imaplib.IMAP4:
    """
    Opret og log ind på en IMAP-forbindelse.

    Kalderen har ansvar for til sidst at lukke forbindelsen igen med
    `conn.logout()`.

    Rejser `ImapAuthError` ved forkert login, `ImapCertificateError` hvis
    serverens certifikat ikke kan verificeres, `ImapNetworkError` ved alt
    andet forbindelsesrelateret.

    Issue #902: SSL-forbindelsen verificerer nu serverens certifikat og
    værtsnavn med den fælles kontekst fra opensak.net (systemets
    certifikater + certifi). Uden en eksplicit ssl_context bruger imaplib
    en kontekst UDEN verifikation, så kodeordet kunne opsnappes af en
    man-in-the-middle. Der er bevidst ingen undtagelse for selvsignerede
    servere (løsning A i #902).
    """
    try:
        if config.use_ssl:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                config.host, config.port, timeout=timeout, ssl_context=SSL_CONTEXT,
            )
        else:
            conn = imaplib.IMAP4(config.host, config.port, timeout=timeout)
    except ssl.SSLCertVerificationError as exc:
        raise ImapCertificateError(_certificate_detail(exc)) from exc
    except (OSError, socket.gaierror, ssl.SSLError) as exc:
        raise ImapNetworkError(str(exc)) from exc

    try:
        conn.login(config.username, password)
    except imaplib.IMAP4.error as exc:
        try:
            conn.logout()
        except Exception:
            pass  # forbindelsen var alligevel ikke brugbar
        raise ImapAuthError(str(exc)) from exc
    except (OSError, socket.gaierror, ssl.SSLError) as exc:
        raise ImapNetworkError(str(exc)) from exc

    return conn


def _certificate_detail(exc: ssl.SSLCertVerificationError) -> str:
    """Den korte, læsbare årsag (fx 'self-signed certificate' eller
    'Hostname mismatch, ...') frem for hele OpenSSL-strengen."""
    return getattr(exc, "verify_message", None) or str(exc)


def check_connection(config: ImapConfig, password: str, timeout: float = 10.0) -> None:
    """
    Prøv at logge ind og med det samme logge ud igen.

    Bruges af "Test connection"-knappen i Settings → PQ Email — rejser
    samme undtagelser som `connect()`; returnerer intet ved succes.

    Hedder bevidst IKKE `test_connection` — et navn der starter med
    `test_` bliver samlet op af pytest som en testfunktion, hvis den
    nogensinde importeres direkte ind i en testfils navnerum (skete
    under implementeringen af #443, session 1).
    """
    conn = connect(config, password, timeout=timeout)
    try:
        conn.logout()
    except Exception:
        pass  # login var allerede bekræftet på dette tidspunkt
