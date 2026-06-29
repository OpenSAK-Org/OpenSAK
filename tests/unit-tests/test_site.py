# tests/unit-tests/test_site.py — sanity checks for site deployment files.
from pathlib import Path


def test_site_cname_exists():
    """site/CNAME must always exist and contain opensak.com.

    If this test fails, site/CNAME has been deleted or changed,
    and opensak.com will go down on the next deploy!
    """
    cname = Path("site/CNAME")
    assert cname.exists(), "site/CNAME is missing — opensak.com will go down on next deploy!"
    assert cname.read_text().strip() == "opensak.com", (
        f"site/CNAME contains wrong domain: {cname.read_text().strip()!r}"
    )


def test_user_guide_changelog_link_pins_to_release_tag():
    """Regression for the bug reported on Facebook after beta.12: the User
    Guide's "Changelog" reference link must always pin to the exact release
    tag matching opensak.__version__ — never to a moving branch name like
    'main' or 'beta'. A branch-HEAD link drifts as soon as that branch moves
    on, so a reader following the link from an older beta.N page ended up on
    the stable CHANGELOG instead of the beta entry the page was about.

    This is the same class of bug already fixed for the in-app update popup
    (beta.10) — it just resurfaced in a second location. As of beta.14,
    site/user-guide.html is the *only* User Guide source (see
    test_no_duplicate_user_guide_copy below), so there's only one place
    left to check.
    """
    from opensak import __version__

    expected_link = f"blob/v{__version__}/CHANGELOG.md"

    text = Path("site/user-guide.html").read_text(encoding="utf-8")
    assert expected_link in text, (
        f"site/user-guide.html is missing the pinned changelog link "
        f"{expected_link!r} — did the version label get bumped without "
        f"updating the link?"
    )
    assert "blob/main/CHANGELOG.md" not in text, (
        "site/user-guide.html has a changelog link hardcoded to 'main' — "
        "it must pin to the release tag instead."
    )
    assert "blob/beta/CHANGELOG.md" not in text, (
        "site/user-guide.html has a changelog link hardcoded to 'beta' — "
        "it must pin to the release tag instead."
    )


def test_no_duplicate_user_guide_copy():
    """Regression for beta.13: docs/opensak-user-guide.html used to be a
    second, manually-synced copy of site/user-guide.html. It was never
    referenced by any code, build step, or README link — purely an
    unmaintained duplicate left over from when GitHub Pages deployed from
    the /docs folder on `main`, before the switch to Actions-based
    deployment from site/. It silently drifted out of sync (wrong version
    label, stale changelog link) because there was a second place to
    forget to update.

    site/user-guide.html is now the single source of truth. This test
    fails loudly if that file, or its supporting CNAME/screenshot
    duplicates, ever reappear.
    """
    for path in (
        Path("docs/opensak-user-guide.html"),
        Path("docs/CNAME"),
        Path("docs/assets"),
    ):
        assert not path.exists(), (
            f"{path} has reappeared — site/user-guide.html must be the "
            f"only User Guide source. Do not recreate a synced docs/ copy; "
            f"update site/user-guide.html only."
        )
