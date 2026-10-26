"""
tests/unit-tests/test_macro_get_url_1079.py — opensak.get_url() (#1079):
every web site needs the user's approval, which belongs to the macro and
its code like its folders (#1091).

The requests go to a local http.server. Host names under example.test are
connected to it by a patched socket.create_connection; their lookup fails,
so they count as internet hosts until the connection shows 127.0.0.1.
Most tests treat every address as public (the `public` fixture).
"""

from __future__ import annotations

import http.server
import socket
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from opensak.macro import FolderApproval, MacroError, MacroRuntime, WriteApproval
from opensak.macro import db_access, net_access
from opensak.macro.net_access import HostPermission, NetError, check_url, is_local_address
from opensak.utils import flags


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, content_type: str = "text/plain",
              headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        path = url.path
        if path == "/latin1":
            self._send(200, "Grüezi".encode("latin-1"), "text/plain; charset=iso-8859-1")
        elif path == "/utf8":
            self._send(200, "Grüezi ✓ \xff".encode("utf-8") + b"\xff", "text/plain")
        elif path == "/binary":
            self._send(200, bytes(range(256)), "application/octet-stream")
        elif path == "/missing":
            self._send(404, b"not here")
        elif path == "/headers":
            text = f"{self.headers.get('User-Agent')}|{self.headers.get('Cookie')}"
            self._send(200, text.encode())
        elif path == "/redirect":
            self._send(302, b"", headers={"Location": query["to"][0]})
        elif path.startswith("/chain/"):
            n = int(path.rsplit("/", 1)[1])
            if n == 0:
                self._send(200, b"end")
            else:
                self._send(302, b"", headers={"Location": f"/chain/{n - 1}"})
        elif path == "/big":
            self._send(200, b"x" * 200_000)
        elif path == "/slow":
            time.sleep(2)
            self._send(200, b"late")
        elif path == "/drip":
            # Content-Length promised, the body sent slowly
            self.send_response(200)
            self.send_header("Content-Length", str(64 * 1024 * 100))
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"y" * 64 * 1024)
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass
        else:
            self._send(200, b"ok")


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def setup(monkeypatch, server):
    """The flag on, no system proxy, and example.test hosts connected to
    the test server."""
    monkeypatch.setattr(flags, "lua_macro_net", True)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    real = socket.create_connection

    def connect(address, *args, **kwargs):
        host, port = address
        if host.endswith(".example.test"):
            address = ("127.0.0.1", server)
        return real(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", connect)
    db_access.reset_session()
    yield
    db_access.reset_session()


@pytest.fixture
def public(monkeypatch):
    """Every address counts as an internet address."""
    monkeypatch.setattr(net_access, "is_local_address", lambda _address: False)


class Host:
    """A MacroHost answering the web site question with *answer*."""

    def __init__(self, answer=FolderApproval.ONCE):
        self.answer = answer
        self.asked: list[dict] = []
        self.change_answer = WriteApproval.ALWAYS
        self.changes: list = []
        self.cancel_progress = False

    def approve_host(self, host, macro=None, local=False, secure=True, declared=False):
        self.asked.append({"host": host, "macro": macro, "local": local,
                           "secure": secure, "declared": declared})
        return self.answer

    def approve_macro_change(self, macro, change, editing=False):
        self.changes.append(change)
        return self.change_answer

    def approve_folder(self, target, folder, write, macro=None):
        return FolderApproval.ONCE              # opensak.run_macro() reads a file

    def progress(self, title, total):
        host = self

        class Window:
            updates: list = []

            def update(self, value, text):
                self.updates.append(value)
                return not host.cancel_progress

            def close(self):
                pass

        return Window()

    def end_macro(self):
        pass

    def __getattr__(self, name):
        # Bound into the opensak table at every run, never called here.
        return lambda *args: None


def url(port: int, path: str, host: str = "a.example.test") -> str:
    return f"http://{host}:{port}{path}"


def run(host, source: str, macro: Path | None = None, runtime: MacroRuntime | None = None):
    """Run *source* (from the file *macro*, if given); what it printed."""
    out: list[str] = []
    rt = runtime or MacroRuntime(host, output=out.append)
    if macro is not None:
        macro.write_text(source, encoding="utf-8")
        source = db_access.read_macro_source(macro)
    rt.run(source, macro_path=macro, base_dir=macro.parent if macro else None)
    return out


# ── The URL ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad, why", [
    ("ftp://example.com/x", "only http and https"),
    ("file:///etc/passwd", "only http and https"),
    ("data:text/plain,hi", "only http and https"),
    ("https://user:secret@example.com/", "user name or password"),
    ("https://user@example.com/", "user name or password"),
    ("https://www.geocaching.com/geocache/GC1", "never contact www.geocaching.com"),
    ("https://geocaching.com/", "never contact geocaching.com"),
    ("http://coord.info/GC1", "never contact coord.info"),
    ("https://www.waymarking.com/", "never contact www.waymarking.com"),
    ("https://wherigo.com/", "never contact wherigo.com"),
    ("https://img.groundspeak.net/x.png", "never contact img.groundspeak.net"),
    ("https://GEOCACHING.COM./", "never contact geocaching.com"),
    ("https://exa mple.com/", "not a valid host"),
    ("https://example.com/a\nb", "control characters"),
    ("https:///path", "not a valid host"),
    ("https://localhost/", "not a valid host"),
])
def test_refused_urls(bad, why):
    with pytest.raises(NetError, match=why):
        check_url(bad)


