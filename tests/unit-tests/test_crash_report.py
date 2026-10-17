# tests/unit-tests/test_crash_report.py — crash catching and reporting.

import faulthandler
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from opensak import crash_report as cr


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from opensak import settings_store as ss
    monkeypatch.setattr(ss, "get_install_dir", lambda: tmp_path)
    monkeypatch.setattr(cr, "_shown", set())
    monkeypatch.setattr(cr, "_previous_crash", None)
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    faulthandler_was_on = faulthandler.is_enabled()
    yield
    cr.shutdown()
    sys.excepthook, threading.excepthook = old_hook, old_thread_hook
    # shutdown() disables faulthandler; give pytest's own back.
    if faulthandler_was_on:
        faulthandler.enable(file=sys.__stderr__)


def _raise_and_capture(exc: BaseException):
    try:
        raise exc
    except BaseException:
        return sys.exc_info()


# ── Report text ──────────────────────────────────────────────────────────────

class TestScrub:
    def test_removes_home_folder_both_slash_styles(self):
        home = str(Path.home())
        text = f"File \"{home}\\a.py\" and {home.replace(chr(92), '/')}/b.py"
        out = cr.scrub(text)
        assert home not in out
        assert home.replace("\\", "/") not in out
        assert "~" in out

    def test_removes_user_name(self, monkeypatch):
        monkeypatch.setattr(cr.getpass, "getuser", lambda: "geocacher42")
        assert cr.scrub("D:\\data\\geocacher42\\db") == "D:\\data\\<user>\\db"

    def test_short_user_name_is_left_alone(self, monkeypatch):
        # "ab" would match far too much ordinary text.
        monkeypatch.setattr(cr.getpass, "getuser", lambda: "ab")
        assert cr.scrub("tab ab") == "tab ab"

    @pytest.mark.parametrize("text", [
        'access_token="abc.def"',
        "refresh_token: abc.def",
        "Authorization: Bearer abc.def",
        "{'password': 'abc.def'}",
    ])
    def test_redacts_secrets(self, text):
        out = cr.scrub(text)
        assert "abc.def" not in out
        assert "<redacted>" in out

    def test_redacts_email_but_keeps_support_address(self):
        out = cr.scrub("from me@example.org to hello@opensak.com")
        assert "me@example.org" not in out
        assert "hello@opensak.com" in out


class TestReports:
    def test_exception_report_has_system_info_and_traceback(self):
        report = cr.format_exception_report(*_raise_and_capture(ValueError("boom")))
        assert report.startswith("OpenSAK: ")
        assert "Traceback" in report
        assert report.endswith("ValueError: boom")

    def test_title_is_the_exception_line(self):
        report = cr.format_exception_report(*_raise_and_capture(KeyError("gc_code")))
        assert cr.report_title(report) == "Crash: KeyError: 'gc_code'"

    def test_title_for_unclean_shutdown(self):
        report = cr.format_previous_crash_report(cr.PreviousCrash("", ""))
        assert cr.report_title(report) == "Crash: OpenSAK did not exit normally"

    def test_previous_report_includes_dump_and_log(self):
        report = cr.format_previous_crash_report(
            cr.PreviousCrash("Fatal Python error: Segmentation fault", "last log line")
        )
        assert "Segmentation fault" in report
        assert "last log line" in report


class TestUrls:
    def test_github_url_is_prefilled_and_bounded(self):
        report = cr.format_exception_report(*_raise_and_capture(RuntimeError("x" * 20000)))
        url = cr.github_issue_url(report)
        assert url.startswith(cr.GITHUB_NEW_ISSUE_URL + "?")
        query = parse_qs(urlsplit(url).query)
        body = query["body"][0]
        assert len(body) <= cr._GITHUB_BODY_MAX
        assert body.count("```") == 2  # the code fence survives shortening
        assert "shortened" in body
        assert query["title"][0].startswith("Crash: RuntimeError")
        assert "opensak.log (Help" in body

    def test_github_url_previous_session_points_to_previous_log(self):
        url = cr.github_issue_url("report", previous_session=True)
        assert "opensak.log.previous" in parse_qs(urlsplit(url).query)["body"][0]

    def test_mailto_is_addressed_and_bounded(self):
        report = cr.format_exception_report(*_raise_and_capture(RuntimeError("y" * 20000)))
        url = cr.mailto_url(report)
        assert url.startswith("mailto:hello@opensak.com?subject=Crash")
        body = unquote(url.split("&body=", 1)[1])
        assert len(body) < cr._MAILTO_BODY_MAX + 200


