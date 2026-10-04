"""The Lua API registry, the `opensak` table and docs/macros/api.md must agree."""

from pathlib import Path

import pytest

from opensak.macro.api_docs import DOC_PATH, render_api_markdown
from opensak.macro.runtime import (
    API,
    API_VERSION,
    FILTER_KEY_DOCS,
    FILTER_KEYS,
    MacroRuntime,
)

REGENERATE = "run: python scripts/generate_macro_api_docs.py"


class _Host:
    """Just enough of a MacroHost to build the `opensak` table."""

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def end_macro(self):
        pass


def test_every_exposed_function_is_documented():
    out: list[str] = []
    MacroRuntime(_Host(), output=out.append).run(  # type: ignore[arg-type]
        "for name in pairs(opensak) do print(name) end")
    assert sorted(out) == sorted(f.name for f in API)


def test_api_names_are_unique():
    names = [f.name for f in API]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("func", API, ids=[f.name for f in API])
def test_api_entry_is_complete(func):
    assert func.signatures and all(s.startswith(f"opensak.{func.name}") for s in func.signatures)
    assert func.description.strip() and func.example.strip()
    assert 1 <= func.since <= API_VERSION


@pytest.mark.parametrize("func", API, ids=[f.name for f in API])
def test_api_example_compiles(func):
    from lupa.lua54 import LuaRuntime

    LuaRuntime().compile(func.example)


def test_every_filter_key_is_documented():
    documented = [k for doc in FILTER_KEY_DOCS for k in doc.keys]
    assert sorted(documented) == sorted(FILTER_KEYS)


def test_generated_doc_is_up_to_date():
    path = Path(DOC_PATH)
    assert path.exists(), f"{DOC_PATH} is missing — {REGENERATE}"
    assert path.read_text(encoding="utf-8") == render_api_markdown(), \
        f"{DOC_PATH} is out of date — {REGENERATE}"