def test_url_is_normalised():
    t = check_url("HTTPS://Bücher.Example.com/a b?q=ä#frag")
    assert t.host == "xn--bcher-kva.example.com"
    assert t.url == "https://xn--bcher-kva.example.com/a%20b?q=%C3%A4"
    assert t.secure and t.port == 443
    assert check_url("http://[::1]:8080").url == "http://[::1]:8080/"


@pytest.mark.parametrize("address, local", [
    ("127.0.0.1", True), ("10.1.2.3", True), ("192.168.1.1", True), ("172.16.0.5", True),
    ("169.254.1.1", True), ("100.64.0.1", True), ("0.0.0.0", True), ("::1", True),
    ("fe80::1%eth0", True), ("::ffff:192.168.0.1", True), ("fd00::1", True),
    ("8.8.8.8", False), ("2606:4700::1111", False),
])
def test_local_addresses(address, local):
    assert is_local_address(address) is local


def test_stored_hosts_are_checked_again():
    assert HostPermission.from_dict({"host": "Example.COM", "local": 1}) == \
        HostPermission("example.com", True)
    assert HostPermission.from_dict({"host": "www.geocaching.com"}) is None
    assert HostPermission.from_dict({"host": "not a host"}) is None


def test_macro_errors_name_the_url(server, public):
    with pytest.raises(MacroError, match="only http and https URLs can be requested: ftp://x"):
        run(Host(), 'opensak.get_url("ftp://x.example.com/")')
    # A geocaching.com URL is refused without asking the user.
    host = Host()
    with pytest.raises(MacroError, match="never contact www.geocaching.com"):
        run(host, 'opensak.get_url("https://www.geocaching.com/")')
    assert host.asked == []


def test_without_the_flag_get_url_is_not_registered(monkeypatch):
    monkeypatch.setattr(flags, "lua_macro_net", False)
    assert run(Host(), "print(opensak.get_url == nil)") == ["true"]


# ── Approval ────────────────────────────────────────────────────────────────


def test_denied_host_fails_and_is_not_asked_again(server, public, tmp_path):
    host = Host(FolderApproval.DENY)
    source = (f'local ok, err = pcall(opensak.get_url, "{url(server, "/")}")\n'
              "print(ok, err)\n"
              f'print((pcall(opensak.get_url, "{url(server, "/other")}")))\n')
    out = run(host, source, tmp_path / "deny.lua")
    assert out[0].startswith("false") and "did not allow contacting a.example.test" in out[0]
    assert out[1] == "false"
    assert len(host.asked) == 1
    assert host.asked[0]["host"] == "a.example.test" and host.asked[0]["secure"] is False
    assert db_access.approvals() == []