# ── Unclean-shutdown detection ──────────────────────────────────────────────

def _leave_stale_lock(path: Path) -> None:
    """Take the lock in another process that then dies without unlocking."""
    code = (
        "import os, sys\n"
        "from PySide6.QtCore import QLockFile\n"
        "l = QLockFile(sys.argv[1])\n"
        "assert l.tryLock(0)\n"
        "os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-c", code, str(path)], check=True)
    assert path.exists()


class TestPreviousCrash:
    def test_first_start_is_not_a_crash(self, tmp_path):
        assert cr._detect_previous_crash(tmp_path) is None
        assert (tmp_path / cr._SESSION_LOCK_NAME).exists()

    def test_clean_exit_is_not_a_crash(self, tmp_path):
        cr.install(tmp_path)
        cr.shutdown()
        assert not (tmp_path / cr._SESSION_LOCK_NAME).exists()
        cr.install(tmp_path)
        assert cr.take_previous_crash() is None

    def test_lock_left_by_dead_process_is_a_crash(self, tmp_path):
        _leave_stale_lock(tmp_path / cr._SESSION_LOCK_NAME)
        (tmp_path / cr._NATIVE_LOG_NAME).write_text("Fatal Python error: boom", encoding="utf-8")
        (tmp_path / "opensak.log.previous").write_text(
            "\n".join(f"line {i}" for i in range(100)), encoding="utf-8"
        )
        cr.install(tmp_path)
        crash = cr.take_previous_crash()
        assert crash is not None
        assert "boom" in crash.native_dump
        assert crash.log_tail.splitlines()[0] == f"line {100 - cr._LOG_TAIL_LINES}"
        assert cr.take_previous_crash() is None  # only offered once
        # This session's dump file starts empty.
        assert (tmp_path / cr._NATIVE_LOG_NAME).read_text(encoding="utf-8") == ""

    def test_running_second_instance_is_not_a_crash(self, tmp_path):
        from PySide6.QtCore import QLockFile
        other = QLockFile(str(tmp_path / cr._SESSION_LOCK_NAME))
        assert other.tryLock(0)
        try:
            assert cr._detect_previous_crash(tmp_path) is None
            assert cr._session_lock is None
        finally:
            other.unlock()

    def test_show_previous_crash_notice_without_crash_does_nothing(self, monkeypatch):
        import opensak.gui.dialogs.crash_report_dialog as dlg_mod
        monkeypatch.setattr(dlg_mod, "CrashReportDialog", None)  # would raise if used
        cr.show_previous_crash_notice()


# ── Hooks ────────────────────────────────────────────────────────────────────

