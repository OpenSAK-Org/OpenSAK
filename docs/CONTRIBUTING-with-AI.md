# Contributing to OpenSAK with an AI Assistant

This guide is for OpenSAK/GSAK users who have some programming experience (in any
language — you don't need to already know Python, Qt, or PySide6) and want to
contribute a new feature or fix, using an AI assistant to help write the code.

It walks through the whole path: giving your AI assistant the right context,
finding something to work on, writing and testing the code, and opening a pull
request.

> This guide was written and tested using [Claude](https://claude.ai), and the
> concrete setup instructions (Step 3) reflect Claude's Projects feature
> specifically. The rest of the workflow — and the companion reference file —
> should be adaptable to other AI coding assistants with similar "persistent
> context" or "custom instructions" features.

---

## Who this is for

You should be comfortable with:
- Basic Git (`clone`, `branch`, `commit`, `push`, opening a pull request)
- Reading and reasoning about code in *some* language — your AI assistant will
  handle most of the Python/Qt specifics, but you'll need to understand what it
  produces well enough to test and review it

You do **not** need prior Python, PySide6/Qt, or SQLAlchemy experience.

**Good first contributions** are typically things that follow an existing pattern
closely: a new menu item, a new filter type modeled on an existing one, a small UI
tweak, a new translation key. Larger architectural changes (e.g. touching the GPS
export pipeline or the database schema) are better discussed with the maintainers
first — open an issue and ask before diving in.

---

## Step 1 — Get the code

Fork [OpenSAK-Org/OpenSAK](https://github.com/OpenSAK-Org/OpenSAK) on GitHub, then
clone **your fork's `beta` branch**:

```bash
git clone --branch beta --depth 1 https://github.com/<your-username>/OpenSAK.git
cd OpenSAK
git remote add upstream https://github.com/OpenSAK-Org/OpenSAK.git
git checkout -b feature/my-feature
```

> ⚠️ **Important:** GitHub's default branch is `main`, which holds the current
> stable release. All active development happens on `beta`. If you clone without
> `--branch beta`, you'll be working from outdated code.
>
> Always create your own branch off `beta` for each contribution — never commit
> directly to `beta` itself. This is how the maintainers work internally too, and
> it means you can work on more than one contribution at a time without them
> interfering with each other.

## Step 2 — Set up your local environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"            # runtime + test deps (single source: pyproject.toml)
python run.py                     # confirm the app launches
```

Run the existing test suite once to confirm your setup works before you start
changing anything:

```bash
source .venv/bin/activate
pytest -v tests/unit-tests/
```

## Step 3 — Set up your AI assistant with the right context

Give your AI assistant persistent access to
[`OpenSAK-Contributor-Reference.md`](./OpenSAK-Contributor-Reference.md) — the
companion file to this guide. It gives the assistant the tech stack, repo layout,
and the project's non-negotiable rules (branch strategy, version bumping,
language files, etc.), so you don't have to re-explain the project in every
conversation.

Optionally, add your own notes to the bottom of that file before uploading — for
example, which issue you're working on, or constraints specific to your
contribution.

**If you're using Claude:**
1. Create a new **Project** (e.g. "OpenSAK Contributions").
2. Open the Project's knowledge section and upload the reference file there.

**If you're using a different AI assistant**, use whatever equivalent feature it
offers — a "project," "custom instructions," "system prompt," or similar — to
attach the reference file. If your tool has no such feature, paste the file's
contents at the start of each new conversation instead.

**If your AI assistant works directly in your local clone** (e.g. Claude Code,
Cursor, or similar), it can read the repository itself. Point it at
`docs/OpenSAK-Contributor-Reference.md` at the start of a session, or copy the
file into the tool's own instructions file (for Claude Code: a `CLAUDE.md` in
your clone, kept out of your commits).

## Step 4 — Find something to work on

Browse [open issues](https://github.com/OpenSAK-Org/OpenSAK/issues). A good
place to start is the
[good first issue](https://github.com/OpenSAK-Org/OpenSAK/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
label — these are small, self-contained, and usually come with a note on where
to look in the code.

- **Picking up an existing issue?** Leave a comment saying you'd like to work on
  it, and wait for a maintainer to assign it to you before you start. This avoids
  two people unknowingly working on the same thing, and gives a maintainer a
  chance to flag anything non-obvious about that issue. New contributors are
  assigned one issue at a time; once your PR is merged, you're welcome to pick
  the next one.
- **Have your own idea?** Open an issue describing it before writing code. This
  lets a maintainer weigh in early, and avoids duplicate or unwanted work.

Either way, look for issues that describe a self-contained feature or bug, or
that clearly follow an existing pattern in the code.

## Step 5 — Design the change with your AI assistant

Describe the issue to your AI assistant and ask it to:
- Locate the relevant existing code (it can be asked to inspect the repo
  directly, or you can paste in the relevant file)
- Propose an approach *in plain language first*, before writing code
- Point out which files will need to change, including whether it touches
  translations (all language files) or the database schema

**If you're fixing a bug**, ask it to include a test that reproduces the bug
as part of the change — a bug fix isn't considered complete without a test that
would have caught it. This applies even for small fixes.

Review the plan before asking your AI assistant to write the actual code.
Catching a wrong approach here is much cheaper than after the code is written.

## Step 6 — Get the code from your AI assistant

Once you're happy with the plan, ask it to write the complete files. A good
delivery from your AI assistant should include:

- **Complete files**, not partial snippets — ready to save as-is
- Confirmation that file paths/names were checked against the real repo (not
  assumed from memory)
- The exact test commands to run, including activating the virtual environment
- The `mypy` command
- Git commands to rebase, commit, and push

If any of this is missing, just ask for it — it's easy to forget a step in a long
conversation.

## Step 7 — Test locally

```bash
source .venv/bin/activate
pytest -v tests/unit-tests/
xvfb-run pytest -v tests/e2e-tests/   # if your change touches the GUI
mypy                                  # from the repo root, no arguments
```

`xvfb-run` is for Linux. On Windows and macOS, run the e2e tests with plain
`pytest -v tests/e2e-tests/`.

Run the app itself (`python run.py`) and manually exercise the feature you built.
Automated tests won't catch everything, especially in the UI.

If something fails, paste the error back to your AI assistant in the same
conversation — it has the context to help debug it.

## Step 8 — Commit and open a pull request

Rebase on the latest `beta` before pushing — it changes often, partly due to an
automated screenshot-generation workflow, not just other contributors' commits:

```bash
git fetch upstream beta
git rebase upstream/beta
```

Then commit and push to your fork:

```bash
git add <files>
git commit -m "Short summary of the change

Longer description if needed.

fixes #NNN"
git push origin feature/my-feature
```

Use `fixes #NNN` when your PR completes the issue. If the issue is being done
across several PRs (for example one module at a time), write `Part of #NNN`
instead — otherwise GitHub closes the issue when your first PR is merged.

Write commit messages, code comments and docstrings in English, even if you
use another language with your AI assistant.

Open a pull request from your fork's `feature/my-feature` branch into
`OpenSAK-Org/OpenSAK:beta`. In the description, mention:
- Which issue it addresses
- What you changed and why
- How you tested it

## Step 9 — Respond to review

A maintainer will review and may ask for changes — this is normal, even for
experienced contributors. Keep the conversation with your AI assistant open if
you need help addressing feedback; it still has the full context of what you
built.

---

## Common pitfalls

- **Cloning `main` instead of `beta`** — the single most common mistake. Double
  check with `git branch` after cloning.
- **Editing `__init__.py`'s version manually** instead of using
  `scripts/bump_version.py` — this is rarely something a contributor needs to do,
  but if a task involves it, always use the script.
- **Updating only some language files** when adding a translation key — all of
  them are required (currently 11, see `AVAILABLE_LANGUAGES` in
  `src/opensak/lang/__init__.py`), or CI will fail on `test_no_missing_keys`.
- **Running mypy from a subdirectory** — always run it from the repo root with no
  arguments.
- **Forgetting to rebase before pushing** — `beta` moves more often than you'd
  expect, largely due to automated CI activity.
- **Fixing a bug without adding a test for it** — every bug fix should include a
  test that reproduces the original problem, not just the fix itself.
- **Non-English comments or docstrings** — everything in the code is written in
  English. Some older modules still have Danish comments; don't copy that style
  (they're being translated in #1249).
- **Hardcoding user-facing text** — labels, messages and tooltips always go
  through a translation key (Appendix C), never as a string in the code.

---

## Appendices — worked examples

These walk through three representative contributions end-to-end, from issue to
PR. They're meant to show *how* to work with an AI assistant on a task, not to be followed
literally — the actual code will depend on the current state of the repo.

### Appendix A — Adding a new menu item

**Example scenario:** adding a "Copy GC code to clipboard" item to the cache
right-click context menu.

1. Ask your AI assistant to find the existing context menu code (likely in
   `src/opensak/gui/cache_table.py` or a related dialog) and show you how an
   existing menu item is wired up — action, icon, handler.
2. Ask it to add the new item following the same pattern, including a
   keyboard shortcut if appropriate (check
   `src/opensak/gui/dialogs/` for the shortcuts dialog, to keep it consistent).
3. If the menu item's label needs to be translated, this touches every language
   file — flag this explicitly if it doesn't mention it.
4. Test manually: right-click a cache, confirm the item appears and works, confirm
   existing menu items still work.

### Appendix B — Adding a new filter type

**Example scenario:** adding a filter for "caches without a recent log in N days."

1. Point your AI assistant at `src/opensak/filters/engine.py` and ask it to
   explain how an existing, similar filter type is structured (its
   class/definition, how it's registered, how `apply_filters()` uses it).
2. Ask it to design the new filter following that pattern — including how it
   appears in the filter-building UI dialog.
3. Since filters can be combined with AND/OR and saved as profiles, ask it to
   confirm the new filter type interacts correctly with those (it usually should,
   if it follows the existing pattern closely, but confirm rather than assume).
4. Write or extend a unit test for the new filter type — ask it to include one
   if it doesn't by default.

### Appendix C — Adding a new UI string (translation key)

**Example scenario:** adding a new label to a dialog, which needs a translation
key.

1. Ask your AI assistant to find the existing key naming convention in
   `src/opensak/lang/__init__.py` and one language file, e.g. `en`.
2. Ask it to add the new key to **all language files** — explicitly confirm
   this in the request, since it's the single most common thing to get half-done.
   For languages you don't speak, ask it to provide a reasonable machine
   translation and flag it clearly as unverified, so a native speaker can review
   it later.
3. Run the test suite — `test_no_missing_keys` will fail immediately if any file
   was missed, which is a fast way to confirm you got all of them.
4. Wire the new key into the UI code where the string is used.

---

*This guide covers the AI-assisted workflow for contributing to OpenSAK. For
the maintainers' own internal notes, see the project's non-public documentation.*
