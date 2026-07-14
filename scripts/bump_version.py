#!/usr/bin/env python3
"""
scripts/bump_version.py

Bump OpenSAK's version everywhere it's hardcoded, in one atomic step.

Why this exists: src/opensak/__init__.py is the single source of truth for
__version__, but site/user-guide.html is static HTML with no build step, so
it hardcodes the same version string in five separate places (title, nav,
hero, changelog link, footer). v1.14.0-beta.15 and v1.14.0-beta.16 both
shipped a tag where __init__.py had been bumped but user-guide.html still
pointed at the previous release — caught only by
test_user_guide_changelog_link_pins_to_release_tag in CI, after the tag was
already pushed. Twice.

This script makes "bump the version" one command instead of "remember to
edit __init__.py, AND remember to also edit user-guide.html, AND remember
which five lines in user-guide.html".

Usage:
    python scripts/bump_version.py 1.14.0-beta.18
    python scripts/bump_version.py v1.14.0-beta.18      # leading 'v' is fine
    python scripts/bump_version.py --check              # verify consistency, change nothing

It does NOT touch CHANGELOG.md — that needs an actual prose entry describing
what changed in the release, which stays a manual (or Claude-drafted) step.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT_PY = ROOT / "src" / "opensak" / "__init__.py"
USER_GUIDE = ROOT / "site" / "user-guide.html"

# Matches e.g. "1.14.0", "1.14.0-beta.18", "2.0.0-rc.1"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z]+\.\d+)?$")

_INIT_VERSION_RE = re.compile(r'__version__ = "([^"]+)"')


def get_init_version() -> str:
    """Return the version currently set in src/opensak/__init__.py."""
    text = INIT_PY.read_text(encoding="utf-8")
    match = _INIT_VERSION_RE.search(text)
    if not match:
        raise RuntimeError(f"Could not find __version__ in {INIT_PY}")
    return match.group(1)


def set_init_version(new_version: str) -> None:
    """Write new_version into src/opensak/__init__.py."""
    text = INIT_PY.read_text(encoding="utf-8")
    new_text, n = _INIT_VERSION_RE.subn(f'__version__ = "{new_version}"', text, count=1)
    if n == 0:
        raise RuntimeError(f"Could not find __version__ in {INIT_PY}")
    INIT_PY.write_text(new_text, encoding="utf-8")


def bump_user_guide(old_version: str, new_version: str) -> int:
    """Replace every occurrence of old_version with new_version in
    site/user-guide.html. Returns the number of occurrences replaced."""
    text = USER_GUIDE.read_text(encoding="utf-8")
    count = text.count(old_version)
    if count:
        USER_GUIDE.write_text(text.replace(old_version, new_version), encoding="utf-8")
    return count


def check_consistency() -> bool:
    """Verify user-guide.html has no stale version strings left over from a
    previous release. Returns True if consistent, prints details either way.
    Does not modify anything."""
    current = get_init_version()
    text = USER_GUIDE.read_text(encoding="utf-8")
    current_count = text.count(current)
    # Anything that looks like a version number but isn't the current one
    stale = sorted(set(re.findall(r"\d+\.\d+\.\d+(?:-[a-zA-Z]+\.\d+)?", text)) - {current})

    print(f"__init__.py version: {current}")
    print(f"  found {current_count}x in site/user-guide.html (expected 5)")
    if stale:
        print(f"  STALE version string(s) also found: {', '.join(stale)}")
        return False
    if current_count == 0:
        print("  WARNING: current version not found at all — check manually")
        return False
    print("  OK — consistent")
    return True


def find_stale_user_guide_version(current: str) -> str | None:
    """Return the single stale version string in user-guide.html that isn't
    `current`, or None if there isn't exactly one (i.e. none, or several —
    too ambiguous to auto-fix safely). Shares its detection logic with
    check_consistency() above."""
    text = USER_GUIDE.read_text(encoding="utf-8")
    stale = sorted(set(re.findall(r"\d+\.\d+\.\d+(?:-[a-zA-Z]+\.\d+)?", text)) - {current})
    return stale[0] if len(stale) == 1 else None


def main() -> None:
    args = sys.argv[1:]

    if args == ["--check"]:
        sys.exit(0 if check_consistency() else 1)

    if len(args) != 1:
        print(__doc__)
        sys.exit(1)

    new_version = args[0].lstrip("v")
    if not VERSION_RE.match(new_version):
        print(f"ERROR: '{new_version}' doesn't look like a version "
              f"(expected e.g. 1.14.0-beta.18 or 1.14.0)")
        sys.exit(1)

    old_version = get_init_version()

    if old_version == new_version:
        # __init__.py is already at new_version — most likely someone edited
        # it by hand instead of running this script (exactly what happened
        # for v1.15.0-beta.4: a `chmod`/permission hiccup led to a manual
        # edit of __init__.py, and user-guide.html was silently left behind
        # until CI caught it). Don't just give up — check whether
        # user-guide.html still has the *previous* version lingering, and
        # fix that too, so this early-exit can't reintroduce the exact bug
        # this script exists to prevent.
        stale = find_stale_user_guide_version(new_version)
        if stale:
            print(f"__init__.py is already at {new_version}, but "
                  f"{USER_GUIDE.relative_to(ROOT)} still references the "
                  f"stale version '{stale}' — fixing that now.\n")
            count = bump_user_guide(stale, new_version)
            if count:
                print(f"  \u2713 {USER_GUIDE.relative_to(ROOT)}: replaced "
                      f"{count} occurrence(s) of '{stale}'")
            else:
                print(f"  ! replace failed unexpectedly — check manually")
            sys.exit(0)
        print(f"Already at {new_version} — nothing to do.")
        sys.exit(0)

    print(f"Bumping OpenSAK version: {old_version} -> {new_version}\n")

    set_init_version(new_version)
    print(f"  \u2713 {INIT_PY.relative_to(ROOT)}")

    count = bump_user_guide(old_version, new_version)
    if count:
        print(f"  \u2713 {USER_GUIDE.relative_to(ROOT)}: replaced {count} occurrence(s)")
    else:
        print(f"  ! {USER_GUIDE.relative_to(ROOT)}: no occurrences of "
              f"'{old_version}' found — check manually, something's off")

    print("\nDon't forget: CHANGELOG.md still needs a manual entry for this release.")


if __name__ == "__main__":
    main()
