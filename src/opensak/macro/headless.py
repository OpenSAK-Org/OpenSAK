"""
src/opensak/macro/headless.py — the MacroHost of `opensak run` (#1203).

No window and nobody to ask: the filter is kept in memory and evaluated
against the database, output goes to the callables given by the command, and
a question never blocks. It is answered from the command line:

* confirm, input, choose: --arg with the prompt as key, then the default
  with --yes (confirm: yes; choose without a default has no answer);
* form: --arg with the field id as key, then the values remembered with
  remember = true, then the fields' defaults with --yes. The form closes as
  with its default button;
* choose_file, choose_folder: only --arg with the title as key. A picked
  file or folder may be used whatever the folder list says, so a macro must
  not be able to answer this itself through values it saved.

A question without an answer ends the run with EXIT_NEEDS_UI, through the
runtime's stop_run(), so pcall cannot catch it. So does a write or a folder
that is not approved: nothing asks, and the command line names the way out
(--allow-write, --allow-folder).

approve_host, confirm_declarations and set_center are left out on purpose:
the runtime handles a host without them (MacroHost).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, NoReturn, Optional

from opensak.db.database import get_session
from opensak.export.file_export import active_database_name
from opensak.filters.engine import FilterSet, SortSpec, apply_filters_auto
from opensak.macro import forms as macro_forms
from opensak.macro.errors import MacroError
from opensak.macro.runtime import FolderApproval, WriteApproval
from opensak.utils.run_macro import EXIT_NEEDS_UI, EXIT_USAGE

_TRUE = ("yes", "true", "1")
_FALSE = ("no", "false", "0")


def _parse_bool(text: str) -> Optional[bool]:
    word = text.strip().lower()
    return True if word in _TRUE else False if word in _FALSE else None


class _Progress:
    """A progress window: one line per new text. Nobody can press Cancel."""

    def __init__(self, status: Callable[[str], None], title: str):
        self._status = status
        self._last = title
        status(title)

    def update(self, value: Optional[int], text: str) -> bool:
        if text and text != self._last:
            self._last = text
            self._status(text)
        return True

    def close(self) -> None:
        pass


class _Form:
    """The FormController of a form nobody sees."""

    def __init__(self, form: macro_forms.Form):
        self.values = {f: macro_forms.initial_value(f) for f in form.value_fields()}
        self.closed = False
        self.closer: Optional[str] = None

    def value(self, field: macro_forms.Field) -> Any:
        return self.values[field]

    def set_value(self, field: macro_forms.Field, value: Any) -> None:
        self.values[field] = value

    def set_options(self, field: macro_forms.Field) -> None:
        try:
            self.values[field] = macro_forms.coerce_value(field, self.values[field], "opensak.form")
        except MacroError:
            self.values[field] = macro_forms.initial_value(field)

    def set_enabled(self, field: macro_forms.Field, on: bool) -> None:
        pass

    def set_visible(self, field: macro_forms.Field, on: bool) -> None:
        pass

    def set_label(self, field: macro_forms.Field) -> None:
        pass

    def set_title(self, title: str) -> None:
        pass

    def focus(self, field: macro_forms.Field) -> None:
        pass

    def close_form(self, closer: Optional[str]) -> None:
        if not self.closed:
            self.closed = True
            self.closer = closer

    def by_id(self) -> dict[str, Any]:
        return {f.id: v for f, v in self.values.items() if f.id}


class HeadlessHost:
    """A MacroHost without a user interface.

    *answers* are the --arg values, *yes* is --yes, *allow_write* is
    --allow-write. *output* gets message() texts, *status* the progress
    lines. *switch* makes another database active and *lock* takes the lock
    of one the macro uses besides the active one (both may call the stop
    function); without *switch* opensak.switch_database() fails. Call
    set_stop() with the runtime's stop_run before the run.
    """

    def __init__(
        self,
        answers: Optional[dict[str, str]] = None,
        yes: bool = False,
        allow_write: bool = False,
        output: Callable[[str], None] = print,
        status: Callable[[str], None] = print,
        switch: Optional[Callable[[str], None]] = None,
        lock: Optional[Callable[[Any], None]] = None,
    ):
        self._answers = answers or {}
        self._yes = yes
        # Read by MacroRuntime._require_write()
        self.allow_write = allow_write
        self._output = output
        self._status = status
        self._switch = switch
        self._lock = lock
        self._stop_run: Optional[Callable[[int, str], None]] = None
        self._filterset = FilterSet()
        self._filter_label = ""
        self._sort: Optional[SortSpec] = None

    def set_stop(self, stop_run: Callable[[int, str], None]) -> None:
        self._stop_run = stop_run

    def _stop(self, code: int, message: str) -> NoReturn:
        if self._stop_run is not None:
            self._stop_run(code, message)
        raise MacroError(message)

    def _needs_ui(self, what: str, how: str) -> NoReturn:
        self._stop(EXIT_NEEDS_UI, f"{what}, and this run has no window — {how}")

    # -- filter and list -------------------------------------------------------

    def _caches(self, filterset: FilterSet, sort: Optional[SortSpec] = None) -> list:
        with get_session() as session:
            return apply_filters_auto(session, filterset, sort)

    def apply_filter(self, filterset: FilterSet, label: str) -> int:
        found = len(self._caches(filterset))
        if found:
            self._filterset, self._filter_label = filterset, label
        return found

    def clear_filter(self) -> None:
        self._filterset, self._filter_label = FilterSet(), ""

    def cache_count(self) -> int:
        return len(self._caches(self._filterset))

    def filtered_caches(self) -> list:
        return self._caches(self._filterset, self._sort)

    def filter_name(self) -> str:
        return self._filter_label

    def active_filterset(self) -> FilterSet:
        return self._filterset

    def set_sort(self, sort: SortSpec) -> None:
        self._sort = sort

    def current_code(self) -> Optional[str]:
        return None

    def selected_codes(self) -> list[str]:
        return []

    def select_cache(self, gc_code: str) -> bool:
        return False

    # -- databases -------------------------------------------------------------

    def database_name(self) -> str:
        return active_database_name()

    def center_name(self) -> str:
        from opensak.gui.settings import get_settings
        return get_settings().active_home_name or ""

    def switch_database(self, name: str) -> None:
        if self._switch is None:
            raise RuntimeError("not available in this run")
        self._switch(name)
        self.clear_filter()

    def lock_database(self, db: Any) -> None:
        if self._lock is not None:
            self._lock(db)

    def set_corrected_coords(
        self, gc_code: str, lat: Optional[float], lon: Optional[float]
    ) -> bool:
        from opensak.db.corrected_coords import set_corrected_coords
        return set_corrected_coords(gc_code, lat, lon, source="macro")

    # Nothing shows the data, so there is nothing to refresh. The GUI can't be
    # open on this database either: it would hold the lock.

    def database_list_changed(self) -> None:
        pass

    def caches_removed(self) -> None:
        pass

    def caches_changed(self, codes: list[str], added: bool) -> None:
        pass

    def refresh(self) -> None:
        pass

    def end_macro(self) -> None:
        pass

    def open_file(self, path: Path) -> bool:
        return True

    # -- approvals -------------------------------------------------------------

    def approve_macro_write(
        self, macro: Optional[Path], change: Any = None, editing: bool = False,
    ) -> WriteApproval:
        name = macro.name if macro is not None else "the macro"
        self._needs_ui(
            f"{name} wants to change a database without an \"Always\" approval "
            "for its current code",
            "approve it in OpenSAK, or use --allow-write")

    def approve_macro_change(
        self, macro: Path, change: Any, editing: bool = False,
    ) -> WriteApproval:
        self._needs_ui(
            f"{macro.name} has changed since it was approved",
            "approve it again in OpenSAK, or use --allow-write or --allow-folder")

    def approve_folder(
        self, target: Path, folder: Path, write: bool, macro: Optional[Path] = None,
    ) -> FolderApproval:
        kind = "write" if write else "read"
        self._needs_ui(
            f"the macro wants to {kind} {target} in a folder that isn't approved",
            f"approve it in OpenSAK, or use --allow-folder \"{folder}\"")

    # -- questions -------------------------------------------------------------

    def confirm(self, message: str) -> bool:
        answer = self._answers.get(message)
        if answer is not None:
            parsed = _parse_bool(answer)
            if parsed is None:
                self._stop(EXIT_USAGE, f"--arg for {message!r} expects yes or no, got {answer!r}")
            return parsed
        if self._yes:
            return True
        self._needs_ui(f"the macro asks: {message}",
                       f"use --yes, or --arg \"{message}=yes\"")

    def input(self, prompt: str, default: str, title: str) -> Optional[str]:
        answer = self._answers.get(prompt)
        if answer is not None:
            return answer
        if self._yes:
            return default
        self._needs_ui(f"the macro asks: {prompt}",
                       f"use --yes for the default, or --arg \"{prompt}=VALUE\"")

    def choose(self, prompt: str, options: list[str], default: int, title: str) -> Optional[int]:
        answer = self._answers.get(prompt)
        if answer is not None:
            if answer not in options:
                self._stop(EXIT_USAGE, f"--arg for {prompt!r} must be one of "
                                       f"{', '.join(options)}, got {answer!r}")
            return options.index(answer)
        if self._yes and default >= 0:
            return default
        self._needs_ui(f"the macro asks: {prompt}", f"use --arg \"{prompt}=VALUE\"")

    def choose_file(
        self, title: str, file_filter: str, save: bool, start_dir: Path
    ) -> Optional[Path]:
        return self._picked_path(title, "a file")

    def choose_folder(self, title: str, start_dir: Path) -> Optional[Path]:
        return self._picked_path(title, "a folder")

    def _picked_path(self, title: str, what: str) -> Path:
        answer = self._answers.get(title) if title else None
        if answer:
            return Path(answer).expanduser()
        self._needs_ui(f"the macro asks for {what} ({title or 'without a title'})",
                       f"use --arg \"{title}=PATH\"" if title else "it can't be answered")

    def message(self, text: str, title: str) -> None:
        self._output(f"{title}: {text}" if title else text)

    def progress(self, title: str, total: int) -> _Progress:
        return _Progress(self._status, title)

    # -- forms -----------------------------------------------------------------

    def show_form(
        self, form: macro_forms.Form, events: macro_forms.FormEvents
    ) -> Optional[tuple[dict[str, Any], str]]:
        name = form.title or form.id or "a form"
        fields = [f for f in form.value_fields() if f.id]
        given = [f for f in fields if f.id in self._answers]
        known = {f.id for f in given} | form.remembered
        missing = [f.id for f in fields if f.id not in known]
        # The new values first: a bad one shouldn't run the macro's callbacks.
        values = {f: self._form_value(f, self._answers[str(f.id)]) for f in given}
        if (missing or not fields) and not self._yes:
            how = ("use --yes for the defaults, or --arg for each of: " + ", ".join(map(str, missing))
                   if missing else "use --yes")
            self._needs_ui(f"the macro shows {name}", how)

        state = _Form(form)
        events(state, "open", None)
        for field, value in values.items():
            if state.closed:
                break
            if value != state.values[field]:
                state.values[field] = value
                events(state, "change", field)
                if field.get("exit_on_change"):
                    state.close_form(field.id)
        if not state.closed:
            self._press_default(form, state, events, name)
        return None if state.closer is None else (state.by_id(), state.closer)

    def _press_default(
        self, form: macro_forms.Form, state: _Form, events: macro_forms.FormEvents, name: str,
    ) -> None:
        """As the default button of the dialog does."""
        buttons = [b for b in form.buttons if not b.get("cancel")]
        button = next((b for b in buttons if b.get("default")), buttons[0] if buttons else None)
        if button is None:
            self._needs_ui(f"{name} has no button to close it with", "it can't be answered")
        if button.get("on_click") is not None:
            events(state, "click", button)
            if not state.closed:
                self._needs_ui(f"the button of {name} leaves it open", "it can't be answered")
            return
        missing = macro_forms.check_required(form, state.by_id())
        if missing is not None:
            self._needs_ui(f"{name} requires {missing.name}", f"use --arg \"{missing.name}=VALUE\"")
        problem = events(state, "validate", button)
        if not state.closed and problem:
            self._stop(EXIT_USAGE, f"{name}: {problem}")
        state.close_form(button.id)

    def _form_value(self, field: macro_forms.Field, text: str) -> Any:
        value: Any = text
        if field.type == "checkbox":
            value = _parse_bool(text)
            if value is None:
                self._stop(EXIT_USAGE, f"--arg {field.id} expects yes or no, got {text!r}")
        elif field.type == "checklist":
            value = [item.strip() for item in text.split(",") if item.strip()]
        try:
            return macro_forms.coerce_value(field, value, f"--arg {field.id}")
        except MacroError as exc:
            self._stop(EXIT_USAGE, str(exc))
