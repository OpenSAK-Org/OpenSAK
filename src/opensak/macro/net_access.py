"""
src/opensak/macro/net_access.py — the web sites a Lua macro may contact with
opensak.get_url() (#1079), and the request itself.

A macro can read everything in the databases, so with network access it
could send that data anywhere — a GET is enough, in the query string. The
web site (host) therefore plays the part a folder plays for files: before
a macro's first request to a host, the runtime asks the user through
OpenSAK's own dialog, which shows the host name, never text the macro can
set: This time only (for this run), Always or Deny. Like folders (#1091),
an approval belongs to the running macro: "Always" is stored with its file
and code in db_access.py, and a changed macro is asked again. There is no
list of hosts shared by all macros.

The rules (check_url(), fetch()):

  * http and https only. Credentials in the URL (user:pass@) are refused,
    and so are hosts of BLOCKED_DOMAINS (geocaching.com, coord.info and
    Groundspeak's other sites, with their subdomains): their terms of use
    don't allow automated access outside their API, so the user cannot
    approve them.
  * GET only, without cookies or custom headers. The User-Agent is fixed
    to USER_AGENT. The system's proxy settings apply.
  * Every redirect is checked like the first URL, and its host approved;
    at most MAX_REDIRECTS of them.
  * A host on the local network (loopback, private, link-local and shared
    addresses: a router, a NAS) can be approved too, but only by its own
    question, which says so. An approval given while it was a public
    address does not cover it: the address the connection actually goes to
    is checked as well, so an approved public host cannot be pointed at
    the local network later (DNS rebinding). Through a proxy only the
    first lookup is checked, since the proxy connects, not OpenSAK.
  * The body is read in CHUNK_BYTES pieces, so a cancelled run stops
    within one, and a body larger than the macro's limit ends the request.
    The socket timeout covers a server that does not answer.

TLS uses opensak.net.SSL_CONTEXT (#901).
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Callable, Iterable, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from opensak import __version__
from opensak.macro.metadata import is_blocked_host, normalize_host
from opensak.net import SSL_CONTEXT

USER_AGENT = f"OpenSAK-Macro/{__version__}"
SCHEMES = ("http", "https")
MAX_REDIRECTS = 5
CHUNK_BYTES = 64 * 1024
# opensak.get_url(): seconds without an answer, and the largest body
DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 120
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_MAX_BYTES = 50 * 1024 * 1024
# Per run: requests in all, the pause between two requests to one host,
# and how many hosts the user is asked about (a macro looping over many
# hosts would otherwise bury the user in questions).
MAX_REQUESTS_PER_RUN = 200
MIN_INTERVAL_S = 1.0
MAX_HOST_QUESTIONS_PER_RUN = 5

# Characters left as they are when a URL is quoted: those with a meaning
# in a URL, and "%" so an already quoted URL stays the same.
_URL_SAFE = "/?#[]@!$&'()*+,;=:%~"
# 100.64.0.0/10, carrier-grade NAT: not "private" to ipaddress, but not
# reachable from the internet either.
_SHARED = ipaddress.ip_network("100.64.0.0/10")


class NetError(Exception):
    """A request a macro may not make, or one that failed."""


@dataclass(frozen=True)
class HostPermission:
    """A host a macro may contact; *local*: also while it is on the local
    network."""

    host: str
    local: bool = False

    def to_dict(self) -> dict:
        return {"host": self.host, "local": self.local}

    @classmethod
    def from_dict(cls, data: dict) -> Optional["HostPermission"]:
        host = data.get("host")
        if not isinstance(host, str):
            return None
        normalized = normalize_host(host)
        if normalized is None or is_blocked_host(normalized):
            return None
        return cls(normalized, bool(data.get("local")))


def covers(permissions: Iterable[HostPermission], host: str, local: bool) -> bool:
    """True if *permissions* allow contacting *host* (on the local network
    if *local*)."""
    return any(p.host == host and (p.local or not local) for p in permissions)


def with_host(
    permissions: Iterable[HostPermission], host: str, local: bool
) -> list[HostPermission]:
    """A copy of *permissions* that also allows *host*; an entry for the
    same host is extended instead of duplicated."""
    result: list[HostPermission] = []
    found = False
    for p in permissions:
        if p.host == host and not found:
            found = True
            p = HostPermission(host, p.local or local)
        result.append(p)
    if not found:
        result.append(HostPermission(host, local))
    return result


def is_local_address(address: str) -> bool:
    """True if the IP address *address* is not on the internet: loopback,
    private, link-local, shared (CGNAT), unspecified ("0.0.0.0" reaches
    this computer) and the like."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified
        or ip.is_reserved or ip.is_multicast
        or (isinstance(ip, ipaddress.IPv4Address) and ip in _SHARED)
        or (isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local)
    )


