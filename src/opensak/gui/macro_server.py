"""
src/opensak/gui/macro_server.py — runs macros sent by `opensak --run-macro`
(#1013).

The running OpenSAK listens on a local socket (a named pipe on Windows,
see utils/run_macro.py for the protocol). QLocalServer's UserAccessOption
limits it to the current user: on Windows the pipe's ACL grants only the
user's own account, elsewhere the socket file is mode 0600.

A request runs the macro on the GUI thread like the Run button in the macro
window, with the same host, so its dialogs and folder prompts are the same;
they come up in front of the OpenSAK window while the caller waits. A
request is refused while a dialog is open or another macro runs — a macro
starting under an open dialog could not be seen or answered properly.

Cancelling (the caller's --timeout or Ctrl+C, or the caller going away)
closes a dialog the macro has open and ends the macro at its next print or
API call. A macro busy in pure Lua cannot be interrupted, but the
instruction limit ends it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog

from opensak.macro import MacroBusy, MacroError, MacroHost, MacroRuntime, macro_running
from opensak.utils.run_macro import (
    EXIT_BUSY, EXIT_MACRO_ERROR, EXIT_USAGE, decode_lines, encode, format_error,
    server_name,
)

logger = logging.getLogger(__name__)

# How long a write to the caller may block the GUI thread.
_WRITE_TIMEOUT_MS = 1000


class MacroServer(QObject):
    """Accepts `opensak --run-macro` connections and runs their macros
    against *host* (the main window)."""

    def __init__(self, host: MacroHost, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._host = host
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_new_connection)

    def start(self) -> bool:
        """Start listening; False (logged) if that is not possible, e.g.
        because another OpenSAK of this user already listens."""
        name = server_name()
        if self._server.listen(name):
            logger.info("macro server: listening on %s", self._server.fullServerName())
            return True
        # A socket file left behind by a crash (Unix) blocks listen(); remove
        # it unless another OpenSAK still answers on it.
        probe = QLocalSocket()
        probe.connectToServer(name)
        if probe.waitForConnected(500):
            probe.abort()
            logger.warning("macro server: another OpenSAK already receives "
                           "--run-macro requests; not listening")
            return False
        QLocalServer.removeServer(name)
        if self._server.listen(name):
            logger.info("macro server: listening on %s", self._server.fullServerName())
            return True
        logger.warning("macro server: cannot listen on %s: %s",
                       name, self._server.errorString())
        return False

    def close(self) -> None:
        self._server.close()

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            _Session(socket, self._host, self)


class _Session(QObject):
    """One --run-macro connection: a single run request, then exit."""

    def __init__(self, socket: QLocalSocket, host: MacroHost, parent: QObject):
        super().__init__(parent)
        self._socket = socket
        self._host = host
        self._buffer = b""
        self._requested = False
        self._runtime: Optional[MacroRuntime] = None
        socket.setParent(self)
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(self._on_disconnected)
        self._send({"op": "hello", "pid": os.getpid()})

    # -- Connection ------------------------------------------------------------

    def _connected(self) -> bool:
        return self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState

    def _send(self, message: dict[str, Any]) -> None:
        if not self._connected():
            return
        self._socket.write(encode(message))
        # The macro runs on this thread, so there is no event loop to write
        # in the background — wait until the data has gone out.
        if self._socket.bytesToWrite() and not self._socket.waitForBytesWritten(_WRITE_TIMEOUT_MS):
            if not self._connected():
                self._cancel()

    def _on_ready_read(self) -> None:
        messages, self._buffer = decode_lines(self._buffer + self._socket.readAll().data())
        for message in messages:
            op = message.get("op")
            if op == "run" and not self._requested:
                self._requested = True
                path = message.get("path")
                # Not from within readyRead: Qt does not emit readyRead again
                # while its slot runs, so a cancel sent while the macro shows
                # a dialog would not arrive.
                QTimer.singleShot(0, self, lambda p=path: self._run(p))
            elif op == "cancel":
                self._cancel()

    def _on_disconnected(self) -> None:
        if self._runtime is not None:
            self._cancel()
        else:
            self.deleteLater()

    def _finish(self, code: int) -> None:
        self._send({"op": "exit", "code": code})
        self._socket.disconnectFromServer()
        self.deleteLater()

    # -- Running ---------------------------------------------------------------

    def _err(self, text: str) -> None:
        self._send({"op": "err", "text": text})

    def _busy_reason(self) -> Optional[str]:
        if macro_running():
            return "another macro is still running"
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget():
            return "a dialog is open — close it first"
        return None

    def _run(self, raw_path: Any) -> None:
        if not self._connected():
            self.deleteLater()
            return
        if not isinstance(raw_path, str) or not raw_path:
            self._err("opensak: no macro path in the request")
            self._finish(EXIT_USAGE)
            return
        path = Path(raw_path)
        busy = self._busy_reason()
        if busy is not None:
            self._err(f"opensak: OpenSAK is busy: {busy}")
            self._finish(EXIT_BUSY)
            return
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self._err(f"{path}: cannot read the macro: {exc}")
            self._finish(EXIT_USAGE)
            return

        logger.info("macro server: running %s", path)
        self._runtime = MacroRuntime(
            self._host,
            output=lambda text: self._send({"op": "out", "text": text}),
            log=self._err,
        )
        try:
            code = self._runtime.run(source, chunk_name=path.name, base_dir=path.parent)
        except MacroBusy as exc:
            self._err(f"opensak: OpenSAK is busy: {exc}")
            code = EXIT_BUSY
        except MacroError as exc:
            self._err(format_error(str(exc), path.name, path))
            code = EXIT_MACRO_ERROR
        finally:
            self._runtime = None
        logger.info("macro server: %s ended with exit code %d", path, code)
        self._finish(code)

    def _cancel(self) -> None:
        """Stop the running macro: close the dialog it has open (nothing
        else can be open — runs only start without one) and end it at its
        next print or API call."""
        if self._runtime is None:
            return
        self._runtime.cancel()
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QDialog):
            dialog.reject()
