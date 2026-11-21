# tests/unit-tests/test_mtp.py — Windows MTP adapter (mtp.py) unit tests.
#
# These tests exercise MTPDevice, MTPPath, and find_mtp_devices using
# lightweight Python fakes in place of the real win32com Shell COM objects.
# No Windows or pywin32 dependency is needed to run them.

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from opensak.gps.garmin import (
    export_ggz_to_device,
    export_to_device,
    get_garmin_ggz_path,
    get_garmin_gpx_path,
    write_export,
)
from opensak.gps.mtp import (
    MTPDevice,
    MTPError,
    MTPPath,
    _copy_timeout,
    _find_item,
    _has_direct_garmin,
    _shell_folder,
    find_mtp_devices,
)


# ── Fake Shell COM objects ────────────────────────────────────────────────────


class FakeItem:
    """Mimics a Shell FolderItem with Name, Path, and optional GetFolder."""

    def __init__(self, name: str, children=None, *, path: str = "", filename: str | None = None,
                 data: bytes | None = None):
        self.Name = name
        self.data = data  # a file's content, as CopyHere received it
        self.Path = path
        self._filename = filename
        self._children = children  # list[FakeItem] or None (file)
        self._deleted = False
        # Cache the folder so every access returns the same object.
        self._folder = FakeFolder(children) if children is not None else None

    @property
    def GetFolder(self):
        if self._folder is None:
            raise RuntimeError("File items do not expose GetFolder")
        return self._folder

    @property
    def Size(self):
        return None if self.data is None else len(self.data)

    def InvokeVerb(self, verb: str) -> None:
        if verb == "delete":
            self._deleted = True

    def ExtendedProperty(self, name: str):
        if name in {"System.FileName", "System.ItemName"}:
            return self._filename
        if name == "System.FileExtension" and self._filename:
            return Path(self._filename).suffix
        return None


class FakeFolder:
    """Mimics a Shell Folder3 COM object."""

    def __init__(self, items: list[FakeItem]):
        self._items = list(items)

    def Items(self):
        return [i for i in self._items if not getattr(i, "_deleted", False)]

    def CopyHere(self, source: str, flags: int) -> None:
        # Simulate CopyHere by adding a new item with the source filename
        # (replacing an existing item, as the "yes to all" flag does).
        name = Path(source).name
        self._items = [i for i in self._items if i.Name.casefold() != name.casefold()]
        self._items.append(FakeItem(name, data=Path(source).read_bytes()))

    def NewFolder(self, name: str) -> None:
        self._items.append(FakeItem(name, children=[]))


