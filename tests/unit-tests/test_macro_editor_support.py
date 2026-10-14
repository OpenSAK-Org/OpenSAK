"""The LuaLS stub and .luarc.json in the user's macros folder (#1012)."""

import json

from opensak.macro.editor_support import (
    LUARC,
    bundled_stub,
    install_editor_support,
)


def test_bundled_stub_is_the_generated_one():
    assert bundled_stub().is_file()
    assert bundled_stub().read_text(encoding="utf-8").startswith("---@meta")


def test_fresh_folder_gets_stub_and_luarc(tmp_path):
    install_editor_support(tmp_path)
    assert (tmp_path / "types" / "opensak.lua").read_bytes() == bundled_stub().read_bytes()
    assert json.loads((tmp_path / ".luarc.json").read_text(encoding="utf-8")) == LUARC


def test_outdated_stub_is_refreshed(tmp_path):
    stub = tmp_path / "types" / "opensak.lua"
    stub.parent.mkdir()
    stub.write_text("---@meta\n-- from an older version\n", encoding="utf-8")
    install_editor_support(tmp_path)
    assert stub.read_bytes() == bundled_stub().read_bytes()


def test_existing_luarc_is_kept(tmp_path):
    luarc = tmp_path / ".luarc.json"
    luarc.write_text('{"runtime.version": "Lua 5.4"}\n', encoding="utf-8")
    install_editor_support(tmp_path)
    assert luarc.read_text(encoding="utf-8") == '{"runtime.version": "Lua 5.4"}\n'


def test_defaults_to_the_macros_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("opensak.macro.editor_support.get_macros_dir", lambda: tmp_path)
    install_editor_support()
    assert (tmp_path / "types" / "opensak.lua").is_file()
    assert (tmp_path / ".luarc.json").is_file()
