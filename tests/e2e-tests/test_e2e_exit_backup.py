# tests/e2e-tests/test_e2e_exit_backup.py — back up on exit in MainWindow (#959).
#
# The controller is replaced by a stub whose decision each test sets, so
# this only tests MainWindow's side: the close is held while the controller
# says so, and goes through once it says "close".

import pytest

from opensak.backup import exit_state
from opensak.gui.dialogs import exit_backup_dialog as ebd


class _StubController:
    decision = True
    instances: list["_StubController"] = []

    def __init__(self, window):
        from PySide6.QtCore import QObject, Signal

        class _Sig(QObject):
            done = Signal(bool)

        self._sig = _Sig()
        self.done = self._sig.done
        _StubController.instances.append(self)

    def start(self):
        return _StubController.decision


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(exit_state, "_suppressed", False)
    _StubController.decision = True
    _StubController.instances = []
    monkeypatch.setattr(ebd, "ExitBackupController", _StubController)
    yield _StubController
    # Let the window fixture's teardown close the window.
    _StubController.decision = True
    exit_state._suppressed = True


def test_close_goes_through(stub, empty_window, qtbot):
    empty_window.close()
    assert not empty_window.isVisible()
    assert len(stub.instances) == 1


def test_cancel_keeps_the_window_open_and_untouched(stub, empty_window, qtbot):
    stub.decision = False
    empty_window.close()
    assert empty_window.isVisible()
    assert empty_window._exit_backup is None


def test_running_backup_holds_the_close_until_done(stub, empty_window, qtbot):
    stub.decision = None
    empty_window.close()
    assert empty_window.isVisible()
    controller = stub.instances[0]
    assert empty_window._exit_backup is controller

    # Closing again while the backup runs neither asks again nor closes.
    empty_window.close()
    assert empty_window.isVisible()
    assert len(stub.instances) == 1

    controller.done.emit(True)
    qtbot.waitUntil(lambda: not empty_window.isVisible(), timeout=3000)
    assert len(stub.instances) == 1          # the second close skipped the prompt


def test_failed_backup_cancelled_keeps_the_window_open(stub, empty_window, qtbot):
    stub.decision = None
    empty_window.close()
    stub.instances[0].done.emit(False)
    qtbot.wait(50)
    assert empty_window.isVisible()
    assert empty_window._exit_backup is None

    # The next close asks again.
    stub.decision = True
    empty_window.close()
    assert not empty_window.isVisible()
    assert len(stub.instances) == 2


def test_suite_default_never_prompts(empty_window, monkeypatch):
    """tests/conftest.py keeps the prompt suppressed for every other test."""
    assert exit_state.is_exit_backup_suppressed() is True
    empty_window.close()
    assert not empty_window.isVisible()
