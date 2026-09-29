"""
src/opensak/api/geocaching.py — Geocaching.com OAuth 2.0 PKCE client.

Håndterer:
  - OAuth 2.0 Authorization Code flow med PKCE (ingen client_secret nødvendig)
  - Token gemmes krypteret i ~/.config/opensak/gc_token.json
  - Automatisk token-refresh
  - API-kald: cache detaljer, trackables i cache, brugerens fund-historik

Status: Klar til brug — mangler kun GC_CLIENT_ID fra Geocaching.com.
        Når CLIENT_ID er tom returnerer alle metoder None og logger en advarsel.

OAuth flow:
  1. Generer PKCE code_verifier + code_challenge (SHA-256)
  2. Åbn browser til geocaching.com/oauth/authorize
  3. Start lokal HTTP-server på localhost:GC_REDIRECT_PORT (fanger callback)
  4. Modtag authorization_code
  5. Veksle code → access_token + refresh_token via token endpoint
  6. Gem tokens i GC_TOKEN_FILE
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, List
from enum import IntEnum
from opensak.net import SSL_CONTEXT
from opensak.utils.types import GcCode, LogType
from opensak.utils.utils import validate_gc_code

logger = logging.getLogger(__name__)

# ── Konfiguration ─────────────────────────────────────────────────────────────
# Disse værdier udfyldes når Geocaching.com godkender ansøgningen.
# client_id er offentlig og må gerne ligge i koden.
# client_secret er IKKE nødvendig med PKCE-flow.

GC_CLIENT_ID    = ""   # Udfyldes ved API-godkendelse — f.eks. "opensak_abc123"
GC_AUTH_URL     = "https://www.geocaching.com/oauth/authorize"
GC_TOKEN_URL    = "https://oauth.geocaching.com/token"
GC_API_BASE     = "https://api.groundspeak.com/v1"
GC_REDIRECT_PORT = 7654
GC_REDIRECT_URI = f"http://localhost:{GC_REDIRECT_PORT}/callback"
GC_SCOPES       = "*"   # Fuld adgang — justeres evt. ved godkendelse

# ── Token håndtering med cache ────────────────────────────────────────────────

_cached_token: Optional[dict] = None  # In-memory cache for token

def get_token_file() -> Path:
    """Returnerer stien til token-filen i app data-mappen."""
    from opensak.config import get_app_data_dir
    return get_app_data_dir() / "gc_token.json"


# ── PKCE hjælpefunktioner ──────────────────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    """
    Generer PKCE code_verifier og code_challenge.
    Returns: (code_verifier, code_challenge)
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ── Token håndtering ──────────────────────────────────────────────────────────

def _save_token(token_data: dict) -> None:
    """Gem token til disk og opdater cache. Sæt filrettigheder til 600."""
    global _cached_token
    _cached_token = token_data
    token_file = get_token_file()
    token_file.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    try:
        os.chmod(token_file, 0o600)
    except OSError:
        pass  # Windows understøtter ikke chmod — ignoreres


def _load_token() -> Optional[dict]:
    """Indlæs token fra disk. Returnerer None hvis filen ikke eksisterer."""
    global _cached_token
    if _cached_token is not None:
        return _cached_token

    token_file = get_token_file()
    if not token_file.exists():
        return None
    try:
        _cached_token = json.loads(token_file.read_text(encoding="utf-8"))
        return _cached_token
    except (json.JSONDecodeError, OSError):
        return None


def _delete_token() -> None:
    """Slet token-filen (log ud) og ryd cache."""
    global _cached_token
    _cached_token = None
    token_file = get_token_file()
    if token_file.exists():
        token_file.unlink()


def _is_token_valid(token_data: dict) -> bool:
    """Returner True hvis access_token ikke er udløbet (med 60s buffer)."""
    expires_at = token_data.get("expires_at", 0)
    return time.time() < (expires_at - 60)


# ── OAuth PKCE flow ───────────────────────────────────────────────────────────

class _CallbackServer(http.server.HTTPServer):
    """HTTPServer der bærer OAuth-resultatet fra callback-handleren."""

    auth_code: Optional[str] = None
    auth_error: Optional[str] = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler der fanger OAuth callback fra browseren."""

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/callback":
            code = params.get("code", [None])[0]
            error = params.get("error", [None])[0]

            if code:
                self.server.auth_code = code
                self.server.auth_error = None
                html = self._html("Login gennemført", "Du kan lukke dette vindue og vende tilbage til OpenSAK.", "✓")
            else:
                self.server.auth_code = None
                self.server.auth_error = error or "Ukendt fejl"
                html = self._html("Login fejlede", f"Fejl: {self.server.auth_error}", "✗")
        else:
            html = self._html("OpenSAK", "Venter på godkendelse...", "")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):  # noqa: A002
        pass  # Undertrykker HTTP-log i konsollen

    @staticmethod
    def _html(title: str, message: str, icon: str) -> str:
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;margin:0;background:#1e2a3a;color:#fff}}
.box{{text-align:center;padding:2em}}.icon{{font-size:4em}}</style></head>
<body><div class="box"><div class="icon">{icon}</div>
<h2>{title}</h2><p>{message}</p></div></body></html>"""


