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
    (beta.10) — it just resurfaced in a second, manually-synced location.
    site/user-guide.html (deployed to opensak.com) and
    docs/opensak-user-guide.html (kept as a synced source copy) must both
    be correct and in sync.
    """
    from opensak import __version__

    expected_link = f"blob/v{__version__}/CHANGELOG.md"

    for path in (Path("site/user-guide.html"), Path("docs/opensak-user-guide.html")):
        text = path.read_text(encoding="utf-8")
        assert expected_link in text, (
            f"{path} is missing the pinned changelog link {expected_link!r} — "
            f"did the version label get bumped without updating the link?"
        )
        assert "blob/main/CHANGELOG.md" not in text, (
            f"{path} has a changelog link hardcoded to 'main' — "
            f"it must pin to the release tag instead."
        )
        assert "blob/beta/CHANGELOG.md" not in text, (
            f"{path} has a changelog link hardcoded to 'beta' — "
            f"it must pin to the release tag instead."
        )
