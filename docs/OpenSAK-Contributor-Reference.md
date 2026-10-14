# OpenSAK — Contributor Reference for AI-Assisted Contributions

> Upload or paste this file into your AI assistant of choice — as project context,
> a custom instructions field, or at the start of a conversation — to give it the
> context it needs to help you write code for OpenSAK. See the full guide,
> [Contributing to OpenSAK with an AI Assistant](./CONTRIBUTING-with-AI.md), for
> how to set everything up.
>
> This guide was written and tested using Claude, but the steps should be
> adaptable to other AI coding assistants too.
>
> Feel free to append your own notes at the bottom (e.g. "I'm working on issue
> `#NNN`, here's what I've found so far") — your AI assistant will pick those
> up too.

## What is OpenSAK?

OpenSAK (Open Source Swiss Army Knife) is a free, open-source, cross-platform
geocache management application — a modern successor to GSAK. It runs on Windows,
Linux, and macOS.

- **GitHub:** https://github.com/OpenSAK-Org/OpenSAK
- **Website:** opensak.com
- **UI languages:** 8 (da, en, fr, nl, pt, cs, se, de) — see `src/opensak/lang/`

## Tech stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| GUI | PySide6 (Qt6) |
| Database | SQLite via SQLAlchemy 2.0 (each database is an independent .sqlite file) |
| Map | Leaflet.js + OpenStreetMap via QtWebEngine |
| Import | lxml (GPX / Pocket Query ZIP parser) |
| Packaging | PyInstaller (Windows/Linux/macOS) |
| Tests | pytest (unit + e2e), e2e run via `xvfb-run` |
| Type checking | mypy |

## Repository structure (main points)

```
opensak/
├── run.py
├── scripts/
│   └── bump_version.py                 # Atomic version bump — ALWAYS use this
├── src/opensak/
│   ├── __init__.py                     # __version__ (single source of truth)
│   ├── lang/
│   │   └── __init__.py                 # AVAILABLE_LANGUAGES dict, 8 languages
│   ├── db/                             # SQLAlchemy models, sessions, manager
│   ├── importer/                       # GPX + PQ ZIP importer
│   ├── filters/
│   │   └── engine.py                   # Filter types, AND/OR, FilterProfile, apply_filters()
│   ├── gps/
│   │   └── garmin.py                   # Garmin detection, GPX/GGZ generation
│   └── gui/
│       ├── mainwindow.py
│       ├── cache_table.py
│       ├── cache_detail.py             # Tabs: Notes, Waypoints, Attributes, etc.
│       ├── icon_provider.py
│       ├── map_widget.py
│       └── dialogs/
├── site/
│   └── user-guide.html                 # 5 hardcoded version references — see bump_version.py
├── CHANGELOG.md
└── tests/
    ├── unit-tests/
    └── e2e-tests/
```

The exact file names and structure change over time — always verify against the
real repository (see the guide's Step 1) rather than assuming from this document.

## Critical rules — read these before writing any code

1. **Always work from the `beta` branch, never `main`.** GitHub's default branch is
   `main` (stable), but all active development happens on `beta`. A plain
   `git clone` without `--branch beta` will silently give you outdated code. Always
   use:
   ```bash
   git clone --branch beta --depth 1 https://github.com/OpenSAK-Org/OpenSAK.git
   ```

2. **Never bump the version number manually.** Always use
   `python scripts/bump_version.py <version>` — it updates `__init__.py` and 5
   hardcoded references in `site/user-guide.html` atomically. A manual edit will
   miss references and break the build.

3. **All 8 language files must be updated together.** If you add or change a
   translation key, every file in `src/opensak/lang/` needs the new key, or the
   `test_no_missing_keys` test will fail in CI. Verify the exact file names in the
   repo — don't assume a naming pattern.

4. **Run mypy from the repository root with no arguments.** Scoped invocations
   (e.g. `mypy src/`) miss files that CI checks. Always run:
   ```bash
   mypy
   ```

5. **The `beta` branch changes frequently**, mostly because of an automated CI
   workflow that regenerates screenshots and opens a PR against `beta` (the
   *Macro API docs* workflow does the same for `docs/macros/api.md` and the
   editor files in `macros/types/`, which feature branches don't commit) — not just
   from other contributors' work. Before pushing, always do:
   ```bash
   git fetch origin beta
   git rebase origin/beta
   ```

6. **Commit messages** should have a descriptive body and end with a `fixes #NNN`
   footer referencing the GitHub issue.

7. **One thing per PR.** Keep changes focused on a single issue or feature —
   don't mix unrelated changes in one PR.

8. **Always work on your own branch off `beta`**, never commit directly to
   `beta` itself: `git checkout -b feature/my-feature`. This lets you work on
   more than one contribution at a time without them interfering with each
   other, and matches how the maintainers work internally.

9. **Every bug fix needs a regression test** — a test that reproduces the
   original bug and confirms the fix resolves it, not just the fix itself.

## Local setup & test commands

```bash
git clone --branch beta --depth 1 https://github.com/OpenSAK-Org/OpenSAK.git
cd OpenSAK
git checkout -b feature/my-feature
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"            # runtime + test deps (single source: pyproject.toml)
```

Running the app:
```bash
source .venv/bin/activate
python run.py
```

Running tests (always activate the venv first):
```bash
source .venv/bin/activate
pytest -v tests/unit-tests/               # Unit tests
xvfb-run pytest -v tests/e2e-tests/       # E2E tests (needs a virtual framebuffer for Qt)
```

Type checking:
```bash
mypy
```

## Database architecture

Each OpenSAK database is a fully independent SQLite file — no data is shared
between databases for the same GC code. Keep this in mind when working on
import/export or database-manager code.

## Before opening a pull request

- [ ] Code changes are complete and address a single GitHub issue
- [ ] If fixing a bug, includes a regression test that reproduces the original issue
- [ ] All 8 language files updated, if translation keys changed
- [ ] `pytest -v tests/unit-tests/` passes
- [ ] `xvfb-run pytest -v tests/e2e-tests/` passes (if relevant to your change)
- [ ] `mypy` passes with no errors, run from the repo root
- [ ] Rebased on the latest `origin/beta`
- [ ] Commit message includes a `fixes #NNN` footer
- [ ] PR description explains what changed and why

---

*This file is a trimmed, public version of OpenSAK's internal project reference,
intended for contributors' own AI-assisted setups. It omits internal contacts,
funding/financial details, and other maintainer-only information.*
