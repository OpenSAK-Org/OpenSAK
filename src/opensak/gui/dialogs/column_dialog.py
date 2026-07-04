"""
src/opensak/gui/dialogs/column_dialog.py — Vælg synlige kolonner i cachelisten.
"""

from __future__ import annotations
import json
from typing import cast
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
    QDialogButtonBox, QComboBox
)
from opensak.lang import tr
from opensak.settings_store import get_store

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
    ("corrected",    "col_corrected_label",    36,  True),
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
    ("user_sort",      "col_user_sort",     55, False),
    ("user_data_1",    "col_user_data_1",  100, False),
    ("user_data_2",    "col_user_data_2",  100, False),
    ("user_data_3",    "col_user_data_3",  100, False),
    ("user_data_4",    "col_user_data_4",  100, False),
]

def get_all_columns():
    """Returner kolonner med oversatte navne."""
    from opensak.lang import tr
    return [(fid, tr(key), w, default) for fid, key, w, default in _ALL_COLUMNS_DEF]

# Bagudkompatibel alias — bruges af column_dialog internt
ALL_COLUMNS = property(lambda self: get_all_columns()) if False else None  # se get_all_columns()

# Kolonner der altid skal være synlige
ALWAYS_VISIBLE = {"gc_code", "name"}


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


def get_visible_columns() -> list[str]:
    """Returner liste over synlige kolonne-id'er for den aktive database."""
    saved = get_store().get(_col_key("visible"))
    if saved:
        return list(saved)
    # Standard: vis de kolonner der er markeret som standard
    return [col[0] for col in get_all_columns() if col[3]]


def set_visible_columns(col_ids: list[str]) -> None:
    """Gem liste over synlige kolonne-id'er for den aktive database."""
    get_store().set(_col_key("visible"), col_ids)


def get_column_widths() -> dict[str, int]:
    """Return saved column widths (col_id -> px) for the active database."""
    raw = get_store().get(_col_key("widths"))
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
    val = get_store().get(_CONTAINER_DISPLAY_KEY, "bar")
    return val if val in ("bar", "text") else "bar"


def set_container_display(mode: str) -> None:
    """Persist the container column display mode."""
    get_store().set(_CONTAINER_DISPLAY_KEY, mode)


_TYPE_DISPLAY_KEY = "columns.type_display"


def get_type_display() -> str:
    """Return the cache_type column display mode: 'icon', 'text', or 'both'."""
    val = get_store().get(_TYPE_DISPLAY_KEY, "icon")
    return val if val in ("icon", "text", "both") else "icon"


def set_type_display(mode: str) -> None:
    """Persist the cache_type column display mode."""
    get_store().set(_TYPE_DISPLAY_KEY, mode)


class ColumnChooserDialog(QDialog):
    """Dialog til at vælge hvilke kolonner der vises i cachelisten."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("column_dialog_title"))
        self.setMinimumSize(360, 460)
        self._visible = set(get_visible_columns())
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("column_dialog_hint")))

        self._list = QListWidget()
        for col_id, col_name, _, _ in get_all_columns():
            item = QListWidgetItem(col_name)
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

    def _save_and_accept(self) -> None:
        checked: set[str] = {
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        } | ALWAYS_VISIBLE

        old_order = get_visible_columns()
        old_set = set(old_order)

        # Keep existing visible columns in the user's drag order, then append
        # newly added columns in _ALL_COLUMNS_DEF order (predictable insertion).
        visible = [c for c in old_order if c in checked]
        visible += [fid for fid, *_ in _ALL_COLUMNS_DEF if fid in checked and fid not in old_set]

        set_visible_columns(visible)
        set_container_display(self._container_display_combo.currentData())
        set_type_display(self._type_display_combo.currentData())
        self.accept()
