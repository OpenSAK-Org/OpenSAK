# tests/unit-tests/test_garmin_mounts_1094.py — issue #1094, item 3.
#
# lsblk --raw escapes spaces and other characters in a mount point as \xHH,
# and /proc/mounts escapes them as octal (\040). The Linux GPS detection
# compared those escaped strings with real paths, so a mount point with a
# space outside /media, /run/media and /mnt was never found.

import sys
import subprocess
from pathlib import Path
from unittest.mock import mock_open

import pytest

from opensak.gps import garmin
from opensak.gps.garmin import _unescape_mount


class TestUnescapeMount:
    def test_plain_path_is_unchanged(self):
        assert _unescape_mount("/media/allan/GARMIN") == "/media/allan/GARMIN"

    def test_lsblk_hex_space(self):
        assert _unescape_mount(r"/srv/My\x20GPS") == "/srv/My GPS"

    def test_proc_mounts_octal_space_and_tab(self):
        assert _unescape_mount(r"/srv/My\040GPS\011x") == "/srv/My GPS\tx"

    def test_backslash_itself(self):
        assert _unescape_mount(r"/srv/a\134b") == "/srv/a\\b"
        assert _unescape_mount(r"/srv/a\x5cb") == "/srv/a\\b"

    def test_multibyte_utf8_letter(self):
        # "æ" is two bytes in UTF-8, so lsblk writes two escapes.
        assert _unescape_mount(r"/srv/K\xc3\xa6lder") == "/srv/Kælder"

    def test_text_that_only_looks_like_an_escape_is_kept(self):
        assert _unescape_mount(r"/srv/a\9b") == r"/srv/a\9b"


linux_only = pytest.mark.skipif(sys.platform == "win32",
                                reason="Linux mount points are POSIX paths")


@linux_only
class TestLinuxMounts:
    MOUNT = "/srv/My GPS"

    @pytest.fixture
    def fake_system(self, monkeypatch):
        """lsblk and /proc/mounts both report self.MOUNT in escaped form."""
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if "MOUNTPOINT,RM" in args:
                out = "/ 0\n/srv/My\\x20GPS 1\n"
            else:
                out = "/\n/srv/My\\x20GPS\n"
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

        mount = self.MOUNT
        real_is_dir = Path.is_dir
        monkeypatch.setattr(garmin.subprocess, "run", fake_run)
        monkeypatch.setattr(
            Path, "is_dir", lambda p: str(p) == mount or real_is_dir(p),
        )
        return calls

    def test_is_removable_matches_escaped_lsblk_line(self, fake_system):
        assert garmin._is_removable_path(Path(self.MOUNT))

    def test_lsblk_mount_with_space_is_found(self, fake_system, monkeypatch):
        monkeypatch.setattr("builtins.open", mock_open(read_data=""))
        assert Path(self.MOUNT) in garmin._linux_mounts()

    def test_proc_mounts_mount_with_space_is_found(self, monkeypatch):
        def no_lsblk_mounts(args, **kwargs):
            if "MOUNTPOINT,RM" in args:
                out = "/srv/Other\\x20GPS 1\n"
            else:
                out = ""
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

        real_is_dir = Path.is_dir
        monkeypatch.setattr(garmin.subprocess, "run", no_lsblk_mounts)
        monkeypatch.setattr(
            Path, "is_dir",
            lambda self: str(self) == "/srv/Other GPS" or real_is_dir(self),
        )
        proc = "/dev/sdb1 /srv/Other\\040GPS vfat rw,nosuid 0 0\n"
        monkeypatch.setattr("builtins.open", mock_open(read_data=proc))
        assert Path("/srv/Other GPS") in garmin._linux_mounts()
