# tests/unit-tests/test_dialog_height_policy.py
#
# Issue #811: the Settings dialog exceeded the screen's available height
# at 200% DPI scaling on a high-resolution display (Microsoft Store
# certification, 31 Aug 2026), cutting off content with no way to reach
# it. An audit found this wasn't unique to Settings — of 23 dialog files
# in this package, only filter_dialog.py (issue #580) had any protection
# against it at all.
#
# This test doesn't re-fix every dialog (that's tracked incrementally,
# see KNOWN_UNPROTECTED below). What it does is make the *gap* visible
# and durable: every dialog file must either show evidence of protection,
# or be explicitly and consciously listed as a known gap. A new dialog
# added without either is a CI failure, not a silent omission — and a
# protected dialog that later loses its protection (e.g. someone strips
# the clamp call during a refactor) fails the same way.
#
# This is a static/source-scan test, not a real-widget one: it never
# instantiates Qt, so it runs anywhere pytest does, with no GUI/display
# requirements — unlike most of this package's other dialog tests, which
# need qtbot and a real (or virtual) display.

from __future__ import annotations

import re
from pathlib import Path

DIALOGS_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "opensak" / "gui" / "dialogs"

# Files in this package that aren't dialogs themselves — nothing to check.
_NOT_A_DIALOG_FILE = {"__init__.py", "widgets.py"}

# A file counts as "protected" if it shows evidence of constraining
# itself to the available screen — either via the shared helper
# (clamp_dialog_height_to_screen, see widgets.py) or via its own
# equivalent screen-geometry logic (filter_dialog.py predates the shared
# helper and does its own thing; that's fine, same effect).
#
# Matched as a call site (name followed by "("), not just any mention —
# an unused import still containing the name (e.g. after someone strips
# the actual call but forgets the import) must NOT count as protected.
_PROTECTION_MARKERS = (
    r"clamp_dialog_height_to_screen\s*\(",
    r"availableGeometry\s*\(",
)

# Dialogs known NOT to have this protection yet, and why it's judged
# acceptable for now. Every entry here is conscious tracked debt, not an
# oversight — remove a line once its dialog is fixed. Filed as follow-ups
# to #811; update with per-dialog issue numbers as they're created.
#
# The five highest-content dialogs (trip_dialog.py, column_dialog.py,
# welcome_wizard.py, import_dialog.py, gps_dialog.py) were fixed directly
# alongside settings_dialog.py rather than listed here — see #811. The
# remaining 13 follow-ups were fixed in #816, so the list is empty: keep it
# that way unless a gap is a conscious, tracked decision.
KNOWN_UNPROTECTED: dict[str, str] = {}


def _strip_comments(source: str) -> str:
    """
    Naive '#'-to-end-of-line stripper. Good enough here: this codebase
    doesn't have '#' inside string literals in these files, and the only
    thing at stake is whether a *mention* of a protection marker's name
    in a comment (e.g. "see clamp_dialog_height_to_screen()'s docstring")
    gets mistaken for a real call site — a full tokenizer would be
    overkill for that.
    """
    return "\n".join(line.split("#", 1)[0] for line in source.splitlines())


def _is_protected(source: str) -> bool:
    code_only = _strip_comments(source)
    return any(re.search(marker, code_only) for marker in _PROTECTION_MARKERS)


def _defines_a_qdialog_subclass(source: str) -> bool:
    """
    True if the file defines at least one class inheriting from QDialog,
    directly or via a mixin (e.g. "class Foo(_PreviewMixin, QDialog):").
    Deliberately a simple substring/regex check, not an AST/import-based
    one: importing every dialog module just to introspect its classes
    would pull in the full PySide6/DB/settings stack for a check that's
    really just "does this file's source mention QDialog as a base".
    """
    return bool(re.search(r"class\s+\w+\([^)]*\bQDialog\b[^)]*\)\s*:", source))


def _all_dialog_files() -> list[Path]:
    return sorted(
        p for p in DIALOGS_DIR.glob("*.py") if p.name not in _NOT_A_DIALOG_FILE
    )


class TestDialogHeightPolicy:
    def test_every_dialog_file_is_protected_or_explicitly_tracked(self):
        unprotected_and_untracked = []

        for path in _all_dialog_files():
            source = path.read_text(encoding="utf-8")
            if not _defines_a_qdialog_subclass(source):
                continue  # not a dialog file (e.g. a dataclass-only module)

            is_protected = _is_protected(source)
            is_tracked = path.name in KNOWN_UNPROTECTED

            if not is_protected and not is_tracked:
                unprotected_and_untracked.append(path.name)

        assert not unprotected_and_untracked, (
            "These dialog(s) have no screen-height protection (#811) and "
            "aren't in KNOWN_UNPROTECTED either: "
            f"{unprotected_and_untracked}. Either add "
            "clamp_dialog_height_to_screen() (see widgets.py) to the "
            "dialog, or add it to KNOWN_UNPROTECTED with a reason if "
            "this is deliberately deferred."
        )

    def test_known_unprotected_entries_still_exist_and_are_still_unprotected(self):
        """
        Catches two kinds of drift in the other direction: a stale
        allowlist entry for a file that's been renamed/removed, and an
        entry that's actually already fixed (protection added) but not
        removed from the list — both mean KNOWN_UNPROTECTED no longer
        reflects reality and should be cleaned up.
        """
        existing_names = {p.name for p in _all_dialog_files()}

        stale_entries = [name for name in KNOWN_UNPROTECTED if name not in existing_names]
        assert not stale_entries, (
            f"KNOWN_UNPROTECTED references file(s) that no longer exist: "
            f"{stale_entries}. Remove the stale entries."
        )

        now_fixed = []
        for name in KNOWN_UNPROTECTED:
            source = (DIALOGS_DIR / name).read_text(encoding="utf-8")
            if _is_protected(source):
                now_fixed.append(name)
        assert not now_fixed, (
            f"These KNOWN_UNPROTECTED dialog(s) already have protection "
            f"now — remove them from the allowlist: {now_fixed}"
        )

    def test_known_unprotected_list_is_empty(self):
        """
        Every dialog is protected since #816. This catches a *new* dialog
        silently added straight into the allowlist instead of being
        protected from the start — a dialog fixed without being removed
        from the list is already caught above.
        """
        assert not KNOWN_UNPROTECTED, (
            f"KNOWN_UNPROTECTED has entries again: {sorted(KNOWN_UNPROTECTED)}. "
            "Protect the dialog with clamp_dialog_height_to_screen() instead "
            "of adding it to the allowlist, unless that gap is a conscious, "
            "tracked decision (then change this test too)."
        )