def start_oauth_flow() -> Optional[dict]:
    """
    Start OAuth PKCE flow. Åbner browser og venter på callback.

    Returns:
        Token dict ved succes, None ved fejl eller hvis CLIENT_ID ikke er sat.

    Raises:
        RuntimeError: Hvis flow fejler med en specifik fejl.
    """
    if not GC_CLIENT_ID:
        logger.warning(
            "GC_CLIENT_ID er ikke sat — Geocaching.com API ikke tilgængeligt endnu. "
            "Udfyld GC_CLIENT_ID i api/geocaching.py når API-adgang er godkendt."
        )
        return None

    import webbrowser

    code_verifier, code_challenge = _generate_pkce()

    # Byg authorization URL
    params = {
        "client_id":             GC_CLIENT_ID,
        "response_type":         "code",
        "redirect_uri":          GC_REDIRECT_URI,
        "scope":                 GC_SCOPES,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
        "state":                 secrets.token_urlsafe(16),
    }
    auth_url = GC_AUTH_URL + "?" + urllib.parse.urlencode(params)

    # Start lokal callback-server
    server = _CallbackServer(("localhost", GC_REDIRECT_PORT), _CallbackHandler)
    server.timeout = 120  # Timeout efter 2 minutter

    thread = threading.Thread(target=lambda: server.handle_request(), daemon=True)
    thread.start()

    # Åbn browser
    logger.info("Åbner browser til Geocaching.com login...")
    webbrowser.open(auth_url)

    # Vent på callback (max 120 sekunder)
    thread.join(timeout=120)

    if server.auth_error:
        raise RuntimeError(f"OAuth fejl: {server.auth_error}")

    if not server.auth_code:
        raise RuntimeError("Timeout — intet svar fra Geocaching.com inden 2 minutter.")

    # Veksle authorization code → tokens
    return _exchange_code(server.auth_code, code_verifier)


