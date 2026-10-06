"""
tests/unit-tests/test_widget_cleanup_958.py — widgets a test leaves behind are
deleted before the next test starts (issue #958).

pytest-qt only calls deleteLater() on widgets registered with
qtbot.addWidget(), and outside a running event loop nothing processes those
deferred deletes. tests/conftest.py flushes them after every test; without
that, test_filter_dialog.py left about 346,000 widgets alive, and the first
event loop in a later test spent about a minute deleting them.

The two tests depend on running in this order, which pytest guarantees
within one file.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

import shiboken6
from PySide6.QtWidgets import QComboBox, QDialog, QVBoxLayout

# Filled by the first test, checked by the second. Holds the dialog itself:
# with a strong Python reference the C++ object can only go away through the
# deferred delete, which is exactly what is being tested.
_left_behind: list[QDialog] = []


def test_a_registers_a_dialog_with_child_widgets(qtbot):
    d = QDialog()
    layout = QVBoxLayout(d)
    for _ in range(5):
        # A combo box adds a top-level popup frame, like the filter dialog's.
        layout.addWidget(QComboBox(d))
    qtbot.addWidget(d)
    d.show()
    _left_behind.append(d)
    assert shiboken6.isValid(d)


def test_b_the_dialog_is_gone_before_the_next_test(qapp):
    assert _left_behind, "test_a must run first"
    d = _left_behind.pop()
    assert not shiboken6.isValid(d)
