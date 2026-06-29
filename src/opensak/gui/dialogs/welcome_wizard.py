"""
src/opensak/gui/dialogs/welcome_wizard.py — Velkomst-wizard til første opstart.

Issue #210: Bruger vælger installations-mappe og database-mappe ved første opstart.
Issue #358: Kan også genåbnes manuelt fra Settings → Advanced.

5 trin:
  1. Velkomst + sprog-valg
  2. Installationsmappe (settings + logs)
  3. Databasemappe
  4. GC profil (brugernavn + hjemkoordinat)
  5. Færdig
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget, QComboBox,
)

from opensak.lang import tr, AVAILABLE_LANGUAGES, current_language
from opensak.gui.dialogs.widgets import DirRow as _DirRow


# ── Individuelle trin ─────────────────────────────────────────────────────────

def _make_header(title: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 8)
    lbl = QLabel(title)
    font = QFont()
    font.setPointSize(13)
    font.setBold(True)
    lbl.setFont(font)
    lay.addWidget(lbl)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setWordWrap(True)
        sub.setStyleSheet("color: palette(mid);")
        lay.addWidget(sub)
    return w


def _page_welcome() -> tuple[QWidget, QComboBox]:
    """Trin 1: Velkomst + sprog-valg."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.addWidget(_make_header(
        tr("setup_welcome_title"),
        tr("wizard_welcome_subtitle"),
    ))

    lay.addSpacing(12)
    lay.addWidget(QLabel(tr("wizard_language_label")))

    lang_combo = QComboBox()
    for code, name in AVAILABLE_LANGUAGES.items():
        lang_combo.addItem(name, code)
    # Vælg nuværende sprog
    cur = current_language()
    for i in range(lang_combo.count()):
        if lang_combo.itemData(i) == cur:
            lang_combo.setCurrentIndex(i)
            break
    lay.addWidget(lang_combo)
    lay.addStretch()
    return page, lang_combo


def _page_install_dir(default: Path) -> tuple[QWidget, _DirRow]:
    """Trin 2: Vælg installationsmappe (settings + logs)."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.addWidget(_make_header(
        tr("wizard_install_dir_title"),
        tr("wizard_install_dir_subtitle"),
    ))
    lay.addSpacing(8)
    row = _DirRow(default)
    lay.addWidget(row)
    note = QLabel(tr("wizard_install_dir_note"))
    note.setWordWrap(True)
    note.setStyleSheet("color: palette(mid); font-size: 11px;")
    lay.addWidget(note)
    lay.addStretch()
    return page, row


def _page_db_dir(default: Path) -> tuple[QWidget, _DirRow]:
    """Trin 3: Vælg databasemappe."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.addWidget(_make_header(
        tr("wizard_db_dir_title"),
        tr("wizard_db_dir_subtitle"),
    ))
    lay.addSpacing(8)
    row = _DirRow(default)
    lay.addWidget(row)
    note = QLabel(tr("wizard_db_dir_note"))
    note.setWordWrap(True)
    note.setStyleSheet("color: palette(mid); font-size: 11px;")
    lay.addWidget(note)
    lay.addStretch()
    return page, row


def _page_gc_profile() -> tuple[QWidget, QLineEdit, QLineEdit]:
    """Trin 4: GC brugernavn + hjemkoordinat."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.addWidget(_make_header(
        tr("wizard_gc_title"),
        tr("wizard_gc_subtitle"),
    ))
    lay.addSpacing(8)

    lay.addWidget(QLabel(tr("wizard_gc_username_label")))
    username_edit = QLineEdit()
    username_edit.setPlaceholderText(tr("wizard_gc_username_placeholder"))
    lay.addWidget(username_edit)

    lay.addSpacing(8)
    lay.addWidget(QLabel(tr("wizard_gc_home_label")))
    home_edit = QLineEdit()
    home_edit.setPlaceholderText("N55 47.123 E012 25.456")
    lay.addWidget(home_edit)

    hint = QLabel(tr("wizard_gc_skip_hint"))
    hint.setWordWrap(True)
    hint.setStyleSheet("color: palette(mid); font-size: 11px;")
    lay.addWidget(hint)
    lay.addStretch()
    return page, username_edit, home_edit


def _page_done() -> QWidget:
    """Trin 5: Færdig."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.addStretch()
    lay.addWidget(_make_header(
        tr("wizard_done_title"),
        tr("wizard_done_subtitle"),
    ))
    lay.addStretch()
    return page


# ── Hoved-wizard dialog ───────────────────────────────────────────────────────

