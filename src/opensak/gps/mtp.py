"""Windows Portable Device (MTP) support used by Garmin export.

The Windows Shell exposes MTP storage through the same ``Folder`` automation
API used by File Explorer, but it does not assign the device a drive letter.
This small adapter keeps that API detail out of the GPX/GGZ exporter.

The module is intentionally importable on non-Windows systems; the optional
pywin32 dependency is only required when an MTP scan is requested on Windows.
"""

from __future__ import annotations

import fnmatch
import os
import platform
import re
import tempfile
import time
from pathlib import Path
from typing import Any


class MTPError(OSError):
    """Raised when a Windows portable device cannot be accessed."""


def _shell_folder(item: Any) -> Any:
    try:
        folder = getattr(item, "GetFolder", None)
    except Exception as error:
        raise MTPError("The portable device item is not a folder") from error
    if folder is None:
        raise MTPError("The portable device does not expose a storage folder")
    return folder


def _item_filename(item: Any) -> str:
    """Return an item's real filename rather than its extensionless display name."""
    extended_property = getattr(item, "ExtendedProperty", None)
    if extended_property is not None:
        for property_name in ("System.FileName", "System.ItemName"):
            try:
                value = extended_property(property_name)
                if value:
                    return str(value)
            except Exception:
                pass

        try:
            extension = extended_property("System.FileExtension")
            display_name = str(item.Name)
            if extension and not display_name.casefold().endswith(str(extension).casefold()):
                return display_name + str(extension)
        except Exception:
            pass

    return str(item.Name)


def _find_item(folder: Any, name: str) -> Any | None:
    wanted = name.casefold()
    for item in folder.Items():
        if _item_filename(item).casefold() == wanted:
            return item
    return None


class MTPPath:
    """Path-like reference to a file or folder on an :class:`MTPDevice`."""

    def __init__(self, device: "MTPDevice", parts: tuple[str, ...]):
        self.device = device
        self.parts = parts

    @property
    def name(self) -> str:
        return self.parts[-1] if self.parts else self.device.name

    @property
    def stem(self) -> str:
        return Path(self.name).stem

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix

    @property
    def parent(self) -> "MTPPath":
        return MTPPath(self.device, self.parts[:-1])

    def __str__(self) -> str:
        return f"{self.device.name}:\\" + "\\".join(self.parts)

    def __truediv__(self, other: str | Path) -> "MTPPath":
        return MTPPath(self.device, self.parts + tuple(Path(other).parts))

    def exists(self) -> bool:
        return self.device._resolve(self.parts) is not None

    def is_dir(self) -> bool:
        item = self.device._resolve(self.parts)
        if item is None:
            return False
        try:
            _shell_folder(item)
            return True
        except MTPError:
            return False

    def is_file(self) -> bool:
        return self.exists() and not self.is_dir()

    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        self.device._ensure_folder(self.parts)

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        self.write_bytes(data.encode(encoding))

    def write_bytes(self, data: bytes) -> None:
        self.device.write_file(self.parts, data)

    def unlink(self) -> None:
        self.device.delete_file(self.parts)

    def glob(self, pattern: str) -> list["MTPPath"]:
        folder = self.device._folder_for(self.parts)
        if folder is None:
            return []
        return [
            MTPPath(self.device, self.parts + (_item_filename(item),))
            for item in folder.Items()
            if fnmatch.fnmatchcase(_item_filename(item).casefold(), pattern.casefold())
        ]


