"""
crash_report.py — Catch crashes and help the user report them.

Two kinds of crash are covered:

  - Python exceptions nobody caught (in the GUI thread, a QThread or a
    plain thread). sys.excepthook / threading.excepthook log the traceback
    and show the crash dialog, so a windowed build no longer swallows them
    silently on a non-existent stderr.

  - Hard crashes Python cannot react to (a segfault in Qt/WebEngine, the
    process being killed). faulthandler writes the native stack to
    crash_native.log, and a session lock file that is only removed on a
    clean exit tells the next start that the last session did not end
    normally — the user is then offered to report it with the previous
    session's log (opensak.log.previous, issue #737).

Nothing is ever sent automatically: the report goes to a prefilled GitHub
issue or e-mail, or is copied for a post in the Facebook group, only when
the user clicks the button, and the text is scrubbed of home paths, the user name and tokens first.
"""

from __future__ import annotations

import atexit
import faulthandler
import getpass
import logging
import os
import platform
import re
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

if TYPE_CHECKING:
    from PySide6.QtCore import QLockFile

logger = logging.getLogger("opensak.crash")

GITHUB_NEW_ISSUE_URL = "https://github.com/OpenSAK-Org/OpenSAK/issues/new"
SUPPORT_EMAIL = "hello@opensak.com"
FACEBOOK_GROUP_URL = "https://www.facebook.com/groups/2062154441232996"

# GitHub refuses very long URLs (~8 KB); mail clients choke much earlier
# (Outlook/Windows mailto handlers at ~2 KB). The body is cut to fit.
_GITHUB_BODY_MAX = 6000
_MAILTO_BODY_MAX = 1800
# Lines of the previous session's log put in an unclean-shutdown report.
_LOG_TAIL_LINES = 40

_NATIVE_LOG_NAME = "crash_native.log"
_SESSION_LOCK_NAME = "session.lock"


@dataclass
class PreviousCrash:
    """What is known about a previous session that did not exit cleanly."""
    native_dump: str
    log_tail: str


# Module state — set by install(), cleared by shutdown().
_installed = False
_native_file = None
_session_lock: "QLockFile | None" = None
_previous_crash: PreviousCrash | None = None
_bridge = None
_shown: set[str] = set()
_dialog_open = False


def _app_data_dir() -> Path:
    from opensak.config import get_app_data_dir
    return get_app_data_dir()


# ── Report text ──────────────────────────────────────────────────────────────

def system_info() -> str:
    """Version and platform lines put at the top of every report."""
    from opensak import __version__
    try:
        from PySide6 import __version__ as pyside_version
    except ImportError:  # pragma: no cover - PySide6 is a hard dependency
        pyside_version = "?"
    try:
        from opensak.msix import is_msix_packaged
        msix = is_msix_packaged()
    except Exception:
        msix = False
    if msix:
        build = "Microsoft Store"
    elif getattr(sys, "frozen", False):
        build = "installer/frozen"
    else:
        build = "source"
    return (
        f"OpenSAK: {__version__} ({build})\n"
        f"OS: {platform.platform()}\n"
        f"Python: {platform.python_version()}\n"
        f"PySide6: {pyside_version}"
    )