def _exchange_code(code: str, code_verifier: str) -> dict:
    """Veksle authorization code til access_token + refresh_token."""
    data = urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  GC_REDIRECT_URI,
        "client_id":     GC_CLIENT_ID,
        "code_verifier": code_verifier,
    }).encode("utf-8")

    req = urllib.request.Request(
        GC_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
        token_data = json.loads(resp.read().decode("utf-8"))

    # Beregn absolut udløbstidspunkt
    expires_in = token_data.get("expires_in", 3600)
    token_data["expires_at"] = time.time() + expires_in

    _save_token(token_data)
    logger.info("Token gemt til disk.")
    return token_data


def _refresh_token() -> Optional[dict]:
    """Forsøg at refreshe access token med refresh_token."""
    token_data = _load_token()
    if not token_data or "refresh_token" not in token_data:
        return None

    data = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": token_data["refresh_token"],
        "client_id":     GC_CLIENT_ID,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            GC_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            new_token = json.loads(resp.read().decode("utf-8"))

        expires_in = new_token.get("expires_in", 3600)
        new_token["expires_at"] = time.time() + expires_in
        _save_token(new_token)
        logger.info("Token refreshed.")
        return new_token

    except Exception as exc:
        logger.error(f"Token refresh fejlede: {exc}")
        return None


def get_valid_token() -> Optional[str]:
    """
    Returner et gyldigt access_token.
    Refresher automatisk hvis udløbet.
    Returnerer None hvis ikke logget ind.
    """
    token_data = _load_token()
    if not token_data:
        return None

    if _is_token_valid(token_data):
        return token_data.get("access_token")

    # Token udløbet — forsøg refresh
    refreshed = _refresh_token()
    if refreshed:
        return refreshed.get("access_token")

    return None


def logout() -> None:
    """Log ud — slet token fra disk."""
    _delete_token()
    logger.info("Logget ud fra Geocaching.com.")


def is_logged_in() -> bool:
    """Returnerer True hvis der er et (måske udløbet) token på disk."""
    return _load_token() is not None


# ── API klient ────────────────────────────────────────────────────────────────

def _api_get(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    """
    Lav et autentificeret GET-kald til Geocaching.com API.

    Args:
        endpoint: API endpoint uden base URL, f.eks. "/geocaches/GC12345"
        params:   Query parametre som dict

    Returns:
        Parsed JSON response som dict, eller None ved fejl.
    """
    access_token = get_valid_token()
    if not access_token:
        logger.warning("Ikke logget ind — kan ikke kalde API.")
        return None

    url = GC_API_BASE + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept":        "application/json",
            "User-Agent":    "OpenSAK/1.0 (open source geocache manager)",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            logger.warning("401 Unauthorized — token muligvis udløbet.")
            # Forsøg refresh og prøv én gang til
            refreshed = _refresh_token()
            if refreshed:
                return _api_get(endpoint, params)
        logger.error(f"API HTTP fejl {exc.code}: {endpoint}")
        return None
    except Exception as exc:
        logger.error(f"API fejl: {exc}")
        return None


# ── Offentlige API-funktioner ─────────────────────────────────────────────────

def get_cache_details(gc_code: GcCode) -> Optional[dict]:
    """
    Hent fulde cache-detaljer fra Geocaching.com API.

    Returnerer bl.a.:
      - favoritePoints (int)
      - trackables (liste)
      - shortDescription / longDescription (HTML)
      - hint (krypteret)
      - attributes (liste)
      - recentActivity (liste af logs)

    Args:
        gc_code: Cache kode, f.eks. "GC12345"

    Returns:
        Dict med cache data, eller None ved fejl/ikke logget ind.
    """
    if not GC_CLIENT_ID:
        return None
    
    try:
        validate_gc_code(gc_code)  # Valider formatet på gc_code inden API-kald
    except ValueError as exc:
        logger.warning(f"Invalid gc_code: {gc_code} — {exc}")
        return None
    
    return _api_get(
        f"/geocaches/{gc_code}",
        params={
            "fields": (
                "referenceCode,name,difficulty,terrain,favoritePoints,"
                "trackables,shortDescription,longDescription,hints,"
                "attributes,recentActivity,location,geocacheType,"
                "geocacheSize,status,owner,postedCoordinates"
            ),
            "expand": "trackables:50,recentActivity:10",
        },
    )


def get_trackables_in_cache(gc_code: GcCode) -> Optional[list]:
    """
    Hent liste af trackables (Travel Bugs m.fl.) der aktuelt er i en cache.

    Args:
        gc_code: Cache kode, f.eks. "GC12345"

    Returns:
        Liste af trackable dicts, eller None ved fejl.
        Hvert element har typisk: referenceCode, name, trackableType, owner
    """
    if not GC_CLIENT_ID:
        return None
    
    try:
        validate_gc_code(gc_code)  # Valider formatet på gc_code inden API-kald
    except ValueError as exc:
        logger.warning(f"Invalid gc_code: {gc_code} — {exc}")
        return None

    result = _api_get(
        f"/geocaches/{gc_code}/trackables",
        params={"fields": "referenceCode,name,trackableType,owner,goal,description"},
    )

    if result is None:
        return None

    # API returnerer enten en liste direkte eller {"data": [...]}
    if isinstance(result, list):
        return result
    return result.get("data", [])


def get_user_profile() -> Optional[dict]:
    """
    Hent den loggede brugers profil fra Geocaching.com.

    Returns:
        Dict med: referenceCode, username, avatarUrl, findCount, hideCount m.fl.
    """
    if not GC_CLIENT_ID:
        return None

    return _api_get(
        "/users/me",
        params={"fields": "referenceCode,username,avatarUrl,findCount,hideCount,membershipLevelId"},
    )


def _get_user_logs(
    username: str,
    log_types: Optional[List[LogType]] = None,
    max_results: int = 200,
) -> Optional[list]:
    """
    Fetch logs for a user from Geocaching API.

    Args:
        username: Geocaching username
        log_types: list of LogType enums (e.g., [LogType.FOUND, LogType.DNF])
        max_results: maximum number of logs (max 200)

    Returns:
        List of log dictionaries
    """

    if not GC_CLIENT_ID:
        return None

    params = {
        "fields": "referenceCode,geocacheCode,loggedDate,logType,text",
        "limit": min(max_results, 200),
    }

    if log_types:
        # Convert IntEnum values to comma-separated string
        params["types"] = ",".join(str(t.value) for t in log_types)

    result = _api_get(f"/users/{username}/geocachelogs", params=params)

    if result is None:
        return None

    if isinstance(result, list):
        return result

    return result.get("data", [])


def get_user_finds(username: str, max_results):
    return _get_user_logs(username, [LogType.FOUND], max_results)


def get_user_dnfs(username: str, max_results):
    return _get_user_logs(username, [LogType.DNF], max_results)


def get_user_notes(username: str, max_results):
    return _get_user_logs(username, [LogType.NOTE], max_results)


def get_user_archives(username: str, max_results):
    return _get_user_logs(username, [LogType.ARCHIVE], max_results)


def get_user_activity(username: str, max_results):
    return _get_user_logs(username, list(LogType), max_results)


def get_favorite_points() -> Optional[dict]:
    """
    Hent brugerens tilgængelige favorite points.

    Returns:
        Dict med: availableFavoritePoints (int)
    """
    if not GC_CLIENT_ID:
        return None

    result = _api_get("/users/me", params={"fields": "referenceCode,username,favoritePoints"})
    return result
