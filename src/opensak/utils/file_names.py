"""
src/opensak/utils/file_names.py — Windows names that are not plain files.

Windows treats some file names as devices, whatever folder they are in and
whatever extension they have: "COM3", "COM3.gpx" and "nul .txt" all open a
device, not a file. Writing to "COM3" sends the bytes to a serial port, and
GPS receivers are serial devices. Both the macro sandbox
(opensak.macro.permissions) and the export file names
(opensak.export.file_export_settings.expand_file_name) use the check here.
It has no dependencies, so either side can import it.
"""

from __future__ import annotations

# CON, PRN, AUX, NUL, COM0–COM9 and LPT0–LPT9 (also with the superscript
# digits ¹ ² ³, which Windows treats as 1 2 3), and the console devices.
RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {
        f"{prefix}{digit}"
        for prefix in ("COM", "LPT")
        for digit in "0123456789¹²³"
    }
)


def is_reserved_device_name(name: str) -> bool:
    """True if Windows opens a device for the file name *name*.

    The extension does not matter ("COM3.gpx"), and neither do trailing dots
    or spaces ("NUL.", "con .txt"). Case does not matter either.
    """
    base = name.rstrip(". ").partition(".")[0].rstrip(" ")
    return base.upper() in RESERVED_DEVICE_NAMES
