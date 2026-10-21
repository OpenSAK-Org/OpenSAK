from collections import Counter
import importlib.util
import io
import pathlib
import re
import string
import tokenize
import pytest
import ast

LANG_DIR = pathlib.Path("src/opensak/lang")
SRC_DIR = pathlib.Path("src/opensak")
REFERENCE_LANG = "en"  # reference language file

def load_strings(file_path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("lang_module", file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    strings = getattr(module, "STRINGS", None)
    if not isinstance(strings, dict):
        raise TypeError(f"{file_path} does not define STRINGS as a dict")
    return strings

# Load reference keys
ref_file = LANG_DIR / f"{REFERENCE_LANG}.py"
ref_strings = load_strings(ref_file)
ref_keys = set(ref_strings.keys())

# Scan all language files except the reference and __init__.py
lang_files = [
    f for f in LANG_DIR.glob("*.py")
    if f.stem not in {REFERENCE_LANG, "__init__"}
]

# Generate human-readable IDs for pytest
lang_ids = [f.stem for f in lang_files]

@pytest.mark.parametrize("lang_file", lang_files, ids=lang_ids)
def test_no_missing_keys(lang_file):
    strings = load_strings(lang_file)
    missing_keys = ref_keys - set(strings.keys())
    assert not missing_keys, f"{lang_file.stem} is missing keys: {sorted(missing_keys)}"


@pytest.mark.parametrize("lang_file", lang_files, ids=lang_ids)
def test_no_empty_values(lang_file):
    strings = load_strings(lang_file)
    empty_values = [k for k, v in strings.items() if not v or str(v).strip() == ""]
    assert not empty_values, f"{lang_file.stem} has empty values: {sorted(empty_values)}"


@pytest.mark.parametrize("lang_file", lang_files, ids=lang_ids)
def test_no_extra_keys(lang_file):
    strings = load_strings(lang_file)
    extra_keys = set(strings.keys()) - ref_keys
    assert not extra_keys, f"{lang_file.stem} has extra keys: {sorted(extra_keys)}"

@pytest.mark.parametrize("lang_file", [ref_file] + lang_files, ids=[REFERENCE_LANG] + lang_ids)
def test_no_duplicate_keys(lang_file):
    # Verifies that there are no duplicate keys defined in the STRINGS dictionary.
    content = lang_file.read_text(encoding="utf-8")
    tree = ast.parse(content)
    
    found_strings_dict = False
    for node in ast.walk(tree):
        target_name = None
        value_node = None

        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "STRINGS":
                    target_name = "STRINGS"
                    value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "STRINGS":
                target_name = "STRINGS"
                value_node = node.value

        if target_name == "STRINGS" and isinstance(value_node, ast.Dict):
            found_strings_dict = True
            
            # Extract keys from the AST nodes
            keys = []
            for key_node in value_node.keys:
                if isinstance(key_node, ast.Constant):
                    keys.append(key_node.value)
                elif isinstance(key_node, (ast.Str, ast.Bytes)):
                    keys.append(getattr(key_node, 's', getattr(key_node, 'n', None)))

            counts = Counter(keys)
            duplicates = {k: v for k, v in counts.items() if v > 1}
            
            # Formatted error message: "key_name (3x), another_key (2x)"
            err_msg = ", ".join([f"'{k}' ({v}x)" for k, v in duplicates.items()])
            
            assert not duplicates, f"{lang_file.name} has duplicate keys defined: {err_msg}"
            break 

    assert found_strings_dict, f"Could not find STRINGS dictionary in {lang_file.name}"


@pytest.mark.parametrize("lang_file", [ref_file] + lang_files, ids=[REFERENCE_LANG] + lang_ids)
def test_no_double_escaped_newlines(lang_file):
    """
    Regression test: writing "\\\\n" (a literal backslash followed by "n")
    in a language file's source instead of "\\n" (an actual newline escape)
    shows up in the UI as a raw "\n" instead of a line break. Reported on
    Facebook for German; turned out to also affect Danish, French, Dutch,
    Portuguese, Czech and Swedish — only English was unaffected.
    """
    strings = load_strings(lang_file)
    offending = [k for k, v in strings.items() if isinstance(v, str) and "\\n" in v]
    assert not offending, (
        f"{lang_file.stem} has a literal backslash-n (double-escaped newline) "
        f"in: {sorted(offending)}"
    )


def test_no_globally_redundant_values():
    """No two keys should share the same translation value across ALL supported languages.

    A pair (k1, k2) is redundant when strings[k1] == strings[k2] in every language
    file simultaneously — meaning they represent one concept split into two keys for
    no reason.  Context-dependent keys that differ in at least one language are allowed.
    """
    all_langs = {f.stem: load_strings(f) for f in [ref_file] + lang_files}
    all_keys = list(ref_strings.keys())

    redundant_pairs = []
    for i, k1 in enumerate(all_keys):
        for k2 in all_keys[i + 1:]:
            if all(strings.get(k1) == strings.get(k2) for strings in all_langs.values()):
                redundant_pairs.append((k1, k2, ref_strings[k1]))

    assert not redundant_pairs, (
        "The following key pairs have identical translations in every language "
        "and should be merged into a single key:\n"
        + "\n".join(f"  '{k1}' == '{k2}'  ({repr(v)})" for k1, k2, v in redundant_pairs)
    )


def _collect_string_literals(src_dir: pathlib.Path) -> set[str]:
    """Return all string literal values found in Python source under src_dir (via AST)."""
    literals: set[str] = set()
    for f in sorted(src_dir.rglob("*.py")):
        if f.parent.name == "lang":
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
    return literals


def test_no_unused_keys():
    """Every key defined in the reference language file must appear at least once
    as a string literal in the application source (outside the lang/ package).

    Unused keys are dead translations that bloat every language file and mislead
    contributors who maintain translations.  Remove the key from all language files
    (and from this test's assertion message) when it genuinely has no caller.
    """
    all_keys = set(ref_strings.keys())
    used_literals = _collect_string_literals(SRC_DIR)
    unused = all_keys - used_literals

    assert not unused, (
        f"{len(unused)} translation key(s) defined in {REFERENCE_LANG}.py are never "
        "used as string literals in the application source.  Either add a tr() call "
        "or remove the key from every language file:\n"
        + "\n".join(f"  {k!r}" for k in sorted(unused))
    )


# ── i18n guards (#1134) ──────────────────────────────────────────────────────
# test_no_missing_keys only checks that the language files agree with each
# other, so nothing else notices code that bypasses tr(). These three tests
# keep the sweep from #1127/#1128 from slowly coming undone.
#
# A literal or call that legitimately holds untranslated text (a product
# name, a format sample, a vowel set) is marked with an inline comment
# "# i18n: ignore — <reason>" on any line it spans, so the reason sits next
# to the code rather than in a central list here.

IGNORE_MARKER = "i18n: ignore"
DANISH_LETTERS = frozenset("æøåÆØÅ")

# Qt calls whose first positional argument is user-facing text.
_TEXT_METHODS = frozenset({
    "setText", "setToolTip", "setWindowTitle", "setPlaceholderText",
    "setTitle", "showMessage", "setStatusTip", "setWhatsThis",
    "setTabText", "setLabelText", "addItem", "insertItem",
})
# QMessageBox.<x>(parent, title, text, ...): title and text.
_MESSAGE_BOX_METHODS = frozenset({"information", "warning", "critical", "question", "about"})
# QFileDialog.<x>(parent, caption, ...): caption.
_FILE_DIALOG_METHODS = frozenset({
    "getSaveFileName", "getOpenFileName", "getOpenFileNames", "getExistingDirectory",
})
# Widget constructors whose first positional argument is their text.
_TEXT_CONSTRUCTORS = frozenset({
    "QLabel", "QPushButton", "QCheckBox", "QGroupBox", "QRadioButton",
    "QAction", "QToolButton", "QMenu",
})
# Receivers that are loggers: log.warning(...) is not QMessageBox.warning(...).
_LOGGER_NAMES = frozenset({"log", "logger", "_log", "_logger", "LOG", "logging"})

_HTML_CODE = re.compile(r"<code>.*?</code>", re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]*>")
_FILE_EXTENSION = re.compile(r"^\s*\.[A-Za-z0-9]+\s*$")
_LETTER = re.compile(r"[^\W\d_]")


def _app_source_files() -> list[pathlib.Path]:
    """Every .py file under src/opensak/ except the lang/ package."""
    return [
        f for f in sorted(SRC_DIR.rglob("*.py"))
        if f.relative_to(SRC_DIR).parts[0] != "lang"
    ]


def _ignored_lines(source: str) -> set[int]:
    """Line numbers carrying a '# i18n: ignore' comment. tokenize is used so
    that the marker inside a string literal does not count."""
    lines: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT and IGNORE_MARKER in tok.string:
            lines.add(tok.start[0])
    return lines


def _is_ignored(node: ast.AST, ignored: set[int]) -> bool:
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", None) or start
    return any(line in ignored for line in range(start, end + 1))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id() of every docstring Constant node in *tree*."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def _parsed_sources():
    """(path, tree, ignored line numbers) for every application source file."""
    for f in _app_source_files():
        source = f.read_text(encoding="utf-8")
        yield f, ast.parse(source), _ignored_lines(source)


def _visible_text(node: ast.AST) -> str | None:
    """The text a reader would see from a str literal or f-string: the
    constant parts with HTML tags and <code> spans removed. None when the
    node is not a literal (a tr() call, a variable, ...) or holds no
    visible text (an HTML wrapper, a file extension like '.gpx')."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        text = "".join(
            v.value for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    else:
        return None
    text = _HTML_TAG.sub("", _HTML_CODE.sub("", text))
    if _FILE_EXTENSION.match(text) or not _LETTER.search(text):
        return None
    return text


def _receiver_name(func: ast.Attribute) -> str | None:
    value = func.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


def _ui_text_args(call: ast.Call) -> list[ast.expr]:
    """The arguments of *call* that end up as visible UI text."""
    func = call.func
    if isinstance(func, ast.Name):
        return list(call.args[:1]) if func.id in _TEXT_CONSTRUCTORS else []
    if not isinstance(func, ast.Attribute):
        return []
    receiver = _receiver_name(func)
    if receiver in _LOGGER_NAMES:
        return []
    if func.attr in _MESSAGE_BOX_METHODS:
        return list(call.args[1:3]) if receiver == "QMessageBox" else []
    if func.attr in _FILE_DIALOG_METHODS:
        return list(call.args[1:2]) if receiver == "QFileDialog" else []
    if func.attr == "addTab":
        # addTab(widget, label): only the label. The second argument of
        # addItem(text, data) is userData, so only the first is checked.
        return list(call.args[1:2])
    if func.attr in _TEXT_METHODS:
        return list(call.args[:1])
    return []


def test_no_danish_in_source_literals():
    """No string literal in the application source (outside lang/ and
    docstrings) contains æ, ø or å. Comments are not AST nodes, so Danish
    comments are fine; this catches Danish UI text and Danish log/print
    messages alike. Danish words without æ/ø/å ("Gem", "Ingen") slip
    through here; test_ui_text_goes_through_tr covers those in the UI."""
    offending = []
    for path, tree, ignored in _parsed_sources():
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings
                    and DANISH_LETTERS.intersection(node.value)
                    and not _is_ignored(node, ignored)):
                offending.append(f"  {path}:{node.lineno}: {node.value[:60]!r}")
    assert not offending, (
        "Danish text in string literals — put UI text through tr() and write "
        "log messages in English, or mark a legitimate literal with "
        f"'# {IGNORE_MARKER} — <reason>':\n" + "\n".join(offending)
    )


def test_ui_text_goes_through_tr():
    """Text passed straight to a text-setting Qt call (setText, setToolTip,
    QMessageBox.warning, QLabel(...), ...) must come from tr(), not a
    literal. Known blind spot: text built in a non-GUI module and passed
    around before it reaches a widget is not seen here (it is caught by
    test_no_danish_in_source_literals if it is Danish)."""
    offending = []
    for path, tree, ignored in _parsed_sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _is_ignored(node, ignored):
                continue
            for arg in _ui_text_args(node):
                text = _visible_text(arg)
                if text is not None:
                    offending.append(f"  {path}:{arg.lineno}: {text[:60]!r}")
    assert not offending, (
        "UI text that bypasses tr() — add a translation key, or mark a "
        "product name / format sample with "
        f"'# {IGNORE_MARKER} — <reason>':\n" + "\n".join(offending)
    )


def _tr_names(tree: ast.AST) -> set[str]:
    """'tr' plus any alias it is imported under (from opensak.lang import tr as _tr)."""
    names = {"tr"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "opensak.lang":
            for alias in node.names:
                if alias.name == "tr" and alias.asname:
                    names.add(alias.asname)
    return names


def test_tr_keys_exist():
    """Every tr("literal_key", ...) call must name a key in en.py — the
    reverse of test_no_unused_keys. A typo in a key otherwise shows the raw
    key in the UI. Calls with a computed key are not checked."""
    missing = []
    for path, tree, _ignored in _parsed_sources():
        names = _tr_names(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in names and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value not in ref_keys):
                missing.append(f"  {path}:{node.lineno}: {node.args[0].value!r}")
    assert not missing, (
        f"tr() keys that do not exist in {REFERENCE_LANG}.py:\n" + "\n".join(missing)
    )


# ── Placeholder guards ───────────────────────────────────────────────────────
# tr() swallows the KeyError/ValueError from str.format(), so a wrong
# placeholder never crashes — the user just sees the raw "{name}" template.
# These tests make that mistake visible in CI instead.

def _placeholders(text: str) -> set[str]:
    """The {field} names in *text*, reduced to their root name ("x" for
    "{x.attr}" or "{x[0]}"). Raises ValueError on unbalanced braces."""
    return {
        re.split(r"[.\[]", field, maxsplit=1)[0]
        for _literal, field, _spec, _conv in string.Formatter().parse(text)
        if field is not None
    }


@pytest.mark.parametrize("lang_file", [ref_file] + lang_files, ids=[REFERENCE_LANG] + lang_ids)
def test_placeholders_match_reference(lang_file):
    """Every translation must use exactly the {placeholders} of the en.py
    string, and its braces must be balanced. A renamed or dropped placeholder
    ("{nom}" for "{name}") makes format() fail and shows the raw template."""
    strings = load_strings(lang_file)
    offending = []
    for key, value in strings.items():
        if not isinstance(value, str) or key not in ref_strings:
            continue
        try:
            fields = _placeholders(value)
        except ValueError as e:
            offending.append(f"  {key}: unbalanced braces ({e})")
            continue
        try:
            expected = _placeholders(ref_strings[key])
        except ValueError:
            continue  # reported when the reference file itself is checked
        if fields != expected:
            offending.append(
                f"  {key}: {sorted(fields)} != {REFERENCE_LANG} {sorted(expected)}"
            )
    assert not offending, (
        f"{lang_file.stem} has placeholders that do not match "
        f"{REFERENCE_LANG}.py:\n" + "\n".join(offending)
    )


def test_tr_kwargs_match_placeholders():
    """A tr("literal_key", a=..., b=...) call must pass exactly the
    placeholders of that key in en.py. One missing kwarg makes format() fail,
    so none of the others are filled in either. Calls without kwargs (help
    texts that show "{date}" to the user on purpose) and calls with **kwargs
    are not checked."""
    offending = []
    for path, tree, _ignored in _parsed_sources():
        names = _tr_names(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in names and node.args and node.keywords
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in ref_keys):
                continue
            if any(kw.arg is None for kw in node.keywords):
                continue
            key = node.args[0].value
            kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
            expected = _placeholders(ref_strings[key])
            if kwargs != expected:
                details = []
                if expected - kwargs:
                    details.append(f"missing {sorted(expected - kwargs)}")
                if kwargs - expected:
                    details.append(f"unused {sorted(kwargs - expected)}")
                offending.append(f"  {path}:{node.lineno}: {key!r} {', '.join(details)}")
    assert not offending, (
        f"tr() kwargs that do not match the placeholders in {REFERENCE_LANG}.py:\n"
        + "\n".join(offending)
    )


def test_available_languages_match_files():
    """AVAILABLE_LANGUAGES and the lang/*.py files must agree: an unregistered
    file never shows up in Settings, and a registered code without a file
    silently falls back to Danish."""
    from opensak.lang import AVAILABLE_LANGUAGES

    files = {f.stem for f in LANG_DIR.glob("*.py") if f.stem != "__init__"}
    registered = set(AVAILABLE_LANGUAGES)
    assert files == registered, (
        f"Language files without an AVAILABLE_LANGUAGES entry: {sorted(files - registered)}; "
        f"AVAILABLE_LANGUAGES entries without a file: {sorted(registered - files)}"
    )
