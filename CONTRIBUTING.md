# Contributing to OpenSAK

Thank you for your interest in contributing! Here is how to get involved.

---

## Reporting Bugs

Please use [GitHub Issues](https://github.com/OpenSAK-Org/opensak/issues) and include:

- Platform and version (e.g. "Linux Mint 21.3" or "Windows 11")
- Python version (`python3 --version`)
- What you were trying to do
- The error message from the terminal (if any)

Issues in Danish are also welcome — the project was created in Denmark and Danish bug reports are perfectly fine.

---

## Suggesting Features

Open a GitHub Issue with the label **enhancement** and describe what you would like and why. Screenshots or mockups are very helpful.

---

## Before You Start — Please Open an Issue First

**Before submitting a pull request, please open a GitHub Issue describing what you'd like to contribute** — even for small changes. This lets us:

- Confirm the change fits the project's direction and current roadmap
- Avoid duplicate or overlapping work (check existing issues and milestones first — it might already be planned or in progress)
- Agree on scope and approach before you invest time in an implementation

If an issue already exists for what you want to work on, leave a comment saying you'd like to pick it up, and wait for a maintainer to confirm before starting.

Pull requests opened without a linked issue may be asked to have one created retroactively before review begins.

---

## Branch Workflow

OpenSAK uses a **two-branch workflow**:

- **`beta`** — active development branch. **All pull requests should target this branch.**
- **`main`** — stable release branch only. Changes land here exclusively through maintainer-managed release merges, never directly from external PRs.

Note that `main` is GitHub's default branch for this repo, so a plain `git clone` or fork will check out `main` unless you explicitly select `beta`. Please make sure both your local branch and your pull request's base branch are set to `beta` — PRs opened against `main` will fail our version/changelog CI checks and will need to be retargeted before review.

---

## Contributing Code

1. Fork the repository
2. Clone your fork and check out `beta`: `git clone --branch beta https://github.com/YOUR_USERNAME/opensak.git`
3. Create a branch off `beta`: `git checkout -b feature/my-feature`
4. Make your changes
5. Run the test suite: `pytest -v tests/`
6. Commit with a clear message: `git commit -m "Add: description of change"`
7. Push: `git push origin feature/my-feature`
8. Open a Pull Request — double-check the base branch is set to `beta`, not `main`

Please keep pull requests focused — one feature or fix per PR makes review much easier.

---

## Development Setup

```bash
git clone --branch beta https://github.com/OpenSAK-Org/opensak.git
cd opensak
python3 -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\activate           # Windows
pip install -e ".[dev]"            # runtime + test deps (single source: pyproject.toml)
pytest -v tests/                   # run tests
python run.py                      # start the application
```

---

## Adding or Updating a Translation

Want to translate OpenSAK into a new language, or update an existing one? It only takes one file.

**Creating a new language file:**

1. Copy `src/opensak/lang/en.py` to e.g. `src/opensak/lang/de.py`
2. Translate the string values on the right-hand side — **do not change the keys**
3. Register the language in `src/opensak/lang/__init__.py`:
   ```python
   AVAILABLE_LANGUAGES = {
       "da": "Dansk",
       "en": "English",
       "fr": "Français",
       "nl": "Nederlands",
       "pt": "Português",
       "cs": "Čeština",
       "se": "Svenska",
       "de": "Deutsch",
       "fi": "Suomi",   # ← add this line
   }
   ```

4. Test by selecting the new language in **Tools → Settings** and restarting
5. Verify the integrity of labels, values, and missing content by running:

```bash
opensak-test # or pytest tests
```

6. Submit your translation — see below for how

The language files contain around 570 strings. A rough machine translation that a native speaker then reviews is a perfectly good starting point.

---

### Submitting a translation — two options

#### Option A — Email (simplest, always works)

Just email your updated language file to the maintainer. This is perfectly fine and just as welcome as a GitHub contribution. No GitHub knowledge required.

#### Option B — Fork & Pull Request

This is the standard open source workflow and gives you credit on GitHub.

**Why you get a 403 error if you try to push directly:**
The repository belongs to the maintainer — nobody else has write access. The correct approach is to fork the repository first (make your own copy on GitHub), push your changes there, and then open a Pull Request.

**Step by step:**

1. Go to https://github.com/OpenSAK-Org/opensak and click **Fork** (top-right corner). GitHub creates a copy at `https://github.com/YOUR_USERNAME/opensak`.

2. Clone your fork and check out `beta` (development happens on `beta`, not `main` — see [Branch Workflow](#branch-workflow) above):
   ```bash
   git clone --branch beta https://github.com/YOUR_USERNAME/opensak.git
   cd opensak
   ```

3. (Optional but recommended) Add the original as `upstream` so you can sync later:
   ```bash
   git remote add upstream https://github.com/OpenSAK-Org/opensak.git
   ```

4. Create a branch off `beta`:
   ```bash
   git checkout -b update-french-translation
   ```

5. Copy your updated language file into `src/opensak/lang/` and commit:
   ```bash
   git add src/opensak/lang/fr.py
   git commit -m "Update French translation"
   git push origin update-french-translation
   ```

6. Go to your fork on GitHub — click **"Compare & pull request"** and submit. The maintainer will review and merge it.

**Keeping your fork up to date for future contributions:**
```bash
git checkout beta
git pull upstream beta
git push origin beta
```

---

## Code Style

- Python 3.11+, PySide6 for the GUI
- Use `pathlib.Path` for all file paths (cross-platform)
- Background work runs in `QThread` subclasses — never block the main thread
- All user-visible strings go through `tr("key")` from `opensak.lang`
- New UI strings need a matching key in `lang/en.py` file

---

## Running the Tests

```bash
opensak-test # or pytest -v tests/
```

The test suite covers the database layer, importer, filter engine, dialogs, GPS/export, and language completeness. New features should include tests where practical.

---

## Questions?

Open an issue or start a discussion on GitHub. Contributions of any size are welcome — from fixing a typo to adding a whole new feature.
