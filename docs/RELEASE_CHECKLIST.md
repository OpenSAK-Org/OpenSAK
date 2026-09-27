# OpenSAK — Stable Release Checklist (beta → main)

Use this for every stable (`main`) release, e.g. `v1.20.0`. It isn't needed
for ordinary beta tags — only when promoting `beta` to a stable,
non-prerelease version. Copy the checkboxes into a tracking issue if you want a
visible record per release.

Throughout this file, `X.Y.0` means the version being released and `X.Y.0-beta.N`
means the betas leading up to it.

---

## 0. Preconditions — don't start until these are true

- [ ] No open release-blocking issues on the milestone for `X.Y.0`
- [ ] Latest beta tested and confirmed on **macOS** (e.g. Mike Wood, Bob Long)
- [ ] Latest beta tested and confirmed on **Windows** (e.g. Hans), including
      the Microsoft Store/MSIX build if anything Windows-specific changed
- [ ] Latest beta tested on **Linux**, including AppImage self-update
      ("Upgrade now") from the previous beta
- [ ] All language files complete — currently 11: `da`, `en`, `fr`, `nl`, `pt`,
      `cs`, `se`, `de`, `de_CH`, `pl`, `es` (see `AVAILABLE_LANGUAGES` in
      `src/opensak/lang/__init__.py`). Note in the release notes any language
      that is still machine-translated and unreviewed
- [ ] `ci.yml` (which runs `tests.yml` and `quality.yml`) green on `beta` for
      the latest commit
- [ ] Screenshot suite (`tests/screenshots/`) run explicitly and reviewed —
      it's excluded from normal runs, so it's easy to forget before a stable cut

## 1. Version & changelog housekeeping (still on `beta`)

- [ ] Decide the final version number (`X.Y.0`, no `-beta.N` suffix)
- [ ] Consolidate the CHANGELOG: rewrite the run of `X.Y.0-beta.1` …
      `X.Y.0-beta.N` entries into one clean `## [X.Y.0] — YYYY-MM-DD` entry
      for end users — nobody installing stable needs to read a dozen beta
      micro-fixes. The detailed beta entries stay in git history
- [ ] Bump the version with `python scripts/bump_version.py X.Y.0` —
      **never** by hand. It updates `src/opensak/__init__.py` and the five
      version references in `site/user-guide.html` together. Check that
      `git diff --stat` shows both files
- [ ] Check `_RELEASE_DEFAULTS` in `src/opensak/utils/flags.py` — flip
      anything that was beta-gated and should now default on for everyone
- [ ] Sanity-check that `pyproject.toml` is still purely cosmetic (the app
      reads its version from `src/opensak/__init__.py`, not
      `importlib.metadata`) — no need to bump it, just confirm nothing changed
      that assumption

## 2. Documentation & public-facing text (still on `beta`)

Review everything against the final `beta` code — not against memory or the
changelog. **Always** = check every stable release. **If changed** = only when
the release touched that area. Commit the 2a changes to `beta` before the
merge in §3, so they ship with the release.

### 2a. In the repository

**Always**
- [ ] `CHANGELOG.md` — consolidated `## [X.Y.0]` entry (see §1)
- [ ] `README.md` — feature list, filter tab count, language count and list,
      Known Limitations, Documentation table (every user-facing file in
      `docs/` is linked), download/install wording
- [ ] `site/user-guide.html` — all new/changed features covered, no leftover
      `beta.N` labels, screenshots refreshed (latest screenshots auto-PR merged)
- [ ] `site/index.html` — feature list, language count, download section
      (Microsoft Store first on Windows), `<meta name="description">`
- [ ] `docs/ROADMAP.md` — status note per item and the "Last updated" date
- [ ] `docs/installation.md` — every distribution channel (Microsoft Store,
      direct Windows download, `.dmg`, AppImage, from source) and how updating
      works for each
- [ ] `docs/Uninstalling-OpenSAK.md` — matches the actual uninstall paths and
      data locations on each platform
- [ ] `docs/getting-started.md` — first-run flow (Welcome Wizard), import,
      filtering and export still match the UI
- [ ] `CONTRIBUTORS.md` — everyone who contributed code, translations or
      testing this cycle
- [ ] `docs/RELEASE_CHECKLIST.md` — this file: tester names, language count,
      artifact list, and anything in the process that changed this time

**If changed**
- [ ] `docs/filters.md` — filter dialog changes
- [ ] `docs/keyboard-shortcuts.md` — new or changed shortcuts
- [ ] `docs/update-location.md` — reverse-geocoding / boundary data changes
- [ ] `CONTRIBUTING.md` — Python version, number of language strings, test
      commands, translation workflow
- [ ] `docs/CONTRIBUTING-with-AI.md` + `docs/OpenSAK-Contributor-Reference.md`
      — project structure, file paths, conventions, workflow
- [ ] `PRIVACY_POLICY.md` **and** `site/PRIVACY_POLICY.html` (keep both in
      sync) — any new network access or stored credentials (e.g. IMAP,
      keyring, update check, online geocoding)
- [ ] `.github/ISSUE_TEMPLATE/*.yml` — still asks for the right information
      (OpenSAK version, install source, platform)
- [ ] `packaging/msix/README.md` — changes to the MSIX/Store build process

