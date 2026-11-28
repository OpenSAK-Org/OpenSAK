"""
tests/unit-tests/test_macro_wait_1398.py — opensak.sleep() keeps the
windows alive (#1398).

A macro runs on the GUI thread. opensak.sleep() used to block it for the
whole pause (up to 10 s), so the windows stopped painting and the desktop
called OpenSAK "not responding". It now waits in short steps and in
between repaints the macro's progress window or, without one, lets the
host keep its windows painted (keep_alive).

The tests that count repaints run on a fake clock (``clock``), which
moves on by exactly the step each wait asks for: on a busy CI runner a
real wait can overshoot so far that a 300 ms sleep has only two steps.
"""

from __future__ import annotations

import threading
import time

import pytest

from opensak.macro import MacroRuntime
from opensak.macro import runtime as macro_runtime


class Host:
    """A MacroHost counting keep_alive() calls, with progress windows
    that record their updates and can be cancelled."""

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
        self.cancelled = False
        self.closed = False

    def update(self, value, text):
        self.updates.append((value, text))
        return not self.cancelled

    def close(self):
        self.closed = True


class FakeClock:
    """Stands in for the runtime's ``time`` module: monotonic() only
    moves when a wait (ClockedEvent) lets it; all else is the real one."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


class ClockedEvent(threading.Event):
    """The runtime's cancel event, whose wait() returns at once and moves
    the fake clock on by the timeout instead of sleeping."""

    def __init__(self, clock: FakeClock):
        super().__init__()
        self._clock = clock

    def wait(self, timeout=None) -> bool:
        if timeout:
            self._clock.now += timeout
        return self.is_set()


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(macro_runtime, "time", fake)
    return fake


def run(host: Host, source: str, clock: FakeClock | None = None) -> list[str]:
    out: list[str] = []
    runtime = MacroRuntime(host, output=out.append)  # type: ignore[arg-type]
    if clock is not None:
        runtime._cancelled = ClockedEvent(clock)
    runtime.run(source)
    return out


def test_sleep_still_sleeps_as_long():
    host = Host()
    started = time.monotonic()
    run(host, "opensak.sleep(500)")
    assert time.monotonic() - started >= 0.45
    assert host.kept_alive >= 1


def test_sleep_keeps_the_windows_alive(clock):
    host = Host()
    run(host, "opensak.sleep(500)", clock)
    assert host.kept_alive >= 10                        # every 50 ms step


def test_sleep_repaints_the_progress_window_instead(clock):
    host = Host()
    run(host, 'local p = opensak.progress("Working", 10)\n'
              'p:update(3, "three")\n'
              'opensak.sleep(500)\n'
              'p:close()', clock)
    [window] = host.windows
    assert window.updates[0] == (3, "three")
    repaints = window.updates[1:]
    assert len(repaints) >= 10 and set(repaints) == {(None, "")}   # value and text kept
    assert host.kept_alive == 0


def test_cancel_during_sleep_is_seen_at_the_next_update():
    host = Host()
    real = host.progress

    def progress(title, total):
        window = real(title, total)
        window.cancelled = True                 # the user presses Cancel at once
        return window

    host.progress = progress                    # type: ignore[method-assign]
    out = run(host, 'local p = opensak.progress("Working", 10)\n'
                    'opensak.sleep(200)\n'      # the sleep itself goes on
                    'print(p:update(1, "one"))')
    assert out == ["false"]


def test_a_failing_repaint_does_not_end_the_sleep(clock):
    class Broken(Host):
        def keep_alive(self):
            super().keep_alive()
            raise RuntimeError("no window")

    host = Broken()
    out = run(host, 'opensak.sleep(300)\nprint("went on")', clock)
    assert out == ["went on"] and host.kept_alive >= 6
