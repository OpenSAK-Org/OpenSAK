# Contributing to OpenSAK

Thank you for your interest in contributing! Here is how to get involved.

---

## Reporting Bugs

Please use [GitHub Issues](https://github.com/AgreeDK/opensak/issues) and include:

- Platform and version (e.g. "Linux Mint 21.3" or "Windows 11")
- Python version (`python3 --version`)
- What you were trying to do
- The error message from the terminal (if any)

Issues in Danish are also welcome — the project was created in Denmark and Danish bug reports are perfectly fine.

---

## Suggesting Features

Open a GitHub Issue with the label **enhancement** and describe what you would like and why. Screenshots or mockups are very helpful.

---

## Contributing Code

1. Fork the repository
2. Create a branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run the test suite: `pytest -v tests/`
5. Commit with a clear message: `git commit -m "Add: description of change"`
6. Push: `git push origin feature/my-feature`
7. Open a Pull Request

Please keep pull requests focused — one feature or fix per PR makes review much easier.

---

## Development Setup

```bash
git clone https://github.com/AgreeDK/opensak.git
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

1. Go to https://github.com/AgreeDK/opensak and click **Fork** (top-right corner). GitHub creates a copy at `https://github.com/YOUR_USERNAME/opensak`.

2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/opensak.git
   cd opensak
   ```

3. (Optional but recommended) Add the original as `upstream` so you can sync later:
   ```bash
   git remote add upstream https://github.com/AgreeDK/opensak.git
   ```

4. Create a branch:
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
git checkout main
git pull upstream main
git push origin main
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
