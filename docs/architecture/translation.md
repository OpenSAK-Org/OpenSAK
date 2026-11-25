# Offline Translation of Cache Descriptions — Design (#672)

*Draft for review, 25 November 2026. Nothing is implemented yet. Comments go on #672; update this file in the same commit when a decision changes.*

## Summary

OpenSAK can translate a cache's description, hint and selected logs offline with [Argos Translate](https://github.com/argosopentech/argos-translate). Translations are stored next to the original and never replace it. The user chooses per cache whether the original or the translation is shown, and search, filters and GPS export follow that choice.

Translation is an **optional component**: OpenSAK works exactly as today until the user adds it, and the installers do not grow.

**Goals**

- Make listings in languages the user can't read usable, offline, in the field included.
- Never lose or hide the original: a translation can be stale or wrong, and the user must always be able to see the original.
- Keep the installer small and the main database schema untouched.

## Decisions

| Topic | Decision | Source |
| --- | --- | --- |
| Storage | A separate store next to the original; import never overwrites or deletes a translation | #672 proposal, nagisml |
| Cache deleted | Its translations are deleted with it. A later re-import of the same cache is untranslated until the user translates it again | AgreeDK, 25 Nov |
| Target language | Chosen by the user from the languages Argos supports; not tied to the UI language | AgreeDK, 25 Nov |
| Display | Per cache: original or translation ("Show translation" on/off) | AgreeDK, 25 Nov |
| Search and filters | Work on what is shown for each cache | AgreeDK, 25 Nov |
| Packaging | Argos is an optional component the user adds; language packs are opt-in downloads | AgreeDK, nagisml, Allyn56 |
| Detection | Language detected automatically, per paragraph; the user can override ("translate anyway") | AgreeDK addendum, nagisml, Allyn56 |
| Logs | Included, but translated on demand, not in bulk | nagisml, Allyn56 |

## Where translations are stored: the companion database

