"""
tests/unit-tests/test_macro_lua_loop_1398.py — long-running Lua code keeps
the windows alive and can be cancelled (#1398).

A macro runs on the GUI thread. A long Lua loop used to keep it busy with
no host call in between, so the windows stopped painting, the desktop
called OpenSAK "not responding", and cancelling only took effect at the
macro's next OpenSAK call. The instruction-count hook now calls
MacroRuntime._tick(), which repaints the macro's progress window or lets
the host keep its windows painted (keep_alive), and stops a cancelled run.
"""

from __future__ import annotations

import threading
import time

import pytest

from opensak.macro import MacroError, MacroRuntime

# Busy for about half a second without calling OpenSAK. os.clock() is
# processor time, so a slow machine only runs fewer instructions. A tight
# loop runs the default budget (DEFAULT_INSTRUCTION_LIMIT) in a fraction of
# a second, so these runs get a larger one.
BUSY = "local t = os.clock() while os.clock() - t < 0.5 do end"
BUDGET = 5_000_000_000


class Host:
    """A MacroHost counting keep_alive() calls, with progress windows
    that record their updates."""

    def __init__(self):
        self.kept_alive = 0
        self.windows: list[Window] = []

    def keep_alive(self):
        self.kept_alive += 1

    def progress(self, title, total):
        self.windows.append(Window())
        return self.windows[-1]

    def clear_filter(self):
        pass

    def cache_count(self):
        return 0

    def end_macro(self):
        pass


class Window:
    def __init__(self):
        self.updates: list[tuple] = []

    def update(self, value, text):
        self.updates.append((value, text))
        return True

    def close(self):
        pass


def run(host, source: str, **kwargs) -> list[str]:
    out: list[str] = []
    kwargs.setdefault("instruction_limit", BUDGET)
    MacroRuntime(host, output=out.append, **kwargs).run(source)
    return out


def test_a_long_loop_keeps_the_windows_alive():
    host = Host()
    run(host, BUSY)
    assert host.kept_alive >= 5


def test_the_windows_are_kept_alive_at_most_every_interval(monkeypatch):
    import opensak.macro.runtime as runtime

    monkeypatch.setattr(runtime, "KEEP_ALIVE_INTERVAL_S", 0.2)
    host = Host()
    run(host, BUSY)
    assert 1 <= host.kept_alive <= 4            # 0.5 s / 0.2 s, plus the first


def test_a_loop_in_a_coroutine_keeps_the_windows_alive_too():
    host = Host()
    run(host, f"coroutine.wrap(function() {BUSY} end)()")
    assert host.kept_alive >= 5


def test_an_open_progress_window_is_repainted_instead():
    host = Host()
    run(host, f'local p = opensak.progress("Working", 10)\n'
              f'p:update(3, "three")\n{BUSY}\np:close()')
    [window] = host.windows
    assert window.updates[0] == (3, "three")
    repaints = window.updates[1:]
    assert len(repaints) >= 5 and set(repaints) == {(None, "")}   # value and text kept
    assert host.kept_alive == 0


def _cancel_during(rt: MacroRuntime, source: str) -> float:
    """Run *source*, cancelling it until the run ends (a single cancel
    could land before run() resets the flag, as in test_macro_helpers);
    the seconds it took."""
    done = threading.Event()

    def keep_cancelling() -> None:
        while not done.wait(0.05):
            rt.cancel()

    canceller = threading.Thread(target=keep_cancelling, daemon=True)
    canceller.start()
    started = time.monotonic()
    try:
        with pytest.raises(MacroError, match="macro cancelled"):
            rt.run(source)
    finally:
        done.set()
        canceller.join()
    return time.monotonic() - started


def test_cancel_stops_a_loop_without_opensak_calls():
    # The budget lasts many seconds, so only the cancel can end the loop soon.
    rt = MacroRuntime(Host(), output=lambda _t: None, instruction_limit=BUDGET)
    assert _cancel_during(rt, "while true do end") < 2


def test_pcall_cannot_catch_the_cancel():
    out: list[str] = []
    rt = MacroRuntime(Host(), output=out.append, instruction_limit=BUDGET)
    _cancel_during(rt, 'while true do pcall(function() while true do end end) end\n'
                       'print("went on")')
    assert out == []


def test_a_failing_repaint_does_not_end_the_macro():
    class Broken(Host):
        def keep_alive(self):
            super().keep_alive()
            raise RuntimeError("no window")

    host = Broken()
    out = run(host, f'local ok = pcall(function() {BUSY} end)\nprint(ok)')
    assert out == ["true"] and host.kept_alive >= 5
