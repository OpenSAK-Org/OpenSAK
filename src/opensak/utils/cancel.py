"""
src/opensak/utils/cancel.py — cancelling a long job from the GUI (#1265).

Qt-free, so the importer, the exporters and db/transfer.py can use it. A
worker thread owns a CancelFlag; its dialog sets it when the user presses
Cancel (or Escape), and the job checks it at points where stopping leaves
nothing half written:

  * the GPX importer and transfer_caches() take a ``should_cancel``
    callable and stop between batches, after a commit;
  * an export has no such points of its own, so its progress callback
    raises Cancelled (CancelFlag.raise_if_set) and the export's temporary
    file is removed (gps.garmin.write_export), leaving any previous file
    as it was.
"""

from __future__ import annotations

import threading


class Cancelled(BaseException):
    """The user cancelled the job. A BaseException, like KeyboardInterrupt,
    so the ``except Exception`` blocks that turn a failed step into an error
    message (the GPS exports, for one) don't swallow it."""


class CancelFlag:
    """Set from the GUI thread, read by the worker thread."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def raise_if_set(self) -> None:
        if self._event.is_set():
            raise Cancelled
