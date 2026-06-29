# OpenSAK — Stable Release Checklist (beta → main)

Use this for every `main` release (v1.14.0, v1.15.0, ...). Not needed for
ordinary beta tags — only when promoting `beta` to a stable, non-prerelease
version. Copy this file's checkboxes into a tracking issue if you want a
visible record per release.

---

## 0. Preconditions — don't start until these are true

- [ ] No open release-blocking issues on the `beta` milestone
- [ ] Latest beta tested and confirmed by macOS testers (Mike Wood, Bob Long)
- [ ] Latest beta tested and confirmed on Windows (Hans, or another Windows tester)
- [ ] All 8 language files complete and proofread (`da`, `en`, `fr`, `nl`, `pt`,
      `cs`, `se`, `de`) — no machine-translated strings left unreviewed
- [ ] `ci.yml`, `quality.yml`, `tests.yml` all green on `beta` for the latest commit
- [ ] Screenshot suite (`tests/screenshots/`) run explicitly and reviewed —
      it's excluded from normal runs, so it's easy to forget before a stable cut

## 1. Version & changelog housekeeping (still on `beta`)

- [ ] Decide the final version number (e.g. `1.14.0`, no `-betaN` suffix)
- [ ] Consolidate the CHANGELOG: rewrite the run of `1.14.0-beta.1` …
      `1.14.0-beta.N` entries into one clean `## [1.14.0] — YYYY-MM-DD` entry
      for end users — nobody installing stable needs to read 12 beta
      micro-fixes. Keep the detailed beta entries in git history, not in the
      user-facing changelog.
- [ ] Check `_RELEASE_DEFAULTS` in `flags.py` — flip anything that was
      beta-gated and should now default on for everyone
- [ ] Sanity-check `pyproject.toml` is still purely cosmetic (app reads the
      version from `src/opensak/__init__.py`, not `importlib.metadata`) —
      no need to bump it, just confirm nothing changed that assumption

## 2. Merge `beta` → `main`

- [ ] `git checkout main && git pull origin main`
- [ ] `git merge beta` (full merge, not the partial `git checkout beta -- path`
      trick we used for a docs-only sync last time)
- [ ] **Diff `pyproject.toml` specifically** — last time, `main` still had
      `python_version = "3.11"` in the mypy config while `beta` had moved to
      `"3.12"`, and that silently broke `quality.yml` after merge. Confirm
      this is actually in sync now, don't assume it stayed fixed.
- [ ] Confirm `site/CNAME` still exists with the correct content after the
      merge (there's a unit test for this — let it run, don't eyeball it)
- [ ] Confirm GitHub Pages source is still set to "GitHub Actions" (not
      "Deploy from branch")
- [ ] CI fully green on `main` post-merge
- [ ] Get at least one review on the merge PR before pushing past it — for a
      stable release it's worth not using the bypass, even though you can

## 3. Website & docs

- [ ] `opensak.com` User Guide reflects the final feature set — no leftover
      `beta.N` version labels anywhere in `site/user-guide.html` (the only
      copy as of beta.14 — there is no longer a synced `docs/` duplicate)
- [ ] Changelog link on the User Guide page pins to the new release tag —
      there's a unit test for this too (`test_user_guide_changelog_link_pins_to_release_tag`),
      let it run, don't eyeball it
- [ ] Any "planned, not available" callouts (e.g. GGZ export) still accurate
      for what's actually shipping in this version
- [ ] Download/links on the website point at the new stable tag, not an old
      `1.13.x` release

## 4. Tag & build

- [ ] On `main`: `git tag v1.14.0 && git push origin v1.14.0`
- [ ] Confirm the GitHub Release for this tag is **not** marked as a
      pre-release (uncheck "This is a pre-release" if creating notes manually)
- [ ] Confirm `build.yml` produced and attached all three platform artifacts:
      Linux AppImage, Windows `.exe`, macOS build
- [ ] If `screenshots.yml` auto-PR'd anything back to `beta` off this tag,
      review and merge that PR too, don't let it sit
- [ ] Do at least one **clean install test** per platform (not just
      upgrade-in-place) — first-run migration (`bootstrap.json` /
      `opensak.json`, QSettings migration) only gets properly exercised on a
      truly fresh install

## 5. Post-release verification

- [ ] On a machine running the previous stable (`1.13.x`): confirm the
      update popup correctly offers `1.14.0`
- [ ] On a machine running the latest beta: confirm the update popup offers
      `1.14.0` as a stable upgrade, not another beta
- [ ] Click the changelog link in that popup — confirm it opens
      `blob/v1.14.0/CHANGELOG.md`, and that the rendered page shows the
      consolidated `1.14.0` entry, not "Unreleased"
- [ ] Click "open releases" — confirm it lands on the `v1.14.0` release page

## 6. Communication

- [ ] Facebook group post — major version bumps get an announcement (minor
      releases are left to the auto-update notifier)
- [ ] Thank the beta testers and translators by name (Mike Wood, Bob Long,
      Hans, Pierre, Fabio)
- [ ] GitHub Discussions post / pinned announcement
- [ ] Close out resolved issues tagged for this milestone; re-triage anything
      deliberately deferred (e.g. `HasTrackableFilter` asymmetry) to the next
      milestone instead of leaving it dangling

## 7. Start the next cycle

- [ ] Bump `beta`'s `src/opensak/__init__.py` forward to the next dev version
      (e.g. `1.15.0-beta.1`) so beta testers immediately see they're on a new
      cycle, rather than the app still reporting the just-released stable
      version number
- [ ] Open a fresh milestone for the next version if you track issues that way
