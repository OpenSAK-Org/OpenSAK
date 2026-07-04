# tests/unit-tests/test_bump_version_script.py — scripts/bump_version.py
#
# Regression coverage for the class of bug that broke v1.14.0-beta.15 and
# v1.14.0-beta.16: __init__.py gets bumped, site/user-guide.html doesn't, CI
# catches it only after the tag is already pushed. These tests run against
# throwaway files in tmp_path — never the real repo's __init__.py or
# user-guide.html.

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "bump_version.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bump_version", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bv(tmp_path, monkeypatch):
    """A freshly loaded copy of the script, pointed at throwaway
    __init__.py / user-guide.html files instead of the real repo."""
    module = _load_module()

    init_py = tmp_path / "__init__.py"
    init_py.write_text(
        '__version__ = "1.0.0-beta.1"\n__author__ = "OpenSAK Contributors"\n',
        encoding="utf-8",
    )

    user_guide = tmp_path / "user-guide.html"
    user_guide.write_text(
        "<title>OpenSAK User Guide — v1.0.0-beta.1</title>\n"
        "<div>Version 1.0.0-beta.1</div>\n"
        "<a href='blob/v1.0.0-beta.1/CHANGELOG.md'>changelog</a>\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "INIT_PY", init_py)
    monkeypatch.setattr(module, "USER_GUIDE", user_guide)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


# ── version read/write helpers ──────────────────────────────────────────────

class TestVersionHelpers:
    def test_get_init_version(self, bv):
        assert bv.get_init_version() == "1.0.0-beta.1"

    def test_set_init_version_updates_only_the_version_line(self, bv):
        bv.set_init_version("1.0.0-beta.2")
        text = bv.INIT_PY.read_text(encoding="utf-8")
        assert '__version__ = "1.0.0-beta.2"' in text
        assert '__author__ = "OpenSAK Contributors"' in text  # untouched


class TestBumpUserGuide:
    def test_replaces_all_occurrences(self, bv):
        count = bv.bump_user_guide("1.0.0-beta.1", "1.0.0-beta.2")
        assert count == 3
        text = bv.USER_GUIDE.read_text(encoding="utf-8")
        assert "1.0.0-beta.1" not in text
        assert text.count("1.0.0-beta.2") == 3

    def test_no_occurrences_found_makes_no_changes(self, bv):
        count = bv.bump_user_guide("9.9.9", "1.0.0-beta.2")
        assert count == 0
        assert "1.0.0-beta.1" in bv.USER_GUIDE.read_text(encoding="utf-8")


# ── --check (consistency probe, no writes) ──────────────────────────────────

class TestCheckConsistency:
    def test_consistent_state_returns_true(self, bv, capsys):
        assert bv.check_consistency() is True
        assert "OK" in capsys.readouterr().out

    def test_detects_the_exact_beta15_beta16_bug(self, bv, capsys):
        # __init__.py bumped, user-guide.html left pointing at the old version.
        bv.set_init_version("1.0.0-beta.2")
        assert bv.check_consistency() is False
        out = capsys.readouterr().out
        assert "STALE" in out
        assert "1.0.0-beta.1" in out  # names the stale version it found

    def test_detects_version_missing_entirely(self, bv):
        bv.USER_GUIDE.write_text("<p>no version string here</p>", encoding="utf-8")
        assert bv.check_consistency() is False

    def test_check_never_writes_to_either_file(self, bv):
        before_init = bv.INIT_PY.read_text(encoding="utf-8")
        before_guide = bv.USER_GUIDE.read_text(encoding="utf-8")
        bv.set_init_version("1.0.0-beta.2")
        bv.check_consistency()
        # check_consistency() must not have "fixed" anything itself
        assert bv.INIT_PY.read_text(encoding="utf-8") != before_init  # our setup change only
        assert bv.USER_GUIDE.read_text(encoding="utf-8") == before_guide


# ── CLI entry point ─────────────────────────────────────────────────────────

class TestMainCLI:
    def test_bumps_both_files_in_one_call(self, bv, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "1.0.0-beta.2"])
        bv.main()
        assert bv.get_init_version() == "1.0.0-beta.2"
        assert bv.USER_GUIDE.read_text(encoding="utf-8").count("1.0.0-beta.2") == 3
        assert bv.check_consistency() is True

    def test_strips_leading_v(self, bv, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "v1.0.0-beta.2"])
        bv.main()
        assert bv.get_init_version() == "1.0.0-beta.2"

    def test_rejects_malformed_version_and_touches_nothing(self, bv, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "not-a-version"])
        with pytest.raises(SystemExit) as exc:
            bv.main()
        assert exc.value.code == 1
        assert bv.get_init_version() == "1.0.0-beta.1"  # unchanged

    def test_already_at_target_version_is_a_noop(self, bv, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "1.0.0-beta.1"])
        with pytest.raises(SystemExit) as exc:
            bv.main()
        assert exc.value.code == 0
        assert "Already at" in capsys.readouterr().out

    def test_manually_bumped_init_still_fixes_stale_user_guide(self, bv, monkeypatch, capsys):
        # Regression: this is exactly what happened releasing v1.15.0-beta.4
        # — a permission hiccup led to __init__.py being edited by hand
        # instead of via this script, so old_version == new_version from the
        # script's point of view, but user-guide.html was never touched and
        # still pointed at the previous release. The early "nothing to do"
        # exit must not skip fixing that.
        bv.set_init_version("1.0.0-beta.2")  # simulates the manual edit
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "1.0.0-beta.2"])
        with pytest.raises(SystemExit) as exc:
            bv.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "stale version" in out
        assert "1.0.0-beta.1" in out
        text = bv.USER_GUIDE.read_text(encoding="utf-8")
        assert "1.0.0-beta.1" not in text
        assert text.count("1.0.0-beta.2") == 3
        assert bv.check_consistency() is True

    def test_noop_stays_a_noop_when_user_guide_has_no_stale_version(self, bv, monkeypatch, capsys):
        # If __init__.py is already at the target AND user-guide.html is
        # already fully consistent (no stale version at all), nothing
        # should be rewritten and the plain "nothing to do" message stays.
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "1.0.0-beta.1"])
        before = bv.USER_GUIDE.read_text(encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            bv.main()
        assert exc.value.code == 0
        assert "Already at" in capsys.readouterr().out
        assert bv.USER_GUIDE.read_text(encoding="utf-8") == before

    def test_no_args_prints_usage_and_exits_nonzero(self, bv, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["bump_version.py"])
        with pytest.raises(SystemExit) as exc:
            bv.main()
        assert exc.value.code == 1

    def test_check_flag_exits_zero_when_consistent(self, bv, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "--check"])
        with pytest.raises(SystemExit) as exc:
            bv.main()
        assert exc.value.code == 0

    def test_check_flag_exits_nonzero_when_stale(self, bv, monkeypatch):
        bv.set_init_version("1.0.0-beta.2")
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "--check"])
        with pytest.raises(SystemExit) as exc:
            bv.main()
        assert exc.value.code == 1