class AsyncCopyFolder(FakeFolder):
    """A folder whose CopyHere, like the real one, returns before the copy
    is done: each tick() (one time.sleep() of the caller) copies part of
    the file, which replaces an existing one on the first tick, and the
    source stays open (cannot be deleted) until the copy is complete."""

    def __init__(self, items, steps: int = 3, keep_open: bool = False):
        super().__init__(items)
        self.steps = steps
        self.keep_open = keep_open
        self.pending: list | None = None  # [source, data, ticks]

    def CopyHere(self, source: str, flags: int) -> None:
        self.pending = [Path(source), Path(source).read_bytes(), 0]

    def tick(self, _seconds=0) -> None:
        if self.pending is None:
            return
        source, data, ticks = self.pending
        ticks += 1
        self.pending[2] = ticks
        name = source.name
        self._items = [i for i in self._items if i.Name.casefold() != name.casefold()]
        if ticks >= self.steps:
            self._items.append(FakeItem(name, data=data))
            if not self.keep_open:
                self.pending = None
        else:
            self._items.append(FakeItem(name, data=data[: len(data) * ticks // self.steps]))

    def holds(self, path: Path) -> bool:
        return self.pending is not None and self.pending[0] == path


def _make_garmin_tree():
    """Return a FakeItem tree: Internal Storage / GARMIN / {GPX, GarminDevice.xml}."""
    gpx_folder = FakeItem("GPX", children=[])
    device_xml = FakeItem("GarminDevice.xml")
    garmin = FakeItem("GARMIN", children=[gpx_folder, device_xml])
    storage = FakeItem("Internal Storage", children=[garmin])
    return storage


def _make_device(name: str = "Garmin eTrex Solar", storage=None):
    """Build an MTPDevice from fake COM objects."""
    if storage is None:
        storage = _make_garmin_tree()
    root_item = FakeItem(name, children=[storage])
    return MTPDevice(root_item, shell=None)


# ── _shell_folder / _find_item helpers ────────────────────────────────────────


class TestHelpers:
    def test_shell_folder_returns_folder(self):
        item = FakeItem("x", children=[])
        assert _shell_folder(item) is not None

    def test_shell_folder_raises_for_file(self):
        item = FakeItem("x", children=None)
        with pytest.raises(MTPError):
            _shell_folder(item)

    def test_find_item_found(self):
        folder = FakeFolder([FakeItem("GPX", children=[])])
        assert _find_item(folder, "gpx") is not None

    def test_find_item_case_insensitive(self):
        folder = FakeFolder([FakeItem("Garmin", children=[])])
        assert _find_item(folder, "GARMIN") is not None
        assert _find_item(folder, "garmin") is not None

    def test_find_item_not_found(self):
        folder = FakeFolder([FakeItem("Other", children=[])])
        assert _find_item(folder, "GARMIN") is None

    def test_has_direct_garmin_true(self):
        folder = FakeFolder([FakeItem("GARMIN", children=[])])
        assert _has_direct_garmin(folder) is True

    def test_has_direct_garmin_false(self):
        folder = FakeFolder([FakeItem("Other", children=[])])
        assert _has_direct_garmin(folder) is False


# ── MTPPath ───────────────────────────────────────────────────────────────────


class TestMTPPath:
    def _device(self):
        return _make_device()

    def test_name(self):
        p = MTPPath(self._device(), ("Garmin", "GPX", "test.gpx"))
        assert p.name == "test.gpx"

    def test_stem(self):
        p = MTPPath(self._device(), ("Garmin", "GPX", "test.gpx"))
        assert p.stem == "test"

    def test_suffix(self):
        p = MTPPath(self._device(), ("Garmin", "GPX", "test.gpx"))
        assert p.suffix == ".gpx"

    def test_parent(self):
        p = MTPPath(self._device(), ("Garmin", "GPX", "test.gpx"))
        parent = p.parent
        assert parent.parts == ("Garmin", "GPX")

    def test_str(self):
        dev = self._device()
        p = MTPPath(dev, ("GARMIN", "GPX"))
        assert dev.name in str(p)
        assert "GARMIN" in str(p)

    def test_truediv(self):
        dev = self._device()
        p = MTPPath(dev, ("GARMIN",))
        child = p / "GPX"
        assert child.parts == ("GARMIN", "GPX")

    def test_exists_true(self):
        dev = self._device()
        p = dev / "Internal Storage" / "GARMIN" / "GPX"
        assert p.exists()

    def test_exists_false(self):
        dev = self._device()
        p = dev / "Internal Storage" / "GARMIN" / "NONEXISTENT"
        assert not p.exists()

    def test_is_dir_true(self):
        dev = self._device()
        p = dev / "Internal Storage" / "GARMIN" / "GPX"
        assert p.is_dir()

    def test_is_dir_false_for_file(self):
        dev = self._device()
        p = dev / "Internal Storage" / "GARMIN" / "GarminDevice.xml"
        assert not p.is_dir()

    def test_is_file_true(self):
        dev = self._device()
        p = dev / "Internal Storage" / "GARMIN" / "GarminDevice.xml"
        assert p.is_file()

    def test_is_file_false_for_dir(self):
        dev = self._device()
        p = dev / "Internal Storage" / "GARMIN" / "GPX"
        assert not p.is_file()

    def test_glob(self):
        dev = self._device()
        gpx_folder = dev._resolve(
            ("Internal Storage", "GARMIN", "GPX")
        )
        # Add some files to the GPX folder
        gpx_com = _shell_folder(gpx_folder)
        gpx_com._items.append(FakeItem("cache1.gpx"))
        gpx_com._items.append(FakeItem("cache2.gpx"))
        gpx_com._items.append(FakeItem("notes.txt"))

        p = dev / "Internal Storage" / "GARMIN" / "GPX"
        matches = p.glob("*.gpx")
        names = [m.name for m in matches]
        assert "cache1.gpx" in names
        assert "cache2.gpx" in names
        assert "notes.txt" not in names

    def test_glob_and_unlink_use_canonical_filename_when_extension_is_hidden(self):
        dev = self._device()
        gpx_folder = dev._folder_for(("Internal Storage", "GARMIN", "GPX"))
        gpx_folder._items.append(FakeItem("Washington", filename="Washington.gpx"))

        path = dev / "Internal Storage" / "GARMIN" / "GPX"
        matches = path.glob("*.gpx")

        assert [match.name for match in matches] == ["Washington.gpx"]
        matches[0].unlink()
        assert matches[0].exists() is False

    def test_write_text(self):
        dev = self._device()
        p = dev / "Internal Storage" / "GARMIN" / "GPX" / "test.gpx"
        p.write_text("<gpx/>")
        # File should now appear in GPX folder
        assert (dev / "Internal Storage" / "GARMIN" / "GPX" / "test.gpx").exists()

    def test_mkdir(self):
        dev = self._device()
        p = dev / "Internal Storage" / "GARMIN" / "GGZ"
        p.mkdir()
        assert (dev / "Internal Storage" / "GARMIN" / "GGZ").is_dir()


# ── MTPDevice ─────────────────────────────────────────────────────────────────


class TestMTPDevice:
    def test_str(self):
        dev = _make_device("Garmin eTrex Solar")
        assert "Garmin eTrex Solar" in str(dev)
        assert "MTP" in str(dev)

    def test_is_dir(self):
        assert _make_device().is_dir()

    def test_truediv(self):
        dev = _make_device()
        p = dev / "GARMIN"
        assert isinstance(p, MTPPath)
        assert p.parts == ("GARMIN",)

    def test_garmin_paths_resolve_storage_layer(self):
        dev = _make_device()
        (dev / "Internal Storage" / "GARMIN" / "GGZ").mkdir()

        assert get_garmin_gpx_path(dev).is_dir()
        assert get_garmin_ggz_path(dev).is_dir()

    def test_resolve_root(self):
        dev = _make_device()
        assert dev._resolve(()) is not None

    def test_resolve_deep(self):
        dev = _make_device()
        item = dev._resolve(("Internal Storage", "GARMIN", "GPX"))
        assert item is not None

    def test_resolve_missing(self):
        dev = _make_device()
        assert dev._resolve(("NONEXISTENT",)) is None

    def test_folder_for(self):
        dev = _make_device()
        folder = dev._folder_for(("Internal Storage", "GARMIN"))
        assert folder is not None

    def test_folder_for_missing(self):
        dev = _make_device()
        assert dev._folder_for(("NONEXISTENT",)) is None

    def test_ensure_folder_existing(self):
        dev = _make_device()
        folder = dev._ensure_folder(("Internal Storage", "GARMIN", "GPX"))
        assert folder is not None

    def test_ensure_folder_creates_new(self):
        dev = _make_device()
        folder = dev._ensure_folder(("Internal Storage", "GARMIN", "GGZ"))
        assert folder is not None
        # Verify it's now navigable
        assert dev._folder_for(("Internal Storage", "GARMIN", "GGZ")) is not None

    def test_write_file_correct_name(self):
        """CopyHere must receive a file named after the target, not a tmp name."""
        dev = _make_device()
        dev.write_file(("Internal Storage", "GARMIN", "GPX", "export.gpx"), b"<gpx/>")
        gpx_folder = dev._folder_for(("Internal Storage", "GARMIN", "GPX"))
        assert _find_item(gpx_folder, "export.gpx") is not None

    def test_write_file_empty_parts_raises(self):
        dev = _make_device()
        with pytest.raises(ValueError):
            dev.write_file((), b"data")

    def test_delete_file(self):
        dev = _make_device()
        # Add a file to delete
        gpx_folder = dev._folder_for(("Internal Storage", "GARMIN", "GPX"))
        target = FakeItem("old.gpx")
        gpx_folder._items.append(target)
        assert _find_item(gpx_folder, "old.gpx") is not None

        dev.delete_file(("Internal Storage", "GARMIN", "GPX", "old.gpx"))
        # InvokeVerb("delete") sets _deleted=True; our FakeFolder.Items() filters those out
        assert _find_item(gpx_folder, "old.gpx") is None

    def test_delete_file_missing_is_noop(self):
        dev = _make_device()
        # Should not raise
        dev.delete_file(("Internal Storage", "GARMIN", "GPX", "nonexistent.gpx"))


# ── Export to a Windows MTP device (#1346) ─────────────────────────────────────


class TestExportToWindowsMtp:
    """Exports to a Windows MTP device failed with "'MTPPath' object has no
    attribute 'with_name'" once they streamed into a .part file (#1346)."""

    def _file(self, dev, *parts):
        item = dev._resolve(("Internal Storage", "GARMIN") + parts)
        assert item is not None, "/".join(parts)
        return item

    def test_write_export_copies_finished_file_to_device(self):
        dev = _make_device()
        target = dev / "Internal Storage" / "GARMIN" / "GPX" / "opensak.gpx"

        result = write_export(target, lambda f: f.write("<gpx/>") and 7)

        assert result == 7
        assert self._file(dev, "GPX", "opensak.gpx").data == b"<gpx/>"

    def test_write_export_binary(self):
        dev = _make_device()
        target = dev / "Internal Storage" / "GARMIN" / "GPX" / "x.bin"
        write_export(target, lambda f: f.write(b"\x00\x01"), binary=True)
        assert self._file(dev, "GPX", "x.bin").data == b"\x00\x01"

    def test_write_export_failure_leaves_device_file_alone(self):
        dev = _make_device()
        target = dev / "Internal Storage" / "GARMIN" / "GPX" / "opensak.gpx"
        target.write_bytes(b"old")

        def fail(f):
            f.write("partial")
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            write_export(target, fail)
        assert self._file(dev, "GPX", "opensak.gpx").data == b"old"

    def test_export_to_device_writes_gpx(self):
        dev = _make_device()
        result = export_to_device([], dev, "opensak")
        assert result.success, result.error
        assert b"<gpx" in self._file(dev, "GPX", "opensak.gpx").data

    def test_export_ggz_to_device_creates_ggz_folder(self):
        dev = _make_device()
        result = export_ggz_to_device([], dev, "opensak")
        assert result.success, result.error
        assert self._file(dev, "GGZ", "opensak.ggz").data[:2] == b"PK"

    def test_move_file_requires_target_name(self, tmp_path):
        dev = _make_device()
        source = tmp_path / "other.gpx"
        source.write_bytes(b"x")
        with pytest.raises(ValueError):
            dev.move_file(("Internal Storage", "GARMIN", "GPX", "opensak.gpx"), source)


class TestMoveFileWaitsForAsyncCopy:
    """CopyHere is asynchronous. Overwriting an existing file returned at
    once, because the old file was still listed, and the temporary source
    was then deleted while the copy still read it (#1346 review)."""

    PARTS = ("Internal Storage", "GARMIN", "GPX", "opensak.gpx")

    def _device(self, monkeypatch, **kwargs):
        dev = _make_device()
        gpx = dev._resolve(self.PARTS[:-1])
        folder = AsyncCopyFolder([FakeItem("opensak.gpx", data=b"old")], **kwargs)
        gpx._folder = folder
        monkeypatch.setattr("opensak.gps.mtp.time.sleep", folder.tick)
        real_unlink = Path.unlink

        def unlink(path, missing_ok=False):
            if folder.holds(path):
                raise PermissionError(f"{path} is in use")
            real_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", unlink)
        return dev, folder

    def _source(self, tmp_path, data=b"<gpx>new</gpx>"):
        source = tmp_path / "opensak.gpx"
        source.write_bytes(data)
        return source

    def test_overwrite_waits_until_the_copy_is_complete(self, tmp_path, monkeypatch):
        dev, folder = self._device(monkeypatch)
        source = self._source(tmp_path)

        dev.move_file(self.PARTS, source)

        assert folder.pending is None
        assert _find_item(folder, "opensak.gpx").data == b"<gpx>new</gpx>"
        assert not source.exists()

    def test_old_file_is_deleted_before_copying(self, tmp_path, monkeypatch):
        dev, folder = self._device(monkeypatch)
        old = _find_item(folder, "opensak.gpx")

        dev.move_file(self.PARTS, self._source(tmp_path))

        assert old._deleted

    def test_waits_while_source_is_still_open(self, tmp_path, monkeypatch):
        # The device lists the file at full size before the copy engine
        # closes the source: wait for that too.
        dev, folder = self._device(monkeypatch, steps=1)
        source = self._source(tmp_path)
        released = []
        original = folder.holds

        def holds(path):
            if len(released) < 3:
                released.append(path)
                return True
            return original(path)

        folder.holds = holds
        folder.keep_open = False

        dev.move_file(self.PARTS, source)

        assert len(released) == 3
        assert not source.exists()

    def test_times_out_when_source_is_never_released(self, tmp_path, monkeypatch):
        dev, folder = self._device(monkeypatch, keep_open=True)
        monkeypatch.setattr("opensak.gps.mtp._COPY_TIMEOUT", 0.2)

        with pytest.raises(MTPError):
            dev.move_file(self.PARTS, self._source(tmp_path))

    def test_timeout_grows_with_file_size(self):
        # A large GGZ copied at a slow device's rate must not time out (#1346).
        assert _copy_timeout(0) == 300
        assert _copy_timeout(512 * 1024 * 1024) == 300 + 2048

    def test_large_file_gets_more_time(self, tmp_path, monkeypatch):
        dev, folder = self._device(monkeypatch, keep_open=True)
        clock = [0.0]
        monkeypatch.setattr("opensak.gps.mtp.time.monotonic", lambda: clock[0])

        def tick(_seconds=0):
            folder.tick()
            clock[0] += 100
            if clock[0] >= 600:  # past the fixed 300 s: release the source
                folder.pending = None

        monkeypatch.setattr("opensak.gps.mtp.time.sleep", tick)
        monkeypatch.setattr("opensak.gps.mtp._copy_timeout", lambda size: 1000)

        dev.move_file(self.PARTS, self._source(tmp_path))

        assert clock[0] >= 600

    def test_write_export_overwrites_existing_file(self, monkeypatch):
        dev, folder = self._device(monkeypatch)
        target = dev / "Internal Storage" / "GARMIN" / "GPX" / "opensak.gpx"

        write_export(target, lambda f: f.write("<gpx>new</gpx>"), binary=False)

        data = _find_item(folder, "opensak.gpx").data
        assert data.replace(b"\r\n", b"\n") == b"<gpx>new</gpx>"


# ── find_mtp_devices ─────────────────────────────────────────────────────────


class TestFindMtpDevices:
    def test_returns_empty_on_non_windows(self, monkeypatch):
        monkeypatch.setattr("opensak.gps.mtp.platform.system", lambda: "Linux")
        assert find_mtp_devices() == []

    def test_skips_drive_letters(self, monkeypatch):
        """Regular drives (C:\\, D:\\) must not produce MTPDevice entries."""
        monkeypatch.setattr("opensak.gps.mtp.platform.system", lambda: "Windows")

        c_drive = FakeItem("Local Disk (C:)", children=[], path="C:\\")
        garmin_usb = FakeItem("GARMIN (E:)", children=[_make_garmin_tree()], path="E:\\")
        garmin_mtp = FakeItem(
            "Garmin eTrex Solar",
            children=[_make_garmin_tree()],
            path="\\\\?\\usb#vid_091e&pid_506a",
        )

        class FakeNamespace:
            def Items(self):
                return [c_drive, garmin_usb, garmin_mtp]

        class FakeShell:
            def Namespace(self, csidl):
                return FakeNamespace()

        # Inject a fake win32com.client module so the lazy import succeeds
        import types, sys
        fake_client = types.ModuleType("win32com.client")
        fake_client.Dispatch = lambda prog_id: FakeShell()
        fake_win32com = types.ModuleType("win32com")
        fake_win32com.client = fake_client
        monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
        monkeypatch.setitem(sys.modules, "win32com.client", fake_client)

        devices = find_mtp_devices()
        names = [d.name for d in devices]
        # Only one device should be found — the MTP Garmin's storage root.
        # Drive-letter items (C:\, E:\) must be skipped.
        assert len(devices) == 1
        # The mass-storage Garmin on E:\ must NOT appear as MTP
        assert "GARMIN (E:)" not in names
        assert "Local Disk (C:)" not in names
        # The found device should have a reachable GARMIN folder
        assert devices[0]._folder_for(("GARMIN",)) is not None

    def test_handles_import_error(self, monkeypatch):
        monkeypatch.setattr("opensak.gps.mtp.platform.system", lambda: "Windows")
        import sys
        # Remove win32com so the import inside find_mtp_devices fails
        monkeypatch.delitem(sys.modules, "win32com", raising=False)
        monkeypatch.delitem(sys.modules, "win32com.client", raising=False)
        # Ensure the import will fail by adding a broken entry
        import types
        broken = types.ModuleType("win32com")
        broken.client = property(lambda self: (_ for _ in ()).throw(ImportError("no pywin32")))
        monkeypatch.setitem(sys.modules, "win32com", broken)
        assert find_mtp_devices() == []
