"""tests/unit-tests/test_macro_info_1351.py — opensak.macro_info() (#1351).

A macro reads its own header (Name, Version, …) instead of repeating the
values in its code and keeping them in sync with the header.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from opensak.macro import MacroRuntime
from opensak.macro.api_docs import render_api_markdown, render_lua_stub
from opensak.macro.permissions import FolderPermission
from opensak.macro.runtime import API, API_VERSION

HEADER = (
    "-- OpenSAK Macro\n"
    "-- Name: example-macro\n"
    "-- Author: JohnDoe\n"
    "-- Version: 1.10\n"
    "-- Requires-API: 5\n"
    "-- Description: blah blah blah\n"
    "-- Permissions: write, read-files, run-macros\n"
    "-- Network: api.example.com\n"
)


def _runtime(folder: Path | None = None):
    out: list[str] = []
    permissions = [FolderPermission(str(folder), read=True, write=False)] if folder else None
    return MacroRuntime(MagicMock(), output=out.append, folder_permissions=permissions), out


def _lua(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_header_values_are_returned_as_written(tmp_path):
    file = _lua(tmp_path / "example.lua", HEADER + (
        "local i = opensak.macro_info()\n"
        "print(i.name, i.author, i.version, i.requires_api, i.description)\n"
        "print(type(i.version), i.headless, i.file)\n"
        "print(table.concat(i.permissions, ','), table.concat(i.network, ','))\n"
    ))
    runtime, out = _runtime(tmp_path)
    runtime.run(file.read_text(encoding="utf-8"), base_dir=tmp_path, macro_path=file)
    assert out == [
        "example-macro\tJohnDoe\t1.10\t5\tblah blah blah",
        f"string\ttrue\t{file}",
        "write,read-files,run-macros\tapi.example.com",
    ]


def test_missing_keys_give_empty_strings_and_the_file_name(tmp_path):
    file = _lua(tmp_path / "plain.lua", (
        "local i = opensak.macro_info()\n"
        "print(i.name, i.version == '', i.author == '', #i.permissions, #i.network)\n"
    ))
    runtime, out = _runtime(tmp_path)
    runtime.run(file.read_text(encoding="utf-8"), base_dir=tmp_path, macro_path=file)
    assert out == ["plain\ttrue\ttrue\t0\t0"]


def test_unsaved_code_has_no_file():
    runtime, out = _runtime()
    runtime.run("-- Version: 2.0\n-- Headless: no\n"
                "local i = opensak.macro_info()\n"
                "print(i.version, i.headless, i.file)")
    assert out == ["2.0\tfalse\tnil"]


def test_called_macro_sees_its_own_header(tmp_path):
    _lua(tmp_path / "called.lua", "-- Version: 2.0\n"
                                  "print('called', opensak.macro_info().version)")
    caller = _lua(tmp_path / "caller.lua", "-- Version: 1.0\n"
                                           "opensak.run_macro('called')\n"
                                           "print('caller', opensak.macro_info().version)")
    runtime, out = _runtime(tmp_path)
    runtime.run(caller.read_text(encoding="utf-8"), base_dir=tmp_path, macro_path=caller)
    assert out == ["called\t2.0", "caller\t1.0"]


def test_returned_table_is_a_copy(tmp_path):
    runtime, out = _runtime()
    runtime.run("-- Version: 1.0\n"
                "opensak.macro_info().version = 'x'\n"
                "print(opensak.macro_info().version)")
    assert out == ["1.0"]


def test_registered_in_the_current_api_version():
    func = next(f for f in API if f.name == "macro_info")
    assert func.since == 5 <= API_VERSION
    assert func.flag is None


def test_documented_in_docs_and_stub():
    assert "opensak.macro_info()" in render_api_markdown()
    stub = render_lua_stub()
    assert "---@class opensak.MacroInfo" in stub
    assert "---@return opensak.MacroInfo" in stub
