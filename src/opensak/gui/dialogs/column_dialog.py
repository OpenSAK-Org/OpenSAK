"""
src/opensak/gui/dialogs/column_dialog.py — Vælg synlige kolonner i cachelisten.

Understøtter navngivne, gemte "Column Views" (issue #607), parallelt med
filterprofiler i filter_dialog.py: et view samler synlige kolonner, bredder,
container-display og type-display under ét navn, og ét view kan udpeges som
den globale standard, der bruges af enhver database uden sin egen eksplicitte
kolonneopsætning.
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
    QDialogButtonBox, QComboBox, QInputDialog, QSizePolicy
)
from opensak.gui.icon import OpenSAKMessageBox as QMessageBox
from opensak.lang import tr
from opensak.settings_store import get_store
from opensak.gui.icon_provider import get_corrected_coords_icon
from opensak.gui.dialogs.widgets import clamp_dialog_height_to_screen

# Alle tilgængelige kolonner: (felt_id, visningsnavn, bredde, standard_synlig)
# Kolonnestruktur: (felt_id, tr_nøgle, bredde, standard_synlig)
_ALL_COLUMNS_DEF = [
    # Standard synlige kolonner — i GSAK-rækkefølge
    # col_user_flag og col_corrected viser kun ikon i kolonneoverskriften,
    # men i Column Chooser bruges _label-varianten med læsbar tekst.
    ("user_flag",    "col_user_flag_label",    30,  True),
    ("locked",       "col_locked_label",       30,  True),
    ("gc_code",      "col_gc_code",      80,  True),
    ("name",         "col_name",        260,  True),
    ("cache_type",   "col_type",          40,  True),   # ikon + tooltip, ingen tekst
    ("container",    "col_container",    80,  True),   # størrelses-bar
    ("difficulty",   "col_difficulty",   36,  True),
    ("terrain",      "col_terrain",      36,  True),
    ("distance",     "col_distance",     75,  True),
    ("bearing",      "col_bearing",      70,  True),
    ("found",        "col_found",        36,  True),
    ("corrected",    "detail_corrected_coords", 36,  True),
    # Ekstra kolonner (fra)
    ("country",      "col_country",      80, False),
    ("state",        "col_state",       120, False),
    ("county",       "col_county",      100, False),
    ("placed_by",    "col_placed_by",   120, False),
    ("hidden_date",  "col_hidden_date",  90, False),
    ("last_log",     "col_last_log",     90, False),
    ("log_count",    "col_log_count",    70, False),
    ("dnf",          "col_dnf",          36, False),
    ("premium_only", "col_premium",      36, False),
    ("archived",     "col_archived",     36, False),
    # ── Issue #84: Latitude og Longitude ──────────────────────────────────────
    ("latitude",     "col_latitude",     95, False),
    ("longitude",    "col_longitude",    95, False),
    # ── Issue #33: GSAK-compatible fields ─────────────────────────────────────
    ("found_date",     "col_found_date",    90, False),
    ("dnf_date",       "col_dnf_date",      90, False),
    ("first_to_find",  "col_first_to_find", 36, False),
    ("favorite_points","col_favorite_points",55, False),
    ("trackables",     "col_trackables",     55, False),
    ("user_sort",      "col_user_sort",     55, False),
    ("user_data_1",    "col_user_data_1",  100, False),
    ("user_data_2",    "col_user_data_2",  100, False),
    ("user_data_3",    "col_user_data_3",  100, False),
    ("user_data_4",    "col_user_data_4",  100, False),
    # ── Issue #658: additional GSAK-compatible columns ─────────────────────────
    ("gc_cache_id",    "col_gc_cache_id",   90, False),
    ("changed_date",   "col_changed_date",  90, False),
    ("creation_date",  "col_creation_date", 90, False),
    ("elevation",      "col_elevation",     70, False),
    ("find_count",     "col_find_count",    80, False),
    ("gc_note",        "col_gc_note",      160, False),
    ("guid",           "col_guid",         160, False),
    ("hints",          "col_hints",        160, False),
    ("notes",          "col_notes",        160, False),
    ("owner_id",       "col_owner_id",      90, False),
    ("owner_name",     "col_owner_name",   120, False),
    ("source",         "col_source",       120, False),
    ("url",            "col_url",          160, False),
    ("watch",          "col_watch",         55, False),
    # ── Issue #716: follow-up derived columns ──────────────────────────────
    ("last_found_date", "col_last_found_date", 90, False),
    ("last_gpx_update", "col_last_gpx_update", 90, False),
    ("last_four_logs",  "col_last_four_logs", 70, False),
]

def get_all_columns():
    """Returner kolonner med oversatte navne."""
    from opensak.lang import tr
    return [(fid, tr(key), w, default) for fid, key, w, default in _ALL_COLUMNS_DEF]

# Bagudkompatibel alias — bruges af column_dialog internt
ALL_COLUMNS = property(lambda self: get_all_columns()) if False else None  # se get_all_columns()

# Kolonner der altid skal være synlige
ALWAYS_VISIBLE = {"gc_code", "name"}


def _safe_db_key(name: str) -> str:
    """Samme sanitisering som _col_key() bruger til at bygge nøgle-suffixet."""
    return name.replace(".", "_").replace(" ", "_")


def migrate_column_settings_for_rename(old_name: str, new_name: str) -> None:
    """
    Flyt gemte kolonneindstillinger (#199) fra den gamle til den nye
    database-navne-nøgle, når en database omdøbes (#539).

    Uden dette ville et rename få kolonneopsætningen til at "nulstille"
    (fordi den nu peger på en tom nøgle for det nye navn), mens de gamle
    indstillinger blev hængende forældreløse under det gamle navns nøgle.
    """
    if old_name == new_name:
        return
    old_safe = _safe_db_key(old_name)
    new_safe = _safe_db_key(new_name)
    if old_safe == new_safe:
        return  # sanitiseringen gør nøglerne ens selvom navnene ikke er

    store = get_store()
    for suffix in ("visible", "widths"):
        old_key = f"columns.{old_safe}.{suffix}"
        new_key = f"columns.{new_safe}.{suffix}"
        value = store.get(old_key)
        if value is not None:
            store.set(new_key, value)
            store.delete(old_key)


def _col_key(suffix: str) -> str:
    """
    Returner en settings-nøgle der er unik per aktiv database.

    Format: "columns.<db_name>.<suffix>"
    Falder tilbage til "columns.default.<suffix>" hvis ingen aktiv database.
    Issue #199: column views gemmes per database-navn.
    """
    try:
        from opensak.db.manager import get_db_manager
        manager = get_db_manager()
        if manager.active:
            # Brug database-navn (ikke sti) — mere læsbart og portabelt
            safe = manager.active.name.replace(".", "_").replace(" ", "_")
            return f"columns.{safe}.{suffix}"
    except Exception:
        pass
    return f"columns.default.{suffix}"


# ── Issue #607: Navngivne, gemte "Column Views" ──────────────────────────────
#
# Erstatter #606's "last used"-fallback (columns.__last_used__.*) med en
# eksplicit, brugerstyret mekanisme: brugeren gemmer en kolonneopsætning under
# et navn (parallelt med FilterProfile i filters/engine.py) og kan derefter
# udpege ét gemt view som den globale standard. En database uden sin egen
# eksplicitte per-DB-nøgle falder tilbage til standard-viewet i stedet for
# blot at arve, hvad der senest tilfældigvis blev gemt et andet sted.
#
# OK-knappen i ColumnChooserDialog låser fortsat altid den aktive database
# fast til den valgte opsætning (uændret adfærd fra før #607) — at vælge et
# view i dropdown'en og trykke OK gemmer det som et almindeligt per-DB-valg,
# det opretter ikke i sig selv et dynamisk "følg standard"-link.

_DEFAULT_VIEW_NAME_KEY = "columns.default_view_name"


class ColumnView:
    """
    Et navngivet, gemt sæt af kolonneindstillinger, gemt som JSON.

    Views gemmes i ~/.local/share/opensak/column_views/ (eller platformens
    tilsvarende app-data-mappe), én fil pr. view.
    """

    def __init__(
        self,
        name: str,
        visible_columns: list[str],
        widths: Optional[dict[str, int]] = None,
        container_display: str = "bar",
        type_display: str = "icon",
    ):
        self.name = name
        self.visible_columns = visible_columns
        self.widths = widths or {}
        self.container_display = container_display
        self.type_display = type_display

    @staticmethod
    def _views_dir() -> Path:
        from opensak.config import get_app_data_dir
        d = get_app_data_dir() / "column_views"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _safe_filename(name: str) -> str:
        return "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)

    def save(self, views_dir: Optional[Path] = None) -> Path:
        """Gem dette view til disk som JSON. Returnerer den gemte filsti."""
        if views_dir is None:
            views_dir = self._views_dir()
        views_dir.mkdir(parents=True, exist_ok=True)
        path = views_dir / f"{self._safe_filename(self.name)}.json"

        data = {
            "name": self.name,
            "visible_columns": self.visible_columns,
            "widths": self.widths,
            "container_display": self.container_display,
            "type_display": self.type_display,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "ColumnView":
        """Indlæs et view fra en JSON-fil."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=data["name"],
            visible_columns=list(data.get("visible_columns", [])),
            widths=dict(data.get("widths", {})),
            container_display=data.get("container_display", "bar"),
            type_display=data.get("type_display", "icon"),
        )

    @classmethod
    def list_views(cls, views_dir: Optional[Path] = None) -> list[Path]:
        """Returner en liste over alle gemte views' filstier."""
        if views_dir is None:
            views_dir = cls._views_dir()
        if not views_dir.exists():
            return []
        return sorted(views_dir.glob("*.json"))

    def __repr__(self) -> str:
        return f"<ColumnView {self.name!r}>"


def get_default_view_name() -> Optional[str]:
    """Returner navnet på det view der er udpeget som global standard, hvis nogen."""
    val = get_store().get(_DEFAULT_VIEW_NAME_KEY)
    return val if isinstance(val, str) and val else None


def set_default_view_name(name: Optional[str]) -> None:
    """Udpeg et gemt view (ved navn) som global standard. None fjerner standarden."""
    if name:
        get_store().set(_DEFAULT_VIEW_NAME_KEY, name)
    else:
        get_store().delete(_DEFAULT_VIEW_NAME_KEY)


def get_default_view() -> Optional[ColumnView]:
    """Indlæs og returner standard-viewet, hvis et er udpeget og stadig findes."""
    name = get_default_view_name()
    if not name:
        return None
    for path in ColumnView.list_views():
        try:
            view = ColumnView.load(path)
        except Exception:
            continue
        if view.name == name:
            return view
    # Standard-viewet er blevet slettet et andet sted fra — rens referencen op.
    set_default_view_name(None)
    return None


def get_visible_columns() -> list[str]:
    """Returner liste over synlige kolonne-id'er for den aktive database."""
    saved = get_store().get(_col_key("visible"))
    if saved:
        return list(saved)
    # Issue #607: ingen egen opsætning for denne database — brug det
    # udpegede standard-view, hvis der er et.
    default_view = get_default_view()
    if default_view and default_view.visible_columns:
        return list(default_view.visible_columns)
    # Ellers: vis de kolonner der er markeret som hardkodet standard.
    return [col[0] for col in get_all_columns() if col[3]]


def set_visible_columns(col_ids: list[str]) -> None:
    """Gem liste over synlige kolonne-id'er for den aktive database."""
    get_store().set(_col_key("visible"), col_ids)


def get_column_widths() -> dict[str, int]:
    """Return saved column widths (col_id -> px) for the active database."""
    raw = get_store().get(_col_key("widths"))
    if not raw:
        # Issue #607: samme fallback til standard-viewet som ovenfor.
        default_view = get_default_view()
        if default_view and default_view.widths:
            raw = default_view.widths
    if raw:
        try:
            if isinstance(raw, str):
                return json.loads(raw)
            if isinstance(raw, dict):
                return {k: int(v) for k, v in raw.items()}
        except Exception:
            pass
    return {}


def set_column_widths(widths: dict[str, int]) -> None:
    """Persist column widths (col_id -> px) for the active database."""
    get_store().set(_col_key("widths"), widths)


_CONTAINER_DISPLAY_KEY = "columns.container_display"


def get_container_display() -> str:
    """Return the container column display mode: 'bar' or 'text'."""
    val = get_store().get(_CONTAINER_DISPLAY_KEY)
    if val not in ("bar", "text"):
        # Issue #607: container/type-display har altid været globale (ikke
        # per-database), så her falder vi tilbage til standard-viewets værdi
        # frem for direkte til den hardkodede fabriksstandard.
        default_view = get_default_view()
        val = default_view.container_display if default_view else None
    return val if val in ("bar", "text") else "bar"


def set_container_display(mode: str) -> None:
    """Persist the container column display mode."""
    get_store().set(_CONTAINER_DISPLAY_KEY, mode)


_TYPE_DISPLAY_KEY = "columns.type_display"


def get_type_display() -> str:
    """Return the cache_type column display mode: 'icon', 'text', or 'both'."""
    val = get_store().get(_TYPE_DISPLAY_KEY)
    if val not in ("icon", "text", "both"):
        default_view = get_default_view()
        val = default_view.type_display if default_view else None
    return val if val in ("icon", "text", "both") else "icon"


def set_type_display(mode: str) -> None:
    """Persist the cache_type column display mode."""
    get_store().set(_TYPE_DISPLAY_KEY, mode)


class ColumnChooserDialog(QDialog):
    """Dialog til at vælge hvilke kolonner der vises i cachelisten."""

    _DEFAULT_MARK = "★ "

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("column_dialog_title"))
        self.setMinimumSize(360, 520)
        # Issue #811: the ~40-entry column list has its own scrollbar
        # (QListWidget/QAbstractItemView always does) but only actually
        # uses it once something constrains the *dialog's* height —
        # without a cap, Qt just grows the window to show all items
        # unscrolled, same failure mode as the Settings dialog.
        clamp_dialog_height_to_screen(self, parent)
        self._visible = set(get_visible_columns())
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("column_dialog_hint")))

        # ── Issue #607: Gem/indlæs/standard-vælg Column Views ────────────────
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel(tr("column_view_saved_label")))
        self._view_combo = QComboBox()
        self._view_combo.setMinimumWidth(160)
        self._view_combo.blockSignals(True)
        self._load_views_into_combo()
        self._view_combo.blockSignals(False)
        self._view_combo.currentIndexChanged.connect(self._on_view_selected)
        view_row.addWidget(self._view_combo)

        save_view_btn = QPushButton(tr("filter_save_btn"))
        # #927: size to the translated label instead of a hardcoded width,
        # so longer translations are never clipped.
        save_view_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        save_view_btn.setAutoDefault(False)
        save_view_btn.clicked.connect(self._save_view)
        view_row.addWidget(save_view_btn)

        self._del_view_btn = QPushButton("🗑")
        self._del_view_btn.setMaximumWidth(40)
        self._del_view_btn.setToolTip(tr("column_view_delete_tooltip"))
        self._del_view_btn.setAutoDefault(False)
        self._del_view_btn.clicked.connect(self._delete_view)
        view_row.addWidget(self._del_view_btn)

        layout.addLayout(view_row)

        default_row = QHBoxLayout()
        self._set_default_btn = QPushButton(tr("column_view_set_default_btn"))
        self._set_default_btn.setAutoDefault(False)
        self._set_default_btn.setToolTip(tr("column_view_set_default_tooltip"))
        self._set_default_btn.clicked.connect(self._set_default_view)
        default_row.addWidget(self._set_default_btn)
        default_row.addStretch()
        layout.addLayout(default_row)

        self._update_view_buttons()

        self._list = QListWidget()
        for col_id, col_name, _, _ in get_all_columns():
            item = QListWidgetItem(col_name)
            if col_id == "corrected":
                # Issue #354: same SVG warning-triangle used in the column
                # header/cells/context-menu/detail-panel, instead of the old
                # "📍" emoji that used to be baked into the label text.
                item.setIcon(get_corrected_coords_icon(16))
            item.setData(Qt.ItemDataRole.UserRole, col_id)
            item.setCheckState(
                Qt.CheckState.Checked
                if col_id in self._visible
                else Qt.CheckState.Unchecked
            )
            if col_id in ALWAYS_VISIBLE:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setForeground(Qt.GlobalColor.gray)
            self._list.addItem(item)
        layout.addWidget(self._list)

        # Vælg alle / Fravælg alle
        btn_row = QHBoxLayout()
        select_all = QPushButton(tr("column_select_all"))
        select_all.clicked.connect(self._select_all)
        btn_row.addWidget(select_all)

        select_default = QPushButton(tr("column_select_default"))
        select_default.clicked.connect(self._select_default)
        btn_row.addWidget(select_default)
        layout.addLayout(btn_row)

        display_row = QHBoxLayout()
        display_row.addWidget(QLabel(tr("container_display_label")))
        self._container_display_combo = QComboBox()
        for label, value in (
            (tr("container_display_bar"),  "bar"),
            (tr("container_display_text"), "text"),
        ):
            self._container_display_combo.addItem(label, value)
        current_mode = get_container_display()
        self._container_display_combo.setCurrentIndex(0 if current_mode == "bar" else 1)
        display_row.addWidget(self._container_display_combo)
        layout.addLayout(display_row)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel(tr("type_display_label")))
        self._type_display_combo = QComboBox()
        for label, value in (
            (tr("type_display_icon"), "icon"),
            (tr("container_display_text"), "text"),
            (tr("type_display_both"), "both"),
        ):
            self._type_display_combo.addItem(label, value)
        _type_idx = {"icon": 0, "text": 1, "both": 2}.get(get_type_display(), 0)
        self._type_display_combo.setCurrentIndex(_type_idx)
        type_row.addWidget(self._type_display_combo)
        layout.addLayout(type_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _select_all(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setCheckState(Qt.CheckState.Checked)

    def _select_default(self) -> None:
        defaults = {col[0] for col in get_all_columns() if col[3]}
        for i in range(self._list.count()):
            item = self._list.item(i)
            col_id = item.data(Qt.ItemDataRole.UserRole)
            if col_id not in ALWAYS_VISIBLE:
                item.setCheckState(
                    Qt.CheckState.Checked
                    if col_id in defaults
                    else Qt.CheckState.Unchecked
                )

    # ── Issue #607: Column Views (gem/indlæs/slet/sæt som standard) ──────────

    def _load_views_into_combo(self) -> None:
        self._view_combo.clear()
        self._view_combo.addItem(tr("column_view_none"), None)
        default_name = get_default_view_name()
        for path in ColumnView.list_views():
            try:
                view = ColumnView.load(path)
            except Exception:
                continue
            label = view.name
            if default_name and view.name == default_name:
                label = f"{self._DEFAULT_MARK}{label}"
            self._view_combo.addItem(label, path)

    def _update_view_buttons(self) -> None:
        path = self._view_combo.currentData()
        self._del_view_btn.setEnabled(path is not None)
        self._set_default_btn.setEnabled(path is not None)

    def _on_view_selected(self, index: int) -> None:
        self._update_view_buttons()
        path = self._view_combo.currentData()
        if path is None:
            return
        try:
            view = ColumnView.load(path)
        except Exception as e:
            QMessageBox.warning(self, tr("error"), tr("column_view_load_error", error=e))
            return
        self._apply_view_to_ui(view)

    def _apply_view_to_ui(self, view: ColumnView) -> None:
        """Indlæs et views kolonner/bredder/display-indstillinger i dialogens felter."""
        visible = set(view.visible_columns) | ALWAYS_VISIBLE
        for i in range(self._list.count()):
            item = self._list.item(i)
            col_id = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked
                if col_id in visible
                else Qt.CheckState.Unchecked
            )
        idx = 0 if view.container_display == "bar" else 1
        self._container_display_combo.setCurrentIndex(idx)
        type_idx = {"icon": 0, "text": 1, "both": 2}.get(view.type_display, 0)
        self._type_display_combo.setCurrentIndex(type_idx)

    def _current_checked_columns(self) -> list[str]:
        old_order = get_visible_columns()
        checked: set[str] = {
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        } | ALWAYS_VISIBLE
        old_set = set(old_order)
        visible = [c for c in old_order if c in checked]
        visible += [fid for fid, *_ in _ALL_COLUMNS_DEF if fid in checked and fid not in old_set]
        return visible

    def _save_view(self) -> None:
        name, ok = QInputDialog.getText(
            self, tr("column_view_save_title"), tr("column_view_name_label")
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        view = ColumnView(
            name=name,
            visible_columns=self._current_checked_columns(),
            widths=dict(get_column_widths()),
            container_display=self._container_display_combo.currentData(),
            type_display=self._type_display_combo.currentData(),
        )
        view.save()
        self._view_combo.blockSignals(True)
        self._load_views_into_combo()
        self._view_combo.blockSignals(False)
        for i in range(self._view_combo.count()):
            if self._view_combo.itemText(i).lstrip(self._DEFAULT_MARK) == name:
                self._view_combo.setCurrentIndex(i)
                break
        self._update_view_buttons()
        QMessageBox.information(
            self, tr("filter_saved_title"), tr("column_view_saved_msg", name=name)
        )

    def _delete_view(self) -> None:
        path = self._view_combo.currentData()
        if path is None:
            return
        view_name = self._view_combo.currentText().lstrip(self._DEFAULT_MARK)
        reply = QMessageBox.question(
            self, tr("column_view_delete_title"),
            tr("column_view_delete_msg", name=view_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(path)
        except Exception:
            pass
        if get_default_view_name() == view_name:
            set_default_view_name(None)
        self._view_combo.blockSignals(True)
        self._load_views_into_combo()
        self._view_combo.blockSignals(False)
        self._update_view_buttons()

    def _set_default_view(self) -> None:
        path = self._view_combo.currentData()
        if path is None:
            return
        view_name = self._view_combo.currentText().lstrip(self._DEFAULT_MARK)
        set_default_view_name(view_name)
        self._view_combo.blockSignals(True)
        self._load_views_into_combo()
        self._view_combo.blockSignals(False)
        for i in range(self._view_combo.count()):
            if self._view_combo.itemText(i).lstrip(self._DEFAULT_MARK) == view_name:
                self._view_combo.setCurrentIndex(i)
                break
        self._update_view_buttons()
        QMessageBox.information(
            self, tr("column_view_default_set_title"),
            tr("column_view_default_set_msg", name=view_name)
        )

    def _save_and_accept(self) -> None:
        visible = self._current_checked_columns()

        set_visible_columns(visible)
        set_container_display(self._container_display_combo.currentData())
        set_type_display(self._type_display_combo.currentData())
        self.accept()
