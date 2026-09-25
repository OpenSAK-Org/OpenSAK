"""
src/opensak/gui/dialogs/pq_email_check_dialog.py — "Check for PQ Email"
dialog (issue #443, session 2).

Mirrors GsakImportDialog's worker/threading pattern (single background
QThread, indeterminate progress bar, plain-text result log) — see that
file's docstring for the general shape. The mailbox account itself is
configured once in Settings → PQ Email (session 1); this dialog only
triggers a manual "Check now" pass and reports what it found.

Scheduled/background checking (repeating this automatically) is #445
and is not implemented here.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QCheckBox, QProgressBar, QTextEdit,
)

from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.gui.settings import get_settings
from opensak.gui.theme import hint_style
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen
from opensak.lang import tr


class PQEmailCheckWorker(QThread):
    """Kører selve mailboks-gennemgangen i baggrunden (issue #443)."""
    result_ready = Signal(object)     # PQScanResult
    error        = Signal(str, str)   # (kind: "auth" | "certificate" | "network" | "other", detail)
    # Se ImportWorker/GsakImportWorker for begrundelsen om ikke at emitte
    # et selvstændigt "done"-signal fra run() — QThread.finished bruges i stedet.

    def __init__(self, config, password: str, delete_after_import: bool, only_unseen: bool, parent=None):
        super().__init__(parent)
        self._config = config
        self._password = password
        self._delete_after_import = delete_after_import
        self._only_unseen = only_unseen

    def run(self) -> None:
        from opensak.email.connection import (
            ImapAuthError, ImapCertificateError, ImapNetworkError,
        )
        from opensak.email.service import scan_and_import
        try:
            result = scan_and_import(
                self._config, self._password,
                delete_after_import=self._delete_after_import,
                only_unseen=self._only_unseen,
            )
            self.result_ready.emit(result)
        except ImapAuthError as exc:
            self.error.emit("auth", str(exc))
        except ImapCertificateError as exc:   # #902 — før ImapNetworkError (subklasse)
            self.error.emit("certificate", str(exc))
        except ImapNetworkError as exc:
            self.error.emit("network", str(exc))
        except Exception as exc:
            self.error.emit("other", str(exc))


class PQEmailCheckDialog(QDialog):
    """Dialog til manuelt at tjekke den opsatte mailkonto for PQ-mails."""

    import_completed = Signal()   # emitted når mindst én cache blev oprettet/opdateret

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("pq_check_dialog_title"))
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)
        # Issue #811: cap this dialog's height to the screen too — see
        # clamp_dialog_height_to_screen()'s docstring in widgets.py for
        # the full reasoning. The log QTextEdit below already grows/
        # shrinks freely, so it absorbs the cap without needing a
        # separate QScrollArea wrapper (unlike settings_dialog.py's
        # fixed-height tabs).
        clamp_dialog_height_to_screen(self, parent)
        self._worker: PQEmailCheckWorker | None = None
        self._setup_ui()
        self._refresh_configured_state()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(tr("pq_check_intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._not_configured_label = QLabel(tr("pq_check_not_configured"))
        self._not_configured_label.setWordWrap(True)
        self._not_configured_label.setStyleSheet(hint_style())
        layout.addWidget(self._not_configured_label)

        self._delete_cb = QCheckBox(tr("pq_check_delete_cb"))
        layout.addWidget(self._delete_cb)

        self._only_unseen_cb = QCheckBox(tr("pq_check_only_unseen_cb"))
        layout.addWidget(self._only_unseen_cb)

        btn_row = QHBoxLayout()
        self._open_settings_btn = QPushButton(tr("pq_check_open_settings_btn"))
        self._open_settings_btn.clicked.connect(self._open_settings)
        btn_row.addWidget(self._open_settings_btn)

        btn_row.addStretch()

        self._check_btn = QPushButton(tr("pq_check_btn"))
        self._check_btn.clicked.connect(self._start_check)
        btn_row.addWidget(self._check_btn)

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)

        layout.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

    # ── Konto-status ──────────────────────────────────────────────────────────

    def _is_configured(self) -> bool:
        s = get_settings()
        return bool(s.pq_email_host and s.pq_email_username)

    def _refresh_configured_state(self) -> None:
        configured = self._is_configured()
        self._not_configured_label.setVisible(not configured)
        self._delete_cb.setChecked(get_settings().pq_email_delete_after_import)
        self._only_unseen_cb.setChecked(get_settings().pq_email_only_unseen)
        self._check_btn.setEnabled(configured)

    def _open_settings(self) -> None:
        from opensak.gui.dialogs.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        # PQ Email er fane-index 3: General, Map, Geocaching.com, PQ Email, Advanced.
        dlg._tabs.setCurrentIndex(3)
        dlg.exec()
        self._refresh_configured_state()

    # ── Tjek ──────────────────────────────────────────────────────────────────

    def _start_check(self) -> None:
        from opensak.email import credentials
        from opensak.email.connection import ImapConfig

        s = get_settings()
        password = credentials.get_password(s.pq_email_username)
        if not password:
            QMessageBox.warning(
                self, tr("pq_check_dialog_title"), tr("pq_check_no_password")
            )
            return

        s.pq_email_delete_after_import = self._delete_cb.isChecked()
        s.pq_email_only_unseen = self._only_unseen_cb.isChecked()

        config = ImapConfig(
            host=s.pq_email_host,
            port=s.pq_email_port,
            use_ssl=s.pq_email_use_ssl,
            username=s.pq_email_username,
        )

        self._check_btn.setEnabled(False)
        self._open_settings_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._log.clear()
        self._append_log(tr("pq_check_checking"))

        self._worker = PQEmailCheckWorker(
            config, password, self._delete_cb.isChecked(),
            self._only_unseen_cb.isChecked(), self,
        )
        self._worker.result_ready.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_result(self, result) -> None:
        if not result.outcomes:
            self._append_log(tr("pq_check_no_new_mail"))
            return

        any_success = False
        for outcome in result.outcomes:
            if outcome.imported:
                any_success = True
                db_label = outcome.matched_db_name or "?"
                count = outcome.created + outcome.updated
                self._append_log(tr(
                    "pq_check_entry_success",
                    name=outcome.pq_name, count=count, db=db_label,
                ))
            else:
                error_text = outcome.errors[0] if outcome.errors else ""
                self._append_log(tr(
                    "pq_check_entry_error",
                    name=outcome.pq_name, error=error_text,
                ))

        if any_success:
            self.import_completed.emit()

    def _on_error(self, kind: str, detail: str) -> None:
        if kind == "auth":
            msg = tr("pq_check_error_auth", detail=detail)
        elif kind == "certificate":
            msg = tr("pq_check_error_certificate", detail=detail)
        elif kind == "network":
            msg = tr("pq_check_error_network", detail=detail)
        else:
            msg = tr("pq_check_error_other", detail=detail)
        self._append_log(msg)

    def _on_done(self) -> None:
        self._progress.setVisible(False)
        self._append_log(tr("pq_check_done"))
        self._check_btn.setEnabled(True)
        self._open_settings_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
        try:
            if self._worker and self._worker.isRunning():
                self._worker.wait()
        except RuntimeError:
            pass
        self._worker = None
        super().closeEvent(event)

    # ── Log helper ────────────────────────────────────────────────────────────

    def _append_log(self, text: str) -> None:
        current = self._log.toPlainText()
        self._log.setPlainText(current + ("\n" if current else "") + text)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )
