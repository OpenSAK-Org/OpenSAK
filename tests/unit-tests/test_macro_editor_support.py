"""The editor files (LuaLS stub, Notepad++, UltraEdit) and .luarc.json in
the user's macros folder (#1012).

The files are generated and not committed by feature branches (see
.github/workflows/macro-api-docs.yml), so the copy tests run against a
bundle of their own instead of whatever macros/types holds right now."""

import json

import pytest

from opensak.macro.editor_support import (
    EDITOR_FILES,
    LUARC,
    bundled_stub,
    install_editor_support,
)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A fake bundled macros/types folder holding every editor file."""
    types = tmp_path / "bundle" / "types"
    for name in EDITOR_FILES:
        (types / name).parent.mkdir(parents=True, exist_ok=True)
        (types / name).write_text(f"-- bundled {name}\n", encoding="utf-8")
    monkeypatch.setattr("opensak.macro.editor_support.bundled_types_dir", lambda: types)
    return types


def test_bundled_stub_is_the_generated_one():
    assert bundled_stub().is_file()
    assert bundled_stub().read_text(encoding="utf-8").startswith("---@meta")


def test_fresh_folder_gets_editor_files_and_luarc(tmp_path, bundle):
    macros = tmp_path / "macros"
    install_editor_support(macros)
    for name in EDITOR_FILES:
        assert (macros / "types" / name).read_bytes() == (bundle / name).read_bytes(), name
    assert json.loads((macros / ".luarc.json").read_text(encoding="utf-8")) == LUARC


def test_outdated_stub_is_refreshed(tmp_path, bundle):
    stub = tmp_path / "macros" / "types" / "opensak.lua"
    stub.parent.mkdir(parents=True)
    stub.write_text("---@meta\n-- from an older version\n", encoding="utf-8")
    install_editor_support(tmp_path / "macros")
    assert stub.read_bytes() == (bundle / "opensak.lua").read_bytes()


def test_outdated_notepadpp_file_is_refreshed(tmp_path, bundle):
    xml = tmp_path / "macros" / "types" / "notepad++" / "lua.xml"
    xml.parent.mkdir(parents=True)
    xml.write_text("<NotepadPlus />\n", encoding="utf-8")
    install_editor_support(tmp_path / "macros")
    assert xml.read_bytes() == (bundle / "notepad++" / "lua.xml").read_bytes()


def test_file_missing_from_the_bundle_is_skipped(tmp_path, bundle):
    # Not generated yet in this checkout: the others are still copied.
    (bundle / "ultraedit" / "opensak-lua.uew").unlink()
    install_editor_support(tmp_path / "macros")
    assert (tmp_path / "macros" / "types" / "opensak.lua").is_file()
    assert not (tmp_path / "macros" / "types" / "ultraedit" / "opensak-lua.uew").exists()


def test_existing_luarc_is_kept(tmp_path, bundle):
    luarc = tmp_path / ".luarc.json"
    luarc.write_text('{"runtime.version": "Lua 5.4"}\n', encoding="utf-8")
    install_editor_support(tmp_path)
    assert luarc.read_text(encoding="utf-8") == '{"runtime.version": "Lua 5.4"}\n'


def test_defaults_to_the_macros_folder(tmp_path, monkeypatch, bundle):
    monkeypatch.setattr("opensak.macro.editor_support.get_macros_dir", lambda: tmp_path)
    install_editor_support()
    assert (tmp_path / "types" / "opensak.lua").is_file()
    assert (tmp_path / ".luarc.json").is_file()