def test_once_lasts_for_this_run_only(server, public, tmp_path):
    host = Host(FolderApproval.ONCE)
    macro = tmp_path / "once.lua"
    source = (f'print((opensak.get_url("{url(server, "/")}")))\n'
              f'print((opensak.get_url("{url(server, "/x")}")))\n')
    assert run(host, source, macro) == ["ok", "ok"]
    assert len(host.asked) == 1
    run(host, source, macro)
    assert len(host.asked) == 2
    assert db_access.approvals() == []


def test_always_is_stored_with_the_macro(server, public, tmp_path):
    host = Host(FolderApproval.ALWAYS)
    macro = tmp_path / "always.lua"
    source = ("-- Network: a.example.test\n"
              f'print((opensak.get_url("{url(server, "/")}")))\n')
    run(host, source, macro)
    run(host, source, macro)
    assert len(host.asked) == 1 and host.asked[0]["declared"] is True
    [record] = db_access.approvals()
    assert record.hosts == (HostPermission("a.example.test"),) and not record.write
    assert record.declared == ("network:a.example.test",)
    # Another macro has to ask on its own.
    run(host, source, tmp_path / "other.lua")
    assert len(host.asked) == 2


def test_undeclared_host_is_marked(server, public, tmp_path):
    host = Host()
    run(host, f'opensak.get_url("{url(server, "/")}")', tmp_path / "undeclared.lua")
    assert host.asked[0]["declared"] is False


def test_changed_macro_is_asked_before_it_contacts_the_host(server, public, tmp_path):
    host = Host(FolderApproval.ALWAYS)
    macro = tmp_path / "changed.lua"
    run(host, f'-- Version: 1\nopensak.get_url("{url(server, "/")}")\n', macro)

    # Replaced by an update: asked whether it may have its web site again.
    host.change_answer = WriteApproval.DENY
    new = f'-- Version: 2\nopensak.get_url("{url(server, "/")}")\n'
    with pytest.raises(MacroError, match="did not allow contacting a.example.test"):
        run(host, new, macro)
    [change] = host.changes
    assert change.hosts == (HostPermission("a.example.test"),)
    assert (change.old_version, change.new_version) == ("1", "2")
    assert len(host.asked) == 1            # declined as a whole: no second question

    host.change_answer = WriteApproval.ALWAYS
    run(host, new, macro)
    assert len(host.changes) == 2 and len(host.asked) == 1
    assert db_access.approvals()[0].version == "2"


def test_unsaved_macro_gets_the_host_for_one_run(server, public):
    host = Host(FolderApproval.ALWAYS)
    source = f'opensak.get_url("{url(server, "/")}")'
    run(host, source)
    run(host, source)
    assert len(host.asked) == 2 and host.asked[0]["macro"] is None
    assert db_access.approvals() == []


def test_called_macro_needs_its_own_approval(server, public, tmp_path):
    host = Host(FolderApproval.ONCE)
    inner = tmp_path / "inner.lua"
    inner.write_text(f'opensak.get_url("{url(server, "/")}")', encoding="utf-8")
    run(host, f'opensak.get_url("{url(server, "/")}")\nopensak.run_macro("inner.lua")',
        tmp_path / "outer.lua")
    assert [a["macro"].name for a in host.asked] == ["outer.lua", "inner.lua"]


def test_at_most_five_questions_per_run(server, public, tmp_path):
    host = Host(FolderApproval.DENY)
    calls = "\n".join(
        f'print(select(2, pcall(opensak.get_url, "{url(server, "/", f"h{i}.example.test")}")))'
        for i in range(7))
    out = run(host, calls, tmp_path / "many.lua")
    assert len(host.asked) == net_access.MAX_HOST_QUESTIONS_PER_RUN
    assert "did not allow contacting h4.example.test" in out[4]
    assert "h5.example.test is not approved, and this run has asked about 5 web sites" in out[5]


