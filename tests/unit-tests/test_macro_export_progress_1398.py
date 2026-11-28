"""
tests/unit-tests/test_macro_export_progress_1398.py — opensak.export_gpx()
and opensak.export_file() show a progress window and can be cancelled
(#1398).

A macro runs on the GUI thread. The exports wrote the whole file in one
go, with no progress window and no cancel check, so a large export (10 s
for 5,000 caches, 42 s for 20,000) froze the windows until the file was
written. They now open a progress window, update it while they write, and
stop on Cancel without leaving a file behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opensak.db.database import get_session
from opensak.db.models import Cache
from opensak.export.file_export_settings import FileExportProfile, FileExportSettings
from opensak.lang import tr
from opensak.macro import MacroError, MacroRuntime
from opensak.macro.permissions import FolderPermission

CACHES = 300


@pytest.fixture(scope="module", autouse=True)
def seed(tmp_db):
    with get_session() as s:
        for i in range(CACHES):
            s.add(Cache(gc_code=f"GCX{i:04d}", name=f"Cache {i}",
                        cache_type="Traditional Cache", difficulty=1.0, terrain=1.0,
                        latitude=47 + i / 1000, longitude=8 + i / 1000))


class Window:
    def __init__(self, host: "Host", title: str):
        self.host, self.title = host, title
        self.updates: list[tuple] = []
        self.closed = False

    def update(self, value, text):
        self.updates.append((value, text))
        if self.host.on_update is not None:
            return self.host.on_update(self)
        return True

    def close(self):
        self.closed = True


class Host:
    """A MacroHost exporting every cache in the test database."""

    def __init__(self):
        self.windows: list[Window] = []
        # Called at every update; returns False to press Cancel.
        self.on_update = None

    def progress(self, title, total):
        self.windows.append(Window(self, title))
        return self.windows[-1]

    def filtered_caches(self):
        with get_session() as s:
            return s.query(Cache).order_by(Cache.gc_code).all()

    def database_name(self):
        return "TestDB"

    def filter_name(self):
        return "All"

    def center_name(self):
        return "Home"

    def clear_filter(self):
        pass

    def cache_count(self):
        return CACHES

    def end_macro(self):
        pass


def runtime(host, tmp_path: Path, out: list | None = None) -> MacroRuntime:
    FileExportProfile("GPX Export", FileExportSettings(
        folder=str(tmp_path / "out"), file_name="saved", if_exists="overwrite",
    )).save(tmp_path / "export_settings")
    return MacroRuntime(
        host, output=(out if out is not None else []).append,
        export_settings_dir=tmp_path / "export_settings",
        folder_permissions=[FolderPermission(str(tmp_path), read=True, write=True)],
    )


def export_gpx(tmp_path: Path, extra: str = "") -> str:
    return f'print(opensak.export_gpx{{ path = {str(tmp_path / "out" / "a.gpx")!r}, {extra} }})'


@pytest.mark.parametrize("source, name", [
    (lambda tmp: export_gpx(tmp), "a.gpx"),
    (lambda tmp: 'print(opensak.export_file("GPX Export"))', "saved.gpx"),
])
def test_export_shows_its_progress(tmp_path, source, name):
    host = Host()
    out: list[str] = []
    runtime(host, tmp_path, out).run(source(tmp_path))
    assert out == [f"{(tmp_path / 'out' / name).resolve()}\t{CACHES}"]
    [window] = host.windows
    assert window.title == tr("file_export_dialog_title") and window.closed
    assert window.updates[-1] == (100, name)            # the last cache is always shown
    assert {text for _, text in window.updates} == {name}   # no counting text


def test_updates_are_throttled(tmp_path, monkeypatch):
    import opensak.macro.runtime as rt

    monkeypatch.setattr(rt, "_EXPORT_PROGRESS_S", 3600)
    host = Host()
    runtime(host, tmp_path).run(export_gpx(tmp_path))
    [window] = host.windows
    assert window.updates == [(0, "a.gpx"), (100, "a.gpx")]  # the first and the last


def test_cancel_in_the_window_stops_the_export_and_keeps_the_old_file(tmp_path):
    old = tmp_path / "out" / "a.gpx"
    old.parent.mkdir()
    old.write_text("old file", encoding="utf-8")
    host = Host()
    host.on_update = lambda window: len(window.updates) < 2  # Cancel at the 2nd update
    with pytest.raises(MacroError, match=r"export_gpx: cancelled by the user: .*a\.gpx"):
        runtime(host, tmp_path).run(export_gpx(tmp_path))
    assert old.read_text(encoding="utf-8") == "old file"
    assert sorted(p.name for p in old.parent.iterdir()) == ["a.gpx"]   # no temporary file
    assert host.windows[0].closed


def test_cancelling_the_macro_stops_the_export(tmp_path):
    host = Host()
    rt = runtime(host, tmp_path)

    def cancel(window):
        rt.cancel()
        return True

    host.on_update = cancel
    with pytest.raises(MacroError, match="^macro cancelled$"):
        rt.run(export_gpx(tmp_path))
    assert list((tmp_path / "out").iterdir()) == []


def test_pcall_cannot_catch_cancelling_the_macro(tmp_path):
    host = Host()
    out: list[str] = []
    rt = runtime(host, tmp_path, out)

    def cancel(window):
        rt.cancel()
        return True

    host.on_update = cancel
    with pytest.raises(MacroError, match="^macro cancelled$"):
        rt.run(f'pcall(function() {export_gpx(tmp_path)} end)\nprint("went on")')
    assert out == []


def test_cancel_while_the_macro_names_the_caches(tmp_path):
    host = Host()
    host.on_update = lambda window: len(window.updates) < 2
    out: list[str] = []
    rt = runtime(host, tmp_path, out)
    with pytest.raises(MacroError, match="cancelled by the user"):
        rt.run('local n = 0\n' + export_gpx(
            tmp_path, 'rename = function(c) n = n + 1 return "R " .. c.name end'
        ) + '\nprint("went on")')
    [window] = host.windows
    assert window.updates[0] == (None, "a.gpx")      # no total while naming
    assert out == [] and not (tmp_path / "out" / "a.gpx").exists()


def test_without_a_user_interface_the_export_still_works(tmp_path):
    class Headless(Host):
        progress = None                             # type: ignore[assignment]

    out: list[str] = []
    runtime(Headless(), tmp_path, out).run(export_gpx(tmp_path))
    assert out == [f"{(tmp_path / 'out' / 'a.gpx').resolve()}\t{CACHES}"]
