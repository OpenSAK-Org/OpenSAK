"""Dialogs package."""

# Cap GUI progress updates so per-cache callbacks on large exports (thousands of
# caches) never flood the Qt event loop.
_MAX_PROGRESS_UPDATES = 200


def make_progress_cb(emit, cancel=None):
    """Return a throttled progress_cb(done, total) that forwards to *emit*.

    Emits at most ~200 updates plus the final one, keeping the progress bar
    smooth without swamping the event loop. With a CancelFlag (*cancel*,
    see opensak.utils.cancel) it raises Cancelled on every call once the
    flag is set, which stops the export it is passed to (#1265).
    """
    def cb(done: int, total: int) -> None:
        if cancel is not None:
            cancel.raise_if_set()
        if total <= 0:
            return
        if done == total or done % max(1, total // _MAX_PROGRESS_UPDATES) == 0:
            emit(done, total)

    return cb
