"""
src/opensak/net.py — Fælles netværkshjælpere.

Issue #901: én verificerende SSL-kontekst til ALLE HTTPS-kald i OpenSAK.

Python finder normalt CA-certifikaterne via systemet, men i en PyInstaller-
bundlet app virker det ikke pålideligt: på macOS leder den bundlede OpenSSL
i en sti fra build-maskinen, der ikke findes hos brugeren (HTTPS fejler
altid med CERTIFICATE_VERIFY_FAILED); på Windows kan GitHubs rod-certifikat
mangle i storet på en frisk/låst maskine; og en AppImage bygget på Ubuntu
kender kun Debian-familiens certifikatstier. certifi's cacert.pem bundles
derfor eksplicit via opensak.spec, og konteksten herunder bruger den OVENI
systemets certifikater (se build_ssl_context).

Oprindeligt lå dette kun i updater.py, så opdateringstjekket virkede, mens
fx Boundary Data Updates (geo/packs.py) fejlede på macOS og blev meldt som
"no network connection". Brug derfor altid SSL_CONTEXT ved urlopen() — en
test (test_net_ssl_901.py) fejler, hvis et kald i src/ glemmer det.

Issue #1111: "offline detected"-flag for sessionen. Når et kald fejler fordi
der ikke er netværk (timeout, DNS, forbindelse afvist/utilgængelig), sættes
flaget; automatiske kald (baggrunds-opdateringstjek, boundary-data under
import) springes derefter over i stedet for at vente en fuld timeout hver.
Kald brugeren selv starter prøver altid, og ethvert vellykket kald nulstiller
flaget. Efter OFFLINE_RETRY_SECONDS må et automatisk kald prøve igen, så en
forbindelse der kommer tilbage midt i sessionen bliver opdaget.
"""

from __future__ import annotations

import errno
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError

from opensak.logger import get_logger

log = get_logger("net")


def build_ssl_context() -> ssl.SSLContext:
    """
    Byg en verificerende SSL-kontekst der stoler på BÅDE systemets
    certifikater og certifi's bundlede certifikat-bundt.

    - Systemets store (det create_default_context() finder) bevares, så
      brugere bag en virksomheds-proxy med TLS-inspektion — hvis rod-
      certifikat kun ligger i systemets store — ikke låses ude.
    - certifi lægges oveni, så HTTPS også virker der, hvor Python ikke kan
      finde systemets certifikater (den bundlede macOS-app, en frisk
      Windows-maskine, en AppImage på en ikke-Debian-distro).

    Mangler certifi, bruges systemets certifikater alene. Verifikation er
    aktiv i alle tilfælde — den slås aldrig fra.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except ImportError:
        log.warning("certifi not available — using system certificates only")
    except (OSError, ssl.SSLError) as exc:
        log.warning("could not load certifi's certificates (%s) — using system certificates only", exc)
    return ctx


SSL_CONTEXT: ssl.SSLContext = build_ssl_context()


# ── "Offline detected" (#1111) ────────────────────────────────────────────────

# How long automatic requests are skipped after a connectivity failure before
# one may try again.
OFFLINE_RETRY_SECONDS = 300

# errno values for "no route to the network/host" (POSIX, and Winsock's
# WSAENETUNREACH / WSAEHOSTUNREACH, which Windows reports instead).
_UNREACHABLE_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, 10051, 10065}

_offline_lock = threading.Lock()
_offline_since: float | None = None


def is_connectivity_error(exc: BaseException) -> bool:
    """True if *exc* means the network (not the server) is unreachable.

    An HTTP error status means a server answered, so we are online; an SSL
    error points to a certificate/proxy problem, not to a missing network."""
    if isinstance(exc, HTTPError):
        return False
    if isinstance(exc, URLError) and isinstance(exc.reason, BaseException):
        exc = exc.reason
    if isinstance(exc, ssl.SSLError):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, socket.gaierror)):
        return True
    return isinstance(exc, OSError) and exc.errno in _UNREACHABLE_ERRNOS


def note_failure(exc: BaseException) -> None:
    """Record a failed request; sets the offline flag on a connectivity error."""
    global _offline_since
    if not is_connectivity_error(exc):
        return
    with _offline_lock:
        if _offline_since is None:
            log.info("network unreachable (%s) — skipping automatic requests for now", exc)
        _offline_since = time.monotonic()


def note_success() -> None:
    """Record a request that reached its server; clears the offline flag."""
    global _offline_since
    with _offline_lock:
        if _offline_since is not None:
            log.info("network reachable again")
        _offline_since = None


def offline_detected() -> bool:
    """True while automatic requests should be skipped: a connectivity
    failure happened less than OFFLINE_RETRY_SECONDS ago and nothing has
    succeeded since."""
    with _offline_lock:
        return (_offline_since is not None
                and time.monotonic() - _offline_since < OFFLINE_RETRY_SECONDS)


def reset_offline() -> None:
    """Forget an earlier connectivity failure (tests)."""
    global _offline_since
    with _offline_lock:
        _offline_since = None