def resolves_local(host: str, port: int) -> bool:
    """True if *host* is, or one of its addresses is, on the local network.
    A name that cannot be looked up counts as public: the request then
    fails, or goes through a proxy that looks it up itself."""
    try:
        ipaddress.ip_address(host)
        return is_local_address(host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False
    return any(is_local_address(str(info[4][0])) for info in infos)


@dataclass(frozen=True)
class Target:
    """A URL a macro may request: *url* quoted, with the host as approved."""

    url: str
    scheme: str
    host: str
    port: int

    @property
    def secure(self) -> bool:
        return self.scheme == "https"


def check_url(url: str) -> Target:
    """*url* as a Target; NetError if a macro may not request it."""
    text = url.strip()
    if any(ord(c) < 32 or c == "\x7f" for c in text):
        raise NetError(f"not a valid URL (control characters): {url!r}")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise NetError(f"not a valid URL ({exc}): {url}") from None
    scheme = parts.scheme.lower()
    if scheme not in SCHEMES:
        raise NetError(f"only http and https URLs can be requested: {url}")
    if parts.username is not None or parts.password is not None:
        raise NetError(f"a URL with a user name or password is not allowed: {url}")
    host = normalize_host(parts.hostname or "")
    if host is None:
        raise NetError(f"not a valid host name: {url}")
    if is_blocked_host(host):
        raise NetError(f"macros may never contact {host}")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    quoted = urlunsplit((
        scheme, netloc, quote(parts.path, safe=_URL_SAFE) or "/",
        quote(parts.query, safe=_URL_SAFE), "",
    ))
    return Target(quoted, scheme, host, port or (443 if scheme == "https" else 80))


@dataclass(frozen=True)
class Response:
    """What a request returned: the body, the HTTP status (also for an
    error status such as 404), the Content-Type and the URL after
    redirects."""

    body: bytes
    status: int
    content_type: str
    url: str
    charset: Optional[str] = None

    def text(self) -> str:
        """The body decoded with the charset of its Content-Type (UTF-8 if
        there is none or Python does not know it), invalid bytes
        replaced."""
        try:
            return self.body.decode(self.charset or "utf-8", errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


# approve(target, local): raise unless the macro may contact target.host,
# on the local network if *local*. Called before the first request, for
# every redirect, and once the connection shows a local address.
Approve = Callable[[Target, bool], None]
# progress(bytes read, bytes in all or None): called after every chunk;
# raises to stop the request.
Progress = Callable[[int, Optional[int]], None]


def _proxied(req: urllib.request.Request) -> bool:
    """True if *req* goes through a proxy (urllib then replaced its host)."""
    return (req.host or "").lower() != urlsplit(req.full_url).netloc.lower()


def _checked_connection(base: type, req: urllib.request.Request, approve: Approve) -> type:
    """*base* (HTTPConnection or HTTPSConnection) checking the address it
    connected to: a local one needs a local approval of the host."""
    target = check_url(req.full_url)
    direct = not _proxied(req)

    class Connection(base):  # type: ignore[valid-type, misc]
        def connect(self) -> None:
            super().connect()
            if direct and self.sock is not None:
                peer = str(self.sock.getpeername()[0])
                if is_local_address(peer):
                    approve(target, True)

    return Connection


class _HttpHandler(urllib.request.HTTPHandler):
    def __init__(self, approve: Approve):
        super().__init__()
        self._approve = approve

    def http_open(self, req):
        return self.do_open(_checked_connection(http.client.HTTPConnection, req, self._approve),
                            req)


class _HttpsHandler(urllib.request.HTTPSHandler):
    def __init__(self, approve: Approve):
        super().__init__(context=SSL_CONTEXT)
        self._approve = approve

    def https_open(self, req):
        return self.do_open(_checked_connection(http.client.HTTPSConnection, req, self._approve),
                            req, context=SSL_CONTEXT)


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    """Checks every redirect like the first URL and has its host approved."""

    def __init__(self, approve: Approve):
        super().__init__()
        self._approve = approve
        self.hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops += 1
        if self.hops > MAX_REDIRECTS:
            raise NetError(f"more than {MAX_REDIRECTS} redirects, the last to {newurl}")
        target = check_url(newurl)
        self._approve(target, resolves_local(target.host, target.port))
        new = super().redirect_request(req, fp, code, msg, headers, target.url)
        if new is not None:
            new.timeout = req.timeout
        return new


def _opener(approve: Approve, redirects: _RedirectHandler) -> urllib.request.OpenerDirector:
    """An opener for http and https only (no file:, ftp: or data:, which
    build_opener() would add), without cookies."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        _HttpHandler(approve),
        _HttpsHandler(approve),
        urllib.request.HTTPDefaultErrorHandler(),
        redirects,
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    opener.addheaders = [("User-Agent", USER_AGENT), ("Accept-Encoding", "identity")]
    return opener


def _read(resp, max_bytes: int, progress: Progress, url: str) -> bytes:
    length = resp.headers.get("Content-Length")
    total = int(length) if length and length.strip().isdigit() else None
    if total is not None and total > max_bytes:
        raise NetError(f"{url} is larger than {max_bytes} bytes ({total})")
    chunks: list[bytes] = []
    done = 0
    while True:
        chunk = resp.read(CHUNK_BYTES)
        if not chunk:
            break
        done += len(chunk)
        if done > max_bytes:
            raise NetError(f"{url} is larger than {max_bytes} bytes")
        chunks.append(chunk)
        progress(done, total)
    return b"".join(chunks)


def fetch(
    url: str, approve: Approve, timeout: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES, progress: Optional[Progress] = None,
) -> Response:
    """GET *url* for a macro (see the module docstring). *approve* is asked
    for every host before it is contacted. Raises NetError, or whatever
    *approve* or *progress* raise."""
    target = check_url(url)
    approve(target, resolves_local(target.host, target.port))
    redirects = _RedirectHandler(approve)
    opener = _opener(approve, redirects)
    report = progress or (lambda _done, _total: None)
    try:
        resp = opener.open(target.url, timeout=timeout)
    except urllib.error.HTTPError as exc:
        resp = exc                          # 404, 500, …: a response too
    except urllib.error.URLError as exc:
        raise NetError(f"cannot reach {target.url}: {exc.reason}") from None
    except (TimeoutError, socket.timeout):
        raise NetError(f"{target.url} did not answer within {timeout:g} s") from None
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise NetError(f"cannot request {target.url}: {exc}") from None
    try:
        final = resp.geturl() or target.url
        try:
            body = _read(resp, max_bytes, report, final)
        except (TimeoutError, socket.timeout):
            raise NetError(f"{final} did not answer within {timeout:g} s") from None
        except (OSError, http.client.HTTPException) as exc:
            raise NetError(f"cannot read {final}: {exc}") from None
        headers: Message = resp.headers
        return Response(
            body=body,
            status=int(resp.status if resp.status is not None else resp.code),
            content_type=headers.get("Content-Type", ""),
            url=final,
            charset=headers.get_content_charset(),
        )
    finally:
        resp.close()
