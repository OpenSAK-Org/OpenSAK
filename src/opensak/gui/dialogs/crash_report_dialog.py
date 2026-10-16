"""
src/opensak/gui/dialogs/crash_report_dialog.py — "OpenSAK ran into a
problem" dialog (see opensak/crash_report.py).

Shown for an uncaught exception, and at startup when the previous session
did not exit cleanly. The scrubbed report is shown read-only so the user
sees exactly what would be shared; nothing leaves the machine until they
click GitHub or e-mail, and both only open a prefilled page/mail.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from opensak import crash_report
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr


class CrashReportDialog(QDialog):
    def __init__(
        self,
        report: str,
        previous_session: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._report = report
        self._previous_session = previous_session
        self.quit_requested = False
        self.setWindowTitle(tr("crash_dialog_title"))
        self.setMinimumWidth(620)
        self._setup_ui()
        clamp_dialog_height_to_screen(self, parent)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        intro_key = "crash_dialog_previous_msg" if self._previous_session else "crash_dialog_msg"
        intro = QLabel(tr(intro_key, email=crash_report.SUPPORT_EMAIL))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._text = QPlainTextEdit(self._report)
        self._text.setReadOnly(True)
        self._text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._text.setMinimumHeight(220)
        layout.addWidget(self._text, 1)

        privacy = QLabel(tr("crash_dialog_privacy"))
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: gray;")
        layout.addWidget(privacy)

        report_row = QHBoxLayout()
        self._btn_github = QPushButton(tr("crash_dialog_github"))
        self._btn_github.clicked.connect(self._open_github)
        self._btn_email = QPushButton(tr("crash_dialog_email"))
        self._btn_email.clicked.connect(self._open_email)
        self._btn_copy = QPushButton(tr("crash_dialog_copy"))
        self._btn_copy.clicked.connect(self._copy)
        self._btn_log = QPushButton(tr("crash_dialog_show_log"))
        self._btn_log.clicked.connect(self._show_log)
        for btn in (self._btn_github, self._btn_email, self._btn_copy, self._btn_log):
            report_row.addWidget(btn)
        layout.addLayout(report_row)

        close_row = QHBoxLayout()
        close_row.addStretch()
        if not self._previous_session:
            self._btn_quit = QPushButton(tr("crash_dialog_quit"))
            self._btn_quit.clicked.connect(self._quit)
            close_row.addWidget(self._btn_quit)
        self._btn_close = QPushButton(
            tr("close" if self._previous_session else "crash_dialog_continue")
        )
        self._btn_close.setDefault(True)
        self._btn_close.clicked.connect(self.accept)
        close_row.addWidget(self._btn_close)
        layout.addLayout(close_row)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _open_github(self) -> None:
        QDesktopServices.openUrl(
            QUrl(crash_report.github_issue_url(self._report, self._previous_session))
        )
        # The issue page cannot take the log via URL — show it to drag in.
        self._show_log()

    def _open_email(self) -> None:
        QDesktopServices.openUrl(
            QUrl(crash_report.mailto_url(self._report, self._previous_session))
        )
        self._show_log()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._report)
        self._btn_copy.setText(tr("crash_dialog_copied"))

    def _show_log(self) -> None:
        """Open the folder holding the log, so it can be dragged/attached."""
        from opensak.config import get_log_path, get_previous_log_path
        log = get_previous_log_path() if self._previous_session else get_log_path()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log.parent)))

    def _quit(self) -> None:
        self.quit_requested = True
        self.reject()
