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
"""

from __future__ import annotations

import ssl

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
        log.warning("certifi ikke tilgængeligt — bruger kun systemets certifikater")
    except (OSError, ssl.SSLError) as exc:
        log.warning("kunne ikke indlæse certifi's certifikater (%s) — bruger kun systemets", exc)
    return ctx


SSL_CONTEXT: ssl.SSLContext = build_ssl_context()