_TOKEN_RE = re.compile(
    r"(?i)((?:access|refresh|id)_?token|authorization|bearer|password|secret|api_?key)"
    r"([\"']?\s*[:=]\s*[\"']?|\s+)((?:bearer\s+)?[^\s\"',}]+)"
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub(text: str) -> str:
    """Remove personal data the user would not want in a public issue."""
    home = str(Path.home())
    if home and home not in ("/", "\\"):
        # Both slash styles — tracebacks and logs mix them on Windows.
        for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            text = re.sub(re.escape(variant), "~", text, flags=re.IGNORECASE)
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if len(user) >= 3:
        text = re.sub(rf"(?i)(?<![\w]){re.escape(user)}(?![\w])", "<user>", text)
    text = _TOKEN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _EMAIL_RE.sub(
        lambda m: m.group(0) if m.group(0).lower() == SUPPORT_EMAIL else "<email>", text
    )
    return text


def format_exception_report(exc_type, exc_value, exc_tb) -> str:
    """The full report text for an uncaught exception."""
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    return scrub(f"{system_info()}\n\n{tb}").rstrip()


def format_previous_crash_report(crash: PreviousCrash) -> str:
    """The full report text for a session that did not exit cleanly."""
    parts = [system_info(), "", "The previous OpenSAK session did not exit normally."]
    if crash.native_dump.strip():
        parts += ["", "Native crash dump:", crash.native_dump.strip()]
    if crash.log_tail.strip():
        parts += ["", f"Last {_LOG_TAIL_LINES} lines of the previous log:", crash.log_tail.strip()]
    return scrub("\n".join(parts)).rstrip()


def report_title(report: str) -> str:
    """A short issue title — the exception line of a traceback if there is one."""
    lines = [ln for ln in report.splitlines() if ln.strip()]
    for line in reversed(lines):
        if not line.startswith((" ", "\t")) and re.match(r"^[\w.]+(Error|Exception|Interrupt|Exit|Warning)\b", line):
            return f"Crash: {line.strip()}"[:120]
    return "Crash: OpenSAK did not exit normally"


def _fit(body: str, limit: int) -> str:
    if len(body) <= limit:
        return body
    # Keep the head (version/OS) and the tail (where the exception is).
    note = "\n\n[… shortened — please attach the log file …]\n\n"
    keep = limit - len(note)
    head = keep // 3
    return body[:head] + note + body[-(keep - head):]


def _log_hint(previous_session: bool) -> str:
    if previous_session:
        return "opensak.log.previous (Help → Open log from last restart)"
    return "opensak.log (Help → Open log file)"


def _issue_body(report: str, previous_session: bool) -> str:
    return (
        "**What were you doing when it happened?**\n\n\n\n"
        "**Crash report**\n\n```\n" + report + "\n```\n\n"
        f"_Please also drag {_log_hint(previous_session)} into this issue._\n"
    )


def github_issue_url(report: str, previous_session: bool = False) -> str:
    """A prefilled 'new issue' URL. GitHub cannot attach files via URL."""
    # Shorten the report, not the wrapper, so the code fence stays closed.
    overhead = len(_issue_body("", previous_session))
    body = _issue_body(_fit(report, _GITHUB_BODY_MAX - overhead), previous_session)
    return GITHUB_NEW_ISSUE_URL + "?" + urlencode(
        {"title": report_title(report), "body": body, "labels": "bug"},
        quote_via=quote,
    )


def mailto_url(report: str, previous_session: bool = False) -> str:
    """A mailto: link with the (shortened) report as the body."""
    body = (
        "What were you doing when it happened?\n\n\n\n"
        + _fit(report, _MAILTO_BODY_MAX)
        + f"\n\nPlease attach {_log_hint(previous_session)}.\n"
    )
    return (
        f"mailto:{SUPPORT_EMAIL}?subject={quote(report_title(report))}"
        f"&body={quote(body)}"
    )


# ── Unclean-shutdown detection ──────────────────────────────────────────────

def _read_tail(path: Path, lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _detect_previous_crash(app_dir: Path) -> PreviousCrash | None:
    """
    Take this session's lock and report whether the last session crashed.

    QLockFile stores the owning PID: if the file is still there but tryLock()
    succeeds, its owner is gone without unlocking — it crashed or was killed.
    If tryLock() fails another OpenSAK is running right now, which is not a
    crash (and this instance then runs without a lock of its own).
    """
    global _session_lock
    from PySide6.QtCore import QLockFile

    lock_path = app_dir / _SESSION_LOCK_NAME
    existed = lock_path.exists()
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)  # only a dead PID makes it stale, never age
    if not lock.tryLock(0):
        logger.info("crash: another OpenSAK instance holds the session lock")
        return None
    _session_lock = lock
    if not existed:
        return None

    from opensak.config import get_previous_log_path
    native = _read_tail(app_dir / _NATIVE_LOG_NAME, 200)
    logger.warning("crash: previous session did not exit cleanly")
    return PreviousCrash(
        native_dump=native,
        log_tail=_read_tail(get_previous_log_path(), _LOG_TAIL_LINES),
    )


def _enable_faulthandler(app_dir: Path) -> None:
    global _native_file
    try:
        # "w" — the dump of the previous session has been read by now.
        _native_file = open(app_dir / _NATIVE_LOG_NAME, "w", encoding="utf-8")
        faulthandler.enable(file=_native_file, all_threads=True)
    except (OSError, RuntimeError):
        logger.warning("crash: could not enable faulthandler", exc_info=True)


# ── Hooks ────────────────────────────────────────────────────────────────────

def _excepthook(exc_type, exc_value, exc_tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    _handle(exc_type, exc_value, exc_tb)


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    _handle(args.exc_type, args.exc_value, args.exc_traceback)


def _handle(exc_type, exc_value, exc_tb) -> None:
    """Log the exception and hand it to the GUI thread for the dialog."""
    try:
        logger.critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        report = format_exception_report(exc_type, exc_value, exc_tb)
    except Exception:
        # Never let the reporter itself raise out of an excepthook.
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    if _bridge is None:
        return
    from PySide6.QtCore import QThread
    if _bridge.thread() is QThread.currentThread() and QThread.currentThread().loopLevel() == 0:
        # GUI thread with no event loop yet (startup before app.exec(), or an
        # exception escaping main()): a queued signal would never be
        # delivered, so show the dialog right here — nothing to re-enter.
        _show_exception_dialog(report)
    else:
        # Always queued, also on the GUI thread: the excepthook runs while the
        # failing slot / paint event is still on the stack, and dlg.exec()
        # there would start a nested event loop inside it.
        _bridge.crashed.emit(report)


def _show_exception_dialog(report: str) -> None:
    global _dialog_open
    # The same exception repeating (e.g. in a paint event) would otherwise
    # open one dialog after another; it is still logged every time.
    key = report.splitlines()[-1] if report else ""
    if _dialog_open or key in _shown:
        return
    _shown.add(key)
    _dialog_open = True
    try:
        from opensak.gui.dialogs.crash_report_dialog import CrashReportDialog
        dlg = CrashReportDialog(report, previous_session=False)
        dlg.exec()
        if dlg.quit_requested:
            # Not app.quit(): the error may have left the window in a state
            # where a normal close (and its exit backup) fails again. The
            # database is in WAL mode, so a hard exit loses nothing committed.
            shutdown()
            logging.shutdown()
            os._exit(1)
    except Exception:
        logger.exception("crash: could not show the crash dialog")
    finally:
        _dialog_open = False


def install(app_dir: Path | None = None) -> None:
    """
    Install the crash hooks. Call once from app.main(), after logging is set
    up and the QApplication exists. Must run before anything shows a window.
    """
    global _installed, _previous_crash, _bridge
    if _installed:
        return
    app_dir = app_dir or _app_data_dir()
    try:
        _previous_crash = _detect_previous_crash(app_dir)
    except Exception:
        logger.warning("crash: unclean-shutdown check failed", exc_info=True)
    # The native dump file belongs to the lock holder: without the lock,
    # another instance's faulthandler may be writing to it, and opening it
    # here would truncate that dump. Our own dump would not be read anyway —
    # only the next lock holder looks at the file.
    if _session_lock is not None:
        _enable_faulthandler(app_dir)

    from PySide6.QtCore import QObject, Qt, Signal, Slot

    class _Bridge(QObject):
        # The slot is a method of this GUI-thread object (not a plain
        # function), so the queued emit is delivered on the GUI thread even
        # when it comes from a worker thread.
        crashed = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self.crashed.connect(self._on_crashed, Qt.ConnectionType.QueuedConnection)

        @Slot(str)
        def _on_crashed(self, report: str) -> None:
            _show_exception_dialog(report)

    _bridge = _Bridge()

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    # Any normal interpreter exit (incl. sys.exit() after a startup error
    # the user was already told about) counts as clean; a native crash or
    # a kill never gets here, so its lock file stays behind.
    atexit.register(shutdown)
    _installed = True


def take_previous_crash() -> PreviousCrash | None:
    """The previous session's crash, once — None if there was none."""
    global _previous_crash
    crash, _previous_crash = _previous_crash, None
    return crash


def show_previous_crash_notice(parent=None) -> None:
    """If the last session crashed, offer to report it. Call after startup."""
    crash = take_previous_crash()
    if crash is None:
        return
    try:
        from opensak.gui.dialogs.crash_report_dialog import CrashReportDialog
        CrashReportDialog(
            format_previous_crash_report(crash), previous_session=True, parent=parent
        ).exec()
    except Exception:
        logger.exception("crash: could not show the previous-crash dialog")


def shutdown() -> None:
    """Mark the session as cleanly ended. Call when the app exits normally."""
    global _native_file, _session_lock, _installed, _bridge
    if _session_lock is not None:
        _session_lock.unlock()
        _session_lock = None
    if _native_file is not None:
        try:
            faulthandler.disable()
            _native_file.close()
        except (OSError, ValueError):
            pass
        _native_file = None
    if _installed:
        sys.excepthook = sys.__excepthook__
        threading.excepthook = threading.__excepthook__
    _bridge = None
    _installed = False
