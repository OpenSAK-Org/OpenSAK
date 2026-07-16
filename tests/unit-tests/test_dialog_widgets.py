# tests/unit-tests/test_dialog_widgets.py — delte dialog-widgets (DirRow).

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from opensak.gui.dialogs import widgets as w


class TestDirRowBrowse:
    def test_browse_normalizes_qt_forward_slashes(self, qtbot, tmp_path, monkeypatch):
        """
        Issue #609: QFileDialog.getExistingDirectory() always returns paths
        using forward slashes, regardless of platform. The displayed path
        must be normalized to the platform's native separator instead of
        showing the raw Qt-style path to the user.
        """
        row = w.DirRow(tmp_path)
        qtbot.addWidget(row)

        chosen = str(tmp_path).replace("\\", "/") + "/subdir"
        monkeypatch.setattr(
            w.QFileDialog, "getExistingDirectory", lambda *a, **k: chosen
        )

        row._browse()

        # Displayed text must go through Path() normalization rather than
        # showing Qt's raw forward-slash path verbatim — on Windows this
        # turns "E:/Users/.../subdir" into "E:\Users\...\subdir".
        assert row._edit.text() == str(Path(chosen))

    def test_browse_cancelled_leaves_text_unchanged(self, qtbot, tmp_path, monkeypatch):
        row = w.DirRow(tmp_path)
        qtbot.addWidget(row)
        original = row._edit.text()

        monkeypatch.setattr(w.QFileDialog, "getExistingDirectory", lambda *a, **k: "")

        row._browse()

        assert row._edit.text() == original

    def test_path_property_reflects_edit_text(self, qtbot, tmp_path):
        row = w.DirRow(tmp_path)
        qtbot.addWidget(row)
        assert row.path == tmp_path

    def test_set_path_updates_display(self, qtbot, tmp_path):
        row = w.DirRow(tmp_path)
        qtbot.addWidget(row)
        other = tmp_path / "elsewhere"
        row.set_path(other)
        assert row._edit.text() == str(other)
