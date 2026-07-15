"""
src/opensak/gui/widgets/center_point_picker.py — Reusable center-point selector.

Introduced for issue #511 ("Center Points" — make any cache, saved home point,
or manual coordinate usable as a distance-filter center, not just Home).

Deliberately built as a standalone QWidget (not baked into filter_dialog.py)
so it can also be dropped straight into the planned issue #558 ("Quick 'Where'
filter box in toolbar") without duplicating the combo/validation logic.

Usage
-----
    picker = CenterPointPicker(self)
    picker.set_current_cache(some_cache_or_none)   # enables the "selected cache" option
    picker.changed.connect(self._something)
    ...
    center = picker.get_center()          # -> (lat, lon) | None
    label  = picker.current_label()       # -> human-readable text for display
    state  = picker.to_state()            # -> serializable dict, for saving
    picker.set_state(state)               # -> restore a previously saved state

get_center() re-resolves "Home" and saved home points against current Settings
every time it's called — it never caches a snapshot itself. Callers that need
a frozen value (e.g. a filter that will be saved to disk) must read
get_center() once at build time and store the plain (lat, lon), exactly like
the rest of the filter engine already does.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QLabel

from opensak.lang import tr

# Combo item data is a tuple whose first element identifies the kind:
#   ("home",)
#   ("point", lat, lon, name)
#   ("cache",)
#   ("custom",)
_KIND_HOME = "home"
_KIND_POINT = "point"
_KIND_CACHE = "cache"
_KIND_CUSTOM = "custom"


class CenterPointPicker(QWidget):
    """Lets the user pick a center point: Home, a saved home point, the
    currently selected cache, or a manually entered coordinate."""

    changed = Signal()  # fires whenever the resolved center point may have changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_cache = None  # opensak.db.models.Cache | None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._combo = QComboBox()
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        layout.addWidget(self._combo)

        self._custom_row = QWidget()
        custom_layout = QHBoxLayout(self._custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self._custom_edit = QLineEdit()
        self._custom_edit.setPlaceholderText(tr("coord_conv_placeholder"))
        self._custom_edit.textChanged.connect(self._on_custom_text_changed)
        custom_layout.addWidget(self._custom_edit)
        layout.addWidget(self._custom_row)
        self._custom_row.setVisible(False)

        self._hint = QLabel("")
        self._hint.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self._hint)

        self._rebuild_combo()

    # ── Public API ────────────────────────────────────────────────────────

    def set_current_cache(self, cache) -> None:
        """Gør en cache valgbar som centrum (None = ingen valgt cache lige nu).

        Kaldes typisk med den aktuelt markerede cache i tabellen, så brugeren
        kan vælge "Denne cache" som centrum (GSAK's CenterPoint > Current
        Cache). Kald igen når markeringen ændrer sig, hvis pickeren skal følge
        med live (fx i en toolbar-brug som #558).
        """
        prev_kind = self._current_kind()
        self._current_cache = cache
        self._rebuild_combo(keep_kind=prev_kind)

    def refresh(self) -> None:
        """Genindlæs gemte hjemmepunkter fra Settings.

        Kald hvis Settings kan være ændret mens denne widget allerede er åben
        (fx hvis brugeren har rettet i hjemmepunkter i en anden dialog).
        """
        self._rebuild_combo(keep_kind=self._current_kind())

    def get_center(self) -> Optional[tuple[float, float]]:
        """Returnér (lat, lon) for det aktuelt valgte centrum, eller None hvis
        valget er ugyldigt (fx ingen cache valgt, eller ugyldig koordinat)."""
        data = self._combo.currentData()
        if data is None:
            return None
        kind = data[0]
        if kind == _KIND_HOME:
            from opensak.gui.settings import get_settings
            s = get_settings()
            if not s.home_lat and not s.home_lon:
                return None
            return (s.home_lat, s.home_lon)
        if kind == _KIND_POINT:
            return (data[1], data[2])
        if kind == _KIND_CACHE:
            if self._current_cache is None:
                return None
            lat = getattr(self._current_cache, "latitude", None)
            lon = getattr(self._current_cache, "longitude", None)
            if lat is None or lon is None:
                return None
            return (lat, lon)
        if kind == _KIND_CUSTOM:
            from opensak.coords import parse_coords
            return parse_coords(self._custom_edit.text().strip())
        return None

    def current_label(self) -> str:
        """Menneskelæsbart navn for det valgte centrum (til visning/gemning)."""
        return self._combo.currentText()

    def to_state(self) -> dict:
        """Serialiserbar tilstand — bruges til at gemme valget (fx sammen med
        et filter der gemmes som profil)."""
        data = self._combo.currentData()
        if not data:
            return {"kind": _KIND_HOME}
        if data[0] == _KIND_POINT:
            return {"kind": _KIND_POINT, "name": data[3]}
        if data[0] == _KIND_CUSTOM:
            return {"kind": _KIND_CUSTOM, "text": self._custom_edit.text()}
        return {"kind": data[0]}

    def set_state(self, state: dict) -> None:
        """Modstykke til to_state() — genskab et tidligere valg.

        Falder tilbage til Home hvis et gemt hjemmepunkt-navn ikke længere
        findes, eller hvis "cache"-valget ikke er tilgængeligt lige nu.
        """
        kind = (state or {}).get("kind", _KIND_HOME)
        if kind == _KIND_POINT:
            target_name = state.get("name")
            for i in range(self._combo.count()):
                d = self._combo.itemData(i)
                if d and d[0] == _KIND_POINT and d[3] == target_name:
                    self._combo.setCurrentIndex(i)
                    return
        elif kind == _KIND_CUSTOM:
            for i in range(self._combo.count()):
                d = self._combo.itemData(i)
                if d and d[0] == _KIND_CUSTOM:
                    self._combo.setCurrentIndex(i)
                    self._custom_edit.setText(state.get("text", ""))
                    return
        elif kind == _KIND_CACHE:
            for i in range(self._combo.count()):
                d = self._combo.itemData(i)
                if d and d[0] == _KIND_CACHE:
                    self._combo.setCurrentIndex(i)
                    return
        # Ukendt/utilgængeligt valg → Home (indeks 0, findes altid)
        self._combo.setCurrentIndex(0)

    # ── Intern ────────────────────────────────────────────────────────────

    def _current_kind(self) -> Optional[str]:
        data = self._combo.currentData()
        return data[0] if data else None

    def _rebuild_combo(self, keep_kind: Optional[str] = None) -> None:
        from opensak.gui.settings import get_settings
        s = get_settings()

        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItem(tr("center_point_home"), (_KIND_HOME,))
        for p in s.home_points:
            self._combo.addItem(f"★ {p.name}", (_KIND_POINT, p.lat, p.lon, p.name))
        if self._current_cache is not None:
            gc = getattr(self._current_cache, "gc_code", "") or ""
            name = getattr(self._current_cache, "name", "") or ""
            label = f"{gc} — {name}".strip(" —")
            self._combo.addItem(tr("center_point_selected_cache", cache=label), (_KIND_CACHE,))
        self._combo.addItem(tr("center_point_custom"), (_KIND_CUSTOM,))
        self._combo.blockSignals(False)

        if keep_kind:
            for i in range(self._combo.count()):
                d = self._combo.itemData(i)
                if d and d[0] == keep_kind:
                    self._combo.setCurrentIndex(i)
                    break
        self._on_combo_changed(self._combo.currentIndex())

    def _on_combo_changed(self, index: int) -> None:
        data = self._combo.itemData(index)
        is_custom = bool(data and data[0] == _KIND_CUSTOM)
        self._custom_row.setVisible(is_custom)
        self._update_hint()
        self.changed.emit()

    def _on_custom_text_changed(self, _text: str) -> None:
        self._update_hint()
        self.changed.emit()

    def _update_hint(self) -> None:
        data = self._combo.currentData()
        kind = data[0] if data else None

        if kind == _KIND_CUSTOM:
            text = self._custom_edit.text().strip()
            if not text:
                self._hint.setText("")
                return
            from opensak.coords import parse_coords, format_coords
            from opensak.gui.settings import get_settings
            coord = parse_coords(text)
            if coord is None:
                self._set_hint_error(tr("settings_hp_coord_error"))
            else:
                fmt = get_settings().coord_format
                self._hint.setText(f"✓  {format_coords(coord[0], coord[1], fmt)}")
                self._hint.setStyleSheet("color: #2e7d32; font-size: 10px;")
        elif kind == _KIND_CACHE and self._current_cache is None:
            self._set_hint_error(tr("center_point_no_cache_selected"))
        elif kind == _KIND_HOME:
            from opensak.gui.settings import get_settings
            s = get_settings()
            if not s.home_lat and not s.home_lon:
                self._set_hint_error(tr("center_point_no_home_set"))
            else:
                self._hint.setText("")
        else:
            self._hint.setText("")

    def _set_hint_error(self, text: str) -> None:
        self._hint.setText(text)
        self._hint.setStyleSheet("color: #c62828; font-size: 10px;")
