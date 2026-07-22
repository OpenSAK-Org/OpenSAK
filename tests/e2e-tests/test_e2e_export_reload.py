"""tests/e2e-tests/test_e2e_export_reload.py — regression for the #207 export crash,
updated for #627 beta.9-11's lightweight query path.

Table-model caches are partial — historically (#207) real Cache ORM objects,
detached with a deferred encoded_hints column, which raised
DetachedInstanceError; since #627 beta.9-11, apply_filters_auto() normally
returns LightweightCache rows instead, which raise AttributeError for the
same field by design (see LightweightCache's docstring). Either way,
exporting them used to crash; the workers now reload full Cache objects via
reload_caches_full() before generating, which recognizes both object types.
"""

import pytest

pytest.importorskip("pytestqt")


def _table_caches(window):
    # The exact (partial) objects the export menu hands to a dialog — since
    # #627 beta.9-11, normally LightweightCache rows, not real Cache ORM
    # objects.
    model = window._cache_table._model
    return [
        c
        for c in (model.cache_at(i) for i in range(window._cache_table.row_count()))
        if c is not None
    ]


def test_table_caches_are_detached_and_deferred(seeded_window):
    """Precondition: the objects the export receives are the ones that used to
    crash — incomplete, missing encoded_hints without an explicit reload.

    #627 beta.9-11 changed *how* this fails (LightweightCache's deliberate
    AttributeError safety net, not SQLAlchemy's DetachedInstanceError from a
    real ORM object whose session has closed) but not *that* it fails — table
    caches never carry the full hint/log data without reload_caches_full().
    That's the invariant this test protects; the exact exception type is an
    implementation detail of which object type happens to be in the table.
    """
    from opensak.filters.engine import LightweightCache

    caches = _table_caches(seeded_window)
    assert caches
    assert isinstance(caches[0], LightweightCache), (
        "expected apply_filters_auto() to be serving the table via the "
        "lightweight query path (#627 beta.9-11) — if this fails, either "
        "that path was disabled/removed, or mainwindow.py stopped calling "
        "apply_filters_auto(). Either way this test's premise needs revisiting."
    )
    with pytest.raises(AttributeError, match="encoded_hints"):
        _ = caches[0].encoded_hints


def test_file_export_reloads_full_caches(seeded_window, tmp_path):
    """Exporting table caches to GPX no longer crashes, and the output includes
    the hint (deferred) and log text (noload'ed) that were absent at load time."""
    from opensak.gui.dialogs.file_export_dialog import _ExportWorker

    caches = _table_caches(seeded_window)
    out = tmp_path / "reload.gpx"

    worker = _ExportWorker(caches, out, "gpx")
    errors = []
    worker.error.connect(errors.append)
    worker.run()  # synchronous — deterministic, no thread to wait on

    assert not errors, errors
    content = out.read_text(encoding="utf-8")
    assert "Under a rock." in content      # encoded_hints (deferred) reloaded
    assert "TFTC! Great hide." in content   # log text (noload'ed) reloaded