Translations go in the **companion database** (#1034, `db/companion.py`, `plans/companion-database.md`), not in the main schema. It fits on every point:

| Need | What the companion already does |
| --- | --- |
| Deleted with the cache | Cache-linked tables (a `gc_code` column) lose their rows when `delete_caches()` deletes the cache, in the same transaction; orphans are removed when the database is opened |
| Follows copy and move | `transfer_caches()` → `companion.copy_rows()` |
| Backup and restore | The companion is part of the backup set (`companions/`) |
| Main schema untouched | No migration of the main database, no new columns on `caches` |
| Isolation | A damaged companion is moved aside and the main database keeps working. For translations that is acceptable: the data can always be regenerated |
| Macro access | Macros can read OpenSAK-owned companion tables |

**Dependency:** the companion is behind the `companion-db` feature flag today. Translation either ships after the flag is removed, or is behind the same flag while it is beta.

### Tables

Both tables are registered in `opensak_tables` with owner `opensak` and kind `cache`.

```
translations                        -- one row per translated field
  gc_code      TEXT NOT NULL        -- cache-linked key (companion convention, not cache_id)
  field        TEXT NOT NULL        -- 'short_desc' | 'long_desc' | 'hint' | 'log'
  log_guid     TEXT NOT NULL DEFAULT ''   -- logs.log_id for field='log', '' otherwise
  target_lang  TEXT NOT NULL        -- ISO 639-1, e.g. 'da'
  source_lang  TEXT                 -- detected: 'de', or 'mixed' when paragraphs differ
  source_hash  TEXT NOT NULL        -- sha256 of the original text when it was translated
  text         TEXT NOT NULL        -- the translation; HTML kept when the original is HTML
  engine       TEXT NOT NULL        -- e.g. 'argos 1.9.6; de→en 1.0, en→da 1.1'
  pivot        INTEGER NOT NULL     -- 1 if the translation went through English
  created_at   TEXT NOT NULL        -- ISO 8601 UTC
  PRIMARY KEY (gc_code, field, log_guid, target_lang)

translation_display                 -- the per-cache choice; no row = show the original
  gc_code      TEXT PRIMARY KEY
  lang         TEXT NOT NULL        -- show the translations into this language
```

Notes:

- **Logs are keyed by `logs.log_id`** (the geocaching.com log GUID, or OpenSAK's generated `gen_…` id), not by `logs.id`. The importer updates a known log in place, so the GUID survives re-imports. A translation whose log no longer exists (e.g. removed by a `PurgeLogs`-style macro) is ignored when read and removed by the translation housekeeping on open.
- **`log_guid` is `''`, not NULL**, so it can be part of the primary key.
- **The display choice stores a language, not on/off.** "Show translation" sets `lang` to the current target language. If the user later changes the target language, caches translated earlier keep showing their existing translation instead of silently falling back to the original.
- `translation_display` could be a column in the main database instead. Keeping it in the companion keeps everything about translation in one place, and it follows delete, copy and move the same way.

### Stale translations

Import never touches these tables. When a cache is re-imported and its description changed, the stored `source_hash` no longer matches the original: the translation is **stale**.

- The detail panel shows it with a marker: "The original has changed since it was translated — Translate again".
- Search, filters and export keep using it until it is replaced (a cache shouldn't drop out of a filter because the owner fixed a typo).
- "Translate again" is offered for the cache and as a bulk action for the selection.

## The effective text

Everything that reads descriptions goes through one function, so the detail panel, filters, GPS export and macros can never disagree:

```python
def effective_text(cache, field, log_guid="") -> tuple[str | None, TranslationState]:
    """The text the user has chosen to see for *field* of *cache*: the
    translation into translation_display.lang when one exists, otherwise
    the original. The state says which, and whether it is stale."""
```

### Search and filters

`TextSearchFilter` (`filters/engine.py`) has two paths, and both need the effective text:

**SQL path** (`apply_to_query()`): replace the column by a COALESCE over a correlated lookup. For the long description:

```sql
COALESCE(
  (SELECT t.text
     FROM companion.translations t
     JOIN companion.translation_display d ON d.gc_code = t.gc_code
    WHERE t.gc_code = caches.gc_code
      AND t.field = 'long_desc' AND t.log_guid = ''
      AND t.target_lang = d.lang),
  caches.long_description)
```

The primary key makes this one index lookup per cache, and it is a no-op for caches without a row in `translation_display`. Logs work the same way inside the existing `EXISTS` over `logs`, joined on `log_guid = logs.log_id`.

**Python path** (`_values()`, used for regex): load the effective texts of the candidate caches in one query before matching, like the custom-field values are loaded today.

When the companion isn't attached (flag off, missing or damaged), the filter uses the original texts, as it does today.

Whether the cache list's columns (e.g. short description) also show the effective text is open; see Questions.

### GPS export

The GPX/GGZ export (`gps/garmin.py`, file export) gets a setting:

- **Original**
- **As shown for each cache** (default once translation is installed)
- **Both** — translation first, then the original under a separator. Useful in the field when the translation is poor.

The hint is exported the same way. A translated log replaces the log text only with "As shown" or "Both".

## Translating

### The flow

1. Split the text into blocks. For HTML: block elements (`<p>`, `<div>`, `<li>`, `<td>`, headings, `<br><br>`); for plain text: blank lines.
2. Detect the language of each block (a light library, e.g. `py3langid`; to be chosen in the spike).
3. Skip blocks already in the target language, and blocks that are mostly not text (coordinates, numbers, URLs, image-only).
4. Translate the rest, keeping markup, links and images: only text nodes are sent to Argos. The Argos/LibreTranslate project has a small HTML helper on top of Argos for this; check it in the spike before writing our own.
5. Put the document back together and store it with the hash of the original.

Blocks in different languages are each translated from their own language. That also covers Swiss listings with several languages (urs-beeli), at least where the languages are in separate paragraphs.

### Hints and logs

- **Hints** are short, so detection is unreliable. Default: translate from the description's detected language; "translate anyway from…" lets the user pick. `hint_detect.py` decides first whether the stored hint is plain text or ROT13 — only plain text is translated.
- **Logs** are translated one at a time from the Logs tab, or "Translate the latest N logs". Never automatically on import.

### Pivot through English

Argos translates most pairs through English (nagisml). That costs quality, most of all on short texts. The UI says so when a pair pivots ("Translated via English"), and `pivot` is stored so it can be shown later.

### Bulk translation

"Translate selected caches" in the context menu runs in a background thread with progress and cancel, like imports. It skips caches whose translation is current, and translates stale ones again.

## The optional component

OpenSAK is a frozen PyInstaller app, so it can't `pip install` Argos later: the Python version and the compiled libraries (CTranslate2, sentencepiece) would have to match the build exactly.

**Proposal: a separate helper program, `opensak-translate`.**

- Built by CI for Windows, Linux and macOS, as its own PyInstaller bundle, published as a release asset.
- OpenSAK downloads it when the user turns translation on (Settings → Translation → Install), checks its hash, and keeps it in the user data folder.
- OpenSAK talks to it over stdin/stdout with JSON lines: `detect`, `translate`, `list_packages`, `install_package`, `remove_package`, `version`.
- The helper stays running while translations are made and stops after a short idle time, so models load once per session.
- A crash or hang in the helper can never take OpenSAK down; OpenSAK reports it and carries on.
- The helper has its own version and protocol version; OpenSAK refuses a helper with an incompatible protocol and offers the update.

Alternatives considered:

| Option | Why not (for now) |
| --- | --- |
| Bundle Argos in the installer | Every user pays the size; nagisml and Allyn56 both prefer opt-in |
| Plugin bundle on `sys.path` | Must match OpenSAK's exact Python and build; fragile on every upgrade |
| Local LibreTranslate server | Much heavier than needed |

**Language packs** (up to ~100 MB per pair, nagisml) are downloaded from the Argos package index on request, through the helper, into the user data folder — never into the database or install folder. Settings → Translation lists installed and available pairs, with size and Remove. For a pivot pair, the user is told which two packs are needed.

**Settings** (opensak.json):

| Key | Meaning |
| --- | --- |
| `translation.target_lang` | Target language, chosen from the installed packs |
| `translation.helper_dir` | Where the helper lives (default under the user data folder) |
| `translation.export_mode` | `original` / `shown` / `both` |

The backup set already includes opensak.json; the helper and language packs are not backed up (they can be downloaded again).

## Macros

Read access through the companion: the `translations` and `translation_display` tables, plus an `effective_text` helper in the API so macros don't have to repeat the COALESCE logic. Writing translations from macros is not planned. To be agreed with nagisml.

## Edge cases and risks

- **Size and startup time of the helper** are the biggest unknowns; the spike measures them before any UI work.
- **macOS:** the helper must be signed and notarized the same way as the app (or opened the same way as an unsigned app), otherwise Gatekeeper blocks it.
- **Companion missing or damaged:** translations are gone; originals are shown; the user can translate again.
- **Copy/move to a database whose companion can't be created:** the translations stay behind (same as custom values).
- **HTML that breaks** after translation (unclosed tags, inline styles in the middle of sentences): the stored translation is sanitised the same way as the original before display.
- **Encoded log texts** (`logs.text_encoded`): decoded before translation, as for display.
- **Very long listings:** translate in chunks with progress; cap the blocks per request to keep memory down.
- **Licences:** Argos itself and each language model have their own licences; the spike checks them, and Settings → Translation links to them.

## Sub-issues and order of work

| # | Sub-issue | Depends on |
| --- | --- | --- |
| 1 | **Spike:** build `opensak-translate` on all three platforms; measure size, startup and translation time; test HTML handling and a detection library | — |
| 2 | Companion tables, `effective_text()`, housekeeping (log orphans), tests with a fake engine | companion-db |
| 3 | Helper client in OpenSAK (process, protocol, timeouts) + Settings → Translation (install helper, packs, target language) | 1 |
| 4 | Detail panel: Translate, Show translation on/off, stale marker, "Translated via English" | 2, 3 |
| 5 | Search and filters on the effective text (SQL and Python paths) | 2 |
| 6 | Bulk: translate selected caches, show/hide translation for selection | 4 |
| 7 | Hints and logs | 4 |
| 8 | GPS export setting | 2 |
| 9 | Macro read access | 2, nagisml |

## Questions

1. Should the cache list's columns (short description, hint column) also show the effective text, or always the original?
2. Should translating a cache automatically switch "Show translation" on? (Proposed: yes.)
3. Should the main window show which caches have a translation — an icon or a column, and a filter "Has translation"?
4. Should the helper be downloaded from our GitHub release, or should we also allow a manual install for offline machines?
5. Is shipping behind the `companion-db` flag acceptable, or should translation wait until the flag is removed?