def test_without_a_user_interface_an_unapproved_host_fails(server, public):
    class Headless:
        def end_macro(self):
            pass

        def __getattr__(self, name):
            if name == "approve_host":
                raise AttributeError(name)
            return lambda *args: None

    with pytest.raises(MacroError, match="no user interface to ask"):
        MacroRuntime(Headless(), output=lambda _t: None).run(
            f'opensak.get_url("{url(server, "/")}")')


# ── Redirects ───────────────────────────────────────────────────────────────


def test_redirect_to_another_host_is_asked_about(server, public, tmp_path):
    host = Host(FolderApproval.ONCE)
    target = url(server, "/final", "b.example.test")
    source = (f'local body, info = opensak.get_url("{url(server, "/redirect?to=" + target)}")\n'
              "print(body, info.url, info.status)")
    assert run(host, source, tmp_path / "redirect.lua") == [f"ok\t{target}\t200"]
    assert [a["host"] for a in host.asked] == ["a.example.test", "b.example.test"]


def test_redirect_to_a_denied_host_fails(server, public, tmp_path):
    class Picky(Host):
        def approve_host(self, host, **kw):
            super().approve_host(host, **kw)
            return FolderApproval.ONCE if host == "a.example.test" else FolderApproval.DENY

    target = url(server, "/final", "b.example.test")
    with pytest.raises(MacroError, match="did not allow contacting b.example.test"):
        run(Picky(), f'opensak.get_url("{url(server, "/redirect?to=" + target)}")',
            tmp_path / "picky.lua")


def test_redirect_to_a_blocked_or_other_scheme_fails(server, public):
    for to, why in (("https://www.geocaching.com/x", "never contact"),
                    ("ftp://a.example.test/x", "HTTP Error 302|only http")):
        with pytest.raises(MacroError, match=why):
            run(Host(), f'opensak.get_url("{url(server, "/redirect?to=" + to)}")')


def test_at_most_five_redirects(server, public):
    assert run(Host(), f'print((opensak.get_url("{url(server, "/chain/5")}")))') == ["end"]
    with pytest.raises(MacroError, match="more than 5 redirects"):
        run(Host(), f'opensak.get_url("{url(server, "/chain/6")}")')


# ── The local network ───────────────────────────────────────────────────────


def test_local_address_is_asked_about_as_such(server, tmp_path):
    host = Host(FolderApproval.ALWAYS)
    macro = tmp_path / "nas.lua"
    run(host, f'opensak.get_url("{url(server, "/", "127.0.0.1")}")', macro)
    assert host.asked[0]["local"] is True
    assert db_access.approvals()[0].hosts == (HostPermission("127.0.0.1", local=True),)


def test_public_approval_does_not_cover_a_local_address(server, tmp_path):
    """DNS rebinding: an approved internet host now leads to the local
    network. The address actually connected to is checked."""
    macro = tmp_path / "rebind.lua"
    source = f'opensak.get_url("{url(server, "/")}")'
    macro.write_text(source, encoding="utf-8")
    db_access.grant_host(macro, "a.example.test", False, db_access.source_hash(source))
    host = Host(FolderApproval.DENY)
    with pytest.raises(MacroError, match="did not allow contacting a.example.test"):
        run(host, source, macro)
    [asked] = host.asked
    assert asked["local"] is True


# ── The response ────────────────────────────────────────────────────────────


def test_text_is_decoded_with_the_servers_charset(server, public):
    source = (f'local a = opensak.get_url("{url(server, "/latin1")}")\n'
              f'local b, info = opensak.get_url("{url(server, "/utf8")}")\n'
              "print(a, b, info.content_type)")
    assert run(Host(), source) == ["Grüezi\tGrüezi ✓ ÿ�\ttext/plain"]


