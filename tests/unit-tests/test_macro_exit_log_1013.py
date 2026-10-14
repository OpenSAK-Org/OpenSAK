"""tests/unit-tests/test_macro_exit_log_1013.py — runtime parts of #1013.

opensak.exit() and opensak.log(), errors of API functions naming the macro
line, cancelling from inside a dialog, and one macro at a time.
"""

from unittest.mock import MagicMock

import pytest

from opensak.macro import MacroBusy, MacroError, MacroRuntime, macro_running


def _runtime(host=None):
    out: list[str] = []
    log: list[str] = []
    runtime = MacroRuntime(host or MagicMock(), output=out.append, log=log.append)
    return runtime, out, log


def test_run_returns_zero_without_exit():
    runtime, out, _ = _runtime()
    assert runtime.run('print("hi")') == 0
    assert out == ["hi"]


@pytest.mark.parametrize("source, code", [
    ("opensak.exit(3)", 3),
    ("opensak.exit()", 0),
    ("opensak.exit(255)", 255),
])
def test_exit_returns_code_and_stops(source, code):
    runtime, out, _ = _runtime()
    assert runtime.run(f'{source} print("not reached")') == code
    assert out == []


def test_exit_cannot_be_caught_by_pcall():
    runtime, out, _ = _runtime()
    source = 'pcall(function() opensak.exit(4) end) print("not reached") while true do end'
    assert runtime.run(source) == 4
    assert out == []


def test_exit_inside_coroutine_ends_the_macro():
    runtime, out, _ = _runtime()
    source = 'coroutine.wrap(function() opensak.exit(7) end)() print("not reached")'
    assert runtime.run(source) == 7
    assert out == []


@pytest.mark.parametrize("arg", ["1.5", "-1", "256", '"x"'])
def test_exit_rejects_bad_codes(arg):
    runtime, _, _ = _runtime()
    with pytest.raises(MacroError, match="opensak.exit expects a whole number"):
        runtime.run(f"opensak.exit({arg})")


def test_exit_state_does_not_leak_into_the_next_run():
    runtime, out, _ = _runtime()
    assert runtime.run("opensak.exit(5)") == 5
    assert runtime.run('print("again")') == 0
    assert out == ["again"]


def test_log_goes_to_log_sink_and_log_file(caplog):
    runtime, out, log = _runtime()
    with caplog.at_level("INFO", logger="opensak.macro.runtime"):
        runtime.run('opensak.log("a", 1, true) print("b")')
    assert log == ["a\t1\ttrue"]
    assert out == ["b"]
    assert "macro: a\t1\ttrue" in caplog.text


def test_log_defaults_to_output():
    out: list[str] = []
    MacroRuntime(MagicMock(), output=out.append).run('opensak.log("x")')
    assert out == ["x"]


def test_api_error_names_the_calling_line():
    runtime, _, _ = _runtime()
    with pytest.raises(MacroError) as exc:
        runtime.run("\n\nopensak.sleep(-1)", chunk_name="m.lua")
    assert str(exc.value).startswith("m.lua:3: opensak.sleep expects")


def test_api_error_called_from_lua_function_names_that_line():
    runtime, out, _ = _runtime()
    runtime.run("local ok, e = pcall(function()\n  opensak.sleep(-1)\nend)\nprint(e)",
                chunk_name="m.lua")
    assert out == ["m.lua:2: opensak.sleep expects milliseconds >= 0, got -1"]


def test_api_error_called_directly_by_pcall_has_no_line():
    # pcall is a C function: there is no macro line to name.
    runtime, out, _ = _runtime()
    runtime.run("local ok, e = pcall(opensak.sleep, -1)\nprint(e)", chunk_name="m.lua")
    assert out == ["opensak.sleep expects milliseconds >= 0, got -1"]


def test_cancel_in_a_dialog_ends_the_macro_despite_pcall():
    host = MagicMock()
    runtime, out, _ = _runtime(host)

    def confirm(message):
        runtime.cancel()        # e.g. the --run-macro caller timed out
        return False

    host.confirm.side_effect = confirm
    with pytest.raises(MacroError, match="macro cancelled"):
        runtime.run('pcall(opensak.confirm, "go?") print("not reached")')
    assert out == []


def test_one_macro_at_a_time():
    host = MagicMock()
    runtime, _, _ = _runtime(host)
    other, _, _ = _runtime()
    seen: list = []

    def confirm(message):
        # A dialog's nested event loop could start a second run.
        assert macro_running()
        with pytest.raises(MacroBusy):
            other.run('print("second")')
        seen.append(True)
        return True

    host.confirm.side_effect = confirm
    runtime.run('opensak.confirm("x")')
    assert seen == [True]
    assert not macro_running()