class TestHooks:
    def test_install_and_shutdown_swap_hooks(self, tmp_path):
        cr.install(tmp_path)
        assert sys.excepthook is cr._excepthook
        assert threading.excepthook is cr._thread_excepthook
        cr.shutdown()
        assert sys.excepthook is sys.__excepthook__

    def test_excepthook_logs_and_shows_report(self, tmp_path, monkeypatch, qtbot, caplog):
        shown = []
        monkeypatch.setattr(cr, "_show_exception_dialog", shown.append)
        cr.install(tmp_path)
        sys.excepthook(*_raise_and_capture(ValueError("from gui")))
        assert len(shown) == 1 and shown[0].endswith("ValueError: from gui")
        assert "Uncaught exception" in caplog.text

    def test_worker_thread_exception_reaches_gui_thread(self, tmp_path, monkeypatch, qtbot):
        shown = []
        monkeypatch.setattr(
            cr, "_show_exception_dialog",
            lambda r: shown.append((r, threading.current_thread() is threading.main_thread())),
        )
        cr.install(tmp_path)

        def worker():
            raise RuntimeError("from worker")

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        qtbot.waitUntil(lambda: bool(shown), timeout=2000)
        report, on_main = shown[0]
        assert report.endswith("RuntimeError: from worker")
        assert on_main

    def test_keyboard_interrupt_goes_to_default_hook(self, tmp_path, monkeypatch):
        called = []
        monkeypatch.setattr(sys, "__excepthook__", lambda *a: called.append(a[0]))
        monkeypatch.setattr(cr, "_show_exception_dialog", lambda r: pytest.fail("shown"))
        cr.install(tmp_path)
        sys.excepthook(*_raise_and_capture(KeyboardInterrupt()))
        assert called == [KeyboardInterrupt]

    def test_same_error_opens_dialog_once(self, monkeypatch):
        import opensak.gui.dialogs.crash_report_dialog as dlg_mod
        opened = []

        class FakeDialog:
            quit_requested = False

            def __init__(self, report, previous_session, parent=None):
                opened.append(report)

            def exec(self):
                return 0

        monkeypatch.setattr(dlg_mod, "CrashReportDialog", FakeDialog)
        cr._show_exception_dialog("Traceback\nValueError: a")
        cr._show_exception_dialog("Traceback\nValueError: a")
        cr._show_exception_dialog("Traceback\nValueError: b")
        assert len(opened) == 2


# ── Dialog ───────────────────────────────────────────────────────────────────

class TestDialog:
    @pytest.fixture
    def opened(self, monkeypatch):
        import opensak.gui.dialogs.crash_report_dialog as dlg_mod
        urls = []
        monkeypatch.setattr(dlg_mod.QDesktopServices, "openUrl", lambda u: urls.append(u.toString()))
        return urls

    def _make(self, qtbot, previous_session=False):
        from opensak.gui.dialogs.crash_report_dialog import CrashReportDialog
        dlg = CrashReportDialog("Traceback\nValueError: x", previous_session=previous_session)
        qtbot.addWidget(dlg)
        return dlg

    def test_shows_report_read_only(self, qtbot):
        dlg = self._make(qtbot)
        assert dlg._text.toPlainText() == "Traceback\nValueError: x"
        assert dlg._text.isReadOnly()

    def test_github_opens_issue_and_log_folder(self, qtbot, opened):
        dlg = self._make(qtbot)
        dlg._btn_github.click()
        assert opened[0].startswith(cr.GITHUB_NEW_ISSUE_URL)
        assert opened[1].startswith("file:")

    def test_facebook_copies_report_and_opens_group(self, qtbot, opened):
        from PySide6.QtWidgets import QApplication
        dlg = self._make(qtbot)
        dlg._btn_facebook.click()
        assert QApplication.clipboard().text() == "Traceback\nValueError: x"
        assert opened[0] == cr.FACEBOOK_GROUP_URL
        assert opened[1].startswith("file:")

    def test_email_opens_mailto(self, qtbot, opened):
        dlg = self._make(qtbot)
        dlg._btn_email.click()
        assert opened[0].startswith("mailto:hello@opensak.com")

    def test_copy_puts_report_on_clipboard(self, qtbot):
        from PySide6.QtWidgets import QApplication
        dlg = self._make(qtbot)
        dlg._btn_copy.click()
        assert QApplication.clipboard().text() == "Traceback\nValueError: x"

    def test_quit_sets_flag(self, qtbot):
        dlg = self._make(qtbot)
        dlg._btn_quit.click()
        assert dlg.quit_requested

    def test_previous_session_has_no_quit_button(self, qtbot):
        dlg = self._make(qtbot, previous_session=True)
        assert not hasattr(dlg, "_btn_quit")
