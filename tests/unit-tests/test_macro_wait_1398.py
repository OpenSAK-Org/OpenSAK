"""
tests/unit-tests/test_macro_wait_1398.py — opensak.sleep() keeps the
windows alive (#1398).

A macro runs on the GUI thread. opensak.sleep() used to block it for the
whole pause (up to 10 s), so the windows stopped painting and the desktop
called OpenSAK "not responding". It now waits in short steps and in
between repaints the macro's progress window or, without one, lets the
host keep its windows painted (keep_alive).
"""

from __future__ import annotations

import time

from opensak.macro import MacroRuntime


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


def run(host: Host, source: str) -> list[str]:
    out: list[str] = []
    MacroRuntime(host, output=out.append).run(source)  # type: ignore[arg-type]
    return out


def test_sleep_keeps_the_windows_alive():
    host = Host()
    started = time.monotonic()
    run(host, "opensak.sleep(500)")
    assert time.monotonic() - started >= 0.45           # still sleeps as long
    assert host.kept_alive >= 5


def test_sleep_repaints_the_progress_window_instead():
    host = Host()
    run(host, 'local p = opensak.progress("Working", 10)\n'
              'p:update(3, "three")\n'
              'opensak.sleep(500)\n'
              'p:close()')
    [window] = host.windows
    assert window.updates[0] == (3, "three")
    repaints = window.updates[1:]
    assert len(repaints) >= 5 and set(repaints) == {(None, "")}   # value and text kept
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