### 2b. Outside the repository — prepare now, publish in §6

- [ ] **GitHub Release notes** — short user-facing summary with a link to the
      CHANGELOG
- [ ] **Microsoft Store "What's new in this version"** text — mind the
      undocumented ~300-character limit on Partner Center free-text fields
- [ ] **Facebook announcement** post + first comment with links
- [ ] **GitHub Discussions** announcement
- [ ] **GSAK forum** post for the OpenSAK thread

## 3. Merge `beta` → `main`

- [ ] Create a dedicated branch from `beta`: `git checkout -b merge-beta-to-X.Y.0`,
      push it, and open a PR into `main`
- [ ] Merge it with **"Create a merge commit"** — not squash — to keep git
      ancestry intact for future beta → main merges
- [ ] **Diff `pyproject.toml` specifically** — once, `main` still had
      `python_version = "3.11"` in the mypy config while `beta` had moved to
      `"3.12"`, which silently broke `quality.yml` after the merge. Confirm
      it's in sync; don't assume it stayed fixed
- [ ] `site/CNAME` still exists with the correct content after the merge
      (covered by `test_site_cname_exists` — let it run, don't eyeball it)
- [ ] GitHub Pages source is still set to "GitHub Actions" (not "Deploy from branch")
- [ ] CI fully green on `main` after the merge
- [ ] Get at least one review on the merge PR — for a stable release it's
      worth not using the admin bypass, even though you can

## 4. Tag & build

- [ ] On `main`: `git tag vX.Y.0 && git push origin vX.Y.0` — pushing the tag
      is what triggers `build.yml`, `screenshots.yml` and `deploy-site.yml`
- [ ] `build.yml` attached all four artifacts to the GitHub Release:
      `OpenSAK-vX.Y.0-Windows.zip`, `OpenSAK-vX.Y.0-Linux-x86_64.AppImage`,
      `OpenSAK-vX.Y.0-macOS-arm64.dmg`, `OpenSAK-vX.Y.0-macOS-x86_64.dmg`
- [ ] The release is **not** marked as a pre-release (set automatically from
      the tag name — just confirm)
- [ ] `deploy-site.yml` ran and opensak.com shows the new version, and the
      User Guide changelog link pins to the new tag (covered by
      `test_user_guide_changelog_link_pins_to_release_tag`)
- [ ] If `screenshots.yml` auto-PR'd anything back to `beta` off this tag,
      review and merge that PR too — don't let it sit
- [ ] Do at least one **clean install test** per platform (not just
      upgrade-in-place) — first-run migration (`bootstrap.json` /
      `opensak.json`, QSettings migration) is only properly exercised on a
      truly fresh install

### 4a. Microsoft Store

- [ ] Run **`build-msix.yml`** manually (Actions → Run workflow) on `main`.
      Leave version and display name blank — they're derived automatically
      (`X.Y.0` → `X.Y.999.0`, display name "OpenSAK")
- [ ] Download the `.msix` artifact (kept for 7 days only)
- [ ] Create a new submission in Partner Center: upload the package, paste the
      "What's new" text from §2b, update description/screenshots if features
      changed
- [ ] Remember: MSIX Revision is always `0`, and **a version number can never
      be reused** for changed content — not even metadata-only. If a
      submission fails, fix it and build a new version
- [ ] After certification: install/update from the Store on a real machine
      and confirm the version shown in **Help → About**

## 5. Post-release verification

- [ ] On a machine running the previous stable: the update popup offers `X.Y.0`
- [ ] On a machine running the latest beta: the update popup offers `X.Y.0` as
      a stable upgrade, not another beta
- [ ] Click the changelog link in that popup — it opens
      `blob/vX.Y.0/CHANGELOG.md` and shows the consolidated `X.Y.0` entry
- [ ] Click the download/releases button — it lands on the `vX.Y.0` release
      page (or starts the right download on Windows/macOS)
- [ ] Linux AppImage: "Upgrade now" from the latest beta works
- [ ] Microsoft Store: an existing Store install picks up the update

## 6. Communication

**Always**
- [ ] Publish the **GitHub Release notes** (§2b)
- [ ] **Facebook group announcement** — Tuesday/Wednesday around 16–18 Danish
      time; download links in the **first comment**, not in the post itself
- [ ] **GitHub Discussions** announcement, pinned
- [ ] **GSAK forum** — post in the OpenSAK thread ("Not strictly GSAK, but related")
- [ ] Thank testers, translators and contributors by name

**If changed**
- [ ] **Facebook group description / "About" and featured post** — platforms,
      languages, download link, what OpenSAK is
- [ ] **GitHub repository "About"** — description, website link, topics
- [ ] **Open Collective** — project description; consider an update post for
      major releases
- [ ] **GitHub Sponsors profile** — once approved

## 7. Close out and start the next cycle

- [ ] Close the issues that shipped in this release (only once they're in the
      tagged build with green CI); re-triage anything deliberately deferred to
      the next milestone instead of leaving it dangling
- [ ] Close the `X.Y.0` milestone and open one for the next version
- [ ] Bump `beta` forward with `python scripts/bump_version.py X.(Y+1).0-beta.1`
      so beta testers immediately see they're on a new cycle, rather than the
      app still reporting the stable version number