class MTPDevice:
    """A Garmin storage root exposed through Windows MTP/Shell automation."""

    def __init__(self, item: Any, shell: Any):
        self._item = item
        self._shell = shell
        self._root_folder = _shell_folder(item)
        self.name = str(item.Name)

    def __str__(self) -> str:
        return f"{self.name} (MTP)"

    def __truediv__(self, other: str | Path) -> MTPPath:
        return MTPPath(self, tuple(Path(other).parts))

    def is_dir(self) -> bool:
        return True

    def _folder_for(self, parts: tuple[str, ...]) -> Any | None:
        folder = self._root_folder
        for part in parts:
            item = _find_item(folder, part)
            if item is None:
                return None
            try:
                folder = _shell_folder(item)
            except MTPError:
                return None
        return folder

    def _resolve(self, parts: tuple[str, ...]) -> Any | None:
        if not parts:
            return self._item
        folder = self._root_folder
        item = None
        for part in parts:
            item = _find_item(folder, part)
            if item is None:
                return None
            try:
                folder = _shell_folder(item)
            except MTPError:
                if part != parts[-1]:
                    return None
        return item

    def _ensure_folder(self, parts: tuple[str, ...]) -> Any:
        folder = self._root_folder
        for part in parts:
            item = _find_item(folder, part)
            if item is None:
                folder.NewFolder(part)
                deadline = time.monotonic() + 10
                while item is None and time.monotonic() < deadline:
                    item = _find_item(folder, part)
                    if item is None:
                        time.sleep(0.1)
                if item is None:
                    raise MTPError(f"Could not create MTP folder: {part}")
            folder = _shell_folder(item)
        return folder

    def write_file(self, parts: tuple[str, ...], data: bytes) -> None:
        if not parts:
            raise ValueError("An MTP file path is required")
        folder = self._ensure_folder(parts[:-1])
        filename = parts[-1]
        # CopyHere preserves the source filename, so the local file must
        # have the target name.  Use a temp *directory* + the real name.
        tmpdir = Path(tempfile.mkdtemp())
        local_path = tmpdir / filename
        local_path.write_bytes(data)
        try:
            folder.CopyHere(str(local_path), 16 | 4 | 1024)
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if _find_item(folder, filename) is not None:
                    return
                time.sleep(0.2)
            raise MTPError(f"Timed out copying {filename} to {self.name}")
        finally:
            local_path.unlink(missing_ok=True)
            tmpdir.rmdir()

    def delete_file(self, parts: tuple[str, ...]) -> None:
        item = self._resolve(parts)
        if item is None:
            return
        invoke = getattr(item, "InvokeVerb", None)
        if invoke is None:
            raise MTPError("The MTP device does not support file deletion")
        name = parts[-1]
        parent_folder = self._folder_for(parts[:-1]) if len(parts) > 1 else self._root_folder
        invoke("delete")
        # Verify the file was actually removed.
        if parent_folder is not None:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if _find_item(parent_folder, name) is None:
                    return
                time.sleep(0.2)
            raise MTPError(f"Failed to delete {name} from {self.name}")


_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:\\")


def find_mtp_devices() -> list[MTPDevice]:
    """Return Garmin MTP devices visible in Windows' This PC namespace."""
    if platform.system() != "Windows":
        return []
    try:
        import win32com.client  # type: ignore[import-not-found]
        shell = win32com.client.Dispatch("Shell.Application")
        drives = shell.Namespace(17)  # CSIDL_DRIVES / This PC
        devices: list[MTPDevice] = []
        for item in drives.Items():
            try:
                # Skip regular drive letters — they are handled by
                # _windows_drives() and would cause duplicate detection.
                item_path = str(getattr(item, "Path", ""))
                if _DRIVE_LETTER_RE.match(item_path):
                    continue
                root = MTPDevice(item, shell)
                # File Explorer commonly exposes a portable device first and
                # its "Internal Storage" as a child. Garmin is stored under
                # that child, so expose the storage node as the export root.
                if _has_direct_garmin(root._root_folder):
                    devices.append(root)
                else:
                    for child in root._root_folder.Items():
                        try:
                            child_folder = _shell_folder(child)
                            if _has_direct_garmin(child_folder):
                                devices.append(MTPDevice(child, shell))
                        except MTPError:
                            continue
            except Exception:
                continue
        return devices
    except Exception:
        return []


def _has_direct_garmin(folder: Any) -> bool:
    for item in folder.Items():
        if str(item.Name).casefold() == "garmin":
            return True
    return False
