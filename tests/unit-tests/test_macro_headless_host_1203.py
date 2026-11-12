"""
tests/unit-tests/test_macro_headless_host_1203.py — HeadlessHost, the MacroHost
of `opensak run` (#1203), through the real runtime. No QApplication and no
display: a question is answered from the command line or ends the run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.macro import MacroError, MacroHost, MacroRuntime, WriteApproval, db_access
from opensak.macro.db_access import source_hash
from opensak.macro.headless import HeadlessHost
from opensak.macro.metadata import parse_header
from opensak.utils.run_macro import EXIT_NEEDS_UI, EXIT_USAGE

WRITE = 'opensak.update("GCH1", { user_sort = 5 })\n'


@pytest.fixture(scope="module", autouse=True)
def database(tmp_db):
    with get_session() as s:
        s.add(Cache(gc_code="GCH1", name="Headless one", cache_type="Traditional Cache",
                    latitude=47.0, longitude=8.0))
        s.add(Cache(gc_code="GCH2", name="Headless two", cache_type="Multi-cache",
                    latitude=47.1, longitude=8.1))
    yield


class Run:
    """One macro run: its exit code, print() and message() output."""

    def __init__(self, source: str, *, macro_path=None, **host_options):
        self.lines: list[str] = []
        self.status: list[str] = []
        host = HeadlessHost(output=self.lines.append, status=self.status.append, **host_options)
        # cast: the optional MacroHost functions are left out on purpose
        runtime = MacroRuntime(cast(MacroHost, host), output=self.lines.append)
        host.set_stop(runtime.stop_run)
        self.code = runtime.run(source, macro_path=macro_path)


def _user_sort() -> int | None:
    with get_session() as s:
        return s.query(Cache).filter_by(gc_code="GCH1").one().user_sort


# ── Filter and list ──────────────────────────────────────────────────────────

def test_filter_is_kept_in_memory_and_counts_from_the_database():
    run = Run('print(opensak.count())\n'
              'print(opensak.filter{ type = "Multi-cache" })\n'
              'print(opensak.count())\n'
              'opensak.clear_filter()\n'
              'print(opensak.count())\n')
    assert run.code == 0
    assert run.lines == ["2", "1", "1", "2"]


def test_a_filter_without_matches_leaves_the_active_one():
    run = Run('opensak.filter{ type = "Multi-cache" }\n'
              'print(opensak.filter{ name = "no such cache" })\n'
              'print(opensak.count())\n')
    assert run.lines == ["0", "1"]


def test_no_current_cache_or_selection():
    run = Run('print(opensak.current())\nprint(#opensak.selected())\n')
    assert run.lines == ["nil", "0"]


# ── Questions ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", [
    'opensak.confirm("Sure?")',
    'opensak.input("Name?", "x")',
    'opensak.choose("Pick", { "a", "b" }, "a")',
    'opensak.choose_file("Pick file")',
    'opensak.choose_folder("Pick folder")',
    'opensak.form{ title = "F", fields = { { type = "text", id = "a", default = "x" } } }',
])
def test_an_unanswered_question_stops_with_needs_ui(source):
    run = Run(source + '\nprint("went on")\n')
    assert run.code == EXIT_NEEDS_UI
    assert "went on" not in run.lines


def test_pcall_cannot_catch_the_stop():
    run = Run('local ok = pcall(opensak.confirm, "Sure?")\nprint("went on", ok)\n')
    assert run.code == EXIT_NEEDS_UI
    assert run.lines == []


def test_arg_answers_by_prompt():
    run = Run('print(opensak.confirm("Sure?"))\n'
              'print(opensak.input("Name?", "x"))\n'
              'print(opensak.choose("Pick", { "a", "b" }, "a"))\n',
              answers={"Sure?": "no", "Name?": "given", "Pick": "b"})
    assert run.code == 0
    assert run.lines == ["false", "given", "b"]


def test_yes_takes_the_defaults():
    run = Run('print(opensak.confirm("Sure?"))\n'
              'print(opensak.input("Name?", "x"))\n'
              'print(opensak.choose("Pick", { "a", "b" }, "b"))\n', yes=True)
    assert run.code == 0
    assert run.lines == ["true", "x", "b"]


def test_yes_has_no_answer_for_choose_without_a_default():
    assert Run('opensak.choose("Pick", { "a", "b" })', yes=True).code == EXIT_NEEDS_UI


@pytest.mark.parametrize("source, answers", [
    ('opensak.confirm("Sure?")', {"Sure?": "maybe"}),
    ('opensak.choose("Pick", { "a" })', {"Pick": "z"}),
])
def test_a_bad_answer_is_a_usage_error(source, answers):
    assert Run(source, answers=answers).code == EXIT_USAGE


@pytest.mark.parametrize("source", [
    'opensak.choose_file("Pick")', 'opensak.choose_folder("Pick")',
])
def test_yes_never_answers_a_picker(source):
    assert Run(source, yes=True).code == EXIT_NEEDS_UI


def test_a_picker_answer_comes_from_arg(tmp_path):
    run = Run('print(opensak.choose_folder("Pick"))\n', answers={"Pick": str(tmp_path)})
    assert run.code == 0
    assert run.lines == [str(tmp_path.resolve())]


def test_saved_settings_cannot_answer_a_picker(tmp_path):
    # The macro controls its saved settings; a picked folder skips the folder list.
    macro = tmp_path / "picker.lua"
    source = ('opensak.settings.save({ Pick = "' + tmp_path.as_posix() + '" })\n'
              'opensak.choose_folder("Pick")\n')
    macro.write_text(source, encoding="utf-8")
    host = HeadlessHost(output=lambda _: None, status=lambda _: None, yes=True)
    runtime = MacroRuntime(host, output=lambda _: None, settings_dir=tmp_path / "settings")
    host.set_stop(runtime.stop_run)
    assert runtime.run(source, macro_path=macro) == EXIT_NEEDS_UI


def test_message_and_progress_are_written():
    run = Run('opensak.message("Hello", "Title")\n'
              'local p = opensak.progress("Working", 2)\n'
              'print(p:update(1, "one"))\n'
              'p:close()\n')
    assert run.code == 0
    assert run.lines == ["Title: Hello", "true"]
    assert run.status == ["Working", "one"]


# ── Forms ────────────────────────────────────────────────────────────────────

FORM = ('local v, closer = opensak.form{ title = "F", fields = {\n'
        '  { type = "text", id = "name", default = "x" },\n'
        '  { type = "number", id = "n", default = 1 },\n'
        '  { type = "checkbox", id = "on" },\n'
        '  { type = "checklist", id = "list", options = { "a", "b", "c" } },\n'
        '} }\n'
        'print(closer, v.name, v.n, v.on, #v.list)\n')


def test_form_with_every_field_given():
    run = Run(FORM, answers={"name": "y", "n": "7", "on": "yes", "list": "a, c"})
    assert run.code == 0
    assert run.lines == ["ok\ty\t7\ttrue\t2"]


def test_form_with_a_field_missing_needs_ui():
    assert Run(FORM, answers={"name": "y"}).code == EXIT_NEEDS_UI


def test_form_with_yes_takes_the_defaults_and_arg_wins():
    run = Run(FORM, yes=True, answers={"name": "y"})
    assert run.lines == ["ok\ty\t1\tfalse\t0"]


def test_a_bad_form_value_is_a_usage_error():
    assert Run(FORM, yes=True, answers={"n": "many"}).code == EXIT_USAGE


def test_a_form_without_fields_needs_yes():
    source = 'print(select(2, opensak.form{ title = "F", fields = { { type = "label", label = "Hi" } } }))\n'
    assert Run(source).code == EXIT_NEEDS_UI
    assert Run(source, yes=True).lines == ["ok"]


def test_remembered_values_answer_a_form(tmp_path):
    macro = tmp_path / "remember.lua"
    source = ('local v = opensak.form{ title = "F", remember = true, fields = {\n'
              '  { type = "text", id = "name", default = "x" } } }\n'
              'print(v.name)\n')
    macro.write_text(source, encoding="utf-8")

    def run(**options):
        lines: list[str] = []
        host = HeadlessHost(output=lines.append, status=lines.append, **options)
        runtime = MacroRuntime(host, output=lines.append, settings_dir=tmp_path / "settings")
        host.set_stop(runtime.stop_run)
        return runtime.run(source, macro_path=macro), lines

    assert run()[0] == EXIT_NEEDS_UI
    assert run(answers={"name": "saved"}) == (0, ["saved"])
    assert run() == (0, ["saved"])


def test_form_callbacks_run_and_a_change_is_reported():
    run = Run('local v = opensak.form{ title = "F",\n'
              '  on_open = function(f) print("open") end,\n'
              '  fields = { { type = "text", id = "a", default = "x",\n'
              '               on_change = function(f, value) print("change", value) end } },\n'
              '  validate = function(f) print("validate") end }\n'
              'print(v.a)\n', answers={"a": "y"})
    assert run.code == 0
    assert run.lines == ["open", "change\ty", "validate", "y"]


def test_a_required_field_without_a_value_needs_ui():
    source = 'opensak.form{ title = "F", fields = { { type = "text", id = "a", required = true } } }'
    assert Run(source, yes=True).code == EXIT_NEEDS_UI


def test_a_failed_validation_is_a_usage_error():
    source = ('opensak.form{ title = "F", fields = { { type = "text", id = "a" } },\n'
              '  validate = function(f) return "no good" end }')
    assert Run(source, answers={"a": "y"}).code == EXIT_USAGE


def test_a_question_inside_a_form_callback_stops_the_run():
    source = ('opensak.form{ title = "F", fields = { { type = "text", id = "a" } },\n'
              '  on_open = function(f) opensak.confirm("Sure?") end }\n'
              'print("went on")\n')
    run = Run(source, answers={"a": "y"})
    assert run.code == EXIT_NEEDS_UI
    assert run.lines == []


# ── Approvals ────────────────────────────────────────────────────────────────

def test_an_unapproved_write_stops_and_changes_nothing(tmp_path):
    macro = tmp_path / "write.lua"
    source = 'local ok = pcall(function() ' + WRITE + ' end)\nprint("went on")\n'
    run = Run(source, macro_path=macro)
    assert run.code == EXIT_NEEDS_UI
    assert run.lines == []
    assert _user_sort() != 5


def test_allow_write_writes_and_stores_no_approval(tmp_path):
    macro = tmp_path / "allowed.lua"
    source = WRITE.replace("5", "6")
    assert Run(source, macro_path=macro, allow_write=True).code == 0
    assert _user_sort() == 6
    assert not db_access.is_approved(macro, source_hash(source))
    assert Run(source, macro_path=macro).code == EXIT_NEEDS_UI


def test_an_always_approval_for_the_current_code_is_enough(tmp_path):
    macro = tmp_path / "always.lua"
    source = WRITE.replace("5", "8")
    db_access.approve(macro, WriteApproval.ALWAYS, source_hash(source))
    assert Run(source, macro_path=macro).code == 0
    assert _user_sort() == 8


def test_a_macro_edited_since_its_approval_stops(tmp_path):
    macro = tmp_path / "edited.lua"
    db_access.approve(macro, WriteApproval.ALWAYS, source_hash("-- the old code\n"))
    run = Run(WRITE.replace("5", "9"), macro_path=macro)
    assert run.code == EXIT_NEEDS_UI
    assert _user_sort() != 9
    # The approval is neither renewed nor taken over by --allow-write.
    source = WRITE.replace("5", "10")
    assert Run(source, macro_path=macro, allow_write=True).code == 0
    assert _user_sort() == 10
    assert not db_access.is_approved(macro, source_hash(source))


def test_an_unapproved_folder_stops(tmp_path):
    source = f'opensak.write_text("{(tmp_path / "x.txt").as_posix()}", "x")\nprint("went on")\n'
    run = Run(source)
    assert run.code == EXIT_NEEDS_UI
    assert run.lines == []
    assert not (tmp_path / "x.txt").exists()


# ── Outside `opensak run` ───────────────────────────────────────────────────

def test_not_headless_and_no_args_by_default():
    lines: list[str] = []
    host = HeadlessHost(output=lines.append, status=lines.append)
    runtime = MacroRuntime(cast(MacroHost, host), output=lines.append)
    assert runtime.run('print(opensak.is_headless(), next(opensak.args))\n') == 0
    assert lines == ["false\tnil"]


@pytest.mark.parametrize("header, expected", [
    ("", True), ("-- Headless: yes\n", True), ("-- headless: NO\n", False),
    ("-- Headless: maybe\n", True),
])
def test_headless_header(header, expected):
    info = parse_header(header + "print(1)\n", Path("m.lua"))
    assert info.runs_headless is expected


# ── Without a window ─────────────────────────────────────────────────────────

def test_switch_database_is_refused_without_a_switcher():
    with pytest.raises(MacroError):
        Run('opensak.create_database("Other")\nopensak.switch_database("Other")\n',
            allow_write=True)


def test_importing_the_host_does_not_import_the_gui():
    code = ("import sys\n"
            "import opensak.macro.headless\n"
            "print('BAD', [m for m in sys.modules if m.startswith('PySide6.QtWidgets')"
            " or m == 'opensak.app' or m == 'opensak.gui.mainwindow'])\n")
    src = Path(__file__).parents[2] / "src"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(src)})
    assert "BAD []" in out.stdout, out.stdout + out.stderr
