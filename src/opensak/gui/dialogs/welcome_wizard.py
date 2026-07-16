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

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget, QComboBox,
)

from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr, AVAILABLE_LANGUAGES, current_language
from opensak.gui.dialogs.widgets import DirRow as _DirRow

logger = logging.getLogger(__name__)


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
        from opensak.settings_store import (
            set_install_dir, get_store, reset_store,
            get_install_dir, get_db_dir,
        )
        from opensak.gui.settings import get_settings

        # Issue #562: den faktiske (nuværende) install/db-mappe FØR vi
        # skifter — bruges til at afgøre om der reelt er noget at flytte.
        # self._default_install/_default_db kan ikke bruges her, da de kun
        # afspejler værdien ved wizardens åbning, ikke nødvendigvis den
        # aktuelle disk-sandhed.
        old_install_dir = get_install_dir()
        old_db_dir = get_db_dir()

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

        # Issue #562: hvis installationsmappen ændres (typisk ved genkørsel
        # af wizarden fra Settings → Advanced), skal opensak.json — som
        # indeholder ALLE settings, inkl. listen over kendte databaser —
        # flyttes MED. Ellers loader den nye SettingsStore nedenfor fra en
        # tom/ikke-eksisterende fil på den nye sti, og hele opsætningen
        # (inkl. databases.list) ser ud til at "forsvinde", selvom den
        # gamle opensak.json stadig ligger uberørt i den gamle mappe.
        if install_dir != old_install_dir:
            old_settings_existed = (old_install_dir / "opensak.json").exists()
            moved = self._move_settings_file(old_install_dir, install_dir)
            if old_settings_existed and not moved:
                QMessageBox.warning(
                    self,
                    tr("wizard_settings_file_exists_title"),
                    tr("wizard_settings_file_exists_msg", path=str(install_dir)),
                )
            # Issue #562 follow-up: den gamle installationsmappe kan have
            # mere brugerskabt indhold end bare opensak.json — bl.a.
            # icons/-mappen (#519, brugerdefinerede ikoner) og
            # gc_token.json (Geocaching.com OAuth-token). At navngive kun
            # kendte filnavne enkeltvis ramte ikke icons/, så mappen kunne
            # aldrig blive helt tom og dermed aldrig fjernet. Flyt i stedet
            # ALT tilbageværende (filer og undermapper) generisk.
            self._move_remaining_install_dir_contents(old_install_dir, install_dir)
            self._cleanup_old_dir(old_install_dir)

        # Gem installationsmappe i bootstrap.json
        set_install_dir(install_dir)

        # Gem databasemappe i store
        reset_store()  # nulstil så den finder den (evt. flyttede) opensak.json
        store = get_store()

        # Issue #562: hvis databasemappen ændres, tilbyd at flytte de
        # fysiske .db-filer (+ -shm/-wal sidecars) med til den nye mappe —
        # samme mønster og tekster som Settings → Advanced's direkte
        # database-mappe-felt bruger. Springes over ved "Skip wizard"
        # (use_defaults), da der her ikke er foretaget noget aktivt valg.
        if db_dir != old_db_dir and not use_defaults:
            self._offer_move_databases(old_db_dir, db_dir)

        store.set("databases.dir", str(db_dir))
        store.set("_wizard_completed", True)

        # #562 follow-up: hvis databasemappen lå som en undermappe af den
        # gamle installationsmappe (fx "…/myOpenSAK" + "…/myOpenSAK/Data"),
        # var installationsmappen stadig ikke-tom da den blev forsøgt ryddet
        # op ovenfor — Data-undermappen var der jo endnu. Prøv én gang til
        # nu hvor databasemappen er flyttet/ryddet, i tilfælde af at
        # installationsmappen er blevet tom i mellemtiden.
        if install_dir != old_install_dir:
            self._cleanup_old_dir(old_install_dir)

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

    @staticmethod
    def _cleanup_old_dir(folder: Path, extra_files: tuple = ()) -> None:
        """
        Best-effort oprydning af en mappe der ikke længere skal bruges.

        Issue #562 follow-up: "Flyt og slet" (databaser) og skift af
        installationsmappe efterlod tidligere den gamle mappe liggende
        (evt. med tomme undermapper, fx et separat "Data"-niveau) selvom
        alt indhold reelt var flyttet/slettet. Sletter de navngivne
        "kendte" ekstra-filer (fx en tilbageværende log-fil, der alligevel
        nulstilles ved næste opstart) og fjerner derefter selve mappen —
        men KUN hvis den er reelt tom bagefter. Rører aldrig andre/ukendte
        filer, og går aldrig længere op i mappetræet end den givne mappe
        selv, for ikke ved et uheld at fjerne overordnede mapper brugeren
        ikke bad om at få ryddet.

        Fejler stille i alle tilfælde (fil i brug, mappe ikke tom, mappen
        findes ikke længere, osv.) — ikke kritisk, brugeren kan altid rydde
        manuelt, og en delvist gennemført flytning må ikke fremstå som en
        fejl i selve flytningen.
        """
        for name in extra_files:
            f = folder / name
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass
        try:
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass

    @staticmethod
    def _move_remaining_install_dir_contents(old_install_dir: Path, new_install_dir: Path) -> None:
        """
        Flyt alt tilbageværende indhold i den gamle installationsmappe til
        den nye — fx den brugerdefinerede icons/-mappe (#519), gc_token.json
        (Geocaching.com OAuth-token), og alt andet der måtte dukke op her i
        fremtiden. opensak.json er allerede håndteret separat på dette
        tidspunkt (_move_settings_file, med sin egen synlige advarsel ved
        kollision) og findes derfor ikke længere i den gamle mappe.

        Issue #562 follow-up: at navngive kun specifikke kendte filer (først
        kun opensak.json, siden også gc_token.json) ramte ikke icons/-mappen
        — den blev hverken flyttet eller ryddet væk, og den gamle mappe
        kunne derfor aldrig blive helt tom og dermed aldrig fjernes, uanset
        hvor meget andet der blev ryddet op.

        Sletter først log-filerne — de skal IKKE flyttes, kun væk: de
        nulstilles alligevel ved næste opstart (se logger.py), så en flyttet
        log ville bare være en forældet engangs-snapshot på den nye
        placering.

        Flytter derefter alt resterende (filer OG undermapper som helhed) —
        men rører aldrig noget der allerede findes med samme navn på
        destinationen, for ikke at overskrive noget der kan være bevidst
        (fx hvis den nye mappe allerede har sin egen icons/-mappe).

        Best-effort: enkelte filer/mapper der ikke kan flyttes (i brug,
        navnekollision) springes stille over — resten flyttes stadig, og
        den efterfølgende _cleanup_old_dir()-oprydning fjerner kun selve
        mappen hvis den reelt endte helt tom.
        """
        for name in ("opensak.log", "opensak.log.1"):
            f = old_install_dir / name
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass

        try:
            entries = list(old_install_dir.iterdir())
        except OSError:
            return

        for entry in entries:
            target = new_install_dir / entry.name
            if target.exists():
                continue  # kollision — rør det ikke, behold begge som de er
            try:
                shutil.move(str(entry), str(target))
            except OSError:
                logger.warning(
                    "Wizard: failed to move %s from %s to %s",
                    entry.name, old_install_dir, new_install_dir, exc_info=True,
                )

    @staticmethod
    def _move_settings_file(old_install_dir: Path, new_install_dir: Path) -> bool:
        """
        Flyt opensak.json fra den gamle til den nye installationsmappe.

        Issue #562: uden dette starter brugeren forfra med tomme/default
        settings hver gang installationsmappen ændres via wizarden — den
        gamle opensak.json (settings, databases.list, kolonneopsætning,
        filtre, osv.) blev aldrig rørt, kun bootstrap.json's pointer.

        Best-effort: hvis flytningen fejler (fx filen er i brug, eller
        målet allerede har en opensak.json), fortsætter wizarden alligevel
        i stedet for at crashe — brugeren ender i så fald med en frisk
        settings-fil på den nye placering, som var den tidligere (fejlende)
        opførsel, ikke værre.

        Returnerer True hvis filen blev flyttet, False ellers (intet at
        flytte, eller sprunget over pga. kollision/fejl) — så kaldestedet
        kan advare brugeren synligt i stedet for kun at logge stille, hvis
        den valgte mappe allerede indeholder en settings-fil (#562
        follow-up: gentagne test-runder med genbrugte mapper gav ellers
        ingen synlig indikation af hvorfor intet blev flyttet).
        """
        old_settings = old_install_dir / "opensak.json"
        new_settings = new_install_dir / "opensak.json"

        if not old_settings.exists():
            return False  # intet at flytte (fx allerførste opstart)
        if new_settings.exists():
            # Målet har allerede en settings-fil (fx brugeren peger tilbage
            # på en mappe der før har været install-mappe) — rør den ikke,
            # for ikke at overskrive noget der kan være bevidst.
            logger.warning(
                "Wizard: opensak.json already exists at %s — not overwriting "
                "with the one from %s", new_settings, old_settings,
            )
            return False

        try:
            shutil.move(str(old_settings), str(new_settings))
            return True
        except OSError:
            logger.warning(
                "Wizard: failed to move opensak.json from %s to %s",
                old_install_dir, new_install_dir, exc_info=True,
            )
            return False

    def _offer_move_databases(self, old_db_dir: Path, new_db_dir: Path) -> None:
        """
        Tilbyd at flytte eksisterende databaser til den nye databasemappe.

        Issue #562: uden dette peger settings på den nye (tomme) mappe,
        mens de fysiske .db-filer (+ -shm/-wal sidecars) bliver liggende i
        den gamle — databaserne "forsvinder" fra appen selvom de stadig
        findes på disk. Genbruger samme dialog-mønster og oversatte tekster
        som Settings → Advanced's direkte database-mappe-felt (se
        settings_dialog.py), så oplevelsen er konsistent uanset hvilken vej
        brugeren ændrer mappen.
        """
        from opensak.db.manager import get_db_manager

        manager = get_db_manager()
        # Issue #609: tæl kun databaser der rent faktisk har en fysisk fil
        # på disk endnu — ellers vises "You have 1 existing database(s)"
        # for det auto-oprettede "Default"-metadata-objekt ved en helt
        # frisk installation, selvom der intet er at flytte.
        existing_count = sum(1 for db in manager.databases if db.exists)
        if existing_count == 0:
            return  # ingen kendte databaser at flytte

        move_box = QMessageBox(self)
        move_box.setWindowTitle(tr("settings_move_databases_title"))
        move_box.setText(tr("settings_move_databases_msg", count=existing_count))
        move_box.setIcon(QMessageBox.Icon.Question)
        btn_move_keep = move_box.addButton(
            tr("settings_move_keep_originals"), QMessageBox.ButtonRole.AcceptRole
        )
        btn_move_delete = move_box.addButton(
            tr("settings_move_delete_originals"), QMessageBox.ButtonRole.DestructiveRole
        )
        move_box.addButton(
            tr("settings_move_skip"), QMessageBox.ButtonRole.RejectRole
        )
        move_box.exec()
        clicked = move_box.clickedButton()

        if clicked in (btn_move_keep, btn_move_delete):
            delete_originals = clicked == btn_move_delete
            errors = manager.move_databases_to(new_db_dir, delete_originals)
            if errors:
                QMessageBox.warning(
                    self,
                    tr("settings_move_errors_title"),
                    "\n".join(errors),
                )
            elif delete_originals:
                # #562 follow-up: "Flyt og slet" fjernede kun de kendte
                # .db(+ -shm/-wal)-filer — selve mappen (og evt. tomme
                # undermapper, fx et separat "Data"-niveau under
                # installationsmappen) blev efterladt. Ryd op nu den gamle
                # mappe er tom — kun hvis den rent faktisk ER tom (ingen
                # fejl ovenfor, og ingen andre/ukendte filer liggende der).
                self._cleanup_old_dir(old_db_dir)