class WelcomeWizard(QDialog):
    """
    Velkomst-wizard der vises ved første opstart (issue #210).

    Returnerer via exec() — brug result() til at tjekke om brugeren
    gennemførte (QDialog.DialogCode.Accepted) eller annullerede.

    Efter Accepted er install_dir, db_dir, gc_username og gc_home_location
    gemt i settings_store / AppSettings.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("wizard_window_title"))
        self.setMinimumSize(520, 380)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        from opensak.settings_store import get_install_dir, get_db_dir
        self._default_install = get_install_dir()
        # Issue #358: brug den faktiske nuværende databasemappe som default —
        # ikke installationsmappen. De er kun ens ved selve første opstart
        # (get_db_dir() falder netop tilbage til get_install_dir() i det
        # tilfælde), men ved genkørsel af wizarden skal det IKKE foreslå at
        # flytte en allerede valgt databasemappe tilbage til install-mappen.
        self._default_db = get_db_dir()

        self._setup_ui()
        self._update_buttons()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 16)

        # Trin-indikator
        self._step_lbl = QLabel()
        self._step_lbl.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(self._step_lbl)

        # Stak af sider
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # Byg alle sider
        p1, self._lang_combo = _page_welcome()
        p2, self._install_row = _page_install_dir(self._default_install)
        p3, self._db_row = _page_db_dir(self._default_db)
        p4, self._username_edit, self._home_edit = _page_gc_profile()
        p5 = _page_done()

        for p in (p1, p2, p3, p4, p5):
            self._stack.addWidget(p)

        # Knapper
        btn_lay = QHBoxLayout()
        self._back_btn = QPushButton(tr("wizard_back"))
        self._next_btn = QPushButton(tr("wizard_next"))
        self._next_btn.setDefault(True)
        skip = QPushButton(tr("wizard_skip"))
        skip.setFlat(True)

        btn_lay.addWidget(skip)
        btn_lay.addStretch()
        btn_lay.addWidget(self._back_btn)
        btn_lay.addWidget(self._next_btn)
        root.addLayout(btn_lay)

        self._back_btn.clicked.connect(self._go_back)
        self._next_btn.clicked.connect(self._go_next)
        skip.clicked.connect(self._skip)

        # Sprog-skift trigger genindlæsning af UI-tekster
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)

    @property
    def _current(self) -> int:
        return self._stack.currentIndex()

    @property
    def _total(self) -> int:
        return self._stack.count()

    def _update_buttons(self):
        i = self._current
        last = self._total - 1
        self._back_btn.setEnabled(i > 0)
        if i == last:
            self._next_btn.setText(tr("wizard_finish"))
        else:
            self._next_btn.setText(tr("wizard_next"))
        self._step_lbl.setText(
            tr("wizard_step_of", current=i + 1, total=self._total)
        )

    def _on_lang_changed(self):
        """Gem sproget og genindlæs applikationssproget øjeblikkeligt."""
        code = self._lang_combo.currentData()
        from opensak.config import set_language
        from opensak.lang import load_language
        set_language(code)
        load_language(code)

    def _go_back(self):
        if self._current > 0:
            self._stack.setCurrentIndex(self._current - 1)
            self._update_buttons()

    def _go_next(self):
        if self._current == self._total - 1:
            self._finish()
        else:
            self._stack.setCurrentIndex(self._current + 1)
            self._update_buttons()

    def _skip(self):
        """Spring wizard over — brug alle defaults."""
        self._save_all(use_defaults=True)
        self.reject()

    def _finish(self):
        """Gem alle valg og luk wizard."""
        self._save_all(use_defaults=False)
        self.accept()

    def _save_all(self, use_defaults: bool = False):
        from opensak.settings_store import set_install_dir, get_store, reset_store
        from opensak.gui.settings import get_settings

        # Installationsmappe
        install_dir = (
            self._default_install if use_defaults
            else self._install_row.path
        )
        # Databasemappe
        db_dir = (
            self._default_db if use_defaults
            else self._db_row.path
        )

        # Opret mapper
        install_dir.mkdir(parents=True, exist_ok=True)
        db_dir.mkdir(parents=True, exist_ok=True)

        # Gem installationsmappe i bootstrap.json
        set_install_dir(install_dir)

        # Gem databasemappe i store
        reset_store()  # nulstil så den finder den nye install_dir
        store = get_store()
        store.set("databases.dir", str(db_dir))
        store.set("_wizard_completed", True)

        # GC profil
        if not use_defaults:
            s = get_settings()
            username = self._username_edit.text().strip()
            if username:
                s.gc_username = username
            home = self._home_edit.text().strip()
            if home:
                from opensak.coords import parse_coords
                if parse_coords(home) is not None:
                    s.gc_home_location = home