def test_binary_returns_the_bytes(server, public):
    source = (f'local b = opensak.get_url("{url(server, "/binary")}", {{ binary = true }})\n'
              "print(#b, b:byte(1), b:byte(256))")
    assert run(Host(), source) == ["256\t0\t255"]


def test_http_error_status_is_returned(server, public):
    source = (f'local body, info = opensak.get_url("{url(server, "/missing")}")\n'
              "print(body, info.status)")
    assert run(Host(), source) == ["not here\t404"]


def test_fixed_user_agent_and_no_cookies(server, public):
    out = run(Host(), f'print((opensak.get_url("{url(server, "/headers")}")))')
    assert out == [f"{net_access.USER_AGENT}|None"]


def test_too_large_body_fails(server, public):
    with pytest.raises(MacroError, match="larger than 1000 bytes"):
        run(Host(), f'opensak.get_url("{url(server, "/big")}", {{ max_bytes = 1000 }})')


def test_timeout_fails(server, public):
    with pytest.raises(MacroError, match="did not answer within 1 s|timed out"):
        run(Host(), f'opensak.get_url("{url(server, "/slow")}", {{ timeout = 1 }})')


@pytest.mark.parametrize("options, why", [
    ("{ timeout = 0 }", "timeout must be a number from 1 to 120"),
    ("{ timeout = 121 }", "timeout must be a number from 1 to 120"),
    ("{ max_bytes = 60 * 1024 * 1024 }", "max_bytes must be a number"),
    ("{ binary = 1 }", "binary must be true or false"),
    ("{ method = 'POST' }", "unknown option"),
])
def test_bad_options(server, options, why):
    with pytest.raises(MacroError, match=why):
        run(Host(), f'opensak.get_url("{url(server, "/")}", {options})')


def test_cancel_stops_a_running_download(server, public):
    host = Host()
    out: list[str] = []
    rt = MacroRuntime(host, output=out.append)
    threading.Timer(0.3, rt.cancel).start()
    started = time.monotonic()
    with pytest.raises(MacroError, match="cancelled"):
        rt.run(f'opensak.get_url("{url(server, "/drip")}")')
    assert time.monotonic() - started < 3       # the whole body takes 5 s


def test_cancel_in_the_progress_window(server, public):
    host = Host()
    host.cancel_progress = True
    with pytest.raises(MacroError, match="cancelled by the user"):
        run(host, f'opensak.get_url("{url(server, "/drip")}", {{ caption = "Data" }})')


def test_progress_window_shows_percent(server, public):
    host = Host()
    windows = []
    real = host.progress

    def progress(title, total):
        windows.append((title, total, real(title, total)))
        return windows[-1][2]

    host.progress = progress
    run(host, f'opensak.get_url("{url(server, "/big")}", {{ caption = "Data" }})')
    [(title, total, window)] = windows
    assert (title, total) == ("Data", 100) and window.updates[-1] == 100


# ── Limits per run ──────────────────────────────────────────────────────────


def test_request_limit_per_run(server, public, monkeypatch):
    monkeypatch.setattr(net_access, "MAX_REQUESTS_PER_RUN", 2)
    monkeypatch.setattr(net_access, "MIN_INTERVAL_S", 0)
    calls = "\n".join(f'opensak.get_url("{url(server, "/")}")' for _ in range(3))
    with pytest.raises(MacroError, match="at most 2 requests per run"):
        run(Host(), calls)


def test_pause_between_requests_to_one_host(server, public, monkeypatch):
    monkeypatch.setattr(net_access, "MIN_INTERVAL_S", 0.5)
    started = time.monotonic()
    run(Host(), f'opensak.get_url("{url(server, "/")}")\n'
                f'opensak.get_url("{url(server, "/", "b.example.test")}")')
    assert time.monotonic() - started < 0.5     # different hosts: no pause
    started = time.monotonic()
    run(Host(), f'opensak.get_url("{url(server, "/")}")\nopensak.get_url("{url(server, "/")}")')
    assert time.monotonic() - started >= 0.5
