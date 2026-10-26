"""
tests/unit-tests/test_macro_declarations_1079.py — what a macro's header
declares (Permissions, Network) is shown before the macro first runs, and
again only when it declares more (#1079). A notice, never an approval.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opensak.macro import MacroError, MacroRuntime
from opensak.macro import db_access, declarations
from opensak.macro.examples import bundled_examples_dir, list_examples, shipped_hashes


class Host:
    """A MacroHost answering the declaration notice with *answer*."""

    def __init__(self, answer=True):
        self.answer = answer
        self.shown: list[tuple[Path, tuple[str, ...], bool]] = []

    def confirm_declarations(self, macro, info, added, first, editing=False):
        self.shown.append((macro, added, first))
        return self.answer

    def end_macro(self):
        pass

    def __getattr__(self, name):
        # Bound into the opensak table at every run, never called here.
        return lambda *args: None


def run(host, path: Path, source: str | None = None) -> list[str]:
    """Run the macro file *path* (written with *source* first, if given)."""
    if source is not None:
        path.write_text(source, encoding="utf-8")
    out: list[str] = []
    MacroRuntime(host, output=out.append).run(
        db_access.read_macro_source(path), base_dir=path.parent, macro_path=path)
    return out


HEADER = "-- Permissions: write\n-- Network: api.example.com\n"


def test_shown_before_the_first_run_only(tmp_path):
    host = Host()
    macro = tmp_path / "m.lua"
    assert run(host, macro, HEADER + 'print("ran")') == ["ran"]
    assert host.shown == [(macro, ("write", "network:api.example.com"), True)]
    run(host, macro)
    assert len(host.shown) == 1


def test_shown_again_when_the_macro_declares_more(tmp_path):
    host = Host()
    macro = tmp_path / "m.lua"
    run(host, macro, HEADER + 'print("1")')
    # Declaring less, or the same, runs without the notice.
    run(host, macro, '-- Permissions: write\nprint("2")')
    assert len(host.shown) == 1
    run(host, macro, '-- Permissions: write, open\n-- Network: b.example.com\nprint("3")')
    assert host.shown[1] == (macro, ("open", "network:b.example.com"), False)


@pytest.mark.parametrize("source", [
    'print("no header")',
    '-- Name: Plain\n-- Version: 1\nprint("x")',
    '-- Permissions: none\nprint("x")',
])
def test_not_shown_when_nothing_is_declared(tmp_path, source):
    host = Host(answer=False)
    assert run(host, tmp_path / "m.lua", source) == ["x" if "x" in source else "no header"]
    assert host.shown == []


def test_cancel_does_not_run_the_macro(tmp_path):
    host = Host(answer=False)
    macro = tmp_path / "m.lua"
    with pytest.raises(MacroError, match="'m.lua' was not run: cancelled by the user"):
        run(host, macro, HEADER + 'print("ran")')
    assert declarations.seen(macro) is None
    # Asked again next time, and running then remembers it.
    host.answer = True
    assert run(host, macro) == ["ran"]
    assert len(host.shown) == 2 and declarations.seen(macro) is not None


def test_unsaved_macro_and_headless_run_show_nothing(tmp_path):
    host = Host(answer=False)
    out: list[str] = []
    MacroRuntime(host, output=out.append).run(HEADER + 'print("unsaved")')
    assert out == ["unsaved"] and host.shown == []

    class Headless:
        def end_macro(self):
            pass

        def __getattr__(self, name):
            if name == "confirm_declarations":
                raise AttributeError(name)
            return lambda *args: None

    assert run(Headless(), tmp_path / "m.lua", HEADER + 'print("headless")') == ["headless"]


def test_called_macro_shows_its_own_declaration(tmp_path):
    (tmp_path / "inner.lua").write_text('-- Network: inner.example.com\nprint("inner")',
                                        encoding="utf-8")

    class Folders(Host):
        def approve_folder(self, target, folder, write, macro=None):
            from opensak.macro import FolderApproval
            return FolderApproval.ONCE

    host = Folders()
    run(host, tmp_path / "outer.lua", '-- Permissions: run-macros\nopensak.run_macro("inner.lua")')
    assert [(m.name, added) for m, added, _ in host.shown] == [
        ("outer.lua", ("run-macros",)), ("inner.lua", ("network:inner.example.com",))]


def test_unchanged_shipped_example_shows_nothing(tmp_path):
    names = list_examples()
    declaring = [n for n in names
                 if "Permissions:" in (bundled_examples_dir() / n).read_text(encoding="utf-8")]
    assert declaring, "the shipped examples declare permissions"
    source = db_access.read_macro_source(bundled_examples_dir() / declaring[0])
    assert db_access.source_hash(source) in shipped_hashes()

    host = Host(answer=False)
    copy = tmp_path / declaring[0]
    runtime = MacroRuntime(host, output=lambda _t: None)

    # Unchanged: not shown. The example may fail here without a database
    # or user interface, but not because the notice was cancelled.
    try:
        runtime.run(source, macro_path=copy, base_dir=tmp_path)
    except MacroError as exc:
        assert "cancelled by the user" not in str(exc)
    assert host.shown == []

    # Changed by the user: shown like any other macro.
    with pytest.raises(MacroError, match="cancelled by the user"):
        runtime.run(source + "\n-- my change\n", macro_path=copy, base_dir=tmp_path)
    assert len(host.shown) == 1
